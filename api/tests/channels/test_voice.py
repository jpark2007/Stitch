"""Signed voice intake and reserved transcription; synthetic audio, never a live provider."""

import asyncio
import base64
import io
import json
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from agent.conversation.demo import ScriptedConversation
from channels import voice
from channels.models import InboundVoice, MAX_PHOTO_BODY_BYTES
from channels.worker import ChannelWorker
from db import conversations, media
from tests.channels.test_inbox import stack, sign, send, envelope as text_envelope, add_contact
from tests.channels.test_worker import Sender, Runs

PATH = "/internal/channels/inbound-voice"


def wav_bytes(frames=80, value=b"\0\0"):
    output = io.BytesIO()
    with wave.open(output, "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(8000)
        sound.writeframes(value * frames)
    return output.getvalue()


def envelope(data=None, **changes):
    return {"messageId": "voice-1", "provider": "terminal", "spaceId": "room-1", "senderId": "alice",
        "attachmentId": "attachment-1", "mimeType": "audio/wav",
        "dataBase64": base64.b64encode(wav_bytes() if data is None else data).decode(), **changes}


def post(client, message=None):
    raw = json.dumps(envelope() if message is None else message).encode()
    return client.post(PATH, content=raw, headers=sign(raw, PATH))


class Transcriber:
    def __init__(self, text="We sailed with Grandma."):
        self.text, self.calls = text, []

    async def transcribe(self, data, *, content_type):
        self.calls.append((data, content_type))
        return self.text


@pytest.fixture
def voice_stack(stack):
    client, db = stack
    transcriber, sender, runs = Transcriber(), Sender(), Runs()
    client.app.state.channels_transcriber = transcriber
    worker = ChannelWorker(db, sender=sender, runs=runs, agent_factory=ScriptedConversation,
                           transcriber=transcriber)
    return client, db, worker, transcriber, sender, runs


def test_signed_pcm_note_is_stored_fast_then_transcribed_to_draft(voice_stack):
    client, db, worker, transcriber, sender, runs = voice_stack
    assert post(client).json() == {"accepted": True, "duplicate": False}
    row = db.channelMessages.find_one({"receiptType": "voice"})
    assert row["status"] == "received" and row["transcriptionStatus"] == "pending"
    assert row["principal"] == "contact:family-a:alice" and row["handle"] == "alice"
    assert "dataBase64" not in row and not transcriber.calls
    assert media.get_media(db, "family-a", row["audioId"]).kind == "audio"
    assert worker.tick()["processed"] == 1
    assert len(transcriber.calls) == 1
    row = db.channelMessages.find_one({"receiptType": "voice"})
    assert row["transcript"] == transcriber.text and row["transcriptionStatus"] == "complete"
    assert db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"] == transcriber.text
    assert "saved in the draft" in sender.messages[-1]["text"]
    assert not runs.starts
    assert post(client).json() == {"accepted": True, "duplicate": True}
    worker.tick()
    assert len(transcriber.calls) == 1


@pytest.mark.parametrize("change", [{"attachmentId": "other"}, {"spaceId": "other"},
    {"dataBase64": base64.b64encode(wav_bytes(value=b"\1\0")).decode()}])
def test_changed_voice_content_conflicts(voice_stack, change):
    client, db, *_ = voice_stack
    assert post(client).status_code == 202
    assert post(client, envelope(**change)).status_code == 409
    assert db[media.INLINE].count_documents({}) == 1


@pytest.mark.parametrize("voice_first", [True, False])
def test_text_voice_message_id_collision(voice_stack, voice_first):
    client, *_ = voice_stack
    text = lambda: send(client, text_envelope(messageId="voice-1"))
    audio = lambda: post(client)
    first, second = (audio, text) if voice_first else (text, audio)
    assert first().status_code == 202 and second().status_code == 409


@pytest.mark.parametrize("mode,expected", [("unknown", 404), ("stopped", 403), ("ambiguous", 409)])
def test_consent_before_decode_or_storage(voice_stack, monkeypatch, mode, expected):
    client, db, *_ = voice_stack
    if mode == "unknown":
        db.contacts.delete_many({})
    elif mode == "stopped":
        db.contacts.update_many({}, {"$set": {"status": "stopped"}})
    else:
        add_contact(db, family="family-b", person="person-b")
    monkeypatch.setattr(voice, "decode_voice", lambda *_: pytest.fail("decoded without consent"))
    assert post(client).status_code == expected
    assert db[media.INLINE].count_documents({}) == 0


def test_voice_requires_explicit_configuration(stack, monkeypatch):
    client, db = stack
    monkeypatch.delenv("STITCH_CHANNELS_TRANSCRIPTION", raising=False)
    assert post(client).status_code == 503
    assert db[media.INLINE].count_documents({}) == 0


@pytest.mark.parametrize("change", [{"mimeType": "audio/unknown"}, {"mimeType": "audio/mp4"},
    {"dataBase64": "data:audio/wav;base64,abc"}, {"dataBase64": "!!!!"}, {"dataBase64": ""},
    {"dataBase64": "YQ==\n"}, {"caption": "not supported"}, {"senderId": "x" * 257}])
def test_invalid_voice_boundary(voice_stack, change):
    client, db, *_ = voice_stack
    assert post(client, envelope(**change)).status_code == 422
    assert db[media.INLINE].count_documents({}) == 0


@pytest.mark.parametrize("data", [b"<html>not audio</html>", b"PK\3\4archive", b"RIFF\0\0\0\0WAVE", wav_bytes()[:-1]])
def test_fake_truncated_wav_rejected(voice_stack, data):
    assert post(voice_stack[0], envelope(data)).status_code == 422


def test_voice_large_raw_cap_is_scoped(voice_stack):
    client, _, *_ = voice_stack
    assert post(client, envelope(wav_bytes(frames=10000))).status_code == 202
    raw = json.dumps(text_envelope(text="x" * 20000)).encode()
    assert client.post("/internal/channels/inbound", content=raw,
        headers=sign(raw, "/internal/channels/inbound")).status_code == 413
    assert MAX_PHOTO_BODY_BYTES == 27978412


def test_decoded_limit_rejects_before_container_validation():
    value = envelope(b"x" * (media.MAX_BYTES + 1))
    message = InboundVoice.model_validate(value)
    with pytest.raises(ValueError, match="size"):
        voice.decode_voice(message)


def test_concurrent_duplicate_audio_has_single_writer(voice_stack, monkeypatch):
    client, db, *_ = voice_stack
    calls = []
    original = media.put_media
    def slow(*args, **kwargs):
        calls.append(1)
        time.sleep(0.02)
        return original(*args, **kwargs)
    monkeypatch.setattr(media, "put_media", slow)
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: post(client), range(6)))
    assert all(r.status_code in {202, 503} for r in responses)
    assert len(calls) == 1 and db[media.INLINE].count_documents({}) == 1
    assert post(client).json()["duplicate"] is True


def test_interrupted_upload_not_repeated(voice_stack, monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("partial chunks")
    monkeypatch.setattr(media, "put_media", fail)
    assert post(voice_stack[0]).status_code == 503
    assert post(voice_stack[0]).status_code == 503
    assert calls == [1]


@pytest.mark.parametrize("failure", [RuntimeError("provider failed"), asyncio.CancelledError()])
def test_failed_or_cancelled_provider_is_never_retried(voice_stack, failure):
    client, db, worker, transcriber, sender, _ = voice_stack
    async def fail(*args, **kwargs):
        transcriber.calls.append(1)
        raise failure
    transcriber.transcribe = fail
    assert post(client).status_code == 202
    assert worker.tick()["blocked"] == 1
    worker.tick()
    assert len(transcriber.calls) == 1 and not sender.messages
    assert db.channelMessages.find_one({"receiptType": "voice"})["transcriptionStatus"] == "uncertain"


def test_stop_after_reservation_prevents_provider_call(voice_stack, monkeypatch):
    client, db, worker, transcriber, sender, _ = voice_stack
    original = conversations.reserve_transcription
    def stop(*args):
        row = original(*args)
        db.contacts.update_many({}, {"$set": {"status": "stopped"}})
        return row
    monkeypatch.setattr(conversations, "reserve_transcription", stop)
    assert post(client).status_code == 202
    assert worker.tick()["blocked"] == 1
    assert not transcriber.calls and not sender.messages


def test_stop_during_transcription_suppresses_transcript_and_draft(voice_stack):
    client, db, worker, transcriber, sender, _ = voice_stack
    async def stop(*args, **kwargs):
        db.contacts.update_many({}, {"$set": {"status": "stopped"}})
        return "Private transcript"
    transcriber.transcribe = stop
    assert post(client).status_code == 202
    assert worker.tick()["blocked"] == 1
    assert "transcript" not in db.channelMessages.find_one({"receiptType": "voice"})
    assert not db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"]
    assert not sender.messages


def test_cross_family_audio_reference_cannot_reach_provider(voice_stack):
    client, db, worker, transcriber, *_ = voice_stack
    assert post(client).status_code == 202
    foreign = media.put_media(db, "family-b", wav_bytes(), content_type="audio/wav", kind="audio")
    db.channelMessages.update_one({"receiptType": "voice"}, {"$set": {"audioId": foreign}})
    assert worker.tick()["blocked"] == 1 and not transcriber.calls


def test_handle_remap_blocks_transcription(voice_stack):
    client, db, worker, transcriber, *_ = voice_stack
    assert post(client).status_code == 202
    db.contacts.update_many({}, {"$set": {"handle": "other-handle"}})
    assert worker.tick()["blocked"] == 1 and not transcriber.calls


def test_outbox_failure_keeps_transcript_and_never_retranscribes(voice_stack):
    client, db, worker, transcriber, sender, _ = voice_stack
    def fail(*args):
        raise TimeoutError("delivery unknown")
    sender.send = fail
    assert post(client).status_code == 202
    assert worker.tick()["blocked"] == 1
    worker.tick()
    assert len(transcriber.calls) == 1
    assert db.channelMessages.find_one({"receiptType": "voice"})["transcript"] == transcriber.text


@pytest.mark.parametrize("text", ["SUBMIT", "YEAR 1998", "KEEP", "YES"])
def test_transcribed_commands_are_story_words_not_authorization(voice_stack, text):
    client, db, worker, transcriber, _, runs = voice_stack
    transcriber.text = text
    assert post(client).status_code == 202
    assert worker.tick()["processed"] == 1
    draft = db.conversations.find_one({"principal": {"$exists": True}})["draft"]
    assert draft["text"] == text and not draft.get("year") and not runs.starts


def test_one_voice_provider_call_per_contact_tick(voice_stack):
    client, _, worker, transcriber, *_ = voice_stack
    assert post(client).status_code == 202
    assert post(client, envelope(messageId="voice-2")).status_code == 202
    assert worker.tick()["processed"] == 1 and len(transcriber.calls) == 1
    assert worker.tick()["processed"] == 1 and len(transcriber.calls) == 2


def test_concurrent_worker_ticks_transcribe_once(voice_stack):
    client, _, worker, transcriber, *_ = voice_stack
    assert post(client).status_code == 202
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: worker.tick(), range(6)))
    assert len(transcriber.calls) == 1


def test_transcript_persistence_failure_is_not_retried(voice_stack, monkeypatch):
    client, db, worker, transcriber, sender, _ = voice_stack
    def fail(*args):
        raise RuntimeError("database interrupted")
    monkeypatch.setattr(conversations, "complete_transcription", fail)
    assert post(client).status_code == 202
    assert worker.tick()["blocked"] == 1
    worker.tick()
    assert len(transcriber.calls) == 1 and not sender.messages
    assert db.channelMessages.find_one({"receiptType": "voice"})["transcriptionStatus"] == "uncertain"


@pytest.mark.parametrize("transcript", ["", " " * 10, "x" * 8001, "😀" * 2001, "\ud800", None])
def test_invalid_transcript_never_enters_draft(voice_stack, transcript):
    client, db, worker, transcriber, sender, _ = voice_stack
    transcriber.text = transcript
    assert post(client).status_code == 202
    assert worker.tick()["blocked"] == 1 and not sender.messages
    assert not db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"]


def test_pcm_wav_alias_and_compressed_structural_headers():
    assert voice.decode_voice(InboundVoice.model_validate(envelope(mimeType="audio/x-wav"))) == wav_bytes()
    def box(name, data):
        return (len(data) + 8).to_bytes(4, "big") + name + data
    # These are structural fixtures, not playable/decoder-qualified audio.
    mp4 = box(b"ftyp", b"M4A \0\0\0\0isom") + box(b"moov", b"hdlr\0soun") + box(b"mdat", b"compressed")
    fixtures = [("audio/m4a", mp4), ("audio/mp3", b"\xff\xfb\x90\x00encoded-frame"),
        ("audio/webm", b"\x1aE\xdf\xa3webmA_OPUS\x1fC\xb6ucluster")]
    for mime, data in fixtures:
        assert voice.decode_voice(InboundVoice.model_validate(envelope(data, mimeType=mime))) == data
        with pytest.raises(ValueError):
            voice.decode_voice(InboundVoice.model_validate(envelope(b"not-audio", mimeType=mime)))


def test_photo_and_voice_identity_collision(voice_stack):
    from tests.channels.test_photos import envelope as photo_envelope
    client, *_ = voice_stack
    body = json.dumps(photo_envelope(messageId="voice-1")).encode()
    path = "/internal/channels/inbound-photo"
    assert client.post(path, content=body, headers=sign(body, path)).status_code == 202
    assert post(client).status_code == 409
