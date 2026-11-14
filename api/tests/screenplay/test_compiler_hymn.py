"""The golden test: compiling hymn_books.screenplay.json reproduces hymn_books.timeline.json.

Same (target, channel) set, the same parent swaps at the same times, every other track agreeing
within 1e-3 at every key time of either timeline, and the same shots, duration, captions,
narrations, interactions and explanations.
"""

import json
from pathlib import Path

import pytest

import schemas
from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import Screenplay, Timeline
from screenplay.tracks import evaluate

SAMPLES = Path(__file__).resolve().parents[3] / "web" / "public" / "samples"
TOL = 1e-3


def _load(name):
    return json.loads((SAMPLES / name).read_text())


@pytest.fixture(scope="module")
def golden() -> Timeline:
    return Timeline.model_validate(_load("hymn_books.timeline.json"))


@pytest.fixture(scope="module")
def compiled() -> Timeline:
    memory = _load("hymn_books.memory.json")
    result = compile_screenplay(
        Screenplay.model_validate(_load("hymn_books.screenplay.json")),
        sets={s["id"]: schemas.SetDef.model_validate(s) for s in memory["sets"]},
        props={p["id"]: schemas.Prop.model_validate(p) for p in memory["props"]},
        people={p["id"]: schemas.Person.model_validate(p) for p in memory["people"]},
    )
    assert result.ok, [e.message for e in result.errors]
    assert result.errors == []
    assert result.timeline is not None
    return result.timeline


def _close(a, b) -> bool:
    if isinstance(a, str) or isinstance(b, str) or a is None or b is None:
        return a == b
    if isinstance(a, (list, tuple)):
        return isinstance(b, (list, tuple)) and len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    return abs(float(a) - float(b)) <= TOL


def test_same_tracks(compiled, golden):
    assert {(t.target, t.channel) for t in compiled.tracks} == {(t.target, t.channel) for t in golden.tracks}


def test_identical_parent_swaps(compiled, golden):
    def parents(tl):
        (tr,) = [t for t in tl.tracks if (t.target, t.channel) == ("prop:hymn_books", "parent")]
        return tr.keys

    got, want = parents(compiled), parents(golden)
    assert [v for _, v in got] == [v for _, v in want] == [
        "puppet:mom.hand_r", "ground", "puppet:dad.hand_r", "puppet:mom.hand_r",
    ]
    assert all(abs(a[0] - b[0]) <= TOL for a, b in zip(got, want, strict=True))


def test_every_track_agrees_at_every_key_time(compiled, golden):
    mismatches = []
    for want in golden.tracks:
        (got,) = [t for t in compiled.tracks if (t.target, t.channel) == (want.target, want.channel)]
        times = sorted({k[0] for k in want.keys} | {k[0] for k in got.keys})
        for t in times:
            a = evaluate(compiled.tracks, want.target, want.channel, t)
            b = evaluate(golden.tracks, want.target, want.channel, t)
            if not _close(a, b):
                mismatches.append((want.target, want.channel, t, a, b))
    assert mismatches == []


def test_parent_and_position_keys_share_times(compiled):
    for target in {t.target for t in compiled.tracks if t.channel == "parent"}:
        (parent,) = [t for t in compiled.tracks if (t.target, t.channel) == (target, "parent")]
        (position,) = [t for t in compiled.tracks if (t.target, t.channel) == (target, "position")]
        assert {k[0] for k in parent.keys} <= {k[0] for k in position.keys}


def test_shots_and_duration(compiled, golden):
    assert _close(compiled.duration, golden.duration)
    assert len(compiled.shots) == len(golden.shots)
    for a, b in zip(compiled.shots, golden.shots):
        assert (a.id, a.set, a.transition, a.page_text) == (b.id, b.set, b.transition, b.page_text)
        assert _close([a.start, a.action_start, a.end], [b.start, b.action_start, b.end])


def _captions_equal(a, b):
    assert [c.text for c in a] == [c.text for c in b]
    assert _close([c.t for c in a], [c.t for c in b])


def test_captions_and_narrations(compiled, golden):
    assert compiled.default_narrator == golden.default_narrator == "dad"
    assert compiled.audio is None and compiled.effects == []
    _captions_equal(compiled.captions, golden.captions)
    assert list(compiled.narrations) == list(golden.narrations)
    for author, want in golden.narrations.items():
        got = compiled.narrations[author]
        assert got.author_id == want.author_id and got.audio == want.audio
        _captions_equal(got.captions, want.captions)
        assert len(got.lines) == len(want.lines)
        for gl, wl in zip(got.lines, want.lines):
            assert (gl.text, gl.source, gl.cue, gl.cue_word) == (wl.text, wl.source, wl.cue, wl.cue_word)
            assert _close(gl.t, wl.t)
            assert [w.word for w in gl.words] == [w.word for w in wl.words]
            assert _close([w.t for w in gl.words], [w.t for w in wl.words])


def test_interactions_and_explanations(compiled, golden):
    assert len(compiled.interactions) == len(golden.interactions) == 1
    got, want = compiled.interactions[0], golden.interactions[0]
    assert _close([got.drives.from_, got.drives.to], [want.drives.from_, want.drives.to])
    blank = {"drives": None}
    assert got.model_copy(update=blank) == want.model_copy(update=blank)
    assert got.drives.model_copy(update={"from_": 0, "to": 0}) == want.drives.model_copy(update={"from_": 0, "to": 0})
    assert compiled.explanations == golden.explanations


def test_whole_timeline_round_trips_through_json(compiled):
    again = Timeline.model_validate_json(compiled.model_dump_json(by_alias=True))
    assert again == compiled
