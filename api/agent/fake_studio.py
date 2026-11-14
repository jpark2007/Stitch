"""A deterministic multi-agent "model" that drives the real studio end to end on fakes.

`fake_studio_model(story)` is a `ScriptedChatModel` whose responder plays every role of
ARCHITECTURE.md section 2: the studio and its eight sub-agents (interviewer,
storyboarder, prop_master, set_designer, director, narrator, checker, linker). Wire it
into `studio.build_studio` with the real tools (on fake services) and a run goes

    interviewer -> storyboarder -> prop_master + set_designer (one turn)
      -> director (write, compile, fix; at most 3 rounds) -> narrator (write pass)
      -> compile_screenplay -> checker (at most 2 fix rounds) -> narrator (voice pass)
      -> save_memory -> linker

or, for a second telling (run context `memoryId` set): read the shared scene ->
narrator (write pass, `narrations[<authorId>]`) -> compile -> checker -> narrator
(voice) -> save_memory -> linker for participants new to the moment.

How it decides
--------------
The responder is a pure function of (messages, bound tool names) and the frozen
`FakeStory`. Each role finds out who it is from its system prompt (`role_of`), then
replays its own history: every tool call it has made, with its ToolMessage, in order.
The first step of its script that has no result yet is the next reply. Because nothing
is remembered between calls, parallel sub-agents (prop_master and set_designer) and
interrupt resumes (ask_teller, confirm_props) work without any shared state.

Passing data between roles: every task description the studio writes ends with one line
`CONTEXT: <json>`, and every sub-agent's final message ends with `RESULT: <json>`, which
the studio parses from the task ToolMessage.

What a story can change (`FakeStory`, all optional, defaults give the one-shot hymn-books
style scene):

- `place`, `prop`, `prop_size`: the place and the first object.
- `extra_props`: more (name, size) objects. The storyboarder lists every object; the prop
  master finds (or generates) each one and has the family confirm them all in one
  `confirm_props` call.
- `max_cast`: how many people are cast, the teller first and then the participants in the
  order the run lists them (default 2).
- `screenplay`: a hand-written director. When set, the director's valid draft is
  `story.screenplay(stage_ctx)` instead of `build_screenplay(stage_ctx)`; stage_ctx has
  teller, tellerName, text, year, place, cast, names, props ([{id, name, size}]), setId
  and marks. The first `compile_failures` drafts are `bad_screenplay(stage_ctx, build)` of
  that draft.
- `followup`, `compile_failures`, `checker_problems`: the detours (see the fields).

The default director (`build_screenplay`) and the narrator model what the prompts and the
directing skill teach. The director's walk on is unnamed setup and the first named beat
comes FIRST_BEAT_SECONDS or more in, with a close-up landing on it and the pull tab on it.
The narrator narrates every shot (`narration_by_shot`): the teller's sentences are spread
over the shots in order (`shot_sentences`, at least one line per shot), each shot's first
line is cued to that shot's first named action on an early `cueWord` (`early_cue_index`),
started so the word falls where the action already is, and lines never overlap. A second
telling writes the same shape into `narrations[<teller>]`; when the teller is the memory's
default narrator it rewrites each shot's own `narration` instead (`default_teller`). The
prop master gives surface furniture its socket (`surface_sockets`).

If a tool the script wants is not bound for that call, the role skips that step (for
example the checker without `render_frames` / `inspect_frames`). The narrator's voice pass
calls `synthesize_narration` once more when it answers `Error...` (a media store failure),
as the narrator prompt says; a `warning:` (the speech service failed and the teller was
narrated silently) is a success and is not retried.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from agent.fake_llm import Responder, ScriptedChatModel, system_prompt_of, tool_call, tool_calls
from agent.subagents import STUDIO_PROMPT, SUBAGENT_NAMES, load_prompt

SCREENPLAY_PATH = "/screenplay.json"
SHOTLIST_PATH = "/shotlist.md"
CRITIQUE_PATH = "/critique.md"
READ_LIMIT = 10_000
"""Lines asked of read_file, so one read returns the whole draft."""

CONTEXT_PREFIX = "CONTEXT: "
RESULT_PREFIX = "RESULT: "
SAVED = re.compile(r"\bsaved\s+(\S+)\s+v(\d+)\b")
MAX_CHECK_FIX_ROUNDS = 2
MAX_STUDIO_COMPILES = 3
PAGE_TEXT_WORDS = 30
WORD_SECONDS = 0.25  # screenplay.narration.WORD_SECONDS: spoken word spacing without TTS timings
WALK_SPEED = 0.35  # stage units per second (shared/vocabulary.json speeds.walk)
FRAME_TIMES = [1.0, 2.0]
SECOND_TELLING = "second_telling"
NEW_MEMORY = "new_memory"
BAD_MARK = "nowhere"


@dataclass(frozen=True)
class FakeStory:
    """What the fake studio stages, and which detours it takes on the way."""

    place: str = "church steps"
    prop: str = "hymn books"
    prop_size: str = "carried"
    """A size class from agent.tools.props.SIZES (tiny, hand, carried, furniture, vehicle, building)."""
    followup: str | None = None
    """When set, the interviewer asks this once with ask_teller."""
    compile_failures: int = 1
    """The director's first N drafts carry one deliberate unknown mark (`to: "nowhere"`)."""
    checker_problems: int = 0
    """Checker rounds that report a staging problem before passing."""
    extra_props: tuple[tuple[str, str], ...] = ()
    """More (name, size) objects beyond `prop`. The storyboarder lists them all; the prop
    master finds or generates each and confirms them all in one confirm_props call."""
    max_cast: int = 2
    """How many people (the teller first, then participants in order) are cast."""
    screenplay: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    """The director's valid draft: `screenplay(stage_ctx)` instead of `build_screenplay`.

    `stage_ctx` has teller, tellerName, text, year, place, cast, names, props
    ([{id, name, size}] as the family kept them), setId and marks. The first
    `compile_failures` drafts are still the bad version of this one (`bad_screenplay`).
    """

    @property
    def objects(self) -> list[str]:
        """Every object's name, `prop` first."""
        return [self.prop, *(name for name, _ in self.extra_props)]

    def size_of(self, name: str) -> str:
        """The size class of one of `objects` (`prop_size` for `prop`)."""
        for extra, size in self.extra_props:
            if extra == name and name != self.prop:
                return size
        return self.prop_size


# --------------------------------------------------------------------------- roles

_ROLE_PROMPTS: tuple[tuple[str, str], ...] = (
    ("studio", STUDIO_PROMPT.strip()),
    *((name, load_prompt(name).strip()) for name in SUBAGENT_NAMES),
)


def role_of(messages: list[BaseMessage]) -> str:
    """'studio' or one of SUBAGENT_NAMES, from the system prompt's prefix.

    Raises AssertionError when the system prompt starts with none of them.
    """
    prompt = system_prompt_of(messages).lstrip()
    for role, text in _ROLE_PROMPTS:
        if prompt.startswith(text):
            return role
    raise AssertionError(f"fake studio: unknown agent, system prompt starts {prompt[:60]!r}")


# --------------------------------------------------------------------------- replay


class _Next(Exception):  # noqa: N818 - control flow, not an error
    """Raised by a script to return its next reply."""

    def __init__(self, message: AIMessage) -> None:
        super().__init__("next")
        self.message = message


@dataclass
class _Done:
    name: str
    args: dict[str, Any]
    result: str


def _text(message: BaseMessage) -> str:
    text = message.text
    return text if isinstance(text, str) else str(message.content)


class _Script:
    """One agent's history, replayed step by step.

    `do(...)` returns the results of calls already made, in order; the first call that
    has not been made yet ends the replay with that call as the reply (`_Next`).
    `write_todos` calls are kept apart: `do(..., todos=...)` first rewrites the plan when
    the last plan written differs from the one wanted for this step.
    """

    def __init__(self, messages: Sequence[BaseMessage], tool_names: Sequence[str]) -> None:
        self.tools = set(tool_names)
        results = {m.tool_call_id: _text(m) for m in messages if isinstance(m, ToolMessage)}
        self.calls: list[_Done] = []
        self.todos: list[dict[str, str]] | None = None
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            for call in message.tool_calls:
                args = dict(call.get("args") or {})
                if call["name"] == "write_todos":
                    self.todos = args.get("todos")
                    continue
                self.calls.append(_Done(call["name"], args, results.get(call.get("id") or "", "")))
        self.cursor = 0

    def has(self, *names: str) -> bool:
        return all(name in self.tools for name in names)

    def do(self, *calls: tuple[str, dict[str, Any]], todos: list[dict[str, str]] | None = None) -> list[str | None]:
        """Results of `calls` (None for a call whose tool is not bound), or raise _Next."""
        wanted = [(i, name, args) for i, (name, args) in enumerate(calls) if name in self.tools]
        out: list[str | None] = [None] * len(calls)
        if not wanted:
            return out
        made = self.calls[self.cursor : self.cursor + len(wanted)]
        if made:
            if len(made) < len(wanted):
                raise AssertionError(f"fake studio: expected {len(wanted)} calls in one turn, history has {len(made)}")
            for (i, name, args), done in zip(wanted, made, strict=True):
                if done.name != name or done.args.get("subagent_type") != args.get("subagent_type"):
                    raise AssertionError(f"fake studio: history has {done.name} where the script expects {name}")
                out[i] = done.result
            self.cursor += len(wanted)
            return out
        self.plan(todos)
        if len(wanted) == 1:
            raise _Next(tool_call(wanted[0][1], wanted[0][2]))
        raise _Next(tool_calls(*((name, args) for _, name, args in wanted)))

    def one(self, name: str, args: dict[str, Any], *, todos: list[dict[str, str]] | None = None) -> str | None:
        return self.do((name, args), todos=todos)[0]

    def plan(self, todos: list[dict[str, str]] | None) -> None:
        """Write `todos` first if they differ from the last plan written."""
        if todos is not None and "write_todos" in self.tools and self.todos != todos:
            raise _Next(tool_call("write_todos", {"todos": todos}))

    def finish(self, text: str, *, todos: list[dict[str, str]] | None = None) -> None:
        self.plan(todos)
        raise _Next(AIMessage(content=text))


# --------------------------------------------------------------------------- data helpers


def _json_line(prefix: str, text: str) -> dict[str, Any]:
    """The JSON object on the last line of `text` that starts with `prefix` ({} if none)."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith(prefix):
            try:
                value = json.loads(line[len(prefix) :])
            except ValueError:
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def _result(text: str | None) -> dict[str, Any]:
    return _json_line(RESULT_PREFIX, text or "")


def _task_context(messages: Sequence[BaseMessage]) -> dict[str, Any]:
    """CONTEXT from a sub-agent's task description (its first human message)."""
    for message in messages:
        if isinstance(message, HumanMessage):
            return _json_line(CONTEXT_PREFIX, _text(message))
    return {}


def _run_context(messages: Sequence[BaseMessage]) -> dict[str, Any]:
    """The run-context JSON the studio gets as its first human message."""
    for message in messages:
        if isinstance(message, HumanMessage):
            try:
                value = json.loads(_text(message))
            except ValueError as exc:
                raise AssertionError("fake studio: the first message is not the run-context JSON") from exc
            if isinstance(value, dict):
                return value
    raise AssertionError("fake studio: no run context")


def _report(summary: str, result: dict[str, Any]) -> str:
    return f"{summary.strip()}\n{RESULT_PREFIX}{json.dumps(result, ensure_ascii=False)}"


def _task(subagent: str, text: str, context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    description = f"{text.strip()}\n{CONTEXT_PREFIX}{json.dumps(context, ensure_ascii=False)}"
    return "task", {"subagent_type": subagent, "description": description}


_GUTTER = re.compile(r"^\s*(\d+)(?:\.(\d+))?(?:\t|  )(.*)$")


def file_body(read_result: str) -> str:
    """The file text inside a read_file result.

    deepagents 0.7.38 returns a status header line `@@ lines A-B of T @@` and then the
    source lines verbatim. Older versions prefixed every line with a line-number gutter
    (`  12\\t...`, long lines split as `12.1`); that form is undone too.
    """
    lines = read_result.split("\n")
    if lines and lines[0].startswith("@@") and lines[0].rstrip().endswith("@@"):
        lines = lines[1:]
    head = _GUTTER.match(lines[0]) if lines else None
    if head is not None and head.group(1) == "1" and head.group(2) is None:
        rows: list[str] = []
        for line in lines:
            match = _GUTTER.match(line)
            if match is None:
                rows.append(line)
            elif match.group(2) is not None and rows:
                rows[-1] += match.group(3)
            else:
                rows.append(match.group(3))
        lines = rows
    return "\n".join(lines)


def _read_json(read_result: str | None) -> dict[str, Any] | None:
    if not read_result:
        return None
    try:
        value = json.loads(file_body(read_result))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s.strip()]


def _words(text: str, limit: int) -> str:
    words = text.split()
    return " ".join(words[:limit])


def _todos(items: Sequence[str], current: int) -> list[dict[str, str]]:
    """Items before `current` completed, `current` in progress, the rest pending (current >= len: all done)."""
    return [
        {"content": item, "status": "completed" if i < current else "in_progress" if i == current else "pending"}
        for i, item in enumerate(items)
    ]


def _compiled(result: str | None) -> bool:
    """A compile_screenplay result counts as success only when it starts with 'ok:'."""
    return bool(result) and result.lstrip().startswith("ok:")  # type: ignore[union-attr]


def _errors_of(result: str | None) -> list[str]:
    lines = (result or "").splitlines()
    return [ln for ln in lines if ln.strip() and not ln.startswith(("round ", "Fix ", "STOP"))]


# --------------------------------------------------------------------------- studio


def _studio(s: _Script, messages: Sequence[BaseMessage], story: FakeStory) -> None:
    ctx = _run_context(messages)
    if ctx.get("memoryId"):
        _studio_second_telling(s, ctx, story)
    _studio_new_memory(s, ctx, story)


def _run_basics(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "teller": ctx["authorId"],
        "tellerName": ctx.get("authorName") or ctx["authorId"],
        "text": ctx.get("text") or "",
        "year": ctx.get("year"),
        "photoIds": list(ctx.get("photoIds") or []),
    }


def _studio_new_memory(s: _Script, ctx: dict[str, Any], story: FakeStory) -> None:
    base = {**_run_basics(ctx), "mode": NEW_MEMORY}
    teller, teller_name = base["teller"], base["tellerName"]
    participants = [p for p in dict.fromkeys(ctx.get("participantIds") or []) if p != teller]
    items = [
        "Check the story with the teller",
        "Storyboard the shots",
        f"Find the props and the set: {', '.join(story.objects)}, {story.place}",
        "Stage the scene",
        f"Write {teller_name}'s narration",
        "Check the frames",
        f"Record {teller_name}'s voice and save",
        "Share it with everyone who was there",
    ]

    # 1. interviewer
    interview = _result(
        s.one(
            *_task(
                "interviewer",
                f"Check that {teller_name}'s story can be staged: a place, the people, one concrete action. "
                f"Teller {teller}, year {base['year']}. Story: {base['text']}",
                {**base, "participants": participants},
            ),
            todos=_todos(items, 0),
        )
    )
    place = interview.get("place") or story.place
    people = {p["id"]: p for p in interview.get("people") or [] if isinstance(p, dict) and "id" in p}
    if not people:
        people = {pid: {"id": pid, "name": pid, "died": None} for pid in [teller, *participants]}
    names = {pid: str(p.get("name") or pid) for pid, p in people.items()}
    names.setdefault(teller, teller_name)
    others = [pid for pid in participants if pid in people]
    living = [pid for pid in others if not people[pid].get("died")]
    cast = [teller, *others[: max(0, story.max_cast - 1)]]

    # 2. storyboarder
    objects = story.objects
    board_ctx = {**base, "place": place, "cast": cast, "names": names, "objects": objects,
                 "answers": interview.get("answers") or []}
    s.one(*_task("storyboarder", f"Write /shotlist.md for the story at the {place}.", board_ctx),
          todos=_todos(items, 1))

    # 3. prop_master and set_designer, in one turn
    props_text, sets_text = s.do(
        _task("prop_master",
              f"Find or make the props in /shotlist.md: {', '.join(objects)}. Have the family confirm them.",
              {**base, "objects": objects}),
        _task("set_designer", f"Reuse or make the set for the {place}, with marks for the cast.",
              {**base, "place": place}),
        todos=_todos(items, 2),
    )
    props = [p for p in _result(props_text).get("props") or [] if isinstance(p, dict) and p.get("id")]
    set_info = _result(sets_text)

    # 4. director
    stage_ctx = {
        **base,
        "place": place,
        "cast": cast,
        "names": names,
        "props": [{"id": p["id"], "name": p.get("name") or p["id"], "size": p.get("size")} for p in props],
        "setId": set_info.get("setId"),
        "marks": set_info.get("marks") or {},
    }
    staged = _result(
        s.one(*_task("director", "Stage the scene in /screenplay.json from /shotlist.md and compile it.",
                     {**stage_ctx, "pass": "stage"}),
              todos=_todos(items, 3))
    )
    if not staged.get("compiled"):
        errors = "; ".join(staged.get("errors") or []) or "the director could not compile it"
        s.finish(f"The scene could not be staged: {errors}")

    # 5. narrator (write pass), then compile, sending staging errors back to the director
    narr_ctx = {**base, "pass": "write"}
    s.one(*_task("narrator", f"Write pass, no voice yet: write {teller_name}'s narration lines into /screenplay.json.",
                 narr_ctx), todos=_todos(items, 4))
    _compile_with_fixes(s, stage_ctx, todos=_todos(items, 4))

    # 6. checker, at most 2 fix rounds
    _check_with_fixes(s, {**stage_ctx, "pass": "fix"}, "director", todos=_todos(items, 5))

    # 7. narrator (voice pass), save
    s.one(*_task("narrator", f"Voice pass: call synthesize_narration for {teller}. Change nothing else.",
                 {**base, "pass": "voice"}), todos=_todos(items, 6))
    title = (ctx.get("title") or "").strip() or f"The {story.prop} at the {place}"
    saved = s.one("save_memory", {"title": title, "participant_ids": list(dict.fromkeys([*cast, *others]))},
                  todos=_todos(items, 6))
    match = SAVED.search(saved or "")
    if match is None:
        s.finish(f"The scene was made but could not be saved: {(saved or 'save_memory is not available').strip()}")
    memory_id = match.group(1)  # type: ignore[union-attr]

    # 8. linker
    if living:
        s.one(*_task("linker", f"Add memory {memory_id} to everyone's book and ask each for their version.",
                     {**base, "memoryId": memory_id, "place": place,
                      "prop": props[0].get("name") if props else None,
                      "ask": [{"id": pid, "name": names.get(pid, pid)} for pid in living]}),
              todos=_todos(items, 7))
    s.finish(f"Made the scene \"{title}\" and saved it to {teller_name}'s book.", todos=_todos(items, len(items)))


def _studio_second_telling(s: _Script, ctx: dict[str, Any], story: FakeStory) -> None:
    base = {**_run_basics(ctx), "mode": SECOND_TELLING, "memoryId": ctx["memoryId"]}
    teller, teller_name = base["teller"], base["tellerName"]
    items = [
        "Read the shared scene",
        f"Write {teller_name}'s narration over it",
        "Check the new narration",
        f"Record {teller_name}'s voice and save",
        "Share it with anyone new to the moment",
    ]
    screenplay = _read_json(s.one("read_file", {"file_path": SCREENPLAY_PATH, "limit": READ_LIMIT},
                                  todos=_todos(items, 0)))
    if screenplay is None:
        s.finish("The existing scene could not be loaded, so nothing was added.")
    cast = list(screenplay.get("cast") or [])  # type: ignore[union-attr]

    s.one(*_task("narrator",
                 f"Write pass, second telling: write {teller}'s lines only into narrations[{teller}] of /screenplay.json.",
                 {**base, "pass": "write"}), todos=_todos(items, 1))
    _compile_with_fixes(s, {**base, "pass": "fix"}, todos=_todos(items, 1))
    _check_with_fixes(s, {**base, "pass": "write"}, "narrator", todos=_todos(items, 2))

    s.one(*_task("narrator", f"Voice pass, second telling: call synthesize_narration for {teller}.",
                 {**base, "pass": "voice"}), todos=_todos(items, 3))
    saved = s.one("save_memory", {"title": f"{teller_name}'s telling", "participant_ids": [teller]},
                  todos=_todos(items, 3))
    match = SAVED.search(saved or "")
    if match is None:
        s.finish(f"{teller_name}'s telling could not be saved: {(saved or 'save_memory is not available').strip()}")
    memory_id = match.group(1)  # type: ignore[union-attr]

    # "New to the moment" means not yet in the saved memory's participants (which include
    # people linked by an earlier run but never cast). The teller is a participant once
    # saved, so find_memories(person_id=teller) lists the moment; fall back to the cast.
    already = set(cast)
    try:
        briefs = json.loads(s.one("find_memories", {"person_id": teller}, todos=_todos(items, 4)) or "[]")
    except ValueError:
        briefs = []
    for brief in briefs if isinstance(briefs, list) else []:
        if isinstance(brief, dict) and brief.get("id") == memory_id:
            already |= set(brief.get("participants") or [])
            break
    new = [p for p in dict.fromkeys(ctx.get("participantIds") or []) if p != teller and p not in already]
    if new:
        s.one(*_task("linker", f"Add memory {memory_id} to the books of the people new to it.",
                     {**base, "memoryId": memory_id, "place": story.place, "prop": story.prop,
                      "ask": [{"id": pid, "name": pid} for pid in new]}),
              todos=_todos(items, 4))
    s.finish(f"Added {teller_name}'s telling to the moment.", todos=_todos(items, len(items)))


def _compile_with_fixes(s: _Script, fix_ctx: dict[str, Any], *, todos: list[dict[str, str]]) -> None:
    """compile_screenplay; on errors, the director fixes them; at most MAX_STUDIO_COMPILES compiles."""
    for attempt in range(1, MAX_STUDIO_COMPILES + 1):
        result = s.one("compile_screenplay", {}, todos=todos)
        if result is None or _compiled(result):
            return
        if attempt == MAX_STUDIO_COMPILES or "STOP" in result:
            s.finish(f"The scene stopped compiling after the narration: {'; '.join(_errors_of(result))}")
        s.one(*_task("director", "Fix exactly these compile errors in /screenplay.json, then compile.",
                     {**fix_ctx, "pass": "fix", "errors": _errors_of(result), "round": attempt}), todos=todos)


def _check_with_fixes(s: _Script, fix_ctx: dict[str, Any], fixer: str, *, todos: list[dict[str, str]]) -> None:
    """checker; on problems the fixer fixes, the studio compiles, the checker looks again (2 rounds max)."""
    for check_round in range(MAX_CHECK_FIX_ROUNDS + 1):
        verdict = _result(
            s.one(*_task("checker", "Render the key moments and critique them into /critique.md.",
                         {**fix_ctx, "round": check_round}), todos=todos)
        )
        problems = verdict.get("problems") or []
        if verdict.get("verdict") != "fix" or not problems or check_round == MAX_CHECK_FIX_ROUNDS:
            return
        s.one(*_task(fixer, "Fix only the problems in /critique.md, keep everything else, then compile.",
                     {**fix_ctx, "problems": problems, "round": check_round + 1}), todos=todos)
        _compile_with_fixes(s, fix_ctx, todos=todos)


# --------------------------------------------------------------------------- sub-agents


def _interviewer(s: _Script, ctx: dict[str, Any], story: FakeStory) -> None:
    teller = ctx.get("teller")
    people: list[dict[str, Any]] = []
    for pid in [teller, *(ctx.get("participants") or [])]:
        found = s.one("get_person", {"person_id": pid})
        if found is None:  # get_person not bound: trust the ids
            people.append({"id": pid, "name": ctx.get("tellerName") if pid == teller else pid, "died": None})
            continue
        try:
            person = json.loads(found)
        except ValueError:
            continue  # "not found: person ..."
        people.append({"id": pid, "name": person.get("name") or pid, "died": person.get("died")})
    answers: list[str] = []
    if story.followup:
        answer = s.one("ask_teller", {"question": story.followup})
        if answer is not None:
            answers.append(answer)
    s.finish(_report(
        "\n".join([
            "verdict: stageable",
            f"place: {story.place}",
            "who: " + ", ".join(p["id"] for p in people),
            f"action: {_words(ctx.get('text') or '', 20)}",
            f"year: {ctx.get('year')}",
            "answers: " + ("; ".join(json.dumps(a) for a in answers) or "none"),
            "unknown: none",
        ]),
        {"verdict": "stageable", "place": story.place, "people": people, "answers": answers},
    ))


def _storyboarder(s: _Script, ctx: dict[str, Any], story: FakeStory) -> None:
    teller = ctx.get("teller")
    names = ctx.get("names") or {}
    cast = ctx.get("cast") or [teller]
    # Each photo sent with the telling is looked at once; its description backs the shot.
    seen: list[str] = []
    for photo_id in ctx.get("photoIds") or []:
        described = s.one("describe_photo", {"photo_id": photo_id})
        if described and not described.startswith(("unknown", "could not", "photo ")):
            seen.append(f"photo:{photo_id} ({_words(described, 20)})")
    objects = [str(o) for o in ctx.get("objects") or story.objects]
    listed = [f"{_slug(objects[0])} ({names.get(teller, teller)} carries it)", *(_slug(o) for o in objects[1:])]
    shotlist = "\n".join([
        "## s1",
        f"place: {ctx.get('place') or story.place} (from telling:{teller}:s1)",
        "who: " + ", ".join(cast),
        f"moment: {_words(ctx.get('text') or '', 25)}",
        "objects: " + ", ".join(listed),
        "sources: " + ", ".join([f"telling:{teller}:s1", *seen]),
        "",
    ])
    s.one("write_file", {"file_path": SHOTLIST_PATH, "content": shotlist})
    s.finish(_report(
        f"1 shot.\ns1 | {ctx.get('place') or story.place} | {_words(ctx.get('text') or '', 12)}\n"
        f"objects: {', '.join(objects)}",
        {"shots": ["s1"], "objects": objects},
    ))


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "prop"


_CONFIRM_LINE = re.compile(r"^(?P<id>[^|]+?)\s*\|\s*(?P<name>[^|]*?)\s*\|\s*size=(?P<size>\S+)\s*\|\s*(?P<status>kept|renamed|photo)\s*$")


def _find_or_make(s: _Script, ctx: dict[str, Any], name: str, size: str, taken: set[str]) -> dict[str, Any] | None:
    """One object's chip: the first saved match not already used for another object, else a
    generated prop (None when generation failed)."""
    found = s.one("find_prop", {"description": name})
    matches: Any = None
    if found and not found.startswith(("error", "no saved")):
        try:
            matches = json.loads(found)
        except ValueError:
            matches = None
    match = None
    if isinstance(matches, list):
        match = next((m for m in matches if isinstance(m, dict) and m.get("id") and m["id"] not in taken), None)
    if match is not None:
        return {"id": match["id"], "name": match.get("name") or name,
                "description": match.get("description"), "size": match.get("size") or size,
                "art": match.get("preview"), "reused": True, "sockets": _socket_names(match)}
    args: dict[str, Any] = {
        "name": name,
        "description": f"the {name} from {ctx.get('tellerName') or 'the teller'}'s telling",
        "size": size,
    }
    if size not in BIG_SIZES:
        args["grip"] = [0.5, 0.0]
    sockets = surface_sockets(name, size)
    if sockets is not None:
        args["sockets"] = sockets
    made_text = s.one("generate_prop", args)
    try:
        made = json.loads(made_text or "")
    except ValueError:
        made = None
    if isinstance(made, dict) and made.get("id"):
        return {"id": made["id"], "name": made.get("name") or name,
                "description": f"the {name} from the telling", "size": made.get("size") or size,
                "art": made.get("preview"), "reused": False, "sockets": _socket_names(made)}
    return None


SURFACE_SOCKETS: tuple[tuple[str, dict[str, list[float]]], ...] = (
    ("table", {"top": [0.5, 0.1]}),
    ("counter", {"top": [0.5, 0.1]}),
    ("desk", {"top": [0.5, 0.1]}),
    ("stove", {"burner": [0.5, 0.2]}),
    ("shelf", {"shelf": [0.5, 0.3]}),
)
"""Furniture things are set on, by a word of its name, and the socket the prop master gives it
(prop_master.md: the default seat is the wrong place for a cake on a table or a pot on a stove)."""


def surface_sockets(name: str, size: str) -> dict[str, list[float]] | None:
    """The sockets the prop master passes to generate_prop for `name`, or None for the size's
    defaults: a table, counter or desk gets a "top", a stove a "burner", a shelf a "shelf"."""
    if size != "furniture":
        return None
    words = set(re.findall(r"[a-z]+", name.lower()))
    for word, sockets in SURFACE_SOCKETS:
        if word in words:
            return {k: list(v) for k, v in sockets.items()}
    return None


def _socket_names(prop: dict[str, Any]) -> list[str]:
    sockets = prop.get("sockets")
    return sorted(sockets) if isinstance(sockets, dict) else []


def _prop_master(s: _Script, ctx: dict[str, Any], story: FakeStory) -> None:
    chips: list[dict[str, Any]] = []
    failed: list[str] = []
    for name in story.objects:
        chip = _find_or_make(s, ctx, name, story.size_of(name), {c["id"] for c in chips})
        if chip is None:
            failed.append(name)
        else:
            chips.append(chip)
    if not chips:
        s.finish(_report(f"could not make: {', '.join(failed)}", {"props": [], "removed": [], "failed": failed}))
    reused = {c["id"]: bool(c.get("reused")) for c in chips}
    sockets = {c["id"]: c.pop("sockets", []) for c in chips}  # for the report, not the chips
    reviewed = s.one("confirm_props", {"props": chips})
    kept: list[dict[str, Any]] = []
    removed: list[str] = []
    if reviewed is None:  # confirm_props not bound: keep what was found
        kept = [{"id": c["id"], "name": c["name"], "size": c["size"], "status": "kept"} for c in chips]
    else:
        for line in reviewed.splitlines():
            line = line.strip()
            if line.startswith("removed:"):
                rest = line[len("removed:") :].strip()
                removed = [] if rest in ("", "none") else [r.strip() for r in rest.split(",") if r.strip()]
                continue
            m = _CONFIRM_LINE.match(line)
            if m:
                kept.append({"id": m["id"], "name": m["name"], "size": m["size"], "status": m["status"]})
    teller = ctx.get("teller")
    lines = [
        f"{p['id']} | {p['name']} | size={p['size']} | {'held by ' + str(teller) if i == 0 else 'on the set'} | "
        f"shots s1 | {'reused' if reused.get(p['id']) else 'generated'} | parts: none | states: none | "
        f"sockets: {', '.join(sockets.get(p['id']) or []) or 'none'}"
        for i, p in enumerate(kept)
    ]
    if removed:
        lines.append(f"removed by the family: {', '.join(removed)}")
    if failed:
        lines.append(f"could not make: {', '.join(failed)}")
    result: dict[str, Any] = {"props": kept, "removed": removed}
    if failed:
        result["failed"] = failed
    s.finish(_report("\n".join(lines) or "no props kept", result))


def _set_designer(s: _Script, ctx: dict[str, Any], story: FakeStory) -> None:
    place = ctx.get("place") or story.place
    found = s.one("find_set", {"place": place})
    matches: Any = None
    if found and not found.startswith(("error", "no saved")):
        try:
            matches = json.loads(found)
        except ValueError:
            matches = None
    if isinstance(matches, list) and matches:
        match = matches[0]
        set_id, marks, how = match["id"], match.get("marks") or {}, "reused"
    else:
        set_id, marks, how = None, {}, "generated"
        layers = [
            ("back", f"the sky and the far side of the {place}", None),
            ("middle", f"the {place}", {"left_spot": [0.3, 0], "right_spot": [0.7, 0]}),
            ("front", f"a low edge in front of the {place}", None),
        ]
        for layer, description, layer_marks in layers:
            args: dict[str, Any] = {"place": place, "layer": layer, "description": description}
            if layer_marks:
                args["marks"] = layer_marks
            made_text = s.one("generate_set_layer", args)
            try:
                made = json.loads(made_text or "")
            except ValueError:
                made = None
            if isinstance(made, dict) and made.get("setId"):
                set_id, marks = made["setId"], made.get("marks") or marks
    if set_id is None:
        s.finish(_report(f"could not make a set for {place}", {"setId": None, "marks": {}}))
    mark_text = ", ".join(f"{name} {value}" for name, value in marks.items())
    s.finish(_report(f"{set_id} | {place} | {how} | shots s1\nmarks: {mark_text}",
                     {"setId": set_id, "marks": marks, "reused": how == "reused"}))


# ----------------------------------------------------------------- director


def _ground_marks(marks: dict[str, Any]) -> tuple[str, str]:
    """(left, right) marks for the two puppets: prefer ground-level marks, then any."""
    usable = {
        name: value for name, value in marks.items()
        if isinstance(value, list) and len(value) >= 2 and name not in ("offstage_left", "offstage_right")
        and 0.05 <= float(value[0]) <= 0.95
    }
    if not usable:
        return "left_spot", "right_spot"
    ground = {n: v for n, v in usable.items() if float(v[1]) == 0.0} or usable
    if "left_spot" in ground and "right_spot" in ground:
        return "left_spot", "right_spot"
    ordered = sorted(ground, key=lambda n: (float(ground[n][0]), n))
    return ordered[0], ordered[-1]


WAVE_SECONDS = 1.0
FIRST_BEAT_SECONDS = 2.5
"""The directing skill: each shot's first named beat about 2.5 s or more in, after the setup."""
BEAT_LEAD = 0.5
"""The close-up on the beat starts this long before it and ends as it starts (directing skill)."""
BIG_SIZES = ("furniture", "vehicle", "building")
"""Prop sizes too big to carry (screenplay.validate TOO_BIG): they stand at PROP_MARK instead."""
PROP_MARK = "prop_spot"


def _wave(t: float, who: str) -> dict[str, Any]:
    """A wave as the `pose` verb with the rig's 'wave' pose.

    `pose` is a core verb of screenplay.compiler; the `wave` verb comes with the body
    verbs, which a compiler build may not have yet. Both look the same on stage.
    """
    return {"t": t, "do": "pose", "who": who, "pose": "wave", "dur": WAVE_SECONDS, "id": "wave"}


def build_screenplay(ctx: dict[str, Any]) -> dict[str, Any]:
    """The director's valid draft for one shot on the reported set (camelCase JSON).

    Cast is the teller plus at most one other person; the teller starts holding the first
    kept prop small enough to carry; sizes in BIG_SIZES, and any other props, stand on the
    ground at PROP_MARK. The draft follows the directing skill: the teller's walk at 0.5 is
    an unnamed setup action, and the first named beat comes FIRST_BEAT_SECONDS or more in, so
    the opening line can land on it. That beat is the hand-over of the carried prop (two
    people and a prop), else the other's wave (two people), else the teller raising the prop
    (`hold`) or waving (alone); with two people the other waves after a hand-over. The camera
    opens `wide` and closes up on the beat's heart BEAT_LEAD seconds before it, ending as it
    starts; the one pull tab scrubs the beat. It uses only core verbs of screenplay.compiler
    (walk_to, hand_to, hold, pose).
    """
    teller: str = ctx["teller"]
    cast: list[str] = list(ctx.get("cast") or [teller])
    other = next((p for p in cast if p != teller), None)
    names: dict[str, str] = ctx.get("names") or {}
    props: list[dict[str, Any]] = ctx.get("props") or []
    prop = next((p for p in props if p.get("size") not in BIG_SIZES), None)  # the one the teller carries
    marks: dict[str, Any] = ctx.get("marks") or {"left_spot": [0.3, 0], "right_spot": [0.7, 0]}
    left, right = _ground_marks(marks)
    lu = float(marks.get(left, [0.3, 0])[0])
    ru, rh = (float(x) for x in marks.get(right, [0.7, 0])[:2])

    shot_marks: dict[str, list[float]] = {}
    start: dict[str, dict[str, Any]] = {teller: {"at": left, "facing": "right"}}
    if prop:
        start[teller]["holding"] = prop["id"]
    # Every other prop (furniture, vehicles, buildings, extra small ones) stands on the
    # ground between the two marks.
    prop_start = {p["id"]: {"at": PROP_MARK} for p in props if p is not prop}
    if prop_start:
        shot_marks[PROP_MARK] = [round((lu + ru) / 2, 3), 0.0]
    actions: list[dict[str, Any]] = []
    walk_t = 0.5
    teller_name = names.get(teller, teller)
    prop_name = (prop.get("name") or prop["id"]) if prop else ""
    if other:
        other_name = names.get(other, other)
        start[other] = {"at": right, "facing": "left"}
        meet_u = round(max(0.05, ru - 0.12), 3)
        shot_marks["meet"] = [meet_u, rh]
        walk_end = walk_t + abs(2 * (meet_u - lu)) / WALK_SPEED
        actions.append({"t": walk_t, "do": "walk_to", "who": teller, "to": "meet"})  # setup: no id
        t = round(max(FIRST_BEAT_SECONDS, walk_end + 0.3), 2)
        if prop:
            actions.append({"t": t, "do": "hand_to", "who": teller, "what": prop["id"], "to": other, "id": "hand_over"})
            beat = (t, round(t + 0.9, 2), [f"prop:{prop['id']}", f"puppet:{teller}", f"puppet:{other}"],
                    f"Pull to hand over the {prop_name}", prop["id"])
            actions.append(_wave(round(t + 1.2, 2), other))
        else:
            actions.append(_wave(t, other))
            beat = (t, round(t + WAVE_SECONDS + 0.4, 2), [f"puppet:{other}"], f"Pull to make {other_name} wave", other)
    else:
        walk_end = walk_t + abs(2 * (ru - lu)) / WALK_SPEED
        actions.append({"t": walk_t, "do": "walk_to", "who": teller, "to": right})  # setup: no id
        t = round(max(FIRST_BEAT_SECONDS, walk_end + 0.3), 2)
        if prop:
            actions.append({"t": t, "do": "hold", "who": teller, "what": prop["id"], "id": "hold"})
            beat = (t, round(t + 0.4, 2), [f"prop:{prop['id']}", f"puppet:{teller}"],
                    f"Pull to raise the {prop_name}", prop["id"])
        else:
            actions.append(_wave(t, teller))
            beat = (t, round(t + WAVE_SECONDS + 0.4, 2), [f"puppet:{teller}"], f"Pull to make {teller_name} wave", teller)

    place = ctx.get("place") or "the place"
    who = f"{teller_name} and {names.get(other, other)}" if other else teller_name
    named = props[0] if props else None  # the page names the first prop, carried or not
    with_prop = f" with the {named.get('name') or named['id']}" if named else ""
    page_text = _words(f"In {ctx.get('year')}, {who} at the {place}{with_prop}.", PAGE_TEXT_WORDS)

    explanations = [{"target": f"puppet:{teller}", "text": f"{teller_name} tells this story."}]
    if other:
        explanations.append({"target": f"puppet:{other}",
                             "text": f"{names.get(other, other)} was there, in {teller_name}'s telling."})
    explanations.extend(
        {"target": f"prop:{p['id']}", "text": f"The {p.get('name') or p['id']} from {teller_name}'s telling."}
        for p in props
    )
    beat_from, beat_to, targets, label, heart = beat
    shot: dict[str, Any] = {
        "id": "s1",
        "set": ctx.get("setId"),
        "start": start,
        "camera": [
            {"t": 0, "move": "wide"},
            {"t": round(beat_from - BEAT_LEAD, 2), "move": "close_up", "target": heart, "dur": BEAT_LEAD},
        ],
        "actions": actions,
        "narration": [],
        "transition": "fold",
        "pageText": page_text,
    }
    if shot_marks:
        shot["marks"] = shot_marks
    if prop_start:
        shot["propStart"] = prop_start
    return {
        "memoryId": "draft",
        "cast": cast,
        "props": [p["id"] for p in props],
        "shots": [shot],
        "interactions": [{
            "id": "pull_beat",
            "kind": "pull_tab",
            "label": label,
            "shot": "s1",
            "page": "stage",
            "at": [0.85, 0.82],
            "drives": {"type": "scrub", "from": beat_from, "to": beat_to, "targets": targets},
        }],
        "explanations": explanations,
    }


def bad_screenplay(
    ctx: dict[str, Any],
    build: Callable[[dict[str, Any]], dict[str, Any]] = build_screenplay,
) -> dict[str, Any]:
    """The valid draft from `build` with one deliberate mistake: the first action of the first
    shot goes `to` an unknown mark (on a verb without `to` that is an unexpected parameter)."""
    draft = build(ctx)
    draft["shots"][0]["actions"][0]["to"] = BAD_MARK
    return draft


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _director(s: _Script, ctx: dict[str, Any], story: FakeStory) -> None:
    if ctx.get("pass") == "fix":
        _director_fix(s, ctx)
    build = story.screenplay or build_screenplay
    failures = 0
    while True:
        draft = bad_screenplay(ctx, build) if failures < story.compile_failures else build(ctx)
        s.one("write_file", {"file_path": SCREENPLAY_PATH, "content": _dump(draft)})
        result = s.one("compile_screenplay", {})
        if result is None or _compiled(result):
            shots = "\n".join(
                f"{shot.get('id')} | {shot.get('set')} | " + ", ".join(a.get("do", "?") for a in shot.get("actions") or [])
                for shot in draft.get("shots") or []
            )
            tabs = ", ".join(str(i.get("id")) for i in draft.get("interactions") or []) or "none"
            s.finish(_report(
                f"compiled: yes\nrounds: {failures + 1}\n{shots}\ninteractions: {tabs}",
                {"compiled": True, "rounds": failures + 1},
            ))
        failures += 1
        if "STOP" in result:
            errors = _errors_of(result)
            s.finish(_report("compiled: no\nrounds: 3\n" + "\n".join(errors),
                             {"compiled": False, "rounds": failures, "errors": errors}))


def _director_fix(s: _Script, ctx: dict[str, Any]) -> None:
    """Fix mode: keep everything (narration included), apply the named fix, compile once more."""
    current = _read_json(s.one("read_file", {"file_path": SCREENPLAY_PATH, "limit": READ_LIMIT}))
    if current is None:
        s.finish(_report("compiled: no\n/screenplay.json could not be read", {"compiled": False, "errors": []}))
    if ctx.get("mode") != SECOND_TELLING:
        teller = ctx.get("teller")
        for shot in current.get("shots") or []:  # type: ignore[union-attr]
            if teller in (shot.get("start") or {}):
                shot["start"][teller]["facing"] = "right"
    s.one("write_file", {"file_path": SCREENPLAY_PATH, "content": _dump(current)})  # type: ignore[arg-type]
    result = s.one("compile_screenplay", {})
    ok = result is None or _compiled(result)
    s.finish(_report(f"compiled: {'yes' if ok else 'no'}\nfixed: " + "; ".join(ctx.get("problems") or ctx.get("errors") or []),
                     {"compiled": ok, "errors": [] if ok else _errors_of(result)}))


# ------------------------------------------------------------------ narrator


MAX_LINES_PER_SHOT = 4
RISE_SECONDS = 1.2
"""A shot's set rises this long before its t = 0; another telling's line may start no earlier."""


def _tokens(text: str) -> list[str]:
    """Spoken words as the compiler counts them: whitespace tokens with their edge punctuation
    stripped, punctuation-only tokens dropped (screenplay.narration.tokenize)."""
    out: list[str] = []
    for raw in text.split():
        start, end = 0, len(raw)
        while start < end and not raw[start].isalnum():
            start += 1
        while end > start and not raw[end - 1].isalnum():
            end -= 1
        if start < end:
            out.append(raw[start:end])
    return out


def _cue_index(line: dict[str, Any], tokens: list[str]) -> int:
    """Index of the line's cue word among its spoken words (default and fallback: the last)."""
    cue_word = line.get("cueWord")
    if cue_word:
        for i, token in enumerate(tokens):
            if token.casefold() == str(cue_word).casefold():
                return i
    return max(0, len(tokens) - 1)


def _cue_time(shot: dict[str, Any], action_id: str) -> float | None:
    """Shot time the shared action starts on, from the shot's default-telling cued line.

    The compiler moves a cued action onto its line's cue word (`cueWord`, default the last
    word): the word's TTS timing when the line has been voiced, else spoken `WORD_SECONDS`
    apart. None if no default line in `shot` cues `action_id`.
    """
    for line in shot.get("narration") or []:
        if line.get("cue") != action_id:
            continue
        start = float(line.get("t") or 0)
        words = line.get("words") or []
        if words:
            index = _cue_index(line, [str(w.get("word") or "") for w in words])
            return start + float(words[index].get("t") or 0)
        tokens = _tokens(str(line.get("text") or ""))
        return start + WORD_SECONDS * _cue_index(line, tokens)
    return None


def _first_cue(shot: dict[str, Any]) -> tuple[str, float] | None:
    """(id, t) of the shot's earliest action that has an id (list order breaks ties)."""
    named = [(float(a.get("t") or 0), i, str(a["id"])) for i, a in enumerate(shot.get("actions") or []) if a.get("id")]
    if not named:
        return None
    t, _, action_id = min(named)
    return action_id, t


def shot_sentences(sentences: Sequence[str], shots: int) -> list[list[tuple[int, str]]]:
    """The teller's sentences (numbered from 1) spread over `shots` shots, in order.

    Every shot gets at least one: with fewer sentences than shots, each later shot reuses the
    last sentence; with more, they are split into runs as even as possible (earlier shots take
    the extra ones), at most MAX_LINES_PER_SHOT per shot. No sentences: no lines anywhere.
    """
    numbered = list(enumerate(sentences, start=1))
    if shots <= 0:
        return []
    if not numbered:
        return [[] for _ in range(shots)]
    if len(numbered) <= shots:
        return [[numbered[min(i, len(numbered) - 1)]] for i in range(shots)]
    base, extra = divmod(len(numbered), shots)
    out: list[list[tuple[int, str]]] = []
    i = 0
    for k in range(shots):
        size = base + (1 if k < extra else 0)
        out.append(numbered[i : i + size][:MAX_LINES_PER_SHOT])
        i += size
    return out


EARLY_CUE_WORDS = 4
"""The fake narrator cues one of a line's first four words (narrator.md: prefer an early cue word)."""
FILLER_WORDS = frozenset(
    "a an and the then so but or of to in on at for with by from up out as i we he she it they you me "
    "us him her them my our his its their was were is are be been had has have did do that this there "
    "when just all every".split()
)
"""Words never chosen as a cue word while a word that names something comes early enough."""


def early_cue_index(tokens: Sequence[str], latest: int) -> int:
    """Index of the cue word among a line's spoken words: the first of its first EARLY_CUE_WORDS
    that is not a FILLER_WORDS word, at most `latest` (the latest word that keeps the line from
    starting too early); the latest allowed word when they are all filler. 0 for no words."""
    if not tokens:
        return 0
    limit = max(0, min(len(tokens) - 1, EARLY_CUE_WORDS - 1, latest))
    for i in range(limit + 1):
        if tokens[i].casefold() not in FILLER_WORDS:
            return i
    return limit


def _lines_for_shot(ctx: dict[str, Any], shot: dict[str, Any], numbered: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """One shot's lines: each sentence quoted and sourced, the first cued to the shot's first
    named action, the rest after it with no overlap.

    As narrator.md teaches, the cued line names an early `cueWord` (`early_cue_index`) and
    starts so that word falls where the action is already written: t = action t - 0.25 x the
    words before it, so the compiler moves nothing. A first telling cues an earlier word when
    the action is too early for that, rather than starting the line before t = 0. Another
    telling (`ctx["mode"] == "second_telling"`) shares the action: its line is placed the same
    way over the action as it was spoken in the default telling (its TTS word time when voiced),
    and since the compiler shifts it onto that word and lets it start at most RISE_SECONDS
    before the shot's t = 0, the cue word is early enough for that. Later lines start only
    after the cued line has been said.
    """
    teller = ctx["teller"]
    second = ctx.get("mode") == SECOND_TELLING
    retells = bool(ctx.get("retells_default"))  # the default narrator rewriting each shot's narration
    cue = _first_cue(shot)
    lines: list[dict[str, Any]] = []
    t = 0.0
    for n, (number, sentence) in enumerate(numbered):
        line: dict[str, Any] = {"t": 0.0, "text": sentence, "source": f"telling:{teller}:s{number}"}
        tokens = _tokens(sentence)
        words = max(1, len(tokens))
        end_after: float | None = None
        if n == 0 and cue is not None:
            action_id, action_t = cue
            line["cue"] = action_id
            spoken = _cue_time(shot, action_id) if second else None
            anchor = action_t if spoken is None else spoken
            # The latest word that keeps the line from starting too early: at t >= 0 in the
            # default telling (a first telling, or the default narrator's retelling, whose cue
            # moves the action), no more than RISE_SECONDS before t = 0 in another (less 0.1 s).
            lead = RISE_SECONDS - 0.1 if second and not retells else 0.0
            latest = int((anchor + lead) / WORD_SECONDS + 1e-9)
            if tokens:
                line["cueWord"] = tokens[early_cue_index(tokens, latest)]
            index = _cue_index(line, tokens)  # the compiler takes the first match
            t = max(0.0, anchor - WORD_SECONDS * index)
            end_after = anchor + WORD_SECONDS * (words - index)  # said through its last word
        line["t"] = round(t, 2)
        end = t + WORD_SECONDS * words
        if end_after is not None:
            end = max(end, end_after)
        lines.append(line)
        t = max(t + 0.3 * words, end) + 0.6
    return lines


def narration_by_shot(ctx: dict[str, Any], screenplay: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The teller's lines for every shot (shot id -> lines), from `ctx["text"]`.

    The sentences are spread over the shots in order (`shot_sentences`): at least one line
    per shot, each sourced `telling:<teller>:s<n>`, and each shot's first line cued to that
    shot's first named action (see `_lines_for_shot`).
    """
    shots = [shot for shot in screenplay.get("shots") or [] if isinstance(shot, dict)]
    spread = shot_sentences(_sentences(ctx.get("text") or ""), len(shots))
    if ctx.get("mode") == SECOND_TELLING and default_teller(screenplay) == ctx.get("teller"):
        ctx = {**ctx, "retells_default": True}
    return {str(shot.get("id")): _lines_for_shot(ctx, shot, numbered) for shot, numbered in zip(shots, spread, strict=True)}


_SOURCE_TELLER = re.compile(r"^telling:([^:]+):")


def default_teller(screenplay: dict[str, Any]) -> str | None:
    """Whose telling each shot's `narration` is: the teller its first sourced line names (the
    compiler's default author), or None when no shot has a sourced line."""
    for shot in screenplay.get("shots") or []:
        for line in (shot.get("narration") or []) if isinstance(shot, dict) else []:
            m = _SOURCE_TELLER.match(str(line.get("source") or ""))
            if m:
                return m.group(1)
    return None


def narration_lines(ctx: dict[str, Any], screenplay: dict[str, Any]) -> list[dict[str, Any]]:
    """The teller's lines for the first shot (`narration_by_shot` of that shot)."""
    shots = screenplay.get("shots") or []
    if not shots:
        return []
    return narration_by_shot(ctx, screenplay).get(str(shots[0].get("id")), [])


def _narrator(s: _Script, ctx: dict[str, Any], _story: FakeStory) -> None:
    teller = ctx.get("teller")
    if ctx.get("pass") == "voice":
        result = s.one("synthesize_narration", {})
        if result is not None and result.startswith("Error"):
            # The media store may fail once; voice once more. A TTS failure is not an
            # Error: the tool narrates silently and says so in a warning line.
            result = s.one("synthesize_narration", {})
        ok = result is not None and not result.startswith("Error")
        s.finish(_report(f"voice pass for {teller}: {'ok' if ok else 'failed'}\n{result or ''}",
                         {"pass": "voice", "ok": ok}))
    screenplay = _read_json(s.one("read_file", {"file_path": SCREENPLAY_PATH, "limit": READ_LIMIT}))
    if screenplay is None:
        s.finish(_report(f"write pass for {teller}: /screenplay.json could not be read", {"pass": "write", "lines": 0}))
    by_shot = narration_by_shot(ctx, screenplay)  # type: ignore[arg-type]
    shots = screenplay.get("shots") or []  # type: ignore[union-attr]
    if ctx.get("mode") == SECOND_TELLING and default_teller(screenplay) != teller:  # type: ignore[arg-type]
        narrations = dict(screenplay.get("narrations") or {})  # type: ignore[union-attr]
        narrations[teller] = by_shot
        screenplay["narrations"] = narrations  # type: ignore[index]
    else:
        for shot in shots:
            shot["narration"] = by_shot.get(str(shot.get("id")), [])
    s.one("write_file", {"file_path": SCREENPLAY_PATH, "content": _dump(screenplay)})  # type: ignore[arg-type]
    counts = "; ".join(f"{sid} has {len(lines)} line(s)" for sid, lines in by_shot.items()) or "no shots"
    cues = [f"{ln['text']!r} -> {ln['cue']}" for lines in by_shot.values() for ln in lines if ln.get("cue")]
    total = sum(len(lines) for lines in by_shot.values())
    s.finish(_report(f"write pass for {teller}: {counts}; cues: {', '.join(cues) or 'none'}",
                     {"pass": "write", "lines": total}))


# ------------------------------------------------------------------- checker


def _checker(s: _Script, ctx: dict[str, Any], story: FakeStory) -> None:
    frames = s.has("render_frames", "inspect_frames")
    if frames:
        s.one("render_frames", {"times": list(FRAME_TIMES)})
        s.one("inspect_frames", {})
    check_round = int(ctx.get("round") or 0)
    problems: list[str] = []
    if check_round < story.checker_problems:
        teller = ctx.get("teller")
        problems.append(f"shot s1, t=0: {teller} must face the others; set start.{teller}.facing to right")
        critique = "verdict: fix\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(problems, start=1))
    else:
        critique = "verdict: pass" if frames else "verdict: pass (no frame tools)"
    s.one("write_file", {"file_path": CRITIQUE_PATH, "content": critique + "\n"})
    verdict = "fix" if problems else "pass"
    s.finish(_report(f"verdict: {verdict}\nround: {check_round}\n" + ("\n".join(problems) or "none"),
                     {"verdict": verdict, "round": check_round, "problems": problems}))


# -------------------------------------------------------------------- linker


def linker_question(ctx: dict[str, Any]) -> str:
    teller_name = ctx.get("tellerName") or ctx.get("teller")
    place = ctx.get("place") or "that day"
    if ctx.get("prop"):
        return f"{teller_name} remembers the {ctx['prop']} at the {place}. What do you remember?"
    return f"{teller_name} told the story of the {place}. What do you remember?"


def _linker(s: _Script, ctx: dict[str, Any], _story: FakeStory) -> None:
    memory_id = ctx.get("memoryId")
    question = linker_question(ctx)
    lines: list[str] = []
    for person in ctx.get("ask") or []:
        pid = person["id"] if isinstance(person, dict) else str(person)
        result = s.one("link_participants", {"memory_id": memory_id, "person_ids": [pid], "question": question})
        lines.append(f"{pid}: {question}" + (f" ({result})" if result else ""))
    s.finish(_report("\n".join(lines) or "no one to ask", {"memoryId": memory_id, "asked": len(lines)}))


_SUBAGENTS: dict[str, Callable[[_Script, dict[str, Any], FakeStory], None]] = {
    "interviewer": _interviewer,
    "storyboarder": _storyboarder,
    "prop_master": _prop_master,
    "set_designer": _set_designer,
    "director": _director,
    "narrator": _narrator,
    "checker": _checker,
    "linker": _linker,
}


# --------------------------------------------------------------------------- public


def make_fake_studio_responder(story: FakeStory = FakeStory()) -> Responder:  # noqa: B008 - frozen dataclass
    """The responder for ScriptedChatModel that plays the studio and every sub-agent for `story`."""

    def responder(messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        role = role_of(messages)
        script = _Script(messages, tool_names)
        try:
            if role == "studio":
                _studio(script, messages, story)
            else:
                _SUBAGENTS[role](script, _task_context(messages), story)
        except _Next as reply:
            return reply.message
        raise AssertionError(f"fake studio: the {role} script ended without a reply")  # pragma: no cover

    return responder


def fake_studio_model(story: FakeStory = FakeStory()) -> ScriptedChatModel:  # noqa: B008 - frozen dataclass
    """A ScriptedChatModel playing the whole studio for `story`."""
    return ScriptedChatModel(responder=make_fake_studio_responder(story))


__all__ = [
    "FakeStory",
    "bad_screenplay",
    "build_screenplay",
    "fake_studio_model",
    "file_body",
    "linker_question",
    "make_fake_studio_responder",
    "narration_by_shot",
    "narration_lines",
    "role_of",
    "shot_sentences",
]
