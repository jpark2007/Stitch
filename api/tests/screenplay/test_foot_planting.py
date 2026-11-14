"""Foot planting (shared/CONTRACTS.md Clarifications 1): at every walk and run cycle key the
compiler lowers root.position.y by (hipHeight - lowestFoot) * scale, so the lowest foot stays on
the standing surface, and sets Timeline.feetPlanted.

This file has its own forward kinematics, a port of web/components/stage/puppet/kinematics.ts
over shared/rig/puppet.json (it does not use screenplay.planting's), and finds the cycle keys
from the timeline alone: the leg key times where all four legs are exactly a walk or run frame of
shared/rig/poses.json. The standing surface at such a time is the root's y in the same
screenplay compiled with ``plant_feet=False`` (on a walk onto a raised mark, the lerp of the start
and end heights). ``planting_problems`` is also the compile fuzz's planting invariant.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import copy

import pytest
from story_scenes import STORIES, story_scene
from verb_cases import Scene, _a, _at, _one, base_scene

import schemas
from screenplay import ik, planting
from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import VOCABULARY, Screenplay, Timeline
from screenplay.tracks import PLANT_CHANNEL, TrackSet, evaluate

SHARED = Path(__file__).resolve().parents[3] / "shared"
RIG = json.loads((SHARED / "rig" / "puppet.json").read_text())
CYCLES = json.loads((SHARED / "rig" / "poses.json").read_text())["cycles"]
LEGS = ("leg_l.upper", "leg_l.lower", "leg_r.upper", "leg_r.lower")
BUILDS = sorted(VOCABULARY["builds"])
TOL = 2e-3


# ------------------------------------------------- forward kinematics (kinematics.ts port)

def _bone_order() -> list[str]:
    out: list[str] = []
    seen = {RIG["root"]}
    names = list(RIG["bones"])
    while len(out) < len(names):
        before = len(out)
        for n in names:
            if n not in seen and RIG["bones"][n]["parent"] in seen:
                seen.add(n)
                out.append(n)
        assert len(out) > before, "rig parents form a cycle"
    return out


ORDER = _bone_order()


def forward_kinematics(rot: dict[str, float]) -> dict[str, tuple[float, float, float]]:
    """Every bone's (pivot x, pivot y, absolute angle) in the hip frame (forwardKinematics)."""
    frames: dict[str, tuple[float, float, float]] = {}
    for n in ORDER:
        b = RIG["bones"][n]
        p = None if b["parent"] == RIG["root"] else frames[b["parent"]]
        pa = p[2] if p else 0.0
        c, s = math.cos(pa), math.sin(pa)
        px, py = b["pivot"]
        frames[n] = ((p[0] if p else 0.0) + px * c - py * s, (p[1] if p else 0.0) + px * s + py * c,
                     pa + rot.get(n, 0.0))
    return frames


def bone_tip(frames: dict[str, tuple[float, float, float]], bone: str) -> tuple[float, float]:
    b = RIG["bones"][bone]
    x, y, a = frames[bone]
    dx, dy = b["dir"][0] * b["length"], b["dir"][1] * b["length"]
    return (x + dx * math.cos(a) - dy * math.sin(a), y + dx * math.sin(a) + dy * math.cos(a))


def lowest_foot(rot: dict[str, float]) -> float:
    """lowestFoot: how far below the hip the lower foot is, rig units, never below 0."""
    frames = forward_kinematics(rot)
    return max(0.0, *(-bone_tip(frames, f)[1] for f in ("leg_l.lower", "leg_r.lower")))


def puppet_scale(build: str) -> float:
    """puppetScale: stageHeight * build / height."""
    return RIG["stageHeight"] * VOCABULARY["builds"][build] / RIG["height"]


# ------------------------------------------------------------------- timeline checks

CYCLE_FRAMES = {tuple(round(float(f.get(b, 0.0)), 3) for b in LEGS) for c in CYCLES.values() for f in c["frames"]}


def _legs_at(tl: Timeline, target: str, t: float) -> dict[str, float]:
    out = {}
    for bone in LEGS:
        v = evaluate(tl.tracks, target, f"{bone}.rot", t)
        out[bone] = 0.0 if v is None else float(v)
    return out


def cycle_key_times(tl: Timeline, target: str) -> list[float]:
    """Leg key times where the four legs are exactly a walk or run cycle frame."""
    times = sorted({k[0] for tr in tl.tracks if tr.target == target and tr.channel in {f"{b}.rot" for b in LEGS}
                    for k in tr.keys})
    return [t for t in times if tuple(round(v, 3) for v in _legs_at(tl, target, t).values()) in CYCLE_FRAMES]


def foot_errors(tl: Timeline, surface: Timeline, builds: dict[str, str]) -> list[tuple[str, float, float]]:
    """(target, t, error) at every cycle key of every puppet: how far the lowest foot of ``tl``
    is above (+) or below (-) the root y of ``surface`` (the unplanted compile)."""
    out = []
    for who, build in sorted(builds.items()):
        target = f"puppet:{who}"
        for t in cycle_key_times(tl, target):
            root = evaluate(tl.tracks, target, "root.position", t)
            ground = evaluate(surface.tracks, target, "root.position", t)
            foot = root[1] + (RIG["hipHeight"] - lowest_foot(_legs_at(tl, target, t))) * puppet_scale(build)
            out.append((target, t, foot - ground[1]))
    return out


def planting_problems(planted: Timeline, unplanted: Timeline, builds: dict[str, str]) -> list[str]:
    """Empty when ``planted`` is ``unplanted`` with the feet planted: feetPlanted set, every track
    but the puppets' root.position identical, root x and z unchanged, and at every walk / run
    cycle key the lowest foot on the surface within 2e-3."""
    out: list[str] = []
    if planted.feet_planted is not True:
        out.append(f"feet_planted is {planted.feet_planted!r}")
    if unplanted.feet_planted is not None:
        out.append(f"unplanted feet_planted is {unplanted.feet_planted!r}")
    other = {(t.target, t.channel): t for t in unplanted.tracks}
    if set(other) != {(t.target, t.channel) for t in planted.tracks}:
        out.append("planted and unplanted tracks differ in (target, channel)")
        return out
    for tr in planted.tracks:
        if tr.target.startswith("puppet:") and tr.channel == "root.position":
            for t in sorted({k[0] for k in tr.keys} | {k[0] for k in other[(tr.target, tr.channel)].keys}):
                a = evaluate(planted.tracks, tr.target, tr.channel, t)
                b = evaluate(unplanted.tracks, tr.target, tr.channel, t)
                if abs(a[0] - b[0]) > 1e-3 or abs(a[2] - b[2]) > 1e-3:
                    out.append(f"{tr.target} root x/z moved at t={t}: {a} vs {b}")
        elif tr != other[(tr.target, tr.channel)]:
            out.append(f"{tr.target} {tr.channel} changed")
    out += [f"{target} foot {err:+.4f} off the surface at t={t}"
            for target, t, err in foot_errors(planted, unplanted, builds) if abs(err) > TOL]
    rest = {"tracks": [], "feet_planted": None}
    if planted.model_copy(update=rest) != unplanted.model_copy(update=rest):
        out.append("planting changed something besides the tracks")
    return out


# ------------------------------------------------------------------------- scenes

def _recast(scene: Scene, build: str) -> Scene:
    people = {pid: p.model_copy(update={"character": p.character.model_copy(update={"build": build})})
              for pid, p in scene.people.items()}
    return Scene(scene.sets, scene.props, people)


def _compile(sp: Screenplay, scene: Scene, **kw) -> Timeline:
    res = compile_screenplay(sp, sets=scene.sets, props=scene.props, people=scene.people, **kw)
    assert res.ok, [e.message for e in res.errors]
    assert res.timeline is not None
    return res.timeline


def _with_marks(sp: Screenplay, marks: dict[str, list[float]]) -> Screenplay:
    """The screenplay with shot marks added to its first shot."""
    data = sp.model_dump(by_alias=True)
    data["shots"][0]["marks"] = marks
    return Screenplay.model_validate(data)


# name -> (screenplay, the puppet whose walk is checked)
SCENES: dict[str, tuple[Screenplay, str]] = {
    "walk_to": (_one(_a(1, "walk_to", who="dad", to="curb"), start={"dad": _at("door")}), "dad"),
    "walk_to_and_back": (_one(_a(1, "walk_to", who="dad", to="curb"), _a(7, "walk_to", who="dad", to="door"),
                              start={"dad": _at("door")}), "dad"),
    "run_to": (_one(_a(1, "run_to", who="kid", to="curb"), start={"kid": _at("door")}), "kid"),
    "enter": (_one(_a(1, "enter", who="dad", to="door", **{"from": "offstage_left"}),
                   start={"dad": _at("offstage_left")}), "dad"),
    # waits at offstage_left, enters from the right: two root keys at the enter's start (a jump)
    "enter_jump": (_one(_a(1, "enter", who="dad", to="curb", **{"from": "offstage_right"}),
                        start={"dad": _at("offstage_left")}), "dad"),
    "exit": (_one(_a(1, "exit", who="mom", to="offstage_right"), start={"mom": _at("curb")}), "mom"),
    "follow": (_one(_a(0, "walk_to", who="dad", to="curb"), _a(0, "follow", who="kid", target="dad"),
                    start={"dad": _at("beside_bench"), "kid": _at("door")}), "kid"),
    "follow_dur": (_one(_a(0, "walk_to", who="dad", to="curb"), _a(0, "follow", who="kid", target="dad", dur=2.5),
                        start={"dad": _at("beside_bench"), "kid": _at("door")}), "kid"),
    "pick_up_walk": (_one(_a(1, "pick_up", who="dad", what="cap"), start={"dad": _at("curb")},
                          prop_start={"cap": {"at": "door"}}), "dad"),
    "hug_walk_up": (_one(_a(1, "hug", who="dad", target="mom"), start={"dad": _at("door"), "mom": _at("curb")}),
                    "dad"),
    "sit_on_walk": (_one(_a(1, "sit_on", who="dad", what="chair"), start={"dad": _at("curb")},
                         prop_start={"chair": {"at": "bench_spot"}}), "dad"),
    "get_in_walk": (_one(_a(1, "get_in", who="dad", what="sedan"), start={"dad": _at("door")},
                         prop_start={"sedan": {"at": "curb"}}), "dad"),
    "raised_mark": (_with_marks(_one(_a(1, "walk_to", who="dad", to="stage"), _a(7, "run_to", who="dad", to="door"),
                                     start={"dad": _at("curb")}), {"stage": [0.4, 0.3]}), "dad"),
}


@pytest.fixture(scope="module")
def compiled() -> dict[tuple[str, str], tuple[Timeline, Timeline, dict[str, str]]]:
    out = {}
    for build in BUILDS:
        scene = _recast(base_scene(), build)
        builds = {pid: build for pid in scene.people}
        for name, (sp, _) in SCENES.items():
            out[(name, build)] = (_compile(sp, scene), _compile(sp, scene, plant_feet=False), builds)
    return out


@pytest.mark.parametrize("build", BUILDS)
@pytest.mark.parametrize("name", list(SCENES))
def test_every_cycle_key_has_the_lowest_foot_on_the_surface(compiled, name, build):
    planted, unplanted, builds = compiled[(name, build)]
    who = SCENES[name][1]
    keys = cycle_key_times(planted, f"puppet:{who}")
    assert len(keys) >= 3, f"{who} has no walk to check"
    assert planting_problems(planted, unplanted, builds) == []
    errs = [e for target, _, e in foot_errors(planted, unplanted, builds) if target == f"puppet:{who}"]
    assert len(errs) == len(keys) and max(abs(e) for e in errs) <= TOL


@pytest.mark.parametrize("build", BUILDS)
def test_without_planting_the_feet_lift_off_mid_stride(compiled, build):
    _, unplanted, builds = compiled[("walk_to", build)]
    lift = max(e for _, _, e in foot_errors(unplanted, unplanted, builds))
    assert lift > 0.01  # the widest stride lifts the feet 1.5 to 2 cm (stage units)
    planted, _, _ = compiled[("walk_to", build)]
    assert max(abs(e) for _, _, e in foot_errors(planted, unplanted, builds)) <= TOL


def test_a_run_drops_further_than_a_walk(compiled):
    walk = max(e for _, _, e in foot_errors(compiled[("walk_to", "average")][1], compiled[("walk_to", "average")][1],
                                            compiled[("walk_to", "average")][2]))
    run = max(e for _, _, e in foot_errors(compiled[("run_to", "average")][1], compiled[("run_to", "average")][1],
                                           compiled[("run_to", "average")][2]))
    assert run > walk > 0.01


def test_a_walk_onto_a_raised_mark_plants_on_the_slope(compiled):
    planted, unplanted, _ = compiled[("raised_mark", "average")]
    surface = [evaluate(unplanted.tracks, "puppet:dad", "root.position", t)[1]
               for t in cycle_key_times(planted, "puppet:dad")]
    assert any(0.01 < y < 0.29 for y in surface)  # on the way up, between the two heights
    assert planting_problems(planted, unplanted, {"dad": "average", "mom": "average", "kid": "average"}) == []


def test_the_enter_jump_still_steps(compiled):
    planted, _, _ = compiled[("enter_jump", "average")]
    (pos,) = [tr for tr in planted.tracks if (tr.target, tr.channel) == ("puppet:dad", "root.position")]
    t_enter = planted.shots[0].action_start + 1
    at = [k[1] for k in pos.keys if abs(k[0] - t_enter) < 1e-6]
    assert at == [[-1.3, 0.0, 0.5], [1.3, 0.0, 0.5]]


@pytest.mark.parametrize("build", BUILDS)
def test_a_seated_or_kneeling_puppet_is_never_planted(build):
    scene = _recast(base_scene(), build)
    sp = _one(_a(1, "kneel", who="mom"), _a(1, "sit_on", who="dad", what="chair"),
              start={"dad": _at("bench_spot"), "mom": _at("curb")}, prop_start={"chair": {"at": "bench_spot"}})
    planted, unplanted = _compile(sp, scene), _compile(sp, scene, plant_feet=False)
    assert planted.feet_planted is True
    assert planted.tracks == unplanted.tracks


def test_every_timeline_is_marked_planted_even_with_no_walks():
    scene = base_scene()
    sp = _one(_a(1, "wave", who="dad"), start={"dad": _at("door")})
    planted = _compile(sp, scene)
    assert planted.feet_planted is True
    assert json.loads(planted.model_dump_json(by_alias=True))["feetPlanted"] is True
    unplanted = _compile(sp, scene, plant_feet=False)
    assert unplanted.feet_planted is None
    assert planted.tracks == unplanted.tracks


def test_a_walk_cut_short_by_a_later_action_samples_the_final_legs():
    """A kneel re-keys dad's legs right where his follow's clipped walk ends. Planting reads the
    legs as they finally are, so every cycle key still has the foot on the ground, and the keys
    after the walk (the kneel's) are untouched."""
    scene = base_scene()
    sp = _one(_a(0, "walk_to", who="dad", to="curb"), _a(0, "follow", who="kid", target="dad", dur=2),
              _a(3, "kneel", who="kid"), start={"dad": _at("beside_bench"), "kid": _at("door")})
    planted, unplanted = _compile(sp, scene), _compile(sp, scene, plant_feet=False)
    builds = {pid: p.character.build for pid, p in scene.people.items()}
    assert cycle_key_times(planted, "puppet:kid")
    assert planting_problems(planted, unplanted, builds) == []


# ------------------------------------------------------------------ planting.lowest_foot

def test_lowest_foot_standing_is_hip_height():
    assert planting.lowest_foot({}) == pytest.approx(RIG["hipHeight"], abs=1e-12)
    assert lowest_foot({}) == pytest.approx(RIG["hipHeight"], abs=1e-12)
    assert planting.plant_drop({}, "average") == pytest.approx(0.0, abs=1e-12)


def test_lowest_foot_walk_frame_zero_matches_kinematics_test():
    legs = {"leg_l.upper": 0.45, "leg_l.lower": -0.1, "leg_r.upper": -0.35, "leg_r.lower": -0.25}
    want = 0.24 * math.cos(0.45) + 0.24 * math.cos(0.35)
    assert planting.lowest_foot(legs) == pytest.approx(want, abs=1e-9)
    assert lowest_foot(legs) == pytest.approx(want, abs=1e-9)
    assert planting.plant_drop(legs, "tall") == pytest.approx((RIG["hipHeight"] - want) * ik.scale("tall"), abs=1e-12)


def test_lowest_foot_agrees_with_the_port_on_every_cycle_frame_and_ignores_the_torso():
    for cycle in CYCLES.values():
        for frame in cycle["frames"]:
            rots = {b: float(v) for b, v in frame.items() if b != "phase"}
            assert planting.lowest_foot(rots) == pytest.approx(lowest_foot(rots), abs=1e-12)
            leaning = {**rots, "torso": 0.7, "head": -0.3, "arm_r.upper": 1.2}
            assert planting.lowest_foot(leaning) == pytest.approx(lowest_foot(rots), abs=1e-12)


def test_lowest_foot_never_goes_below_zero():
    up = {"leg_l.upper": math.pi, "leg_r.upper": math.pi}  # both legs swung overhead
    assert planting.lowest_foot(up) == 0.0 == lowest_foot(up)


# ------------------------------------------------------------------ planting.plant_feet

BENT = {"leg_l.upper": 0.45, "leg_l.lower": -0.1, "leg_r.upper": -0.35, "leg_r.lower": -0.25}
BENT_DROP = (RIG["hipHeight"] - (0.24 * math.cos(0.45) + 0.24 * math.cos(0.35))) * ik.scale("average")


def _legs(ts: TrackSet, t: float, rots: dict[str, float]) -> None:
    for bone, v in rots.items():
        ts.add("puppet:dad", f"{bone}.rot", t, v)


def _root(ts: TrackSet) -> list:
    return next([list(k) for k in tr.keys] for tr in ts.tracks() if tr.channel == "root.position")


def test_plant_feet_adds_a_lowered_key_at_a_marker_with_no_root_key():
    ts = TrackSet()
    ts.add("puppet:dad", "root.position", 0.0, [0.0, 0.0, 0.5])
    ts.add("puppet:dad", "root.position", 2.0, [1.0, 0.2, 0.5])
    _legs(ts, 0.0, dict.fromkeys(BENT, 0.0))
    _legs(ts, 1.0, BENT)
    _legs(ts, 2.0, dict.fromkeys(BENT, 0.0))
    ts.add("puppet:dad", PLANT_CHANNEL, 1.0, "walk")
    planting.plant_feet(ts, {"dad": "average"})
    assert _root(ts) == [[0.0, [0.0, 0.0, 0.5]], [1.0, [0.5, round(0.1 - BENT_DROP, 3), 0.5]], [2.0, [1.0, 0.2, 0.5]]]


def test_plant_feet_lowers_both_keys_of_a_jump_at_a_marker():
    ts = TrackSet()
    ts.add("puppet:dad", "root.position", 0.0, [0.0, 0.0, 0.5])
    ts.add("puppet:dad", "root.position", 1.0, [0.0, 0.0, 0.5])
    ts.add("puppet:dad", "root.position", 1.0, [0.8, 0.0, 0.5])  # a jump at t=1
    ts.add("puppet:dad", "root.position", 2.0, [0.8, 0.0, 0.5])
    _legs(ts, 1.0, BENT)
    ts.add("puppet:dad", PLANT_CHANNEL, 1.0, "walk")
    ts.add("puppet:dad", PLANT_CHANNEL, 1.0004, "walk")  # the same time at 3 dp: one marker
    planting.plant_feet(ts, {"dad": "average"})
    low = round(-BENT_DROP, 3)
    assert _root(ts) == [[0.0, [0.0, 0.0, 0.5]], [1.0, [0.0, low, 0.5]], [1.0, [0.8, low, 0.5]],
                         [2.0, [0.8, 0.0, 0.5]]]
    assert ts.value_at("puppet:dad", "root.position", 1.0) == [0.8, low, 0.5]  # still steps to the later key


def test_plant_feet_leaves_straight_legs_and_other_puppets_alone():
    ts = TrackSet()
    ts.add("puppet:dad", "root.position", 0.0, [0.0, 0.0, 0.5])
    ts.add("puppet:dad", "root.position", 2.0, [1.0, 0.0, 0.5])
    ts.add("puppet:dad", PLANT_CHANNEL, 1.0, "walk")  # no leg keys: standing
    ts.add("puppet:mom", "root.position", 0.0, [0.0, 0.0, 0.5])
    _legs(ts, 1.0, {})
    before = ts.tracks()
    planting.plant_feet(ts, {"dad": "average", "mom": "average"})
    assert ts.tracks() == before


# ------------------------------------------------------------------ every story, planted

STORY_TOL = 1e-3
SAMPLES = SHARED.parent / "web" / "public" / "samples"


def _story_compile(name: str, **kw) -> Timeline:
    sets, props, people = story_scene()
    res = compile_screenplay(Screenplay.model_validate(copy.deepcopy(STORIES[name])), sets=sets, props=props,
                             people=people, **kw)
    assert res.ok, [f"{e.code}: {e.message}" for e in res.errors]
    assert res.timeline is not None
    return res.timeline


def _hymn_compile(**kw) -> Timeline:
    memory = json.loads((SAMPLES / "hymn_books.memory.json").read_text())
    res = compile_screenplay(
        Screenplay.model_validate(json.loads((SAMPLES / "hymn_books.screenplay.json").read_text())),
        sets={s["id"]: schemas.SetDef.model_validate(s) for s in memory["sets"]},
        props={p["id"]: schemas.Prop.model_validate(p) for p in memory["props"]},
        people={p["id"]: schemas.Person.model_validate(p) for p in memory["people"]}, **kw)
    assert res.ok, [f"{e.code}: {e.message}" for e in res.errors]
    assert res.timeline is not None
    return res.timeline


def _story_builds() -> dict[str, str]:
    return {pid: p.character.build for pid, p in story_scene()[2].items()}


def _hymn_builds() -> dict[str, str]:
    memory = json.loads((SAMPLES / "hymn_books.memory.json").read_text())
    return {p["id"]: p["character"]["build"] for p in memory["people"]}


_PLANTING: dict[str, tuple[Timeline, Timeline, dict[str, str]]] = {}


def planted_pair(name: str) -> tuple[Timeline, Timeline, dict[str, str]]:
    """(planted, unplanted, builds) for a story name or ``"hymn_books"``, compiled once."""
    if name not in _PLANTING:
        if name == "hymn_books":
            _PLANTING[name] = (_hymn_compile(), _hymn_compile(plant_feet=False), _hymn_builds())
        else:
            _PLANTING[name] = (_story_compile(name), _story_compile(name, plant_feet=False), _story_builds())
    return _PLANTING[name]


STORY_NAMES = sorted(STORIES)


@pytest.mark.parametrize("name", STORY_NAMES)
def test_every_story_plants_the_lowest_foot_at_every_cycle_key(name):
    """Every story compiles planted and unplanted; planting changes nothing but root y at cycle
    keys; and at every walk / run cycle key of every puppet the lowest foot, by forward
    kinematics, is on the standing surface (the unplanted root's y) within 1e-3."""
    planted, unplanted, builds = planted_pair(name)
    assert planting_problems(planted, unplanted, builds) == []
    assert planted.feet_planted is True
    assert unplanted.feet_planted is None
    errs = foot_errors(planted, unplanted, builds)
    walkers = {target for target, _, _ in errs}
    assert walkers, f"{name} has no walk or run cycle to check"
    for target in walkers:
        assert len([e for e in errs if e[0] == target]) == len(cycle_key_times(planted, target))
    off = [(target, t, round(e, 5)) for target, t, e in errs if abs(e) > STORY_TOL]
    assert off == []


def test_the_stories_plant_runs_and_walks_for_every_build():
    """The story catalogue exercises planting widely: several puppets of different builds walk."""
    builds = _story_builds()
    walked = {target.removeprefix("puppet:") for name in STORY_NAMES
              for target, _, _ in foot_errors(*planted_pair(name))}
    assert len(walked) >= 4
    assert len({builds[w] for w in walked}) >= 3


# ------------------------------------------------------- between keys: the foot, measured
#
# The stage interpolates root.position and the leg rotations linearly between keys, so the
# lowest foot is exactly on the surface only at the planted cycle keys. Between two keys of a
# cycle the foot can lift or sink. Measured over every story and the hymn sample, 8 samples per
# key interval (each interval of the puppet's root / leg key times with a cycle key at either
# end), on the code this test was written for:
#
#   worst lift (foot above the surface): +0.00047 (graduation, teen at a cycle key: 3 dp
#                                        rounding of the keys, not interpolation)
#   worst sink (foot below the surface): -0.01008 (moving_house and wedding, kid, small build,
#                                        between a run's rest key and its first cycle key)
#
# The lift is far below the 0.01 the task set for adding interior root keys, so planting.py
# adds none. The bounds below pin the measurement with a little headroom.

BETWEEN_SAMPLES = 8
MAX_LIFT = 1e-3   # measured +0.00047; the task's threshold for adding interior keys is 0.01
MAX_SINK = 0.0125  # measured -0.01008
ROOT_CHANNEL = "root.position"


def between_key_foot_offsets(planted: Timeline, unplanted: Timeline, builds: dict[str, str],
                             samples: int = BETWEEN_SAMPLES) -> list[tuple[float, str, float]]:
    """(foot height above the surface, target, t) at ``samples`` evenly spaced points (both ends
    included) of every key interval of a puppet's root / leg keys that has a cycle key at either
    end. The surface is the unplanted root's y at t."""
    chans = {ROOT_CHANNEL} | {f"{b}.rot" for b in LEGS}
    out: list[tuple[float, str, float]] = []
    for who, build in sorted(builds.items()):
        target = f"puppet:{who}"
        cycle = set(cycle_key_times(planted, target))
        if not cycle:
            continue
        times = sorted({k[0] for tr in planted.tracks if tr.target == target and tr.channel in chans
                        for k in tr.keys})
        for a, b in zip(times, times[1:]):
            if a not in cycle and b not in cycle:
                continue
            for i in range(samples + 1):
                t = a + (b - a) * i / samples
                root = evaluate(planted.tracks, target, ROOT_CHANNEL, t)
                ground = evaluate(unplanted.tracks, target, ROOT_CHANNEL, t)
                foot = root[1] + (RIG["hipHeight"] - lowest_foot(_legs_at(planted, target, t))) * puppet_scale(build)
                out.append((foot - ground[1], target, t))
    return out


@pytest.mark.parametrize("name", ["hymn_books", *STORY_NAMES])
def test_between_cycle_keys_the_foot_stays_near_the_surface(name):
    planted, unplanted, builds = planted_pair(name)
    offsets = between_key_foot_offsets(planted, unplanted, builds)
    assert offsets, f"{name} has no cycle to measure"
    lift = max(offsets)
    sink = min(offsets)
    assert lift[0] <= MAX_LIFT, lift
    assert sink[0] >= -MAX_SINK, sink


def test_the_between_key_measurement_sees_the_dip_and_the_unplanted_lift():
    """The measurement is not vacuous: over all of them the foot does dip between keys, and on
    the unplanted compile it lifts well off the surface mid-stride."""
    names = ["hymn_books", *STORY_NAMES]
    planted = [o for n in names for o in between_key_foot_offsets(*planted_pair(n))]
    assert min(planted)[0] < -0.005
    unplanted = [o for n in names
                 for o in between_key_foot_offsets(planted_pair(n)[1], planted_pair(n)[1], planted_pair(n)[2])]
    assert max(unplanted)[0] > 0.01
