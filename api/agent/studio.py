"""Builds the deep agent (create_deep_agent): the pop-up book studio.

`build_studio` is the one place the studio graph is assembled. Everything a run
depends on is passed in (model, tools, sub-agents, interrupt config,
checkpointer, store, prompt) so tests run it with `ScriptedChatModel`,
`InMemorySaver` and `InMemoryStore`, and production with ChatAnthropic,
MongoDBSaver and MongoDBStore.

Files the agent sees (one CompositeBackend):

- `/` (default): StateBackend. Drafts like `/shotlist.md` and `/screenplay.json`
  live in the checkpointed `files` state key, so tools can read them through
  `InjectedState` and sub-agent writes flow back to the studio's state.
- `/memories/`: StoreBackend over the injected store, namespaced by family id,
  so one family's notes are invisible to every other family's studio.
- `/skills/`: the skill folders under `agent/skills/`, read-only.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission, SubAgent, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from deepagents.backends.protocol import DeleteResult, EditResult, FileUploadResponse, WriteResult
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer

from agent.guards import RunLimits

if TYPE_CHECKING:
    from langgraph.checkpoint.mongodb import MongoDBSaver
    from langgraph.graph.state import CompiledStateGraph
    from langgraph.store.mongodb import MongoDBStore

DEFAULT_MODEL = "claude-opus-5-5"
"""Claude model for the studio and its sub-agents; override with env STITCH_MODEL."""

SKILLS_DIR = Path(__file__).parent / "skills"

SKILLS_ROUTE = "/skills/"
MEMORIES_ROUTE = "/memories/"

SKILL_SOURCES = [SKILLS_ROUTE]
"""Skill sources for deepagents' SkillsMiddleware, for the studio and every sub-agent.

A source is a skills ROOT: a directory whose sub-directories each hold a SKILL.md
(`/skills/popup-style/SKILL.md`, `/skills/storyboarding/SKILL.md`,
`/skills/directing/SKILL.md`), so the one source `/skills/` loads all three.
One skill's own folder (`/skills/directing/`) is NOT a source: the middleware
lists a source's sub-folders, finds none there, and loads nothing
(`check_skill_sources` rejects it). Only each skill's name, description and
SKILL.md path go into the system prompt; the agent reads a body when it needs it.
Each SKILL.md needs YAML frontmatter with `name` (equal to its folder name) and
`description`, or the middleware skips it.
"""

RECURSION_LIMIT = RunLimits.max_graph_steps
"""Default graph steps per run (env STITCH_MAX_GRAPH_STEPS overrides it, see
`agent.guards.RunLimits`). One model turn plus its tool calls is about 2 steps and
sub-agents run inside the `task` tool, so 1000 allows several hundred studio
turns: far beyond a normal run, low enough to stop a looping agent."""

_NAMESPACE_COMPONENT = re.compile(r"^[A-Za-z0-9\-_.@+:~]+$")
"""What deepagents' StoreBackend accepts in a namespace component."""


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


def make_model(model: str | None = None) -> BaseChatModel:
    """The production chat model: ChatAnthropic, model id from `model`, env STITCH_MODEL, or DEFAULT_MODEL.

    Each request is bounded by `RunLimits.model_timeout_s` seconds and retried up to
    `RunLimits.model_max_retries` times (env STITCH_MODEL_TIMEOUT_S, STITCH_MODEL_MAX_RETRIES),
    so a hung request cannot stall a run forever.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        msg = "ANTHROPIC_API_KEY is not set"
        raise RuntimeError(msg)
    from langchain_anthropic import ChatAnthropic

    limits = RunLimits.from_env()
    model_id = model or os.environ.get("STITCH_MODEL") or DEFAULT_MODEL
    return ChatAnthropic(
        model_name=model_id,
        max_tokens=16_000,
        timeout=limits.model_timeout_s,
        max_retries=limits.model_max_retries,
        stop=None,
    )


# --------------------------------------------------------------------------
# Backend
# --------------------------------------------------------------------------

_READ_ONLY = "read-only: {path} is a shared skill file and cannot be changed"


class ReadOnlyFilesystemBackend(FilesystemBackend):
    """A FilesystemBackend that refuses every write, edit, upload and delete."""

    def write(self, file_path: str, content: str) -> WriteResult:  # noqa: ARG002
        return WriteResult(error=_READ_ONLY.format(path=file_path))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:  # noqa: ARG002, FBT001, FBT002
        return EditResult(error=_READ_ONLY.format(path=file_path))

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:  # noqa: FBT001, FBT002
        return self.edit(file_path, old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=_READ_ONLY.format(path=file_path))

    async def adelete(self, file_path: str) -> DeleteResult:
        return self.delete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)


def check_family_id(family_id: str) -> str:
    """Reject a family id that cannot be a store namespace component (empty, wildcards, slashes)."""
    if not isinstance(family_id, str) or not _NAMESPACE_COMPONENT.match(family_id):
        msg = f"invalid family_id {family_id!r}"
        raise ValueError(msg)
    return family_id


def memories_namespace(family_id: str) -> tuple[str, ...]:
    """Store namespace that holds one family's `/memories/` files."""
    return ("families", check_family_id(family_id), "memories")


def make_backend(*, family_id: str, skills_dir: Path = SKILLS_DIR) -> CompositeBackend:
    """Route `/` to agent state, `/memories/` to this family's store namespace, `/skills/` to read-only disk.

    The `/memories/` namespace is fixed from `family_id` when the studio is
    built, not taken from anything the model or the run config supplies, so a
    prompt can never reach another family's notes.
    """
    namespace = memories_namespace(family_id)
    return CompositeBackend(
        default=StateBackend(),
        routes={
            MEMORIES_ROUTE: StoreBackend(namespace=lambda _runtime: namespace),
            SKILLS_ROUTE: ReadOnlyFilesystemBackend(root_dir=skills_dir, virtual_mode=True),
        },
    )


SKILLS_READ_ONLY = FilesystemPermission(operations=["write"], paths=[f"{SKILLS_ROUTE}**"], mode="deny")
"""Second guard (tool level) on top of ReadOnlyFilesystemBackend (backend level)."""


# --------------------------------------------------------------------------
# Studio
# --------------------------------------------------------------------------


def _source_path(source: str | tuple[str, str]) -> str:
    """The path of a SkillsMiddleware source: a bare path or a `(path, label)` pair."""
    return source[0] if isinstance(source, tuple) else source


def check_skill_sources(sources: Sequence[str | tuple[str, str]], *, owner: str = "studio") -> None:
    """Raise ValueError for a source that is one skill's folder instead of a skills root.

    SkillsMiddleware keeps only a source's sub-folders that hold a SKILL.md, so
    `/skills/directing/` (or any path under SKILLS_ROUTE other than the root)
    silently loads zero skills. `owner` names the agent in the message.
    """
    for source in sources:
        path = _source_path(source)
        normalized = path.rstrip("/") + "/"
        if normalized != SKILLS_ROUTE and normalized.startswith(SKILLS_ROUTE):
            msg = (
                f"{owner}: skills source {path!r} is a single skill folder; a source must be a skills root "
                f"such as {SKILLS_ROUTE!r} (SkillsMiddleware loads the sub-folders of a source that hold a "
                f"SKILL.md, so a single skill folder loads nothing)"
            )
            raise ValueError(msg)


def _prepare_subagent(spec: SubAgent, interrupt_on: Mapping[str, Any]) -> SubAgent:
    """Fill in what a sub-agent must share with the studio.

    - interrupt_on: deepagents already passes the studio's config to a sub-agent
      that sets none; one that sets its own gets the studio's gates merged under
      it, so `ask_teller` / `confirm_props` always pause wherever they are called.
    - skills: declarative sub-agents do not inherit skills, so one that names no
      sources gets the studio's SKILL_SOURCES. `build_subagents` already gives
      every sub-agent SKILL_SOURCES: each sees all three skills by name and
      description, and its prompt names the ones it must follow
      (`subagents.SUBAGENT_SKILLS`). A source that is a single skill folder
      (`/skills/directing/`) loads nothing and raises ValueError.
    - permissions: a sub-agent's own rules would replace the studio's, so the
      `/skills/` write ban is appended to them.
    """
    prepared: dict[str, Any] = dict(spec)
    if "interrupt_on" in prepared:
        prepared["interrupt_on"] = {**interrupt_on, **prepared["interrupt_on"]}
    if not prepared.get("skills") and prepared.get("mode") != "fork":
        prepared["skills"] = list(SKILL_SOURCES)
    check_skill_sources(prepared.get("skills") or [], owner=f"sub-agent {prepared.get('name')!r}")
    if "permissions" in prepared:
        prepared["permissions"] = [*prepared["permissions"], SKILLS_READ_ONLY]
    return prepared  # type: ignore[return-value]


def build_studio(
    *,
    family_id: str,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    subagents: Sequence[SubAgent],
    interrupt_on: Mapping[str, Any],
    checkpointer: Checkpointer,
    store: BaseStore,
    system_prompt: str,
    skills_dir: Path = SKILLS_DIR,
) -> CompiledStateGraph:
    """The studio graph for one family: run it with `thread_config(run_id)`.

    `interrupt_on` maps tool names (ask_teller, merge_people, confirm_props) to
    `True` or an InterruptOnConfig; the run pauses before those tools both in
    the studio and inside every sub-agent, and resumes with
    `Command(resume={"decisions": [...]})` on the same thread.
    """
    gates = dict(interrupt_on)
    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt,
        # deepagents 0.7 no longer adds the planning tool; the studio's plan
        # (write_todos -> state["todos"]) is what streams to the UI as `plan`.
        middleware=[TodoListMiddleware()],
        subagents=[_prepare_subagent(s, gates) for s in subagents],
        skills=list(SKILL_SOURCES),
        permissions=[SKILLS_READ_ONLY],
        backend=make_backend(family_id=family_id, skills_dir=skills_dir),
        interrupt_on=gates or None,
        checkpointer=checkpointer,
        store=store,
        name="studio",
    )


def thread_config(run_id: str) -> dict[str, Any]:
    """Run config for one studio run: its thread (resume with the same one) and a step limit
    (`RunLimits.max_graph_steps`, env STITCH_MAX_GRAPH_STEPS, default RECURSION_LIMIT)."""
    return {"configurable": {"thread_id": run_id}, "recursion_limit": RunLimits.from_env().max_graph_steps}


# --------------------------------------------------------------------------
# Production persistence
# --------------------------------------------------------------------------


def mongo_persistence(uri: str | None = None, db_name: str = "stitch") -> tuple[MongoDBSaver, MongoDBStore]:
    """Checkpointer and long-term store on MongoDB (env MONGODB_URI unless `uri` is given).

    Checkpoints go to `agent_checkpoints` / `agent_checkpoint_writes`, the
    long-term store to `agent_store`. One MongoClient backs both; it lives as
    long as the process.
    """
    from langgraph.checkpoint.mongodb import MongoDBSaver
    from langgraph.store.mongodb import MongoDBStore
    from pymongo import MongoClient

    uri = uri or os.environ.get("MONGODB_URI")
    if not uri:
        msg = "MONGODB_URI is not set"
        raise RuntimeError(msg)
    client: MongoClient = MongoClient(uri, appname="stitch-studio")
    saver = MongoDBSaver(
        client,
        db_name=db_name,
        checkpoint_collection_name="agent_checkpoints",
        writes_collection_name="agent_checkpoint_writes",
    )
    store = MongoDBStore(collection=client[db_name]["agent_store"])
    return saver, store
