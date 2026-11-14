import base64
import hashlib
import hmac
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from agent.conversation import ConversationService
from agent.conversation.agent import conversation_tools
from channels import ensure_channel_indexes, photos
from channels.models import InboundPhoto, MAX_PHOTO_BODY_BYTES
from channels.routes import router
from db import client as db_client, conversations, media

PATH = "/internal/channels/inbound-photo"
SECRET = "dummy-photo-test-secret-" * 3


def image_bytes(format="PNG"):
    output = io.BytesIO()
    Image.new("RGB", (4, 4), (100, 120, 140)).save(output, format=format)
    return output.getvalue()


def envelope(data=None, **changes):
    return {"messageId": "photo-message", "attachmentId": "attachment-1", "provider": "terminal",
            "spaceId": "terminal", "senderId": "alice", "mimeType": "image/png",
            "dataBase64": base64.b64encode(image_bytes() if data is None else data).decode(), **changes}


def post(client, message=None, path=PATH, *, signed=True):
    body = json.dumps(envelope() if message is None else message, ensure_ascii=False).encode(errors="backslashreplace")
    timestamp, nonce = str(int(time.time())), str(uuid4())
    value = f"POST\n{path}\n{timestamp}\n{nonce}\n".encode() + body
    headers = {"content-type": "application/json", "X-Channels-Timestamp": timestamp,
               "X-Channels-Nonce": nonce,
               "X-Channels-Signature": hmac.new(SECRET.encode(), value, hashlib.sha256).hexdigest()}
    return client.post(path, content=body, headers=headers if signed else {})


def contact(db, family="family-a", *, handle="alice", **changes):
    now = datetime.now(timezone.utc)
    row = {"_id": family + ":" + handle, "familyId": family, "personId": family + "-person",
           "channel": "terminal", "handle": handle, "status": "active", "verifiedAt": now,
           "optInAt": now, **changes}
    db.contacts.insert_one(row)
    db.people.insert_one({"_id": row["personId"], "familyId": family})
    return row


@pytest.fixture
def stack(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True).db
    ensure_channel_indexes(db)
    contact(db)
    monkeypatch.setenv("STITCH_CHANNELS", "1")
    monkeypatch.setenv("CHANNELS_SHARED_SECRET", SECRET)
    monkeypatch.setattr(db_client, "get_db", lambda: db)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, db


def test_photo_receipt_and_duplicate_keep_one_file(stack):
    client, db = stack
    assert post(client).json() == {"accepted": True, "duplicate": False}
    assert post(client).json() == {"accepted": True, "duplicate": True}
    receipt = db.channelMessages.find_one({"kind": "inbound"})
    assert receipt["status"] == "received" and len(receipt["photoIds"]) == 1
    found = media.get_media(db, "family-a", receipt["photoIds"][0])
    assert found.data == image_bytes() and found.kind == "photo"
    assert media.get_media(db, "family-b", receipt["photoIds"][0]) is None
    assert db[media.INLINE].count_documents({}) == 1
    assert "dataBase64" not in receipt and "senderId" not in receipt


@pytest.mark.parametrize("format,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"),
                                        ("WEBP", "image/webp"), ("GIF", "image/gif")])
def test_allowed_decodable_formats(stack, format, mime):
    assert post(stack[0], envelope(image_bytes(format), mimeType=mime)).status_code == 202


@pytest.mark.parametrize("changes", [{"attachmentId": "other"}, {"caption": "other"},
                                     {"spaceId": "other"}, {"dataBase64": base64.b64encode(image_bytes("GIF")).decode(),
                                                            "mimeType": "image/gif"}])
def test_conflicting_photo_envelope_is_rejected(stack, changes):
    client, db = stack
    assert post(client).status_code == 202
    assert post(client, envelope(**changes)).status_code == 409
    assert db[media.INLINE].count_documents({}) == 1


@pytest.mark.parametrize("photo_first", [False, True])
def test_text_photo_message_id_collision(stack, photo_first):
    client, db = stack
    text = {"messageId": "photo-message", "provider": "terminal", "senderId": "alice",
            "spaceId": "terminal", "text": "A story"}
    first = lambda: post(client) if photo_first else post(client, text, "/internal/channels/inbound")
    second = lambda: post(client, text, "/internal/channels/inbound") if photo_first else post(client)
    assert first().status_code == 202
    assert second().status_code == 409
    assert db[media.INLINE].count_documents({}) == int(photo_first)


@pytest.mark.parametrize("changes,code", [({"senderId": "unknown"}, 404), ({}, 403)])
def test_unknown_or_stopped_contact_never_decodes_or_stores(stack, monkeypatch, changes, code):
    client, db = stack
    db.contacts.update_many({}, {"$set": {"status": "stopped"}})
    monkeypatch.setattr(photos, "decode_photo", lambda _: pytest.fail("decoded before consent"))
    assert post(client, envelope(**changes)).status_code == code
    assert db[media.INLINE].count_documents({}) == 0


def test_ambiguous_contact_and_authorization(stack):
    client, db = stack
    request = {"provider": "terminal", "senderId": "alice"}
    assert post(client, request, "/internal/channels/authorize").json() == {"allowed": True}
    assert post(client, request, "/internal/channels/authorize", signed=False).status_code == 401
    contact(db, "family-b")
    assert post(client, request, "/internal/channels/authorize").status_code == 409
    assert post(client).status_code == 409
    assert db[media.INLINE].count_documents({}) == 0


@pytest.mark.parametrize("changes", [{"mimeType": "image/jpeg"}, {"mimeType": "text/plain"},
                                     {"dataBase64": "AA==\n"}, {"dataBase64": "AB=="},
                                     {"dataBase64": "invalid!!"}, {"dataBase64": ""},
                                     {"caption": "x" * 8001}, {"url": "http://localhost/private"},
                                     {"attachmentId": "x" * 257}])
def test_invalid_photo_messages_no_media(stack, changes):
    client, db = stack
    response = post(client, envelope(**changes))
    assert response.status_code == 422
    assert "dataBase64" not in response.text
    assert db[media.INLINE].count_documents({}) == 0


def test_fake_and_truncated_images(stack):
    for data in (b"not an image", image_bytes()[:30]):
        assert post(stack[0], envelope(data)).status_code == 422


def test_pixel_bomb_rejected(stack, monkeypatch):
    monkeypatch.setattr(photos, "MAX_IMAGE_PIXELS", 10)
    assert post(stack[0]).status_code == 422


def test_large_photo_body_allowance_is_exact_path_only(stack):
    client, _ = stack
    # A valid PNG with ignorable trailing bytes exercises the separate raw body cap.
    assert post(client, envelope(image_bytes() + b"x" * 20000)).status_code == 202
    text = {"messageId": "large-text", "provider": "terminal", "senderId": "alice",
            "spaceId": "terminal", "text": "x" * 20000}
    assert post(client, text, "/internal/channels/inbound").status_code == 413
    assert post(client, text, "/internal/channels/authorize").status_code == 413
    assert MAX_PHOTO_BODY_BYTES == 27978412


def test_decoded_limit_even_at_same_encoded_length():
    message = InboundPhoto.model_validate(envelope(b"x" * (media.MAX_BYTES + 1)))
    with pytest.raises(ValueError, match="size"):
        photos.decode_photo(message)


def test_recovery_after_media_write_before_receipt_commit(stack, monkeypatch):
    client, db = stack
    original = db.channelMessages.update_one
    failed = False
    def interrupt(query, update, **kwargs):
        nonlocal failed
        if update.get("$set", {}).get("status") == "received" and not failed:
            failed = True
            raise OSError("interrupted receipt persistence")
        return original(query, update, **kwargs)
    monkeypatch.setattr(db.channelMessages, "update_one", interrupt)
    assert post(client).status_code == 503
    assert db.channelMessages.find_one({"kind": "inbound"})["status"] == "photo_pending"
    monkeypatch.setattr(media, "put_media", lambda *_a, **_k: pytest.fail("duplicate media upload"))
    assert post(client).json() == {"accepted": True, "duplicate": True}
    assert db[media.INLINE].count_documents({}) == 1


def test_incomplete_upload_is_not_blindly_retried(stack, monkeypatch):
    calls = []
    def broken(*args, **kwargs):
        calls.append(True)
        raise OSError("uncertain incomplete GridFS write")
    monkeypatch.setattr(media, "put_media", broken)
    assert post(stack[0]).status_code == 503
    assert post(stack[0]).status_code == 503
    assert len(calls) == 1
    assert stack[1].channelMessages.find_one({"kind": "inbound"})["status"] == "photo_pending"


def test_single_writer_under_concurrent_delivery(stack, monkeypatch):
    _, db = stack
    calls = []
    original = media.put_media
    def delayed(*args, **kwargs):
        calls.append(True)
        time.sleep(.02)
        return original(*args, **kwargs)
    monkeypatch.setattr(media, "put_media", delayed)
    message = InboundPhoto.model_validate(envelope())
    def receive(_):
        try:
            return photos.receive_photo(db, message)
        except conversations.PhotoPending:
            return "retry"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(receive, range(8)))
    assert {"accepted": True, "duplicate": False} in results
    assert len(calls) == 1 and db[media.INLINE].count_documents({}) == 1
    assert photos.receive_photo(db, message)["duplicate"] is True


def test_stop_after_reservation_prevents_write(stack, monkeypatch):
    client, db = stack
    original = db.channelMessages.insert_one
    def revoke(record, **kwargs):
        result = original(record, **kwargs)
        if record.get("receiptType") == "photo":
            db.contacts.update_many({}, {"$set": {"status": "stopped"}})
        return result
    monkeypatch.setattr(db.channelMessages, "insert_one", revoke)
    assert post(client).status_code == 403
    assert db[media.INLINE].count_documents({}) == 0


def test_existing_media_must_match_hash_and_kind(stack):
    client, db = stack
    assert post(client).status_code == 202
    db[media.INLINE].update_one({}, {"$set": {"metadata.kind": "audio"}})
    assert post(client).status_code == 409


def test_trusted_worker_attach_and_studio_photo_ids(stack):
    client, db = stack
    assert post(client).status_code == 202
    photo_id = db.channelMessages.find_one({"kind": "inbound"})["photoIds"][0]
    starts = []
    class Runs:
        def start_run(self, *args):
            starts.append(args)
            return "photo-run"
    service = ConversationService.for_contact(db, "family-a", "family-a:alice", runs=Runs())
    assert "attach_photos" not in {t.name for t in conversation_tools(service)}
    assert service.attach_photos([photo_id, "media/" + photo_id]) == {"photoCount": 1}
    service.save_story_draft("A memory with a photo", year=2000)
    service.submit_story()
    assert starts[0][-1] == [photo_id]


def test_cross_family_nonphoto_and_deleted_attachments_rejected(stack):
    _, db = stack
    foreign = media.put_media(db, "family-b", image_bytes(), content_type="image/png", kind="photo")
    audio = media.put_media(db, "family-a", b"audio", content_type="audio/wav", kind="audio")
    service = ConversationService.for_contact(db, "family-a", "family-a:alice")
    for ref in (foreign, audio, "invalid"):
        with pytest.raises(ValueError):
            service.attach_photos([ref])
    own = media.put_media(db, "family-a", image_bytes(), content_type="image/png", kind="photo")
    service.attach_photos([own])
    service.save_story_draft("A memory", year=2000)
    media.delete_media(db, "family-a", own)
    with pytest.raises(ValueError):
        service.submit_story()
    assert service._state()["status"] == "draft"


def test_photo_count_bound(stack):
    _, db = stack
    ids = [media.put_media(db, "family-a", image_bytes(), content_type="image/png", kind="photo") for _ in range(11)]
    service = ConversationService.for_contact(db, "family-a", "family-a:alice")
    service.attach_photos(ids[:10])
    with pytest.raises(ValueError):
        service.attach_photos(ids[10:])
    assert len(service._state()["draft"]["photoIds"]) == 10


def test_heif_requires_real_decoder(stack, monkeypatch):
    app, db = stack
    def unavailable():
        raise photos.PhotoDecoderUnavailable("unavailable")
    monkeypatch.setattr(photos, "_enable_heif", unavailable)
    def box(kind, payload):
        return (len(payload) + 8).to_bytes(4, "big") + kind + payload
    data = box(b"ftyp", b"heic\0\0\0\0heicmif1") + box(b"meta", b"\0\0\0\0") + box(b"mdat", b"encoded")
    assert post(app, envelope(data, mimeType="image/heic")).status_code == 415
    assert db["media.files"].count_documents({}) == 0


def test_fabricated_heif_rejected_even_with_registered_decoder(stack, monkeypatch):
    app, db = stack
    monkeypatch.setattr(photos, "_enable_heif", lambda: None)
    data = b"\0\0\0\x18ftypheic\0\0\0\0heicmif1"
    assert post(app, envelope(data, mimeType="image/heif")).status_code == 422
    assert db["media.files"].count_documents({}) == 0


def test_real_heif_when_decoder_installed(stack):
    plugin = pytest.importorskip("pillow_heif")
    plugin.register_heif_opener()
    output = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(output, format="HEIF")
    assert post(stack[0], envelope(output.getvalue(), mimeType="image/heic")).status_code == 202


def test_duplicate_preserves_worker_receipt_status(stack):
    client, db = stack
    assert post(client).status_code == 202
    db.channelMessages.update_one({"receiptType": "photo"}, {"$set": {"status": "processed"}})
    assert post(client).json() == {"accepted": True, "duplicate": True}
    assert db.channelMessages.find_one({"receiptType": "photo"})["status"] == "processed"
