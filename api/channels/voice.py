"""Bounded voice-note intake. Compressed headers are sanity checks, not audio decoding."""

import asyncio
import base64
import binascii
import hashlib
import io
import wave

from db import contacts, conversations, media
from .models import InboundVoice, WHITESPACE
from .transcription import MIME_FILENAMES, MAX_TRANSCRIPT_BYTES
from .audio import CAF_MIME, AudioConversionError, convert_caf, validate_caf


def _wav(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as sound:
            frames = sound.getnframes()
            if (sound.getcomptype() != "NONE" or not 1 <= sound.getnchannels() <= 8
                    or not 1 <= sound.getsampwidth() <= 4 or sound.getframerate() <= 0 or frames <= 0):
                raise ValueError("Invalid PCM WAV")
            expected = frames * sound.getnchannels() * sound.getsampwidth()
            if expected > len(data) or len(sound.readframes(frames)) != expected:
                raise ValueError("Truncated PCM WAV")
    except (wave.Error, EOFError):
        raise ValueError("Invalid WAV") from None


def _mp4(data):
    offset, boxes = 0, {}
    count = 0
    while offset < len(data):
        count += 1
        if count > 1024 or len(data) - offset < 8:
            raise ValueError("Invalid MP4 boxes")
        size, name = int.from_bytes(data[offset:offset + 4], "big"), data[offset + 4:offset + 8]
        header = 8
        if size == 1:
            if len(data) - offset < 16:
                raise ValueError("Invalid MP4 size")
            size, header = int.from_bytes(data[offset + 8:offset + 16], "big"), 16
        if size == 0:
            size = len(data) - offset
        if size < header or offset + size > len(data):
            raise ValueError("Invalid MP4 box")
        payload = data[offset + header:offset + size]
        if name in {b"ftyp", b"moov", b"mdat"}:
            boxes[name] = payload
        offset += size
    ftyp = boxes.get(b"ftyp", b"")
    if (len(ftyp) < 8 or not boxes.get(b"mdat") or b"soun" not in boxes.get(b"moov", b"")
            or not any(brand in ftyp for brand in (b"M4A ", b"isom", b"iso2", b"mp41", b"mp42", b"qt  "))):
        raise ValueError("Missing audio MP4 structure")


def _mp3(data):
    offset = 0
    if data.startswith(b"ID3"):
        if len(data) < 10 or any(x & 128 for x in data[6:10]):
            raise ValueError("Invalid ID3 header")
        offset = 10 + sum(x << shift for x, shift in zip(data[6:10], (21, 14, 7, 0)))
        if data[5] & 16:
            offset += 10
    header = data[offset:offset + 4]
    if (len(header) != 4 or header[0] != 255 or header[1] & 224 != 224
            or (header[1] >> 3) & 3 == 1 or (header[1] >> 1) & 3 == 0
            or header[2] >> 4 in {0, 15} or (header[2] >> 2) & 3 == 3 or len(data) <= offset + 4):
        raise ValueError("Missing MPEG audio frame")


def _ogg(data):
    if not data.startswith(b"OggS\x00") or len(data) < 28:
        raise ValueError("Invalid Ogg header")
    segments = data[26]
    start = 27 + segments
    if not segments or len(data) < start:
        raise ValueError("Invalid Ogg segment table")
    size = sum(data[27:start])
    first = data[start:start + size]
    if len(first) != size or not first.startswith((b"OpusHead", b"\x01vorbis")):
        raise ValueError("Unsupported Ogg audio codec")
    if not data[start + size:].startswith(b"OggS\x00"):
        raise ValueError("Missing Ogg audio pages")


def _flac(data):
    if len(data) < 44 or not data.startswith(b"fLaC") or data[4] & 127 != 0 or data[5:8] != b"\x00\x00\x22":
        raise ValueError("Invalid FLAC stream info")
    offset = 4
    for _ in range(128):
        if len(data) - offset < 4:
            break
        last = data[offset] & 128
        size = int.from_bytes(data[offset + 1:offset + 4], "big")
        offset += size + 4
        if offset > len(data):
            break
        if last:
            if len(data) > offset + 2 and data[offset] == 255 and data[offset + 1] & 254 == 248:
                return
            break
    raise ValueError("Missing FLAC frames")


def decode_voice(message: InboundVoice) -> bytes:
    if message.mimeType not in MIME_FILENAMES and message.mimeType != CAF_MIME:
        raise ValueError("Unsupported audio MIME")
    try:
        data = base64.b64decode(message.dataBase64, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid audio encoding") from None
    if not data or len(data) > media.MAX_BYTES or base64.b64encode(data).decode("ascii") != message.dataBase64:
        raise ValueError("Invalid audio encoding or size")
    mime = message.mimeType
    if mime == CAF_MIME:
        try:
            validate_caf(data)
        except AudioConversionError:
            raise ValueError("Invalid CAF audio") from None
    elif mime in {"audio/wav", "audio/x-wav"}:
        _wav(data)
    elif mime in {"audio/mp4", "audio/m4a", "audio/x-m4a"}:
        _mp4(data)
    elif mime in {"audio/mpeg", "audio/mp3", "audio/mpga"}:
        _mp3(data)
    elif mime == "audio/ogg":
        _ogg(data)
    elif mime in {"audio/flac", "audio/x-flac"}:
        _flac(data)
    elif (not data.startswith(b"\x1aE\xdf\xa3") or b"webm" not in data[:4096]
          or not any(codec in data[:1_048_576] for codec in (b"A_OPUS", b"A_VORBIS"))
          or b"\x1fC\xb6u" not in data):
        raise ValueError("Missing WebM audio structure")
    return data


def receive_voice(db, message: InboundVoice) -> dict:
    contact = contacts.resolve_contact(db, message.provider, message.senderId)
    data = decode_voice(message)
    duplicate = conversations.save_voice_inbound(db, contact, message.model_dump(exclude={"dataBase64"}), data)
    return {"accepted": True, "duplicate": duplicate}


def transcribe_receipt(db, family_id: str, contact_id: str, receipt: dict, *, transcriber, check) -> str:
    """Called only inside the worker's contact and receipt reservations."""
    check()
    stored = conversations.reserve_transcription(db, family_id, contact_id, receipt["_id"])
    if stored["transcriptionStatus"] == "complete":
        return stored["transcript"]
    try:
        audio = media.get_media(db, family_id, stored["audioId"])
        if (audio is None or audio.kind != "audio" or audio.content_type != stored["audioMimeType"]
                or hashlib.sha256(audio.data).hexdigest() != stored["audioSha256"]):
            raise ValueError("Audio is unavailable or changed")
        check()
        async def call():
            async with asyncio.timeout(60):
                data, content_type = audio.data, audio.content_type
                if content_type == CAF_MIME:
                    data = await convert_caf(data)
                    content_type = "audio/wav"
                    check()  # Recheck consent after local decoding, before any provider upload.
                return await transcriber.transcribe(data, content_type=content_type)
        transcript = asyncio.run(call())
        check()
        if (not isinstance(transcript, str) or not transcript.strip(WHITESPACE)
                or len(transcript.encode()) > MAX_TRANSCRIPT_BYTES):
            raise ValueError("Invalid transcript")
        conversations.complete_transcription(db, family_id, contact_id, receipt["_id"], transcript)
        check()
        return transcript
    except AudioConversionError:
        conversations.uncertain_transcription(db, family_id, contact_id, receipt["_id"])
        raise  # Explicit conversion-only failure; no provider request was made.
    except (Exception, asyncio.CancelledError):
        conversations.uncertain_transcription(db, family_id, contact_id, receipt["_id"])
        raise RuntimeError("Transcription outcome requires reconciliation") from None
