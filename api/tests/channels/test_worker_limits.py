"""Preflight recovery tests; no live providers or daily quotas."""
import pytest
from agent.conversation.agent import ConversationAgent
from agent.conversation.demo import ScriptedConversation
from agent.conversation.service import ConversationService
from db import conversations, media
from tests.channels.test_inbox import stack, envelope
from tests.channels.test_worker import worker_stack, input_text

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


def test_submit_missing_configuration_keeps_draft(stack, monkeypatch):
    client, db, worker, sender, _ = worker_stack(stack)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    service = ConversationService.for_contact(db, "family-a", "family-a:alice")
    service.save_story_draft("Exact story", year=1998)
    worker.runs = None
    input_text(client, "SUBMIT")
    assert worker.tick()["blocked"] == 0
    assert service._state()["status"] == "draft"
    assert "not configured" in sender.messages[-1]["text"]


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


def test_readonly_invocation_limit_completes_receipt_without_replaying_calls(stack):
    from agent.fake_llm import ScriptedChatModel, tool_call
    client, db, worker, sender, _ = worker_stack(stack)
    model = ScriptedChatModel(responder=lambda *_: tool_call("get_open_questions", {}))
    worker.agent_factory = lambda service: ConversationAgent(service, model=model, worker_managed=True, max_model_calls=1)
    input_text(client, "First exact words")
    assert worker.tick()["blocked"] == 0
    assert len(model.calls) == 1
    assert worker.tick()["processed"] == 0 and len(model.calls) == 1
    input_text(client, "Next exact words")
    assert worker.tick()["blocked"] == 0 and len(model.calls) == 2
    assert db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"] == "First exact words\nNext exact words"


def test_uncertain_model_failure_is_not_preflight_recoverable(stack):
    from agent.fake_llm import ScriptedChatModel
    client, db, worker, sender, _ = worker_stack(stack)
    def fail(*_):
        raise RuntimeError("provider outcome unknown")
    worker.agent_factory = lambda service: ConversationAgent(service, model=ScriptedChatModel(responder=fail), worker_managed=True)
    input_text(client, "Preserve these words")
    assert worker.tick()["blocked"] == 1
    with pytest.raises(RuntimeError, match="proven"):
        conversations.resume_preflight_worker(db, "family-a", "family-a:alice")
    assert not sender.messages
