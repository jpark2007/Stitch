"""Riders on props stacked on a vehicle (a chair on a pickup's bed): a drive carries them on
their tracks exactly as World.move_vehicle carries them in the world, so whatever they do
next starts where they really are; they count for the drive's busy check, for ride_to, and
for what a follow of them waits for (compiler._moves).

Compiled end to end on verb_cases.base_scene()'s street, with the sedan given a bed socket."""

from __future__ import annotations

import pytest
from verb_cases import base_scene

from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import CompileResult, Screenplay

# --------------------------------------------------------------------------- helpers


def scene():
    s = base_scene()
    s.props["sedan"] = s.props["sedan"].model_copy(update={"sockets": {"driver": [0.42, 0.55], "bed": [0.8, 0.5]}})
    return s


SEATED = {"dad": {"at": "garage", "in": "sedan.driver"}, "kid": {"at": "garage", "in": "chair.seat"},
          "mom": {"at": "corner"}}


def run(actions: list[dict], start: dict | None = None) -> CompileResult:
    s = scene()
    sp = Screenplay.model_validate({
        "memoryId": "m", "cast": ["dad", "mom", "kid"], "props": ["sedan", "chair"],
        "shots": [{"id": "s1", "set": "street", "start": start or SEATED,
                   "propStart": {"sedan": {"at": "garage"}, "chair": {"on": "sedan.bed"}},
                   "actions": actions}],
    })
    return compile_screenplay(sp, sets=s.sets, props=s.props, people=s.people)


def keys(result: CompileResult, target: str, channel: str) -> list:
    for tr in result.timeline.tracks:
        if (tr.target, tr.channel) == (target, channel):
            return [(k[0], k[1]) for k in tr.keys]
    return []


def errors(result: CompileResult) -> list[tuple[str, str]]:
    return [(e.code, e.message) for e in result.errors]


DRIVE = {"t": 1, "who": "dad", "do": "drive_to", "what": "sedan", "to": "corner"}
GARAGE_X, CORNER_X = -0.4, 0.6  # the street's marks (x = 2u - 1)


# ---------------------------------------------------------------------------- drives


def test_a_drive_carries_a_puppet_sitting_on_a_chair_on_the_vehicle():
    res = run([DRIVE])
    assert res.ok, errors(res)
    sedan = keys(res, "prop:sedan", "position")
    kid = keys(res, "puppet:kid", "root.position")
    start = sedan[0][1]
    assert start == [GARAGE_X, 0.0, 0.5] and sedan[-1][1] == [CORNER_X, 0.0, 0.5]
    # kid's keys: where kid sits at the shot's start, then one key at every key time of the
    # sedan's path, each translated by the sedan's travel so far
    seat = kid[0][1]
    assert kid[0][0] == 0.0
    drive = kid[1:]
    assert [t for t, _ in drive] == [t for t, _ in sedan[1:]]
    for (_, k), (_, a) in zip(drive, sedan[1:], strict=True):
        assert k == pytest.approx([seat[i] + a[i] - start[i] for i in range(3)], abs=1e-6)
    assert drive[-1][1][0] == pytest.approx(seat[0] + (CORNER_X - GARAGE_X), abs=1e-6)
    # the chair rides on the bed without keys of its own (it is parented to the sedan)
    assert keys(res, "prop:chair", "parent") == [(0.0, "prop:sedan.bed")]
    assert len(keys(res, "prop:chair", "position")) == 1


def test_standing_up_after_the_drive_starts_where_the_drive_left_the_puppet():
    res = run([DRIVE, {"t": 5, "who": "kid", "do": "stand_up"}])
    assert res.ok, errors(res)
    kid = keys(res, "puppet:kid", "root.position")
    stand_start = 5 + res.timeline.shots[0].action_start
    last_drive = [v for t, v in kid if t < stand_start][-1]
    first_stand = next(v for t, v in kid if t == pytest.approx(stand_start))
    assert abs(first_stand[0] - last_drive[0]) < 1e-6  # no slide back to where the drive began
    assert first_stand == pytest.approx(last_drive, abs=1e-6)
    assert all(abs(v[0] - last_drive[0]) < 1e-6 for t, v in kid if t >= stand_start)


def test_a_drive_waits_for_a_stacked_rider_still_sitting_down():
    res = run([
        {"t": 0, "who": "kid", "do": "sit_on", "what": "chair"},
        {"t": 0.5, "who": "dad", "do": "drive_to", "what": "sedan", "to": "corner"},
    ], start={"dad": {"at": "garage", "in": "sedan.driver"}, "kid": {"at": "garage"}, "mom": {"at": "corner"}})
    assert errors(res) == [(
        "busy",
        'shot s1, t=0.5: dad cannot drive_to "sedan"; kid is still settling into chair.seat, which rests on '
        "sedan, until t=1.94",
    )]


def test_a_drive_after_the_stacked_rider_has_sat_down_carries_them():
    res = run([
        {"t": 0, "who": "kid", "do": "sit_on", "what": "chair"},
        {"t": 2, "who": "dad", "do": "drive_to", "what": "sedan", "to": "corner"},
    ], start={"dad": {"at": "garage", "in": "sedan.driver"}, "kid": {"at": "garage"}, "mom": {"at": "corner"}})
    assert res.ok, errors(res)
    sedan = keys(res, "prop:sedan", "position")
    kid = dict(keys(res, "puppet:kid", "root.position"))
    t0, t1 = sedan[-2][0], sedan[-1][0]
    assert kid[t1][0] - kid[t0][0] == pytest.approx(CORNER_X - GARAGE_X, abs=1e-6)


# --------------------------------------------------------------------------- follows


def test_a_follow_of_a_stacked_rider_waits_for_the_drive():
    res = run([
        {"t": 0.5, "who": "mom", "do": "follow", "target": "kid"},
        DRIVE,
    ], start={**SEATED, "mom": {"at": "garage"}})
    assert res.ok, errors(res)
    kid = keys(res, "puppet:kid", "root.position")
    mom = keys(res, "puppet:mom", "root.position")
    # mom sets off 0.5 s after the sedan does, and ends 0.2 behind kid's carried position
    sedan = keys(res, "prop:sedan", "position")
    assert mom[1][0] == pytest.approx(sedan[-2][0] + 0.5)
    assert mom[-1][1][0] == pytest.approx(kid[-1][1][0] - 0.2, abs=1e-6)
    assert mom[-1][1][0] > kid[0][1][0]  # she walked after kid, who only moved with the sedan


def test_a_follow_of_a_stacked_rider_with_a_window_waits_for_a_drive_inside_it():
    res = run([
        {"t": 0.5, "who": "mom", "do": "follow", "target": "kid", "dur": 6},
        DRIVE,
        {"t": 9, "who": "kid", "do": "stand_up"},  # after the window: does not hold the follow back
    ], start={**SEATED, "mom": {"at": "garage"}})
    assert res.ok, errors(res)
    kid = keys(res, "puppet:kid", "root.position")
    mom = keys(res, "puppet:mom", "root.position")
    assert mom[-1][1][0] > kid[0][1][0]


# --------------------------------------------------------------------------- ride_to


def test_a_stacked_rider_can_ride_to_and_everyone_aboard_is_busy():
    res = run([
        {"t": 1, "who": "kid", "do": "ride_to", "what": "sedan", "to": "corner"},
        {"t": 1.5, "who": "dad", "do": "wave"},
    ])
    assert errors(res) == [("busy", "shot s1, t=1.5: dad cannot wave; dad is still driving until t=2.11")]
    ok = run([{"t": 1, "who": "kid", "do": "ride_to", "what": "sedan", "to": "corner"}])
    assert ok.ok, errors(ok)
    kid = keys(ok, "puppet:kid", "root.position")
    assert kid[-1][1][0] - kid[0][1][0] == pytest.approx(CORNER_X - GARAGE_X, abs=1e-6)


def test_ride_to_by_the_driver_keeps_the_stacked_rider_busy():
    res = run([
        {"t": 1, "who": "dad", "do": "ride_to", "what": "sedan", "to": "corner"},
        {"t": 1.5, "who": "kid", "do": "stand_up"},
    ])
    assert errors(res) == [("busy", "shot s1, t=1.5: kid cannot stand_up; kid is still riding until t=2.11")]


def test_someone_on_a_chair_on_the_ground_cannot_ride_to():
    res = run([{"t": 1, "who": "mom", "do": "ride_to", "what": "sedan", "to": "corner"}],
              start={**SEATED, "mom": {"at": "corner"}})
    assert errors(res) == [("not_in_vehicle", 'shot s1, t=1: mom cannot ride_to "sedan"; mom is not in sedan')]
