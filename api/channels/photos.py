"""Decode/validate signed photo bytes. Never fetch a URL or access Mongo directly."""

import base64
import binascii
import io
import warnings

from PIL import Image, UnidentifiedImageError

from db import contacts, conversations, media
from routes.memories import ALLOWED_PHOTO_TYPES
from .models import InboundPhoto
from .consent import receive_stop

MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_FRAMES = 200
FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP", "image/gif": "GIF"}
class PhotoDecoderUnavailable(ValueError):
    """The claimed format cannot be verified by installed decoders."""


def _enable_heif() -> None:
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        raise PhotoDecoderUnavailable("HEIF decoder is not installed") from None
    register_heif_opener()


def decode_photo(message: InboundPhoto) -> bytes:
    if message.mimeType not in ALLOWED_PHOTO_TYPES:
        raise ValueError("Unsupported photo type")
    try:
        data = base64.b64decode(message.dataBase64, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid photo encoding") from None
    if not data or len(data) > media.MAX_BYTES or base64.b64encode(data).decode("ascii") != message.dataBase64:
        raise ValueError("Invalid photo encoding or size")
    if message.mimeType in {"image/heic", "image/heif"}:
        _enable_heif()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != FORMATS.get(message.mimeType, "HEIF") or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Photo type or dimensions do not match")
                image.verify()
            # verify() alone does not fully decode all supported formats.
            with Image.open(io.BytesIO(data)) as image:
                frames = getattr(image, "n_frames", 1)
                if frames > MAX_IMAGE_FRAMES:
                    raise ValueError("Photo has too many frames")
                total_pixels = 0
                for frame in range(frames):
                    image.seek(frame)
                    total_pixels += image.width * image.height
                    if total_pixels > MAX_IMAGE_PIXELS:
                        raise ValueError("Photo decoded size is too large")
                    image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombWarning,
            Image.DecompressionBombError) as exc:
        raise ValueError("Photo bytes could not be verified") from exc
    return data


def receive_photo(db, message: InboundPhoto) -> dict:
    stopped = receive_stop(db, message.model_dump(exclude={"dataBase64"}), message.caption)
    if stopped is not None:
        return stopped
    # Consent precedes expensive decode and is checked again in the storage layer.
    contact = contacts.resolve_contact(db, message.provider, message.senderId)
    data = decode_photo(message)
    duplicate = conversations.save_photo_inbound(db, contact, message.model_dump(exclude={"dataBase64"}), data)
    return {"accepted": True, "duplicate": duplicate}
