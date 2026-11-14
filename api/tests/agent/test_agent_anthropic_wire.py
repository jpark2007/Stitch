"""The real ChatAnthropic drives the studio over a replaying Messages API transport.

Every other studio test plays the model with ScriptedChatModel, so nothing else checks
that the requests langchain_anthropic.ChatAnthropic builds from our graph are valid
Anthropic Messages API requests. Here the model is a real ChatAnthropic whose SDK clients
send through agent.anthropic_replay.ReplayTransport: each request is validated
(validate_request), turned back into langchain messages, answered by the same fake studio
responder the e2e tests use, and returned as a Messages API response (JSON or SSE).

Sockets are blocked for the whole module: an accidental real call fails loudly. No keys.
"""

from __future__ import annotations

import asyncio
import copy
import json
import socket
from collections.abc import Callable, Iterator
from typing import Any

import anthropic
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

import schemas
from agent import anthropic_replay as replay
from agent import production, studio, wiring
from agent.anthropic_replay import ReplayTransport, replay_chat_model, validate_request
from agent.fake_llm import Responder, ScriptedChatModel
from agent.fake_studio import FakeStory, make_fake_studio_responder, role_of
from agent.repo import InMemoryRepo, load_sample_family
from agent.subagents import SUBAGENT_NAMES

FAMILY = "fam_wire"
MOM_USER = "user_mom"
DAD_USER = "user_dad"
WAIT = 60.0
"""Seconds any one wait may take; a hung run fails the test instead of hanging it."""

FOLLOWUP = "Who else was on the dock that day?"
STORY = FakeStory(place="lake dock", prop="picnic basket", prop_size="carried", followup=FOLLOWUP, compile_failures=1)
MOM_TEXT = (
    "We carried the picnic basket down to the lake dock. "
    "I nearly dropped it at the end of the boards. "
    "He took it from me and laughed."
)
DAD_TEXT = "She was sure the basket would go in the lake. I took it before it did, and we both laughed."
ANSWER = "Only the two of us, early in the morning."
RENAMED = "wicker picnic basket"

Event = tuple[str, BaseModel]
Answer = schemas.AnswerRequest | Callable[[list[Event]], schemas.AnswerRequest]


# --------------------------------------------------------------------------- no network


@pytest.fixture(autouse=True, scope="module")
def no_sockets() -> Iterator[list[Any]]:
    """Fail any attempt to resolve a host or open a connection, for every test here."""
    attempts: list[Any] = []

    def refuse(*args: Any, **_kwargs: Any) -> Any:
        attempts.append(args)
        raise AssertionError(f"network call in an offline test: {args!r}")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket, "getaddrinfo", refuse)
        mp.setattr(socket, "create_connection", refuse)
        mp.setattr(socket.socket, "connect", refuse)
        mp.setattr(socket.socket, "connect_ex", refuse)
        yield attempts
    assert attempts == [], attempts


def test_sockets_are_blocked(no_sockets: list[Any]) -> None:
    before = len(no_sockets)
    with pytest.raises(AssertionError, match="network call"):
        socket.create_connection(("api.anthropic.com", 443), timeout=1)
    with socket.socket() as sock, pytest.raises(AssertionError, match="network call"):
        sock.connect(("127.0.0.1", 9))
    assert len(no_sockets) == before + 2
    del no_sockets[before:]  # these two were on purpose; any other attempt fails the module


# --------------------------------------------------------------------------- validate_request


TOOL = {"name": "get_person", "description": "Look a person up.",
        "input_schema": {"type": "object", "properties": {"person_id": {"type": "string"}}}}
OTHER_TOOL = {"name": "write_todos", "description": "Write the plan.", "input_schema": {"type": "object", "properties": {}}}


def valid_body() -> dict[str, Any]:
    """A request using every feature the rules look at, and breaking none of them."""
    return copy.deepcopy({
        "model": "claude-opus-5-5",
        "max_tokens": 16000,
        "cache_control": {"type": "ephemeral", "ttl": "5m"},
        "system": [{"type": "text", "text": "You are the studio."},
                   {"type": "text", "text": "Skills...", "cache_control": {"type": "ephemeral"}}],
        "tools": [TOOL, {**OTHER_TOOL, "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {"role": "user", "content": "Tell the story."},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Looking."},
                {"type": "tool_use", "id": "toolu_1", "name": "get_person", "input": {"person_id": "mom"}},
                {"type": "tool_use", "id": "call_write_todos_2", "name": "write_todos", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "{\"id\": \"mom\"}", "is_error": False},
                {"type": "tool_result", "tool_use_id": "call_write_todos_2",
                 "content": [{"type": "text", "text": "ok"}]},
                {"type": "text", "text": "Go on."},
            ]},
        ],
    })


def edited(change: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    body = valid_body()
    change(body)
    return body


def msgs(body: dict[str, Any]) -> list[dict[str, Any]]:
    return body["messages"]


BAD_BODIES: dict[str, list[tuple[str, Callable[[dict[str, Any]], Any], str]]] = {
    # rule -> [(case, how to break the valid body, a phrase the problem must contain)]
    "model_and_max_tokens": [
        ("no model", lambda b: b.pop("model"), "model: field required"),
        ("no max_tokens", lambda b: b.pop("max_tokens"), "max_tokens: field required"),
        ("zero max_tokens", lambda b: b.update(max_tokens=0), "positive integer"),
        ("string max_tokens", lambda b: b.update(max_tokens="16000"), "positive integer"),
        ("bool max_tokens", lambda b: b.update(max_tokens=True), "positive integer"),
    ],
    "roles": [
        ("no messages", lambda b: b.update(messages=[]), "at least one message"),
        ("assistant first", lambda b: msgs(b).insert(0, {"role": "assistant", "content": "Hi."}), "first message"),
        ("two user turns", lambda b: msgs(b).append({"role": "user", "content": "More."}), "alternate"),
        ("system role in messages", lambda b: msgs(b).append({"role": "system", "content": "x"}), "must be 'user' or"),
    ],
    "tool_pairing": [
        ("tool_use without a result",
         lambda b: msgs(b)[2]["content"].pop(1), "without `tool_result` blocks immediately after"),
        ("result without a tool_use",
         lambda b: msgs(b)[2]["content"].insert(0, {"type": "tool_result", "tool_use_id": "toolu_9", "content": "x"}),
         "unexpected `tool_use_id`"),
        ("result after text",
         lambda b: msgs(b)[2]["content"].reverse(), "must come before any other content"),
        ("result two turns late",
         lambda b: b.update(messages=[msgs(b)[0], msgs(b)[1], {"role": "user", "content": "wait"},
                                      {"role": "assistant", "content": "ok"}, msgs(b)[2]]),
         "without `tool_result` blocks immediately after"),
        ("tool_use id with a dot",
         lambda b: (msgs(b)[1]["content"][1].update(id="toolu.1"), msgs(b)[2]["content"][0].update(tool_use_id="toolu.1")),
         "should match pattern"),
        ("tool_use id reused", lambda b: b.update(messages=[
            *msgs(b), {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "get_person", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "again"}]},
        ]), "must be unique"),
    ],
    "empty_content": [
        ("empty text block", lambda b: msgs(b)[1]["content"].insert(0, {"type": "text", "text": ""}),
         "text content blocks must be non-empty"),
        ("whitespace text block", lambda b: msgs(b)[2]["content"].append({"type": "text", "text": "  \n"}),
         "text content blocks must be non-empty"),
        ("empty content list", lambda b: msgs(b).extend([{"role": "assistant", "content": []}]),
         "non-empty content"),
        ("empty content string", lambda b: msgs(b).extend([{"role": "assistant", "content": ""}]),
         "non-empty content"),
        ("empty text inside a tool_result",
         lambda b: msgs(b)[2]["content"][1].update(content=[{"type": "text", "text": ""}]),
         "text content blocks must be non-empty"),
    ],
    "tool_definitions": [
        ("name with a space", lambda b: (b["tools"][0].update(name="get person"),
                                         msgs(b)[1]["content"][1].update(name="get person")), "should match pattern"),
        ("name of 65 characters", lambda b: (b["tools"][0].update(name="x" * 65),
                                             msgs(b)[1]["content"][1].update(name="x" * 65)), "should match pattern"),
        ("duplicate names", lambda b: b["tools"].append(copy.deepcopy(TOOL)), "must be unique"),
        ("no description", lambda b: b["tools"][0].pop("description"), "description is missing"),
        ("blank description", lambda b: b["tools"][0].update(description=" "), "description is missing"),
        ("no input_schema", lambda b: b["tools"][0].pop("input_schema"), "input_schema: field required"),
        ("array input_schema", lambda b: b["tools"][0].update(input_schema={"type": "array"}), "must be 'object'"),
    ],
    "tool_use_declared": [
        ("undeclared tool_use", lambda b: b["tools"].pop(0), "not among the declared tools"),
        ("tool blocks without tools", lambda b: b.pop("tools"), "must define tools"),
    ],
    "system": [
        ("system is an object", lambda b: b.update(system={"text": "x"}), "must be a string or a list"),
        ("image block in system", lambda b: b["system"].append(
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ""}}),
         "must be a text block"),
        ("empty system block", lambda b: b["system"].insert(0, {"type": "text", "text": ""}), "non-empty"),
    ],
    "cache_control": [
        ("five breakpoints", lambda b: (b["tools"][0].update(cache_control={"type": "ephemeral"}),
                                        msgs(b)[0].update(content=[{"type": "text", "text": "Tell the story.",
                                                                    "cache_control": {"type": "ephemeral"}}])),
         "maximum of 4"),
    ],
}

CHECK_OF = {
    "model_and_max_tokens": replay.check_model_and_max_tokens,
    "roles": replay.check_roles,
    "tool_pairing": replay.check_tool_pairing,
    "empty_content": replay.check_empty_content,
    "tool_definitions": replay.check_tool_definitions,
    "tool_use_declared": replay.check_tool_use_declared,
    "system": replay.check_system,
    "cache_control": replay.check_cache_control,
}


def test_every_rule_is_a_check_and_has_cases() -> None:
    assert set(CHECK_OF.values()) == set(replay.CHECKS)
    assert set(BAD_BODIES) == set(CHECK_OF)


@pytest.mark.parametrize("rule", sorted(CHECK_OF))
def test_rule_passes_a_valid_body(rule: str) -> None:
    body = valid_body()
    assert CHECK_OF[rule](body) == []
    assert validate_request(body) == []


@pytest.mark.parametrize(
    ("rule", "case"),
    [(rule, case) for rule, cases in sorted(BAD_BODIES.items()) for case, _, _ in cases],
)
def test_rule_fails_a_broken_body(rule: str, case: str) -> None:
    _, breaker, phrase = next(c for c in BAD_BODIES[rule] if c[0] == case)
    body = edited(breaker)
    found = CHECK_OF[rule](body)
    assert found, f"{rule} passed a body with {case}"
    assert any(phrase in p for p in found), found
    assert set(found) <= set(validate_request(body))


def test_rule_passes_other_valid_shapes() -> None:
    plain = {"model": "m", "max_tokens": 1, "system": "You are terse.", "messages": [{"role": "user", "content": "Hi"}]}
    assert validate_request(plain) == []
    prefill = {**plain, "messages": [*plain["messages"], {"role": "assistant", "content": "Sure"}]}
    assert validate_request(prefill) == []
    no_system = {k: v for k, v in plain.items() if k != "system"}
    assert validate_request(no_system) == []
    four = edited(lambda b: b["tools"][0].update(cache_control={"type": "ephemeral"}))
    assert replay.count_cache_breakpoints(four) == 4 and replay.check_cache_control(four) == []
    assert validate_request("nope") == ["body: must be a JSON object"]  # type: ignore[arg-type]


def test_a_removed_tool_can_be_explained() -> None:
    body = edited(lambda b: b["tools"].pop(0))
    assert replay.check_tool_use_declared(body)
    assert replay.check_tool_use_declared(body, removed_tools={"get_person"}) == []
    assert validate_request(body, removed_tools={"get_person"}) == []


# --------------------------------------------------------------------------- request conversion


def test_request_to_messages_recovers_langchain_messages() -> None:
    converted = replay.request_to_messages(valid_body())
    assert [type(m) for m in converted] == [SystemMessage, HumanMessage, AIMessage, ToolMessage, ToolMessage, HumanMessage]
    system, human, ai, first, second, after = converted
    assert system.text == "You are the studio.Skills..."
    assert all("cache_control" not in b for b in system.content)  # type: ignore[union-attr]
    assert human.content == "Tell the story."
    assert ai.content == "Looking."
    assert [(c["name"], c["args"], c["id"]) for c in ai.tool_calls] == [
        ("get_person", {"person_id": "mom"}, "toolu_1"), ("write_todos", {}, "call_write_todos_2")]
    assert (first.name, first.tool_call_id, first.content, first.status) == ("get_person", "toolu_1", "{\"id\": \"mom\"}", "success")
    assert (second.name, second.content) == ("write_todos", "ok")
    assert after.content == "Go on."
    assert replay.tool_names_of(valid_body()) == ["get_person", "write_todos"]


# --------------------------------------------------------------------------- round trip


class Recorder:
    """A responder that replays a list of replies and records what it was asked."""

    def __init__(self, *replies: AIMessage) -> None:
        self.replies = list(replies)
        self.seen: list[tuple[list[BaseMessage], list[str]]] = []

    def __call__(self, messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        self.seen.append((messages, tool_names))
        return self.replies.pop(0)


def _get_weather(city: str) -> str:
    """Weather in a city."""
    return f"sunny in {city}"


def _get_time(zone: str) -> str:
    """Time in a zone."""
    return f"noon in {zone}"


TEXT_REPLY = AIMessage(content="It was a Sunday.")
ONE_CALL = AIMessage(content="", tool_calls=[
    {"name": "_get_weather", "args": {"city": "Paris"}, "id": "call_weather_1", "type": "tool_call"}])
TWO_CALLS = AIMessage(content="Checking both.", tool_calls=[
    {"name": "_get_weather", "args": {"city": "Oslo"}, "id": "call_weather_2", "type": "tool_call"},
    {"name": "_get_time", "args": {"zone": "UTC"}, "id": "call_time_3", "type": "tool_call"},
])


def _calls(message: BaseMessage) -> list[tuple[str, dict[str, Any], str | None]]:
    assert isinstance(message, AIMessage)
    return [(c["name"], c["args"], c["id"]) for c in message.tool_calls]


MODES = ["invoke", "stream", "ainvoke", "astream"]


def _ask(chat: Any, mode: str, messages: list[BaseMessage]) -> AIMessage:
    if mode == "invoke":
        return chat.invoke(messages)
    if mode == "ainvoke":
        return asyncio.run(chat.ainvoke(messages))
    if mode == "stream":
        chunks = list(chat.stream(messages))
    else:
        async def go() -> list[Any]:
            return [c async for c in chat.astream(messages)]

        chunks = asyncio.run(go())
    total = chunks[0]
    for chunk in chunks[1:]:
        total = total + chunk
    return total  # type: ignore[no-any-return]


def test_replay_model_is_a_real_chat_anthropic_like_make_model(monkeypatch: pytest.MonkeyPatch) -> None:
    chat, transport = replay_chat_model(Recorder())
    assert type(chat) is ChatAnthropic and isinstance(transport, ReplayTransport)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    monkeypatch.delenv("STITCH_MODEL", raising=False)
    real = studio.make_model()
    assert isinstance(real, ChatAnthropic)
    for field in ("model", "max_tokens", "default_request_timeout", "stop_sequences", "max_retries", "streaming"):
        assert getattr(chat, field) == getattr(real, field), field
    assert chat.anthropic_api_key.get_secret_value() == replay.DUMMY_API_KEY
    assert replay_chat_model(Recorder(), model="claude-other")[0].model == "claude-other"


@pytest.mark.parametrize("mode", MODES)
def test_round_trip_text_single_and_parallel_tool_calls(mode: str) -> None:
    responder = Recorder(TEXT_REPLY, ONE_CALL, TWO_CALLS)
    chat, transport = replay_chat_model(responder)
    bound = chat.bind_tools([_get_weather, _get_time])
    history: list[BaseMessage] = [SystemMessage(content="You answer briefly."), HumanMessage(content="What day was it?")]

    first = _ask(bound, mode, history)
    assert first.text == "It was a Sunday." and first.tool_calls == []

    history += [first, HumanMessage(content="And the weather in Paris?")]
    second = _ask(bound, mode, history)
    assert _calls(second) == [("_get_weather", {"city": "Paris"}, "call_weather_1")]

    history += [second, ToolMessage(content="sunny", tool_call_id="call_weather_1"), HumanMessage(content="Oslo too?")]
    third = _ask(bound, mode, history)
    assert _calls(third) == [("_get_weather", {"city": "Oslo"}, "call_weather_2"), ("_get_time", {"zone": "UTC"}, "call_time_3")]
    assert third.text == "Checking both."

    # The streamed path really streamed; the plain path did not.
    streamed = mode in ("stream", "astream")
    assert [bool(body.get("stream")) for body in transport.requests] == [streamed] * 3
    assert transport.problems == [] and transport.responder_errors == []

    # The responder saw the conversation as langchain messages, with tools by name.
    messages, tools = responder.seen[-1]
    assert tools == ["_get_weather", "_get_time"]
    assert isinstance(messages[0], SystemMessage) and messages[0].text == "You answer briefly."
    tool_message = next(m for m in messages if isinstance(m, ToolMessage))
    assert (tool_message.tool_call_id, tool_message.name, tool_message.content) == ("call_weather_1", "_get_weather", "sunny")
    assert _calls(next(m for m in messages if isinstance(m, AIMessage) and m.tool_calls)) == [
        ("_get_weather", {"city": "Paris"}, "call_weather_1")]
    assert messages[-1] == HumanMessage(content="Oslo too?")


def test_parallel_tool_results_go_back_in_one_user_turn() -> None:
    chat, transport = replay_chat_model(Recorder(TWO_CALLS, TEXT_REPLY))
    bound = chat.bind_tools([_get_weather, _get_time])
    calls = bound.invoke([HumanMessage(content="Both please.")])
    bound.invoke([
        HumanMessage(content="Both please."), calls,
        ToolMessage(content="cold", tool_call_id="call_weather_2"),
        ToolMessage(content="noon", tool_call_id="call_time_3"),
    ])
    last = transport.requests[-1]["messages"]
    assert [m["role"] for m in last] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in last[2]["content"]] == ["call_weather_2", "call_time_3"]
    assert transport.problems == []


def test_responses_have_the_messages_api_shape() -> None:
    chat, transport = replay_chat_model(Recorder(TWO_CALLS, TEXT_REPLY))
    chat.bind_tools([_get_weather, _get_time]).invoke([HumanMessage(content="Both please.")])
    chat.invoke([HumanMessage(content="Thanks.")])
    calls, text = transport.replies
    assert calls["type"] == "message" and calls["role"] == "assistant" and calls["model"] == studio.DEFAULT_MODEL
    assert calls["id"].startswith("msg_") and calls["stop_reason"] == "tool_use"
    assert [b["type"] for b in calls["content"]] == ["text", "tool_use", "tool_use"]
    assert calls["content"][1] == {"type": "tool_use", "id": "call_weather_2", "name": "_get_weather", "input": {"city": "Oslo"}}
    assert text["stop_reason"] == "end_turn" and text["content"] == [{"type": "text", "text": "It was a Sunday."}]
    assert all(r["usage"]["input_tokens"] > 0 and r["usage"]["output_tokens"] > 0 for r in transport.replies)


def test_sse_stream_is_well_formed() -> None:
    message = {"id": "msg_1", "type": "message", "role": "assistant", "model": "m", "stop_reason": "tool_use",
               "stop_sequence": None, "usage": {"input_tokens": 3, "output_tokens": 4},
               "content": [{"type": "text", "text": "x" * 50},
                           {"type": "tool_use", "id": "t1", "name": "f", "input": {"a": "y" * 40}}]}
    events = [chunk for chunk in replay._sse(message).decode().split("\n\n") if chunk]  # noqa: SLF001
    names = [e.split("\n")[0].removeprefix("event: ") for e in events]
    datas = [json.loads(e.split("\n")[1].removeprefix("data: ")) for e in events]
    assert names[0] == "message_start" and names[-2:] == ["message_delta", "message_stop"]
    assert all(d["type"] == n for n, d in zip(names, datas, strict=True))
    text = "".join(d["delta"]["text"] for d in datas if d.get("delta", {}).get("type") == "text_delta")
    partial = "".join(d["delta"]["partial_json"] for d in datas if d.get("delta", {}).get("type") == "input_json_delta")
    assert text == "x" * 50 and json.loads(partial) == {"a": "y" * 40}
    assert names.count("content_block_start") == names.count("content_block_stop") == 2
    assert datas[-2]["delta"]["stop_reason"] == "tool_use"


@pytest.mark.parametrize("mode", ["invoke", "stream"])
def test_strict_transport_rejects_an_invalid_request_like_the_api(mode: str) -> None:
    responder = Recorder(TEXT_REPLY)
    chat, transport = replay_chat_model(responder)
    bad_tool = {"name": "get person", "description": "x", "input_schema": {"type": "object", "properties": {}}}
    with pytest.raises(anthropic.BadRequestError, match="should match pattern"):
        _ask(chat.bind_tools([bad_tool]), mode, [HumanMessage(content="Hi")])
    assert len(transport.requests) == 1  # a 400 is not retried
    assert transport.problems and transport.problems[0].startswith("request 0: tools.0.name")
    assert responder.seen == []


def test_lenient_transport_records_problems_and_answers() -> None:
    chat, transport = replay_chat_model(Recorder(TEXT_REPLY), strict=False)
    bad_tool = {"name": "get person", "description": "x", "input_schema": {"type": "object", "properties": {}}}
    reply = chat.bind_tools([bad_tool]).invoke([HumanMessage(content="Hi")])
    assert reply.text == "It was a Sunday."
    assert len(transport.problems) == 1


def test_a_responder_failure_is_a_400_and_is_not_retried() -> None:
    calls: list[int] = []

    def broken(_messages: list[BaseMessage], _tools: list[str]) -> AIMessage:
        calls.append(1)
        raise AssertionError("script lost its place")

    chat, transport = replay_chat_model(broken)
    with pytest.raises(anthropic.BadRequestError, match="script lost its place"):
        chat.invoke([HumanMessage(content="Hi")])
    assert calls == [1] and len(transport.responder_errors) == 1 and transport.problems == []


def test_only_messages_is_served() -> None:
    import httpx2

    transport = ReplayTransport(Recorder())
    with httpx2.Client(transport=transport, base_url=replay.BASE_URL) as client:
        response = client.post("/v1/complete", json={})
    assert response.status_code == 404 and response.json()["error"]["type"] == "not_found_error"


# --------------------------------------------------------------------------- studio harness (from test_agent_e2e)


class Studio:
    """One family's RunManager over fake services, with `model` as the brain."""

    def __init__(self, manager: Any, repo: Any) -> None:
        self.manager = manager
        self.repo = repo

    @classmethod
    def fake(cls, model: Any) -> Studio:
        """wiring.fake_services over an InMemoryRepo seeded with the hymn_books sample family."""
        repo = InMemoryRepo()
        load_sample_family(repo, FAMILY)
        return cls(wiring.make_manager(wiring.fake_services(model=model, repo=repo)), repo)

    def names(self, run_id: str) -> list[str]:
        return [name for name, _ in self.manager.events(FAMILY, run_id)]

    def drive(
        self, user_id: str, person_id: str, text: str, answers: list[Answer] | None = None,
        *, family_id: str = FAMILY, photo_ids: list[str] | None = None, **fields: Any,
    ) -> tuple[str, list[Event]]:
        """Start a run, follow it live with stream_run, and answer each pause in turn."""
        form = schemas.SubmitMemoryFields(text=text, year=1994, **fields)
        pending = list(answers or [])

        async def go() -> tuple[str, list[Event]]:
            run_id = self.manager.start_run(family_id, user_id, person_id, form, list(photo_ids or []))
            seen: list[Event] = []

            async def follow() -> None:
                async for event in self.manager.stream_run(family_id, run_id):
                    seen.append(event)

            follower = asyncio.create_task(follow())
            while True:
                status = await asyncio.to_thread(self.manager.wait, family_id, run_id, WAIT)
                names = [n for n, _ in self.manager.events(family_id, run_id)]
                assert status != "running", f"run still running after {WAIT}s: {names}"
                if status != "waiting":
                    break
                assert pending, f"run paused with no answer left: {names}"
                answer = pending.pop(0)
                if callable(answer):
                    answer = answer(self.manager.events(family_id, run_id))
                self.manager.answer_run(family_id, run_id, user_id, answer)  # type: ignore[arg-type]
            await asyncio.wait_for(follower, WAIT)
            assert not pending, f"answers left over: {pending}"
            return run_id, seen

        return asyncio.run(asyncio.wait_for(go(), 4 * WAIT))

    def memory(self, memory_id: str, family_id: str = FAMILY) -> schemas.Memory:
        memory = self.repo.get_memory(family_id, memory_id)
        assert memory is not None, memory_id
        return schemas.Memory.model_validate(memory.model_dump(mode="json", by_alias=True))


@pytest.fixture
def studios() -> Iterator[list[Studio]]:
    made: list[Studio] = []
    yield made
    for s in made:
        s.manager.shutdown(wait=True)


def wire_studio(studios: list[Studio], responder: Responder) -> tuple[Studio, ChatAnthropic, ReplayTransport]:
    chat, transport = replay_chat_model(responder)
    s = Studio.fake(chat)
    studios.append(s)
    return s, chat, transport


def scripted_studio(studios: list[Studio], responder: Responder) -> Studio:
    s = Studio.fake(ScriptedChatModel(responder=responder))
    studios.append(s)
    return s


def keep_all(events: list[Event]) -> schemas.AnswerRequest:
    found = [p for n, p in events if n == "props_found"]
    chips = found[-1].props  # type: ignore[attr-defined]
    return schemas.AnswerRequest(kind="props", props=[schemas.PropEdit(id=c.id, action="keep") for c in chips])


def answer_for(events: list[Event]) -> schemas.AnswerRequest:
    """The answer to whatever the run paused on: the follow-up, or the props (keep all)."""
    name = events[-1][0]
    if name == "question":
        return schemas.AnswerRequest(kind="answer", text=ANSWER)
    assert name == "props_found", name
    return keep_all(events)


def assert_typed(events: list[Event]) -> None:
    for name, payload in events:
        assert name in schemas.RUN_EVENTS, name
        assert isinstance(payload, schemas.RUN_EVENTS[name]), (name, type(payload))


def assert_valid_memory(s: Studio, events: list[Event], family_id: str = FAMILY) -> schemas.Memory:
    names = [n for n, _ in events]
    assert names[-1] == "done" and "error" not in names, events[-1]
    done = events[-1][1]
    assert isinstance(done, schemas.DoneEvent)
    memory = s.memory(done.memory_id, family_id)
    assert memory.timeline is not None and memory.screenplay is not None
    timeline = schemas.Timeline.model_validate(memory.timeline.model_dump(mode="json", by_alias=True))
    assert timeline.duration > 0 and timeline.shots and timeline.tracks
    assert all(shot.page_text and shot.page_text.strip() for shot in timeline.shots)
    return memory


def assert_clean(transport: ReplayTransport) -> None:
    assert transport.requests, "the model was never called"
    assert transport.problems == [], transport.problems[:5]
    assert transport.responder_errors == [], transport.responder_errors[:5]


def roles_called(transport: ReplayTransport) -> list[str]:
    return [role_of(replay.request_to_messages(body)) for body in transport.requests]


def tool_uses(body: dict[str, Any], name: str) -> list[str]:
    """Ids of every `name` tool_use in a request's history."""
    return [
        b["id"] for m in body["messages"] if m["role"] == "assistant" and isinstance(m["content"], list)
        for b in m["content"] if b.get("type") == "tool_use" and b.get("name") == name
    ]


def tool_results(body: dict[str, Any], use_ids: list[str]) -> list[dict[str, Any]]:
    return [
        b for m in body["messages"] if m["role"] == "user" and isinstance(m["content"], list)
        for b in m["content"] if b.get("type") == "tool_result" and b.get("tool_use_id") in use_ids
    ]


def result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content or [] if isinstance(b, dict))


def resumed_results(transport: ReplayTransport, tool: str) -> list[str]:
    """Texts of `tool`'s results in requests, i.e. results that went back after the run resumed."""
    texts: list[str] = []
    for body in transport.requests:
        texts.extend(result_text(b) for b in tool_results(body, tool_uses(body, tool)))
    return texts


def event_signature(events: list[Event]) -> list[tuple[str, Any]]:
    """Events as comparable data: names with their payloads as JSON."""
    return [(name, payload.model_dump(mode="json", by_alias=True)) for name, payload in events]


# --------------------------------------------------------------------------- the new memory over the wire


EXPECTED_TOOLS = {
    # the studio's own tools (wiring.STUDIO_TOOL_NAMES), the sub-agents' extra gates, and
    # what deepagents adds: the filesystem, the task tool and the planner
    *wiring.STUDIO_TOOL_NAMES, "confirm_props", "merge_people",
    "task", "write_todos", "ls", "read_file", "write_file", "edit_file", "glob", "grep",
}


def test_new_memory_over_the_wire(studios: list[Studio]) -> None:
    s, _, transport = wire_studio(studios, make_fake_studio_responder(STORY))
    _, events = s.drive(MOM_USER, "mom", MOM_TEXT, [answer_for, answer_for], participant_ids=["dad"])
    assert_typed(events)
    memory = assert_valid_memory(s, events)
    assert [t.author_id for t in memory.tellings] == ["mom"]
    assert_clean(transport)

    # The same story played by ScriptedChatModel streams the same events.
    baseline = scripted_studio(studios, make_fake_studio_responder(STORY))
    _, expected = baseline.drive(MOM_USER, "mom", MOM_TEXT, [answer_for, answer_for], participant_ids=["dad"])
    assert [n for n, _ in events] == [n for n, _ in expected]
    assert event_signature(events) == event_signature(expected)

    # The studio and all eight sub-agents talked to ChatAnthropic.
    assert set(roles_called(transport)) == {"studio", *SUBAGENT_NAMES}
    studio_requests = [b for b in transport.requests if "task" in replay.tool_names_of(b)]
    assert studio_requests and len(studio_requests) < len(transport.requests)

    # Every tool declared to the API, studio and sub-agents alike, passed the tool checks.
    declared = transport.declared_tools()
    assert EXPECTED_TOOLS <= set(declared), EXPECTED_TOOLS - set(declared)
    for name, tool in declared.items():
        assert replay.check_tool_definitions({"tools": [tool]}) == [], name
    for body in transport.requests:
        assert replay.check_tool_definitions(body) == []
        assert replay.count_cache_breakpoints(body) <= replay.MAX_CACHE_BREAKPOINTS

    # After each resumed interrupt a tool_result went back over the wire.
    props = resumed_results(transport, "confirm_props")
    assert props and all("| kept" in text and "removed: none" in text for text in props), props
    asked = resumed_results(transport, "ask_teller")
    assert asked and set(asked) == {ANSWER}, asked


def test_new_memory_over_the_wire_streaming(studios: list[Studio]) -> None:
    """The same run with ChatAnthropic(streaming=True): every request goes over SSE."""
    s, chat, transport = wire_studio(studios, make_fake_studio_responder(STORY))
    chat.streaming = True
    _, events = s.drive(MOM_USER, "mom", MOM_TEXT, [answer_for, answer_for], participant_ids=["dad"])
    assert_valid_memory(s, events)
    assert_clean(transport)
    assert all(body.get("stream") is True for body in transport.requests)


def with_preamble(base: Responder) -> Responder:
    """`base`, except that every tool-calling reply also says a sentence first, as Claude often does."""

    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        reply = base(messages, tool_names)
        if not reply.tool_calls or reply.text:
            return reply
        return AIMessage(content="Let me do the next step.", tool_calls=reply.tool_calls)

    return responder


def test_new_memory_with_text_before_tool_calls(studios: list[Studio]) -> None:
    """Assistant turns that carry text and tool_use blocks (incl. the paused ones) go back valid."""
    s, _, transport = wire_studio(studios, with_preamble(make_fake_studio_responder(STORY)))
    _, events = s.drive(MOM_USER, "mom", MOM_TEXT, [answer_for, answer_for], participant_ids=["dad"])
    assert_valid_memory(s, events)
    assert_clean(transport)
    mixed = [
        m for body in transport.requests for m in body["messages"]
        if m["role"] == "assistant" and isinstance(m["content"], list)
        and [b["type"] for b in m["content"]][:2] == ["text", "tool_use"]
    ]
    assert mixed
    assert any(tool_uses(body, "confirm_props") for body in transport.requests)


# --------------------------------------------------------------------------- props edit: rename + remove


def with_extra_chip(base: Responder, extra: dict[str, Any]) -> Responder:
    """`base`, except that the prop master's confirm_props call also shows `extra` (a saved prop)."""

    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        reply = base(messages, tool_names)
        if not any(call["name"] == "confirm_props" for call in reply.tool_calls):
            return reply
        calls = []
        for call in reply.tool_calls:
            if call["name"] == "confirm_props":
                call = {**call, "args": {**call["args"], "props": [*call["args"]["props"], extra]}}
            calls.append(call)
        return AIMessage(content=reply.content, tool_calls=calls)

    return responder


def rename_first_remove_second(events: list[Event]) -> schemas.AnswerRequest:
    chips = [p for n, p in events if n == "props_found"][-1].props  # type: ignore[attr-defined]
    assert len(chips) == 2, chips
    return schemas.AnswerRequest(kind="props", props=[
        schemas.PropEdit(id=chips[0].id, action="rename", name=RENAMED),
        schemas.PropEdit(id=chips[1].id, action="remove"),
    ])


def test_props_edit_rename_and_remove_over_the_wire(studios: list[Studio]) -> None:
    probe = InMemoryRepo()
    load_sample_family(probe, FAMILY)
    saved = probe.list_props(FAMILY)[0]
    extra = {"id": saved.id, "name": saved.name, "size": saved.size, "reused": True}

    s, _, transport = wire_studio(studios, with_extra_chip(make_fake_studio_responder(STORY), extra))
    before = {p.id for p in s.repo.list_props(FAMILY)}
    _, events = s.drive(MOM_USER, "mom", MOM_TEXT, [answer_for, rename_first_remove_second], participant_ids=["dad"])
    assert_typed(events)
    memory = assert_valid_memory(s, events)
    assert_clean(transport)

    found = next(p for n, p in events if n == "props_found")
    assert [c.id for c in found.props][1] == saved.id  # type: ignore[attr-defined]
    made = [p for p in s.repo.list_props(FAMILY) if p.id not in before]
    assert len(made) == 1 and made[0].name == RENAMED
    assert memory.prop_ids == [made[0].id] and saved.id not in memory.prop_ids

    # The edited confirm_props went back as a tool_result answering the model's own call.
    props = resumed_results(transport, "confirm_props")
    assert props and all(f"| {RENAMED} |" in t and "| renamed" in t and f"removed: {saved.id}" in t for t in props), props
    for body in transport.requests:
        uses = tool_uses(body, "confirm_props")
        if uses:
            assert len(tool_results(body, uses)) == len(uses)


# --------------------------------------------------------------------------- compile error loop + follow-up


def test_compile_error_loop_and_follow_up_over_the_wire(studios: list[Studio]) -> None:
    story = FakeStory(place="lake dock", prop="picnic basket", followup=FOLLOWUP, compile_failures=2)
    s, _, transport = wire_studio(studios, make_fake_studio_responder(story))
    _, events = s.drive(MOM_USER, "mom", MOM_TEXT, [answer_for, answer_for], participant_ids=["dad"])
    assert_typed(events)
    assert_valid_memory(s, events)
    assert_clean(transport)

    names = [n for n, _ in events]
    errors = [p for n, p in events if n == "compile_error"]
    assert len(errors) == 2 and [e.round for e in errors] == [1, 2]  # type: ignore[attr-defined]
    assert names.count("question") == 1
    assert next(p for n, p in events if n == "question") == schemas.QuestionEvent(text=FOLLOWUP)

    # The director saw both failed compiles and the passing one as tool results over the wire.
    compiles = resumed_results(transport, "compile_screenplay")
    assert sum(1 for t in set(compiles) if not t.lstrip().startswith("ok:")) >= 2
    assert any(t.lstrip().startswith("ok:") for t in compiles)
    assert set(resumed_results(transport, "ask_teller")) == {ANSWER}


# --------------------------------------------------------------------------- second telling


def test_second_telling_over_the_wire(studios: list[Studio]) -> None:
    s, _, transport = wire_studio(studios, make_fake_studio_responder(STORY))
    _, first = s.drive(MOM_USER, "mom", MOM_TEXT, [answer_for, answer_for], participant_ids=["dad"])
    original = assert_valid_memory(s, first)
    requests_before = len(transport.requests)

    _, events = s.drive(DAD_USER, "dad", DAD_TEXT, memory_id=original.id, participant_ids=["mom"])
    assert_typed(events)
    names = [n for n, _ in events]
    assert "props_found" not in names and "question" not in names
    memory = assert_valid_memory(s, events)
    assert memory.id == original.id and memory.version == original.version + 1
    assert [t.author_id for t in memory.tellings] == ["mom", "dad"]
    assert memory.timeline is not None and set(memory.timeline.narrations or {}) == {"mom", "dad"}
    assert len(transport.requests) > requests_before
    assert_clean(transport)


# --------------------------------------------------------------------------- production-shaped build


def _vision_reply(messages: list[BaseMessage], _tools: list[str]) -> AIMessage:
    assert isinstance(messages[-1], HumanMessage)
    return AIMessage(content="verdict: pass\nframe 1: the stage is drawn")


@pytest.fixture
def production_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from db import client as db_client
    from seed.fake import install_fake_db

    for name in ("IMAGE_API_KEY", "ELEVENLABS_API_KEY", "MONGODB_URI", "STITCH_WEB_URL",
                 "STITCH_SUBAGENT_MODEL", "STITCH_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.setenv("STITCH_FAKE_DB", "1")
    install_fake_db(seed=True)  # what agent.demo does for STITCH_FAKE_DB=1: mongomock + seed.demo.seed_demo
    yield
    db_client.set_db(None)


def test_production_services_run_over_the_wire(production_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx2

    from agent.adapters import AnthropicVision
    from seed import demo as seed_demo

    chat, transport = replay_chat_model(make_fake_studio_responder(STORY))
    made: list[str | None] = []

    def make_model(model: str | None = None) -> ChatAnthropic:
        made.append(model)
        return chat

    monkeypatch.setattr(studio, "make_model", make_model)
    # Claude vision (describe_photo, inspect_frames) is a plain anthropic client: give it a
    # replay transport too, so its requests are validated and nothing leaves the process.
    vision_transport = ReplayTransport(_vision_reply)
    vision_client = anthropic.Anthropic(api_key="dummy", http_client=httpx2.Client(transport=vision_transport,
                                                                                    base_url=replay.BASE_URL))
    monkeypatch.setattr(production, "AnthropicVision", lambda: AnthropicVision(client=vision_client))

    services = production.production_services()
    assert services.model is chat and made == [None]
    assert services.labels["persistence"] == "memory" and services.labels["repo"] == "mongo"
    manager = production.production_manager()
    family = seed_demo.DEMO_FAMILY_ID
    account = next(a for a in seed_demo.DEMO_ACCOUNTS if a.person_id == "mom")
    s = Studio(manager, services.repo)
    try:
        _, events = s.drive(account.user_id, "mom", MOM_TEXT, [answer_for, answer_for],
                            family_id=family, participant_ids=["dad"])
    finally:
        manager.shutdown(wait=True)
    assert_typed(events)
    memory = assert_valid_memory(s, events, family)
    assert [t.author_id for t in memory.tellings] == ["mom"]
    assert_clean(transport)
    assert set(roles_called(transport)) == {"studio", *SUBAGENT_NAMES}
    # inspect_frames went to Claude vision over its own replay transport, with images.
    assert vision_transport.requests and vision_transport.problems == [] and vision_transport.responder_errors == []
    assert any(b.get("type") == "image" for b in vision_transport.requests[0]["messages"][0]["content"])
    assert any(n == "frames" for n, _ in events)
