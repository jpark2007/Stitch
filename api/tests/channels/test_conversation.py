import asyncio
import dataclasses
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import mongomock
import pytest
from langchain_core.messages import AIMessage

import schemas
from agent.conversation import ConversationService
from agent.conversation.agent import ConversationAgent, conversation_tools
from agent.fake_llm import ScriptedChatModel, tool_call, tool_messages
from db import conversations


class Runs:
    def __init__(self):
        self.starts = []
        self.answers = []
        self.events = []
        self.closed = False
        self.fail = False

    def start_run(self, *args):
        self.starts.append(args)
        if self.fail:
            raise OSError("sensitive provider failure")
        return "run-1"

    def answer_run(self, *args):
        self.answers.append(args)

    async def stream_run(self, family_id, run_id):
        assert (family_id, run_id) == ("family-a", "run-1")
        try:
            for event in self.events:
                yield event
            await asyncio.sleep(60)
        finally:
            self.closed = True


@pytest.fixture
def setup():
    db = mongomock.MongoClient().db
    now = datetime.now(timezone.utc)
    for suffix in ("a", "b"):
        db.people.insert_one({"_id": f"person-{suffix}", "familyId": f"family-{suffix}"})
        db.contacts.insert_one({"_id": f"contact-{suffix}", "familyId": f"family-{suffix}",
                                "personId": f"person-{suffix}", "status": "active",
                                "optInAt": now, "verifiedAt": now})
    runs = Runs()
    service = ConversationService.for_contact(db, "family-a", "contact-a", runs=runs)
    return db, service, runs


def test_draft_accumulates_persists_and_submits_once(setup):
    db, service, runs = setup
    service.save_story_draft("Grandma baked bread.", year=1998)
    service.save_story_draft("I spilled the flour.", title="Bread")
    restored = ConversationService.for_contact(db, "family-a", "contact-a", runs=runs)
    assert restored.submit_story() == {"runId": "run-1", "duplicate": False}
    assert service.submit_story()["duplicate"]
    assert len(runs.starts) == 1
    family, principal, person, fields, photos = runs.starts[0]
    assert (family, principal, person, photos) == ("family-a", "contact:contact-a", "person-a", [])
    assert fields.text == "Grandma baked bread.\nI spilled the flour."
    assert fields.year == 1998 and fields.title == "Bread"
    with pytest.raises(RuntimeError):
        service.save_story_draft("New text")


@pytest.mark.parametrize("kwargs", [{"year": 1799}, {"year": True}, {"year": 9999},
                                    {"text": "x" * 20001}, {"title": "x" * 201}])
def test_draft_bounds(setup, kwargs):
    with pytest.raises(ValueError):
        setup[1].save_story_draft(**kwargs)
    assert setup[0].conversations.find_one()["draft"] == {"text": ""}


def test_requires_year_and_text(setup):
    service = setup[1]
    with pytest.raises(ValueError):
        service.submit_story()
    service.save_story_draft("Our story")
    with pytest.raises(ValueError):
        service.submit_story()
    assert setup[2].starts == []


def test_concurrent_submission_is_at_most_once(setup):
    _, service, runs = setup
    service.save_story_draft("Our story", year=2000)
    def submit():
        try:
            return service.submit_story()
        except RuntimeError:
            return "busy"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: submit(), range(20)))
    assert len(runs.starts) == 1


def test_uncertain_submission_never_retries(setup):
    db, service, runs = setup
    service.save_story_draft("Our story", year=2000)
    runs.fail = True
    with pytest.raises(RuntimeError, match="uncertain") as exc:
        service.submit_story()
    assert "sensitive" not in str(exc.value)
    restored = ConversationService.for_contact(db, "family-a", "contact-a", runs=runs)
    with pytest.raises(RuntimeError, match="uncertain"):
        restored.submit_story()
    assert len(runs.starts) == 1


@pytest.mark.parametrize("change", [{"status": "stopped"}, {"verifiedAt": None},
                                    {"personId": "person-b"}, {"userId": "new-user"}])
def test_identity_and_consent_rechecked(setup, change):
    db, service, runs = setup
    service.save_story_draft("Our story", year=2000)
    db.contacts.update_one({"_id": "contact-a"}, {"$set": change})
    for operation in (service.get_open_questions, service.submit_story,
                      lambda: service.save_story_draft("more")):
        with pytest.raises(PermissionError):
            operation()
    assert not runs.starts


def test_wrong_family_and_frozen_context(setup):
    db, service, _ = setup
    with pytest.raises(PermissionError):
        ConversationService.for_contact(db, "family-b", "contact-a")
    with pytest.raises(dataclasses.FrozenInstanceError):
        service.context.family_id = "family-b"


def test_second_telling_from_addressed_question_only(setup):
    db, service, runs = setup
    for suffix in ("a", "b"):
        db.questions.insert_one({"_id": f"q-{suffix}", "familyId": f"family-{suffix}",
                                 "personId": f"person-{suffix}", "memoryId": f"memory-{suffix}",
                                 "text": "Your side?", "status": "open"})
    assert service.get_open_questions() == [{"id": "q-a", "text": "Your side?"}]
    with pytest.raises(ValueError):
        service.select_question("q-b")
    service.select_question("q-a")
    service.save_story_draft("My side", year=2000)
    service.submit_story()
    assert runs.starts[0][3].memory_id == "memory-a"


def test_closed_question_not_submitted(setup):
    db, service, runs = setup
    db.questions.insert_one({"_id": "q", "familyId": "family-a", "personId": "person-a",
                             "text": "Your side?", "status": "open"})
    service.select_question("q")
    service.save_story_draft("My side", year=2000)
    db.questions.update_one({"_id": "q"}, {"$set": {"status": "answered"}})
    with pytest.raises(ValueError):
        service.submit_story()
    assert not runs.starts


def test_events_bounded_closed_and_answer_principal_fixed(setup):
    _, service, runs = setup
    service.save_story_draft("Our story", year=2000)
    service.submit_story()
    runs.events = [("question", schemas.QuestionEvent(text="Where?"))]
    result = asyncio.run(service.relay_run_events(timeout=.01))
    assert result == [{"event": "question", "data": {"text": "Where?"}}]
    assert runs.closed
    with pytest.raises(ValueError):
        service.answer_run_question("merge", approve=True)
    service.answer_run_question("answer", text="At home")
    assert runs.answers[0][:3] == ("family-a", "run-1", "contact:contact-a")
    assert runs.answers[0][3].text == "At home"
    assert asyncio.run(service.relay_run_events(timeout=.01)) == []
    with pytest.raises(ValueError):
        service.answer_run_question("answer", text="Again")


def test_props_use_only_relayed_ids_and_merge_needs_boolean(setup):
    _, service, runs = setup
    service.save_story_draft("Our story", year=2000)
    service.submit_story()
    runs.events = [("props_found", schemas.PropsFoundEvent(props=[
        schemas.PropChip(id="real-prop", name="Ball", description="A ball", size="hand")]))]
    asyncio.run(service.relay_run_events(timeout=.01))
    service.answer_run_question("props")
    assert runs.answers[-1][-1].props == [schemas.PropEdit(id="real-prop", action="keep")]
    runs.events.append(("merge", schemas.MergeEvent(text="Same person?", person_id="p", candidate_name="Grandma")))
    asyncio.run(service.relay_run_events(timeout=.01))
    with pytest.raises(ValueError):
        service.answer_run_question("merge", approve="yes")
    service.answer_run_question("merge", approve=False)
    assert runs.answers[-1][-1].approve is False


def test_submit_on_event_loop_fails_before_reservation(setup):
    _, service, runs = setup
    service.save_story_draft("Our story", year=2000)
    async def wrong():
        with pytest.raises(RuntimeError, match="event loop"):
            service.submit_story()
    asyncio.run(wrong())
    assert service._state()["status"] == "draft" and not runs.starts


def test_tools_do_not_accept_scope_or_run_ids(setup):
    for item in conversation_tools(setup[1]):
        props = item.args_schema.model_json_schema().get("properties", {})
        assert not {"family_id", "contact_id", "person_id", "user_id", "run_id", "memory_id"} & props.keys()


def test_real_deep_agent_with_scripted_model_submits(setup):
    _, service, runs = setup
    def respond(messages, tools):
        if not tool_messages(messages, "save_story_draft"):
            return tool_call("save_story_draft", {"text": "We baked bread", "year": 1998})
        if not tool_messages(messages, "submit_story"):
            return tool_call("submit_story", {})
        return AIMessage(content="The studio has started your story.")
    model = ScriptedChatModel(responder=respond)
    agent = ConversationAgent(service, model=model)
    result = agent.invoke("We baked bread in 1998; please submit it.")
    assert result["messages"][-1].content == "The studio has started your story."
    assert len(runs.starts) == 1
    assert service._state()["modelCalls"] == 3


def test_model_budget_is_per_invocation_with_cumulative_telemetry(setup):
    _, service, _ = setup
    model = ScriptedChatModel(responder=lambda *_: AIMessage(content="Tell me more."))
    agent = ConversationAgent(service, model=model, max_model_calls=1)
    for _ in range(33):
        agent.invoke("Hello")
    ConversationAgent(service, model=model, max_model_calls=1).invoke("Again")
    assert len(model.calls) == 34
    assert service._state()["modelCalls"] == 34


def test_deep_agent_subagent_cannot_bypass_budget(setup):
    model = ScriptedChatModel(responder=lambda *_: tool_call(
        "task", {"subagent_type": "general-purpose", "description": "Say hello"}))
    agent = ConversationAgent(setup[1], model=model, max_model_calls=1)
    with pytest.raises(RuntimeError, match="model-call limit"):
        agent.invoke("Hello")
    assert len(model.calls) == 1


def test_consent_revoked_during_model_suppresses_response(setup):
    db, service, _ = setup
    def revoke(*_):
        db.contacts.update_one({"_id": "contact-a"}, {"$set": {"status": "stopped"}})
        return AIMessage(content="This must not be sent.")
    agent = ConversationAgent(service, model=ScriptedChatModel(responder=revoke))
    with pytest.raises(PermissionError):
        agent.invoke("Hello")


def test_oversized_events_close_without_advancing_cursor(setup):
    _, service, runs = setup
    service.save_story_draft("Our story", year=2000)
    service.submit_story()
    runs.events = [("question", schemas.QuestionEvent(text="x" * 70000))]
    with pytest.raises(RuntimeError, match="size limit"):
        asyncio.run(service.relay_run_events(timeout=.1))
    assert runs.closed
    assert service._state()["cursor"] == 0


def test_relay_close_is_bounded(setup):
    _, service, runs = setup
    service.save_story_draft("Our story", year=2000)
    service.submit_story()
    class SlowClose:
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration
        async def aclose(self): await asyncio.sleep(60)
    runs.stream_run = lambda *_: SlowClose()
    with pytest.raises(TimeoutError):
        asyncio.run(service.relay_run_events(timeout=.1))


def test_result_write_failure_leaves_submission_uncertain(setup, monkeypatch):
    _, service, runs = setup
    service.save_story_draft("Our story", year=2000)
    original = conversations.update_conversation
    def fail_result(db, family, contact, revision, fields, **kwargs):
        if fields.get("status") == "submitted":
            raise OSError("database unavailable")
        return original(db, family, contact, revision, fields, **kwargs)
    monkeypatch.setattr(conversations, "update_conversation", fail_result)
    with pytest.raises(RuntimeError, match="uncertain"):
        service.submit_story()
    with pytest.raises(RuntimeError, match="uncertain"):
        service.submit_story()
    assert len(runs.starts) == 1
