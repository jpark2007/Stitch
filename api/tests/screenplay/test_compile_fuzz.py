"""Fuzz compile(): it never raises, its timelines are sane, its errors are well-formed, and it
is deterministic (ARCHITECTURE.md section 3). Also compiles the scripted demo variants of the
hymn sample (seed.fake_runs strip_prop / strip_person).

The directing agent hands compile() arbitrary LLM-written screenplays, so this is a regression
net over random ones. The generator is a plain ``random.Random(seed)``: no new dependency, and
the corpus is the same on every run. It builds on verb_cases.base_scene() (the shared stage of
the verb case catalogue) with its CAST, PROPS, YARD_MARKS and STREET_MARKS. Two kinds of
screenplay come out of it:

- random: 1 to 3 shots (sometimes the same set twice, sometimes a set change) with random start
  blocks and propStart, 1 to 8 actions per shot drawn from every vocabulary verb, parameters
  mostly valid names with about 15% invalid or missing on average (each screenplay draws its
  rate from INVALID_RATES, so a clean third reaches the world simulation), t in [0, 12],
  optional dur, and sometimes camera cues, cued narration, a second teller, interactions and
  explanations. Nearly all of these fail, statically or in the world simulation, which
  exercises the error paths.
- perturbed: one to three ok verb cases played as consecutive shots, with jittered times,
  extra camera cues, narration and a second teller. About half compile, which exercises the
  timeline invariants (including shot contiguity on multi-shot timelines).

A few hand-written edge shapes (no shots, empty or unknown cast, NaN marks, huge t, empty
narration text) go through the same checks.

Each screenplay is built through ``Screenplay.model_validate`` on a dict, so the schema runs
too; only inputs the pydantic schema itself rejects are skipped (and counted).

Invariants, for every generated screenplay:

1. compile(...) returns a CompileResult and never raises.
2. ok: the timeline survives a JSON round trip through Timeline.model_validate; per track, key
   times never decrease; no NaN or inf anywhere; duration >= the last key time; shots are
   contiguous (shot[i].end == shot[i + 1].start within 1e-6).
3. not ok: there is at least one error; every code matches ^[a-z]+(_[a-z]+)*$; every message
   starts with "shot " or "screenplay"; error.shot is set whenever the message starts "shot ".
4. compiling the same input again (a fresh copy of the screenplay and scene) gives
   byte-identical model_dump_json, and compile() does not mutate its input.

Known failures (crashes or broken invariants whose cause is in api/screenplay or api/seed code
owned by other tasks) get a minimal hand-written reproduction marked xfail(strict=True) below,
and their seeds or shapes go in EXCLUDED so the random loop passes. As written, the random loop
found none (also over a one-off sweep of the same generator: random 0-29999 and perturbed
0-11999, 42,000 screenplays, at the commit this file was written on); the one known failure
is a scripted demo variant (test_strip_person_variant_for_the_teller_compiles).
"""

from __future__ import annotations

import copy
import json
import math
import random
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from screenplay.compiler import compile as compile_screenplay
from screenplay.schema import VOCABULARY, CompileResult, Screenplay, Timeline
from seed import demo
from seed.fake_runs import SAMPLE_OTHER, SAMPLE_TELLER, strip_person, strip_prop
from verb_cases import CASES, CAST, PROPS, STREET_MARKS, YARD_MARKS, Scene, base_scene

# ----------------------------------------------------------------------- corpus size

SEED = 20261113
"""Seed of the whole corpus: random screenplay i uses random.Random(SEED * 100_003 + i), perturbed
screenplay i random.Random(SEED * 100_003 + 50_000 + i) (see _seed)."""

N_RANDOM = 8000
"""Random screenplays. With N_PERTURBED, the whole file runs in about 8 s (measured: see the
printed line of test_compile_never_raises_and_keeps_its_invariants with pytest -s)."""

N_PERTURBED = 3500
"""Perturbed ok verb cases."""

INVALID_RATES = [0.0, 0.15, 0.3]
"""Per random screenplay, the chance that a parameter is invalid or missing is one of these
(15% on average). A clean third gets past static validation often enough to reach the world
simulation and, sometimes, a timeline."""

ACTIONS: list[str] = list(VOCABULARY["actions"])
MOVES: list[str] = list(VOCABULARY["camera"])
TIMES_OF_DAY: list[str] = list(VOCABULARY["timesOfDay"])
TRANSITIONS: list[str] = list(VOCABULARY["transitions"])
OFFSTAGE: list[str] = list(VOCABULARY["builtinMarks"])
POSES = ["stand", "hold", "sit", "kneel", "wave", "point", "reach", "hug", "slumped", "arms_raised", "bow_head"]
STATES = ["open", "closed", "lit", "out", "parked", "driving"]
SOCKETS = ["seat", "top", "driver", "passenger"]
SOCKET_REFS = ["chair.seat", "table.top", "sedan.driver", "sedan.passenger"]
ODD_SOCKET_REFS = ["chair", "sedan", "table", "hymn_books.top", "sedan.seat", "lamp"]  # bare props, missing sockets
HAND_PROPS = ["hymn_books", "cap", "lamp", "suitcase"]
WHAT: dict[str, list[str]] = {  # the props each prop verb's `what` suits
    **dict.fromkeys(["pick_up", "hold", "drop", "hand_to", "place_on"], HAND_PROPS),
    "put_on": ["cap", "cap", "lamp"],
    "open": ["suitcase"],
    "close": ["suitcase"],
    "set_state": ["lamp", "suitcase", "sedan"],
    "sit_on": ["chair", "chair", "sedan"],
    **dict.fromkeys(["get_in", "get_out", "drive_to", "ride_to"], ["sedan"]),
}
SOCKET: dict[str, list[str]] = {"sit_on": ["seat"], "place_on": ["top"], "get_in": ["driver", "passenger"]}
PERSON_TARGET = {"hug", "follow", "put_on"}  # verbs whose target must be a cast member
PROP_STATES = {"lamp": ["lit", "out"], "suitcase": ["open", "closed"], "sedan": ["parked", "driving"]}
SET_MARKS = {"yard": list(YARD_MARKS), "street": list(STREET_MARKS)}
WORDS = (
    "we walked down to the old church and dad held the books while mom laughed "
    "then the car came round the corner in the rain"
).split()
CODE = re.compile(r"[a-z]+(_[a-z]+)*")

# Seeds or shapes excluded from the random loop because of a known bug, each with an xfail
# reproduction below: {(kind, index): "reason"}.
EXCLUDED: dict[tuple[str, int], str] = {}


# --------------------------------------------------------------------------- generator

class Gen:
    """One screenplay's random choices."""

    def __init__(self, seed: int) -> None:
        self.r = random.Random(seed)
        self.invalid = self.r.choice(INVALID_RATES)

    # ---- helpers

    def chance(self, p: float) -> bool:
        return self.r.random() < p

    def pick(self, items: list[Any]) -> Any:
        return self.r.choice(items)

    def oops(self, p: float) -> bool:
        """A structural mistake with chance ``p`` at the corpus's average invalid rate (0.15),
        scaled by this screenplay's rate (never, in a clean one)."""
        return self.chance(p * self.invalid / 0.15)

    def t(self, hi: float = 12.0) -> float:
        if self.chance(self.invalid / 15):  # the odd non-finite or negative time an LLM might write
            return self.pick([-1.0, float("nan"), float("inf")])
        return round(self.r.uniform(0.0, hi), 2)

    def dur(self) -> float:
        if self.chance(self.invalid / 5):
            return self.pick([0.0, -1.0, float("nan")])
        return round(self.r.uniform(0.2, 4.0) if not self.chance(0.05) else self.r.uniform(4.0, 60.0), 2)

    def bad(self) -> str:
        return self.pick(["nowhere", "ghost", "", "puppet:dad", "hymn_books.top", "s1"])

    # ---- parameter pools

    def value(self, param: str, verb: str, marks: list[str]) -> Any:
        """A value that suits ``param`` of ``verb``, or (``self.invalid`` of the time) a bad one."""
        if self.chance(self.invalid):
            # A wrong name, or a real name from the wrong pool.
            return self.pick([self.bad(), self.pick(CAST + PROPS + marks + POSES)])
        if param == "who":
            return self.pick(CAST)
        if param == "to":
            if verb == "hand_to":
                return self.pick(CAST)
            if verb == "exit":
                return self.pick(OFFSTAGE)
            return self.pick(marks)
        if param == "from":
            return self.pick(OFFSTAGE)
        if param == "what":
            return self.pick(WHAT.get(verb, PROPS))
        if param == "target":
            return self.pick(CAST if verb in PERSON_TARGET else CAST + PROPS + marks)
        if param == "on":
            return self.pick(["table", "table", "chair"] + marks)
        if param == "socket":
            return self.pick(SOCKET.get(verb, SOCKETS))
        if param == "pose":
            return self.pick(POSES)
        if param == "state":
            return self.pick(STATES)
        if param == "at":
            return self.pick(CAST + PROPS + marks)
        if param == "value":
            return self.pick(TIMES_OF_DAY)
        raise AssertionError(param)

    def action(self, verb: str, marks: list[str], n: int) -> dict:
        spec = VOCABULARY["actions"][verb]
        a: dict[str, Any] = {"t": self.t(), "do": verb}
        for p in spec["required"]:
            if self.chance(self.invalid / 3):
                continue  # missing
            a[p] = self.value(p, verb, marks)
        for p in spec["optional"]:
            if self.chance(0.4):
                a[p] = self.dur() if p == "dur" else self.value(p, verb, marks)
        if verb == "set_state" and "what" in a and a["what"] in PROP_STATES and not self.chance(self.invalid):
            a["state"] = self.pick(PROP_STATES[a["what"]])
        if a.get("who") in CAST and a.get("target") == a["who"] and not self.oops(0.3):
            a["target"] = self.pick([c for c in CAST if c != a["who"]])  # self targets mostly as mistakes
        if verb == "hand_to" and a.get("who") in CAST and a.get("to") == a["who"] and not self.oops(0.3):
            a["to"] = self.pick([c for c in CAST if c != a["who"]])
        if self.chance(self.invalid / 4):  # a parameter the verb does not take
            extra = self.pick(["who", "to", "from", "what", "target", "on", "socket", "pose", "state", "at", "value"])
            a.setdefault(extra, self.value(extra, verb, marks))
        if self.chance(0.35):
            a["id"] = f"a{n}" if not self.oops(0.05) else "a0"  # the odd duplicate id
        return a

    # ---- screenplay parts

    def start(self, marks: list[str]) -> dict:
        start: dict[str, dict] = {}
        for who in CAST:
            if not self.chance(0.7):
                continue
            s: dict[str, Any] = {"at": self.pick(marks + OFFSTAGE) if not self.oops(0.05) else self.bad()}
            if self.chance(0.3):
                s["holding"] = self.pick(HAND_PROPS) if not self.oops(0.1) else self.pick(PROPS)
            if self.chance(0.2):
                s["pose"] = self.pick(POSES) if not self.oops(0.1) else "dance"
            if self.chance(0.4):
                s["facing"] = self.pick(["left", "right"])
            if self.chance(0.15):
                s["in"] = self.pick(SOCKET_REFS) if not self.oops(0.2) else self.pick(ODD_SOCKET_REFS + [self.bad()])
            start[who] = s
        return start

    def prop_start(self, marks: list[str]) -> dict:
        out: dict[str, dict] = {}
        for prop in PROPS:
            if not self.chance(0.35):
                continue
            ps: dict[str, Any] = {}
            if self.chance(0.75):
                ps["at"] = self.pick(marks) if not self.oops(0.05) else self.bad()
            elif self.chance(0.8):
                ps["on"] = self.pick(SOCKET_REFS) if not self.oops(0.2) else self.pick(ODD_SOCKET_REFS)
            if self.chance(0.3) and (prop in PROP_STATES or self.oops(0.5)):
                ps["state"] = self.pick(PROP_STATES[prop]) if prop in PROP_STATES and not self.oops(0.1) else self.pick(STATES)
            out[prop] = ps
        return out

    def cue(self, marks: list[str]) -> dict:
        move = self.pick(MOVES)
        c: dict[str, Any] = {"t": self.t(10.0), "move": move}
        if VOCABULARY["camera"][move]["required"] or self.oops(0.05):
            if not self.oops(0.05):
                c["target"] = self.value("target", move, marks)
        if self.chance(0.4):
            c["dur"] = self.dur()
        return c

    def text(self) -> str:
        return " ".join(self.pick(WORDS) for _ in range(self.r.randint(2, 9))).capitalize() + "."

    def lines(self, shot: dict, author: str | None) -> list[dict]:
        out = []
        cues = [a["id"] for a in shot["actions"] if "id" in a] + [a["do"] for a in shot["actions"]]
        for _ in range(self.r.randint(1, 3)):
            text = self.text()
            line: dict[str, Any] = {"t": self.t(10.0), "text": text}
            if author is not None and self.chance(0.8):
                line["source"] = f"telling:{author}:{shot['id']}"
            if self.chance(0.4):
                line["cue"] = self.pick(cues) if cues and not self.oops(0.1) else self.pick(ACTIONS + ["nope"])
                if self.chance(0.6):
                    words = re.findall(r"[a-z]+", text.lower())
                    line["cueWord"] = self.pick(words) if not self.oops(0.1) else "zebra"
            out.append(line)
        return out

    def shot(self, i: int, prev_set: str | None) -> dict:
        if prev_set is None:
            set_id = self.pick(list(SET_MARKS))
        elif self.chance(0.5):
            set_id = prev_set  # same set again
        else:
            set_id = self.pick([s for s in SET_MARKS if s != prev_set])  # set change
        if self.oops(0.02):
            set_id = "moon"
        marks = list(SET_MARKS.get(set_id, YARD_MARKS))
        shot: dict[str, Any] = {"id": f"s{i + 1}", "set": set_id}
        if self.chance(0.1):
            spot = [round(self.r.uniform(-0.2, 1.2), 2), round(self.r.uniform(0, 0.4), 2) * self.chance(0.3)]
            shot["marks"] = {"extra_spot": spot + [round(self.r.uniform(-0.5, 0.5), 2)] * self.chance(0.3)}
            if self.chance(0.3):
                shot["marks"]["door"] = [0.15, 0.0]  # overrides a set mark
            if self.oops(0.2):
                shot["marks"]["broken"] = self.pick([[0.5], [], [0.5, 0.0, 0.0, 9.0], [float("nan"), 0.0]])
            marks += list(shot["marks"])
        shot["start"] = self.start(marks)
        shot["propStart"] = self.prop_start(marks)
        shot["actions"] = [self.action(self.pick(ACTIONS), marks, n) for n in range(self.r.randint(1, 8))]
        if self.chance(0.5):
            shot["camera"] = [self.cue(marks) for _ in range(self.r.randint(1, 3))]
        if self.chance(0.3):
            shot["transition"] = self.pick(TRANSITIONS)
        if self.chance(0.3):
            shot["pageText"] = self.text()
        return shot

    def screenplay(self) -> dict:
        shots: list[dict] = []
        prev = None
        for i in range(self.r.randint(1, 3)):
            shots.append(self.shot(i, prev))
            prev = shots[-1]["set"]
        if len(shots) > 1 and self.oops(0.03):
            shots[-1]["id"] = shots[0]["id"]  # duplicate shot id
        cast, props = list(CAST), list(PROPS)
        if self.oops(0.03):
            cast = self.r.sample(cast, 2) + ["ghost"] * self.chance(0.5)  # someone missing, or unknown
        if self.oops(0.03):
            props = self.r.sample(props, 5) + ["kite"] * self.chance(0.5)
        return self.dress({"memoryId": "fuzz", "cast": cast, "props": props, "shots": shots})

    def dress(self, sp: dict) -> dict:
        """Narration, a second teller, interactions and explanations, each sometimes."""
        cast = sp["cast"]
        default = cast[0] if cast else None
        if self.chance(0.5):
            default = self.pick(cast)
            for shot in sp["shots"]:
                if self.chance(0.7):
                    shot["narration"] = self.lines(shot, default)
        if self.chance(0.35) and len(cast) > 1:
            other = self.pick([c for c in cast if c != default]) if not self.oops(0.05) else default
            telling = {s["id"]: self.lines(s, other) for s in sp["shots"] if self.chance(0.8)}
            if self.oops(0.05):
                telling["s9"] = [{"t": 0.0, "text": "Nobody saw this shot."}]
            if telling:
                sp["narrations"] = {other: telling}
        if self.chance(0.15):
            shot = self.pick(sp["shots"])["id"] if not self.oops(0.1) else "s9"
            frm = self.t(8.0)
            drives: dict[str, Any] = self.pick([
                {"type": "scrub", "from": frm, "to": round(frm + 0.5, 2),
                 "targets": [self.pick([f"puppet:{c}" for c in CAST] + [f"prop:{p}" for p in PROPS])]},
                {"type": "prop_state", "prop": "lamp", "states": ["lit", "out"]},
                {"type": "prop_part", "prop": "sedan", "part": "wheel_front", "channel": "rot", "range": [0.0, 6.28]},
            ])
            if self.oops(0.2):
                drives = {"type": "scrub", "from": frm, "targets": ["puppet:ghost"]} if self.chance(0.5) else \
                    {"type": "prop_part", "prop": "kite", "part": "tail", "channel": "offset"}
            sp["interactions"] = [{
                "id": "tab", "kind": self.pick(VOCABULARY["interactionKinds"]), "label": "Pull it",
                "shot": shot, "at": [0.8, 0.8], "page": self.pick(["stage", "story"]), "drives": drives,
            }]
        if self.chance(0.15):
            target = self.pick([f"puppet:{c}" for c in CAST] + [f"prop:{p}" for p in PROPS] + ["puppet:ghost"] * self.oops(0.5))
            sp["explanations"] = [{"target": target, "text": self.text()}]
        return sp

    def perturbed(self, first: int) -> dict:
        """An ok verb case (sometimes two or three in a row, one after another as shots) with
        jittered times, camera cues, narration and a second teller. Always clean: these are
        meant to compile, so the timeline invariants get exercised."""
        self.invalid = 0.0
        picks = [OK_CASES[first % len(OK_CASES)]]
        if self.chance(0.4):
            picks += self.r.sample(OK_CASES, self.r.randint(1, 2))
        shots = [shot for c in picks for shot in c.screenplay.model_dump(mode="json", by_alias=True, exclude_none=True)["shots"]]
        for i, shot in enumerate(shots[:3]):
            shot["id"] = f"s{i + 1}"
        sp = {"memoryId": "fuzz", "cast": list(CAST), "props": list(PROPS), "shots": shots[:3]}
        on_stage = [w for s in sp["shots"] for w, st in s.get("start", {}).items() if not st["at"].startswith("offstage")]
        for shot in sp["shots"]:
            marks = SET_MARKS[shot["set"]]
            for a in shot["actions"]:
                a["t"] = round(max(0.0, a["t"] + self.r.uniform(-0.5, 2.0)), 2)
            if self.chance(0.6):
                move = self.pick(MOVES)
                cue: dict[str, Any] = {"t": self.t(6.0), "move": move}
                if VOCABULARY["camera"][move]["required"]:
                    cue["target"] = self.pick(on_stage + marks) if on_stage else self.pick(marks)
                if self.chance(0.5):
                    cue["dur"] = round(self.r.uniform(0.5, 3.0), 2)
                shot.setdefault("camera", []).append(cue)
            if self.chance(0.4):
                shot["transition"] = self.pick(TRANSITIONS)
            if self.chance(0.3):
                shot["pageText"] = self.text()
        return self.dress(sp)


OK_CASES = [c for c in CASES if c.ok]


@dataclass(frozen=True)
class Sample:
    kind: str  # "random" | "perturbed"
    index: int
    raw: dict
    screenplay: Screenplay | None  # None: the pydantic schema rejected it


def _seed(kind: str, i: int) -> int:
    return SEED * 100_003 + i + (0 if kind == "random" else 50_000)


def generate(kind: str, i: int) -> Sample:
    g = Gen(_seed(kind, i))
    raw = g.screenplay() if kind == "random" else g.perturbed(i)
    try:
        sp = Screenplay.model_validate(copy.deepcopy(raw))
    except ValidationError:
        sp = None
    return Sample(kind, i, raw, sp)


def corpus() -> list[Sample]:
    return [generate("random", i) for i in range(N_RANDOM)] + [generate("perturbed", i) for i in range(N_PERTURBED)]


# ------------------------------------------------------------------------- invariants

def _compile(sp: Screenplay, scene: Scene) -> CompileResult:
    return compile_screenplay(sp, sets=scene.sets, props=scene.props, people=scene.people)


def _non_finite(value: Any, path: str = "") -> list[str]:
    """Paths of every NaN or inf float inside a model_dump (python mode)."""
    if isinstance(value, float):
        return [] if math.isfinite(value) else [path]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _non_finite(v, f"{path}.{k}")]
    if isinstance(value, (list, tuple)):
        return [p for i, v in enumerate(value) for p in _non_finite(v, f"{path}[{i}]")]
    return []


def timeline_problems(tl: Timeline) -> list[str]:
    """Invariant 2. Empty when the timeline is sane."""
    out: list[str] = []
    try:
        Timeline.model_validate(json.loads(tl.model_dump_json()))
    except ValidationError as e:  # pragma: no cover - reported, not expected
        out.append(f"round trip: {e}")
    out += [f"non-finite at {p}" for p in _non_finite(tl.model_dump())]
    last = 0.0
    for tr in tl.tracks:
        times = [k[0] for k in tr.keys]
        if any(b < a for a, b in zip(times, times[1:])):
            out.append(f"{tr.target} {tr.channel}: key times decrease: {times}")
        if times:
            last = max(last, max(times))
    if not tl.duration >= last:
        out.append(f"duration {tl.duration} < last key time {last}")
    for a, b in zip(tl.shots, tl.shots[1:]):
        if abs(a.end - b.start) > 1e-6:
            out.append(f"shots not contiguous: {a.id} ends {a.end}, {b.id} starts {b.start}")
    if not tl.shots:
        out.append("no shots")
    return out


def error_problems(result: CompileResult) -> list[str]:
    """Invariant 3. Empty when every error is well-formed."""
    out: list[str] = []
    if not result.errors:
        out.append("not ok, but no errors")
    for e in result.errors:
        if not CODE.fullmatch(e.code):
            out.append(f"bad code {e.code!r}: {e.message}")
        if not (e.message.startswith("shot ") or e.message.startswith("screenplay")):
            out.append(f"bad message head: {e.message!r}")
        if e.message.startswith("shot ") and not e.shot:
            out.append(f"message names a shot but error.shot is unset: {e.message!r}")
    return out


def check(sample: Sample) -> tuple[CompileResult, list[str]]:
    """Run invariants 1 to 4 on one sample. Returns the result and every problem found."""
    assert sample.screenplay is not None
    sp, scene = sample.screenplay, base_scene()
    before = sp.model_dump_json()
    try:
        result = _compile(sp, scene)
    except Exception as e:  # invariant 1
        return CompileResult(ok=False), [f"compile raised {type(e).__name__}: {e}"]
    problems: list[str] = []
    if not isinstance(result, CompileResult):
        return CompileResult(ok=False), [f"compile returned {type(result).__name__}"]
    if result.ok:
        if result.timeline is None:
            problems.append("ok without a timeline")
        else:
            problems += timeline_problems(result.timeline)
        if result.errors:
            problems.append(f"ok with errors: {[e.message for e in result.errors]}")
    else:
        problems += error_problems(result)
    if sp.model_dump_json() != before:
        problems.append("compile mutated its screenplay")
    again = _compile(Screenplay.model_validate(copy.deepcopy(sample.raw)), base_scene())  # invariant 4
    if again.model_dump_json() != result.model_dump_json():
        problems.append("compiling the same input twice gave different JSON")
    return result, problems


# -------------------------------------------------------------------------------- tests

@pytest.fixture(scope="module")
def samples() -> list[Sample]:
    return corpus()


def test_the_generator_is_deterministic():
    for kind, i in [("random", 0), ("random", 17), ("perturbed", 3)]:
        a, b = generate(kind, i), generate(kind, i)
        assert json.dumps(a.raw, sort_keys=True) == json.dumps(b.raw, sort_keys=True)


def test_the_corpus_covers_every_verb_and_camera_move(samples):
    verbs = Counter(a["do"] for s in samples for shot in s.raw["shots"] for a in shot.get("actions", []))
    moves = Counter(c["move"] for s in samples for shot in s.raw["shots"] for c in shot.get("camera", []))
    assert sorted(set(ACTIONS) - set(verbs)) == []
    assert sorted(set(MOVES) - set(moves)) == []
    # Every verb and move shows up in screenplays the schema accepts, too.
    valid = [s.screenplay for s in samples if s.screenplay is not None]
    assert {a.do for sp in valid for shot in sp.shots for a in shot.actions} == set(ACTIONS)
    assert {c.move for sp in valid for shot in sp.shots for c in shot.camera} == set(MOVES)


def test_the_corpus_has_one_to_three_shots_set_changes_tellers_and_cues(samples):
    raws = [s.raw for s in samples if s.kind == "random"]
    assert {len(r["shots"]) for r in raws} == {1, 2, 3}
    pairs = [(a["set"], b["set"]) for r in raws for a, b in zip(r["shots"], r["shots"][1:])]
    assert any(a == b for a, b in pairs) and any(a != b for a, b in pairs)
    assert all(1 <= len(shot["actions"]) <= 8 for r in raws for shot in r["shots"])
    assert sum("narrations" in r for r in raws) > 50
    assert sum(any("cue" in ln for shot in r["shots"] for ln in shot.get("narration", [])) for r in raws) > 50
    assert sum(any(shot.get("camera") for shot in r["shots"]) for r in raws) > 200


def test_compile_never_raises_and_keeps_its_invariants(samples):
    t0 = time.perf_counter()
    failures: dict[str, list[str]] = {}
    stats: Counter[str] = Counter()
    codes: Counter[str] = Counter()
    for s in samples:
        if s.screenplay is None:
            stats["schema_rejected"] += 1
            continue
        if (s.kind, s.index) in EXCLUDED:
            stats["excluded"] += 1
            continue
        result, problems = check(s)
        stats[f"{s.kind}_{'ok' if result.ok else 'error'}"] += 1
        if result.ok and result.timeline is not None and len(result.timeline.shots) > 1:
            stats["ok_multishot"] += 1
        codes.update(e.code for e in result.errors)
        if problems:
            failures[f"{s.kind} {s.index} (seed {_seed(s.kind, s.index)})"] = problems
    elapsed = time.perf_counter() - t0
    print(f"\ncompile fuzz: {dict(sorted(stats.items()))} in {elapsed:.2f} s")
    print(f"error codes: {dict(codes.most_common())}")
    assert failures == {}
    # The corpus must exercise both sides, or invariants 2 and 3 are vacuous.
    assert stats["random_error"] > N_RANDOM // 2
    assert stats["random_ok"] + stats["perturbed_ok"] >= N_PERTURBED // 3
    assert stats["ok_multishot"] >= 100  # shot contiguity is checked on real multi-shot timelines
    assert stats["schema_rejected"] < (N_RANDOM + N_PERTURBED) // 10
    # Errors come from the world simulation as well as from static validation.
    assert len(codes) >= 25


EDGES: dict[str, dict] = {
    "no_shots": {"memoryId": "fuzz", "cast": ["dad"], "shots": []},
    "empty_cast": {"memoryId": "fuzz", "cast": [], "shots": [{"id": "s1", "set": "yard"}]},
    "unknown_cast": {"memoryId": "fuzz", "cast": ["ghost"], "shots": [{"id": "s1", "set": "yard"}]},
    "duplicate_cast": {"memoryId": "fuzz", "cast": ["dad", "dad"], "props": ["cap", "cap"],
                       "shots": [{"id": "s1", "set": "yard", "start": {"dad": {"at": "door", "holding": "cap"}},
                                  "actions": [{"t": 1, "do": "drop", "who": "dad", "what": "cap"}]}]},
    "no_actions": {"memoryId": "fuzz", "cast": ["dad"], "shots": [{"id": "s1", "set": "yard"}]},
    "prop_is_a_person": {"memoryId": "fuzz", "cast": ["dad"], "props": ["dad"], "shots": [{"id": "s1", "set": "yard"}]},
    "nan_mark": {"memoryId": "fuzz", "cast": ["dad"], "shots": [{
        "id": "s1", "set": "yard", "marks": {"spot": [float("nan"), 0.0]}, "start": {"dad": {"at": "door"}},
        "actions": [{"t": 1, "do": "walk_to", "who": "dad", "to": "spot"}]}]},
    "huge_t": {"memoryId": "fuzz", "cast": ["dad"], "shots": [{
        "id": "s1", "set": "yard", "start": {"dad": {"at": "door"}},
        "actions": [{"t": 1e12, "do": "walk_to", "who": "dad", "to": "curb"}]}]},
    "empty_narration_text": {"memoryId": "fuzz", "cast": ["dad"], "shots": [{
        "id": "s1", "set": "yard", "start": {"dad": {"at": "door"}},
        "actions": [{"t": 1, "do": "walk_to", "who": "dad", "to": "curb", "id": "go"}],
        "narration": [{"t": 0, "text": "", "cue": "go"}, {"t": 0, "text": "...", "cue": "walk_to"}]}]},
}


@pytest.mark.parametrize("name", list(EDGES))
def test_hand_written_edge_shapes_keep_the_invariants(name):
    raw = EDGES[name]
    sample = Sample("edge", 0, raw, Screenplay.model_validate(copy.deepcopy(raw)))
    _, problems = check(sample)
    assert problems == []


# ------------------------------------------------------------- scripted demo variants

def _hymn() -> tuple[dict, dict, Scene]:
    sample = demo.load_sample()
    sp = sample.memory.screenplay.model_dump(mode="json", by_alias=True, exclude_none=True)
    tl = sample.memory.timeline.model_dump(mode="json", by_alias=True, exclude_none=True)
    scene = Scene(
        sets={s.id: s for s in sample.sets},
        props={p.id: p for p in sample.props},
        people={p.id: p for p in sample.people},
    )
    return sp, tl, scene


def _messages(sp: dict, scene: Scene) -> list[str]:
    result = _compile(Screenplay.model_validate(sp), scene)
    assert result.ok == (result.errors == [])
    return [e.message for e in result.errors]


def test_the_hymn_sample_itself_compiles():
    sp, _, scene = _hymn()
    assert _messages(sp, scene) == []


def _hymn_props() -> list[str]:
    return [p.id for p in demo.load_sample().props]


@pytest.mark.parametrize("prop_id", _hymn_props())
def test_strip_prop_variant_compiles(prop_id):
    """fake_runs strips the sample's prop when the reviewer removes it (props_found edit)."""
    sp, tl, scene = _hymn()
    stripped, _ = strip_prop(sp, tl, prop_id)
    assert prop_id not in stripped["props"]
    assert _messages(stripped, scene) == []


def test_strip_person_variant_compiles():
    """fake_runs strips the sample's dad, keeping mom's telling, for a family of one."""
    sp, tl, scene = _hymn()
    stripped, _ = strip_person(sp, tl, SAMPLE_OTHER, keep_narrator=SAMPLE_TELLER)
    assert stripped["cast"] == [SAMPLE_TELLER]
    assert _messages(stripped, scene) == []


@pytest.mark.parametrize("prop_id", _hymn_props())
def test_strip_prop_then_person_variant_compiles(prop_id):
    """Both at once: a family of one that also removed the prop."""
    sp, tl, scene = _hymn()
    sp, tl = strip_prop(sp, tl, prop_id)
    stripped, _ = strip_person(sp, tl, SAMPLE_OTHER, keep_narrator=SAMPLE_TELLER)
    assert _messages(stripped, scene) == []


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "seed/fake_runs.py:strip_person: drops a camera cue's target when it names the stripped "
        "person and keeps the cue, so a dolly_to/pan_to/close_up is left without its required "
        'target: "shot s1, t=5.5: cannot dolly_to; dolly_to requires target; "target" is missing". '
        "The scripted demo never strips mom (the teller), so the demo itself is unaffected."
    ),
)
def test_strip_person_variant_for_the_teller_compiles():
    sp, tl, scene = _hymn()
    stripped, _ = strip_person(sp, tl, SAMPLE_TELLER, keep_narrator=SAMPLE_OTHER)
    assert stripped["cast"] == [SAMPLE_OTHER]
    assert _messages(stripped, scene) == []
