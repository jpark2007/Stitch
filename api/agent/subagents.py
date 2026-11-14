"""The studio prompt and the eight sub-agent specs (ARCHITECTURE.md section 2).

interviewer, storyboarder, prop_master, set_designer, director, narrator, checker, linker.

Each spec is a `deepagents.middleware.subagents.SubAgent` dict. Prompts live in
`agent/prompts/<name>.md`. Tools are passed in by name, so the studio decides which
real or fake implementations a run uses; this module only says who gets what.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from deepagents.middleware.subagents import SubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

SUBAGENT_NAMES: tuple[str, ...] = (
    "interviewer",
    "storyboarder",
    "prop_master",
    "set_designer",
    "director",
    "narrator",
    "checker",
    "linker",
)

PROMPT_NAMES: tuple[str, ...] = ("studio", *SUBAGENT_NAMES)


def load_prompt(name: str) -> str:
    """Return the text of `agent/prompts/<name>.md`.

    Raises ValueError for a name that is not the studio or one of the eight sub-agents.
    """
    if name not in PROMPT_NAMES:
        raise ValueError(f"unknown prompt {name!r}; expected one of {', '.join(PROMPT_NAMES)}")
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


STUDIO_PROMPT: str = load_prompt("studio")

SUBAGENT_TOOLS: dict[str, list[str]] = {
    "interviewer": ["get_person", "find_memories", "ask_teller", "merge_people"],
    "storyboarder": ["get_person", "find_memories", "describe_photo"],
    "prop_master": ["find_prop", "generate_prop", "cut_out", "confirm_props", "describe_photo"],
    "set_designer": ["find_set", "generate_set_layer", "describe_photo"],
    "director": ["get_person", "compile_screenplay"],
    "narrator": ["get_person", "synthesize_narration"],
    "checker": ["render_frames", "inspect_frames"],
    "linker": ["link_participants", "find_memories"],
}

# Tools a sub-agent cannot work without: building it without them is an error, not a warning.
REQUIRED_TOOLS: dict[str, list[str]] = {
    "director": ["compile_screenplay"],
}

SKILLS_ROOT = "/skills/"


def _skill(name: str) -> str:
    return f"{SKILLS_ROOT}{name}/"


SUBAGENT_SKILLS: dict[str, list[str]] = {
    "interviewer": [],
    "storyboarder": [_skill("storyboarding")],
    "prop_master": [_skill("popup-style")],
    "set_designer": [_skill("popup-style")],
    "director": [_skill("directing")],
    "narrator": [],
    "checker": [_skill("directing"), _skill("popup-style")],
    "linker": [],
}

SUBAGENT_DESCRIPTIONS: dict[str, str] = {
    "interviewer": (
        "Checks that a story names a place, the people and one concrete action; asks the teller at most "
        "two follow-up questions and confirms person merges. Returns the stageable facts."
    ),
    "storyboarder": (
        "Turns the story and photos into /shotlist.md: one to four shots, each with its place, cast, "
        "visible beat and source sentences."
    ),
    "prop_master": (
        "Finds or generates every prop in /shotlist.md with a size class and grip, then has the family "
        "confirm them once. Returns the prop ids."
    ),
    "set_designer": (
        "Reuses or generates a back, middle and front paper set for each shot in /shotlist.md, with "
        "named marks. Returns the set ids and marks."
    ),
    "director": (
        "Writes /screenplay.json (actions, camera, pageText, explanations, pull tabs), compiles it and "
        "fixes the named errors, at most 3 rounds. Also fixes checker critiques."
    ),
    "narrator": (
        "Writes the teller's narration in their own words with sources and cues into /screenplay.json, "
        "then synthesizes speech with word timings. Handles second tellings."
    ),
    "checker": (
        "Renders the compiled scene at key moments, critiques the frames and the narration into "
        "/critique.md, and returns pass or the exact fixes."
    ),
    "linker": (
        "Adds a saved moment to every participant's book and writes one question per other "
        "participant asking for their version."
    ),
}


def build_subagents(
    tools: Mapping[str, BaseTool],
    *,
    model: BaseChatModel | str | None = None,
) -> list[SubAgent]:
    """Build the eight sub-agent specs, in pipeline order.

    `tools` maps tool name to tool. Each sub-agent gets the tools listed for it in
    SUBAGENT_TOOLS that are present in `tools`; a missing tool is skipped with a
    warning, except a REQUIRED_TOOLS entry, which raises ValueError. `model`, when
    given, is set on every spec; otherwise each sub-agent uses the studio's model.
    """
    specs: list[SubAgent] = []
    for name in SUBAGENT_NAMES:
        missing = [t for t in SUBAGENT_TOOLS[name] if t not in tools]
        required_missing = [t for t in missing if t in REQUIRED_TOOLS.get(name, [])]
        if required_missing:
            raise ValueError(f"sub-agent {name!r} needs tool(s) {', '.join(required_missing)}, which were not provided")
        for tool_name in missing:
            logger.warning("sub-agent %s: tool %s is not available yet; skipping it", name, tool_name)

        spec: SubAgent = {
            "name": name,
            "description": SUBAGENT_DESCRIPTIONS[name],
            "system_prompt": load_prompt(name),
            "tools": [tools[t] for t in SUBAGENT_TOOLS[name] if t in tools],
        }
        if SUBAGENT_SKILLS[name]:
            spec["skills"] = list(SUBAGENT_SKILLS[name])
        if model is not None:
            spec["model"] = model
        specs.append(spec)
    return specs


__all__ = [
    "PROMPTS_DIR",
    "PROMPT_NAMES",
    "REQUIRED_TOOLS",
    "SKILLS_ROOT",
    "STUDIO_PROMPT",
    "SUBAGENT_DESCRIPTIONS",
    "SUBAGENT_NAMES",
    "SUBAGENT_SKILLS",
    "SUBAGENT_TOOLS",
    "build_subagents",
    "load_prompt",
]
