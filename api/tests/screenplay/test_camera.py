"""Camera move resolution and per-shot camera keys."""

import json
from pathlib import Path

import pytest

from screenplay.camera import (
    CAMERA_X_LIMIT,
    CLOSE_UP_FOV,
    DEFAULT_MOVE_SECONDS,
    TARGET_MOVES,
    FLY_IN,
    WIDE,
    CameraPose,
    lerp_pose,
    resolve_move,
    shot_camera_keys,
)
from screenplay.schema import CameraCue
from screenplay.tracks import TrackSet

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"
CUR = CameraPose((0.4, 0.8, 2.0), (0.3, 0.4, 0.5), 30)


def sample_tracks() -> dict:
    raw = json.loads((SAMPLES / "hymn_books.timeline.json").read_text())
    return {(t["target"], t["channel"]): t["keys"] for t in raw["tracks"]}


def screenplay_cues() -> list[CameraCue]:
    raw = json.loads((SAMPLES / "hymn_books.screenplay.json").read_text())
    return [CameraCue.model_validate(c) for c in raw["shots"][0]["camera"]]


def test_sample_camera_tracks_exact():
    calls = []

    def target_at(name, t):
        calls.append((name, t))
        return {"mom": (0.2, 0.3, 0.5)}[name]

    keys, final = shot_camera_keys(screenplay_cues(), 0, 1.2, None, target_at)
    ts = TrackSet()
    ts.extend(keys)
    got = {(t.target, t.channel): json.loads(t.model_dump_json())["keys"] for t in ts.tracks()}
    want = sample_tracks()
    for ch in ("position", "lookAt", "fov"):
        assert got[("camera", ch)] == want[("camera", ch)], ch
    assert set(got) == {("camera", "position"), ("camera", "lookAt"), ("camera", "fov")}
    assert calls == [("mom", pytest.approx(9.7))]
    assert final == CameraPose((0.2, 0.6, 1.5), (0.2, 0.55, 0.5), 35)


def test_resolve_each_move():
    tgt = (0.5, 0.3, 0.5)
    assert resolve_move("wide", CUR, None) == WIDE
    assert resolve_move("dolly_to", CUR, tgt) == CameraPose((0.5, 0.6, 1.5), (0.5, 0.55, 0.5), 30)
    cu = resolve_move("close_up", CUR, tgt)
    assert cu.position == pytest.approx((0.5, 0.6, 1.1))
    assert cu.look_at == pytest.approx((0.5, 0.55, 0.5))
    assert cu.fov == 28
    assert resolve_move("pan_to", CUR, tgt) == CameraPose((0.5, 0.8, 2.0), (0.5, 0.4, 0.5), 30)
    assert resolve_move("pull_back", CUR, None) == CameraPose((0.2, 0.75, 3.0), (0.15, 0.35, 0.5), 35)
    assert resolve_move("pull_back", CUR, tgt) == resolve_move("pull_back", CUR, None)


def test_x_is_clamped():
    far = (1.3, 0.0, 0.5)
    for move in ("dolly_to", "close_up", "pan_to"):
        p = resolve_move(move, CUR, far)
        assert p.position[0] == CAMERA_X_LIMIT and p.look_at[0] == CAMERA_X_LIMIT, move
        p = resolve_move(move, CUR, (-1.3, 0.0, 0.5))
        assert p.position[0] == -CAMERA_X_LIMIT and p.look_at[0] == -CAMERA_X_LIMIT, move
    wild = CameraPose((3.0, 0.6, 2.0), (-4.0, 0.3, 0.5), 35)
    p = resolve_move("pull_back", wild, None)
    assert p.position[0] == CAMERA_X_LIMIT and p.look_at[0] == -CAMERA_X_LIMIT


@pytest.mark.parametrize("move", ["dolly_to", "close_up", "pan_to"])
def test_missing_required_target_raises(move):
    with pytest.raises(ValueError):
        resolve_move(move, CUR, None)
    with pytest.raises(ValueError):
        shot_camera_keys([CameraCue(t=2, move=move)], 0, 1.2, None, lambda n, t: (0, 0, 0.5))


def test_unknown_move_raises():
    with pytest.raises(ValueError):
        resolve_move("spin", CUR, (0, 0, 0))


def _by_channel(keys):
    out = {}
    for target, ch, t, v in keys:
        assert target == "camera"
        out.setdefault(ch, []).append((t, v))
    return out


def test_later_shot_keys_prev_and_default_wide():
    prev = CameraPose((0.2, 0.6, 1.5), (0.2, 0.55, 0.5), 35)
    keys, final = shot_camera_keys([], 10.0, 11.2, prev, lambda n, t: (0, 0, 0.5))
    ch = _by_channel(keys)
    assert ch["position"] == [(10.0, [0.2, 0.6, 1.5]), (11.2, list(WIDE.position))]
    assert ch["lookAt"] == [(10.0, [0.2, 0.55, 0.5]), (11.2, list(WIDE.look_at))]
    assert ch["fov"] == [(10.0, 35)]
    assert final == WIDE


def test_first_shot_flies_in():
    keys, _ = shot_camera_keys([], 0, 1.2, None, lambda n, t: (0, 0, 0.5))
    ch = _by_channel(keys)
    assert ch["position"][0] == (0, list(FLY_IN.position))
    assert ch["lookAt"][0] == (0, list(FLY_IN.look_at))
    assert ch["fov"] == [(0, 35)]


def test_t0_cue_lands_on_action_start_without_hold():
    calls = []

    def target_at(n, t):
        calls.append((n, t))
        return (0.4, 0.3, 0.5)

    keys, final = shot_camera_keys([CameraCue(t=0, move="close_up", target="dad")], 0, 1.2, None, target_at)
    ch = _by_channel(keys)
    assert [t for t, _ in ch["position"]] == [0, 1.2]
    assert ch["position"][1][1] == pytest.approx([0.4, 0.6, 1.1])
    assert ch["fov"] == [(0, 35), (1.2, 28)]
    assert calls == [("dad", 1.2)]
    assert final.fov == 28


def test_default_duration_and_fov_change_keys():
    keys, final = shot_camera_keys(
        [CameraCue(t=2, move="close_up", target="mom")], 0, 1.2, None, lambda n, t: (0.2, 0.3, 0.5)
    )
    ch = _by_channel(keys)
    end = 1.2 + 2 + DEFAULT_MOVE_SECONDS
    assert [t for t, _ in ch["position"]] == [0, 1.2, 3.2, pytest.approx(end)]
    # fov keyed at the start (initial), and around the change: hold 35 at 3.2, 28 at the end
    assert ch["fov"] == [(0, 35), (3.2, 35), (pytest.approx(end), 28)]
    assert final.fov == 28


def test_overlapping_cue_uses_interpolated_hold_pose():
    target = {"mom": (0.6, 0.3, 0.5), "dad": (-0.4, 0.0, 0.5)}
    cues = [
        CameraCue(t=1, move="dolly_to", target="mom", dur=2),   # 2.2 -> 4.2
        CameraCue(t=2, move="pan_to", target="dad", dur=1),     # starts at 3.2, halfway through
    ]
    keys, final = shot_camera_keys(cues, 0, 1.2, None, lambda n, t: target[n])
    ch = _by_channel(keys)
    dolly_end = CameraPose((0.6, 0.6, 1.5), (0.6, 0.55, 0.5), 35)
    hold = lerp_pose(WIDE, dolly_end, 0.5)
    times = [t for t, _ in ch["position"]]
    assert times == [0, 1.2, 2.2, pytest.approx(3.2), pytest.approx(4.2)]
    assert times == sorted(times)  # the interrupted move's end key is gone
    assert ch["position"][3][1] == pytest.approx(list(hold.position))
    assert ch["lookAt"][3][1] == pytest.approx(list(hold.look_at))
    assert final.position == pytest.approx((-0.4, hold.position[1], hold.position[2]))
    assert final.look_at == pytest.approx((-0.4, hold.look_at[1], hold.look_at[2]))


def test_non_overlapping_cue_holds_previous_end():
    cues = [CameraCue(t=1, move="pull_back", dur=1), CameraCue(t=5, move="wide")]
    keys, final = shot_camera_keys(cues, 0, 1.2, None, lambda n, t: (0, 0, 0.5))
    ch = _by_channel(keys)
    pb = resolve_move("pull_back", WIDE, None)
    assert ch["position"][3] == (pytest.approx(3.2), list(pb.position))
    assert ch["position"][4] == (pytest.approx(6.2), list(pb.position))
    assert final == WIDE


VOCAB = Path(__file__).resolve().parents[3] / "shared" / "vocabulary.json"


def vocab_camera() -> dict:
    return json.loads(VOCAB.read_text())["camera"]


def test_target_moves_match_vocabulary():
    required = {m for m, spec in vocab_camera().items() if "target" in spec["required"]}
    assert required == set(TARGET_MOVES)


@pytest.mark.parametrize("move", sorted(json.loads(VOCAB.read_text())["camera"]))
def test_every_vocabulary_camera_move_resolves(move):
    spec = vocab_camera()[move]
    target = (0.3, 0.2, 0.5) if "target" in spec["required"] else None
    pose = resolve_move(move, CUR, target)
    assert isinstance(pose, CameraPose)
    cue = CameraCue(t=2, move=move, target="mom" if target else None, dur=1.0)
    keys, final = shot_camera_keys([cue], 0, 1.2, None, lambda n, t: (0.3, 0.2, 0.5))
    assert final == resolve_move(move, WIDE, target)
    times = [t for _, ch, t, _ in keys if ch == "position"]
    assert times == sorted(times) and times[-1] == pytest.approx(4.2)


def test_unknown_move_error_names_it():
    with pytest.raises(ValueError, match="'spin'"):
        resolve_move("spin", CUR, None)
    with pytest.raises(ValueError, match="'zoom_in'"):
        shot_camera_keys([CameraCue.model_construct(t=1, move="zoom_in", target=None, dur=None)], 0, 1.2, None, lambda n, t: (0, 0, 0.5))


# ---------------------------------------------------------------- bug hunt: edge cases

def _compile_yard(camera: list[dict], actions: list[dict], start: dict, prop_start: dict | None = None):
    from verb_cases import base_scene

    from screenplay.compiler import compile as compile_screenplay
    from screenplay.schema import Screenplay

    scene = base_scene()
    sp = Screenplay.model_validate({
        "memoryId": "m", "cast": ["dad", "mom", "kid"], "props": ["hymn_books", "sedan"],
        "shots": [{"id": "s1", "set": "yard", "start": start, "propStart": prop_start or {},
                   "camera": camera, "actions": actions}],
    })
    return compile_screenplay(sp, sets=scene.sets, props=scene.props, people=scene.people)


def _camera_keys(res, channel):
    (track,) = [t for t in res.timeline.tracks if (t.target, t.channel) == ("camera", channel)]
    return [(k[0], k[1]) for k in track.keys]


def test_an_offstage_target_is_framed_at_the_stage_edge_on_its_side():
    """A target offstage when the move ends (a puppet still entering, a car driving off; the
    seeded green-sedan trip pans after its car to x=1.51) is clamped, not an error: the camera
    waits at the edge it comes in from or leaves by."""
    res = _compile_yard([{"t": 0, "move": "close_up", "target": "kid"}, {"t": 6, "move": "pan_to", "target": "dad"}],
                        [{"t": 0, "who": "kid", "do": "enter", "to": "door"},
                         {"t": 0, "who": "dad", "do": "exit", "to": "offstage_right"}],
                        {"dad": {"at": "curb"}, "kid": {"at": "offstage_left"}})
    assert res.ok, res.errors
    pos = _camera_keys(res, "position")
    assert pos[1] == (1.2, [-CAMERA_X_LIMIT, 0.3, 1.1])  # kid is at x=-1.3 when the action starts
    assert pos[-1][1][0] == CAMERA_X_LIMIT  # dad has left by x=1.3
    assert all(abs(v[0]) <= CAMERA_X_LIMIT for _, v in pos)


def test_a_close_up_on_a_held_prop_raised_high_frames_the_hand():
    res = _compile_yard([{"t": 1, "move": "close_up", "target": "hymn_books", "dur": 1}],
                        [{"t": 0, "who": "mom", "do": "pose", "pose": "arms_raised"}],
                        {"mom": {"at": "by_table", "holding": "hymn_books"}})
    assert res.ok, res.errors
    (t_end, end), = [k for k in _camera_keys(res, "lookAt") if k[0] == pytest.approx(3.2)]
    assert end[1] > 0.5  # above a standing puppet's shoulders: the hand, not the feet


def test_a_close_up_on_a_passenger_lands_where_the_car_stops():
    res = _compile_yard([{"t": 2, "move": "dolly_to", "target": "dad", "dur": 4}],
                        [{"t": 0, "who": "dad", "do": "get_in", "what": "sedan"},
                         {"t": 2, "who": "dad", "do": "drive_to", "what": "sedan", "to": "door"}],
                        {"dad": {"at": "curb"}}, {"sedan": {"at": "curb"}})
    assert res.ok, res.errors
    root = {(t.target, t.channel): t.keys for t in res.timeline.tracks}[("puppet:dad", "root.position")]
    final_x = root[-1][1][0]
    assert _camera_keys(res, "position")[-1][1][0] == pytest.approx(max(-CAMERA_X_LIMIT, final_x))


def _assert_sane(keys):
    by = _by_channel(keys)
    for ch, ks in by.items():
        times = [t for t, _ in ks]
        assert times == sorted(times), ch
        for _, v in ks:
            for x in (v if isinstance(v, list) else [v]):
                assert x == x and abs(x) < 1e6, (ch, v)
    for _, fov in by["fov"]:
        assert CLOSE_UP_FOV <= fov <= FLY_IN.fov


@pytest.mark.parametrize("cues", [
    # two cues at the same time: the later one in the list wins from the same hold
    [CameraCue(t=2, move="dolly_to", target="mom", dur=2), CameraCue(t=2, move="wide", dur=1)],
    # very short moves and moves back to back
    [CameraCue(t=1, move="close_up", target="mom", dur=0.001), CameraCue(t=1.001, move="pull_back", dur=0.001)],
    # three overlapping moves, each interrupting the last
    [CameraCue(t=1, move="dolly_to", target="mom", dur=3), CameraCue(t=2, move="close_up", target="dad", dur=3),
     CameraCue(t=3, move="wide", dur=0.5)],
    # several opening cues, then a move long after the action
    [CameraCue(t=0, move="close_up", target="dad"), CameraCue(t=0, move="pull_back"), CameraCue(t=60, move="wide")],
    # a move that starts exactly where the last one ends
    [CameraCue(t=1, move="pan_to", target="dad", dur=2), CameraCue(t=3, move="close_up", target="mom", dur=2)],
])
def test_camera_keys_are_monotonic_finite_and_in_fov_range(cues):
    where = {"mom": (0.2, 0.3, 0.5), "dad": (-0.95, 0.0, 0.5)}
    keys, final = shot_camera_keys(cues, 3.0, 4.2, WIDE, lambda n, t: where[n])
    _assert_sane(keys)
    last = max(t for _, _, t, _ in keys)
    assert last >= 4.2 + max(c.t for c in cues)


def test_same_time_cues_resolve_from_the_same_hold():
    cues = [CameraCue(t=2, move="dolly_to", target="mom", dur=2), CameraCue(t=2, move="wide", dur=1)]
    keys, final = shot_camera_keys(cues, 0, 1.2, None, lambda n, t: (0.2, 0.3, 0.5))
    pos = _by_channel(keys)["position"]
    ts = TrackSet()
    ts.extend(keys)
    assert final == WIDE
    # the dolly never plays: the camera holds WIDE from 3.2 to 4.2
    assert ts.value_at("camera", "position", 3.7) == pytest.approx(list(WIDE.position))
    assert [t for t, _ in pos] == sorted(t for t, _ in pos)
