"""A real ChatAnthropic over a replaying Messages API transport: the keyed studio, offline.

Every studio test plays the model with `agent.fake_llm.ScriptedChatModel`, which never
builds an Anthropic request. This module puts the real `langchain_anthropic.ChatAnthropic`
in its place and answers its HTTP calls in process:

    chat, transport = replay_chat_model(make_fake_studio_responder(FakeStory()))
    services = wiring.fake_services(model=chat)

`ReplayTransport` receives each `POST /v1/messages` the anthropic SDK sends, checks the
body against the Messages API rules that bite in practice (`validate_request`), turns the
request back into langchain messages, asks the same `Responder` a `ScriptedChatModel`
would, and returns a Messages API response: JSON, or the server-sent event stream when the
request has `stream: true`. Every body is kept in `transport.requests` and every rule it
breaks in `transport.problems`; with `strict=True` (the default) a broken request gets the
HTTP 400 `invalid_request_error` the real API would send, so ChatAnthropic raises.

The anthropic SDK (2.8) sends through `httpx2`, not `httpx`, and refuses `httpx` objects,
so the transport is an `httpx2` transport (sync and async in one class) and the model's
cached `_client` / `_async_client` are replaced with SDK clients over it. No socket is
opened and no key is needed: the API key is a dummy.
"""

from __future__ import annotations

import itertools
import json
import re
import threading
from collections.abc import Collection, Iterable, Sequence
from typing import Any

import anthropic
import httpx2
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import SecretStr

from agent.fake_llm import Responder

MESSAGES_PATH = "/v1/messages"
BASE_URL = "https://api.anthropic.com"
DUMMY_API_KEY = "sk-ant-replay-dummy"  # noqa: S105 - not a secret: the transport never sends it anywhere

MAX_TOKENS = 16_000
"""What studio.make_model asks for (checked against it by the tests)."""

TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
TOOL_USE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")
MAX_CACHE_BREAKPOINTS = 4
JSON_CHUNK = 24
"""Characters per input_json_delta when streaming a tool call's input."""


# --------------------------------------------------------------------------- validation


def _blocks(content: Any) -> list[Any]:
    """A message's content as a list of blocks (a string is one text block)."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content) if isinstance(content, list) else []


def _where(i: int, j: int | None = None) -> str:
    return f"messages.{i}" if j is None else f"messages.{i}.content.{j}"


def check_model_and_max_tokens(body: dict[str, Any]) -> list[str]:
    """`model` is a non-empty string; `max_tokens` is a positive int."""
    problems: list[str] = []
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        problems.append("model: field required (a non-empty string)")
    max_tokens = body.get("max_tokens")
    if max_tokens is None:
        problems.append("max_tokens: field required")
    elif isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        problems.append(f"max_tokens: must be a positive integer, got {max_tokens!r}")
    return problems


def check_roles(body: dict[str, Any]) -> list[str]:
    """`messages` is non-empty, starts with a user turn, and user/assistant alternate."""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return ["messages: at least one message is required"]
    problems: list[str] = []
    previous: str | None = None
    for i, message in enumerate(messages):
        role = message.get("role") if isinstance(message, dict) else None
        if role not in ("user", "assistant"):
            problems.append(f"{_where(i)}.role: must be 'user' or 'assistant', got {role!r}")
            previous = None
            continue
        if i == 0 and role != "user":
            problems.append(f"{_where(0)}.role: the first message must use the 'user' role, got {role!r}")
        if previous == role:
            problems.append(f"{_where(i)}: roles must alternate between 'user' and 'assistant'; two {role!r} turns in a row")
        previous = role
    return problems


def check_tool_pairing(body: dict[str, Any]) -> list[str]:
    """Each tool_use is answered by a tool_result in the next user turn, and each tool_result
    answers a tool_use in the turn just before it; tool_use ids are well formed and unique;
    a user turn's tool_result blocks come before anything else in it."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return []
    problems: list[str] = []
    seen_ids: set[str] = set()
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        blocks = _blocks(message.get("content"))
        if message.get("role") == "assistant":
            uses = [(j, b) for j, b in enumerate(blocks) if isinstance(b, dict) and b.get("type") == "tool_use"]
            for j, block in uses:
                use_id = block.get("id")
                if not isinstance(use_id, str) or not TOOL_USE_ID.match(use_id):
                    problems.append(f"{_where(i, j)}.id: String should match pattern '^[a-zA-Z0-9_-]+$', got {use_id!r}")
                elif use_id in seen_ids:
                    problems.append(f"{_where(i, j)}.id: `tool_use` ids must be unique; {use_id!r} is used twice")
                if isinstance(use_id, str):
                    seen_ids.add(use_id)
            if not uses:
                continue
            following = messages[i + 1] if i + 1 < len(messages) else None
            answered: set[str] = set()
            if isinstance(following, dict) and following.get("role") == "user":
                answered = {
                    b.get("tool_use_id") for b in _blocks(following.get("content"))
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                }
            missing = [b.get("id") for _, b in uses if b.get("id") not in answered]
            if missing:
                problems.append(
                    f"{_where(i)}: `tool_use` ids were found without `tool_result` blocks immediately after: "
                    + ", ".join(str(m) for m in missing)
                    + ". Each `tool_use` block must have a corresponding `tool_result` block in the next message."
                )
        elif message.get("role") == "user":
            previous = messages[i - 1] if i > 0 else None
            asked: set[str] = set()
            if isinstance(previous, dict) and previous.get("role") == "assistant":
                asked = {
                    b.get("id") for b in _blocks(previous.get("content"))
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                }
            other_seen = False
            for j, block in enumerate(blocks):
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    other_seen = True
                    continue
                if other_seen:
                    problems.append(f"{_where(i, j)}: `tool_result` blocks must come before any other content in a user turn")
                if block.get("tool_use_id") not in asked:
                    problems.append(
                        f"{_where(i, j)}: unexpected `tool_use_id` found in `tool_result` blocks: "
                        f"{block.get('tool_use_id')!r}. Each `tool_result` block must have a corresponding "
                        "`tool_use` block in the previous message."
                    )
    return problems


def _empty_text(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "text" and not str(block.get("text") or "").strip()


def check_empty_content(body: dict[str, Any]) -> list[str]:
    """No message has empty content and no text block (also inside a tool_result) is empty."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return []
    problems: list[str] = []
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if content is None or content == [] or (isinstance(content, str) and not content.strip()):
            problems.append(f"{_where(i)}: all messages must have non-empty content")
            continue
        for j, block in enumerate(_blocks(content) if isinstance(content, list) else []):
            if _empty_text(block):
                problems.append(f"{_where(i, j)}: text content blocks must be non-empty")
            if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                for k, inner in enumerate(block["content"]):
                    if _empty_text(inner):
                        problems.append(f"{_where(i, j)}.content.{k}: text content blocks must be non-empty")
    return problems


def check_tool_definitions(body: dict[str, Any]) -> list[str]:
    """Tool names match ^[a-zA-Z0-9_-]{1,64}$ and are unique; each tool has a description
    and an input_schema of type 'object'."""
    tools = body.get("tools")
    if tools is None:
        return []
    if not isinstance(tools, list):
        return [f"tools: must be a list, got {type(tools).__name__}"]
    problems: list[str] = []
    names: set[str] = set()
    for i, tool in enumerate(tools):
        if not isinstance(tool, dict):
            problems.append(f"tools.{i}: must be an object")
            continue
        if tool.get("type") not in (None, "custom"):
            continue  # a server tool (web_search, ...) has its own shape
        name = tool.get("name")
        if not isinstance(name, str) or not TOOL_NAME.match(name):
            problems.append(f"tools.{i}.name: String should match pattern '^[a-zA-Z0-9_-]{{1,64}}$', got {name!r}")
        elif name in names:
            problems.append(f"tools: tool names must be unique; {name!r} is declared twice")
        else:
            names.add(name)
        description = tool.get("description")
        if not isinstance(description, str) or not description.strip():
            problems.append(f"tools.{i} ({name}): description is missing or empty")
        schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            problems.append(f"tools.{i} ({name}).input_schema: field required")
        elif schema.get("type") != "object":
            problems.append(f"tools.{i} ({name}).input_schema.type: must be 'object', got {schema.get('type')!r}")
    return problems


def check_tool_use_declared(body: dict[str, Any], removed_tools: Collection[str] = ()) -> list[str]:
    """A request with tool_use / tool_result blocks declares tools, and every tool_use name in
    the history is a declared tool, unless it is listed in `removed_tools` (a tool the caller
    knows was taken away between turns)."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return []
    declared = {t.get("name") for t in body.get("tools") or [] if isinstance(t, dict)}
    problems: list[str] = []
    has_tool_blocks = False
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        for j, block in enumerate(_blocks(message.get("content"))):
            if not isinstance(block, dict) or block.get("type") not in ("tool_use", "tool_result"):
                continue
            has_tool_blocks = True
            if block.get("type") == "tool_use":
                name = block.get("name")
                if declared and name not in declared and name not in removed_tools:
                    problems.append(f"{_where(i, j)}: tool_use names {name!r}, which is not among the declared tools")
    if has_tool_blocks and not declared:
        problems.append("tools: requests which include `tool_use` or `tool_result` blocks must define tools")
    return problems


def check_system(body: dict[str, Any]) -> list[str]:
    """`system`, when present, is a string or a list of non-empty text blocks."""
    if "system" not in body:
        return []
    system = body["system"]
    if isinstance(system, str):
        return []
    if not isinstance(system, list):
        return [f"system: must be a string or a list of text blocks, got {type(system).__name__}"]
    problems: list[str] = []
    for i, block in enumerate(system):
        if not isinstance(block, dict) or block.get("type") != "text":
            kind = block.get("type") if isinstance(block, dict) else type(block).__name__
            problems.append(f"system.{i}: must be a text block, got {kind!r}")
        elif _empty_text(block):
            problems.append(f"system.{i}: text content blocks must be non-empty")
    return problems


def _cache_controls(value: Any) -> int:
    if isinstance(value, dict):
        return int("cache_control" in value and value["cache_control"] is not None) + sum(
            _cache_controls(v) for k, v in value.items() if k != "cache_control"
        )
    if isinstance(value, list):
        return sum(_cache_controls(v) for v in value)
    return 0


def count_cache_breakpoints(body: dict[str, Any]) -> int:
    """cache_control markers in a request: the top-level one, system, tools and message blocks."""
    top = int(body.get("cache_control") is not None)
    return top + sum(_cache_controls(body.get(key)) for key in ("system", "tools", "messages"))


def check_cache_control(body: dict[str, Any]) -> list[str]:
    """At most 4 cache_control breakpoints (the top-level one counts)."""
    count = count_cache_breakpoints(body)
    if count > MAX_CACHE_BREAKPOINTS:
        return [f"A maximum of {MAX_CACHE_BREAKPOINTS} blocks with cache_control may be provided. Found {count}."]
    return []


CHECKS = (
    check_model_and_max_tokens,
    check_roles,
    check_tool_pairing,
    check_empty_content,
    check_tool_definitions,
    check_tool_use_declared,
    check_system,
    check_cache_control,
)
"""Every rule validate_request applies, in order."""


def validate_request(body: dict[str, Any], *, removed_tools: Collection[str] = ()) -> list[str]:
    """Every way `body` breaks the Messages API rules above ([] for a valid request)."""
    if not isinstance(body, dict):
        return ["body: must be a JSON object"]
    problems: list[str] = []
    for check in CHECKS:
        if check is check_tool_use_declared:
            problems.extend(check_tool_use_declared(body, removed_tools))
        else:
            problems.extend(check(body))
    return problems


# --------------------------------------------------------------------------- request -> messages


def _text_of(blocks: Iterable[Any]) -> str:
    return "".join(str(b.get("text") or "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")


def _strip_cache_control(block: Any) -> Any:
    if isinstance(block, dict):
        return {k: v for k, v in block.items() if k != "cache_control"}
    return block


def _tool_result_content(block: dict[str, Any]) -> str | list[Any]:
    content = block.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    blocks = [_strip_cache_control(b) for b in content]
    if all(isinstance(b, dict) and b.get("type") == "text" for b in blocks):
        return _text_of(blocks)
    return blocks


def request_to_messages(body: dict[str, Any]) -> list[BaseMessage]:
    """The request's `system` and `messages` as langchain messages.

    An assistant turn becomes one AIMessage (its text, and its tool_use blocks as
    tool_calls); a user turn becomes one ToolMessage per tool_result (named after the
    matching tool_use) followed by a HumanMessage for whatever else it carries.
    """
    out: list[BaseMessage] = []
    system = body.get("system")
    if isinstance(system, str) and system:
        out.append(SystemMessage(content=system))
    elif isinstance(system, list) and system:
        out.append(SystemMessage(content=[_strip_cache_control(b) for b in system]))
    names: dict[str, str] = {}
    for message in body.get("messages") or []:
        blocks = _blocks(message.get("content"))
        if message.get("role") == "assistant":
            calls = []
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    names[block["id"]] = block["name"]
                    calls.append({"name": block["name"], "args": dict(block.get("input") or {}),
                                  "id": block["id"], "type": "tool_call"})
            out.append(AIMessage(content=_text_of(blocks), tool_calls=calls))
            continue
        rest: list[Any] = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                use_id = str(block.get("tool_use_id"))
                out.append(ToolMessage(
                    content=_tool_result_content(block),
                    tool_call_id=use_id,
                    name=names.get(use_id),
                    status="error" if block.get("is_error") else "success",
                ))
            else:
                rest.append(_strip_cache_control(block))
        if rest:
            if all(isinstance(b, dict) and b.get("type") == "text" for b in rest):
                out.append(HumanMessage(content=_text_of(rest)))
            else:
                out.append(HumanMessage(content=rest))
    return out


def tool_names_of(body: dict[str, Any]) -> list[str]:
    """Names of the tools the request declares, in order."""
    return [str(t["name"]) for t in body.get("tools") or [] if isinstance(t, dict) and "name" in t]


# --------------------------------------------------------------------------- reply -> response


def _reply_text(reply: AIMessage) -> str:
    content = reply.content
    if isinstance(content, str):
        return content
    return _text_of(content)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class ReplayTransport(httpx2.AsyncBaseTransport, httpx2.BaseTransport):
    """An httpx2 transport (sync and async) that answers the Messages API from a Responder.

    `requests` holds every request body received, `problems` every validation problem
    (each prefixed with the request's index), `responder_errors` every exception the
    responder raised. With `strict=True` a request with problems gets HTTP 400
    `invalid_request_error`; a responder exception always does (a non-retryable status, so
    the SDK does not call the responder again).
    """

    def __init__(self, responder: Responder, *, strict: bool = True, removed_tools: Collection[str] = ()) -> None:
        self.responder = responder
        self.strict = strict
        self.removed_tools = frozenset(removed_tools)
        self.requests: list[dict[str, Any]] = []
        self.problems: list[str] = []
        self.responder_errors: list[str] = []
        self.replies: list[dict[str, Any]] = []
        """The response message (JSON form) sent for each request that got one."""
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    # -- httpx2 entry points

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        request.read()
        return self._handle(request)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        await request.aread()
        return self._handle(request)

    # -- the API

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        if request.method != "POST" or request.url.path != MESSAGES_PATH:
            return _error(404, "not_found_error", f"replay transport only serves POST {MESSAGES_PATH}, got "
                                                  f"{request.method} {request.url.path}")
        try:
            body = json.loads(request.content or b"null")
        except ValueError as exc:
            return _error(400, "invalid_request_error", f"request body is not JSON: {exc}")
        with self._lock:
            index = len(self.requests)
            self.requests.append(body)
        problems = validate_request(body, removed_tools=self.removed_tools)
        if problems:
            with self._lock:
                self.problems.extend(f"request {index}: {p}" for p in problems)
            if self.strict:
                return _error(400, "invalid_request_error", "; ".join(problems))
        try:
            reply = self.responder(request_to_messages(body), tool_names_of(body))
            if not isinstance(reply, AIMessage):
                raise TypeError(f"responder must return an AIMessage, got {type(reply).__name__}")
        except Exception as exc:  # noqa: BLE001 - surfaced as a 400 and kept for the test
            message = f"request {index}: responder raised {type(exc).__name__}: {exc}"
            with self._lock:
                self.responder_errors.append(message)
            return _error(400, "invalid_request_error", message)
        message = self._message(body, reply)
        with self._lock:
            self.replies.append(message)
        if body.get("stream"):
            return httpx2.Response(200, headers={"content-type": "text/event-stream", "request-id": message["id"]},
                                   content=_sse(message))
        return httpx2.Response(200, headers={"content-type": "application/json", "request-id": message["id"]},
                               json=message)

    def _message(self, body: dict[str, Any], reply: AIMessage) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        text = _reply_text(reply)
        if text:
            content.append({"type": "text", "text": text})
        for call in reply.tool_calls:
            call_id = call.get("id") or f"toolu_replay_{next(self._ids)}"
            content.append({"type": "tool_use", "id": call_id, "name": call["name"], "input": dict(call.get("args") or {})})
        output = text + "".join(json.dumps(b.get("input")) for b in content if b["type"] == "tool_use")
        return {
            "id": f"msg_replay_{next(self._ids)}",
            "type": "message",
            "role": "assistant",
            "model": body.get("model"),
            "content": content,
            "stop_reason": "tool_use" if reply.tool_calls else "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": _estimate_tokens(json.dumps(body.get("messages"))),
                "output_tokens": _estimate_tokens(output),
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }

    # -- inspection helpers for tests

    def declared_tools(self) -> dict[str, dict[str, Any]]:
        """Every tool declared across all requests, by name (the last definition seen)."""
        tools: dict[str, dict[str, Any]] = {}
        for body in self.requests:
            for tool in body.get("tools") or []:
                if isinstance(tool, dict) and "name" in tool:
                    tools[str(tool["name"])] = tool
        return tools


def _error(status: int, kind: str, message: str) -> httpx2.Response:
    return httpx2.Response(
        status,
        headers={"content-type": "application/json", "request-id": "req_replay_error"},
        json={"type": "error", "error": {"type": kind, "message": message}},
    )


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def _sse(message: dict[str, Any]) -> bytes:
    """The server-sent event stream the API sends for `message` when asked to stream."""
    start = {**message, "content": [], "stop_reason": None, "stop_sequence": None,
             "usage": {**message["usage"], "output_tokens": 1}}
    parts = [_event("message_start", {"type": "message_start", "message": start})]
    for index, block in enumerate(message["content"]):
        if block["type"] == "text":
            parts.append(_event("content_block_start", {"type": "content_block_start", "index": index,
                                                        "content_block": {"type": "text", "text": ""}}))
            text = block["text"]
            for at in range(0, len(text), JSON_CHUNK):
                parts.append(_event("content_block_delta", {"type": "content_block_delta", "index": index,
                                                            "delta": {"type": "text_delta", "text": text[at:at + JSON_CHUNK]}}))
        else:
            parts.append(_event("content_block_start", {
                "type": "content_block_start", "index": index,
                "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}},
            }))
            raw = json.dumps(block["input"])
            for at in range(0, len(raw), JSON_CHUNK):
                parts.append(_event("content_block_delta", {"type": "content_block_delta", "index": index,
                                                            "delta": {"type": "input_json_delta",
                                                                      "partial_json": raw[at:at + JSON_CHUNK]}}))
        parts.append(_event("content_block_stop", {"type": "content_block_stop", "index": index}))
    parts.append(_event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
        "usage": {"output_tokens": message["usage"]["output_tokens"]},
    }))
    parts.append(_event("message_stop", {"type": "message_stop"}))
    return "".join(parts).encode()


# --------------------------------------------------------------------------- the model


def replay_chat_model(
    responder: Responder,
    *,
    model: str | None = None,
    strict: bool = True,
    removed_tools: Sequence[str] = (),
) -> tuple[ChatAnthropic, ReplayTransport]:
    """A real ChatAnthropic whose sync and async SDK clients send through a ReplayTransport.

    Configured like `studio.make_model` (model id, max_tokens=16000, timeout=None, no stop
    sequences) with a dummy API key and the default base URL. The model's cached
    `_client` / `_async_client` are set to anthropic clients over `httpx2.Client` /
    `httpx2.AsyncClient` with the transport, so every request it makes, streamed or not,
    sync or async, is answered in process.
    """
    from agent.studio import DEFAULT_MODEL

    chat = ChatAnthropic(
        model_name=model or DEFAULT_MODEL,
        max_tokens=MAX_TOKENS,
        timeout=None,
        stop=None,
        api_key=SecretStr(DUMMY_API_KEY),
        base_url=BASE_URL,
    )
    transport = ReplayTransport(responder, strict=strict, removed_tools=removed_tools)
    params = dict(chat._client_params)  # noqa: SLF001 - the SDK client settings ChatAnthropic would use
    timeout = params.get("timeout")
    sync_client = anthropic.Client(**params, http_client=httpx2.Client(transport=transport, base_url=BASE_URL,
                                                                      timeout=timeout))
    async_client = anthropic.AsyncClient(**params, http_client=httpx2.AsyncClient(transport=transport, base_url=BASE_URL,
                                                                                 timeout=timeout))
    # Fill the cached_property slots, so ChatAnthropic never builds its default clients.
    chat.__dict__["_client"] = sync_client
    chat.__dict__["_async_client"] = async_client
    if chat._client is not sync_client or chat._async_client is not async_client:  # noqa: SLF001
        raise RuntimeError("could not install the replay clients on ChatAnthropic")
    return chat, transport


__all__ = [
    "CHECKS",
    "ReplayTransport",
    "check_cache_control",
    "check_empty_content",
    "check_model_and_max_tokens",
    "check_roles",
    "check_system",
    "check_tool_definitions",
    "check_tool_pairing",
    "check_tool_use_declared",
    "count_cache_breakpoints",
    "replay_chat_model",
    "request_to_messages",
    "tool_names_of",
    "validate_request",
]
