"""Sub-agents that are told to follow a skill can actually see it.

deepagents' SkillsMiddleware treats each source as a skills ROOT: it lists the source's
sub-folders and keeps the ones that hold a SKILL.md. A source that is one skill's own
folder (`/skills/directing/`) has no such sub-folders, so it loads nothing. These tests
load every real sub-agent spec exactly the way the middleware does, run the fake studio
end to end and read the system prompts each agent was really sent, and pin the guard
that rejects a single-skill-folder source.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from deepagents.middleware.skills import SkillsMiddleware
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

import schemas
from agent import demo, studio, wiring
from agent.fake_llm import ScriptedChatModel, system_prompt_of
from agent.fake_studio import fake_studio_model, role_of
from agent.repo import InMemoryRepo, load_sample_family
from agent.subagents import SKILLS_ROOT, STUDIO_PROMPT, SUBAGENT_NAMES, SUBAGENT_SKILLS, SUBAGENT_TOOLS, build_subagents

ALL_SKILLS = {p.name for p in studio.SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file()}


def skill_md(name: str) -> str:
    """The path the middleware tells the model to read for skill `name`."""
    return f"{SKILLS_ROOT}{name}/SKILL.md"


def _dummy_tool(name: str) -> BaseTool:
    def run(query: str = "") -> str:
        return f"{name}: {query}"

    run.__name__ = name
    run.__doc__ = f"Dummy {name}."
    return tool(name)(run)


def all_tools() -> dict[str, BaseTool]:
    return {name: _dummy_tool(name) for names in SUBAGENT_TOOLS.values() for name in names}


def prepared_specs() -> dict[str, Any]:
    """The sub-agent specs exactly as build_studio hands them to create_deep_agent."""
    gates = {"ask_teller": True, "merge_people": True, "confirm_props": True}
    return {s["name"]: studio._prepare_subagent(s, gates) for s in build_subagents(all_tools())}


def load_skills(sources: list[Any]) -> dict[str, Any]:
    """Run SkillsMiddleware.before_agent(state, runtime, config) the way the graph does."""
    middleware = SkillsMiddleware(backend=studio.make_backend(family_id="fam1"), sources=sources)
    update = middleware.before_agent({}, None, {})  # type: ignore[arg-type]
    assert update is not None
    return dict(update)


# --------------------------------------------------------------------------- the specs load


def test_the_skill_map_names_real_skills() -> None:
    assert set(SUBAGENT_SKILLS) == set(SUBAGENT_NAMES)
    assert ALL_SKILLS == {"directing", "popup-style", "storyboarding"}
    for name, skills in SUBAGENT_SKILLS.items():
        assert set(skills) <= ALL_SKILLS, name
    assert SUBAGENT_SKILLS["director"] == ["directing"]
    assert SUBAGENT_SKILLS["storyboarder"] == ["storyboarding"]
    assert sorted(SUBAGENT_SKILLS["checker"]) == ["directing", "popup-style"]


@pytest.mark.parametrize("name", SUBAGENT_NAMES)
def test_every_subagent_loads_the_skills_it_is_told_to_follow(name: str) -> None:
    spec = prepared_specs()[name]
    loaded = load_skills(spec["skills"])

    assert loaded["skills_load_errors"] == []
    found = {s["name"]: s for s in loaded["skills_metadata"]}
    assert set(SUBAGENT_SKILLS[name]) <= set(found), f"{name} sees {sorted(found)}"
    # Every sub-agent sees the whole (small) catalogue: names and descriptions only.
    assert set(found) == ALL_SKILLS
    for skill in SUBAGENT_SKILLS[name]:
        assert found[skill]["path"] == skill_md(skill)


def test_every_subagent_source_is_the_skills_root() -> None:
    for name, spec in prepared_specs().items():
        assert spec["skills"] == studio.SKILL_SOURCES, name


# --------------------------------------------------------------------------- the prompts sent


def test_fake_studio_run_sends_each_agent_its_skill_paths() -> None:
    """As `agent.demo --fake --auto` does: the real graph on fake services, the fake brain."""
    family_id = demo.FAKE_FAMILY_ID
    repo = InMemoryRepo()
    load_sample_family(repo, family_id)
    model = fake_studio_model()
    manager = wiring.make_manager(wiring.fake_services(model=model, repo=repo))
    fields = schemas.SubmitMemoryFields(
        text="I was carrying far too many hymn books on the church steps. He handed them back.",
        year=1988,
        participant_ids=["dad"],
    )

    result = demo.run_demo(
        manager,
        family_id=family_id,
        user_id="user_mom",
        person_id="mom",
        fields=fields,
        photo_ids=[],
        answer=demo.auto_answer,
        out=io.StringIO(),
        timeout=60,
    )
    assert result.status == "done", result

    prompts: dict[str, set[str]] = {}
    for _, messages in model.calls:
        prompts.setdefault(role_of(messages), set()).add(system_prompt_of(messages))

    director = next(iter(prompts["director"]))
    storyboarder = next(iter(prompts["storyboarder"]))
    checker = next(iter(prompts["checker"]))
    assert director.startswith("# Director")
    assert storyboarder.startswith("# Storyboarder")
    assert checker.startswith("# Checker")
    assert skill_md("directing") in director
    assert skill_md("storyboarding") in storyboarder
    assert skill_md("directing") in checker
    assert skill_md("popup-style") in checker

    # And in general: every agent that ran was sent the path of every skill it must follow.
    for role, sent in prompts.items():
        for prompt in sent:
            assert "No skills available" not in prompt, role
            for skill in SUBAGENT_SKILLS.get(role, []):
                assert skill_md(skill) in prompt, (role, skill)


# --------------------------------------------------------------------------- the guard


@pytest.mark.parametrize(
    "source",
    ["/skills/directing/", "/skills/directing", "/skills/popup-style/", ("/skills/directing/", "Directing")],
)
def test_a_single_skill_folder_source_is_rejected(source: Any) -> None:
    spec = {**prepared_specs()["director"], "skills": [source]}
    with pytest.raises(ValueError, match="single skill folder"):
        studio._prepare_subagent(spec, {})


def test_build_studio_rejects_a_subagent_with_a_single_skill_folder_source() -> None:
    bad = {**prepared_specs()["director"], "skills": ["/skills/directing/"]}
    with pytest.raises(ValueError, match=r"director.*/skills/directing/.*skills root"):
        studio.build_studio(
            family_id="fam1",
            model=ScriptedChatModel(responder=lambda _m, _n: None),  # type: ignore[arg-type,return-value]
            tools=[],
            subagents=[bad],
            interrupt_on={},
            checkpointer=InMemorySaver(),
            store=InMemoryStore(),
            system_prompt=STUDIO_PROMPT,
        )


def test_skills_roots_are_accepted() -> None:
    for sources in (["/skills/"], ["/skills"], [("/skills/", "Studio")], ["/memories/skills/"]):
        assert studio.check_skill_sources(sources) is None
