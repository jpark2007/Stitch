"""Authenticated test outreach uses fake transport and the real consent boundary."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from channels import prompts
from db import conversations
from tests.channels.test_settings import stack, login, create, proof, PERSON


@pytest.fixture
def ready(stack, monkeypatch):
    client, db = stack
    login(client)
    assert proof(client, create(client).json()["code"]).status_code == 200
    class Sender:
        messages = []

        def send(self, message):
            self.messages.append(message)
    sender = Sender()
    monkeypatch.setattr("channels.outbound.BridgeSender", lambda: sender)
    monkeypatch.setenv("STITCH_CHANNELS_CALL_NUMBER", "+12292875827")
    return client, db, sender


def send(client, request_id=None):
    return client.post(PERSON + "/contact/test-prompt", json={"requestId": request_id or str(uuid4())})


def test_self_test_bypasses_cadence_and_reuses_ack(ready):
    client, db, sender = ready
    db.contacts.update_one({}, {"$set": {"quietHours": {"start": "00:00", "end": "23:59"}}})
    key = str(uuid4())
    assert send(client, key).json() == {"status": "sent", "duplicate": False}
    assert send(client, key).json() == {"status": "sent", "duplicate": True}
    assert len(sender.messages) == 1
    assert "+12292875827" in sender.messages[0]["text"] and "STOP" in sender.messages[0]["text"]
    assert sender.messages[0]["spaceId"] == "verified-room"
    assert not db.notifications.count_documents({})  # Test does not consume a weekly question.


def test_lost_ack_is_uncertain_and_never_repeated(ready):
    client, db, sender = ready
    def lost(message):
        sender.messages.append(message)
        raise TimeoutError()
    sender.send = lost
    key = str(uuid4())
    assert send(client, key).json() == {"status": "uncertain", "duplicate": False}
    assert send(client, key).json() == {"status": "uncertain", "duplicate": True}
    assert send(client).status_code == 409
    assert len(sender.messages) == 1
    assert db.conversations.find_one({"kind": "channelWorker"})["status"] == "blocked"


def test_concurrent_same_request_has_one_provider_effect(ready):
    _, db, sender = ready
    key = str(uuid4())
    def attempt(_):
        try:
            return prompts.send_test_prompt(db, "demo_family", "dad", "demo_user_david", key, sender)
        except prompts.TestPromptError as error:
            assert error.status == 409
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(6)))
    assert len(sender.messages) == 1
    assert any(result and result["status"] == "sent" for result in results)


def test_auth_scope_direct_proof_and_stop(ready):
    client, db, sender = ready
    assert client.post(PERSON.replace("demo_family", "other") + "/contact/test-prompt",
                       json={"requestId": str(uuid4())}).status_code == 404
    assert client.post(PERSON.replace("dad", "grandpa") + "/contact/test-prompt",
                       json={"requestId": str(uuid4())}).status_code in {403, 404}
    assert client.post(PERSON.replace("dad", "mom") + "/contact/test-prompt",
                       json={"requestId": str(uuid4())}).status_code == 403
    db.contacts.update_one({}, {"$set": {"spaceKind": "group"}})
    assert send(client).status_code == 403
    db.contacts.update_one({}, {"$set": {"spaceKind": "direct"}})
    client.post(PERSON + "/contact/stop", json={})
    assert send(client).status_code == 403
    assert not sender.messages


def test_stop_between_reservation_and_effect_suppresses_send(ready, monkeypatch):
    client, db, sender = ready
    original = conversations.update_worker
    def update(*args, **kwargs):
        result = original(*args, **kwargs)
        if any(x["status"] == "sending" for x in args[4].get("testPromptOutbox", [])):
            db.contacts.update_one({}, {"$set": {"status": "stopped"}})
        return result
    monkeypatch.setattr(conversations, "update_worker", update)
    assert send(client).json()["status"] == "uncertain"
    assert not sender.messages


def test_invalid_requests_and_unsettled_normal_outbox(ready):
    client, db, sender = ready
    for body in ({}, {"requestId": "not-a-uuid"}, {"requestId": str(uuid4()), "text": "injected"}):
        assert client.post(PERSON + "/contact/test-prompt", json=body).status_code == 422
    contact = db.contacts.find_one({})
    row = conversations.acquire_worker(db, "demo_family", str(contact["_id"]), "existing")
    conversations.update_worker(db, "demo_family", str(contact["_id"]), "existing",
                                {"status": "idle", "outbox": [{"status": "sending"}]})
    assert send(client).status_code == 409
    assert not sender.messages


def test_call_number_validation(monkeypatch):
    monkeypatch.setenv("STITCH_CHANNELS_CALL_NUMBER", "https://untrusted.invalid")
    assert prompts.call_number() is None
    assert prompts.prompt_text("Question?") == "Question?\n\nReply STOP to stop messages."


def test_call_copy_explains_demo_target_or_required_code(monkeypatch):
    monkeypatch.setenv("STITCH_CHANNELS_CALL_NUMBER", "+12292875827")
    monkeypatch.delenv("STITCH_CHANNELS_CALL_AUTO", raising=False)
    assert "private call code" in prompts.prompt_text("Question?")
    monkeypatch.setenv("STITCH_CHANNELS_CALL_AUTO", "macoskos:rick:1984")
    text = prompts.prompt_text("Question?")
    assert "Rick's demo book" in text and "No code is needed" in text
