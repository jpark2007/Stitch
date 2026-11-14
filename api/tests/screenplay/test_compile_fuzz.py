"""Fuzz compile(): it never raises, its timelines are sane, its errors are well-formed and
placed, foot planting changes only what it should, and it is deterministic (ARCHITECTURE.md
section 3). Also compiles the scripted demo variants of the hymn sample (seed.fake_runs
strip_prop / strip_person).

The directing agent hands compile() arbitrary LLM-written screenplays, so this is a regression
net over random ones. The generators are plain ``random.Random(seed)``: no new dependency, and
the corpus is the same on every run. Three kinds of screenplay come out of them:

- random (on verb_cases.base_scene(), with its CAST, PROPS, YARD_MARKS and STREET_MARKS): 1 to 3
  shots (sometimes the same set twice, sometimes a set change) with random start blocks and
  propStart, 1 to 8 actions per shot drawn from every vocabulary verb, parameters mostly valid
  names with about 15% invalid or missing on average (each screenplay draws its rate from
  INVALID_RATES, so a clean third reaches the world simulation), t in [0, 12], optional dur, and
  sometimes camera cues, cued narration, a second teller, interactions and explanations. Nearly
  all of these fail, statically or in the world simulation, which exercises the error paths.
- perturbed (base_scene()): one to three ok verb cases played as consecutive shots, with
  jittered times, extra camera cues, narration and a second teller. About half compile, which
  exercises the timeline invariants (including shot contiguity on multi-shot timelines).
- story (story_scenes.story_scene(), read-only: eight sets, raised marks, a car, furniture with
  seat and surface sockets, boxes gripped low): StoryGen writes a 2 to 4 shot family story that
  is meant to compile. It models who is on stage, seated, kneeling and holding what, and where
  each prop is, and schedules each puppet's next action after its last one ends. Shots switch
  sets about two thirds of the time; props are carried across a set change by listed puppets,
  dropped or set down before a fold, left behind and brought back with propStart; people exit in
  one shot and enter in the next, sit in one and stand up in the next, ride the car across a set
  change. Narration (sometimes cued), a second teller, camera cues and scrub tabs are added. About
  95% compile, so every timeline invariant runs on long, many-verb, multi-set timelines.
  test_the_story_corpus_switches_sets_carries_drops_enters_and_sits checks that mix.

A few hand-written edge shapes (no shots, empty or unknown cast, NaN marks, huge t, empty
narration text) go through the same checks.

Each screenplay is built through ``Screenplay.model_validate`` on a dict, so the schema runs
too; only inputs the pydantic schema itself rejects are skipped (and counted).

Invariants, for every generated screenplay (``check``):

1. compile(...) returns a CompileResult and never raises (with plant_feet=False too).
2. ok: the timeline survives a JSON round trip through Timeline.model_validate; per track, key
   times never decrease; no NaN or inf anywhere; duration >= the last key time; shots are
   contiguous (shot[i].end == shot[i + 1].start within 1e-6); and
   timeline_sanity.sanity_problems(timeline, props=<the scene's props>) == [] (no prop in two
   places or a socket holding two, a position key at every parent key, every time within the
   timeline, nobody walking while seated, finite values, no part of a prop's drawn width showing
   on a set it was not placed on).
3. not ok: there is at least one error; every code matches ^[a-z]+(_[a-z]+)*$; every message
   starts with "shot " or "screenplay"; error.shot is set whenever the message starts "shot ".
4. compiling the same input again (a fresh copy of the screenplay and scene) gives
   byte-identical model_dump_json, and compile() does not mutate its input.
5. planting: compile(plant_feet=False) agrees with the default compile on ok-ness and on the
   exact errors; on ok, feetPlanted is True on the planted timeline and unset on the unplanted
   one, and test_foot_planting.planting_problems(planted, unplanted, builds) == [] (only puppet
   root y differs, and at every walk / run cycle key the lowest foot is on the unplanted surface).
6. not ok (error_site_problems): every error.shot is one of the screenplay's shot ids or None;
   an error.t comes with a shot, is finite and lies within that shot's action span (its first to
   its last action or camera cue time as written, since errors report the written t); the
   message head names the same shot, with ``t=`` when error.t is set; a head ``t=`` without an
   error.t is a time that is not finite (``t=inf``), which error.t leaves out so JSON keeps it.

Size: DEFAULT_SIZES (random 8000, perturbed 3500, story 250) keep the file to roughly 25 to 30 s
of CPU. ``STITCH_FUZZ_N=<total>`` runs a larger sweep split in the same proportions, and
``STITCH_FUZZ_START=<i>`` starts every kind at index i (fresh seeds); seeds outside the default
range are not in EXCLUDED, so a larger sweep reports every known-bug seed it hits as a failure.

Known failures (crashes or broken invariants whose cause is in api/screenplay or api/seed code
owned by other tasks) get a minimal hand-written reproduction marked xfail(strict=True) at the
bottom, naming the module and function, and their seeds go in EXCLUDED so the random loop
passes. None is open in the compiler: EXCLUDED is empty. The wave-2 reproductions were retired
when wave 3 fixed them, each with passing tests in its own task's file: a prop first placed in a
later shot showing from t=0 (props-across-sets: compiler._park_until_placed, and
timeline_sanity.check_props_on_set in CHECKS); a non-finite error.t (errors.error_t: t is None,
test_an_error_on_a_non_finite_t_has_a_json_safe_t below); place_on onto one's own mark, a mark
behind, or with the left hand (place-on-reach); propStart swapping a socket's prop in either
order (state-propstart-and-object-wording); get_out and stand_up by the stage edge
(stage-edge-dismount); pick_up from a raised mark (raised-mark-reach). Opening a box held in
one's own hand is not a bug: it is out_of_reach by design, and the agent is taught never to
write it (api/tests/agent/test_agent_real_compiler.py's open_held_prop case and
test_agent_skills.py::test_open_and_close_are_never_taught_on_a_held_prop pin it), so it has no
reproduction here. StoryGen still steers around the place_on and open/close
shapes (a_place_on, a_open_close) until those fixes are on main. The one known failure left is
the scripted demo variant test_strip_person_variant_for_the_teller_compiles (seed/fake_runs.py,
owned by the seed stream).
"""

from __future__ import annotations

import copy
import itertools
import json
import math
import os
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
from story_scenes import CAST as STORY_CAST, SETS as STORY_SET_DEFS, story_scene
from test_foot_planting import planting_problems
from timeline_sanity import sanity_problems
from verb_cases import CASES, CAST, PROPS, STREET_MARKS, YARD_MARKS, Scene, base_scene

# ----------------------------------------------------------------------- corpus size

SEED = 20261113
"""Seed of the whole corpus: random screenplay i uses random.Random(SEED * 100_003 + i), perturbed
screenplay i random.Random(SEED * 100_003 + 50_000 + i) (see _seed)."""

DEFAULT_SIZES = {"random": 8000, "perturbed": 3500, "story": 250}
"""Screenplays of each kind in the default run (see the printed line of
test_compile_never_raises_and_keeps_its_invariants with pytest -s for the time it takes)."""


def _sizes() -> tuple[dict[str, int], int]:
    """The corpus size of each kind, and the first index.

    ``STITCH_FUZZ_N=<total>`` runs a larger (or smaller) sweep: ``total`` screenplays split
    across the kinds in the default proportions. ``STITCH_FUZZ_START=<i>`` starts every kind at
    index ``i`` instead of 0, to sweep fresh seeds. For example
    ``STITCH_FUZZ_N=20000 uv run pytest tests/screenplay/test_compile_fuzz.py -s``."""
    total = os.environ.get("STITCH_FUZZ_N", "").strip()
    start = int(os.environ.get("STITCH_FUZZ_START", "0") or 0)
    if not total:
        return dict(DEFAULT_SIZES), start
    scale = int(total) / sum(DEFAULT_SIZES.values())
    return {k: max(1, round(n * scale)) for k, n in DEFAULT_SIZES.items()}, start


SIZES, OFFSET = _sizes()
N_RANDOM, N_PERTURBED, N_STORY = SIZES["random"], SIZES["perturbed"], SIZES["story"]

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
# reproduction below: {(kind, index): "reason"}, the reason naming the module, the function and
# the reproduction test, e.g. {("story", 16): "compiler._start_keys: ... (test_...)"}. Check
# that every excluded seed of the default corpus fails only that way. Empty: no known bug
# breaks a seed of the default corpus.
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


# ------------------------------------------------------------ multi-shot story generator

# The story scene (story_scenes.story_scene(), read-only): eight sets, raised marks (the church
# steps, the graduation stage), moving boxes gripped halfway up (floor pick-ups need a crouch),
# a car with two seats, furniture with seat and surface sockets.
STORY_MARKS: dict[str, dict[str, tuple[float, float]]] = {
    sid: {m: (round(2 * v[0] - 1, 6), float(v[1])) for m, v in s["marks"].items()} for sid, s in STORY_SET_DEFS.items()
}
OFF_X = {"offstage_left": -1.3, "offstage_right": 1.3}
STORY_HAND = ["bouquet", "hymn_books", "umbrella", "diploma", "cup", "kettle", "keys", "cap", "cake", "gift",
              "box_a", "box_b"]
STORY_SEATS: dict[str, list[str]] = {"chair": ["seat"], "armchair": ["seat"], "pew": ["seat_aisle", "seat_wall"]}
STORY_SURFACES: dict[str, str] = {"table": "top", "picnic_table": "top", "stove": "burner"}
STORY_FURNITURE = list(STORY_SEATS) + list(STORY_SURFACES)
STORY_STATES: dict[str, list[str]] = {"cake": ["unlit", "lit"], "gift": ["closed", "open"], "box_a": ["closed", "open"],
                                      "box_b": ["closed", "open"], "car": ["parked", "driving"]}
OPENABLE = ("gift", "box_a", "box_b")
STORY_POSES = ["wave", "point", "arms_raised", "bow_head", "slumped", "reach", "hold", "kneel", "hug", "stand"]
CAR_SEATS = ("driver", "passenger")

# Conservative speeds for the generator's own schedule (stage units / s): the slowest walker in
# the story cast (grandpa, "walks slowly with a cane", 0.6 x 0.35) and a slow run.
GEN_WALK, GEN_RUN, GEN_DRIVE = 0.19, 0.42, 0.6


@dataclass
class _Who:
    """The story generator's own model of one cast member (approximate: it only has to keep
    most screenplays valid, the compiler is the judge)."""

    x: float | None = None  # None: offstage
    y: float = 0.0
    seat: tuple[str, str] | None = None  # (prop, socket), the car's included
    kneeling: bool = False
    hands: list[str] | None = None
    head: str | None = None
    free: float = 0.0  # shot time this puppet is free again
    exact: bool = True  # x is exactly a mark's (not wherever an auto-walk stopped)

    def __post_init__(self) -> None:
        self.hands = [] if self.hands is None else self.hands


@dataclass
class _Thing:
    """Where a prop is: off (not on stage), ground (at x), on (a surface socket), held, worn."""

    where: str = "off"
    x: float | None = None
    holder: str | None = None
    on: str | None = None  # "<prop>.<socket>"
    state: str | None = None


class StoryGen:
    """A 2 to 4 shot family story on the story scene, written to compile: it tracks who is on
    stage, seated, kneeling and holding what, where each prop is, and schedules each puppet's
    actions after its previous one ends (by a conservative estimate). Shots switch sets (about
    two thirds of the time) or stay on the same set; props are carried between shots by
    listed puppets, dropped or placed before a fold, left behind on the old set, brought back
    with propStart; people exit in one shot and enter in the next, sit down in one and stand up
    in the next, ride the car across a set change. Narration (sometimes cued), a second teller,
    camera cues and scrub interactions are added too. Everything a family story would do."""

    def __init__(self, seed: int) -> None:
        self.r = random.Random(seed)
        self.cast: list[str] = self.r.sample(STORY_CAST, self.r.randint(2, 4))
        props = self.r.sample(STORY_HAND, self.r.randint(2, 5)) + self.r.sample(STORY_FURNITURE, self.r.randint(0, 2))
        if self.r.random() < 0.4:
            props.append("car")
        self.props: list[str] = props
        self.who = {w: _Who() for w in self.cast}
        self.thing = {p: _Thing() for p in self.props}
        self.marks: dict[str, tuple[float, float]] = {}
        self.ground: list[str] = []
        self.clock = 0.0
        self.n = 0
        self.frozen: set[str] = set()  # leader and follower of a follow: they act no more this shot

    # ---- helpers

    def chance(self, p: float) -> bool:
        return self.r.random() < p

    def pick(self, items: list[Any]) -> Any:
        return self.r.choice(items)

    def on_stage(self, w: str) -> bool:
        return self.who[w].x is not None

    def active(self, w: str) -> bool:
        """On stage and free to take part in another action this shot."""
        return self.who[w].x is not None and w not in self.frozen

    def standing(self, w: str) -> bool:
        p = self.who[w]
        return self.active(w) and p.seat is None and not p.kneeling

    def placed(self, what: str) -> bool:
        return self.thing[what].where in ("ground", "on") and self.thing[what].x is not None

    def x_of(self, what: str) -> float | None:
        th = self.thing[what]
        if th.where == "held" or th.where == "worn":
            return self.who[th.holder].x  # type: ignore[index]
        return th.x

    def schedule(self, who: list[str], est: float) -> float:
        """The next action's t: after the previous action started and after everyone in ``who``
        is free; they are then busy for ``est`` seconds."""
        t = max([self.clock] + [self.who[w].free for w in who]) + self.r.uniform(0.0, 0.6)
        t = round(t, 2)
        self.clock = t
        for w in who:
            self.who[w].free = t + est
        return t

    def walk_time(self, a: float | None, b: float | None, *, run: bool = False, dy: float = 0.0) -> float:
        if a is None or b is None:
            return 2.0
        return 1.0 + (abs(a - b) + abs(dy)) / (GEN_RUN if run else GEN_WALK)

    def act(self, acts: list[dict], busy: list[str], est: float, do: str, **params: Any) -> dict:
        a: dict[str, Any] = {"t": self.schedule(busy, est), "do": do, **{k: v for k, v in params.items() if v is not None}}
        if "frm" in a:
            a["from"] = a.pop("frm")
        if self.chance(0.4):
            self.n += 1
            a["id"] = f"{do}_{self.n}"
        acts.append(a)
        return a

    def free_mark(self, x: float | None, *, raised: bool = False) -> str | None:
        pool = list(self.marks) if raised else self.ground
        choices = [m for m in pool if x is None or abs(self.marks[m][0] - x) > 0.08]
        return self.pick(choices) if choices else None

    def free_socket(self, what: str, sockets: list[str]) -> str | None:
        taken = {p.seat[1] for p in self.who.values() if p.seat is not None and p.seat[0] == what}
        taken |= {th.on.split(".", 1)[1] for th in self.thing.values() if th.where == "on" and th.on
                  and th.on.split(".", 1)[0] == what}
        free = [s for s in sockets if s not in taken]
        return self.pick(free) if free else None

    def riders(self, what: str) -> list[str]:
        return [w for w, p in self.who.items() if p.seat is not None and p.seat[0] == what]

    def put(self, what: str, where: str, *, x: float | None = None, holder: str | None = None, on: str | None = None) -> None:
        """Move a prop in the model, along with anything resting on it."""
        th = self.thing[what]
        if th.where == "held" and th.holder is not None and what in self.who[th.holder].hands:  # type: ignore[operator]
            self.who[th.holder].hands.remove(what)  # type: ignore[union-attr]
        if th.where == "worn" and th.holder is not None and self.who[th.holder].head == what:
            self.who[th.holder].head = None
        th.where, th.x, th.holder, th.on = where, x, holder, on
        if where == "held":
            self.who[holder].hands.append(what)  # type: ignore[index, union-attr]
        if where == "worn":
            self.who[holder].head = what  # type: ignore[index]
        for o in self.thing.values():
            if o.where == "on" and o.on and o.on.split(".", 1)[0] == what:
                o.x = th.x if where in ("ground", "on") else None
                if where == "off":
                    o.where, o.on = "off", None

    # ---- the shot's start

    def begin(self, sid: str, set_id: str, same: bool) -> dict:
        self.marks = dict(STORY_MARKS[set_id])
        self.ground = [m for m, (_, y) in self.marks.items() if y == 0.0]
        self.extra: dict[str, list[float]] = {}
        self.clock = 0.0
        self.frozen = set()
        for p in self.who.values():
            p.free = 0.0
        shot: dict[str, Any] = {"id": sid, "set": set_id}
        start: dict[str, dict] = {}
        prop_start: dict[str, dict] = {}
        if not same:
            listed = [w for w, p in self.who.items()
                      if self.chance(0.8 if (p.hands or (p.seat and p.seat[0] == "car")) else 0.55)]
            if not listed:
                listed = [self.pick(self.cast)]
            car_riders = [w for w in listed if self.who[w].seat is not None and self.who[w].seat[0] == "car"]  # type: ignore[index]
            bring_car = bool(car_riders) and self.chance(0.6)
            for w, p in self.who.items():
                if w not in listed:  # waits offstage; hand-held props stay behind (and leave the stage)
                    for what in list(p.hands):  # type: ignore[union-attr]
                        self.put(what, "off")
                    p.x, p.y, p.seat, p.kneeling = None, 0.0, None, False
            keep = {what for w in listed for what in self.who[w].hands} | {p.head for p in self.who.values() if p.head}  # type: ignore[union-attr]
            # Steering: where each prop stood on the ground on the old set. A propStart that puts
            # it back at the same place on the new set gets no key (compiler._hold_then skips an
            # unchanged value), so timeline_sanity.check_props_on_set cannot tell it from a prop
            # left over from the old set (test_story_scenarios.
            # test_a_prop_put_back_at_the_same_x_on_a_new_set_is_keyed_there). Marks near its old
            # x are avoided (0.2: a drop lands a little off the dropper's mark) until
            # compiler._start_keys keys every propStart on a set change.
            was_at = {what: th.x for what, th in self.thing.items() if th.where == "ground" and th.x is not None}

            def fresh(what: str, marks: list[str]) -> list[str]:
                return [m for m in marks if what not in was_at or abs(self.marks[m][0] - was_at[what]) > 0.2]

            for what, th in self.thing.items():
                if what not in keep and th.where in ("ground", "on"):
                    self.put(what, "off")
            for w in listed:  # listing unseats (the car riders are re-seated below)
                self.who[w].seat, self.who[w].kneeling = None, False
            used: set[str] = set()
            furniture = [f for f in self.props if f in STORY_FURNITURE]
            for f in furniture:
                if self.chance(0.65):
                    pool = fresh(f, [g for g in self.ground if g not in used]) or fresh(f, self.ground)
                    if not pool:
                        continue
                    m = self.pick(pool)
                    used.add(m)
                    prop_start[f] = {"at": m}
                    self.put(f, "ground", x=self.marks[m][0])
            car_mark = None
            if "car" in self.props and (bring_car or self.chance(0.35)) and fresh("car", self.ground):
                car_mark = self.pick(fresh("car", self.ground))
                prop_start["car"] = {"at": car_mark}
                self.put("car", "ground", x=self.marks[car_mark][0])
                if self.chance(0.3):
                    prop_start["car"]["state"] = self.thing["car"].state = self.pick(STORY_STATES["car"])
            surfaces = [f for f in furniture if f in STORY_SURFACES and f in prop_start]
            for h in self.props:
                th = self.thing[h]
                if h not in STORY_HAND or th.where != "off" or not self.chance(0.5):
                    continue
                sock = None
                if surfaces and self.chance(0.5):
                    base = self.pick(surfaces)
                    sock = self.free_socket(base, [STORY_SURFACES[base]])
                if sock is not None:
                    prop_start[h] = {"on": f"{base}.{sock}"}
                    self.put(h, "on", x=self.thing[base].x, on=f"{base}.{sock}")
                elif fresh(h, self.ground):
                    m = self.pick(fresh(h, self.ground))
                    prop_start[h] = {"at": m}
                    self.put(h, "ground", x=self.marks[m][0])
                else:
                    continue
                if h in STORY_STATES and self.chance(0.3):
                    prop_start[h]["state"] = th.state = self.pick(STORY_STATES[h])
            seats_free = {f: list(STORY_SEATS[f]) for f in furniture if f in STORY_SEATS and f in prop_start}
            car_free = list(CAR_SEATS) if car_mark else []
            for w in listed:
                p = self.who[w]
                s: dict[str, Any]
                if w in car_riders and bring_car and car_free:
                    sock = car_free.pop(0) if self.chance(0.7) else car_free.pop()
                    s = {"at": car_mark, "in": f"car.{sock}"}
                    p.x, p.y, p.seat = self.marks[car_mark][0], 0.0, ("car", sock)  # type: ignore[index]
                elif not p.hands and self.chance(0.15):
                    side = self.pick(list(OFF_X))
                    s = {"at": side}
                    p.x, p.y = None, 0.0
                else:
                    m = self.pick(self.ground)
                    s = {"at": m}
                    p.x, p.y = self.marks[m]
                    seats = [f for f, socks in seats_free.items() if socks]
                    if seats and self.chance(0.2):
                        f = self.pick(seats)
                        sock = seats_free[f].pop(0)
                        s["in"] = f"{f}.{sock}"
                        p.seat, p.x = (f, sock), self.thing[f].x
                if self.chance(0.4):
                    s["facing"] = self.pick(["left", "right"])
                loose = [h for h in self.props if h in STORY_HAND and self.thing[h].where == "off"
                         and h not in prop_start and h not in keep]
                if loose and len(p.hands) < 2 and self.chance(0.25):  # type: ignore[arg-type]
                    h = self.pick(loose)
                    s["holding"] = h
                    keep.add(h)
                    self.put(h, "held", holder=w)
                start[w] = s
        else:
            for w, p in self.who.items():
                if self.chance(0.25) and not (p.seat and p.seat[0] == "car"):
                    m = self.pick(self.ground)
                    start[w] = {"at": m}
                    p.x, p.y = self.marks[m]
                    p.seat, p.kneeling = None, False
            movable = [f for f in self.props if f in STORY_FURNITURE and self.placed(f)]
            if movable and self.chance(0.2):  # move a piece of furniture; whoever sits on it rides along
                f = self.pick(movable)
                m = self.pick(self.ground)
                prop_start[f] = {"at": m}
                self.put(f, "ground", x=self.marks[m][0])
                for w in self.riders(f):
                    if w not in start:
                        self.who[w].x = self.marks[m][0]
        shot["start"], shot["propStart"] = start, prop_start
        return shot

    # ---- actions

    def options(self) -> list[tuple[float, Any]]:
        out: list[tuple[float, Any]] = []
        act = [w for w in self.cast if self.active(w)]
        stand = [w for w in self.cast if self.standing(w)]
        if any(self.who[w].x is None for w in self.cast):
            out.append((1.5, self.a_enter))
        if stand:
            out += [(2.5, self.a_walk), (0.6, self.a_exit), (0.6, self.a_kneel)]
        if act:
            out += [(1.0, self.a_gesture), (0.6, self.a_point), (0.4, self.a_turn)]
        if len(stand) >= 2:
            out += [(0.5, self.a_follow), (0.6, self.a_hug)]
        if any(self.who[w].kneeling or (self.who[w].seat and self.who[w].seat[0] != "car") for w in act):
            out.append((2.5, self.a_stand_up))
        if any(self.placed(h) and h in STORY_HAND for h in self.props) and any(len(self.who[w].hands) < 2 for w in stand):  # type: ignore[arg-type]
            out.append((2.5, self.a_pick_up))
        if any(self.who[w].hands for w in stand):
            out += [(1.2, self.a_drop), (1.2, self.a_hand_to), (1.2, self.a_place_on), (0.4, self.a_hold)]
        if "cap" in self.thing and self.thing["cap"].where == "held":
            out.append((0.8, self.a_put_on))
        if any(f in STORY_SEATS and self.placed(f) for f in self.props) and stand:
            out.append((1.5, self.a_sit_on))
        if stand and any(h in self.thing and self.thing[h].where == "on" for h in OPENABLE):
            out.append((1.0, self.a_open_close))
        if any(h in STORY_STATES for h in self.props):
            out.append((0.4, self.a_set_state))
        if "car" in self.thing and self.placed("car"):
            if stand:
                out.append((1.5, self.a_get_in))
            if any(self.active(w) for w in self.riders("car")):
                out += [(2.0, self.a_drive), (1.0, self.a_get_out)]
        out.append((0.5, self.a_effect))
        return out

    def goto(self, acts: list[dict], w: str, m: str) -> None:
        """``w`` walks to mark ``m`` (nothing when already standing exactly there)."""
        p = self.who[w]
        x, y = self.marks[m]
        if p.exact and p.x is not None and abs(p.x - x) < 1e-9 and p.y == y:
            return
        self.act(acts, [w], self.walk_time(p.x, x, dy=y - p.y), "walk_to", who=w, to=m)
        p.x, p.y, p.exact, p.kneeling = x, y, True, False

    def approach(self, acts: list[dict], w: str, x: float, lo: float = 0.1, hi: float = 0.2) -> None:
        """Get ``w`` standing on a ground mark ``lo`` to ``hi`` from ``x``, walking there if not
        already. With no such mark in the set, the shot gets one (Shot.marks), on ``w``'s side."""
        p = self.who[w]
        if p.exact and p.x is not None and p.y == 0.0 and lo <= abs(p.x - x) <= hi:
            return
        near = [m for m in self.ground if lo <= abs(self.marks[m][0] - x) <= hi]
        if not near:
            side = 1.0 if (p.x if p.x is not None else 0.0) >= x else -1.0
            mx = x + side * (lo + hi) / 2
            if not -0.95 <= mx <= 0.95:
                mx = x - side * (lo + hi) / 2
            name = f"spot_{len(self.extra) + 1}"
            self.extra[name] = [round((mx + 1) / 2, 4), 0.0]
            self.marks[name] = (round(2 * self.extra[name][0] - 1, 6), 0.0)
            self.ground.append(name)
            near = [name]
        self.goto(acts, w, self.pick(near))

    def face(self, acts: list[dict], w: str, target: str) -> None:
        """turn_to: place_on and open do not turn toward what they reach for."""
        self.act(acts, [w], 1.2, "turn_to", who=w, target=target)

    def a_enter(self, acts: list[dict]) -> None:
        w = self.pick([w for w in self.cast if self.who[w].x is None])
        m = self.pick(self.ground)
        side = self.pick(list(OFF_X)) if self.chance(0.6) else None
        x = self.marks[m][0]
        est = self.walk_time(OFF_X[side or ("offstage_left" if x < 0 else "offstage_right")], x)
        self.act(acts, [w], est, "enter", who=w, to=m, frm=side)
        p = self.who[w]
        p.x, p.y, p.kneeling, p.exact = x, 0.0, False, True

    def a_exit(self, acts: list[dict]) -> None:
        w = self.pick([w for w in self.cast if self.standing(w)])
        p = self.who[w]
        side = self.pick(list(OFF_X)) if self.chance(0.5) else None
        est = self.walk_time(p.x, OFF_X[side or ("offstage_left" if (p.x or 0) < 0 else "offstage_right")])
        self.act(acts, [w], est, "exit", who=w, to=side)
        p.x, p.y = None, 0.0

    def a_walk(self, acts: list[dict]) -> None:
        w = self.pick([w for w in self.cast if self.standing(w)])
        p = self.who[w]
        m = self.free_mark(p.x, raised=self.chance(0.3))
        if m is None:
            return
        run = self.chance(0.3)
        x, y = self.marks[m]
        self.act(acts, [w], self.walk_time(p.x, x, run=run, dy=y - p.y) + 0.5, "run_to" if run else "walk_to", who=w, to=m)
        p.x, p.y, p.exact = x, y, True

    def a_follow(self, acts: list[dict]) -> None:
        leader, follower = self.r.sample([w for w in self.cast if self.standing(w)], 2)
        lp, fp = self.who[leader], self.who[follower]
        m = self.free_mark(lp.x)
        if m is None:
            return
        x, y = self.marks[m]
        est = self.walk_time(lp.x, x, dy=y - lp.y)
        dur = round(self.r.uniform(1.0, est), 2) if self.chance(0.3) else None
        a = self.act(acts, [leader, follower], est + self.walk_time(fp.x, x) + 1.5, "walk_to", who=leader, to=m)
        f: dict[str, Any] = {"t": a["t"], "do": "follow", "who": follower, "target": leader}
        if dur is not None:
            f["dur"] = dur
        acts.append(f)
        lp.x, lp.y = x, y
        fp.x, fp.y, fp.exact = x, y, False
        self.frozen |= {leader, follower}  # a follow reads its leader's whole path: keep both out of the rest

    def a_gesture(self, acts: list[dict]) -> None:
        w = self.pick([w for w in self.cast if self.active(w)])
        dur = round(self.r.uniform(0.8, 3.0), 2) if self.chance(0.6) else None
        if self.chance(0.5) or not self.standing(w):
            others = [o for o in self.cast if o != w and self.who[o].x is not None]
            target = self.pick(others) if others and self.chance(0.6) else None
            self.act(acts, [w], (dur or 2.0) + 1.5, "wave", who=w, target=target, dur=dur)
        else:
            pose = self.pick(STORY_POSES)
            self.act(acts, [w], (dur or 2.0) + 1.5, "pose", who=w, pose=pose, dur=dur)
            if pose == "kneel" and dur is None:
                self.who[w].kneeling = True

    def a_kneel(self, acts: list[dict]) -> None:
        w = self.pick([w for w in self.cast if self.standing(w)])
        dur = round(self.r.uniform(1.0, 3.0), 2) if self.chance(0.5) else None
        self.act(acts, [w], (dur or 1.5) + 2.0, "kneel", who=w, dur=dur)
        if dur is None:
            self.who[w].kneeling = True

    def a_stand_up(self, acts: list[dict]) -> None:
        w = self.pick([w for w in self.cast if self.active(w) and (self.who[w].kneeling or (
            self.who[w].seat and self.who[w].seat[0] != "car"))])
        self.act(acts, [w], 2.5, "stand_up", who=w)
        self.who[w].seat, self.who[w].kneeling = None, False

    def _target(self, w: str) -> str:
        people = [o for o in self.cast if o != w and self.who[o].x is not None]
        things = [h for h in self.props if self.placed(h)]
        return self.pick(people * 2 + things + list(self.marks))

    def a_point(self, acts: list[dict]) -> None:
        w = self.pick([w for w in self.cast if self.active(w)])
        dur = round(self.r.uniform(0.8, 2.5), 2) if self.chance(0.5) else None
        verb = self.pick(["point_at", "lean_toward"])
        self.act(acts, [w], (dur or 2.0) + 1.5, verb, who=w, target=self._target(w), dur=dur)

    def a_turn(self, acts: list[dict]) -> None:
        w = self.pick([w for w in self.cast if self.active(w)])
        self.act(acts, [w], 1.5, "turn_to", who=w, target=self._target(w))

    def a_hug(self, acts: list[dict]) -> None:
        w, o = self.r.sample([w for w in self.cast if self.standing(w)], 2)
        dur = round(self.r.uniform(1.0, 3.0), 2) if self.chance(0.5) else None
        est = self.walk_time(self.who[w].x, self.who[o].x) + (dur or 2.0) + 2.5
        self.act(acts, [w, o], est, "hug", who=w, target=o, dur=dur)
        self.who[w].x, self.who[w].exact = self.who[o].x, False

    def a_pick_up(self, acts: list[dict]) -> None:
        w = self.pick([w for w in self.cast if self.standing(w) and len(self.who[w].hands) < 2])  # type: ignore[arg-type]
        h = self.pick([h for h in self.props if h in STORY_HAND and self.placed(h)])
        est = self.walk_time(self.who[w].x, self.x_of(h)) + 6.0
        self.act(acts, [w], est, "pick_up", who=w, what=h)
        self.who[w].x, self.who[w].exact = self.x_of(h), False
        self.put(h, "held", holder=w)

    def _holder(self) -> tuple[str, str]:
        w = self.pick([w for w in self.cast if self.standing(w) and self.who[w].hands])
        return w, self.pick(self.who[w].hands)

    def a_drop(self, acts: list[dict], w: str | None = None) -> None:
        w, h = (w, self.pick(self.who[w].hands)) if w else self._holder()  # type: ignore[arg-type]
        self.act(acts, [w], 2.0, "drop", who=w, what=h)
        self.put(h, "ground", x=self.who[w].x)

    def a_hold(self, acts: list[dict]) -> None:
        w, h = self._holder()
        self.act(acts, [w], 2.0, "hold", who=w, what=h)

    def a_hand_to(self, acts: list[dict]) -> None:
        """No auto-walk: the taker stands on a mark (walking there first if needed), the giver
        on a mark next to it."""
        w, h = self._holder()
        takers = [o for o in self.cast if o != w and self.standing(o) and self.who[o].y == 0.0
                  and len(self.who[o].hands) < 2]  # type: ignore[arg-type]
        if not takers:
            return
        o = self.pick(takers)
        if not self.who[o].exact:
            self.goto(acts, o, self.pick(self.ground))
        self.approach(acts, w, self.who[o].x)  # type: ignore[arg-type]
        self.act(acts, [w, o], 3.5, "hand_to", who=w, what=h, to=o)
        self.put(h, "held", holder=o)

    def a_place_on(self, acts: list[dict]) -> None:
        """No auto-walk and no turn: next to a surface (a standing reach), or next to a mark on
        the floor (a crouch), facing it. (Not on the mark the puppet stands on, nor behind it.)"""
        # Steering: place_on onto one's own mark, onto a mark behind, or with the left hand
        # failed out_of_reach (wave 3's place-on-reach task fixes them, with tests in its own
        # file). Relax this to any ground mark, either facing, once that fix is on main.
        w, h = self._holder()
        surfaces = [f for f in self.props if f in STORY_SURFACES and self.placed(f) and self.thing[f].where == "ground"]
        if surfaces and self.chance(0.6):
            base = self.pick(surfaces)
            sock = self.free_socket(base, [STORY_SURFACES[base]])
            if sock is not None:
                self.approach(acts, w, self.thing[base].x)  # type: ignore[arg-type]
                self.face(acts, w, base)
                self.act(acts, [w], 3.5, "place_on", who=w, what=h, on=base, socket=sock if self.chance(0.5) else None)
                self.put(h, "on", x=self.thing[base].x, on=f"{base}.{sock}")
                return
        m = self.pick(self.ground)
        self.approach(acts, w, self.marks[m][0])
        self.face(acts, w, m)
        self.act(acts, [w], 4.5, "place_on", who=w, what=h, on=m)
        self.put(h, "ground", x=self.marks[m][0])

    def a_put_on(self, acts: list[dict]) -> None:
        w = self.thing["cap"].holder
        if w is None or not self.standing(w):
            return
        heads = [o for o in self.cast if self.who[o].head is None and (o == w or (self.standing(o) and self.who[o].y == 0.0))]
        if not heads:
            return
        o = self.pick(heads)
        if o != w:
            if not self.who[o].exact:
                self.goto(acts, o, self.pick(self.ground))
            self.approach(acts, w, self.who[o].x)  # type: ignore[arg-type]
        self.act(acts, [w] if o == w else [w, o], 4.0, "put_on", who=w, what="cap",
                 target=o if o != w or self.chance(0.3) else None)
        self.put("cap", "worn", holder=o)

    def a_sit_on(self, acts: list[dict]) -> None:
        ws = [w for w in self.cast if self.standing(w)]
        seats = [(f, s) for f in self.props if f in STORY_SEATS and self.placed(f)
                 for s in [self.free_socket(f, STORY_SEATS[f])] if s is not None]
        if not ws or not seats:
            return
        w = self.pick(ws)
        f, sock = self.pick(seats)
        est = self.walk_time(self.who[w].x, self.thing[f].x) + 3.5
        self.act(acts, [w], est, "sit_on", who=w, what=f, socket=sock if self.chance(0.5) or sock != "seat" else None)
        self.who[w].seat, self.who[w].x, self.who[w].exact = (f, sock), self.thing[f].x, False

    def a_open_close(self, acts: list[dict]) -> None:
        """A box or gift on a surface, from right beside it, facing it. (Not one on the floor:
        test_story_scenarios.test_open_a_gift_on_the_floor_standing; and never one in the
        opener's own hand, which is out_of_reach by design: the agent is taught not to.)"""
        # Steering: open/close of a prop on the floor, or behind the opener, failed out_of_reach
        # (the fix is parked on origin/jonah/compiler-open-close-floor-reach). Relax this to
        # floor props, either facing, once that fix is on main; keep held props out.
        props = [h for h in OPENABLE if h in self.thing and self.thing[h].where == "on"]
        ws = [w for w in self.cast if self.standing(w)]
        if not props or not ws:
            return
        h, w = self.pick(props), self.pick(ws)
        th = self.thing[h]
        base = th.on.split(".", 1)[0]  # type: ignore[union-attr]
        self.approach(acts, w, th.x, 0.08, 0.12)  # type: ignore[arg-type]
        self.face(acts, w, base)
        verb = "close" if th.state == "open" else "open" if th.state == "closed" else self.pick(["open", "close"])
        self.act(acts, [w], 3.0, verb, who=w, what=h)
        th.state = "open" if verb == "open" else "closed"

    def a_set_state(self, acts: list[dict]) -> None:
        h = self.pick([h for h in self.props if h in STORY_STATES])
        ws = [w for w in self.cast if self.active(w)]
        who = self.pick(ws) if ws and self.chance(0.5) else None
        state = self.pick(STORY_STATES[h])
        self.act(acts, [who] if who else [], 1.5, "set_state", what=h, state=state, who=who)
        self.thing[h].state = state

    def a_get_in(self, acts: list[dict]) -> None:
        ws = [w for w in self.cast if self.standing(w)]
        sock = self.free_socket("car", list(CAR_SEATS))
        if not ws or sock is None:
            return
        w = self.pick(ws)
        named = sock if self.chance(0.4) else None
        if named is None:  # the compiler's default: driver if free, else the next free seat
            sock = "driver" if "driver" not in {self.who[r].seat[1] for r in self.riders("car")} else "passenger"  # type: ignore[index]
        # Wait for the car to stop (a drive in progress keeps every rider busy).
        busy = [w] + self.riders("car")
        self.act(acts, busy, self.walk_time(self.who[w].x, self.thing["car"].x) + 3.5, "get_in", who=w, what="car",
                 socket=named)
        self.who[w].seat, self.who[w].x, self.who[w].kneeling, self.who[w].exact = ("car", sock), self.thing["car"].x, False, False

    def a_get_out(self, acts: list[dict]) -> None:
        w = self.pick([w for w in self.riders("car") if self.active(w)])
        self.act(acts, [w], 3.5, "get_out", who=w, what="car")
        self.who[w].seat = None

    def a_drive(self, acts: list[dict]) -> None:
        riders = self.riders("car")
        driver = [w for w in riders if self.who[w].seat[1] == "driver"]  # type: ignore[index]
        if not driver or any(w in self.frozen for w in riders):
            return
        m = self.free_mark(self.thing["car"].x)
        if m is None:
            return
        x = self.marks[m][0]
        est = 2.5 + abs(x - self.thing["car"].x) / GEN_DRIVE  # type: ignore[operator]
        passengers = [w for w in riders if w not in driver]
        if passengers and self.chance(0.4):
            self.act(acts, riders, est, "ride_to", who=self.pick(passengers), what="car", to=m)
        else:
            self.act(acts, riders, est, "drive_to", who=driver[0], what="car", to=m)
        self.put("car", "ground", x=x)
        for w in riders:
            self.who[w].x = x

    def a_effect(self, acts: list[dict]) -> None:
        kind = self.pick(["confetti", "steam", "smoke", "rain", "time_of_day"])
        t = round(self.clock + self.r.uniform(0.0, 2.0), 2)
        a: dict[str, Any] = {"t": t, "do": kind}
        if kind == "time_of_day":
            a["value"] = self.pick(TIMES_OF_DAY)
        elif kind != "rain" and self.chance(0.7):
            # Someone standing still on stage by then, a prop on the ground, or a mark.
            people = [w for w in self.cast if self.who[w].x is not None and self.who[w].free <= t and w not in self.frozen]
            things = [h for h in self.props if self.thing[h].where == "ground"]
            a["at"] = self.pick(people + things + self.ground)
        if self.chance(0.5):
            a["dur"] = round(self.r.uniform(0.5, 3.0), 2)
        acts.append(a)

    # ---- one shot, the whole screenplay

    def shot(self, i: int, n_shots: int, prev_set: str | None) -> dict:
        if prev_set is not None and self.chance(0.35):
            set_id = prev_set
        else:
            set_id = self.pick([s for s in STORY_MARKS if s != prev_set])
        sid = f"s{i + 1}"
        shot = self.begin(sid, set_id, set_id == prev_set)
        acts: list[dict] = []
        on_at_start = {w for w in self.cast if self.on_stage(w)}
        for _ in range(self.r.randint(2, 7)):
            weights, fns = zip(*self.options())
            self.r.choices(fns, weights)[0](acts)
        if i + 1 < n_shots and self.chance(0.5):  # drop something before the fold
            holders = [w for w in self.cast if self.on_stage(w) and self.who[w].hands]
            if holders:
                self.a_drop(acts, self.pick(holders))
        shot["actions"] = acts
        if self.extra:
            shot["marks"] = dict(self.extra)
        steady = sorted(on_at_start & {w for w in self.cast if self.on_stage(w)}
                        - {a.get("who") for a in acts if a["do"] in ("exit", "enter")})
        if self.chance(0.6):
            cues = []
            for _ in range(self.r.randint(1, 2)):
                move = self.pick(MOVES)
                cue: dict[str, Any] = {"t": round(self.r.uniform(0.0, max(self.clock, 1.0)), 2), "move": move}
                if VOCABULARY["camera"][move]["required"]:
                    cue["target"] = self.pick(steady + self.ground) if steady else self.pick(self.ground)
                if self.chance(0.5):
                    cue["dur"] = round(self.r.uniform(0.5, 3.0), 2)
                cues.append(cue)
            shot["camera"] = sorted(cues, key=lambda c: c["t"])
        if self.chance(0.4):
            shot["transition"] = self.pick(TRANSITIONS)
        if self.chance(0.4):
            shot["pageText"] = " ".join(self.pick(WORDS) for _ in range(5)).capitalize() + "."
        return shot

    def narrate(self, shot: dict, author: str, cue_p: float) -> list[dict]:
        """One or two short lines, spaced so they never overlap; sometimes cued to an action."""
        out = []
        t = round(self.r.uniform(0.0, 1.5), 2)
        ids = {a["id"]: a["t"] for a in shot["actions"] if "id" in a}
        for _ in range(self.r.randint(1, 2)):
            words = [self.pick(WORDS) for _ in range(self.r.randint(3, 6))]
            line: dict[str, Any] = {"t": t, "text": " ".join(words).capitalize() + ".",
                                    "source": f"telling:{author}:{shot['id']}"}
            later = [i for i, at in ids.items() if at >= t + 1.0]
            if later and self.chance(cue_p):  # cued: the line starts about where its action does
                cue = self.pick(later)
                line["t"] = t = round(ids.pop(cue) - 1.0, 2)
                line["cue"] = cue
                if self.chance(0.5):
                    line["cueWord"] = self.pick(words[:2])
            out.append(line)
            t = round(t + 6.0 + self.r.uniform(0.0, 3.0), 2)
        return out

    def screenplay(self) -> dict:
        n = self.r.randint(2, 4)
        shots: list[dict] = []
        prev = None
        for i in range(n):
            shots.append(self.shot(i, n, prev))
            prev = shots[-1]["set"]
        sp: dict[str, Any] = {"memoryId": "fuzz_story", "cast": list(self.cast), "props": list(self.props), "shots": shots}
        teller = self.pick(self.cast)
        for shot in shots:
            if self.chance(0.6):
                shot["narration"] = self.narrate(shot, teller, 0.15)
        if not any("narration" in s for s in shots):
            teller = self.cast[0]  # who tells the default narration when no line names a teller
        if self.chance(0.3):
            other = self.pick([c for c in self.cast if c != teller])
            sp["narrations"] = {other: {s["id"]: self.narrate(s, other, 0.0) for s in shots if self.chance(0.7)}}
        if self.chance(0.15):
            shot = self.pick(shots)
            frm = round(self.r.uniform(0.0, 3.0), 2)
            sp["interactions"] = [{
                "id": "tab", "kind": self.pick(VOCABULARY["interactionKinds"]), "label": "Pull it", "shot": shot["id"],
                "at": [0.8, 0.8], "page": "stage",
                "drives": {"type": "scrub", "from": frm, "to": round(frm + 2.0, 2), "targets": [f"puppet:{self.pick(self.cast)}"]},
            }]
        return sp


@dataclass(frozen=True)
class Sample:
    kind: str  # "random" | "perturbed" | "story" | "edge"
    index: int
    raw: dict
    screenplay: Screenplay | None  # None: the pydantic schema rejected it


KIND_OFFSET = {"random": 0, "perturbed": 50_000, "story": 1_000_000}


def _seed(kind: str, i: int) -> int:
    """Distinct per (kind, i) for random i < 50,000, perturbed i < 950,000 and any story i."""
    return SEED * 100_003 + i + KIND_OFFSET[kind]


def scene_for(kind: str) -> Scene:
    """A fresh copy of the scene a kind of sample compiles against."""
    if kind == "story":
        sets, props, people = story_scene()
        return Scene(sets=sets, props=props, people=people)
    return base_scene()


def generate(kind: str, i: int) -> Sample:
    if kind == "story":
        raw = StoryGen(_seed(kind, i)).screenplay()
    else:
        g = Gen(_seed(kind, i))
        raw = g.screenplay() if kind == "random" else g.perturbed(i)
    try:
        sp = Screenplay.model_validate(copy.deepcopy(raw))
    except ValidationError:
        sp = None
    return Sample(kind, i, raw, sp)


def corpus() -> list[Sample]:
    return [generate(kind, i) for kind, n in SIZES.items() for i in range(OFFSET, OFFSET + n)]


# ------------------------------------------------------------------------- invariants

def _compile(sp: Screenplay, scene: Scene, **kw: Any) -> CompileResult:
    return compile_screenplay(sp, sets=scene.sets, props=scene.props, people=scene.people, **kw)


def _planting_problems(sample: Sample, result: CompileResult) -> list[str]:
    """Invariant 5: the planted compile (``result``) and ``plant_feet=False`` agree on ok-ness
    and errors; on ok, planting_problems is empty and feetPlanted is True on the planted
    timeline and unset on the unplanted one."""
    scene = scene_for(sample.kind)
    unplanted = _compile(Screenplay.model_validate(copy.deepcopy(sample.raw)), scene, plant_feet=False)
    if unplanted.ok != result.ok:
        return [(f"planting: plant_feet=False gives ok={unplanted.ok}, plant_feet=True ok={result.ok}: "
                 f"{[e.message for e in unplanted.errors + result.errors]}")]
    if unplanted.errors != result.errors:
        return [(f"planting: plant_feet=False errors {[e.message for e in unplanted.errors]} differ from "
                 f"plant_feet=True {[e.message for e in result.errors]}")]
    if result.timeline is None or unplanted.timeline is None:
        return []
    out: list[str] = []
    if result.timeline.feet_planted is not True:
        out.append(f"planting: feet_planted is {result.timeline.feet_planted!r} on the planted timeline")
    if "feet_planted" in unplanted.timeline.model_fields_set or unplanted.timeline.feet_planted is not None:
        out.append(f"planting: feet_planted is set ({unplanted.timeline.feet_planted!r}) on the unplanted timeline")
    builds = {pid: p.character.build for pid, p in scene.people.items()}
    return out + [f"planting: {m}" for m in planting_problems(result.timeline, unplanted.timeline, builds)]


def error_site_problems(sp: Screenplay, result: CompileResult) -> list[str]:
    """Invariant 6: every error's ``shot`` is one of the screenplay's shot ids or None; a ``t``
    comes with a ``shot``, is finite and lies within that shot's action span (from its first
    to its last action or camera cue time as written: the compiler reports the written ``t``);
    and the message head names the same shot (``shot <id>``, ``shot <id>, t=`` with a ``t``).
    A head ``t=`` with no ``error.t`` must be a time that is not finite (``t=inf``, ``t=nan``:
    errors.error_t leaves those out so the result is JSON-safe)."""
    spans: dict[str, list[float]] = {}
    for shot in sp.shots:
        spans.setdefault(shot.id, []).extend(x.t for x in [*shot.actions, *shot.camera] if math.isfinite(x.t))
    out: list[str] = []
    for e in result.errors:
        if e.shot is not None and e.shot not in spans:
            out.append(f"error.shot {e.shot!r} is not a shot id: {e.message!r}")
            continue
        if e.shot is not None and not (e.message.startswith(f"shot {e.shot}:") or e.message.startswith(f"shot {e.shot},")):
            out.append(f"error.shot is {e.shot!r} but the message head names another: {e.message!r}")
        if e.t is None:
            head_t = f"shot {e.shot}, t="
            if e.shot is not None and e.message.startswith(head_t):
                written = e.message[len(head_t):].split(":", 1)[0]
                if written not in ("inf", "-inf", "nan"):
                    out.append(f"the message head has t={written} but error.t is unset: {e.message!r}")
            continue
        if e.shot is None:
            out.append(f"error.t is {e.t} without a shot: {e.message!r}")
        elif not math.isfinite(e.t):
            out.append(f"error.t is {e.t}, not finite (JSON writes null): {e.message!r}")
        elif not spans[e.shot] or not min(spans[e.shot]) - 1e-6 <= e.t <= max(spans[e.shot]) + 1e-6:
            span = f"[{min(spans[e.shot]):g}, {max(spans[e.shot]):g}]" if spans[e.shot] else "empty"
            out.append(f"error.t {e.t:g} is outside shot {e.shot}'s action span {span}: {e.message!r}")
        elif not e.message.startswith(f"shot {e.shot}, t="):
            out.append(f"error.t is {e.t:g} but the message head has no t: {e.message!r}")
    return out


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
    """Run every invariant on one sample. Returns the (planted) result and every problem found."""
    assert sample.screenplay is not None
    sp, scene = sample.screenplay, scene_for(sample.kind)
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
            problems += [f"sanity: {m}" for m in sanity_problems(result.timeline, props=scene.props)]
        if result.errors:
            problems.append(f"ok with errors: {[e.message for e in result.errors]}")
    else:
        problems += error_problems(result)
        problems += error_site_problems(sp, result)
    if sp.model_dump_json() != before:
        problems.append("compile mutated its screenplay")
    try:
        problems += _planting_problems(sample, result)
    except Exception as e:  # invariant 1, for plant_feet=False
        problems.append(f"compile(plant_feet=False) raised {type(e).__name__}: {e}")
    again = _compile(Screenplay.model_validate(copy.deepcopy(sample.raw)), scene_for(sample.kind))  # invariant 4
    if again.model_dump_json() != result.model_dump_json():
        problems.append("compiling the same input twice gave different JSON")
    return result, problems


# -------------------------------------------------------------------------------- tests

@pytest.fixture(scope="module")
def samples() -> list[Sample]:
    return corpus()


def test_the_generator_is_deterministic():
    for kind, i in [("random", 0), ("random", 17), ("perturbed", 3), ("story", 0), ("story", 11)]:
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
    f = N_RANDOM / DEFAULT_SIZES["random"]
    assert {len(r["shots"]) for r in raws} == {1, 2, 3}
    pairs = [(a["set"], b["set"]) for r in raws for a, b in zip(r["shots"], r["shots"][1:])]
    assert any(a == b for a, b in pairs) and any(a != b for a, b in pairs)
    assert all(1 <= len(shot["actions"]) <= 8 for r in raws for shot in r["shots"])
    assert sum("narrations" in r for r in raws) > 50 * f
    assert sum(any("cue" in ln for shot in r["shots"] for ln in shot.get("narration", [])) for r in raws) > 50 * f
    assert sum(any(shot.get("camera") for shot in r["shots"]) for r in raws) > 200 * f


def _story_features(raw: dict) -> set[str]:
    """What a story screenplay does across its shots (test_the_story_corpus_...)."""
    out: set[str] = set()
    shots = raw["shots"]
    for a, b in itertools.pairwise(shots):
        out.add("set_change" if a["set"] != b["set"] else "same_set")
        acts_a = a["actions"]
        held_in = {s["holding"] for s in b["start"].values() if "holding" in s}
        picked = {x["what"] for x in acts_a if x["do"] in ("pick_up", "hand_to")}
        if a["set"] != b["set"] and any(w in b["start"] for w in {x["who"] for x in acts_a if x["do"] == "pick_up"}):
            out.add("carry_across_sets")  # someone who picked something up is brought into the next set
        if acts_a and acts_a[-1]["do"] in ("drop", "place_on"):
            out.add("drop_before_fold")
        if held_in - picked:
            out.add("start_holding")
        exits = {x["who"] for x in acts_a if x["do"] == "exit"}
        if exits & {x["who"] for x in b["actions"] if x["do"] == "enter"}:
            out.add("exit_then_enter_next_shot")
        sat = {x["who"] for x in acts_a if x["do"] == "sit_on"}
        if a["set"] == b["set"] and sat & {x["who"] for x in b["actions"] if x["do"] == "stand_up"}:
            out.add("sit_then_stand_next_shot")
        if any(st.get("in", "").startswith("car.") for st in b["start"].values()):
            out.add("ride_across_sets")
        if any(x["do"] in ("drive_to", "ride_to") for x in acts_a):
            out.add("drive")
    return out


def test_the_story_corpus_switches_sets_carries_drops_enters_and_sits(samples):
    stories = [s for s in samples if s.kind == "story"]
    assert {len(s.raw["shots"]) for s in stories} == {2, 3, 4}
    features = Counter(f for s in stories for f in _story_features(s.raw))
    print(f"\nstory features: {dict(sorted(features.items()))}")
    for name in ("set_change", "same_set", "carry_across_sets", "drop_before_fold", "start_holding",
                 "exit_then_enter_next_shot", "sit_then_stand_next_shot", "ride_across_sets", "drive"):
        assert features[name] >= max(1, N_STORY // 100), name
    assert all(s.screenplay is not None for s in stories)  # the schema accepts every story


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
            stats[f"excluded_{s.kind}"] += 1
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
    assert stats["ok_multishot"] >= 100 * N_RANDOM / DEFAULT_SIZES["random"]  # shot contiguity on real multi-shot timelines
    assert stats["schema_rejected"] < (N_RANDOM + N_PERTURBED) // 10
    # The stories are written to compile: most of them reach a timeline (and every invariant).
    assert stats["story_ok"] >= 0.8 * (N_STORY - stats["excluded_story"])
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


# ------------------------------------------------------------ bugs the fuzz found
#
# A bug the fuzz finds whose cause is outside this file gets a minimal hand-written reproduction
# here, xfail(strict=True) with the exact error or broken invariant in its reason, and its random
# seeds go in EXCLUDED. None is open now. The wave-2 reproductions moved out when wave 3 fixed
# them: each fix carries its own passing tests (place-on-reach, state-propstart-and-object-
# wording, stage-edge-dismount, raised-mark-reach, props-across-sets). The controls and the
# JSON-safe error.t test below stay as plain regressions.


def _repro(*shots: dict, scene: Scene | None = None, plant_feet: bool = True) -> CompileResult:
    scene = scene or base_scene()
    sp = Screenplay.model_validate({"memoryId": "repro", "cast": list(CAST), "props": list(PROPS), "shots": list(shots)})
    return _compile(sp, scene, plant_feet=plant_feet)


def _shot(sid: str, set_id: str, start: dict, actions: list[dict], prop_start: dict | None = None,
          marks: dict | None = None) -> dict:
    shot = {"id": sid, "set": set_id, "start": start, "propStart": prop_start or {}, "actions": actions}
    if marks:
        shot["marks"] = marks
    return shot


def _errors(result: CompileResult) -> list[str]:
    return [f"{e.code}: {e.message}" for e in result.errors]


def test_place_on_a_mark_just_in_front_compiles():
    """place_on to a floor mark 0.12 in front, facing it (StoryGen.a_place_on's shape)."""
    result = _repro(_shot("s1", "yard", {"dad": {"at": "door", "holding": "cap", "facing": "right"}},
                          [{"t": 1, "do": "place_on", "who": "dad", "what": "cap", "on": "spot"}],
                          marks={"spot": [0.16, 0.0]}))
    assert result.ok, _errors(result)


def test_an_error_on_a_non_finite_t_has_a_json_safe_t():
    """An action written at t = inf or nan: CompileError.t is None (JSON has no inf), the
    message still says t=inf, and the result round-trips through JSON unchanged."""
    for t, shown in ((float("inf"), "inf"), (float("nan"), "nan")):
        raw = _shot("s1", "yard", {"dad": {"at": "door"}}, [{"t": t, "do": "wave", "who": "dad"}])
        sp = Screenplay.model_validate({"memoryId": "repro", "cast": list(CAST), "props": list(PROPS), "shots": [raw]})
        result = _compile(sp, base_scene())
        assert [(e.code, e.message, e.t) for e in result.errors] == [
            ("bad_time", f"shot s1, t={shown}: dad cannot wave; t must be a finite number >= 0, not {shown}", None)
        ]
        assert error_site_problems(sp, result) == []
        dumped = result.model_dump_json()
        assert CompileResult.model_validate_json(dumped) == result
        assert CompileResult.model_validate_json(dumped).model_dump_json() == dumped


def test_put_a_prop_back_down_with_the_right_hand_compiles():
    """Pick a prop up off a floor mark and put it back down (right hand free)."""
    result = _repro(_shot("s1", "yard", {"dad": {"at": "door", "facing": "right"}},
                          [{"t": 1, "do": "pick_up", "who": "dad", "what": "cap"},
                           {"t": 7, "do": "place_on", "who": "dad", "what": "cap", "on": "spot"}],
                          prop_start={"cap": {"at": "spot"}}, marks={"spot": [0.16, 0.0]}))
    assert result.ok, _errors(result)
