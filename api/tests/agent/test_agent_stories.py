"""The story corpus: varied family stories end to end through the fake studio on the REAL compiler.

Each story is a run through the public seam (agent.wiring.fake_services + make_manager over
an InMemoryRepo seeded with the hymn_books sample family and whatever people and props the
story needs), with agent.fake_studio playing every agent and a hand-written director draft
(tests/agent/story_screenplays.py) as `FakeStory.screenplay`. The rest of the pipeline is the
real one: prop and set tools on placeholder art, compile_screenplay on screenplay.compiler,
the fake narrator's lines over every shot, the voice pass on FakeTTS (word timings, then a
recompile), save_memory.

Each saved Memory must be a complete storybook scene: a timeline that validates, pageText on
every shot, an explanation for every cast member and prop, a pull tab whose scrub targets have
tracks, the teller's narration over every shot with a cue and TTS word timings on every line,
camera moves from shared/vocabulary.json inside their shots (two or more distinct moves when
the story has more than one shot), voiced narration that moves no action more than MAX_DRIFT
seconds from where it was written, and no scene_review / timeline review warnings. That the
camera cues follow the actions the narration moves is test_agent_cue_camera.py's.

Offline: no keys, no network. Every wait has a timeout.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from langchain_core.messages import ToolMessage
from pydantic import BaseModel
from story_screenplays import STORY_BUILDERS

import schemas
from agent import wiring
from agent.fake_compiler import FakeCompiler
from agent.fake_studio import FakeStory, fake_studio_model
from agent.repo import InMemoryRepo, load_sample_family
from agent.tools import compile as compile_tool
from agent.tools.compile import load_compiler
from agent.tools.scene_review import review_cue_drift, review_scene, review_timeline
from screenplay.narration import retime_actions
from screenplay.schema import VOCABULARY, CompileResult, Screenplay, Timeline

FAMILY = "fam_stories"
WAIT = 30.0
"""Seconds any one wait may take; a hung run fails the test instead of hanging it."""

CAMERA_MOVES = frozenset(VOCABULARY["camera"])
EPS = 1e-3
MAX_DRIFT = 1.0
"""Seconds the voiced narration may move an action from its written time (the shot still plays
as blocked; the camera cues move with it, see test_agent_cue_camera.py)."""

Event = tuple[str, BaseModel]


# --------------------------------------------------------------------------- seeds


def _person(pid: str, name: str, build: str, color: str, *, born: int, notes: str | None = None) -> schemas.Person:
    return schemas.Person(
        id=pid, family_id=FAMILY, name=name, born=born,
        character=schemas.CharacterSheet(color=color, build=build, notes=notes),
    )


PEOPLE = [
    _person("sam", "Sam", "small", "#6a9a3b", born=1990, notes="energetic, never still"),
    _person("lily", "Lily", "small", "#e07a5f", born=1992),
    _person("rosa", "Rosa", "short", "#b5838d", born=1931, notes="moves carefully"),
]
"""People the sample family (dad David, mom Ana) lacks: a son, a daughter, a grandmother."""


def _prop(pid: str, name: str, size: str, color: str, **kw: object) -> schemas.Prop:
    return schemas.Prop(id=pid, family_id=FAMILY, name=name, size=size, color=color, art={"body": None}, **kw)


STATION_WAGON = _prop(
    "station_wagon", "station wagon", "vehicle", "#2e5e4e", aspect=2.0, anchor=[0.5, 1.0],
    sockets={"driver": [0.42, 0.55], "passenger": [0.6, 0.55], "back_seat": [0.76, 0.55]},
)
"""Three seats: a generated vehicle gets only driver and passenger, and the family is three."""
PICNIC_TABLE = _prop("picnic_table", "picnic table", "furniture", "#9b7b4f", aspect=1.8, anchor=[0.5, 1.0],
                     sockets={"top": [0.5, 0.25]})
"""A table top to set the cake on (a generated furniture prop only has a seat)."""
STOVE = _prop("stove", "stove", "furniture", "#dedede", aspect=0.9, anchor=[0.5, 1.0], sockets={"burner": [0.5, 0.2]})
"""A burner for the soup pot."""


# --------------------------------------------------------------------------- the corpus


@dataclass(frozen=True)
class Story:
    name: str
    teller: str
    participants: list[str]
    text: str
    year: int
    place: str
    prop: str
    prop_size: str
    extra_props: tuple[tuple[str, str], ...] = ()
    seed_props: tuple[schemas.Prop, ...] = ()
    verbs: frozenset[str] = frozenset()
    """Verbs the saved screenplay must use (the story's point, e.g. the car trip's drive)."""
    effects: frozenset[str] = frozenset()
    """Effects the saved timeline must play."""
    shots: int = 2
    extra: dict[str, object] = field(default_factory=dict)

    def fake_story(self) -> FakeStory:
        return FakeStory(
            place=self.place, prop=self.prop, prop_size=self.prop_size, extra_props=self.extra_props,
            max_cast=1 + len(self.participants), screenplay=STORY_BUILDERS[self.name],
        )


STORIES = [
    Story(
        "two_people_meeting", "dad", ["mom"],
        "It was pouring at the bus stop on Fifth Street. Ana was the only one there with an umbrella. "
        "I ran up soaking wet and she just laughed. Then she held it out over both of us. We missed two buses talking.",
        1984, "bus stop", "umbrella", "carried",
        verbs=frozenset({"enter", "run_to", "hand_to"}), effects=frozenset({"rain", "time_of_day"}),
    ),
    Story(
        "car_trip", "dad", ["mom", "sam"],
        "Every summer we drove the old station wagon up to the lake. Ana packed the picnic basket and Sam "
        "climbed in the back. I drove the whole way with the windows down. When we pulled up at the water, "
        "Sam was out before the engine stopped.",
        1996, "lake road", "station wagon", "vehicle", extra_props=(("picnic basket", "carried"),),
        seed_props=(STATION_WAGON,), verbs=frozenset({"get_in", "drive_to", "ride_to", "get_out"}),
    ),
    Story(
        "birthday", "mom", ["sam", "dad"],
        "For Sam's seventh birthday I carried the cake out to the picnic table. Seven candles, and I was "
        "terrified they would blow out. Sam leaned in and blew them all out in one go. David threw confetti "
        "over everybody.",
        1997, "back yard", "birthday cake", "carried", extra_props=(("picnic table", "furniture"),),
        seed_props=(PICNIC_TABLE,), verbs=frozenset({"place_on", "hug"}), effects=frozenset({"confetti"}),
    ),
    Story(
        "wedding", "mom", ["dad"],
        "On our wedding day I walked down the aisle with a bouquet of daisies. David was waiting at the altar, "
        "shaking. He forgot every word of his vows and just hugged me. Everyone threw confetti on the church steps.",
        1986, "chapel", "bouquet", "hand",
        verbs=frozenset({"enter", "walk_to", "hug"}), effects=frozenset({"confetti"}),
    ),
    Story(
        "cooking_together", "rosa", ["lily"],
        "Every Sunday Lily helped me make the soup. The pot was always steaming on the stove. I handed her the "
        "wooden spoon and showed her how to stir. She tasted it and made the funniest face.",
        2001, "kitchen", "soup pot", "carried", extra_props=(("stove", "furniture"), ("wooden spoon", "hand")),
        seed_props=(STOVE,), verbs=frozenset({"hand_to"}), effects=frozenset({"steam"}),
    ),
    Story(
        "first_bike", "dad", ["lily"],
        "I taught Lily to ride her red bicycle in the park. I held the seat and ran beside her the whole way. "
        "Then I let go and she didn't even notice. She rode all the way to the pond on her own.",
        1999, "park path", "red bicycle", "vehicle",
        verbs=frozenset({"get_in", "drive_to", "run_to", "get_out"}),
    ),
    Story(
        "moving_house", "mom", ["dad"],
        "The day we moved into the house on Birch Lane it rained boxes. David carried the heaviest one up the "
        "walk. I took the box of dishes and prayed the whole way. We set them down on the porch and sat on the steps.",
        2003, "new house", "moving box", "carried", extra_props=(("box of dishes", "carried"),),
        verbs=frozenset({"pick_up", "walk_to", "place_on"}),
    ),
    Story(
        "one_person", "mom", [],
        "After David's mother died I kept her roses going. Every evening I filled the old watering can and "
        "walked down the row. I talked to them the way she used to.",
        2010, "garden", "watering can", "carried",
        verbs=frozenset({"walk_to", "hold"}), effects=frozenset({"time_of_day"}), shots=1,
    ),
]


# --------------------------------------------------------------------------- harness


class Studio:
    """One family's RunManager from agent.wiring on the real compiler, and the repo behind it."""

    def __init__(self, story: Story) -> None:
        self.story = story
        self.repo = InMemoryRepo()
        load_sample_family(self.repo, FAMILY)
        for person in PEOPLE:
            self.repo.add_person(FAMILY, person)
        for prop in story.seed_props:
            self.repo.save_prop(FAMILY, prop)
        self.services = wiring.fake_services(model=fake_studio_model(story.fake_story()), repo=self.repo)
        self.manager = wiring.make_manager(self.services)

    def names(self, run_id: str) -> list[str]:
        return [name for name, _ in self.manager.events(FAMILY, run_id)]

    def wait(self, run_id: str) -> schemas.RunStatus:
        status = self.manager.wait(FAMILY, run_id, timeout=WAIT)
        assert status != "running", f"run still running after {WAIT}s: {self.names(run_id)}"
        return status

    def problems(self, run_id: str) -> str:
        """Every compile error and run error the run reported, for a failure message."""
        out = []
        for name, payload in self.manager.events(FAMILY, run_id):
            if name == "compile_error":
                out += [f"compile_error round {payload.round}: {e.code}: {e.message}" for e in payload.errors]  # type: ignore[attr-defined]
            elif name == "error":
                out.append(f"error: {payload.model_dump()}")
        return "\n".join(out) or "(no compile or run errors)"

    def run(self) -> schemas.Memory:
        """Tell the story, keep every prop the prop master found, and return the saved memory."""
        s = self.story
        form = schemas.SubmitMemoryFields(text=s.text, year=s.year, participant_ids=list(s.participants))
        run_id = self.manager.start_run(FAMILY, f"user_{s.teller}", s.teller, form, [])
        assert self.wait(run_id) == "waiting", (self.names(run_id), self.problems(run_id))
        found = [p for n, p in self.manager.events(FAMILY, run_id) if n == "props_found"]
        assert len(found) == 1, self.names(run_id)
        # Every object of the story, found or made, in the one review.
        assert len(found[0].props) == 1 + len(s.extra_props), found[0].props  # type: ignore[attr-defined]
        self.manager.answer_run(FAMILY, run_id, f"user_{s.teller}", schemas.AnswerRequest(kind="props", props=[]))
        return self._finish(run_id)

    def retell(self, memory_id: str, teller: str, text: str) -> schemas.Memory:
        """A second telling of a saved memory by `teller`; returns the memory it saved."""
        form = schemas.SubmitMemoryFields(text=text, year=self.story.year, memory_id=memory_id,
                                          participant_ids=[self.story.teller])
        run_id = self.manager.start_run(FAMILY, f"user_{teller}", teller, form, [])
        return self._finish(run_id)

    def tool_results(self, name: str) -> list[str]:
        """Every distinct result the model saw from tool `name`, in the order first seen."""
        seen: dict[str, str] = {}
        for _, messages in self.services.model.calls:  # type: ignore[attr-defined]
            for m in messages:
                if isinstance(m, ToolMessage) and m.name == name and m.tool_call_id not in seen:
                    seen[m.tool_call_id] = m.text if isinstance(m.text, str) else str(m.content)
        return list(seen.values())

    def _finish(self, run_id: str) -> schemas.Memory:
        status = self.wait(run_id)
        events = self.manager.events(FAMILY, run_id)
        assert status == "done", (status, [n for n, _ in events], self.problems(run_id))
        done = events[-1][1]
        assert isinstance(done, schemas.DoneEvent) and done.memory_id, [n for n, _ in events]
        memory = self.repo.get_memory(FAMILY, done.memory_id)
        assert memory is not None, done.memory_id
        # Round-trip through the API contract's JSON so the saved shape is what the web reads.
        return schemas.Memory.model_validate(memory.model_dump(mode="json", by_alias=True))


@pytest.fixture(scope="module")
def real_compiler() -> None:
    if isinstance(load_compiler(), FakeCompiler):
        pytest.skip("screenplay.compiler.compile is not importable; the corpus needs the real compiler")


@pytest.fixture
def studios() -> Iterator[list[Studio]]:
    made: list[Studio] = []
    yield made
    for s in made:
        s.manager.shutdown(wait=True)


# --------------------------------------------------------------------------- checks


def _shot_of(timeline: Timeline, t: float) -> str | None:
    """The id of the timeline shot holding the page at timeline time `t`."""
    holder = None
    for shot in timeline.shots:
        if shot.start - EPS <= t:
            holder = shot.id
    return holder


def check_scene(story: Story, memory: schemas.Memory, repo: InMemoryRepo) -> None:
    assert memory.screenplay is not None and memory.timeline is not None
    sp: Screenplay = memory.screenplay
    tl = Timeline.model_validate(memory.timeline.model_dump(mode="json", by_alias=True))
    teller = story.teller

    # The director's draft is what was saved: its shots, cast and story verbs and effects.
    assert len(sp.shots) == story.shots
    assert [s.id for s in tl.shots] == [s.id for s in sp.shots]
    assert sp.cast[0] == teller and set(sp.cast) == {teller, *story.participants}
    used = {a.do for shot in sp.shots for a in shot.actions}
    assert story.verbs <= used, f"verbs missing: {sorted(story.verbs - used)}"
    played = {e.kind for e in tl.effects}
    assert story.effects <= played, f"effects missing from the timeline: {sorted(story.effects - played)}"

    # pageText on every shot, in the screenplay and in the timeline the stage plays.
    assert all((s.page_text or "").strip() for s in sp.shots), [s.page_text for s in sp.shots]
    assert all((s.page_text or "").strip() for s in tl.shots), [s.page_text for s in tl.shots]

    # One explanation for every cast member and every prop.
    wanted = {f"puppet:{p}" for p in sp.cast} | {f"prop:{p}" for p in sp.props}
    explained = [e.target for e in tl.explanations or sp.explanations or []]
    assert set(explained) == wanted and len(explained) == len(wanted), (explained, wanted)
    assert all(e.text.strip() for e in tl.explanations or [])

    # A pull tab whose scrub targets are tracks the timeline has, in a shot that exists.
    targets = {track.target for track in tl.tracks}
    tabs = [i for i in tl.interactions or [] if i.kind == "pull_tab"]
    assert tabs, tl.interactions
    for tab in tabs:
        assert tab.shot in {s.id for s in tl.shots}
        if tab.drives.type == "scrub":
            assert tab.drives.targets and set(tab.drives.targets) <= targets, (tab.drives.targets, sorted(targets))
            assert tab.drives.from_ is not None and tab.drives.to is not None
            assert 0 <= tab.drives.from_ < tab.drives.to <= tl.duration + EPS

    # The teller's narration: lines over every shot, at least one cue, word timings on every line.
    assert tl.default_narrator == teller and tl.narrations and teller in tl.narrations
    track = tl.narrations[teller]
    assert track.audio and repo.get_media(FAMILY, track.audio) is not None
    assert all(line.words and all(w.word for w in line.words) for line in track.lines), track.lines
    assert any(line.cue for line in track.lines)
    spoken = {_shot_of(tl, line.t) for line in track.lines}
    assert spoken == {s.id for s in tl.shots}, f"shots with no narration: {sorted({s.id for s in tl.shots} - spoken)}"
    for shot in sp.shots:
        assert shot.narration and all(line.words for line in shot.narration), shot.id
        assert all((line.source or "").startswith(f"telling:{teller}:s") for line in shot.narration)
        assert shot.narration[0].cue in {a.id for a in shot.actions if a.id}
    for earlier, later in zip(track.lines, track.lines[1:], strict=False):
        assert earlier.words and later.t > earlier.words[-1].t, "narration lines overlap"

    # Camera: tracks exist, every cue is a vocabulary move that starts inside its shot.
    assert any(track.target == "camera" for track in tl.tracks)
    moves = [cue.move for shot in sp.shots for cue in shot.camera]
    assert moves and set(moves) <= CAMERA_MOVES, moves
    if len(sp.shots) > 1:
        assert len(set(moves)) >= 2, moves
    windows = {s.id: s for s in tl.shots}
    for shot in sp.shots:
        ts = windows[shot.id]
        for cue in shot.camera:
            at = ts.action_start + cue.t
            assert ts.start - EPS <= at <= ts.end + EPS, f"{shot.id}: camera {cue.move} at {at} outside {ts.start}..{ts.end}"
        # The compiler moves cued actions onto their spoken words (the compile tool carries the
        # camera cues along); the shot still plays as blocked only if they move a little.
        retimed = retime_actions(shot, ts.action_start)
        drift = max(abs(new - a.t) for new, a in zip(retimed, shot.actions, strict=True))
        assert drift <= MAX_DRIFT, f"{shot.id}: the narration moves the action {drift:.2f} s from where it was written"

    # Nothing left for the scene review to ask for.
    props = {pid: p for pid in sp.props if (p := repo.get_prop(FAMILY, pid)) is not None}
    assert set(props) == set(sp.props)
    assert review_scene(sp, props=props) == []
    assert review_cue_drift(sp) == []
    assert review_timeline(sp, tl, props=props) == []


def check_tools(studio: Studio) -> None:
    """What the agents were told on the way: every successful compile came back without scene
    review warnings, and the voice pass recompiled on the real word timings with no warning."""
    compiled = [r for r in studio.tool_results("compile_screenplay") if r.startswith("ok:")]
    assert compiled, studio.tool_results("compile_screenplay")
    assert all("warnings" not in r for r in compiled), compiled
    voiced = studio.tool_results("synthesize_narration")
    assert voiced and all(r.startswith("Narrated") for r in voiced), voiced
    assert all("warning" not in r and "Recompiled with the spoken word timings" in r for r in voiced), voiced


# --------------------------------------------------------------------------- tests


def test_the_corpus_covers_the_stories_the_brief_names() -> None:
    names = {s.name for s in STORIES}
    assert names >= {"two_people_meeting", "car_trip", "birthday", "wedding", "cooking_together",
                     "first_bike", "moving_house", "one_person"}
    assert names <= set(STORY_BUILDERS)
    assert any(not s.participants for s in STORIES)


@pytest.mark.parametrize("story", STORIES, ids=[s.name for s in STORIES])
def test_story_end_to_end_on_the_real_compiler(story: Story, real_compiler: None, studios: list[Studio]) -> None:
    studio = Studio(story)
    studios.append(studio)
    memory = studio.run()
    check_scene(story, memory, studio.repo)
    check_tools(studio)


GROOM_TEXT = (
    "I watched her come down that aisle and my knees went. "
    "I hugged her before the priest said a word. "
    "Outside the confetti got in everybody's hair."
)


def test_a_second_telling_narrates_every_shot_over_the_saved_action(real_compiler: None, studios: list[Studio]) -> None:
    """The groom's telling of the two-shot wedding: his lines over both shots, the action untouched."""
    story = next(s for s in STORIES if s.name == "wedding")
    studio = Studio(story)
    studios.append(studio)
    first = studio.run()
    assert first.timeline is not None and first.screenplay is not None

    memory = studio.retell(first.id, "dad", GROOM_TEXT)
    assert memory.id == first.id and memory.version == 2
    assert [t.author_id for t in memory.tellings] == ["mom", "dad"]
    sp, tl = memory.screenplay, memory.timeline
    assert sp is not None and tl is not None
    # The shared action and the first telling are exactly as saved.
    assert tl.tracks == first.timeline.tracks
    assert sp.shots == first.screenplay.shots
    assert tl.default_narrator == "mom" and tl.audio == first.timeline.audio

    # His lines: every shot, sourced to his telling, the first of each cued on that shot's
    # first named action, voiced with word timings, over the saved shots.
    mine = (sp.narrations or {})["dad"]
    assert list(mine) == [s.id for s in sp.shots]
    for shot in sp.shots:
        lines = mine[shot.id]
        assert lines and all((ln.source or "").startswith("telling:dad:s") for ln in lines)
        assert lines[0].cue in {a.id for a in shot.actions if a.id}
    track = (tl.narrations or {})["dad"]
    assert track.audio and track.audio != tl.audio
    assert all(line.words for line in track.lines)
    assert {_shot_of(tl, line.t) for line in track.lines} == {s.id for s in tl.shots}
    assert review_scene(sp) == []
    assert review_timeline(sp, tl) == []
    check_tools(studio)


def test_review_warns_when_a_cue_moves_its_action_far_from_where_it_was_written() -> None:
    """A narration cue that moves its action far from where it was written: the camera cues go
    with it (agent.tools.cue_camera), but the shot no longer plays as blocked, so
    review_cue_drift says where to start the line instead, and compile_screenplay passes it on
    with its other warnings. The corpus drafts get no such warning (above)."""
    ctx = {"teller": "mom", "names": {"mom": "Ana"}, "cast": ["mom"], "setId": "garden",
           "props": [{"id": "watering_can", "name": "watering can", "size": "carried"}]}
    draft = STORY_BUILDERS["one_person"](ctx)
    shot = draft["shots"][0]  # walk_to "down_the_row" at t=2.1, framed by a pan_to at 2.1
    shot["narration"] = [{"t": 2.0, "text": "Every evening I walked the row.", "source": "telling:mom:s2",
                          "cue": "down_the_row"}]
    (warning,) = review_cue_drift(Screenplay.model_validate(draft))
    assert warning == (
        'shot s1, narration line 1: cue "down_the_row" moves its action 1.15s later than written '
        "(t=2.1 to t=3.25); "
        "start the line at t=0.85 so its cue word falls at t=2.1"
    )
    compiled = compile_tool._Compiled(CompileResult(ok=True), Screenplay.model_validate(draft))
    assert warning in compile_tool._review(compiled)
    shot["narration"][0]["t"] = 0.85
    assert review_cue_drift(Screenplay.model_validate(draft)) == []

    # Too early to start that far back: cue an earlier word instead.
    shot["narration"][0].update(
        t=0.0, text="Every single evening after supper I walked slowly down the long row of her old roses.")
    (warning,) = review_cue_drift(Screenplay.model_validate(draft))
    assert "moves its action 1.65s later than written (t=2.1 to t=3.75)" in warning
    assert warning.endswith("cue an earlier word (cueWord), or give the action a later t, so the cue word falls on it")

    # Only the opening wide shot: the drift is still worth a warning (busy collisions, a
    # stretched shot), camera move or not.
    shot["camera"] = [{"t": 0, "move": "wide"}]
    (warning,) = review_cue_drift(Screenplay.model_validate(draft))
    assert "moves its action 1.65s later than written (t=2.1 to t=3.75)" in warning
