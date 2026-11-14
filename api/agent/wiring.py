"""Wiring: one run's tools and studio graph, built from a bundle of services (ARCHITECTURE.md section 2).

`Services` holds every outside dependency a run needs: the family's data (`AgentRepo`),
the chat model, persistence, art, speech, vision, frame rendering and the compiler.
`build_run_tools` binds every tool to one run's family, teller and photos;
`build_run_graph` assembles the studio from them with `studio.build_studio`;
`make_manager` gives `agent.runs` a `RunManager` that builds one studio per run.

`fake_services` is the offline bundle (InMemoryRepo, in-memory persistence, placeholder
art, FakeTTS, FakeVision, the Pillow frame diagram, the deterministic compiler) used by
tests; `agent.production.production_services` is the real one.

The manager keeps its run records in `services.store` (the studio's long-term store), so
with a persistent store and checkpointer (MongoDB in production) runs survive a restart.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer

from agent import studio
from agent.repo import AgentRepo, InMemoryRepo
from agent.run_info import RunInfo
from agent.runs import RunManager
from agent.subagents import STUDIO_PROMPT, build_subagents
from agent.tools import ask, narration
from agent.tools.art import ArtService, make_cut_out_tool
from agent.tools.compile import Compiler, SceneInputs, SceneLookupError, load_compiler, make_compile_tool
from agent.tools.family import make_family_tools
from agent.tools.frames import FakeFrameCritic, FakeFrameRenderer, FrameRenderer, make_frame_tools
from agent.tools.narration import TTS, FakeTTS, make_narration_tool
from agent.tools.photos import FakeVision, VisionModel, make_photo_tools
from agent.tools.props import make_prop_tools
from agent.tools.sets import make_set_tools
from screenplay.schema import Screenplay

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)

STUDIO_TOOL_NAMES: tuple[str, ...] = (
    "get_person",
    "find_memories",
    "save_memory",
    "link_participants",
    "describe_photo",
    "find_prop",
    "generate_prop",
    "find_set",
    "generate_set_layer",
    "cut_out",
    "compile_screenplay",
    "render_frames",
    "inspect_frames",
    "synthesize_narration",
    "ask_teller",
)
"""The studio's own tools (ARCHITECTURE.md section 2). Sub-agents get theirs from
`subagents.SUBAGENT_TOOLS`, which also hands out merge_people and confirm_props."""


@dataclass(frozen=True)
class Services:
    """Everything outside the agent that one run uses. Shared by all runs of a process."""

    repo: AgentRepo
    model: BaseChatModel
    checkpointer: Checkpointer
    store: BaseStore
    art: ArtService
    tts: TTS
    vision: VisionModel  # describe_photo
    critic: VisionModel | None  # inspect_frames
    renderer: FrameRenderer | None  # render_frames; None leaves render_frames and inspect_frames out
    compiler: Compiler
    subagent_model: BaseChatModel | None = None
    labels: Mapping[str, str] = field(default_factory=dict)
    """What each service is, for logs: e.g. {'art': 'placeholder', 'tts': 'fake', 'renderer': 'diagram'}."""


# --------------------------------------------------------------------------- scene lookup


def scene_for(repo: AgentRepo, family_id: str) -> Callable[[Screenplay], SceneInputs]:
    """The compiler's scene lookup bound to one family.

    Loads every `screenplay.cast` person, every `screenplay.props` prop and each shot's
    set from `repo`, only in `family_id`. The first id that is missing raises
    `SceneLookupError(kind, id)` with kind 'person', 'prop' or 'set', which the compile
    tool turns into an exact compile error for the director.
    """

    def lookup(screenplay: Screenplay) -> SceneInputs:
        scene = SceneInputs()
        for person_id in screenplay.cast:
            if person_id in scene.people:
                continue
            person = repo.get_person(family_id, person_id)
            if person is None:
                raise SceneLookupError("person", person_id)
            scene.people[person_id] = person
        for prop_id in screenplay.props:
            if prop_id in scene.props:
                continue
            prop = repo.get_prop(family_id, prop_id)
            if prop is None:
                raise SceneLookupError("prop", prop_id)
            scene.props[prop_id] = prop
        for shot in screenplay.shots:
            if shot.set in scene.sets:
                continue
            set_def = repo.get_set(family_id, shot.set)
            if set_def is None:
                raise SceneLookupError("set", shot.set)
            scene.sets[shot.set] = set_def
        return scene

    return lookup


# --------------------------------------------------------------------------- tools


def build_run_tools(run: RunInfo, services: Services) -> dict[str, BaseTool]:
    """Every tool for one run, by name, each bound to `run.family_id` (never to an id the model gives).

    describe_photo only reads `run.photo_ids`; media the tools write (cutouts, narration
    audio) goes into the run's family. render_frames and inspect_frames are present when
    both a renderer and a critic are configured.
    """
    repo = services.repo
    family_id = run.family_id

    def get_media(media_id: str) -> tuple[bytes, str] | None:
        return repo.get_media(family_id, media_id)

    def put_cutout(data: bytes, content_type: str) -> str:
        return repo.put_media(family_id, data, content_type, kind="cutout")

    def put_audio(data: bytes, content_type: str) -> str:
        return repo.put_media(family_id, data, content_type, kind="audio")

    lookup = scene_for(repo, family_id)
    teller = repo.get_person(family_id, run.author_id)

    built: list[BaseTool] = [
        *make_family_tools(repo, run),
        ask.ask_teller,
        ask.merge_people,
        *make_photo_tools(services.vision, get_media, run.photo_ids),
        *make_prop_tools(repo, run, services.art),
        *make_set_tools(repo, run, services.art),
        make_cut_out_tool(services.art, put_cutout, get_media),
        make_compile_tool(services.compiler, lookup),
        make_narration_tool(
            services.tts,
            put_audio,
            author_id=run.author_id,
            voice=narration.voice_for(teller),
            second_telling=run.is_second_telling,
        ),
    ]

    if services.renderer is not None and services.critic is not None:
        built.extend(make_frame_tools(repo, run, services.renderer, services.critic, lookup))

    tools: dict[str, BaseTool] = {}
    for tool in built:
        if tool.name in tools:
            raise ValueError(f"two tools are named {tool.name!r}")
        tools[tool.name] = tool
    return tools


# --------------------------------------------------------------------------- graph


def build_run_graph(run: RunInfo, services: Services) -> CompiledStateGraph:
    """The studio for one run: its tools, the eight sub-agents, the ask/merge/props gates."""
    tools = build_run_tools(run, services)
    return studio.build_studio(
        family_id=run.family_id,
        model=services.model,
        tools=[tools[name] for name in STUDIO_TOOL_NAMES if name in tools],
        subagents=build_subagents(tools, model=services.subagent_model),
        interrupt_on=ask.INTERRUPT_ON,
        checkpointer=services.checkpointer,
        store=services.store,
        system_prompt=STUDIO_PROMPT,
    )


def make_manager(services: Services, *, max_workers: int = 4) -> RunManager:
    """A RunManager that builds a fresh studio per run from `services` and keeps its run
    records in `services.store`, so runs outlive the process when the store does."""
    return RunManager(
        repo=services.repo,
        build_graph=lambda run: build_run_graph(run, services),
        max_workers=max_workers,
        store=services.store,
    )


# --------------------------------------------------------------------------- fakes


def compiler_label(compiler: Compiler) -> str:
    """'FakeCompiler' or 'screenplay.compiler', for labels and logs."""
    from agent.fake_compiler import FakeCompiler

    return "FakeCompiler" if isinstance(compiler, FakeCompiler) else "screenplay.compiler"


def fake_services(
    *,
    model: BaseChatModel,
    repo: AgentRepo | None = None,
    art: ArtService | None = None,
) -> Services:
    """An offline bundle: no keys, no network, deterministic.

    InMemoryRepo (unless `repo` is given), InMemorySaver and InMemoryStore, placeholder
    art (`ArtService(None, None)`: props and set layers get null art and a color) unless
    `art` is given, FakeTTS, FakeVision, FakeFrameRenderer (the Pillow diagram) and
    FakeFrameCritic. The compiler is `load_compiler()`: the real, deterministic
    `screenplay.compiler` once it exists, the FakeCompiler until then.
    """
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    art = art if art is not None else ArtService(None, None)
    compiler = load_compiler()
    labels = {
        "model": "fake",
        "repo": "fake",
        "persistence": "fake",
        "art": "placeholder" if art.image_model is None else "fake",
        "tts": "fake",
        "vision": "fake",
        "critic": "fake",
        "renderer": "fake",
        "compiler": "fake" if compiler_label(compiler) == "FakeCompiler" else "screenplay.compiler",
    }
    return Services(
        repo=repo if repo is not None else InMemoryRepo(),
        model=model,
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
        art=art,
        tts=FakeTTS(),
        vision=FakeVision(),
        critic=FakeFrameCritic(),
        renderer=FakeFrameRenderer(),
        compiler=compiler,
        labels=labels,
    )


__all__ = [
    "STUDIO_TOOL_NAMES",
    "Services",
    "build_run_graph",
    "build_run_tools",
    "compiler_label",
    "fake_services",
    "make_manager",
    "scene_for",
]
