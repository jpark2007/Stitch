"""Immediate durable text visibility, strict owner/proof scope and optional studio."""

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest

from channels.routes import router as inbound_router
from channels.text_drafts import ordinary_text
from channels.worker import ChannelWorker
from db import conversations
from tests.channels.test_settings import stack, login, create, proof, PERSON, BODY, SECRET
from tests.channels.test_inbox import sign
from tests.channels.test_worker import Sender, Runs
from agent.conversation.demo import ScriptedConversation


@pytest.fixture
def linked(stack):
    client, db = stack
    client.app.include_router(inbound_router)
    login(client)
    assert proof(client, create(client).json()["code"]).status_code == 200
    return client, db


def receive(client, text, message_id=None, **changes):
    data = {"messageId": message_id or str(uuid4()), "provider": "imessage",
            "spaceId": "verified-room", "senderId": BODY["handle"], "text": text, **changes}
    raw = json.dumps(data).encode()
    path = "/internal/channels/inbound"
    return client.post(path, content=raw, headers=sign(raw, path, secret=SECRET))


def drafts(client):
    return client.get(PERSON + "/text-drafts")


def test_text_visible_before_any_worker_year_or_submit_and_duplicate_safe(linked):
    client, db = linked
    text = "  Linda sewed overalls.\nWe tipped the canoe!  "
    assert receive(client, text, "same-message").status_code == 202
    assert receive(client, text, "same-message").json()["duplicate"] is True
    assert receive(client, "changed", "same-message").status_code == 409
    response = drafts(client)
    assert response.headers["cache-control"] == "no-store"
    saved = response.json()["drafts"]
    assert len(saved) == 1 and saved[0]["text"] == text
    assert set(saved[0]) == {"id", "text", "createdAt"}
    assert db.memories.count_documents({"title": "Linda sewed overalls"}) == 0
    assert db.conversations.count_documents({"kind": "channelWorker"}) == 0


@pytest.mark.parametrize("text", ["STOP", "ＳＴＯＰ please", "stop\u200b!", "CANCEL", "HELP", "CALL",
                                      "NEW", "SUBMIT", "KEEP", "YES", "NO", "YEAR 1984", "YEAR bad", "LINK abc", "  "])
def test_controls_are_not_text_drafts(text):
    assert ordinary_text(text) is False


def test_story_containing_control_words_is_preserved(linked):
    client, _ = linked
    text = "I said STOP when the canoe hit the bank; YES, we laughed."
    assert receive(client, text).status_code == 202
    assert drafts(client).json()["drafts"][0]["text"] == text


def test_commands_never_listed_and_status_does_not_hide_saved_words(linked):
    client, db = linked
    for text in ["YEAR 1984", "SUBMIT", "KEEP", "NEW", "A real memory."]:
        assert receive(client, text).status_code == 202
    db.channelMessages.update_many({"kind": "inbound"}, {"$set": {"status": "processed"}})
    assert [row["text"] for row in drafts(client).json()["drafts"]] == ["A real memory."]


def test_owner_family_mapping_and_direct_proof_isolation(linked):
    client, db = linked
    assert receive(client, "Own story.").status_code == 202
    assert receive(client, "Private group story.", spaceId="group-room").status_code == 202
    row = db.channelMessages.find_one({"text": "Own story."})
    for name, changes in [("foreign", {"familyId": "foreign"}), ("other", {"principal": "other-user"}),
                          ("legacy", {"contactProof": None}), ("photo", {"receiptType": "photo"})]:
        db.channelMessages.insert_one({**row, "_id": name, "text": name, **changes})
    assert [r["text"] for r in drafts(client).json()["drafts"]] == ["Own story."]
    assert client.get(PERSON.replace("demo_family", "foreign") + "/text-drafts").status_code == 404
    assert client.get(PERSON.replace("dad", "mom") + "/text-drafts").status_code == 403
    db.people.update_one({"_id": "dad"}, {"$set": {"userId": "different-owner"}})
    assert drafts(client).status_code == 403


def test_stop_and_fresh_consent_do_not_expose_previous_generation(linked):
    client, _ = linked
    receive(client, "Old generation.")
    client.post(PERSON + "/contact/stop", json={})
    assert drafts(client).status_code == 403
    assert proof(client, create(client).json()["code"]).status_code == 200
    assert drafts(client).json() == {"drafts": []}
    receive(client, "Fresh consent story.")
    assert [r["text"] for r in drafts(client).json()["drafts"]] == ["Fresh consent story."]


def test_list_is_bounded_newest_first(linked):
    client, db = linked
    receive(client, "Base.")
    base = db.channelMessages.find_one({"kind": "inbound"})
    now = datetime.now(timezone.utc)
    for index in range(120):
        db.channelMessages.insert_one({**base, "_id": f"bounded-{index}", "text": str(index),
                                      "createdAt": now + timedelta(seconds=index)})
    result = drafts(client).json()["drafts"]
    assert len(result) == 50 and result[0]["text"] == "119" and result[-1]["text"] == "70"


def test_worker_preserves_follow_up_flow_and_accessible_drafts(linked):
    from agent.conversation.demo import ScriptedConversation
    client, db = linked
    sender, runs = Sender(), Runs()
    runs.stream_run = lambda family, run: runs._stream()
    worker = ChannelWorker(db, sender=sender, agent_factory=ScriptedConversation, runs=runs,
                           web_url="http://localhost:3114", scheduler_enabled=False)
    receive(client, "This story is saved immediately.")
    assert worker.tick()["processed"] == 1
    assert drafts(client).json()["drafts"][0]["text"] == "This story is saved immediately."
    receive(client, "YEAR 1998", "follow-year")
    worker.tick()
    receive(client, "SUBMIT", "follow-submit")
    assert worker.tick()["blocked"] == 0
    assert len(runs.starts) == 1
    assert sender.messages[-1]["text"] == "Who was there?"


def test_failed_ack_does_not_hide_draft_or_retry_send(linked):
    client, db = linked
    sender = Sender()
    def lost(message):
        sender.messages.append(message)
        raise TimeoutError()
    sender.send = lost
    worker = ChannelWorker(db, sender=sender, agent_factory=ScriptedConversation, runs=Runs(), scheduler_enabled=False)
    receive(client, "Saved even when acknowledgement is lost.")
    assert worker.tick()["blocked"] == 1
    assert drafts(client).json()["drafts"][0]["text"] == "Saved even when acknowledgement is lost."
    worker.tick()
    assert len(sender.messages) == 1
