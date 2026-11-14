"""Daily spend limits and recoverable preflight failures; no live providers."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from agent.conversation.agent import ConversationAgent
from agent.conversation.demo import ScriptedConversation
from agent.conversation.service import ConversationService
from db import conversations, media
from tests.channels.test_conversation import setup
from tests.channels.test_inbox import stack, envelope
from tests.channels.test_worker import worker_stack, input_text


def new_story(service):
    state = service._state()
    service._update(state, {"terminalEvent": {"event": "done", "memoryId": "fixture"}})
    c = service.context
    worker = conversations.acquire_worker(service.db, c.family_id, c.contact_id, "new-story")
    conversations.update_worker(service.db, c.family_id, c.contact_id, "new-story", {"runFinished": True})
    service.start_new_story("new-story")
    conversations.update_worker(service.db, c.family_id, c.contact_id, "new-story", {"status": "idle"})


def test_three_studio_runs_per_utc_day_survive_new_and_reconstruction(setup):
    db, service, runs = setup
    now = datetime.now(timezone.utc).replace(hour=23, minute=59, second=0)
    service.now = lambda: now
    for _ in range(3):
        service.save_story_draft("Original teller words", year=1998)
        service.submit_story()
        new_story(service)
    service.save_story_draft("Fourth story remains unchanged", year=1999)
    restored = ConversationService.for_contact(db, "family-a", "contact-a", runs=runs, clock=lambda: now)
    before = restored._state()["draft"]
    with pytest.raises(conversations.ConversationBudgetExceeded):
        restored.submit_story()
    assert len(runs.starts) == 3 and restored._state()["draft"] == before
    assert restored._state()["status"] == "draft"
    now += timedelta(minutes=2)
    restored.submit_story()
    assert len(runs.starts) == 4 and restored._state()["studioSubmissions"] == 1


def test_concurrent_submissions_reserve_once_and_atomic_model_daily_cap(setup):
    db, service, runs = setup
    service.save_story_draft("Only once", year=2001)
    def submit(_):
        try:
            service.submit_story()
        except RuntimeError:
            pass
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(submit, range(16)))
    assert len(runs.starts) == 1 and service._state()["studioSubmissions"] == 1
    def call(_):
        try:
            conversations.take_conversation_model_call(db, "family-a", "contact-a", 3)
            return True
        except conversations.ConversationBudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(call, range(16))) == 3
    assert service._state()["modelCalls"] == 3


def test_midnight_resets_daily_model_budget_without_backward_rollover(setup):
    db, service, _ = setup
    now = datetime.now(timezone.utc)
    conversations.take_conversation_model_call(db, "family-a", "contact-a", 1, now=now)
    tomorrow = now + timedelta(days=1)
    conversations.take_conversation_model_call(db, "family-a", "contact-a", 1, now=tomorrow)
    with pytest.raises(conversations.ConversationBudgetExceeded):
        conversations.take_conversation_model_call(db, "family-a", "contact-a", 1, now=now)
    service.now = lambda: tomorrow
    assert service._state()["modelCalls"] == 1


def test_budget_rejection_keeps_raw_draft_and_worker_usable_next_day(stack):
    client, db, worker, sender, _ = worker_stack(stack)
    now = datetime.now(timezone.utc); worker.now = lambda: now
    service = ConversationService.for_contact(db, "family-a", "family-a:alice", clock=lambda: now)
    service.save_story_draft("Existing exact words")
    db.conversations.update_one({"_id": conversations.conversation_key("family-a", "family-a:alice")}, {"$set": {"modelCalls": 32}})
    input_text(client, "Rejected exact words")
    assert worker.tick()["blocked"] == 0
    assert service._state()["draft"]["text"] == "Existing exact words"
    assert "midnight UTC" in sender.messages[-1]["text"]
    now += timedelta(days=1)
    input_text(client, "Accepted tomorrow")
    assert worker.tick()["blocked"] == 0
    assert service._state()["draft"]["text"] == "Existing exact words\nAccepted tomorrow"


def test_missing_model_configuration_does_not_mutate_or_poison_draft(stack, monkeypatch):
    client, db, worker, sender, _ = worker_stack(stack)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    worker.agent_factory = lambda service: ConversationAgent(service, worker_managed=True)
    input_text(client, "Not saved while unavailable")
    assert worker.tick()["blocked"] == 0
    assert "not configured" in sender.messages[-1]["text"]
    assert db.conversations.find_one({"principal": {"$exists": True}})["draft"] == {"text": ""}
    worker.agent_factory = ScriptedConversation
    input_text(client, "Retry when configured")
    assert worker.tick()["blocked"] == 0


def test_submit_missing_configuration_keeps_draft_and_daily_slot(stack, monkeypatch):
    client, db, worker, sender, _ = worker_stack(stack)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    service = ConversationService.for_contact(db, "family-a", "family-a:alice")
    service.save_story_draft("Exact story", year=1998)
    worker.runs = None
    input_text(client, "SUBMIT")
    assert worker.tick()["blocked"] == 0
    assert service._state()["status"] == "draft" and service._state()["studioSubmissions"] == 0
    assert "not configured" in sender.messages[-1]["text"]


def test_worker_studio_daily_limit_replies_and_recovers_next_day(stack):
    client, db, worker, sender, runs = worker_stack(stack)
    now = datetime.now(timezone.utc); worker.now = lambda: now
    service = ConversationService.for_contact(db, "family-a", "family-a:alice", clock=lambda: now)
    service.save_story_draft("Draft kept at cap", year=1998)
    db.conversations.update_one({"_id": conversations.conversation_key("family-a", "family-a:alice")}, {"$set": {"studioSubmissions": 3}})
    input_text(client, "SUBMIT")
    assert worker.tick()["blocked"] == 0 and not runs.starts
    assert "3 studio submissions" in sender.messages[-1]["text"]
    assert service._state()["draft"]["text"] == "Draft kept at cap"
    now += timedelta(days=1)
    input_text(client, "SUBMIT")
    assert worker.tick()["blocked"] == 0 and len(runs.starts) == 1


def test_worker_readonly_graph_budget_exhaustion_is_recoverable(stack):
    from agent.fake_llm import ScriptedChatModel, tool_call
    client, db, worker, sender, _ = worker_stack(stack)
    model = ScriptedChatModel(responder=lambda *_: tool_call("get_open_questions", {}))
    worker.agent_factory = lambda service: ConversationAgent(service, model=model, worker_managed=True, max_model_calls=1)
    input_text(client, "Saved before the bounded interview")
    assert worker.tick()["blocked"] == 0
    assert len(model.calls) == 1 and "interview limit" in sender.messages[-1]["text"]
    input_text(client, "Not appended after known exhaustion")
    assert worker.tick()["blocked"] == 0
    assert db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"] == "Saved before the bounded interview"
    assert len(model.calls) == 1


@pytest.mark.parametrize("overflow", ["text", "photos"])
def test_combined_input_limits_precede_any_draft_mutation(stack, overflow):
    client, db, worker, sender, _ = worker_stack(stack)
    service = ConversationService.for_contact(db, "family-a", "family-a:alice")
    photos = [media.put_media(db, "family-a", b"fixture", content_type="image/png", kind="photo") for _ in range(11)]
    service.save_story_draft("x" * (20_000 if overflow == "text" else 10), year=1998)
    service.attach_photos(photos[:10] if overflow == "photos" else [])
    before = service._state()["draft"]
    contact = db.contacts.find_one({})
    conversations.save_inbound(db, contact, envelope(messageId="over-limit", text="must not append"))
    db.channelMessages.update_one({"messageId": "over-limit"}, {"$set": {"photoIds": [photos[-1]], "receiptType": "photo"}})
    assert worker.tick()["blocked"] == 0
    assert service._state()["draft"] == before
    assert "not added" in sender.messages[-1]["text"]
    input_text(client, "YEAR 1999")
    assert worker.tick()["blocked"] == 0 and service._state()["draft"]["year"] == 1999


def test_operator_recovery_requires_proven_preflight_and_preserves_uncertainty(stack):
    client, db, worker, sender, _ = worker_stack(stack)
    worker.agent_factory = lambda _: (_ for _ in ()).throw(RuntimeError("construction failure before invoke"))
    input_text(client, "Safe to retry before effects")
    assert worker.tick()["blocked"] == 1
    row = db.conversations.find_one({"kind": "channelWorker"})
    assert row["reason"] == "preflight_only" and not sender.messages
    conversations.resume_preflight_worker(db, "family-a", "family-a:alice")
    worker.agent_factory = ScriptedConversation
    assert worker.tick()["processed"] == 1
    db.conversations.update_one({"kind": "channelWorker"}, {"$set": {
        "status": "blocked", "reason": "reconciliation_required", "preflightOnly": False}})
    with pytest.raises(RuntimeError, match="proven"):
        conversations.resume_preflight_worker(db, "family-a", "family-a:alice")
    assert db.conversations.find_one({"kind": "channelWorker"})["status"] == "blocked"


@pytest.mark.parametrize("uncertain", ["outbox", "run", "answer", "voice"])
def test_operator_cannot_clear_uncertain_effects_even_with_preflight_marker(stack, uncertain):
    _, db, _, _, _ = worker_stack(stack)
    service = ConversationService.for_contact(db, "family-a", "family-a:alice")
    conversations.acquire_worker(db, "family-a", "family-a:alice", "token")
    conversations.update_worker(db, "family-a", "family-a:alice", "token", {
        "status": "blocked", "reason": "preflight_only", "preflightOnly": True})
    if uncertain == "outbox":
        db.conversations.update_one({"kind": "channelWorker"}, {"$set": {"outbox": [{"status": "sending"}]}})
    elif uncertain == "run":
        service._update(service._state(), {"status": "submission_uncertain"})
    elif uncertain == "answer":
        service._update(service._state(), {"answerStatus": "sending"})
    else:
        db.channelMessages.insert_one({"familyId": "family-a", "contactId": "family-a:alice", "status": "processing",
            "workerToken": "token", "receiptType": "voice", "transcriptionStatus": "transcribing"})
    with pytest.raises(RuntimeError):
        conversations.resume_preflight_worker(db, "family-a", "family-a:alice")
    assert db.conversations.find_one({"kind": "channelWorker"})["status"] == "blocked"
