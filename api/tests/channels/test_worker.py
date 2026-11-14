"""Worker and consent regressions; no external services or credentials."""

from datetime import datetime, timezone

from db import conversations
from tests.channels.test_inbox import stack, send, envelope, add_contact

import asyncio
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
import schemas
from agent.conversation.demo import ScriptedConversation
from agent.conversation.service import ConversationService
from channels.outbound import BridgeSender
from channels.worker import ChannelWorker
from db import media


class Sender:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


class Runs:
    def __init__(self):
        self.starts, self.answers, self.events = [], [], []

    def start_run(self, *args):
        self.starts.append(args)
        self.events.append(("question", schemas.RUN_EVENTS["question"](text="Who was there?")))
        return "test-run"

    async def _stream(self):
        for event in self.events:
            yield event

    def stream_run(self, family, run):
        assert family == "family-a" and run == "test-run"
        return self._stream()

    def answer_run(self, *args):
        self.answers.append(args)
        self.events.append(("done", schemas.RUN_EVENTS["done"](memoryId="verified-memory")))


def worker_stack(stack):
    client, db = stack
    sender, runs = Sender(), Runs()
    worker = ChannelWorker(db, sender=sender, runs=runs, agent_factory=ScriptedConversation)
    return client, db, worker, sender, runs


def input_text(client, text):
    assert send(client, envelope(messageId=str(uuid4()), text=text)).status_code == 202


def test_scripted_text_photo_to_actual_run_question_and_done(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    photo = media.put_media(db, "family-a", b"fixture", content_type="image/png", kind="photo")
    contact = db.contacts.find_one({})
    conversations.save_inbound(db, contact, envelope(messageId="photo", text=""))
    db.channelMessages.update_one({"messageId": "photo"}, {"$set": {"photoIds": [photo]}})
    input_text(client, "We watched the boats.")
    assert worker.tick()["processed"] == 2
    input_text(client, "YEAR 1998")
    worker.tick()
    input_text(client, "SUBMIT")
    assert worker.tick()["blocked"] == 0
    assert len(runs.starts) == 1 and runs.starts[0][-1] == [photo]
    assert sender.messages[-1]["text"] == "Who was there?"
    assert not any("/memories/" in m["text"] for m in sender.messages)
    input_text(client, "My mother.")
    assert worker.tick()["blocked"] == 0
    assert runs.answers[0][2] == "contact:family-a:alice"
    assert sender.messages[-1]["text"].endswith("/memories/verified-memory?family=family-a")
    assert worker.tick()["processed"] == 0
    assert db.conversations.find_one({"kind": "channelWorker"})["pollRun"] is False


def test_command_boundaries_in_backlog(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    for text in ("We went fishing.", "YEAR 1990", "SUBMIT"):
        input_text(client, text)
    for _ in range(3):
        assert worker.tick()["processed"] == 1
    assert runs.starts[0][3].year == 1990
    assert runs.starts[0][3].text == "We went fishing."


def test_concurrent_ticks_reserve_once(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    input_text(client, "One story.")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: worker.tick(), range(6)))
    assert len(sender.messages) == 1
    assert db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"] == "One story."


def test_uncertain_send_never_retried(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    def fail(message):
        sender.messages.append(message)
        raise TimeoutError("ack lost")
    sender.send = fail
    input_text(client, "One story.")
    assert worker.tick()["blocked"] == 1
    worker.tick()
    assert len(sender.messages) == 1
    row = db.conversations.find_one({"kind": "channelWorker"})
    assert row["status"] == "blocked" and row["outbox"][0]["status"] == "sending"


def test_stop_during_model_prevents_send_and_reentry(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    class StopAgent:
        async def ainvoke(self, *args, **kwargs):
            db.contacts.update_many({}, {"$set": {"status": "stopped"}})
    worker.agent_factory = lambda _: StopAgent()
    input_text(client, "One story.")
    assert worker.tick()["blocked"] == 1
    assert not sender.messages


def test_cross_family_and_remapped_receipt_cannot_send(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    input_text(client, "One story.")
    db.contacts.update_one({}, {"$set": {"personId": "foreign"}})
    db.people.insert_one({"_id": "foreign", "familyId": "family-b"})
    assert worker.tick()["blocked"] == 1
    assert not sender.messages


def test_durable_relay_replays_until_ack(stack):
    _, db = stack
    runs = Runs()
    service = ConversationService.for_contact(db, "family-a", "family-a:alice", runs=runs)
    service.save_story_draft("Story", year=1998)
    service.submit_story()
    events = asyncio.run(service.relay_run_events(durable=True))
    assert events == asyncio.run(service.relay_run_events(durable=True))
    service.acknowledge_relay(events)
    assert asyncio.run(service.relay_run_events(durable=True)) == []


def test_relay_outbox_failure_keeps_buffer(stack, monkeypatch):
    client, db, worker, sender, runs = worker_stack(stack)
    for text in ("Story.", "YEAR 1998"):
        input_text(client, text)
        worker.tick()
    original = conversations.update_worker
    def fail(db, family, contact, token, fields):
        if any(x["message"]["text"] == "Who was there?" for x in fields.get("outbox", [])):
            raise RuntimeError("outbox database down")
        return original(db, family, contact, token, fields)
    monkeypatch.setattr(conversations, "update_worker", fail)
    input_text(client, "SUBMIT")
    assert worker.tick()["blocked"] == 1
    state = db.conversations.find_one({"principal": {"$exists": True}})
    assert state["relayBuffer"][0]["event"] == "question"
    assert not any(m["text"] == "Who was there?" for m in sender.messages)


def test_signed_sender_contract_no_redirect_and_bounded_reply():
    secret = "test-secret-" * 4
    seen = []
    def handler(request):
        seen.append(request)
        raw = request.content
        signed = (f"POST\n/send\n{request.headers['x-channels-timestamp']}\n"
                  f"{request.headers['x-channels-nonce']}\n").encode() + raw
        assert request.headers["x-channels-signature"] == hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        return httpx.Response(200, json={"status": "sent"})
    sender = BridgeSender(secret=secret, transport=httpx.MockTransport(handler))
    message = {"messageId": "out-1", "provider": "terminal", "spaceId": "room", "text": "Hello"}
    sender.send(message)
    assert len(seen) == 1 and json.loads(seen[0].content) == message
    sender.transport = httpx.MockTransport(lambda _: httpx.Response(302, headers={"location": "https://example.invalid"}))
    with pytest.raises(Exception):
        sender.send(message)
    sender.transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 1025))
    with pytest.raises(RuntimeError, match="too large"):
        sender.send(message)


@pytest.mark.parametrize("url", ["https://127.0.0.1", "http://example.com", "http://localhost", "http://127.0.0.1/path", "http://user@127.0.0.1"])
def test_sender_rejects_nonlocal_destination(url):
    with pytest.raises(ValueError):
        BridgeSender(base_url=url, secret="dummy-" * 8)


def test_first_five_locked_contacts_do_not_starve_healthy_contact(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    for index in range(6):
        contact = add_contact(db, family=f"queue-{index}", handle=f"sender-{index}", person=f"person-{index}")
        conversations.save_inbound(db, contact, envelope(messageId=f"queue-{index}", senderId=contact["handle"]))
        if index < 5:
            conversations.acquire_worker(db, contact["familyId"], contact["_id"], f"crashed-{index}")
    assert worker.tick()["processed"] == 1
    assert len(sender.messages) == 1


def test_linked_principal_changed_since_receipt_blocks_work(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    input_text(client, "Private story.")
    db.contacts.update_one({}, {"$set": {"userId": "new-user"}})
    assert worker.tick()["blocked"] == 1
    assert not sender.messages


def test_worker_graph_has_no_submission_or_relay_tools(stack):
    from agent.conversation.agent import ConversationAgent, conversation_tools
    from agent.fake_llm import ScriptedChatModel
    from langchain_core.messages import AIMessage
    _, db = stack
    service = ConversationService.for_contact(db, "family-a", "family-a:alice")
    assert {t.name for t in conversation_tools(service, worker_managed=True)} == {
        "get_open_questions"}
    model = ScriptedChatModel(responder=lambda *_: AIMessage(content="Draft response"))
    agent = ConversationAgent(service, model=model, worker_managed=True)
    asyncio.run(agent.ainvoke("Hello"))
    assert service._state()["modelCalls"] == 1


def test_uncertain_model_not_reinvoked(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    calls = []
    class Uncertain:
        async def ainvoke(self, *args, **kwargs):
            calls.append(1)
            raise TimeoutError("model interrupted")
    worker.agent_factory = lambda _: Uncertain()
    input_text(client, "Private story")
    assert worker.tick()["blocked"] == 1
    worker.tick()
    assert calls == [1] and not sender.messages
    assert db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"] == "Private story"
    assert db.channelMessages.find_one({"kind": "inbound"})["status"] == "processing"


@pytest.mark.parametrize("kind,reply,expected", [("props", "yes", False), ("props", "KEEP", True),
    ("merge", "sounds fine", False), ("merge", "YES", True), ("merge", "NO", True)])
def test_exact_pending_approval_commands(stack, kind, reply, expected):
    client, db, worker, sender, runs = worker_stack(stack)
    for text in ("Story.", "YEAR 1998", "SUBMIT"):
        input_text(client, text)
        worker.tick()
    data = {"props": [{"id": "own-prop", "name": "A prop"}]} if kind == "props" else {"text": "Same person?"}
    db.conversations.update_one({"principal": {"$exists": True}}, {"$set": {"pending": {"kind": kind, "data": data}}})
    input_text(client, reply)
    assert worker.tick()["blocked"] == 0
    assert bool(runs.answers) == expected
    if expected and kind == "props":
        assert runs.answers[0][3].props[0].id == "own-prop"
    if expected and kind == "merge":
        assert runs.answers[0][3].approve == (reply == "YES")


def test_enabled_tick_requires_explicit_configuration(stack, monkeypatch):
    client, _ = stack
    monkeypatch.setenv("STITCH_CHANNELS_WORKER", "1")
    monkeypatch.delenv("CHANNELS_CONVERSATION_MODE", raising=False)
    assert send(client, {}, "/internal/channels/tick").status_code == 503


def test_interview_followup_reaches_user_and_words_are_saved(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    class Question:
        async def ainvoke(self, *args, **kwargs):
            return "What did the boat look like?"
    worker.agent_factory = lambda _: Question()
    input_text(client, "We sailed together.")
    assert worker.tick()["processed"] == 1
    assert "What did the boat look like?" in sender.messages[-1]["text"]
    assert db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"] == "We sailed together."


def test_handle_change_blocks_old_receipt_space(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    input_text(client, "Old private story.")
    db.contacts.update_one({}, {"$set": {"handle": "new-handle"}})
    assert worker.tick()["blocked"] == 1
    assert not sender.messages


def test_remapped_middle_receipt_cannot_mix_into_original_batch(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    input_text(client, "Original A.")
    input_text(client, "Private B.")
    db.channelMessages.update_one({"text": "Private B."}, {"$set": {"principal": "other-user"}})
    input_text(client, "Original A again.")
    assert worker.tick()["blocked"] == 1
    assert not sender.messages
    assert not db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"]


def test_scan_cursor_advances_beyond_hundred_locked_receipts(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    for index in range(101):
        contact = add_contact(db, family=f"f-{index}", handle=f"h-{index}", person=f"p-{index}")
        conversations.save_inbound(db, contact, envelope(messageId=str(index), senderId=contact["handle"]))
        conversations.acquire_worker(db, contact["familyId"], contact["_id"], str(index))
    input_text(client, "Healthy queue.")
    for _ in range(3):
        worker.tick()
    assert len(sender.messages) == 1


@pytest.mark.parametrize("suffix", [" ", "\n", "\ufeff"])
def test_outbound_secret_matches_shared_whitespace_rule(suffix):
    with pytest.raises(ValueError):
        BridgeSender(secret="dummy-secret-" * 4 + suffix)


def test_outbound_total_deadline_stops_trickling_response(monkeypatch):
    from channels import outbound
    monkeypatch.setattr(outbound, "SEND_TIMEOUT_SECONDS", 0.02)
    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                await asyncio.sleep(0.005)
                yield b" "
    sender = BridgeSender(secret="dummy-secret-" * 4,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Trickle())))
    with pytest.raises(TimeoutError):
        sender.send({"messageId": "out", "provider": "terminal", "spaceId": "room", "text": "Hi"})


def test_invalid_year_can_be_corrected_without_operator(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    input_text(client, "YEAR 0001")
    assert worker.tick()["blocked"] == 0
    input_text(client, "YEAR 1998")
    assert worker.tick()["blocked"] == 0
    assert db.conversations.find_one({"principal": {"$exists": True}})["draft"]["year"] == 1998


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
