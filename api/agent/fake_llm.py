"""A deterministic, scripted chat model for running the studio offline.

`ScriptedChatModel` stands in for Claude in tests. It never touches the
network: every reply comes from a `responder` callable that receives the
messages the agent sent and the names of the tools bound for that call, and
returns the next `AIMessage` (usually one or more tool calls).

The same instance serves the studio and every sub-agent (deepagents passes the
parent model to declarative sub-agents unless a spec overrides it), so one
responder can script a whole run. It can tell which agent it is serving from
`system_prompt_of(messages)` or from the bound tool names: only the studio has
the `task` tool; a sub-agent never does.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field

Responder = Callable[[list[BaseMessage], list[str]], AIMessage]
"""(messages sent to the model, names of the tools bound for this call) -> reply."""

_ids = itertools.count(1)


def tool_call(name: str, args: dict[str, Any], id: str | None = None) -> AIMessage:  # noqa: A002
    """An AIMessage that makes exactly one tool call."""
    call_id = id or f"call_{name}_{next(_ids)}"
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def tool_calls(*calls: tuple[str, dict[str, Any]]) -> AIMessage:
    """An AIMessage that makes several tool calls in parallel."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call_{name}_{next(_ids)}", "type": "tool_call"}
            for name, args in calls
        ],
    )


def last_tool_message(messages: Sequence[BaseMessage]) -> ToolMessage | None:
    """The most recent ToolMessage, or None if the agent has not called a tool yet."""
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            return message
    return None


def tool_messages(messages: Sequence[BaseMessage], name: str | None = None) -> list[ToolMessage]:
    """Every ToolMessage so far, optionally only those answering tool `name`."""
    return [m for m in messages if isinstance(m, ToolMessage) and (name is None or m.name == name)]


def system_prompt_of(messages: Sequence[BaseMessage]) -> str:
    """The text of the system message (empty if there is none)."""
    for message in messages:
        if isinstance(message, SystemMessage):
            return message.text if isinstance(message.text, str) else str(message.content)
    return ""


def _tool_name(tool: Any) -> str:
    if isinstance(tool, BaseTool):
        return tool.name
    if isinstance(tool, dict) and "name" in tool:
        return str(tool["name"])
    return convert_to_openai_tool(tool)["function"]["name"]


class ScriptedChatModel(BaseChatModel):
    """A chat model whose every reply is decided by a Python callable.

    `bind_tools` returns a copy that remembers the bound tool names; the copy
    shares `calls` with the original so a test can inspect every call made by
    the studio and its sub-agents, in order.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    responder: Responder
    bound_tool_names: list[str] = Field(default_factory=list)
    calls: list[tuple[list[str], list[BaseMessage]]] = Field(default_factory=list)
    """Shared log of (bound tool names, messages) for every call, across copies."""

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": "scripted"}

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable | BaseTool],
        *,
        tool_choice: str | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> Runnable[LanguageModelInput, AIMessage]:
        bound = self.model_copy(update={"bound_tool_names": [_tool_name(t) for t in tools]})
        bound.calls = self.calls  # model_copy is shallow, but be explicit: one shared log
        return bound

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: CallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> ChatResult:
        names = list(self.bound_tool_names)
        self.calls.append((names, list(messages)))
        reply = self.responder(list(messages), names)
        if not isinstance(reply, AIMessage):
            msg = f"responder must return an AIMessage, got {type(reply).__name__}"
            raise TypeError(msg)
        return ChatResult(generations=[ChatGeneration(message=reply)])
