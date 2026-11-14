"""Worker and consent regressions; no external services or credentials."""

from datetime import datetime, timezone

from db import conversations
from tests.channels.test_inbox import stack, send, envelope, add_contact


def test_stop_collision_revokes_all_families(stack):
    client, db = stack
    assert send(client).status_code == 202
    add_contact(db, family="family-b", person="person-b")
    response = send(client, envelope(text="STOP"))
    assert response.status_code == 202
    assert response.json()["stopped"] is True
    assert all(row["status"] == "stopped" for row in db.contacts.find({}))


def test_stop_receipt_failure_does_not_block_revocation(stack, monkeypatch):
    client, db = stack
    def fail(*args, **kwargs):
        raise RuntimeError("receipt unavailable")
    monkeypatch.setattr(conversations, "save_inbound", fail)
    assert send(client, envelope(text="STOP")).json()["stopped"] is True
    assert db.contacts.find_one({})["status"] == "stopped"


def test_nonce_retention_exceeds_clock_skew_window(stack):
    _, db = stack
    timestamp = int(datetime.now(timezone.utc).timestamp())
    assert conversations.consume_nonce(db, "clock-skew-test", timestamp)
    row = db.channelMessages.find_one({"_id": "auth:clock-skew-test"})
    assert row["expireAt"].timestamp() >= timestamp + 900
