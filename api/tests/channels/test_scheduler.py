"""Manual scheduler and story lifecycle: local records, fake studio and sender only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import asyncio

import pytest
import schemas

from agent.conversation import ConversationService
from agent.conversation.demo import ScriptedConversation
from agent.conversation.agent import conversation_tools
from channels import scheduler
from channels.worker import ChannelWorker
from db import conversations, memories, question_bank
from tests.channels.test_inbox import stack, send, envelope, add_contact
from tests.channels.test_worker import Sender, Runs, input_text
from tests.channels.test_voice import voice_stack, post as post_voice, envelope as voice_envelope

NOW = datetime(2026, 11, 14, 17, tzinfo=timezone.utc)


@pytest.fixture
def scheduled(stack):
    client, db = stack
    db.contacts.update_one({}, {"$set": {"timezone": "America/New_York", "cadenceDays": 7,
        "quietHours": {"start": "21:00", "end": "08:00"}, "spaceId": "room-1", "spaceKind": "direct",
        "optInAt": NOW - timedelta(days=30)}})
    sender, runs = Sender(), Runs()
    clock = [NOW]
    worker = ChannelWorker(db, sender=sender, runs=runs, agent_factory=ScriptedConversation,
                           scheduler_enabled=True, clock=lambda: clock[0])
    return client, db, worker, sender, runs, clock


def test_default_off_and_missing_preferences_never_prompt(stack, monkeypatch):
    monkeypatch.delenv("STITCH_CHANNELS_SCHEDULER", raising=False)
    client, db = stack
    sender = Sender()
    worker = ChannelWorker(db, sender=sender, runs=Runs(), agent_factory=ScriptedConversation)
    assert worker.tick()["sent"] == 0
    worker.scheduler_enabled = True
    assert worker.tick()["sent"] == 0 and not sender.messages


def test_due_prompt_is_unique_across_restarts_and_unanswered_chain(scheduled):
    _, db, worker, sender, runs, clock = scheduled
    assert worker.tick()["sent"] == 1
    first = db.notifications.find_one({})
    assert first["status"] == "sent" and first["active"] is True and first["familyId"] == "family-a"
    assert len(question_bank.PROMPTS) == db.questionBank.count_documents({}) == 40
    assert db.questionBank.count_documents({"familyId": {"$exists": True}}) == 0
    clock[0] += timedelta(days=30)
    restored = ChannelWorker(db, sender=sender, runs=runs, agent_factory=ScriptedConversation,
        scheduler_enabled=True, clock=lambda: clock[0])
    assert restored.tick()["sent"] == 0 and len(sender.messages) == 1


@pytest.mark.parametrize("field,value", [("spaceKind", "group"), ("spaceKind", None),
    ("spaceId", None), ("timezone", "not/a-zone"), ("cadenceDays", 0), ("cadenceDays", True),
    ("quietHours", {"start": "09:00", "end": "09:00"}), ("status", "stopped"), ("verifiedAt", None)])
def test_invalid_or_unverified_preferences_fail_closed(scheduled, field, value):
    _, db, worker, sender, *_ = scheduled
    db.contacts.update_one({}, {"$set": {field: value}})
    assert worker.tick()["sent"] == 0 and not sender.messages


@pytest.mark.parametrize("hour,minute,allowed", [(20, 59, True), (21, 0, False), (7, 59, False), (8, 0, True)])
def test_overnight_quiet_hours_boundaries(hour, minute, allowed):
    contact = {"timezone": "UTC", "cadenceDays": 1, "quietHours": {"start": "21:00", "end": "08:00"}}
    assert scheduler.allowed_now(contact, NOW.replace(hour=hour, minute=minute)) is allowed


def test_dst_fold_and_gap_use_actual_local_wall_time():
    contact = {"timezone": "America/New_York", "cadenceDays": 1,
               "quietHours": {"start": "01:00", "end": "02:00"}}
    for hour in (5, 6):
        assert not scheduler.allowed_now(contact, datetime(2026, 11, 1, hour, 30, tzinfo=timezone.utc))
    assert scheduler.allowed_now(contact, datetime(2026, 11, 1, 7, tzinfo=timezone.utc))
    contact["quietHours"]["end"] = "03:00"
    assert not scheduler.allowed_now(contact, datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc))
    assert scheduler.allowed_now(contact, datetime(2026, 3, 8, 7, tzinfo=timezone.utc))


def test_cadence_uses_local_calendar_days_across_dst(scheduled):
    _, db, worker, _, _, clock = scheduled
    clock[0] = datetime(2026, 3, 8, 16, tzinfo=timezone.utc)
    db.contacts.update_one({}, {"$set": {"cadenceDays": 1,
        "optInAt": datetime(2026, 3, 7, 17, tzinfo=timezone.utc)}})
    assert worker.tick()["sent"] == 1  # 23 elapsed hours, one local calendar day.


def test_recent_activity_and_incoming_draft_prevent_proactive_prompt(scheduled):
    client, db, worker, sender, _, clock = scheduled
    db.contacts.update_one({}, {"$set": {"optInAt": NOW - timedelta(days=6)}})
    assert worker.tick()["sent"] == 0
    clock[0] = NOW.replace(hour=3)  # NY quiet hours; ordinary replies still work.
    input_text(client, "We watched the river.")
    assert worker.tick()["processed"] == 1 and len(sender.messages) == 1
    assert db.notifications.count_documents({}) == 0
    clock[0] += timedelta(days=30)
    assert worker.tick()["sent"] == 0  # Do not interrupt a draft.


def test_open_question_priority_preserves_second_telling_target(scheduled):
    client, db, worker, sender, runs, _ = scheduled
    db.memories.insert_one({"_id": "own-memory", "familyId": "family-a"})
    q = memories.create_question(db, "family-a", person_id="person-a", text="What do you remember?",
                                 memory_id="own-memory")
    db.people.insert_one({"_id": "foreign", "familyId": "family-b"})
    memories.create_question(db, "family-b", person_id="foreign", text="Private other family question")
    assert worker.tick()["sent"] == 1
    assert sender.messages[-1]["text"] == q.text
    assert db.conversations.find_one({"principal": {"$exists": True}})["draft"]["questionId"] == q.id
    for text in ("I remember the river.", "YEAR 1998", "SUBMIT"):
        input_text(client, text)
        assert worker.tick()["blocked"] == 0
    assert runs.starts[0][3].memory_id == "own-memory"
    assert db.notifications.find_one({})["status"] == "answered"


def test_concurrent_ticks_and_lost_ack_never_duplicate_prompt(scheduled):
    _, db, worker, sender, *_ = scheduled
    def lost(message):
        sender.messages.append(message)
        raise TimeoutError("ack lost")
    sender.send = lost
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: worker.tick(), range(6)))
    assert len(sender.messages) == 1
    assert db.notifications.find_one({})["status"] == "sending"
    assert db.conversations.find_one({"kind": "channelWorker"})["status"] == "blocked"
    worker.tick()
    assert len(sender.messages) == 1


def complete_story(client, worker):
    for text in ("Story.", "YEAR 1998", "SUBMIT", "My mother."):
        input_text(client, text)
        assert worker.tick()["blocked"] == 0


def test_new_after_actual_terminal_event_preserves_budget_and_archives(scheduled):
    client, db, worker, _, runs, _ = scheduled
    complete_story(client, worker)
    service = ConversationService.for_contact(db, "family-a", "family-a:alice", runs=runs)
    previous_thread = service.thread_id
    db.conversations.update_one({"principal": {"$exists": True}}, {"$set": {"modelCalls": 7}})
    message = envelope(messageId="new-command", text="NEW")
    assert send(client, message).status_code == 202
    assert worker.tick()["processed"] == 1
    state = service._state()
    assert state["draft"] == {"text": ""} and state["status"] == "draft" and state["modelCalls"] == 7
    assert state["generation"] == 1 and state["archive"][0]["event"] == "done"
    assert service.thread_id != previous_thread
    assert "start_new_story" not in {t.name for t in conversation_tools(service)}
    send(client, message)
    worker.tick()
    assert service._state()["generation"] == 1


@pytest.mark.parametrize("change", [{"status": "submission_uncertain"}, {"answerStatus": "sending"},
    {"relayBuffer": [{"event": "done", "data": {}}]}, {"terminalEvent": None}])
def test_new_cannot_clear_uncertain_or_unacknowledged_state(scheduled, change):
    client, db, worker, sender, _, _ = scheduled
    complete_story(client, worker)
    db.conversations.update_one({"principal": {"$exists": True}}, {"$set": change})
    input_text(client, "NEW")
    worker.tick()
    state = db.conversations.find_one({"principal": {"$exists": True}})
    assert not state.get("generation")
    assert state["draft"]["text"] == "Story."


def test_new_during_pending_question_is_not_an_answer(scheduled):
    client, db, worker, sender, runs, _ = scheduled
    for text in ("Story.", "YEAR 1998", "SUBMIT", "NEW"):
        input_text(client, text)
        worker.tick()
    assert not runs.answers
    assert db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"] == "Story."


def test_due_scheduler_can_reset_only_clean_completed_story(scheduled):
    client, db, worker, sender, _, clock = scheduled
    complete_story(client, worker)
    clock[0] += timedelta(days=8)
    assert worker.tick()["sent"] == 1
    state = db.conversations.find_one({"principal": {"$exists": True}})
    assert state["generation"] == 1 and state["draft"]["text"] == ""
    assert db.notifications.count_documents({"active": True}) == 1


@pytest.mark.parametrize("space,proof", [("group-room", "direct"), ("room-1", "group"), ("room-1", None)])
def test_imessage_group_or_unproven_receipt_never_reaches_model(scheduled, space, proof):
    client, db, worker, sender, *_ = scheduled
    db.contacts.update_one({}, {"$set": {"channel": "imessage", "spaceKind": proof}})
    worker.agent_factory = lambda _: pytest.fail("model must not run")
    assert send(client, envelope(provider="imessage", spaceId=space)).status_code == 202
    assert worker.tick()["blocked"] == 1 and not sender.messages
    row = db.conversations.find_one({"principal": {"$exists": True}})
    assert row is None or not row["draft"]["text"]


def test_verified_imessage_direct_reply_works(scheduled):
    client, db, worker, sender, *_ = scheduled
    db.contacts.update_one({}, {"$set": {"channel": "imessage"}})
    assert send(client, envelope(provider="imessage", spaceId="room-1")).status_code == 202
    assert worker.tick()["processed"] == 1 and len(sender.messages) == 1


def test_quiet_hours_change_cancels_unsent_prompt_without_delaying_user_reply(scheduled, monkeypatch):
    client, db, worker, sender, *_ = scheduled
    original = conversations.update_worker
    def quiet(db, family, contact, token, fields):
        result = original(db, family, contact, token, fields)
        if any(x.get("notificationId") for x in fields.get("outbox", [])):
            db.contacts.update_one({}, {"$set": {"quietHours": {"start": "12:00", "end": "13:00"}}})
        return result
    monkeypatch.setattr(conversations, "update_worker", quiet)
    assert worker.tick()["blocked"] == 0 and not sender.messages
    assert db.notifications.find_one({})["status"] == "cancelled"
    input_text(client, "My own story.")
    assert worker.tick()["processed"] == 1 and len(sender.messages) == 1
    assert not db.notifications.find_one({})["active"]


def test_stop_after_notification_reservation_prevents_send(scheduled, monkeypatch):
    _, db, worker, sender, *_ = scheduled
    original = question_bank.update_notification
    def stop(db, family, contact, notification, expected, fields):
        result = original(db, family, contact, notification, expected, fields)
        if fields.get("status") == "sending":
            db.contacts.update_one({}, {"$set": {"status": "stopped"}})
        return result
    monkeypatch.setattr(question_bank, "update_notification", stop)
    assert worker.tick()["blocked"] == 1 and not sender.messages
    assert db.notifications.find_one({})["status"] == "sending"


def test_selected_second_telling_target_cannot_be_reassigned(scheduled):
    _, db, worker, _, _, _ = scheduled
    first = memories.create_question(db, "family-a", person_id="person-a", text="First?")
    second = memories.create_question(db, "family-a", person_id="person-a", text="Second?")
    worker.tick()
    service = ConversationService.for_contact(db, "family-a", "family-a:alice")
    selected = service._state()["draft"]["questionId"]
    other = first.id if selected != first.id else second.id
    with pytest.raises(ValueError, match="already bound"):
        service.select_question(other)


def test_archive_is_bounded_and_new_preserves_current_day_budget(scheduled):
    _, db, _, _, _, _ = scheduled
    class Immediate:
        serial = 0
        def start_run(self, *args):
            self.serial += 1
            return str(self.serial)
        async def events(self):
            yield "done", schemas.RUN_EVENTS["done"](memoryId="memory")
        def stream_run(self, *args):
            return self.events()
    now = datetime.now(timezone.utc)
    service = ConversationService.for_contact(db, "family-a", "family-a:alice", runs=Immediate(), clock=lambda: now)
    db.conversations.update_one({"principal": {"$exists": True}}, {"$set": {"modelCalls": 13}})
    token = "archive-test"
    conversations.acquire_worker(db, "family-a", "family-a:alice", token)
    for index in range(12):
        if index and index % 3 == 0:
            now += timedelta(days=1)
        calls_before = service._state()["modelCalls"]
        service.save_story_draft("Story", year=1998)
        service.submit_story()
        events = asyncio.run(service.relay_run_events(durable=True))
        service.acknowledge_relay(events)
        conversations.update_worker(db, "family-a", "family-a:alice", token,
            {"runFinished": True, "pollRun": False, "outbox": []})
        service.start_new_story(token)
        assert service._state()["modelCalls"] == calls_before
    state = service._state()
    assert state["modelCalls"] == 0 and state["generation"] == 12
    assert len(state["archive"]) == 10 and state["archive"][0]["runId"] == "3"


def test_question_bank_is_global_but_notifications_are_family_scoped(scheduled):
    _, db, worker, sender, *_ = scheduled
    original = db.contacts.find_one({})
    other = add_contact(db, family="family-b", handle="beth", person="person-b")
    prefs = {k: original[k] for k in ("timezone", "cadenceDays", "quietHours", "spaceKind", "optInAt")}
    db.contacts.update_one({"_id": other["_id"]}, {"$set": {**prefs, "spaceId": "room-b"}})
    worker.tick()
    assert len(sender.messages) == 2
    assert db.questionBank.count_documents({}) == 40
    assert {n["familyId"] for n in db.notifications.find({})} == {"family-a", "family-b"}


@pytest.mark.parametrize("command", ["HELP", "CALL"])
def test_help_and_call_do_not_mutate_draft_or_answer_prompt(scheduled, command):
    client, db, worker, sender, _, _ = scheduled
    worker.tick()
    worker.agent_factory = lambda _: pytest.fail("control must not call a model")
    input_text(client, command)
    assert worker.tick()["processed"] == 1
    state = db.conversations.find_one({"principal": {"$exists": True}})
    assert state["draft"] == {"text": ""}
    assert db.notifications.find_one({})["status"] == "sent"
    response = sender.messages[-1]["text"]
    if command == "HELP":
        assert all(name in response for name in ("YEAR", "SUBMIT", "KEEP", "YES/NO", "NEW", "STOP"))
    else:
        assert "not configured" in response


def test_help_is_separate_from_queued_story(scheduled):
    client, db, worker, sender, _, _ = scheduled
    input_text(client, "Story words.")
    input_text(client, "HELP")
    assert worker.tick()["processed"] == 1
    assert worker.tick()["processed"] == 1
    assert db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"] == "Story words."


def test_group_voice_is_blocked_before_stt_or_model(voice_stack):
    client, db, worker, transcriber, sender, _ = voice_stack
    db.contacts.update_one({}, {"$set": {"channel": "imessage", "spaceKind": "direct", "spaceId": "private-dm"}})
    worker.agent_factory = lambda _: pytest.fail("model must not see group audio")
    assert post_voice(client, voice_envelope(provider="imessage", spaceId="group-room")).status_code == 202
    assert worker.tick()["blocked"] == 1 and not transcriber.calls and not sender.messages
    assert not db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"]


@pytest.mark.parametrize("words", ["NEW", "HELP", "CALL"])
def test_voice_control_words_never_reset_or_request_calls(voice_stack, words):
    client, db, worker, transcriber, sender, _ = voice_stack
    transcriber.text = words
    assert post_voice(client).status_code == 202
    assert worker.tick()["processed"] == 1
    state = db.conversations.find_one({"principal": {"$exists": True}})
    assert state["draft"]["text"] == words and not state.get("generation")


def test_model_cannot_switch_notified_second_telling_target(scheduled):
    from agent.conversation.agent import ConversationAgent
    from agent.fake_llm import ScriptedChatModel, tool_call, tool_messages
    from langchain_core.messages import AIMessage
    client, db, worker, sender, runs, _ = scheduled
    questions = []
    for number in (1, 2):
        memory_id = f"memory-{number}"
        db.memories.insert_one({"_id": memory_id, "familyId": "family-a"})
        questions.append(memories.create_question(db, "family-a", person_id="person-a",
            text=f"Question {number}?", memory_id=memory_id))
    worker.tick()
    selected = db.conversations.find_one({"principal": {"$exists": True}})["draft"]["questionId"]
    notified = next(q for q in questions if q.id == selected)
    other = next(q for q in questions if q.id != selected)
    def attempt_switch(messages, tools):
        if not tool_messages(messages, "select_question"):
            return tool_call("select_question", {"question_id": other.id})
        return AIMessage(content="What else do you remember?")
    worker.agent_factory = lambda service: ConversationAgent(service, worker_managed=True,
        model=ScriptedChatModel(responder=attempt_switch))
    for text in ("My memory of that day.", "YEAR 1998", "SUBMIT"):
        input_text(client, text)
        assert worker.tick()["blocked"] == 0
    assert runs.starts[0][3].memory_id == notified.memory_id
    assert runs.starts[0][3].question == notified.text


def test_changed_cadence_before_send_cancels_known_unsent_prompt(scheduled, monkeypatch):
    _, db, worker, sender, *_ = scheduled
    db.contacts.update_one({}, {"$set": {"optInAt": NOW - timedelta(days=8)}})
    original = conversations.update_worker
    def extend_cadence(db, family, contact, token, fields):
        result = original(db, family, contact, token, fields)
        if any(x.get("notificationId") for x in fields.get("outbox", [])):
            db.contacts.update_one({}, {"$set": {"cadenceDays": 30}})
        return result
    monkeypatch.setattr(conversations, "update_worker", extend_cadence)
    assert worker.tick()["blocked"] == 0
    assert not sender.messages
    assert db.notifications.find_one({})["status"] == "cancelled"
    assert db.conversations.find_one({"kind": "channelWorker"})["status"] == "idle"


@pytest.mark.parametrize("change", [{"spaceId": "replacement-room"}, {"spaceKind": "group"},
    {"handle": "replacement-handle"}, {"channel": "terminal"},
    {"verifiedAt": NOW + timedelta(seconds=1)}, {"optInAt": NOW + timedelta(seconds=1)}])
def test_proof_change_after_draft_save_prevents_first_model_call(scheduled, monkeypatch, change):
    from agent.conversation.agent import ConversationAgent
    from agent.fake_llm import ScriptedChatModel
    from langchain_core.messages import AIMessage
    client, db, worker, sender, *_ = scheduled
    db.contacts.update_one({}, {"$set": {"channel": "imessage"}})
    calls = []
    def respond(*args):
        calls.append(1)
        return AIMessage(content="Private question?")
    worker.agent_factory = lambda service: ConversationAgent(service, worker_managed=True,
        model=ScriptedChatModel(responder=respond))
    original = question_bank.answer_prompt
    def remap(*args):
        original(*args)
        db.contacts.update_one({}, {"$set": change})
    monkeypatch.setattr(question_bank, "answer_prompt", remap)
    assert send(client, envelope(provider="imessage")).status_code == 202
    assert worker.tick()["blocked"] == 1
    assert not calls and not sender.messages


@pytest.mark.parametrize("tool_name,args", [("write_todos", {"todos": [{"content": "Ask a question", "status": "pending"}]}),
    ("task", {"description": "Ask a concise follow-up", "subagent_type": "general-purpose"})])
def test_each_model_boundary_rejects_fresh_same_space_reconsent(scheduled, tool_name, args):
    from agent.conversation.agent import ConversationAgent
    from agent.fake_llm import ScriptedChatModel, tool_call
    from langchain_core.messages import AIMessage
    client, db, worker, sender, *_ = scheduled
    db.contacts.update_one({}, {"$set": {"channel": "imessage"}})
    calls = []
    def respond(*unused):
        calls.append(1)
        if len(calls) == 1:
            # STOP followed by fresh consent in the SAME verified space must not
            # revive this invocation, including built-in subagent model calls.
            db.contacts.update_one({}, {"$set": {"status": "stopped"}})
            db.contacts.update_one({}, {"$set": {"status": "active", "verifiedAt": NOW + timedelta(seconds=1),
                                                 "optInAt": NOW + timedelta(seconds=1)}})
            return tool_call(tool_name, args)
        return AIMessage(content="This model call must not occur")
    worker.agent_factory = lambda service: ConversationAgent(service, worker_managed=True,
        model=ScriptedChatModel(responder=respond))
    assert send(client, envelope(provider="imessage")).status_code == 202
    assert worker.tick()["blocked"] == 1
    assert calls == [1] and not sender.messages
    assert db.conversations.find_one({"principal": {"$exists": True}})["modelCalls"] == 1


def test_old_receipt_cannot_run_under_fresh_same_space_consent(scheduled):
    client, db, worker, sender, *_ = scheduled
    db.contacts.update_one({}, {"$set": {"channel": "imessage"}})
    assert send(client, envelope(provider="imessage")).status_code == 202
    receipt = db.channelMessages.find_one({"kind": "inbound"})
    assert set(receipt["contactProof"]) == {"handle", "channel", "spaceId", "spaceKind", "verifiedAt", "optInAt"}
    db.contacts.update_one({}, {"$set": {"verifiedAt": NOW + timedelta(days=1), "optInAt": NOW + timedelta(days=1)}})
    worker.agent_factory = lambda _: pytest.fail("old consent must not reach model")
    assert worker.tick()["blocked"] == 1 and not sender.messages
    assert not db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"]


def test_legacy_imessage_receipt_without_proof_fails_closed(scheduled):
    client, db, worker, sender, *_ = scheduled
    db.contacts.update_one({}, {"$set": {"channel": "imessage"}})
    send(client, envelope(provider="imessage"))
    db.channelMessages.update_one({"kind": "inbound"}, {"$unset": {"contactProof": ""}})
    worker.agent_factory = lambda _: pytest.fail("unproven receipt must not reach model")
    assert worker.tick()["blocked"] == 1 and not sender.messages


def test_changed_cadence_after_send_reservation_still_cancels(scheduled, monkeypatch):
    _, db, worker, sender, *_ = scheduled
    db.contacts.update_one({}, {"$set": {"optInAt": NOW - timedelta(days=8)}})
    original = question_bank.update_notification
    def extend(db, family, contact, notice, expected, fields):
        result = original(db, family, contact, notice, expected, fields)
        if fields.get("status") == "sending":
            db.contacts.update_one({}, {"$set": {"cadenceDays": 30}})
        return result
    monkeypatch.setattr(question_bank, "update_notification", extend)
    assert worker.tick()["blocked"] == 0 and not sender.messages
    assert db.notifications.find_one({})["status"] == "cancelled"


def test_voice_reconsent_during_provider_call_prevents_transcript_use(voice_stack):
    client, db, worker, transcriber, sender, _ = voice_stack
    db.contacts.update_one({}, {"$set": {"channel": "imessage", "spaceKind": "direct", "spaceId": "room-1"}})
    async def reconsent(*args, **kwargs):
        transcriber.calls.append(1)
        db.contacts.update_one({}, {"$set": {"verifiedAt": NOW + timedelta(days=1), "optInAt": NOW + timedelta(days=1)}})
        return "Private transcript"
    transcriber.transcribe = reconsent
    assert post_voice(client, voice_envelope(provider="imessage")).status_code == 202
    assert worker.tick()["blocked"] == 1 and len(transcriber.calls) == 1 and not sender.messages
    receipt = db.channelMessages.find_one({"receiptType": "voice"})
    assert "contactProof" in receipt and "transcript" not in receipt
    assert not db.conversations.find_one({"principal": {"$exists": True}})["draft"]["text"]


def test_photo_receipt_keeps_original_proof_and_blocks_reconsent(scheduled):
    import json
    from tests.channels.test_inbox import sign
    from tests.channels.test_photos import envelope as photo_envelope
    client, db, worker, sender, *_ = scheduled
    db.contacts.update_one({}, {"$set": {"channel": "imessage"}})
    path = "/internal/channels/inbound-photo"
    raw = json.dumps(photo_envelope(provider="imessage")).encode()
    assert client.post(path, content=raw, headers=sign(raw, path)).status_code == 202
    receipt = db.channelMessages.find_one({"receiptType": "photo"})
    assert receipt["contactProof"]["spaceId"] == "room-1"
    db.contacts.update_one({}, {"$set": {"optInAt": NOW + timedelta(days=1)}})
    assert worker.tick()["blocked"] == 1 and not sender.messages
    assert not db.conversations.find_one({"principal": {"$exists": True}})["draft"].get("photoIds")


def test_proof_change_during_model_budget_reservation_prevents_provider(scheduled, monkeypatch):
    from agent.conversation.agent import ConversationAgent
    from agent.fake_llm import ScriptedChatModel
    from langchain_core.messages import AIMessage
    client, db, worker, sender, *_ = scheduled
    db.contacts.update_one({}, {"$set": {"channel": "imessage"}})
    calls = []
    def respond(*args):
        calls.append(1)
        return AIMessage(content="Must not run")
    worker.agent_factory = lambda service: ConversationAgent(service, worker_managed=True,
        model=ScriptedChatModel(responder=respond))
    original = conversations.take_conversation_model_call
    def reserve(*args):
        original(*args)
        db.contacts.update_one({}, {"$set": {"optInAt": NOW + timedelta(days=1)}})
    monkeypatch.setattr(conversations, "take_conversation_model_call", reserve)
    send(client, envelope(provider="imessage"))
    assert worker.tick()["blocked"] == 1 and not calls and not sender.messages
