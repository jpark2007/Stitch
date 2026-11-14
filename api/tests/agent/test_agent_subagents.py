"""The studio prompt, the eight sub-agent prompts, and build_subagents."""

from __future__ import annotations

import logging
import re
import typing

import pytest
from deepagents.middleware.subagents import SubAgent
from langchain_core.tools import BaseTool, tool

from agent import subagents
from agent.subagents import (
    PROMPTS_DIR,
    SKILLS_ROOT,
    STUDIO_PROMPT,
    SUBAGENT_SKILLS,
    SUBAGENT_TOOLS,
    build_subagents,
    load_prompt,
)

ORDER = ["interviewer", "storyboarder", "prop_master", "set_designer", "director", "narrator", "checker", "linker"]

FIXED_TOOLS = [
    "get_person",
    "find_memories",
    "save_memory",
    "link_participants",
    "describe_photo",
    "find_prop",
    "generate_prop",
    "confirm_props",
    "find_set",
    "generate_set_layer",
    "cut_out",
    "compile_screenplay",
    "render_frames",
    "inspect_frames",
    "synthesize_narration",
    "ask_teller",
    "merge_people",
]

# deepagents built-ins every agent has (filesystem, planning, delegation).
BUILTINS = {"write_file", "read_file", "edit_file", "ls", "glob", "grep", "write_todos", "task"}

SKILL_FOLDERS = {"popup-style", "storyboarding", "directing"}


def _dummy_tool(name: str) -> BaseTool:
    def run(query: str = "") -> str:
        return f"{name}: {query}"

    run.__name__ = name
    run.__doc__ = f"Dummy {name}."
    return tool(name)(run)


@pytest.fixture
def all_tools() -> dict[str, BaseTool]:
    return {name: _dummy_tool(name) for name in FIXED_TOOLS}


def _tool_names(spec: SubAgent) -> list[str]:
    return [t.name for t in spec["tools"]]


def _mentioned(text: str, names: typing.Iterable[str]) -> set[str]:
    return {n for n in names if re.search(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", text)}


# ------------------------------------------------------------------ build_subagents


def test_eight_specs_in_order_with_exact_tools(all_tools):
    specs = build_subagents(all_tools)
    assert [s["name"] for s in specs] == ORDER
    for spec in specs:
        name = spec["name"]
        assert spec["name"].strip()
        assert spec["description"].strip()
        assert "\n" not in spec["description"], f"{name}: description should be one line"
        assert spec["system_prompt"].strip()
        assert spec["system_prompt"] == load_prompt(name)
        assert _tool_names(spec) == SUBAGENT_TOOLS[name]
        assert all(t is all_tools[t.name] for t in spec["tools"])
        assert "model" not in spec


def test_specs_only_use_subagent_keys(all_tools):
    allowed = set(typing.get_type_hints(SubAgent, include_extras=True)) | set(SubAgent.__annotations__)
    for spec in build_subagents(all_tools, model="anthropic:claude-test"):
        assert set(spec) <= allowed, f"{spec['name']}: unsupported keys {set(spec) - allowed}"


def test_tools_are_always_declared_so_subagents_never_inherit_the_studio_tools(all_tools):
    # deepagents gives a spec without "tools" every tool the main agent has.
    for spec in build_subagents({"compile_screenplay": all_tools["compile_screenplay"]}):
        assert "tools" in spec


def test_model_is_set_when_given(all_tools):
    specs = build_subagents(all_tools, model="anthropic:claude-test")
    assert all(s["model"] == "anthropic:claude-test" for s in specs)


def test_skills_are_source_paths_under_skills_root(all_tools):
    assert SKILLS_ROOT == "/skills/"
    assert SUBAGENT_SKILLS["storyboarder"] == ["/skills/storyboarding/"]
    assert SUBAGENT_SKILLS["prop_master"] == ["/skills/popup-style/"]
    assert SUBAGENT_SKILLS["set_designer"] == ["/skills/popup-style/"]
    assert SUBAGENT_SKILLS["director"] == ["/skills/directing/"]
    assert sorted(SUBAGENT_SKILLS["checker"]) == ["/skills/directing/", "/skills/popup-style/"]
    for spec in build_subagents(all_tools):
        expected = SUBAGENT_SKILLS[spec["name"]]
        assert spec.get("skills", []) == expected
        for path in expected:
            folder = path.removeprefix(SKILLS_ROOT).strip("/")
            assert folder in SKILL_FOLDERS
            assert (PROMPTS_DIR.parent / "skills" / folder).is_dir()


def test_missing_optional_tools_are_skipped_with_a_warning(all_tools, caplog):
    tools = {k: v for k, v in all_tools.items() if k not in {"ask_teller", "render_frames", "cut_out"}}
    with caplog.at_level(logging.WARNING, logger=subagents.__name__):
        specs = {s["name"]: s for s in build_subagents(tools)}
    assert _tool_names(specs["interviewer"]) == ["get_person", "find_memories", "merge_people"]
    assert _tool_names(specs["checker"]) == ["inspect_frames"]
    assert _tool_names(specs["prop_master"]) == ["find_prop", "generate_prop", "confirm_props", "describe_photo"]
    assert _tool_names(specs["set_designer"]) == ["find_set", "generate_set_layer", "describe_photo"]
    assert _tool_names(specs["director"]) == ["get_person", "compile_screenplay"]
    warned = caplog.text
    for name in ("ask_teller", "render_frames", "cut_out"):
        assert name in warned


def test_only_compile_screenplay_is_needed(all_tools):
    specs = build_subagents({"compile_screenplay": all_tools["compile_screenplay"]})
    assert len(specs) == 8
    assert _tool_names(specs[ORDER.index("director")]) == ["compile_screenplay"]


def test_missing_compile_screenplay_raises(all_tools):
    tools = {k: v for k, v in all_tools.items() if k != "compile_screenplay"}
    with pytest.raises(ValueError, match="compile_screenplay"):
        build_subagents(tools)


def test_subagent_tools_only_use_fixed_names():
    assert list(SUBAGENT_TOOLS) == ORDER
    for name, tool_names in SUBAGENT_TOOLS.items():
        assert set(tool_names) <= set(FIXED_TOOLS), name
        assert len(tool_names) == len(set(tool_names)), name


def test_specs_build_into_a_deep_agent(all_tools):
    """The specs are accepted by create_deep_agent and the graph runs with a fake model."""
    from deepagents import create_deep_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    class FakeModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):  # noqa: ARG002 - the fake ignores tools
            return self

    model = FakeModel(messages=iter([AIMessage(content="planned")]))
    agent = create_deep_agent(
        model=model,
        tools=list(all_tools.values()),
        system_prompt=STUDIO_PROMPT,
        subagents=build_subagents(all_tools),
    )
    result = agent.invoke({"messages": [{"role": "user", "content": "A story."}]})
    assert result["messages"][-1].content == "planned"


# ------------------------------------------------------------------ prompts


@pytest.mark.parametrize("name", ["studio", *ORDER])
def test_prompt_exists_and_is_not_empty(name):
    path = PROMPTS_DIR / f"{name}.md"
    assert path.is_file()
    assert load_prompt(name).strip()


def test_studio_prompt_constant():
    assert STUDIO_PROMPT == load_prompt("studio")


@pytest.mark.parametrize("bad", ["nope", "../studio", "studio.md", ""])
def test_load_prompt_rejects_unknown_names(bad):
    with pytest.raises(ValueError):
        load_prompt(bad)


@pytest.mark.parametrize("name", ORDER)
def test_prompt_mentions_only_its_own_tools(name):
    mentioned = _mentioned(load_prompt(name), FIXED_TOOLS)
    assert mentioned <= set(SUBAGENT_TOOLS[name]) | BUILTINS, f"{name}.md mentions {mentioned - set(SUBAGENT_TOOLS[name])}"


@pytest.mark.parametrize("name", ORDER)
def test_prompt_mentions_the_tools_it_has(name):
    """A tool the prompt never names is a tool the model has no instructions for."""
    assert _mentioned(load_prompt(name), SUBAGENT_TOOLS[name]) == set(SUBAGENT_TOOLS[name])


def test_studio_prompt():
    text = STUDIO_PROMPT
    # The studio calls compile_screenplay and save_memory itself and must order the voice
    # pass; every other tool belongs to a sub-agent and is reached through `task`.
    assert _mentioned(text, FIXED_TOOLS) <= {"compile_screenplay", "save_memory", "synthesize_narration"}
    assert {"write_todos", "task", "save_memory"} <= _mentioned(text, BUILTINS | set(FIXED_TOOLS))
    assert text.index("write_todos") < text.index("**interviewer**")
    order = ["**interviewer**", "**storyboarder**", "**prop_master**", "**set_designer**", "**director**",
             "**narrator**", "**checker**", "`save_memory`", "**linker**"]
    positions = [text.index(item) for item in order]
    assert positions == sorted(positions), "studio.md must delegate in pipeline order"
    lowered = text.lower()
    assert "never invent" in lowered
    assert "everything the model writes is data" in lowered
    assert "second telling" in lowered
    assert "memoryid" in lowered
    for draft in ("/shotlist.md", "/screenplay.json", "/compile_errors.txt", "/critique.md", "/open_questions.md"):
        assert draft in text
    assert "/memories/people/<personId>.md" in text
    assert "/memories/style.md" in text


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n#", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


@pytest.mark.parametrize("heading", ["### New memory", "### Second telling"])
def test_studio_voices_after_the_last_compile_and_before_saving(heading):
    """A compile rebuilds /timeline.json without audio, so the voice pass must follow the last one."""
    steps = _section(STUDIO_PROMPT, heading)
    voice = steps.index("voice pass")
    assert steps.index("write pass") < steps.index("`compile_screenplay`")
    assert steps.rindex("`compile_screenplay`") < voice < steps.index("`save_memory`")


def test_studio_never_compiles_after_the_voice_without_voicing_again():
    lowered = STUDIO_PROMPT.lower()
    assert "only `compile_screenplay` writes `/timeline.json`" in lowered
    assert "run the voice pass again before saving" in lowered


def test_studio_second_telling_starts_from_the_existing_scene():
    mode = _section(STUDIO_PROMPT, "## Run context")
    assert "already in your files" in mode
    assert "`/screenplay.json`" in mode and "`/timeline.json`" in mode
    assert "never rebuild it yourself" in mode
    steps = _section(STUDIO_PROMPT, "### Second telling")
    assert steps.index("Read `/screenplay.json`") < steps.index("**narrator**")


def test_interviewer_prompt():
    text = load_prompt("interviewer")
    lowered = text.lower()
    assert "place" in lowered and "who" in lowered and "action" in lowered
    assert "at most two" in lowered
    assert text.index("merge_people") < text.index("ask_teller` with")
    assert "/open_questions.md" in text


def test_storyboarder_prompt():
    text = load_prompt("storyboarder")
    assert "/shotlist.md" in text
    assert "telling:<authorId>:s<n>" in text


def test_prop_master_prompt():
    text = load_prompt("prop_master")
    lowered = text.lower()
    assert "/shotlist.md" in text
    assert text.index("find_prop") < text.index("generate_prop")
    assert "size class" in lowered and "grip hint" in lowered
    assert "a single time" in lowered and "every prop" in lowered
    for size in ("tiny", "hand", "carried", "furniture", "vehicle", "building"):
        assert f"`{size}`" in text


def test_set_designer_prompt():
    text = load_prompt("set_designer")
    lowered = text.lower()
    assert "/shotlist.md" in text
    assert "back" in lowered and "middle" in lowered and "front" in lowered
    assert "marks" in lowered
    assert text.index("find_set") < text.index("generate_set_layer")
    # make_layer already removes the background of middle and front layers.
    assert "cut_out" not in text


def test_set_designer_tools_have_no_cut_out_but_the_prop_master_keeps_it():
    assert SUBAGENT_TOOLS["set_designer"] == ["find_set", "generate_set_layer", "describe_photo"]
    assert "cut_out" in SUBAGENT_TOOLS["prop_master"]


def test_director_prompt():
    text = load_prompt("director")
    lowered = text.lower()
    for word in ("pageText", "explanations", "interactions", "compile_screenplay", "/screenplay.json"):
        assert word in text
    assert "round 3" in lowered
    assert "/compile_errors.txt" in text
    assert "exactly the errors named" in lowered
    assert "second-telling" in lowered
    assert "puppet:<personId>" in text and "prop:<propId>" in text
    for drive in ("scrub", "prop_state", "prop_part", "pull_tab", "slider"):
        assert drive in text
    # InteractionDrive needs its `type` key and Interaction.page is "stage" or "story".
    for style in ("scrub", "prop_state", "prop_part"):
        assert f'"type": "{style}"' in text
    assert '`"stage"`' in text and '`"story"`' in text
    assert "keep every narration line" in lowered


def test_narrator_prompt():
    text = load_prompt("narrator")
    lowered = text.lower()
    assert "telling:<authorId>:s<n>" in text
    assert "second telling" in lowered
    assert "narrations[<authorId>]" in text
    assert "at most one action per line" in lowered
    assert "synthesize_narration" in text
    assert "never add a detail" in lowered
    # synthesize_narration reads the compiled /timeline.json, so only the voice pass calls it.
    assert "**write pass**" in lowered and "**voice pass**" in lowered
    assert text.index("**Write pass**") < text.index("**Voice pass**")
    assert text.index("Voice pass only: call `synthesize_narration`") > text.index("5. ")


def test_checker_prompt():
    text = load_prompt("checker")
    lowered = text.lower()
    assert "/critique.md" in text
    assert "at most 2 fix rounds" in lowered
    assert "reject any line" in lowered
    assert text.index("render_frames") < text.index("inspect_frames")


def test_linker_prompt():
    text = load_prompt("linker")
    assert "Dad says you met on the church steps. What do you remember?" in text
    lowered = text.lower()
    assert "one question per person" in lowered
    # link_participants stores the question, so each person's question must be passed to it.
    assert "once per person you ask" in lowered
    assert "`question` set to that person's question" in text
    assert text.index("write one question") < text.index("Call `link_participants`")


@pytest.mark.parametrize("name", ["studio", *ORDER])
def test_prompts_treat_input_as_data(name):
    assert "data" in load_prompt(name).lower()


@pytest.mark.parametrize("name", ORDER)
def test_prompts_say_what_they_return(name):
    assert "## What you return" in load_prompt(name)
