"""Verb case catalogue: a success and a precise-error screenplay for every vocabulary verb and
camera move, on one shared stage.

Test data only (not collected by pytest: the file name does not start with ``test_``). Import it
from a test module as ``from verb_cases import ...``; pytest's default ``prepend`` import mode
puts this directory on ``sys.path`` (tests/ has no ``__init__.py`` files).

Each ``VerbCase`` is a minimal screenplay (one shot unless a second is needed) plus the scene it
compiles against. ``ok`` cases must compile. Error cases say where they must fail:

- ``stage="static"``: ``validate.validate`` reports it; ``code`` and ``message`` are its first
  error.
- ``stage="dynamic"``: it passes ``validate.validate`` and must fail inside ``compile()`` (world
  state, IK, narration or a verb handler). ``code`` and ``message`` are the exact CompileError
  wave 3 asserts. Each message carries a comment naming the file and function its reason is
  copied from; the whole message follows ``errors.error_message``::

      shot <id>, t=<t>: <who> cannot <do> "<what>"; <reason>     (who / "<what>" omitted when absent)

Sources of the dynamic reasons (read with ``git show``; only state.py, verbs_objects.py and
verbs_vehicles.py are on main as of this catalogue):

- api/screenplay/state.py, verbs_objects.py, verbs_vehicles.py (main)
- api/screenplay/verbs_body.py (origin/jonah/compiler-verbs-body-land, which builds on main's
  state.py)
- api/screenplay/verbs_motion.py, effects.py (origin/jonah/compiler-verbs-motion-effects-narration)
- api/screenplay/verbs_core.py, locate.py, errors.py, compiler.py (origin/jonah/compiler-compiler-core)

Stage geometry (stage units; x = 2u - 1). Puppets are 0.5 x build tall; a standing average
shoulder is at y = 0.38 and its arm reaches 0.17, so hand-height props sit on the table top
(y = 0.3), as the hymn sample's books sit on its step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import schemas
from screenplay.schema import VOCABULARY, Screenplay

__all__ = ["CASES", "Scene", "VerbCase", "base_scene", "cases_for"]

Stage = Literal["static", "dynamic"]


@dataclass(frozen=True)
class Scene:
    sets: dict[str, schemas.SetDef]
    props: dict[str, schemas.Prop]
    people: dict[str, schemas.Person]


@dataclass(frozen=True)
class VerbCase:
    verb: str
    name: str
    screenplay: Screenplay
    scene: Scene
    ok: bool
    stage: Stage | None
    code: str | None
    message: str | None


# --------------------------------------------------------------------------- the stage

CAST = ["dad", "mom", "kid"]
PROPS = ["hymn_books", "cap", "chair", "table", "suitcase", "lamp", "sedan"]

YARD_MARKS: dict[str, list[float]] = {
    "door": [0.1, 0.0],          # x = -0.8
    "bench_spot": [0.3, 0.0],    # x = -0.4: where the chair stands
    "beside_bench": [0.4, 0.0],  # x = -0.2: close enough to bench_spot to hand things over
    "by_table": [0.55, 0.0],     # x =  0.1: within arm's reach of the table top
    "table_spot": [0.6, 0.0],    # x =  0.2: where the table stands
    "curb": [0.9, 0.0],          # x =  0.8
}
STREET_MARKS: dict[str, list[float]] = {
    "garage": [0.3, 0.0],        # x = -0.4
    "corner": [0.8, 0.0],        # x =  0.6
}


def _layers(heights: list[float]) -> list[schemas.SetLayer]:
    return [schemas.SetLayer(art=None, depth=i, height=h, color="#c8b89a") for i, h in enumerate(heights)]


def _person(pid: str, name: str, build: str, color: str) -> schemas.Person:
    return schemas.Person(
        id=pid, family_id="verb_cases", name=name, character=schemas.CharacterSheet(color=color, build=build)
    )


def _prop(pid: str, size: str, **kw: Any) -> schemas.Prop:
    return schemas.Prop(id=pid, family_id="verb_cases", name=pid.replace("_", " "), size=size, **kw)


def base_scene() -> Scene:
    """The shared stage, built fresh on every call (callers may mutate their copy)."""
    sets = [
        schemas.SetDef(id="yard", name="back yard", layers=_layers([1.0, 0.5, 0.2]), marks=YARD_MARKS),
        schemas.SetDef(id="street", name="street", layers=_layers([1.0, 0.3]), marks=STREET_MARKS),
    ]
    people = [
        _person("dad", "Dad", "tall", "#3f6fb5"),
        _person("mom", "Mom", "average", "#c0504d"),
        _person("kid", "Kid", "small", "#6a9a3b"),
    ]
    props = [
        # A stack of books: anchor at the bottom middle, gripped at the top.
        _prop("hymn_books", "hand", art={"body": None}, color="#2f4a7a",
              anchor=[0.5, 1.0], grip=[0.5, 0.0]),
        # Worn in the head socket (put_on); held by its brim.
        _prop("cap", "hand", description="a wearable blue flat cap", art={"body": None}, color="#3f6fb5",
              aspect=1.4, anchor=[0.5, 1.0], grip=[0.5, 0.6]),
        # A seat 0.2 above the ground.
        _prop("chair", "furniture", art={"body": None}, color="#8a5a3c", aspect=0.8,
              anchor=[0.5, 1.0], sockets={"seat": [0.5, 0.5]}),
        # Its top surface is 0.3 above the ground, in reach of a standing puppet.
        _prop("table", "furniture", art={"body": None}, color="#a0522d", aspect=1.5,
              anchor=[0.5, 1.0], sockets={"top": [0.5, 0.25]}),
        _prop("suitcase", "carried", art={"body": None, "open": None}, color="#6b4423", aspect=1.3,
              anchor=[0.5, 1.0], grip=[0.5, 0.0], states={"open": {"body": "open"}, "closed": {}}),
        _prop("lamp", "hand", art={"body": None, "lit": None}, color="#e8c547", aspect=0.6,
              anchor=[0.5, 1.0], grip=[0.5, 0.3], states={"lit": {"body": "lit"}, "out": {}}),
        _prop("sedan", "vehicle", art={"body": None, "wheel": None}, color="#2e5e4e", aspect=2.0,
              anchor=[0.5, 1.0],
              sockets={"driver": [0.42, 0.55], "passenger": [0.6, 0.55]},
              parts={
                  "wheel_back": schemas.PropPart(art="wheel", at=[0.22, 0.88], spin=True),
                  "wheel_front": schemas.PropPart(art="wheel", at=[0.78, 0.88], spin=True),
              },
              states={"parked": {}, "driving": {}}),
    ]
    return Scene(
        sets={s.id: s for s in sets},
        props={p.id: p for p in props},
        people={p.id: p for p in people},
    )


# ---------------------------------------------------------------------- screenplays

def _shot(
    *actions: dict,
    id: str = "s1",
    set: str = "yard",
    start: dict | None = None,
    prop_start: dict | None = None,
    camera: list[dict] | None = None,
) -> dict:
    return {
        "id": id,
        "set": set,
        "start": start or {},
        "propStart": prop_start or {},
        "camera": camera or [],
        "actions": list(actions),
    }


def _sp(*shots: dict) -> Screenplay:
    return Screenplay.model_validate(
        {"memoryId": "verb_cases", "cast": list(CAST), "props": list(PROPS), "shots": list(shots)}
    )


def _one(*actions: dict, **shot_kw: Any) -> Screenplay:
    return _sp(_shot(*actions, **shot_kw))


def _a(t: float, do: str, **params: Any) -> dict:
    return {"t": t, "do": do, **params}


def _cue(t: float, move: str, **params: Any) -> dict:
    return {"t": t, "move": move, **params}


def _at(mark: str, **kw: Any) -> dict:
    return {"at": mark, **kw}


# Recurring starts.
TABLE = {"table": {"at": "table_spot"}}
CHAIR = {"chair": {"at": "bench_spot"}}
SEDAN_CURB = {"sedan": {"at": "curb"}}
SEDAN_GARAGE = {"sedan": {"at": "garage"}}

# Lists that static messages spell out.
CAST_LIST = "the cast is dad, mom, kid"
PROPS_LIST = "the props are hymn_books, cap, chair, table, suitcase, lamp, sedan"
YARD_MARKS_LIST = "the marks are offstage_left, offstage_right, door, bench_spot, beside_bench, by_table, table_spot, curb"
POSES_LIST = "poses are stand, hold, sit, kneel, wave, point, reach, hug, slumped, arms_raised, bow_head"


def _ok(verb: str, name: str, sp: Screenplay) -> VerbCase:
    return VerbCase(verb, f"{verb}:{name}", sp, base_scene(), True, None, None, None)


def _static(verb: str, name: str, sp: Screenplay, code: str, message: str) -> VerbCase:
    return VerbCase(verb, f"{verb}:{name}", sp, base_scene(), False, "static", code, message)


def _dynamic(verb: str, name: str, sp: Screenplay, code: str, message: str) -> VerbCase:
    return VerbCase(verb, f"{verb}:{name}", sp, base_scene(), False, "dynamic", code, message)


# ------------------------------------------------------------------------------ cases

def _movement() -> list[VerbCase]:
    return [
        # ------------------------------------------------------------------ walk_to
        _ok("walk_to", "ok", _one(_a(1, "walk_to", who="dad", to="curb"), start={"dad": _at("door")})),
        _static("walk_to", "unknown_mark",
                _one(_a(1, "walk_to", who="dad", to="nowhere"), start={"dad": _at("door")}),
                "unknown_mark",
                f'shot s1, t=1: dad cannot walk_to; "nowhere" is not a mark in this shot; {YARD_MARKS_LIST}'),
        _dynamic("walk_to", "offstage",
                 _one(_a(1, "walk_to", who="dad", to="curb"), start={"dad": _at("offstage_left")}),
                 "offstage",
                 # verbs_core.py _on_stage (via apply_walk_to / _locomote)
                 "shot s1, t=1: dad cannot walk_to; dad is offstage; use enter first"),
        _dynamic("walk_to", "busy",
                 _one(_a(0, "walk_to", who="dad", to="curb"), _a(1, "walk_to", who="dad", to="table_spot"),
                      start={"dad": _at("door")}),
                 "busy",
                 # state.py World.check_free, activity from compiler.py ACTIVITY["walk_to"];
                 # the first walk is 1.6 units at the walk speed 0.35: 1.6 / 0.35 = 4.57 s
                 "shot s1, t=1: dad cannot walk_to; dad is still walking until t=4.57"),

        # ------------------------------------------------------------------- run_to
        _ok("run_to", "ok", _one(_a(1, "run_to", who="kid", to="curb"), start={"kid": _at("door")})),
        _static("run_to", "missing_to",
                _one(_a(1, "run_to", who="kid"), start={"kid": _at("door")}),
                "missing_param",
                'shot s1, t=1: kid cannot run_to; run_to requires who, to; "to" is missing'),
        _dynamic("run_to", "seated",
                 _one(_a(1, "run_to", who="dad", to="curb"),
                      start={"dad": _at("bench_spot", **{"in": "chair.seat"})}, prop_start=CHAIR),
                 "seated",
                 # verbs_core.py _not_seated (via _locomote)
                 "shot s1, t=1: dad cannot run_to; dad is seated in chair.seat"),

        # -------------------------------------------------------------------- enter
        _ok("enter", "ok", _one(_a(1, "enter", who="dad", to="door", **{"from": "offstage_left"}),
                                start={"dad": _at("offstage_left")})),
        _static("enter", "bad_from",
                _one(_a(1, "enter", who="dad", to="door", **{"from": "curb"}), start={"dad": _at("offstage_left")}),
                "unknown_mark",
                'shot s1, t=1: dad cannot enter; "from" must be offstage_left or offstage_right, not "curb"'),
        _dynamic("enter", "already_on_stage",
                 _one(_a(1, "enter", who="dad", to="curb"), start={"dad": _at("door")}),
                 "already_on_stage",
                 # verbs_core.py apply_enter
                 "shot s1, t=1: dad cannot enter; dad is already on stage; use walk_to"),

        # --------------------------------------------------------------------- exit
        _ok("exit", "ok", _one(_a(1, "exit", who="mom", to="offstage_right"), start={"mom": _at("curb")})),
        _static("exit", "bad_to",
                _one(_a(1, "exit", who="mom", to="door"), start={"mom": _at("curb")}),
                "unknown_mark",
                'shot s1, t=1: mom cannot exit; "to" must be offstage_left or offstage_right, not "door"'),
        _dynamic("exit", "seated",
                 _one(_a(1, "exit", who="dad"),
                      start={"dad": _at("bench_spot", **{"in": "chair.seat"})}, prop_start=CHAIR),
                 "seated",
                 # state.py World.exit (_seat_reason), run on a copy by verbs_motion.py apply_exit
                 "shot s1, t=1: dad cannot exit; dad is seated in chair.seat"),
        _dynamic("exit", "offstage",
                 _one(_a(1, "exit", who="kid"), start={"kid": _at("offstage_left")}),
                 "offstage",
                 # verbs_motion.py apply_exit
                 "shot s1, t=1: kid cannot exit; kid is already offstage"),

        # ------------------------------------------------------------------ turn_to
        _ok("turn_to", "ok", _one(_a(1, "turn_to", who="dad", target="mom"),
                                  start={"dad": _at("door", facing="left"), "mom": _at("curb")})),
        _static("turn_to", "unknown_target",
                _one(_a(1, "turn_to", who="dad", target="ghost"), start={"dad": _at("door")}),
                "unknown_target",
                f'shot s1, t=1: dad cannot turn_to; "ghost" is not a person, prop or mark in this shot; '
                f"{CAST_LIST}; {PROPS_LIST}; {YARD_MARKS_LIST}"),
        _dynamic("turn_to", "self",
                 _one(_a(1, "turn_to", who="dad", target="dad"), start={"dad": _at("door")}),
                 "self_target",
                 # verbs_motion.py apply_turn_to
                 "shot s1, t=1: dad cannot turn_to; dad cannot turn to face themselves"),
        _dynamic("turn_to", "prop_not_on_stage",
                 _one(_a(1, "turn_to", who="dad", target="cap"), start={"dad": _at("door")}),
                 "not_on_stage",
                 # locate.py Locator._prop_from_world (the locate call in verbs_motion.py apply_turn_to)
                 "shot s1, t=1: dad cannot turn_to; cap is not on stage"),

        # ------------------------------------------------------------------- follow
        _ok("follow", "ok", _one(_a(0, "walk_to", who="mom", to="curb"), _a(0.5, "follow", who="dad", target="mom"),
                                 start={"dad": _at("door"), "mom": _at("bench_spot")})),
        # Following a prop is caught statically: validate's person check runs before the
        # handler's bad_target (verbs_motion.py apply_follow) could.
        _static("follow", "prop",
                _one(_a(1, "follow", who="dad", target="hymn_books"), start={"dad": _at("door")}),
                "unknown_target",
                'shot s1, t=1: dad cannot follow; "target" must be a person in the cast, and "hymn_books" is a prop'),
        _static("follow", "self",
                _one(_a(1, "follow", who="dad", target="dad"), start={"dad": _at("door")}),
                "self_target",
                'shot s1, t=1: dad cannot follow; "target" is dad themselves'),
        _dynamic("follow", "nothing_to_follow",
                 _one(_a(1, "follow", who="dad", target="mom"), start={"dad": _at("door"), "mom": _at("curb")}),
                 "nothing_to_follow",
                 # verbs_motion.py apply_follow
                 "shot s1, t=1: dad cannot follow; mom does not move after t=1"),
    ]


def _body() -> list[VerbCase]:
    return [
        # --------------------------------------------------------------------- pose
        _ok("pose", "ok", _one(_a(1, "pose", who="mom", pose="bow_head"), start={"mom": _at("bench_spot")})),
        _static("pose", "unknown_pose",
                _one(_a(1, "pose", who="mom", pose="dance"), start={"mom": _at("bench_spot")}),
                "unknown_pose",
                f'shot s1, t=1: mom cannot pose; "dance" is not a pose; {POSES_LIST}'),
        _dynamic("pose", "offstage",
                 _one(_a(1, "pose", who="mom", pose="wave"), start={"mom": _at("offstage_right")}),
                 "offstage",
                 # verbs_core.py _on_stage (via apply_pose)
                 "shot s1, t=1: mom cannot pose; mom is offstage; use enter first"),
        _dynamic("pose", "kneel_seated",
                 _one(_a(1, "pose", who="dad", pose="kneel"),
                      start={"dad": _at("bench_spot", **{"in": "chair.seat"})}, prop_start=CHAIR),
                 "seated",
                 # verbs_core.py _not_seated (via apply_pose)
                 "shot s1, t=1: dad cannot pose; dad is seated in chair.seat"),

        # --------------------------------------------------------------------- wave
        _ok("wave", "ok", _one(_a(1, "wave", who="kid", target="mom"),
                               start={"kid": _at("door"), "mom": _at("curb")})),
        _static("wave", "bad_dur",
                _one(_a(1, "wave", who="kid", dur=0), start={"kid": _at("door")}),
                "bad_time",
                "shot s1, t=1: kid cannot wave; dur must be a finite number > 0, not 0"),
        _dynamic("wave", "prop_not_on_stage",
                 _one(_a(1, "wave", who="kid", target="cap"), start={"kid": _at("door")}),
                 "not_on_stage",
                 # locate.py Locator._prop_from_world (the locate call in verbs_body.py apply_wave)
                 "shot s1, t=1: kid cannot wave; cap is not on stage"),

        # ----------------------------------------------------------------- point_at
        _ok("point_at", "ok", _one(_a(1, "point_at", who="kid", target="table"),
                                   start={"kid": _at("door")}, prop_start=TABLE)),
        _static("point_at", "missing_target",
                _one(_a(1, "point_at", who="kid"), start={"kid": _at("door")}),
                "missing_param",
                'shot s1, t=1: kid cannot point_at; point_at requires who, target; "target" is missing'),
        _dynamic("point_at", "prop_not_on_stage",
                 _one(_a(1, "point_at", who="kid", target="lamp"), start={"kid": _at("door")}),
                 "not_on_stage",
                 # locate.py Locator._prop_from_world (the locate call in verbs_body.py apply_point_at)
                 "shot s1, t=1: kid cannot point_at; lamp is not on stage"),

        # ------------------------------------------------------------------- sit_on
        _ok("sit_on", "ok", _one(_a(1, "sit_on", who="dad", what="chair"),
                                 start={"dad": _at("door")}, prop_start=CHAIR)),
        _static("sit_on", "no_seat",
                _one(_a(1, "sit_on", who="dad", what="hymn_books"), start={"dad": _at("door")},
                     prop_start={"hymn_books": {"at": "bench_spot"}}),
                "no_seat",
                'shot s1, t=1: dad cannot sit_on "hymn_books"; "hymn_books" has no sockets to sit on'),
        _dynamic("sit_on", "vehicle",
                 _one(_a(1, "sit_on", who="dad", what="sedan"), start={"dad": _at("table_spot")},
                      prop_start=SEDAN_CURB),
                 "vehicle_seat",
                 # state.py World.sit_on (run on a copy by verbs_body.py apply_sit_on; the landing
                 # verbs_body.py has no "vehicle" pre-check of its own)
                 'shot s1, t=1: dad cannot sit_on "sedan"; sedan is a vehicle; use get_in'),
        _dynamic("sit_on", "socket_taken",
                 _one(_a(1, "sit_on", who="dad", what="chair"),
                      start={"dad": _at("door"), "mom": _at("bench_spot", **{"in": "chair.seat"})},
                      prop_start=CHAIR),
                 "socket_taken",
                 # state.py World._check_socket (via World.sit_on)
                 'shot s1, t=1: dad cannot sit_on "chair"; chair.seat is taken by mom'),

        # ----------------------------------------------------------------- stand_up
        _ok("stand_up", "ok", _one(_a(1, "stand_up", who="dad"),
                                   start={"dad": _at("bench_spot", **{"in": "chair.seat"})}, prop_start=CHAIR)),
        _static("stand_up", "unexpected_what",
                _one(_a(1, "stand_up", who="dad", what="chair"),
                     start={"dad": _at("bench_spot", **{"in": "chair.seat"})}, prop_start=CHAIR),
                "unexpected_param",
                'shot s1, t=1: dad cannot stand_up "chair"; stand_up takes who; "what" is not one of them'),
        _dynamic("stand_up", "not_sitting",
                 _one(_a(1, "stand_up", who="dad"), start={"dad": _at("door")}),
                 "not_sitting",
                 # state.py World.stand_up
                 "shot s1, t=1: dad cannot stand_up; dad is not sitting or kneeling"),
        _dynamic("stand_up", "in_vehicle",
                 _one(_a(1, "stand_up", who="dad"),
                      start={"dad": _at("curb", **{"in": "sedan.driver"})}, prop_start=SEDAN_CURB),
                 "seated_vehicle",
                 # state.py World.stand_up
                 "shot s1, t=1: dad cannot stand_up; dad is in sedan; use get_out"),

        # -------------------------------------------------------------------- kneel
        _ok("kneel", "ok", _one(_a(1, "kneel", who="mom", dur=1.5), start={"mom": _at("bench_spot")})),
        _static("kneel", "bad_dur",
                _one(_a(1, "kneel", who="mom", dur=-1), start={"mom": _at("bench_spot")}),
                "bad_time",
                "shot s1, t=1: mom cannot kneel; dur must be a finite number > 0, not -1"),
        _dynamic("kneel", "seated",
                 _one(_a(1, "kneel", who="mom"),
                      start={"mom": _at("bench_spot", **{"in": "chair.seat"})}, prop_start=CHAIR),
                 "seated",
                 # state.py World.kneel (_seat_reason), run on a copy by verbs_body.py apply_kneel
                 "shot s1, t=1: mom cannot kneel; mom is seated in chair.seat"),
        _dynamic("kneel", "already_kneeling",
                 _one(_a(1, "kneel", who="mom"), start={"mom": _at("bench_spot", pose="kneel")}),
                 "already_kneeling",
                 # verbs_body.py apply_kneel
                 "shot s1, t=1: mom cannot kneel; mom is already kneeling"),

        # ---------------------------------------------------------------------- hug
        _ok("hug", "ok", _one(_a(1, "hug", who="dad", target="mom"),
                              start={"dad": _at("door"), "mom": _at("bench_spot", facing="left")})),
        _static("hug", "self",
                _one(_a(1, "hug", who="dad", target="dad"), start={"dad": _at("door")}),
                "self_target",
                'shot s1, t=1: dad cannot hug; "target" is dad themselves'),
        _static("hug", "prop",
                _one(_a(1, "hug", who="dad", target="lamp"), start={"dad": _at("door")}),
                "unknown_target",
                'shot s1, t=1: dad cannot hug; "target" must be a person in the cast, and "lamp" is a prop'),
        _dynamic("hug", "target_seated",
                 _one(_a(1, "hug", who="dad", target="mom"),
                      start={"dad": _at("door"), "mom": _at("bench_spot", **{"in": "chair.seat"})},
                      prop_start=CHAIR),
                 "seated",
                 # state.py World.hug_check (_seat_reason), called by verbs_body.py apply_hug
                 "shot s1, t=1: dad cannot hug; mom is seated in chair.seat"),
        _dynamic("hug", "target_offstage",
                 _one(_a(1, "hug", who="dad", target="kid"),
                      start={"dad": _at("door"), "kid": _at("offstage_right")}),
                 "offstage",
                 # state.py World._require_on_stage (via World.hug_check)
                 "shot s1, t=1: dad cannot hug; kid is offstage"),

        # -------------------------------------------------------------- lean_toward
        _ok("lean_toward", "ok", _one(_a(1, "lean_toward", who="mom", target="dad"),
                                      start={"mom": _at("bench_spot"), "dad": _at("table_spot")})),
        _static("lean_toward", "unexpected_to",
                _one(_a(1, "lean_toward", who="mom", target="dad", to="curb"), start={"mom": _at("bench_spot")}),
                "unexpected_param",
                'shot s1, t=1: mom cannot lean_toward; lean_toward takes who, target, dur; "to" is not one of them'),
        _dynamic("lean_toward", "prop_not_on_stage",
                 _one(_a(1, "lean_toward", who="mom", target="suitcase"), start={"mom": _at("bench_spot")}),
                 "not_on_stage",
                 # locate.py Locator._prop_from_world (the locate call in verbs_body.py apply_lean_toward)
                 "shot s1, t=1: mom cannot lean_toward; suitcase is not on stage"),
    ]


def _props() -> list[VerbCase]:
    on_table = {"hymn_books": {"on": "table.top"}, **TABLE}
    return [
        # ------------------------------------------------------------------ pick_up
        _ok("pick_up", "ok", _one(_a(1, "pick_up", who="dad", what="hymn_books"),
                                  start={"dad": _at("by_table")}, prop_start=on_table)),
        _ok("pick_up", "ok_walks_over", _one(_a(1, "pick_up", who="dad", what="hymn_books"),
                                             start={"dad": _at("door")}, prop_start=on_table)),
        _static("pick_up", "too_big",
                _one(_a(1, "pick_up", who="dad", what="table"), start={"dad": _at("by_table")}, prop_start=TABLE),
                "too_big",
                'shot s1, t=1: dad cannot pick_up "table"; "table" is furniture-sized and too big to carry'),
        _dynamic("pick_up", "held_by_someone_else",
                 _one(_a(1, "pick_up", who="dad", what="hymn_books"),
                      start={"dad": _at("bench_spot"), "mom": _at("beside_bench", holding="hymn_books")}),
                 "already_held",
                 # state.py World._where (via World.pick_up)
                 'shot s1, t=1: dad cannot pick_up "hymn_books"; mom is holding it'),
        _dynamic("pick_up", "not_on_stage",
                 _one(_a(1, "pick_up", who="dad", what="cap"), start={"dad": _at("door")}),
                 "not_on_stage",
                 # state.py World._require_placed (via World.pick_up)
                 'shot s1, t=1: dad cannot pick_up "cap"; cap is not on stage'),
        # Added with finding F3 (verbs_core.py apply_pick_up steps to a spot the arm reaches
        # from). Standing over the cap, whose grip is under the hips: it steps back first.
        _ok("pick_up", "steps_back_off_the_cap", _one(_a(1, "pick_up", who="dad", what="cap"),
                                                      start={"dad": _at("door")}, prop_start={"cap": _at("door")})),
        # hand_r is full, so arm_l takes it: the walk-up stops where the left shoulder reaches.
        _ok("pick_up", "left_hand_walks_up", _one(_a(1, "pick_up", who="dad", what="cap"),
                                                  start={"dad": _at("curb", holding="hymn_books")},
                                                  prop_start={"cap": _at("door")})),
        _dynamic("pick_up", "out_of_reach",
                 # The cap on a chair standing on the table is above the kid's standing reach
                 # from every spot it could step to.
                 _one(_a(1, "pick_up", who="kid", what="cap"), start={"kid": _at("by_table")},
                      prop_start={**TABLE, "chair": {"on": "table.top"}, "cap": {"on": "chair.seat"}}),
                 "out_of_reach",
                 # errors.py out_of_reach_reason (ik.OutOfReach from verbs_core.py _reach_spot, the
                 # closest miss over the spots tried)
                 "shot s1, t=1: kid cannot pick_up \"cap\"; kid's arm_r cannot reach: target is 0.23 from the "
                 "shoulder, reach is 0.14"),
        _dynamic("pick_up", "seated_out_of_reach",
                 # Seated, mom only bends from the chair (she never steps): the cap at its foot is too low.
                 _one(_a(1, "pick_up", who="mom", what="cap"), start={"mom": _at("bench_spot", **{"in": "chair.seat"})},
                      prop_start={**CHAIR, "cap": _at("bench_spot")}),
                 "out_of_reach",
                 # errors.py out_of_reach_reason (ik.OutOfReach from verbs_core.py _crouch_solution)
                 "shot s1, t=1: mom cannot pick_up \"cap\"; mom's arm_r cannot reach: target is 0.23 from the "
                 "shoulder, reach is 0.17"),

        # --------------------------------------------------------------------- hold
        _ok("hold", "ok", _one(_a(1, "hold", who="mom", what="hymn_books"),
                               start={"mom": _at("bench_spot", holding="hymn_books")})),
        _static("hold", "too_big",
                _one(_a(1, "hold", who="mom", what="sedan"), start={"mom": _at("bench_spot")}),
                "too_big",
                'shot s1, t=1: mom cannot hold "sedan"; "sedan" is vehicle-sized and too big to carry'),
        _dynamic("hold", "not_holding",
                 _one(_a(1, "hold", who="mom", what="hymn_books"), start={"mom": _at("by_table")},
                      prop_start=on_table),
                 "not_holding",
                 # state.py World._where (via World._require_in_hand / World.hold)
                 'shot s1, t=1: mom cannot hold "hymn_books"; it is on table.top'),

        # --------------------------------------------------------------------- drop
        _ok("drop", "ok", _one(_a(1, "drop", who="mom", what="hymn_books"),
                               start={"mom": _at("bench_spot", holding="hymn_books")})),
        _static("drop", "unexpected_on",
                _one(_a(1, "drop", who="mom", what="hymn_books", on="table"),
                     start={"mom": _at("bench_spot", holding="hymn_books")}, prop_start=TABLE),
                "unexpected_param",
                'shot s1, t=1: mom cannot drop "hymn_books"; drop takes who, what; "on" is not one of them'),
        _dynamic("drop", "not_holding",
                 _one(_a(1, "drop", who="mom", what="hymn_books"), start={"mom": _at("bench_spot")},
                      prop_start={"hymn_books": {"at": "beside_bench"}}),
                 "not_holding",
                 # state.py World._where (via World._require_in_hand / World.hold in verbs_core.py apply_drop)
                 'shot s1, t=1: mom cannot drop "hymn_books"; it is on the ground'),

        # ------------------------------------------------------------------ hand_to
        _ok("hand_to", "ok", _one(_a(1, "hand_to", who="mom", what="hymn_books", to="dad"),
                                  start={"mom": _at("bench_spot", holding="hymn_books"),
                                         "dad": _at("beside_bench", facing="left")})),
        _static("hand_to", "self",
                _one(_a(1, "hand_to", who="mom", what="hymn_books", to="mom"),
                     start={"mom": _at("bench_spot", holding="hymn_books")}),
                "self_target",
                'shot s1, t=1: mom cannot hand_to "hymn_books"; "to" is mom themselves'),
        _dynamic("hand_to", "someone_else_holds_it",
                 _one(_a(1, "hand_to", who="dad", what="hymn_books", to="kid"),
                      start={"dad": _at("bench_spot"), "mom": _at("beside_bench", holding="hymn_books"),
                             "kid": _at("door")}),
                 "not_holding",
                 # state.py World._where (via World._require_in_hand / World.hand_to)
                 'shot s1, t=1: dad cannot hand_to "hymn_books"; mom is holding it'),
        _dynamic("hand_to", "taker_offstage",
                 _one(_a(1, "hand_to", who="mom", what="hymn_books", to="kid"),
                      start={"mom": _at("bench_spot", holding="hymn_books"), "kid": _at("offstage_left")}),
                 "offstage",
                 # state.py World._require_on_stage (via World.hand_to)
                 'shot s1, t=1: mom cannot hand_to "hymn_books"; kid is offstage'),

        # ------------------------------------------------------------------- put_on
        _ok("put_on", "ok", _one(_a(1, "put_on", who="dad", what="cap"),
                                 start={"dad": _at("door", holding="cap")})),
        _ok("put_on", "ok_on_someone_else", _one(_a(1, "put_on", who="mom", what="cap", target="kid"),
                                                 start={"mom": _at("bench_spot", holding="cap"),
                                                        "kid": _at("beside_bench", facing="left")})),
        _static("put_on", "prop_target",
                _one(_a(1, "put_on", who="dad", what="cap", target="table"),
                     start={"dad": _at("by_table", holding="cap")}, prop_start=TABLE),
                "unknown_target",
                'shot s1, t=1: dad cannot put_on "cap"; "target" must be a person in the cast, and "table" is a prop'),
        _dynamic("put_on", "head_full",
                 _one(_a(0, "put_on", who="dad", what="cap"),
                      _a(1, "put_on", who="mom", what="lamp", target="dad"),
                      start={"dad": _at("beside_bench", holding="cap", facing="left"),
                             "mom": _at("bench_spot", holding="lamp")}),
                 "head_full",
                 # state.py World.put_on
                 'shot s1, t=1: mom cannot put_on "lamp"; dad is already wearing cap'),

        # ----------------------------------------------------------------- place_on
        _ok("place_on", "ok", _one(_a(1, "place_on", who="dad", what="hymn_books", on="table"),
                                   start={"dad": _at("by_table", holding="hymn_books")}, prop_start=TABLE)),
        _static("place_on", "on_itself",
                _one(_a(1, "place_on", who="dad", what="hymn_books", on="hymn_books"),
                     start={"dad": _at("by_table", holding="hymn_books")}),
                "self_target",
                'shot s1, t=1: dad cannot place_on "hymn_books"; "on" is "hymn_books" itself'),
        _dynamic("place_on", "socket_taken",
                 _one(_a(1, "place_on", who="dad", what="hymn_books", on="table", socket="top"),
                      start={"dad": _at("by_table", holding="hymn_books")},
                      prop_start={"lamp": {"on": "table.top"}, **TABLE}),
                 "socket_taken",
                 # state.py World._check_socket_for_prop (via World.place_on in verbs_objects.py apply_place_on)
                 'shot s1, t=1: dad cannot place_on "hymn_books"; table.top is taken by lamp'),
        _dynamic("place_on", "out_of_reach",
                 _one(_a(1, "place_on", who="dad", what="hymn_books", on="table"),
                      start={"dad": _at("door", holding="hymn_books")}, prop_start=TABLE),
                 "out_of_reach",
                 # errors.py out_of_reach_reason (ik.OutOfReach from verbs_objects.py apply_place_on, which
                 # never walks): the books' grip on the table top is below dad's shoulder and beyond a
                 # standing arm, so he tries bending and kneeling toward it (verbs_core._crouch_solution);
                 # the closest miss is 0.84 from his right shoulder, and a tall arm reaches
                 # 2 x 0.17 x 0.55 = 0.19
                 'shot s1, t=1: dad cannot place_on "hymn_books"; '
                 "dad's arm_r cannot reach: target is 0.84 from the shoulder, reach is 0.19"),

        # --------------------------------------------------------------------- open
        _ok("open", "ok", _one(_a(1, "open", who="dad", what="suitcase"), start={"dad": _at("by_table")},
                               prop_start={"suitcase": {"on": "table.top", "state": "closed"}, **TABLE})),
        _static("open", "no_open_state",
                _one(_a(1, "open", who="dad", what="lamp"), start={"dad": _at("by_table")},
                     prop_start={"lamp": {"on": "table.top"}, **TABLE}),
                "invalid_value",
                'shot s1, t=1: dad cannot open "lamp"; "lamp" has no state "open"; its states are lit, out'),
        _dynamic("open", "already_open",
                 _one(_a(1, "open", who="dad", what="suitcase"), start={"dad": _at("by_table")},
                      prop_start={"suitcase": {"on": "table.top", "state": "open"}, **TABLE}),
                 "already_open",
                 # state.py World._open_close (via verbs_objects.py _open_close)
                 'shot s1, t=1: dad cannot open "suitcase"; it is already open'),
        _dynamic("open", "twice",
                 _one(_a(1, "open", who="dad", what="suitcase"), _a(3, "open", who="dad", what="suitcase"),
                      start={"dad": _at("by_table")},
                      prop_start={"suitcase": {"on": "table.top", "state": "closed"}, **TABLE}),
                 "already_open",
                 # state.py World._open_close (via verbs_objects.py _open_close)
                 'shot s1, t=3: dad cannot open "suitcase"; it is already open'),

        # -------------------------------------------------------------------- close
        _ok("close", "ok", _one(_a(1, "close", who="dad", what="suitcase"), start={"dad": _at("by_table")},
                                prop_start={"suitcase": {"on": "table.top", "state": "open"}, **TABLE})),
        _static("close", "no_closed_state",
                _one(_a(1, "close", who="dad", what="lamp"), start={"dad": _at("by_table")},
                     prop_start={"lamp": {"on": "table.top"}, **TABLE}),
                "invalid_value",
                'shot s1, t=1: dad cannot close "lamp"; "lamp" has no state "closed"; its states are lit, out'),
        _dynamic("close", "already_closed",
                 _one(_a(1, "close", who="dad", what="suitcase"), start={"dad": _at("by_table")},
                      prop_start={"suitcase": {"on": "table.top", "state": "closed"}, **TABLE}),
                 "already_closed",
                 # state.py World._open_close (via verbs_objects.py _open_close)
                 'shot s1, t=1: dad cannot close "suitcase"; it is already closed'),
        _dynamic("close", "not_on_stage",
                 _one(_a(1, "close", who="dad", what="suitcase"), start={"dad": _at("by_table")}),
                 "not_on_stage",
                 # state.py World._require_placed (via World._open_close)
                 'shot s1, t=1: dad cannot close "suitcase"; suitcase is not on stage'),

        # ---------------------------------------------------------------- set_state
        _ok("set_state", "ok", _one(_a(1, "set_state", what="lamp", state="lit"),
                                    prop_start={"lamp": {"on": "table.top", "state": "out"}, **TABLE})),
        _ok("set_state", "ok_with_who", _one(_a(1, "set_state", who="dad", what="lamp", state="out"),
                                             start={"dad": _at("by_table")},
                                             prop_start={"lamp": {"on": "table.top", "state": "lit"}, **TABLE})),
        # An unknown state is caught statically (validate), before World.set_prop_state's
        # unknown_state could raise.
        _static("set_state", "unknown_state",
                _one(_a(1, "set_state", what="lamp", state="broken"),
                     prop_start={"lamp": {"on": "table.top"}, **TABLE}),
                "invalid_value",
                'shot s1, t=1: cannot set_state "lamp"; "lamp" has no state "broken"; its states are lit, out'),
        _dynamic("set_state", "who_busy",
                 _one(_a(0, "walk_to", who="dad", to="by_table"),
                      _a(1, "set_state", who="dad", what="lamp", state="lit"),
                      start={"dad": _at("door")}, prop_start={"lamp": {"on": "table.top"}, **TABLE}),
                 "busy",
                 # state.py World.check_free (run by compiler.py _run_action), activity from
                 # compiler.py ACTIVITY["walk_to"]; door to by_table is 0.9 at 0.35/s = 2.57 s
                 'shot s1, t=1: dad cannot set_state "lamp"; dad is still walking until t=2.57'),
    ]


def _vehicles() -> list[VerbCase]:
    in_sedan = {"in": "sedan.driver"}
    return [
        # ------------------------------------------------------------------- get_in
        _ok("get_in", "ok", _one(_a(1, "get_in", who="dad", what="sedan"),
                                 start={"dad": _at("table_spot")}, prop_start=SEDAN_CURB)),
        _ok("get_in", "ok_passenger", _one(_a(1, "get_in", who="mom", what="sedan", socket="passenger"),
                                           start={"mom": _at("curb")}, prop_start=SEDAN_CURB)),
        _static("get_in", "not_a_vehicle",
                _one(_a(1, "get_in", who="dad", what="chair"), start={"dad": _at("door")}, prop_start=CHAIR),
                "not_a_vehicle",
                'shot s1, t=1: dad cannot get_in "chair"; "chair" is furniture-sized, not a vehicle'),
        _dynamic("get_in", "socket_taken",
                 _one(_a(1, "get_in", who="dad", what="sedan", socket="driver"),
                      start={"dad": _at("table_spot"), "mom": _at("curb", **in_sedan)}, prop_start=SEDAN_CURB),
                 "socket_taken",
                 # state.py World._check_socket (via World.get_in, run on a copy by verbs_vehicles.py apply_get_in)
                 'shot s1, t=1: dad cannot get_in "sedan"; sedan.driver is taken by mom'),
        _dynamic("get_in", "already_seated",
                 _one(_a(1, "get_in", who="dad", what="sedan"),
                      start={"dad": _at("bench_spot", **{"in": "chair.seat"})},
                      prop_start={**CHAIR, **SEDAN_CURB}),
                 "already_seated",
                 # state.py World.get_in (_seat_reason)
                 'shot s1, t=1: dad cannot get_in "sedan"; dad is seated in chair.seat'),
        _dynamic("get_in", "not_on_stage",
                 _one(_a(1, "get_in", who="dad", what="sedan"), start={"dad": _at("curb")}),
                 "not_on_stage",
                 # verbs_vehicles.py _on_stage (via apply_get_in)
                 'shot s1, t=1: dad cannot get_in "sedan"; sedan is not on stage'),

        # ------------------------------------------------------------------ get_out
        _ok("get_out", "ok", _one(_a(1, "get_out", who="dad", what="sedan"),
                                  start={"dad": _at("curb", **in_sedan)}, prop_start=SEDAN_CURB)),
        _static("get_out", "unexpected_socket",
                _one(_a(1, "get_out", who="dad", what="sedan", socket="driver"),
                     start={"dad": _at("curb", **in_sedan)}, prop_start=SEDAN_CURB),
                "unexpected_param",
                'shot s1, t=1: dad cannot get_out "sedan"; get_out takes who, what; "socket" is not one of them'),
        _dynamic("get_out", "not_in_vehicle",
                 _one(_a(1, "get_out", who="dad", what="sedan"), start={"dad": _at("table_spot")},
                      prop_start=SEDAN_CURB),
                 "not_in_vehicle",
                 # state.py World.get_out (run on a copy by verbs_vehicles.py apply_get_out)
                 'shot s1, t=1: dad cannot get_out "sedan"; dad is not in sedan'),

        # ----------------------------------------------------------------- drive_to
        _ok("drive_to", "ok", _one(_a(1, "drive_to", who="dad", what="sedan", to="corner"),
                                   set="street", start={"dad": _at("garage", **in_sedan)},
                                   prop_start=SEDAN_GARAGE)),
        _ok("drive_to", "ok_two_shots", _sp(
            _shot(_a(1, "get_in", who="dad", what="sedan"),
                  start={"dad": _at("table_spot")}, prop_start=SEDAN_CURB),
            _shot(_a(1, "drive_to", who="dad", what="sedan", to="corner"),
                  id="s2", set="street", start={"dad": _at("garage", **in_sedan)}, prop_start=SEDAN_GARAGE),
        )),
        _static("drive_to", "unknown_mark",
                _one(_a(1, "drive_to", who="dad", what="sedan", to="nowhere"),
                     set="street", start={"dad": _at("garage", **in_sedan)}, prop_start=SEDAN_GARAGE),
                "unknown_mark",
                'shot s1, t=1: dad cannot drive_to "sedan"; "nowhere" is not a mark in this shot; '
                "the marks are offstage_left, offstage_right, garage, corner"),
        _dynamic("drive_to", "from_passenger_seat",
                 _one(_a(1, "drive_to", who="mom", what="sedan", to="corner"),
                      set="street", start={"mom": _at("garage", **{"in": "sedan.passenger"})},
                      prop_start=SEDAN_GARAGE),
                 "not_driver",
                 # state.py World.check_driver
                 'shot s1, t=1: mom cannot drive_to "sedan"; mom is in sedan.passenger, not the driver\'s seat'),
        _dynamic("drive_to", "not_in_vehicle",
                 _one(_a(1, "drive_to", who="kid", what="sedan", to="corner"),
                      set="street", start={"kid": _at("garage")}, prop_start=SEDAN_GARAGE),
                 "not_driver",
                 # state.py World.check_driver
                 'shot s1, t=1: kid cannot drive_to "sedan"; kid is not in the driver\'s seat of sedan'),

        # ------------------------------------------------------------------ ride_to
        _ok("ride_to", "ok", _one(_a(1, "ride_to", who="mom", what="sedan", to="corner"),
                                  set="street",
                                  start={"dad": _at("garage", **in_sedan),
                                         "mom": _at("garage", **{"in": "sedan.passenger"})},
                                  prop_start=SEDAN_GARAGE)),
        _static("ride_to", "missing_what",
                _one(_a(1, "ride_to", who="mom", to="corner"), set="street",
                     start={"mom": _at("garage", **{"in": "sedan.passenger"})}, prop_start=SEDAN_GARAGE),
                "missing_param",
                'shot s1, t=1: mom cannot ride_to; ride_to requires who, what, to; "what" is missing'),
        _dynamic("ride_to", "no_driver",
                 _one(_a(1, "ride_to", who="mom", what="sedan", to="corner"),
                      set="street", start={"mom": _at("garage", **{"in": "sedan.passenger"})},
                      prop_start=SEDAN_GARAGE),
                 "no_driver",
                 # state.py World.check_rider
                 'shot s1, t=1: mom cannot ride_to "sedan"; nobody is driving sedan'),
        _dynamic("ride_to", "not_in_vehicle",
                 _one(_a(1, "ride_to", who="kid", what="sedan", to="corner"),
                      set="street", start={"dad": _at("garage", **in_sedan), "kid": _at("garage")},
                      prop_start=SEDAN_GARAGE),
                 "not_in_vehicle",
                 # state.py World.check_rider
                 'shot s1, t=1: kid cannot ride_to "sedan"; kid is not in sedan'),
    ]


def _effects() -> list[VerbCase]:
    # Effects run through effects.py effect_for. Its `at` is resolved with the shot's locate, so
    # an `at` naming a prop that is not on stage fails there with locate.py's reason. Effects
    # have no `who` and no `what`, so the message head is "cannot <effect>".
    on_stage = {"mom": _at("bench_spot")}
    return [
        # ----------------------------------------------------------------- confetti
        _ok("confetti", "ok", _one(_a(1, "confetti", at="curb", dur=2), start=on_stage)),
        _ok("confetti", "ok_default_at", _one(_a(1, "confetti"), start=on_stage)),
        _static("confetti", "bad_dur",
                _one(_a(1, "confetti", at="curb", dur=-1), start=on_stage),
                "bad_time",
                "shot s1, t=1: cannot confetti; dur must be a finite number > 0, not -1"),
        _dynamic("confetti", "at_prop_not_on_stage",
                 _one(_a(1, "confetti", at="cap"), start=on_stage),
                 "not_on_stage",
                 # locate.py Locator._prop_from_world (the locate call in effects.py effect_for)
                 "shot s1, t=1: cannot confetti; cap is not on stage"),

        # -------------------------------------------------------------------- steam
        _ok("steam", "ok", _one(_a(1, "steam", at="table", dur=3), start=on_stage, prop_start=TABLE)),
        _static("steam", "unexpected_value",
                _one(_a(1, "steam", at="table", value="dusk"), start=on_stage, prop_start=TABLE),
                "unexpected_param",
                'shot s1, t=1: cannot steam; steam takes at, dur; "value" is not one of them'),
        _dynamic("steam", "at_prop_not_on_stage",
                 _one(_a(1, "steam", at="lamp", dur=2), start=on_stage),
                 "not_on_stage",
                 # locate.py Locator._prop_from_world (the locate call in effects.py effect_for)
                 "shot s1, t=1: cannot steam; lamp is not on stage"),

        # --------------------------------------------------------------------- rain
        _ok("rain", "ok", _one(_a(1, "rain", dur=3), start=on_stage)),
        _static("rain", "unexpected_at",
                _one(_a(1, "rain", at="curb"), start=on_stage),
                "unexpected_param",
                'shot s1, t=1: cannot rain; rain takes dur; "at" is not one of them'),
        _static("rain", "bad_dur",
                _one(_a(1, "rain", dur=0), start=on_stage),
                "bad_time",
                "shot s1, t=1: cannot rain; dur must be a finite number > 0, not 0"),

        # -------------------------------------------------------------------- smoke
        _ok("smoke", "ok", _one(_a(1, "smoke", at="mom", dur=2), start=on_stage)),
        _static("smoke", "unexpected_what",
                _one(_a(1, "smoke", what="lamp"), start=on_stage),
                "unexpected_param",
                'shot s1, t=1: cannot smoke "lamp"; smoke takes at, dur; "what" is not one of them'),
        _dynamic("smoke", "at_prop_not_on_stage",
                 _one(_a(1, "smoke", at="suitcase"), start=on_stage),
                 "not_on_stage",
                 # locate.py Locator._prop_from_world (the locate call in effects.py effect_for)
                 "shot s1, t=1: cannot smoke; suitcase is not on stage"),

        # -------------------------------------------------------------- time_of_day
        _ok("time_of_day", "ok", _one(_a(1, "time_of_day", value="dusk", dur=2), start=on_stage)),
        _static("time_of_day", "noon",
                _one(_a(1, "time_of_day", value="noon"), start=on_stage),
                "invalid_value",
                'shot s1, t=1: cannot time_of_day; "noon" is not a time of day; use one of dawn, day, dusk, night'),
        _static("time_of_day", "missing_value",
                _one(_a(1, "time_of_day"), start=on_stage),
                "missing_param",
                'shot s1, t=1: cannot time_of_day; time_of_day requires value; "value" is missing'),
    ]


def _camera() -> list[VerbCase]:
    # A target that is not on stage when the move happens is the camera's one dynamic error:
    # compiler.py _camera reports it as bad_camera with locate's reason, in the
    # errors.error_message format with no who and the target in quotes.
    cast = {"dad": _at("door"), "mom": _at("curb")}

    def cam(*cues: dict, prop_start: dict | None = None) -> Screenplay:
        return _one(start=cast, prop_start=prop_start, camera=list(cues))

    return [
        # --------------------------------------------------------------------- wide
        _ok("wide", "ok", cam(_cue(0, "wide"), _cue(2, "close_up", target="dad"), _cue(4, "wide", dur=1))),
        _static("wide", "unexpected_target",
                cam(_cue(0, "wide", target="mom")),
                "unexpected_param",
                'shot s1, t=0: cannot wide "mom"; wide takes dur; "target" is not one of them'),

        # ----------------------------------------------------------------- dolly_to
        _ok("dolly_to", "ok", cam(_cue(1, "dolly_to", target="mom", dur=2))),
        _static("dolly_to", "missing_target",
                cam(_cue(1, "dolly_to", dur=2)),
                "missing_param",
                'shot s1, t=1: cannot dolly_to; dolly_to requires target; "target" is missing'),
        _dynamic("dolly_to", "target_not_on_stage",
                 cam(_cue(1, "dolly_to", target="cap")),
                 "bad_camera",
                 # locate.py Locator._prop_from_world, reported by compiler.py _camera
                 'shot s1, t=1: cannot dolly_to "cap"; cap is not on stage'),

        # ------------------------------------------------------------------- pan_to
        _ok("pan_to", "ok", cam(_cue(1, "pan_to", target="table"), prop_start=TABLE)),
        _static("pan_to", "missing_target",
                cam(_cue(1, "pan_to")),
                "missing_param",
                'shot s1, t=1: cannot pan_to; pan_to requires target; "target" is missing'),
        _dynamic("pan_to", "target_not_on_stage",
                 cam(_cue(1, "pan_to", target="lamp", dur=1)),
                 "bad_camera",
                 # locate.py Locator._prop_from_world, reported by compiler.py _camera
                 'shot s1, t=1: cannot pan_to "lamp"; lamp is not on stage'),

        # ----------------------------------------------------------------- close_up
        _ok("close_up", "ok", cam(_cue(1, "close_up", target="dad"))),
        _static("close_up", "unknown_target",
                cam(_cue(1, "close_up", target="ghost")),
                "unknown_target",
                f'shot s1, t=1: cannot close_up "ghost"; "ghost" is not a person, prop or mark in this shot; '
                f"{CAST_LIST}; {PROPS_LIST}; {YARD_MARKS_LIST}"),
        _dynamic("close_up", "target_not_on_stage",
                 cam(_cue(1, "close_up", target="suitcase")),
                 "bad_camera",
                 # locate.py Locator._prop_from_world, reported by compiler.py _camera
                 'shot s1, t=1: cannot close_up "suitcase"; suitcase is not on stage'),

        # ---------------------------------------------------------------- pull_back
        _ok("pull_back", "ok", cam(_cue(1, "close_up", target="mom"), _cue(3, "pull_back", dur=2))),
        _static("pull_back", "bad_dur",
                cam(_cue(1, "pull_back", dur=0)),
                "bad_time",
                "shot s1, t=1: cannot pull_back; dur must be a finite number > 0, not 0"),
    ]


CASES: list[VerbCase] = [*_movement(), *_body(), *_props(), *_vehicles(), *_effects(), *_camera()]

ACTIONS: list[str] = list(VOCABULARY["actions"])
CAMERA_MOVES: list[str] = list(VOCABULARY["camera"])


def cases_for(verb: str) -> list[VerbCase]:
    """Every case for one action verb or camera move, in catalogue order."""
    if verb not in VOCABULARY["actions"] and verb not in VOCABULARY["camera"]:
        raise KeyError(f"{verb!r} is not a vocabulary action or camera move")
    return [c for c in CASES if c.verb == verb]
