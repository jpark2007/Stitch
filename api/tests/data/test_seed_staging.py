"""Staging of the demo family's seeded scenes (seed/demo_family/), checked geometrically.

The seeded moments are the demo, so their blocking is locked in here, over the timelines the
seed compiles with the real compiler (seed.demo.compile_demo_scenes). Every shot is sampled
every SAMPLE_STEP seconds, from its start to its end, the way the stage plays the tracks
(shared/CONTRACTS.md: linear between keys, held outside them, step for facing, parent and
state, and a prop's position steps at every parent change), and checked for:

(a) every puppet in the shot standing inside the stage, except while it walks to or from a
    mark off the stage, or waits off stage for its entrance;
(b) no two standing puppets on top of each other for long while both stand still, unless
    they are hugging or handing something over to each other;
(c) every prop in a hand parented to a puppet that is in the shot (a worn hat may leave
    with its wearer), and every prop on the ground resting on the floor or on a mark's
    surface, never floating;
(d) the giver and taker facing each other during a hand_to, and anyone hugging, waving,
    pointing or leaning at a person facing that person;
(e) no long stretch of a shot where nothing on stage changes at all;
(f) nobody popping from one place to another on stage instead of walking there, within a
    shot or between two shots on the same set (where no fold hides the change);
(g) no prop standing on stage in a shot that never names it (a prop placed for a later
    shot, waiting in plain view).

A failure names the moment, the shot, the timeline time and the people involved.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from schemas import SetDef
from screenplay.body import PASS_SECONDS, POSE_SECONDS, RETURN_SECONDS
from screenplay.narration import default_author, retime_actions
from screenplay.schema import Screenplay, Timeline, Track
from screenplay.verbs_body import HUG_SECONDS
from seed import demo

# ----------------------------------------------------------------------- thresholds

# Sample every tenth of a second: shorter than any blend in the rig (POSE_SECONDS is 0.4 s).
SAMPLE_STEP = 0.1
# Stage x runs -1..1 (CONTRACTS.md "Stage space"); offstage marks sit beyond +-1.
STAGE_EDGE = 1.0
# A puppet is about 0.15 stage units wide (0.3 x its 0.5 height), so half of it is 0.075.
PUPPET_HALF_WIDTH = 0.075
# A standing puppet must keep its whole body on stage: root |x| at most this.
IN_BOUNDS_X = STAGE_EDGE - PUPPET_HALF_WIDTH
# Roots closer than one body width overlap visibly (hugs stand 0.18 apart, verbs_body.HUG_GAP).
MIN_SEPARATION = 0.15
# Even hugging or handing something over, two bodies never share a spot: less than this is one on the other.
CONTACT_SEPARATION = 0.1
# Puppets whose z differs by more than this stand at visibly different depths (a set layer is 0.35 deep).
SAME_DEPTH = 0.1
# Two puppets may brush past each other; standing on top of each other longer than this is a staging bug.
OVERLAP_TOLERANCE = 0.5
# A root that moves less than this between samples is standing still.
STILL_EPS = 1e-4
# A ground prop rests on the floor or a mark's surface within this height.
SURFACE_TOLERANCE = 0.02
# Walk 0.35, run 0.8 and drive 0.9 stage units/s (vocabulary.json speeds) cover at most 0.09 per sample;
# a root that jumps farther than this between two samples on stage teleports.
POP_DISTANCE = 0.15
# A held tableau longer than this, with no puppet, prop or set track changing, reads as a frozen page.
MAX_DEAD_TIME = 3.0
# Facing is checked this long after a gesture starts, once its turn has played.
FACING_SETTLE = 0.05
# Too close in x to say which way someone should face.
FACING_DX = 0.02

STILL_GESTURES = ("wave", "point_at", "lean_toward")


# -------------------------------------------------------------------------- samples


def _keys(track: Track | None) -> list[tuple[float, object]]:
    return [] if track is None else [(float(k[0]), k[1]) for k in track.keys]


def _sample(keys: list[tuple[float, object]], t: float, *, step: bool = False, breaks: tuple[float, ...] = ()):
    """The stage's value at t: hold outside the keys, step or linear between them, and never
    interpolate across a break (a parent change). At a repeated key time the last key wins."""
    if not keys:
        return None
    before = [k for k in keys if k[0] <= t + 1e-9]
    if not before:
        return keys[0][1]
    t0, v0 = before[-1]
    after = [k for k in keys if k[0] > t + 1e-9]
    if step or not after or isinstance(v0, str):
        return v0
    t1, v1 = after[0]
    if any(t0 < b <= t1 + 1e-9 for b in breaks) and t < t1:
        return v0
    s = (t - t0) / (t1 - t0)
    if isinstance(v0, list):
        return [a + (b - a) * s for a, b in zip(v0, v1, strict=True)]
    return float(v0) + (float(v1) - float(v0)) * s


class Scene:
    """One compiled moment, with the stage's sampling of its tracks."""

    def __init__(self, memory_id: str, screenplay: Screenplay, timeline: Timeline) -> None:
        self.id = memory_id
        self.sp = screenplay
        self.tl = timeline
        self.tracks = {(t.target, t.channel): t for t in timeline.tracks}
        self.cast = list(screenplay.cast)
        self.props = list(screenplay.props)

    def keys(self, target: str, channel: str) -> list[tuple[float, object]]:
        return _keys(self.tracks.get((target, channel)))

    def root(self, who: str, t: float) -> tuple[float, float, float] | None:
        v = _sample(self.keys(f"puppet:{who}", "root.position"), t)
        return None if v is None else (float(v[0]), float(v[1]), float(v[2]))

    def facing(self, who: str, t: float) -> int:
        v = _sample(self.keys(f"puppet:{who}", "root.facing"), t, step=True)
        return -1 if v == -1 else 1

    def bone(self, who: str, bone: str, t: float) -> float:
        v = _sample(self.keys(f"puppet:{who}", f"{bone}.rot"), t)
        return 0.0 if v is None else float(v)

    def seated(self, who: str, t: float) -> bool:
        """Both thighs forward, as in the sit pose (shared/rig/poses.json: leg upper 1.5)."""
        return all(abs(self.bone(who, f"leg_{s}.upper", t) - 1.5) < 0.05 for s in "lr")

    def moving(self, who: str, t: float) -> bool:
        """Walking, running or riding: the root moves across the stage or in depth around t.
        Sitting, kneeling and bending only move it up and down, which does not count."""
        a, b = self.root(who, t - SAMPLE_STEP), self.root(who, t + SAMPLE_STEP)
        return a is not None and b is not None and max(abs(a[0] - b[0]), abs(a[2] - b[2])) > STILL_EPS

    def prop_parent(self, prop: str, t: float) -> str:
        v = _sample(self.keys(f"prop:{prop}", "parent"), t, step=True)
        return "ground" if v is None else str(v)

    def prop_position(self, prop: str, t: float) -> tuple[float, float, float]:
        breaks = tuple(k[0] for k in self.keys(f"prop:{prop}", "parent"))
        v = _sample(self.keys(f"prop:{prop}", "position"), t, breaks=breaks)
        return (0.0, 0.0, 0.5) if v is None else (float(v[0]), float(v[1]), float(v[2]))

    def run_of(self, who: str, t: float) -> tuple[float, float]:
        """The x where the puppet's current movement started and where it ends: walk back and
        forward from t while the root keeps moving."""
        t0 = t
        while t0 - SAMPLE_STEP >= 0 and self.moving(who, t0 - SAMPLE_STEP):
            t0 -= SAMPLE_STEP
        t1 = t
        while t1 + SAMPLE_STEP <= self.tl.duration and self.moving(who, t1 + SAMPLE_STEP):
            t1 += SAMPLE_STEP
        a, b = self.root(who, t0 - SAMPLE_STEP), self.root(who, t1 + SAMPLE_STEP)
        return (a[0] if a else 0.0), (b[0] if b else 0.0)

    def on_stage(self, who: str, t: float) -> bool:
        r = self.root(who, t)
        return r is not None and abs(r[0]) < STAGE_EDGE + PUPPET_HALF_WIDTH


@dataclass(frozen=True)
class Shot:
    scene: Scene
    index: int
    id: str
    set: str
    start: float
    action_start: float
    end: float

    def times(self) -> Iterator[float]:
        n = int(math.floor((self.end - self.start) / SAMPLE_STEP))
        for i in range(n + 1):
            t = round(self.start + i * SAMPLE_STEP, 6)
            if t < self.end:
                yield t

    def where(self, t: float) -> str:
        return f"{self.scene.id} shot {self.id} at t={t:.2f} (shot t={t - self.action_start:+.2f})"

    def present(self) -> set[str]:
        """Everyone who is on stage at some point of the shot."""
        return {who for who in self.scene.cast if any(self.scene.on_stage(who, t) for t in self.times())}

    def surfaces(self) -> list[float]:
        """Standing heights of the shot's marks (the set's, overridden or extended per shot)."""
        set_def = _sets()[self.set]
        sp_shot = self.scene.sp.shots[self.index]
        marks = {**set_def.marks, **sp_shot.marks}
        return sorted({0.0, *(float(m[1]) for m in marks.values())})


@dataclass(frozen=True)
class Act:
    """An authored action, at its compiled (cue-retimed) timeline time."""

    t: float
    do: str
    who: str | None
    other: str | None
    dur: float | None


def _acts(scene: Scene, shot: Shot) -> list[Act]:
    sp_shot = scene.sp.shots[shot.index]
    retimed = retime_actions(sp_shot, shot.action_start, author=default_author(scene.sp))
    out = []
    for action, t in zip(sp_shot.actions, retimed, strict=True):
        other = action.to if action.do == "hand_to" else action.target
        out.append(Act(shot.action_start + t, action.do, action.who, other, action.dur))
    return out


def _shots(scene: Scene) -> list[Shot]:
    return [
        Shot(scene, i, s.id, s.set, s.start, s.action_start, s.end) for i, s in enumerate(scene.tl.shots)
    ]


# --------------------------------------------------------------------------- fixtures

MEMORY_IDS = list(demo.DEMO_MEMORY_IDS)


@functools.cache
def _data() -> demo.DemoData:
    """Every moment compiled once, with the real compiler, exactly as the seed stores it."""
    return demo.load_demo_data()


@functools.cache
def _sets() -> dict[str, SetDef]:
    return {s.id: s for s in _data().sets}


@pytest.fixture(params=MEMORY_IDS)
def scene(request) -> Scene:
    compiled = _data().scenes[request.param]
    return Scene(request.param, compiled.screenplay, compiled.timeline)


# --------------------------------------------------------------------------- windows


def _contact_windows(scene: Scene, shot: Shot) -> list[tuple[frozenset[str], float, float]]:
    """(pair, from, to) for every authored hug and hand_to: while one runs, the two people
    may stand closer than MIN_SEPARATION. A hug's window covers the walk up to the partner,
    the hug itself and the moment the arms settle."""
    out = []
    for act in _acts(scene, shot):
        if act.do not in ("hug", "hand_to") or act.who is None or act.other is None:
            continue
        t = act.t
        while t + SAMPLE_STEP < shot.end and scene.moving(act.who, t + SAMPLE_STEP):
            t += SAMPLE_STEP  # the walk up to the partner
        hold = (act.dur or HUG_SECONDS) if act.do == "hug" else PASS_SECONDS + RETURN_SECONDS
        out.append((frozenset((act.who, act.other)), act.t, t + hold + POSE_SECONDS))
    return out


# ------------------------------------------------------------------------------ rules


def _report(problems: list[tuple[str, str]]) -> None:
    """Fail with each problem once: (key, message), the first sample of each key, and how many
    samples repeat it."""
    first: dict[str, str] = {}
    count: dict[str, int] = {}
    for key, message in problems:
        first.setdefault(key, message)
        count[key] = count.get(key, 0) + 1
    lines = [first[k] + (f" ({count[k]} samples)" if count[k] > 1 else "") for k in first]
    assert not lines, "\n".join(lines)


def test_every_shot_is_sampled(scene: Scene):
    """The sampling covers the whole timeline, shot after shot."""
    shots = _shots(scene)
    assert shots and shots[0].start == 0.0
    for a, b in zip(shots, shots[1:]):
        assert a.end == pytest.approx(b.start), f"{scene.id}: {a.id} ends at {a.end}, {b.id} starts at {b.start}"
    assert shots[-1].end == pytest.approx(scene.tl.duration)


def test_a_puppets_stay_inside_the_stage(scene: Scene):
    """(a) Root x within IN_BOUNDS_X, unless walking to or from an off-stage mark, or waiting
    wholly off stage (not yet entered, or gone)."""
    problems: list[tuple[str, str]] = []
    for shot in _shots(scene):
        for t in shot.times():
            for who in scene.cast:
                r = scene.root(who, t)
                if r is None or abs(r[0]) <= IN_BOUNDS_X:
                    continue
                if scene.moving(who, t):
                    x0, x1 = scene.run_of(who, t)
                    if max(abs(x0), abs(x1)) >= STAGE_EDGE:
                        continue  # walking on or off
                    message = f"{shot.where(t)}: {who} walks past the stage edge (x={r[0]:+.2f})"
                    problems.append((f"{shot.id} {who} past", message))
                elif abs(r[0]) < STAGE_EDGE + PUPPET_HALF_WIDTH:
                    message = f"{shot.where(t)}: {who} stands half off stage at x={r[0]:+.2f}"
                    problems.append((f"{shot.id} {who} half", message))
    _report(problems)


def test_b_nobody_stands_on_top_of_anyone(scene: Scene):
    """(b) Two standing, still puppets at the same depth keep MIN_SEPARATION apart, apart from
    brief brushes (OVERLAP_TOLERANCE) and authored hugs and hand-offs between those two."""
    problems: list[tuple[str, str]] = []
    for shot in _shots(scene):
        windows = _contact_windows(scene, shot)
        since: dict[frozenset[str], float] = {}
        for t in shot.times():
            standing = [w for w in scene.cast if scene.on_stage(w, t) and not scene.seated(w, t)]
            close_now = set()
            for i, a in enumerate(standing):
                for b in standing[i + 1:]:
                    ra, rb = scene.root(a, t), scene.root(b, t)
                    assert ra is not None and rb is not None
                    pair = frozenset((a, b))
                    dx = abs(ra[0] - rb[0])
                    if dx >= MIN_SEPARATION or abs(ra[2] - rb[2]) > SAME_DEPTH:
                        continue
                    if scene.moving(a, t) or scene.moving(b, t):
                        continue
                    touching = any(p == pair and lo <= t <= hi for p, lo, hi in windows)
                    if touching and dx >= CONTACT_SEPARATION:
                        continue
                    close_now.add(pair)
                    first = since.setdefault(pair, t)
                    if t - first > OVERLAP_TOLERANCE:
                        problems.append((
                            f"{shot.id} {sorted(pair)} {first}",
                            f"{shot.where(first)}: {a} and {b} stand on top of each other "
                            f"(x {ra[0]:+.2f} and {rb[0]:+.2f}) for more than {OVERLAP_TOLERANCE} s",
                        ))
            for pair in list(since):
                if pair not in close_now:
                    del since[pair]
    _report(problems)


def test_c_held_props_belong_to_someone_in_the_shot_and_nothing_floats(scene: Scene):
    """(c) A prop in a hand is parented to a puppet in the shot, and a prop on the ground rests
    on the floor or on a mark's surface. (A prop on another prop's socket moves with it.)"""
    problems: list[tuple[str, str]] = []
    for shot in _shots(scene):
        present = shot.present()
        surfaces = shot.surfaces()
        for t in shot.times():
            for prop in scene.props:
                parent = scene.prop_parent(prop, t)
                if parent.startswith("puppet:"):
                    who, _, socket = parent[len("puppet:"):].partition(".")
                    if socket != "head" and who not in present:  # a worn hat leaves with its wearer
                        message = f"{shot.where(t)}: {prop} is held by {who}, who is not in the shot"
                        problems.append((f"{shot.id} {prop} {who}", message))
                elif parent == "ground":
                    x, y, _z = scene.prop_position(prop, t)
                    if abs(x) > STAGE_EDGE:
                        continue  # put away off stage
                    if not _settling(scene, prop, t) and min(abs(y - s) for s in surfaces) > SURFACE_TOLERANCE:
                        message = f"{shot.where(t)}: {prop} floats at height {y:.2f} (x={x:+.2f})"
                        problems.append((f"{shot.id} {prop} {y:.2f}", message))
    _report(problems)


def _settling(scene: Scene, prop: str, t: float) -> bool:
    """A dropped prop falling, or a ground prop being moved: its position is changing at t."""
    a, b = scene.prop_position(prop, t - SAMPLE_STEP), scene.prop_position(prop, t + SAMPLE_STEP)
    return max(abs(p - q) for p, q in zip(a, b, strict=True)) > STILL_EPS


def test_d_people_face_whoever_they_hand_to_or_gesture_at(scene: Scene):
    """(d) A hand_to's giver and taker face each other while the hands meet; a hugger and the
    person hugged face each other; a wave, point or lean at a person faces that person."""
    problems: list[tuple[str, str]] = []
    for shot in _shots(scene):
        for act in _acts(scene, shot):
            if act.who is None or act.other not in scene.cast:
                continue
            if act.do == "hand_to":
                t = act.t + PASS_SECONDS / 2  # the hands are meeting
                pairs = [(act.who, act.other), (act.other, act.who)]
            elif act.do == "hug":
                t = act.t
                while t + SAMPLE_STEP < shot.end and scene.moving(act.who, t + SAMPLE_STEP):
                    t += SAMPLE_STEP
                t += POSE_SECONDS  # into the hug, after the walk up
                pairs = [(act.who, act.other), (act.other, act.who)]
            elif act.do in STILL_GESTURES:
                t = act.t + FACING_SETTLE + min(act.dur or 1.0, 1.0) / 2
                pairs = [(act.who, act.other)]
            else:
                continue
            for a, b in pairs:
                ra, rb = scene.root(a, t), scene.root(b, t)
                if ra is None or rb is None or abs(rb[0] - ra[0]) < FACING_DX:
                    continue
                want = 1 if rb[0] > ra[0] else -1
                if scene.facing(a, t) != want:
                    message = f"{shot.where(t)}: during {act.who}'s {act.do} with {act.other}, {a} faces away from {b}"
                    problems.append((message, message))
    _report(problems)


def _stage_tracks(scene: Scene) -> list[list[tuple[float, object]]]:
    return [_keys(t) for t in scene.tl.tracks if t.target.split(":", 1)[0] in ("puppet", "prop", "set")]


def _changing(keys: list[tuple[float, object]], t0: float, t1: float) -> bool:
    """The track's value is not constant over [t0, t1]: it differs at the two ends, or a key
    inside the span holds another value."""
    values = [_sample(keys, t0), *(v for k, v in keys if t0 + 1e-9 < k <= t1 + 1e-9), _sample(keys, t1)]
    return any(v != values[0] for v in values[1:])


def test_e_no_shot_stands_still_for_long(scene: Scene):
    """(e) Within every shot, some puppet, prop or set track changes at least every
    MAX_DEAD_TIME seconds."""
    tracks = _stage_tracks(scene)
    problems: list[tuple[str, str]] = []
    for shot in _shots(scene):
        still_from = None
        for t in shot.times():
            moving = any(_changing(keys, t, t + SAMPLE_STEP) for keys in tracks)
            if moving:
                still_from = None
                continue
            if still_from is None:
                still_from = t
            if t + SAMPLE_STEP - still_from > MAX_DEAD_TIME + 1e-9:
                message = f"{shot.where(still_from)}: nothing moves for more than {MAX_DEAD_TIME} s"
                problems.append((message, message))
                still_from = None
                break
    _report(problems)


def test_f_nobody_pops_across_the_stage(scene: Scene):
    """(f) Between two consecutive samples, a puppet on stage moves at most POP_DISTANCE. The
    first sample of a shot on a new set is skipped: the fold and the rise hide the change of
    scene, and everyone takes their new places then."""
    problems: list[tuple[str, str]] = []
    shots = _shots(scene)
    for i, shot in enumerate(shots):
        same_set = i > 0 and shots[i - 1].set == shot.set
        times = list(shot.times())
        if same_set:
            times = [times[0] - SAMPLE_STEP, *times]  # the boundary with the previous shot
        for t0, t1 in zip(times, times[1:]):
            for who in scene.cast:
                a, b = scene.root(who, t0), scene.root(who, t1)
                if a is None or b is None:
                    continue
                if min(abs(a[0]), abs(b[0])) >= STAGE_EDGE + PUPPET_HALF_WIDTH:
                    continue  # off stage the whole time: nobody sees it
                jump = math.hypot(b[0] - a[0], b[2] - a[2])
                if jump > POP_DISTANCE:
                    message = (
                        f"{shot.where(t1)}: {who} pops from x={a[0]:+.2f} to x={b[0]:+.2f} "
                        f"in {t1 - t0:.1f} s instead of walking"
                    )
                    problems.append((message, message))
    _report(problems)


def _named_in(scene: Scene, index: int) -> set[str]:
    """Every prop a screenplay shot names: placed, held at the start, or used by an action."""
    shot = scene.sp.shots[index]
    names = set(shot.prop_start)
    for start in shot.start.values():
        names |= {start.holding} | ({start.in_.split(".", 1)[0]} if start.in_ else set())
    for ps in shot.prop_start.values():
        if ps.on:
            names.add(ps.on.split(".", 1)[0])
    for action in shot.actions:
        for value in (action.what, action.target, action.at, action.on):
            if value:
                names.add(value.split(".", 1)[0])
    return names


def test_g_props_show_only_in_shots_that_use_them(scene: Scene):
    """(g) A prop standing on the ground on stage during a shot is named by that shot, or by
    an earlier shot on the same set (it stayed where it was left)."""
    problems: list[tuple[str, str]] = []
    shots = _shots(scene)
    for i, shot in enumerate(shots):
        named = _named_in(scene, i)
        j = i
        while j > 0 and shots[j - 1].set == shot.set:
            j -= 1
            named |= _named_in(scene, j)
        for prop in scene.props:
            if prop in named:
                continue
            for t in shot.times():
                if scene.prop_parent(prop, t) != "ground":
                    continue
                x, _y, _z = scene.prop_position(prop, t)
                if abs(x) <= STAGE_EDGE:
                    message = f"{shot.where(t)}: {prop} stands on stage at x={x:+.2f}, but shot {shot.id} never uses it"
                    problems.append((message, message))
                    break
    _report(problems)
