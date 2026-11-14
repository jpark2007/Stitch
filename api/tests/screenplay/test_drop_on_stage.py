"""drop lands on stage; place_on refuses a mark past the stage edge.

(a) A puppet standing near the edge and facing out used to drop its prop DROP_OFFSET past its
    feet with no bound, so the prop landed at |x| > 1 (x=1.02 from a mark at x=0.94), offstage,
    and a pick_up right after failed with not_on_stage. verbs_core.apply_drop now clamps the
    landing x to [-STAGE_EDGE, STAGE_EDGE] (body.drop_keys' ``edge``): the final position key,
    the returned landing and World.drop agree. A landing already on stage is unchanged.
(b) place_on onto a mark past the edge (offstage_left / offstage_right, or a set mark beyond
    |x| = 1) set the prop down offstage. It is now refused with bad_target, before any key.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from screenplay import body as B
from screenplay import verbs_core
from screenplay.compiler import compile as compile_screenplay
from screenplay.effects import STAGE_EDGE
from screenplay.errors import hint_for
from screenplay.schema import CompileResult, Screenplay, Timeline
from screenplay.state import BUILTIN_MARKS, StateError
from screenplay.tracks import TrackSet, evaluate
from screenplay.verbs_core import STAGE_EDGE as CORE_STAGE_EDGE
from screenplay.verbs_core import apply_drop
from screenplay.verbs_objects import apply_place_on
from test_verbs_objects import ACTION_START, T, act, dump, holding_hat, make_locate
from timeline_sanity import sanity_problems
from verb_cases import CAST, PROPS, base_scene

PROP_IDS = ("suitcase", "hymn_books")


# --------------------------------------------------------------------------- helpers

def _a(t: float, do: str, **kw: Any) -> dict:
    return {"t": t, "do": do, **kw}


def _sp(*actions: dict, start: dict, marks: dict | None = None, prop_start: dict | None = None) -> Screenplay:
    shot: dict[str, Any] = {"id": "s1", "set": "yard", "start": start, "propStart": prop_start or {},
                            "actions": list(actions)}
    if marks:
        shot["marks"] = marks
    return Screenplay.model_validate({"memoryId": "drop_on_stage", "cast": list(CAST), "props": list(PROPS),
                                      "shots": [shot]})


def _compile(sp: Screenplay, **kw: Any) -> CompileResult:
    sc = base_scene()
    return compile_screenplay(sp, sets=sc.sets, props=sc.props, people=sc.people, **kw)


def _errors(result: CompileResult) -> list[str]:
    return [f"{e.code}: {e.message}" for e in result.errors]


def _ok(sp: Screenplay, **kw: Any) -> Timeline:
    res = _compile(sp, **kw)
    assert res.ok and res.timeline is not None, _errors(res)
    assert not res.errors
    assert sanity_problems(res.timeline) == []
    return res.timeline


def _channel(tl: Timeline, target: str, channel: str) -> list:
    tracks = [tr for tr in tl.tracks if tr.target == target and tr.channel == channel]
    assert len(tracks) == 1, (target, channel, [(tr.target, tr.channel) for tr in tl.tracks])
    return tracks[0].keys


def _u(x: float) -> float:
    """The mark u for stage x (shared/CONTRACTS.md, Marks: x = 2u - 1)."""
    return (x + 1) / 2


def _edge_start(x: float, facing: str, what: str) -> tuple[dict, dict]:
    """mom standing at a mark at stage x, facing ``facing``, holding ``what``."""
    marks = {"edge": [_u(x), 0.0]}
    return {"mom": {"at": "edge", "facing": facing, "holding": what}}, marks


def _last_position(tl: Timeline, what: str) -> list[float]:
    keys = _channel(tl, f"prop:{what}", "position")
    return list(keys[-1][1])


def _drop_landing(tl: Timeline, what: str) -> tuple[float, list[float]]:
    """(the drop's timeline time, its landing key's value): the landing is the position key
    FALL_SECONDS after the prop's parent turns to ground (timeline time, after the lead-in)."""
    t_drop = next(t for t, v in _channel(tl, f"prop:{what}", "parent") if v == "ground")
    landing = [v for t, v in _channel(tl, f"prop:{what}", "position") if t == pytest.approx(t_drop + B.FALL_SECONDS)]
    assert len(landing) == 1
    return t_drop, list(landing[0])


# ----------------------------------------------------------------- (a) drop at the edge

@pytest.mark.parametrize("what", PROP_IDS)
@pytest.mark.parametrize("x", [0.94, 0.96, 0.98])
def test_a_drop_facing_out_at_the_right_edge_lands_on_stage(x: float, what: str):
    start, marks = _edge_start(x, "right", what)
    tl = _ok(_sp(_a(1, "drop", who="mom", what=what), start=start, marks=marks))
    landing = _last_position(tl, what)
    assert abs(landing[0]) <= STAGE_EDGE
    assert landing[0] == pytest.approx(min(x + B.DROP_OFFSET, STAGE_EDGE))
    # the final key is the drop's landing, at t + FALL_SECONDS, and the prop stays on the ground
    assert _drop_landing(tl, what)[1] == landing
    assert [k[1] for k in _channel(tl, f"prop:{what}", "parent")][-1] == "ground"
    assert evaluate(tl.tracks, f"prop:{what}", "position", tl.duration)[0] == pytest.approx(landing[0])


@pytest.mark.parametrize("what", PROP_IDS)
@pytest.mark.parametrize("x", [-0.94, -0.96, -0.98])
def test_a_drop_facing_out_at_the_left_edge_lands_on_stage(x: float, what: str):
    start, marks = _edge_start(x, "left", what)
    tl = _ok(_sp(_a(1, "drop", who="mom", what=what), start=start, marks=marks))
    landing = _last_position(tl, what)
    assert landing[0] >= -STAGE_EDGE
    assert landing[0] == pytest.approx(max(x - B.DROP_OFFSET, -STAGE_EDGE))


@pytest.mark.parametrize("what", PROP_IDS)
@pytest.mark.parametrize(("x", "facing"), [(0.94, "right"), (0.96, "right"), (0.98, "right"), (-0.96, "left")])
def test_the_dropper_picks_it_up_again(x: float, facing: str, what: str):
    """On origin/main the pick_up failed: ``not_on_stage`` (offstage at x=1.02). The pick_up
    finds the prop at the world landing, which is the timeline's last drop key."""
    start, marks = _edge_start(x, facing, what)
    sp = _sp(_a(1, "drop", who="mom", what=what), _a(3, "pick_up", who="mom", what=what),
             start=start, marks=marks)
    tl = _ok(sp)
    pos = _channel(tl, f"prop:{what}", "position")
    parents = _channel(tl, f"prop:{what}", "parent")
    t_drop, landing = _drop_landing(tl, what)
    assert abs(landing[0]) <= STAGE_EDGE
    unclamped = x + B.DROP_OFFSET * (1 if facing == "right" else -1)
    assert landing[0] == pytest.approx(max(-STAGE_EDGE, min(STAGE_EDGE, unclamped)))
    assert parents[-1][1].startswith("puppet:mom.")
    # the prop lies still at the landing until the pick_up takes it into mom's hand
    pick = next(t for t, v in parents if t > t_drop and v.startswith("puppet:mom."))
    assert [v for t, v in pos if t_drop + B.FALL_SECONDS < t < pick] == []
    assert evaluate(tl.tracks, f"prop:{what}", "position", pick - 1e-3) == pytest.approx(landing)


def test_drop_then_place_on_a_nearby_mark_finds_it_at_the_landing():
    """A second check that the world agrees with the key: the prop picked up at the clamped
    landing can be set down again on an on-stage mark."""
    start, marks = _edge_start(0.96, "right", "hymn_books")
    marks["near"] = [_u(0.9), 0.0]
    tl = _ok(_sp(_a(1, "drop", who="mom", what="hymn_books"), _a(3, "pick_up", who="mom", what="hymn_books"),
                 _a(6, "place_on", who="mom", what="hymn_books", on="near"), start=start, marks=marks))
    assert _last_position(tl, "hymn_books")[0] == pytest.approx(0.9)


@pytest.mark.parametrize("what", PROP_IDS)
@pytest.mark.parametrize(("mark", "facing"), [("bench_spot", "right"), ("by_table", "left"), ("door", "left")])
def test_a_drop_on_stage_is_unchanged(mark: str, facing: str, what: str):
    """Away from the edge the landing is exactly ground.x + DROP_OFFSET * facing, as before."""
    from verb_cases import YARD_MARKS

    x = 2 * YARD_MARKS[mark][0] - 1
    sign = 1 if facing == "right" else -1
    tl = _ok(_sp(_a(1, "drop", who="mom", what=what), start={"mom": {"at": mark, "facing": facing, "holding": what}}))
    t_drop, landing = _drop_landing(tl, what)
    assert landing[0] == pytest.approx(x + B.DROP_OFFSET * sign, abs=1e-9)
    assert abs(landing[0]) < STAGE_EDGE


@pytest.mark.parametrize(("mark", "facing"), [("bench_spot", "right"), ("curb", "left"), ("door", "left")])
def test_an_on_stage_drop_compiles_byte_identical_to_the_unclamped_drop(monkeypatch, mark: str, facing: str):
    """The clamp changes nothing for a landing already on stage: the whole compile result is
    byte-identical to one with the edge pushed out to infinity (the pre-clamp behaviour)."""
    sp = _sp(_a(1, "drop", who="mom", what="suitcase"), _a(3, "pick_up", who="mom", what="suitcase"),
             start={"mom": {"at": mark, "facing": facing, "holding": "suitcase"}})
    clamped = _compile(sp).model_dump_json(by_alias=True)
    monkeypatch.setattr(verbs_core, "STAGE_EDGE", math.inf)
    assert _compile(sp).model_dump_json(by_alias=True) == clamped


def test_without_the_clamp_the_pick_up_after_an_edge_drop_failed(monkeypatch):
    """The regression this guards: with no edge (origin/main before this change) the drop at
    x=0.94 facing right landed at x=1.02, offstage, and the pick_up failed."""
    start, marks = _edge_start(0.94, "right", "suitcase")
    sp = _sp(_a(1, "drop", who="mom", what="suitcase"), _a(3, "pick_up", who="mom", what="suitcase"),
             start=start, marks=marks)
    monkeypatch.setattr(verbs_core, "STAGE_EDGE", math.inf)
    res = _compile(sp)
    assert [e.code for e in res.errors] == ["not_on_stage"]
    assert "x=1.02" in res.errors[0].message


def test_drop_keys_without_an_edge_is_the_old_landing_and_with_one_is_clamped():
    """body.drop_keys: ``edge`` only moves the landing (the final key); the fall's start key,
    from the hand, and the arm keys are the same."""
    from screenplay import ik

    geom = B.PropGeom.from_prop(base_scene().props["hymn_books"])
    body = ik.Body("mom", (0.96, 0.0), 1, "average")
    hold = B.hold_pose("arm_r")
    free, free_landing = B.drop_keys(body, 1.0, hold, geom, surface_y=0.0, z=0.4)
    clamped, landing = B.drop_keys(body, 1.0, hold, geom, surface_y=0.0, z=0.4, edge=CORE_STAGE_EDGE)
    assert free_landing[0] == pytest.approx(0.96 + B.DROP_OFFSET)
    assert landing == (CORE_STAGE_EDGE, 0.0, 0.4)
    assert clamped[:-1] == free[:-1]
    assert clamped[-1] == (free[-1][0], "position", 1.0 + B.FALL_SECONDS, list(landing))
    inside = ik.Body("mom", (0.5, 0.0), 1, "average")
    assert B.drop_keys(inside, 1.0, hold, geom, 0.0, 0.4, edge=1.0) == B.drop_keys(inside, 1.0, hold, geom, 0.0, 0.4)


def test_the_stage_edge_constants_agree():
    assert CORE_STAGE_EDGE == STAGE_EDGE == 1.0


# ------------------------------------------------------- (b) place_on an offstage mark

@pytest.mark.parametrize("mark", ["offstage_right", "offstage_left"])
def test_place_on_an_offstage_builtin_mark_is_bad_target(mark: str):
    x = BUILTIN_MARKS[mark][0]
    res = _compile(_sp(_a(1, "place_on", who="mom", what="hymn_books", on=mark),
                       start={"mom": {"at": "by_table", "holding": "hymn_books"}}))
    assert not res.ok and res.timeline is None
    assert [(e.code, e.message, e.shot, e.t, e.who, e.do) for e in res.errors] == [(
        "bad_target",
        f'shot s1, t=1: mom cannot place_on "hymn_books"; '
        f"{mark} is offstage at x={x:g}; place_on needs a mark on stage (|x| <= 1)",
        "s1", 1, "mom", "place_on",
    )]
    assert mark in res.errors[0].message and f"x={x:g}" in res.errors[0].message
    # bad_target has no general fix and no variant matches: no hint rather than a wrong one
    assert hint_for(res.errors[0]) is None


def test_place_on_a_set_mark_past_the_edge_is_bad_target():
    res = _compile(_sp(_a(1, "place_on", who="mom", what="hymn_books", on="ledge"),
                       start={"mom": {"at": "curb", "holding": "hymn_books"}}, marks={"ledge": [1.1, 0.0]}))
    assert [(e.code, e.message) for e in res.errors] == [(
        "bad_target",
        'shot s1, t=1: mom cannot place_on "hymn_books"; '
        "ledge is offstage at x=1.2; place_on needs a mark on stage (|x| <= 1)",
    )]


def test_a_refused_place_on_emits_nothing_and_the_prop_stays_in_hand():
    """Atomic: the error is reported, and a later action in the same shot still finds the prop
    in mom's hand (a hand_to succeeds, a drop needs no other setup)."""
    sp = _sp(_a(1, "place_on", who="mom", what="hymn_books", on="offstage_right"),
             _a(3, "hand_to", who="mom", what="hymn_books", to="dad"),
             start={"mom": {"at": "bench_spot", "holding": "hymn_books"}, "dad": {"at": "beside_bench"}})
    res = _compile(sp)
    assert [e.code for e in res.errors] == ["bad_target"]
    # with the place_on removed, the same screenplay compiles: the hand_to's state was right
    fixed = _sp(_a(3, "hand_to", who="mom", what="hymn_books", to="dad"),
                start={"mom": {"at": "bench_spot", "holding": "hymn_books"}, "dad": {"at": "beside_bench"}})
    _ok(fixed)


@pytest.mark.parametrize("mark", ["offstage_right", "offstage_left", "past_edge"])
def test_the_handler_refuses_before_any_key_and_leaves_the_world(mark: str):
    """verbs_objects.apply_place_on itself: StateError(bad_target) with the world and the tracks
    exactly as they were (test_verbs_objects.rejects), so the prop is still in dad's hand and a
    following drop finds it there."""
    tracks = TrackSet()
    w = holding_hat(tracks)
    marks = {**BUILTIN_MARKS, "past_edge": (-1.05, 0.0, 0.5)}
    base = make_locate(w, tracks)

    def locate(name: str, t: float):
        return marks[name] if name in marks else base(name, t)

    x = marks[mark][0]
    before_w, before_t = w.copy(), dump(tracks)
    with pytest.raises(StateError) as ei:
        apply_place_on(w, tracks, act(do="place_on", who="dad", what="hat", on=mark), T, ACTION_START, locate)
    assert (ei.value.code, ei.value.reason) == (
        "bad_target", f"{mark} is offstage at x={x:g}; place_on needs a mark on stage (|x| <= 1)"
    )
    assert w == before_w and dump(tracks) == before_t
    assert w.holder("hat") == ("dad", "hand_r")
    end = apply_drop(w, tracks, act(do="drop", who="dad", what="hat"), T, ACTION_START, locate)
    assert end == pytest.approx(T + B.FALL_SECONDS)
    assert w.holder("hat") is None


def test_place_on_an_on_stage_mark_is_unchanged():
    tl = _ok(_sp(_a(1, "place_on", who="mom", what="hymn_books", on="table_spot"),
                 start={"mom": {"at": "by_table", "holding": "hymn_books"}}))
    assert _last_position(tl, "hymn_books")[0] == pytest.approx(0.2)
