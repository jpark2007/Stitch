"""Story scenarios: realistic multi-shot family screenplays on one self-contained scene catalogue.

Test data only (not collected by pytest: the file name does not start with ``test_``). Import it
from a test module as ``from story_scenes import STORIES, story_scene``; pytest's default
``prepend`` import mode puts this directory on ``sys.path``.

Where verb_cases.py holds one minimal screenplay per verb, these are what a director agent
would write for a whole memory: two to four shots, at least one set change, cued narration,
camera moves, and the verbs used the way a family story uses them. Together the stories use
every action verb, every effect and every camera move in shared/vocabulary.json
(test_story_scenarios checks that).

The catalogue deliberately does not read api/seed (another stream owns it and may change it):
:func:`story_scene` builds every set, prop and person here from ``schemas`` models.

Stage geometry (shared/CONTRACTS.md): a mark ``[u, h]`` stands at ``x = 2u - 1`` on a surface
``h`` above the ground. A puppet is ``0.5 x build`` tall (small 0.4, short 0.45, average 0.5,
tall 0.55). Furniture is 0.4 tall, so a table whose ``top`` socket is at v = 0.25 has its top
0.3 above the ground, within a standing arm's reach; a chair or pew seat at v = 0.5 is 0.2 up.
"""

from __future__ import annotations

from typing import Any

import schemas

__all__ = ["CAST", "PROPS", "STORIES", "story_scene"]

FAMILY = "story_scenes"

CAST = ["grandpa", "grandma", "dad", "mom", "teen", "kid"]
PROPS = [
    "car", "chair", "armchair", "pew", "table", "picnic_table", "stove", "cake", "bouquet",
    "hymn_books", "umbrella", "box_a", "box_b", "box_c", "diploma", "gift", "cup", "kettle",
    "keys", "cap",
]


# --------------------------------------------------------------------------------- sets

def _layers(*heights: float, color: str) -> list[schemas.SetLayer]:
    return [schemas.SetLayer(art=None, depth=i, height=h, color=color) for i, h in enumerate(heights)]


SETS: dict[str, dict[str, Any]] = {
    "kitchen": {
        "name": "the kitchen on Elm Street",
        "layers": (1.0, 0.45, 0.15), "color": "#e9dcc3",
        "marks": {
            "back_door": [0.08, 0.0],     # x = -0.84
            "stove_spot": [0.22, 0.0],    # x = -0.56: where the stove stands
            "by_stove": [0.32, 0.0],      # x = -0.36
            "by_table": [0.48, 0.0],      # x = -0.04: in reach of the table top
            "table_spot": [0.6, 0.0],     # x =  0.2:  where the table stands
            "window": [0.78, 0.0],        # x =  0.56
            "hall_door": [0.92, 0.0],     # x =  0.84
        },
    },
    "church_steps": {
        "name": "St. Mary's, outside",
        "layers": (1.4, 0.6, 0.3), "color": "#b9b2a6",
        "marks": {
            "sidewalk": [0.15, 0.0],      # x = -0.7
            "steps_left": [0.3, 0.0],     # x = -0.4
            "steps_foot": [0.5, 0.0],     # x =  0.0
            "steps_mid": [0.55, 0.15],    # x =  0.1, halfway up
            "steps_top": [0.6, 0.3],      # x =  0.2, the top step
            "church_door": [0.75, 0.3],   # x =  0.5
            "curb": [0.92, 0.0],          # x =  0.84
        },
    },
    "chapel": {
        "name": "St. Mary's, inside",
        "layers": (1.4, 0.7), "color": "#d8cfc0",
        "marks": {
            "back_door": [0.06, 0.0],     # x = -0.88
            "aisle_back": [0.2, 0.0],     # x = -0.6
            "pew_spot": [0.36, 0.0],      # x = -0.28: where the pew stands
            "aisle_mid": [0.5, 0.0],      # x =  0.0
            "altar": [0.68, 0.0],         # x =  0.36
            "altar_right": [0.8, 0.0],    # x =  0.6
        },
    },
    "backyard": {
        "name": "the back yard",
        "layers": (1.0, 0.5, 0.2), "color": "#9fbf7f",
        "marks": {
            "porch_steps": [0.1, 0.0],    # x = -0.8
            "lawn_left": [0.28, 0.0],     # x = -0.44
            "by_picnic": [0.45, 0.0],     # x = -0.1: in reach of the picnic table top
            "picnic_spot": [0.58, 0.0],   # x =  0.16: where the picnic table stands
            "lawn_right": [0.75, 0.0],    # x =  0.5
            "gate": [0.92, 0.0],          # x =  0.84
        },
    },
    "street": {
        "name": "Maple Avenue",
        "layers": (1.2, 0.4), "color": "#a7a9ac",
        "marks": {
            "front_walk": [0.12, 0.0],    # x = -0.76
            "curb": [0.3, 0.0],           # x = -0.4
            "car_spot": [0.55, 0.0],      # x =  0.1: where the car is parked
            "corner": [0.88, 0.0],        # x =  0.76
            "sidewalk_far": [0.75, 0.0],  # x =  0.5
        },
    },
    "living_room": {
        "name": "the living room",
        "layers": (1.0, 0.5), "color": "#d9b99b",
        "marks": {
            "front_door": [0.08, 0.0],    # x = -0.84
            "rug": [0.3, 0.0],            # x = -0.4
            "armchair_spot": [0.5, 0.0],  # x =  0.0: where the armchair stands
            "beside_armchair": [0.62, 0.0],  # x = 0.24
            "tree": [0.78, 0.0],          # x =  0.56: the Christmas tree corner
            "hall": [0.92, 0.0],          # x =  0.84
        },
    },
    "graduation_stage": {
        "name": "Lincoln High auditorium",
        "layers": (1.3, 0.6, 0.15), "color": "#7b2e3a",
        "marks": {
            "wings": [0.12, 0.0],         # x = -0.76
            "stage_left": [0.3, 0.15],    # x = -0.4, up on the stage
            "stage_center": [0.48, 0.15],  # x = -0.04
            "podium": [0.6, 0.15],        # x =  0.2
            "front_row": [0.85, 0.0],     # x =  0.7, down in the audience
        },
    },
    "park": {
        "name": "Riverside Park",
        "layers": (1.2, 0.5, 0.2), "color": "#6f8f72",
        "marks": {
            "gate": [0.1, 0.0],           # x = -0.8
            "path_left": [0.28, 0.0],     # x = -0.44
            "bench": [0.5, 0.0],          # x =  0.0
            "pond": [0.7, 0.0],           # x =  0.4
            "road_left": [0.2, 0.0],      # x = -0.6: where a car pulls in
            "road_end": [0.65, 0.0],      # x =  0.3
            "path_right": [0.9, 0.0],     # x =  0.8
        },
    },
    "new_house_porch": {
        "name": "the new house on Birch Lane",
        "layers": (1.3, 0.5, 0.2), "color": "#c9a27e",
        "marks": {
            "driveway": [0.12, 0.0],      # x = -0.76
            "walkway": [0.3, 0.0],        # x = -0.4
            "porch_spot": [0.5, 0.0],     # x =  0.0: where the boxes pile up
            "beside_pile": [0.4, 0.0],    # x = -0.2
            "porch_door": [0.75, 0.0],    # x =  0.5
            "porch_swing": [0.9, 0.0],    # x =  0.8
        },
    },
}


# -------------------------------------------------------------------------------- props

def _prop(pid: str, size: str, color: str, *, art: tuple[str, ...] = ("body",), **kw: Any) -> schemas.Prop:
    return schemas.Prop(
        id=pid, family_id=FAMILY, name=pid.replace("_", " "), size=size, color=color,
        art={k: None for k in art}, **kw,
    )


def _box(pid: str, color: str) -> schemas.Prop:
    # A moving box: gripped halfway up its side, so a pick_up from the floor needs a crouch;
    # its lid is a socket, so another box stacks on it.
    return _prop(pid, "carried", color, art=("body", "open"), aspect=1.2, anchor=[0.5, 1.0], grip=[0.5, 0.5],
                 sockets={"top": [0.5, 0.0]}, states={"closed": {}, "open": {"body": "open"}})


def _props() -> list[schemas.Prop]:
    return [
        # The family's first car, a station wagon: two seats and two spinning wheels.
        _prop("car", "vehicle", "#2e5e4e", art=("body", "wheel"), aspect=2.0, anchor=[0.5, 1.0],
              sockets={"driver": [0.42, 0.55], "passenger": [0.6, 0.55]},
              parts={
                  "wheel_back": schemas.PropPart(art="wheel", at=[0.22, 0.88], spin=True),
                  "wheel_front": schemas.PropPart(art="wheel", at=[0.78, 0.88], spin=True),
              },
              states={"parked": {}, "driving": {}}),
        _prop("chair", "furniture", "#8a5a3c", aspect=0.8, anchor=[0.5, 1.0], sockets={"seat": [0.5, 0.5]}),
        _prop("armchair", "furniture", "#7a3b3b", aspect=1.1, anchor=[0.5, 1.0], sockets={"seat": [0.5, 0.55]}),
        # A two-seat pew.
        _prop("pew", "furniture", "#5c3a21", aspect=2.4, anchor=[0.5, 1.0],
              sockets={"seat_aisle": [0.7, 0.5], "seat_wall": [0.3, 0.5]}),
        _prop("table", "furniture", "#a0522d", aspect=1.5, anchor=[0.5, 1.0], sockets={"top": [0.5, 0.25]}),
        _prop("picnic_table", "furniture", "#9b7b4f", aspect=1.8, anchor=[0.5, 1.0], sockets={"top": [0.5, 0.25]}),
        _prop("stove", "furniture", "#dedede", aspect=0.9, anchor=[0.5, 1.0], sockets={"burner": [0.5, 0.2]}),
        # A birthday cake with candles that light.
        _prop("cake", "carried", "#f4d6e0", art=("body", "lit"), aspect=1.3, anchor=[0.5, 1.0], grip=[0.5, 0.7],
              states={"unlit": {}, "lit": {"body": "lit"}}),
        _prop("bouquet", "hand", "#f2f2f2", aspect=0.6, anchor=[0.5, 1.0], grip=[0.5, 0.8]),
        _prop("hymn_books", "hand", "#2f4a7a", aspect=1.0, anchor=[0.5, 1.0], grip=[0.5, 0.0]),
        _prop("umbrella", "carried", "#1f3a5f", aspect=0.9, anchor=[0.5, 1.0], grip=[0.5, 0.95]),
        _box("box_a", "#b08850"),
        _box("box_b", "#a57d48"),
        _box("box_c", "#c09a62"),
        _prop("diploma", "hand", "#f5f0e1", aspect=1.5, anchor=[0.5, 1.0], grip=[0.5, 0.5]),
        _prop("gift", "carried", "#c0392b", art=("body", "open"), aspect=1.0, anchor=[0.5, 1.0], grip=[0.5, 0.3],
              states={"closed": {}, "open": {"body": "open"}}),
        _prop("cup", "hand", "#ffffff", aspect=0.8, anchor=[0.5, 1.0], grip=[0.5, 0.5]),
        _prop("kettle", "hand", "#b0b0b0", aspect=1.1, anchor=[0.5, 1.0], grip=[0.5, 0.1]),
        _prop("keys", "tiny", "#d4af37", aspect=1.0, anchor=[0.5, 1.0], grip=[0.5, 0.5]),
        # A mortarboard, worn in the head socket and held by its brim.
        _prop("cap", "hand", "#1b1b1b", description="a black graduation cap", aspect=1.4, anchor=[0.5, 1.0],
              grip=[0.5, 0.6]),
    ]


# ------------------------------------------------------------------------------- people

def _person(pid: str, name: str, build: str, color: str, notes: str | None = None) -> schemas.Person:
    return schemas.Person(
        id=pid, family_id=FAMILY, name=name,
        character=schemas.CharacterSheet(color=color, build=build, notes=notes),
    )


def _people() -> list[schemas.Person]:
    return [
        _person("grandpa", "Grandpa Walt", "tall", "#4a6fa5", "walks slowly with a cane"),
        _person("grandma", "Grandma June", "short", "#b5838d", "moves carefully"),
        _person("dad", "Dad", "tall", "#3f6fb5"),
        _person("mom", "Mom", "average", "#c0504d", "always walks briskly"),
        _person("teen", "Ellie", "average", "#7e57c2"),
        _person("kid", "Sam", "small", "#6a9a3b", "energetic, never still"),
    ]


def story_scene() -> tuple[dict[str, schemas.SetDef], dict[str, schemas.Prop], dict[str, schemas.Person]]:
    """Every set, prop and person the stories use, built fresh on every call."""
    sets = {
        sid: schemas.SetDef(
            id=sid, family_id=FAMILY, name=s["name"], layers=_layers(*s["layers"], color=s["color"]),
            marks={k: list(v) for k, v in s["marks"].items()},
        )
        for sid, s in SETS.items()
    }
    return sets, {p.id: p for p in _props()}, {p.id: p for p in _people()}


# ------------------------------------------------------------------------- screenplay bits

def _a(t: float, do: str, **params: Any) -> dict[str, Any]:
    """An action. ``frm`` stands for the ``from`` parameter."""
    if "frm" in params:
        params["from"] = params.pop("frm")
    return {"t": t, "do": do, **params}


def _cam(t: float, move: str, **params: Any) -> dict[str, Any]:
    return {"t": t, "move": move, **params}


def _line(t: float, text: str, cue: str | None = None, word: str | None = None, source: str | None = None) -> dict:
    line: dict[str, Any] = {"t": t, "text": text}
    if cue is not None:
        line["cue"] = cue
    if word is not None:
        line["cueWord"] = word
    if source is not None:
        line["source"] = source
    return line


def _at(mark: str, **kw: Any) -> dict[str, Any]:
    if "in_" in kw:
        kw["in"] = kw.pop("in_")
    return {"at": mark, **kw}


def _shot(sid: str, set_id: str, *, start: dict, actions: list, camera: list, narration: list,
          prop_start: dict | None = None, marks: dict | None = None, page_text: str | None = None) -> dict:
    shot: dict[str, Any] = {
        "id": sid, "set": set_id, "start": start, "propStart": prop_start or {},
        "camera": camera, "actions": actions, "narration": narration,
    }
    if marks:
        shot["marks"] = marks
    if page_text:
        shot["pageText"] = page_text
    return shot


def _sp(memory: str, cast: list[str], props: list[str], *shots: dict, **extra: Any) -> dict[str, Any]:
    return {"memoryId": memory, "cast": cast, "props": props, "shots": list(shots), **extra}


# ---------------------------------------------------------------------------------- stories

WEDDING = _sp(
    "wedding_1979", ["mom", "dad", "grandma", "kid"], ["bouquet"],
    _shot(
        "aisle", "chapel",
        start={"dad": _at("altar_right", facing="left"), "mom": _at("offstage_left", holding="bouquet")},
        actions=[
            _a(0, "enter", who="mom", frm="offstage_left", to="aisle_back"),
            _a(3.2, "walk_to", who="mom", to="altar", id="down_the_aisle"),
            _a(8.5, "hug", who="dad", target="mom", dur=2.5, id="the_hug"),
        ],
        camera=[_cam(0, "wide"), _cam(3, "pan_to", target="mom", dur=4), _cam(8.5, "close_up", target="dad", dur=2)],
        narration=[
            _line(0, "Your mother came in alone.", source="telling:dad:s1"),
            _line(3, "She walked the whole aisle by herself.", cue="down_the_aisle", word="walked",
                  source="telling:dad:s2"),
            _line(8, "I forgot my vows and just hugged her.", cue="the_hug", word="hugged", source="telling:dad:s3"),
        ],
        page_text="June 1979: she walked the aisle alone.",
    ),
    _shot(
        "reception", "backyard",
        start={
            "mom": _at("lawn_left", holding="bouquet", facing="right"),
            "dad": _at("porch_steps", facing="right"),
            "grandma": _at("lawn_right", facing="left"),
            "kid": _at("gate", facing="left"),
        },
        marks={"beside_grandma": [0.65, 0.0]},
        actions=[
            _a(0, "walk_to", who="mom", to="beside_grandma"),
            _a(3, "hand_to", who="mom", what="bouquet", to="grandma", id="bouquet_to_grandma"),
            _a(4, "confetti", at="mom", dur=3, id="confetti"),
            _a(4.5, "run_to", who="kid", to="lawn_right"),
        ],
        camera=[_cam(0, "wide"), _cam(4, "pull_back", dur=3)],
        narration=[
            _line(0, "She didn't throw the bouquet. She walked it straight over to her grandmother.",
                  cue="bouquet_to_grandma", word="grandmother", source="telling:dad:s4"),
            _line(4, "Then the neighbors' kids threw confetti at everybody.", cue="confetti", word="confetti",
                  source="telling:dad:s5"),
        ],
        page_text="The bouquet went to Grandma June.",
    ),
)

FIRST_CAR = _sp(
    "first_car_1996", ["teen", "dad", "grandma"], ["car", "keys"],
    _shot(
        "the_keys", "street",
        start={
            "dad": _at("curb", holding="keys", facing="right"),
            "teen": _at("front_walk", facing="right"),
            "grandma": _at("sidewalk_far", facing="left"),
        },
        prop_start={"car": {"at": "car_spot", "state": "parked"}},
        actions=[
            _a(0, "hand_to", who="dad", what="keys", to="teen", id="keys"),
            _a(3, "get_in", who="teen", what="car", socket="driver", id="get_in"),
            _a(3.5, "get_in", who="dad", what="car", socket="passenger"),
            _a(7, "wave", who="grandma", target="teen", dur=2),
            _a(7.5, "drive_to", who="teen", what="car", to="corner", id="drive_off"),
            _a(8, "smoke", at="car", dur=2),
        ],
        camera=[_cam(0, "wide"), _cam(2.5, "dolly_to", target="car", dur=3), _cam(8, "pan_to", target="corner", dur=2)],
        narration=[
            _line(0, "Dad dangled the keys in front of me like it was nothing.", cue="keys", word="keys"),
            _line(3, "I got in, he got in, and Grandma stood on the sidewalk waving.", cue="get_in", word="in"),
            _line(7.5, "Then the old wagon coughed a cloud of smoke and we were off.", cue="drive_off", word="off"),
        ],
        page_text="The keys to the station wagon.",
    ),
    _shot(
        "the_park", "park",
        start={
            "teen": _at("road_left", in_="car.driver"),
            "dad": _at("road_left", in_="car.passenger"),
        },
        prop_start={"car": {"at": "road_left", "state": "driving"}},
        actions=[
            _a(0, "ride_to", who="dad", what="car", to="road_end", id="pull_in"),
            _a(4, "get_out", who="teen", what="car", id="out"),
            _a(4.5, "get_out", who="dad", what="car"),
            _a(7, "wave", who="teen", dur=1.5),
            _a(7, "smoke", at="car", dur=1.5),
        ],
        camera=[_cam(0, "wide"), _cam(4, "close_up", target="teen", dur=2)],
        narration=[
            _line(0, "We rolled all the way to Riverside Park at fifteen miles an hour.", cue="pull_in", word="rolled"),
            _line(4, "I climbed out and waved at nobody in particular.", cue="out", word="climbed"),
        ],
        page_text="Fifteen miles an hour, all the way to the park.",
    ),
)

BIRTHDAY = _sp(
    "kids_seventh_birthday", ["mom", "kid", "grandpa", "dad"], ["cake", "picnic_table", "table"],
    _shot(
        "candles", "kitchen",
        start={"mom": _at("by_table", facing="right")},
        prop_start={"table": {"at": "table_spot"}, "cake": {"on": "table.top", "state": "unlit"}},
        actions=[
            _a(0.5, "set_state", who="mom", what="cake", state="lit", id="light"),
            _a(2.5, "pick_up", who="mom", what="cake", id="lift"),
            _a(4.5, "walk_to", who="mom", to="back_door"),
        ],
        camera=[_cam(0, "close_up", target="cake", dur=1), _cam(3, "pull_back", dur=2)],
        narration=[
            _line(0, "Mom lit all seven candles in the kitchen.", cue="light", word="lit"),
            _line(2.5, "Then she carried it out the back like it was made of glass.", cue="lift", word="carried"),
        ],
    ),
    _shot(
        "the_party", "backyard",
        start={
            "mom": _at("porch_steps", holding="cake", facing="right"),
            "kid": _at("lawn_right", facing="left"),
            "grandpa": _at("gate", facing="left"),
            "dad": _at("lawn_left", facing="right"),
        },
        prop_start={"picnic_table": {"at": "picnic_spot"}},
        actions=[
            _a(0, "walk_to", who="mom", to="by_picnic"),
            _a(3, "place_on", who="mom", what="cake", on="picnic_table", socket="top", id="cake_down"),
            _a(5, "hug", who="kid", target="grandpa", dur=2, id="hug"),
            _a(5.5, "confetti", at="kid", dur=3),
            _a(5.5, "pose", who="dad", pose="arms_raised", dur=1.5),
        ],
        camera=[_cam(0, "wide"), _cam(5, "close_up", target="kid", dur=2)],
        narration=[
            _line(0, "Mom marched it out to the picnic table."),
            _line(4.5, "She set it down and everybody sang.", cue="cake_down", word="set"),
            _line(6.5, "Sam ran straight past the cake to hug Grandpa.", cue="hug", word="hug"),
        ],
        page_text="Seven candles and a hug for Grandpa.",
    ),
)

GRADUATION = _sp(
    "ellie_graduates_2014", ["teen", "grandpa", "mom", "dad"], ["diploma", "cap"],
    _shot(
        "diploma", "graduation_stage",
        start={
            "grandpa": _at("podium", holding="diploma", facing="left"),
            "teen": _at("wings", facing="right"),
            "mom": _at("front_row", facing="left"),
        },
        actions=[
            _a(0, "walk_to", who="teen", to="stage_center", id="cross"),
            _a(4.5, "hand_to", who="grandpa", what="diploma", to="teen", id="handed"),
            _a(5, "point_at", who="mom", target="teen", dur=2.5, id="point"),
        ],
        camera=[_cam(0, "wide"), _cam(4, "dolly_to", target="teen", dur=2), _cam(6.5, "pan_to", target="mom", dur=1.5)],
        narration=[
            _line(0, "They called my name and I crossed that stage so fast.", cue="cross", word="crossed"),
            _line(3.5, "Grandpa got to hand me the diploma himself.", cue="handed", word="hand"),
            _line(6, "Mom stood up in the front row and pointed at me.", cue="point", word="pointed"),
        ],
        page_text="Grandpa handed it over himself.",
    ),
    _shot(
        "the_cap", "backyard",
        start={
            "teen": _at("lawn_left", holding="cap", facing="right"),
            "dad": _at("lawn_right", facing="left"),
            "mom": _at("picnic_spot", facing="left"),
        },
        actions=[
            _a(0.5, "put_on", who="teen", what="cap", id="cap_on"),
            _a(3, "point_at", who="dad", target="cap", dur=2),
            _a(3, "hug", who="mom", target="teen", dur=2.5, id="hug"),
        ],
        camera=[_cam(0, "close_up", target="teen", dur=1.5), _cam(3, "wide", dur=2)],
        narration=[
            _line(0, "Back home I put the cap on again for one more picture.", cue="cap_on", word="put"),
            _line(3, "Dad kept pointing at the tassel and Mom wouldn't let go of me.", cue="hug", word="Mom"),
        ],
    ),
)

KITCHEN = _sp(
    "grandmas_kitchen_1988", ["grandma", "kid"], ["stove", "kettle", "table", "cup", "armchair"],
    _shot(
        "the_kettle", "kitchen",
        start={"grandma": _at("by_stove", facing="right"), "kid": _at("hall_door", facing="left")},
        prop_start={
            "stove": {"at": "stove_spot"}, "kettle": {"on": "stove.burner"},
            "table": {"at": "table_spot"}, "cup": {"on": "table.top"},
        },
        actions=[
            _a(0, "steam", at="kettle", dur=5, id="whistle"),
            _a(1.5, "walk_to", who="grandma", to="by_table"),
            _a(5, "pick_up", who="grandma", what="cup", id="cup"),
            _a(7, "walk_to", who="grandma", to="hall_door"),
            _a(7, "walk_to", who="kid", to="window"),
            _a(9.5, "follow", who="kid", target="grandma", dur=3, id="tag_along"),
        ],
        camera=[_cam(0, "close_up", target="kettle", dur=1), _cam(2, "wide", dur=2),
                _cam(9.5, "pan_to", target="hall_door", dur=2)],
        narration=[
            _line(0, "Grandma's kettle always screamed before she got to it.", cue="whistle", word="screamed"),
            _line(4.5, "She'd pour her tea into the chipped blue cup.", cue="cup", word="cup"),
            _line(9, "And I followed her everywhere, like a duckling.", cue="tag_along", word="followed"),
        ],
    ),
    _shot(
        "the_armchair", "living_room",
        start={"grandma": _at("rug", holding="cup", facing="right"), "kid": _at("front_door", facing="right")},
        prop_start={"armchair": {"at": "armchair_spot"}},
        actions=[
            _a(0.5, "sit_on", who="grandma", what="armchair", socket="seat", id="sit"),
            _a(1, "walk_to", who="kid", to="beside_armchair"),
            _a(5, "lean_toward", who="kid", target="grandma", dur=2.5, id="lean"),
        ],
        camera=[_cam(0, "wide"), _cam(5, "dolly_to", target="grandma", dur=2)],
        narration=[
            _line(0, "She'd settle into the red armchair with it.", cue="sit", word="settle"),
            _line(4.5, "And I'd lean in close to hear the stories.", cue="lean", word="lean"),
        ],
    ),
)

CHURCH = _sp(
    "the_hymn_books_again", ["grandpa", "grandma"], ["hymn_books", "pew"],
    _shot(
        "steps", "church_steps",
        start={
            "grandpa": _at("offstage_left", facing="right"),
            "grandma": _at("steps_top", holding="hymn_books", facing="left"),
        },
        actions=[
            _a(0, "enter", who="grandpa", frm="offstage_left", to="steps_foot", id="arrive"),
            _a(8, "hand_to", who="grandma", what="hymn_books", to="grandpa", id="books"),
            _a(10.5, "walk_to", who="grandpa", to="church_door"),
        ],
        camera=[_cam(0, "wide"), _cam(5, "dolly_to", target="grandma", dur=2)],
        narration=[
            _line(0, "Fifty years later he still met her on the church steps.", cue="arrive", word="met"),
            _line(7.5, "And now she handed him the hymn books to carry.", cue="books", word="handed"),
        ],
    ),
    _shot(
        "the_pew", "chapel",
        start={
            "grandpa": _at("aisle_back", holding="hymn_books", facing="right"),
            "grandma": _at("aisle_mid", facing="left"),
        },
        prop_start={"pew": {"at": "pew_spot"}},
        actions=[
            _a(0.5, "sit_on", who="grandpa", what="pew", socket="seat_wall", id="sit"),
            _a(1, "kneel", who="grandma", dur=3, id="kneel"),
            _a(6, "pose", who="grandma", pose="bow_head", dur=2, id="bow"),
            _a(7.5, "stand_up", who="grandpa", id="rise"),
        ],
        camera=[_cam(0, "wide"), _cam(1, "close_up", target="grandma", dur=2), _cam(7.5, "pull_back", dur=2)],
        narration=[
            _line(0, "He took the end of the pew.", cue="sit", word="took"),
            _line(2, "She knelt first and bowed her head.", cue="kneel", word="knelt"),
            _line(7, "When the hymn started he stood up first.", cue="rise", word="stood"),
        ],
    ),
)

MOVING_HOUSE = _sp(
    "moving_to_birch_lane_2003", ["dad", "kid", "mom"], ["box_a", "box_b", "box_c"],
    _shot(
        "packing", "living_room",
        start={"dad": _at("rug", facing="right"), "kid": _at("tree", facing="left")},
        # The box sits on the floor right at Dad's feet, on his own mark.
        prop_start={"box_a": {"at": "rug", "state": "closed"}},
        actions=[
            _a(0.5, "pick_up", who="dad", what="box_a", id="lift"),
            _a(3, "exit", who="dad", to="offstage_left", id="out_the_door"),
            _a(4, "run_to", who="kid", to="front_door", id="race"),
            _a(7, "exit", who="kid", to="offstage_left"),
        ],
        camera=[_cam(0, "close_up", target="dad", dur=1.5), _cam(3, "wide", dur=2)],
        narration=[
            _line(0, "Dad picked up the last box himself. He wouldn't let the movers touch it.",
                  cue="lift", word="picked"),
            _line(3.5, "Sam raced him to the door.", cue="race", word="raced"),
        ],
    ),
    _shot(
        "the_new_porch", "new_house_porch",
        start={
            "dad": _at("walkway", holding="box_a", facing="right"),
            "kid": _at("driveway", facing="right"),
            "mom": _at("porch_door", facing="right"),
        },
        marks={"box_corner": [0.8, 0.0]},
        prop_start={"box_b": {"at": "porch_spot", "state": "closed"}, "box_c": {"at": "box_corner"}},
        actions=[
            _a(0, "walk_to", who="dad", to="beside_pile"),
            _a(2, "place_on", who="dad", what="box_a", on="box_b", socket="top", id="stack"),
            _a(2.5, "run_to", who="kid", to="porch_door", id="run"),
            # Kneeling, facing the box: open does not bend down to the floor or turn toward the
            # prop (see test_story_scenarios.test_open_a_box_on_the_floor_standing).
            _a(5, "kneel", who="mom"),
            _a(6.5, "open", who="mom", what="box_c", id="open"),
        ],
        camera=[_cam(0, "wide"), _cam(5, "close_up", target="box_c", dur=2)],
        narration=[
            _line(0.5, "Then Dad stacked it on the others.", cue="stack", word="stacked"),
            _line(2.5, "Sam ran straight for the front door.", cue="run", word="ran"),
            _line(6, "Mom opened the good dishes first.", cue="open", word="opened"),
        ],
    ),
)

RAINY_WALK = _sp(
    "the_rainy_walk_1992", ["mom", "kid"], ["umbrella"],
    _shot(
        "maple_avenue", "street",
        start={"mom": _at("front_walk", holding="umbrella", facing="right"), "kid": _at("curb", facing="right")},
        actions=[
            _a(0, "rain", id="rain"),
            _a(0, "time_of_day", value="dusk", dur=2),
            _a(0.5, "hold", who="mom", what="umbrella", id="umbrella_up"),
            _a(2, "walk_to", who="mom", to="sidewalk_far", id="walk"),
            _a(2.5, "walk_to", who="kid", to="corner"),
            _a(8, "turn_to", who="kid", target="mom", id="look_back"),
        ],
        camera=[_cam(0, "wide"), _cam(2, "pan_to", target="sidewalk_far", dur=4)],
        narration=[
            _line(0, "It started pouring the minute we left the house.", cue="rain", word="pouring"),
            _line(2.5, "Mom put the big umbrella up and we walked anyway.", cue="walk", word="walked"),
            _line(7.5, "I kept turning around to make sure she was still there.", cue="look_back", word="turning"),
        ],
    ),
    _shot(
        "the_pond", "park",
        start={"mom": _at("gate", holding="umbrella", facing="right"), "kid": _at("path_left", facing="right")},
        actions=[
            _a(0, "rain"),
            _a(0, "time_of_day", value="night", dur=4, id="dark"),
            _a(1, "walk_to", who="kid", to="pond", id="pond"),
            _a(1.5, "walk_to", who="mom", to="bench"),
            _a(6, "turn_to", who="kid", target="mom"),
            _a(6.5, "turn_to", who="mom", target="pond"),
        ],
        camera=[_cam(0, "wide"), _cam(4, "dolly_to", target="kid", dur=2)],
        narration=[
            _line(0, "By the pond it was dark.", cue="dark", word="dark"),
            _line(1.5, "I wanted to see if the ducks got wet.", cue="pond", word="ducks"),
        ],
    ),
)

CHRISTMAS = _sp(
    "christmas_morning_1985", ["dad", "kid", "grandpa", "grandma"], ["gift", "armchair"],
    _shot(
        "the_tree", "living_room",
        start={
            "dad": _at("tree", holding="gift", facing="left"),
            "kid": _at("rug", facing="right"),
            "grandpa": _at("armchair_spot", in_="armchair.seat"),
        },
        prop_start={"armchair": {"at": "armchair_spot"}},
        marks={"by_tree": [0.68, 0.0]},
        actions=[
            _a(0, "walk_to", who="kid", to="by_tree", id="to_tree"),
            _a(3.5, "hand_to", who="dad", what="gift", to="kid", id="gift"),
            _a(6.5, "drop", who="kid", what="gift", id="drop"),
            # Kneeling: open/close do not bend down to a prop on the floor (see
            # test_story_scenarios.test_open_a_gift_on_the_floor_standing).
            _a(7.5, "kneel", who="kid"),
            _a(9, "open", who="kid", what="gift", id="open"),
            _a(10.5, "close", who="kid", what="gift", id="close"),
            _a(10.5, "lean_toward", who="grandpa", target="kid", dur=2),
        ],
        camera=[_cam(0, "wide"), _cam(6.5, "close_up", target="gift", dur=1.5), _cam(10.5, "pull_back", dur=2)],
        narration=[
            _line(0, "I hurried straight to the tree.", cue="to_tree", word="I"),
            _line(3.25, "Dad handed me the big red box first.", cue="gift", word="handed"),
            _line(5.5, "It was so heavy I dropped it right there.", cue="drop", word="dropped"),
            _line(8.5, "I opened it, saw the train set, and shut it again.", cue="close", word="shut"),
        ],
        page_text="Christmas morning, 1985.",
    ),
    _shot(
        "goodbye", "new_house_porch",
        start={
            "grandpa": _at("porch_door", facing="left"),
            "grandma": _at("porch_swing", facing="left"),
            "kid": _at("walkway", facing="right"),
        },
        actions=[
            _a(0, "time_of_day", value="night", dur=2, id="night"),
            _a(1, "wave", who="kid", target="grandpa", dur=2, id="wave"),
            _a(1.5, "wave", who="grandpa", target="kid", dur=2),
            _a(4, "exit", who="grandpa", to="offstage_left", id="leave"),
            _a(4.5, "exit", who="grandma", to="offstage_left"),
        ],
        camera=[_cam(0, "wide"), _cam(4, "pan_to", target="driveway", dur=3)],
        narration=[
            _line(0, "That night they drove home in the snow.", cue="night", word="night"),
            _line(2, "I waved until I couldn't see the tail lights.", cue="wave", word="waved"),
        ],
    ),
)

# Props across sets: what is left on one set stays there. The compiler keys a prop left on the
# ground offstage at the cut to another set, and a set that comes back shows only what its own
# propStart places again (timeline_sanity.check_props_on_set checks both).
MOVING_DAY = _sp(
    "the_last_boxes_2003", ["dad", "mom"], ["box_a", "box_b", "box_c", "armchair"],
    _shot(
        "old_living_room", "living_room",
        start={"dad": _at("rug", facing="right"), "mom": _at("tree", facing="left")},
        # Two boxes and the armchair stay behind in the old house.
        prop_start={"box_a": {"at": "rug", "state": "closed"}, "box_b": {"at": "hall"},
                    "armchair": {"at": "armchair_spot"}},
        actions=[
            _a(0.5, "pick_up", who="dad", what="box_a", id="lift"),
            _a(3, "walk_to", who="dad", to="front_door", id="carry"),
            _a(3.5, "wave", who="mom", target="dad", dur=2),
            _a(7, "exit", who="dad", to="offstage_left"),
        ],
        camera=[_cam(0, "wide"), _cam(3, "pan_to", target="front_door", dur=3)],
        narration=[
            _line(0, "Dad took the first box out himself.", cue="lift", word="took"),
            _line(3, "Mom stayed behind with the rest of it.", cue="carry", word="stayed"),
        ],
    ),
    _shot(
        "new_porch", "new_house_porch",
        start={"dad": _at("walkway", holding="box_a", facing="right"), "mom": _at("offstage_left", facing="right")},
        prop_start={"box_c": {"at": "porch_spot"}},
        actions=[
            _a(0, "walk_to", who="dad", to="beside_pile"),
            _a(2.5, "place_on", who="dad", what="box_a", on="box_c", socket="top", id="stack"),
            _a(3, "enter", who="mom", frm="offstage_left", to="driveway", id="arrive"),
            _a(7, "hug", who="mom", target="dad", dur=2, id="hug"),
        ],
        camera=[_cam(0, "wide"), _cam(7, "close_up", target="mom", dur=2)],
        narration=[
            _line(0.5, "On Birch Lane he stacked it on the only other box there.", cue="stack", word="stacked"),
            _line(6.5, "Then Mom pulled in and hugged him on the porch.", cue="hug", word="hugged"),
        ],
    ),
    _shot(
        "one_last_look", "living_room",
        start={"mom": _at("front_door", facing="right")},
        # Back in the old house: only the armchair is placed again; the boxes left here are gone.
        prop_start={"armchair": {"at": "armchair_spot"}},
        actions=[
            _a(0, "walk_to", who="mom", to="rug", id="back_in"),
            _a(3.5, "sit_on", who="mom", what="armchair", socket="seat", id="sit"),
        ],
        camera=[_cam(0, "wide"), _cam(3.5, "dolly_to", target="mom", dur=2)],
        narration=[
            _line(0, "She went back in for one last look.", cue="back_in", word="went"),
            _line(3.5, "And sat in the old chair one more time.", cue="sit", word="sat"),
        ],
    ),
)

RAINY_SUNDAY = _sp(
    "rainy_sunday_1961", ["grandpa", "grandma"], ["umbrella", "hymn_books", "box_c", "pew"],
    _shot(
        "the_porch", "new_house_porch",
        start={"grandpa": _at("porch_spot", holding="hymn_books", facing="left"),
               "grandma": _at("porch_door", holding="umbrella", facing="left")},
        # A box waits on the porch; it stays at the house.
        prop_start={"box_c": {"at": "porch_swing"}},
        actions=[
            _a(0, "rain", id="rain"),
            _a(1, "hold", who="grandma", what="umbrella", id="umbrella"),
            _a(2, "walk_to", who="grandpa", to="walkway", id="go"),
            _a(2.5, "walk_to", who="grandma", to="beside_pile"),
            _a(7.5, "exit", who="grandpa", to="offstage_left"),
            _a(8, "exit", who="grandma", to="offstage_left"),
        ],
        camera=[_cam(0, "wide"), _cam(2, "pan_to", target="walkway", dur=3)],
        narration=[
            _line(0, "It rained every Sunday that spring.", cue="rain", word="rained"),
            _line(2, "We walked to church anyway, under Grandma's umbrella.", cue="go", word="walked"),
        ],
    ),
    _shot(
        "the_steps", "church_steps",
        start={"grandpa": _at("sidewalk", holding="hymn_books", facing="right"),
               "grandma": _at("steps_left", holding="umbrella", facing="right")},
        actions=[
            _a(0, "rain"),
            _a(0.5, "walk_to", who="grandpa", to="steps_top", id="climb"),
            # The umbrella is left on the steps when they go in.
            _a(3, "drop", who="grandma", what="umbrella", id="leave_it"),
            _a(5, "walk_to", who="grandma", to="steps_mid"),
        ],
        camera=[_cam(0, "wide"), _cam(3, "dolly_to", target="grandpa", dur=2)],
        narration=[
            _line(0.5, "He took the steps two at a time.", cue="climb", word="took"),
            _line(3, "She left the umbrella dripping at the bottom.", cue="leave_it", word="left"),
        ],
    ),
    _shot(
        "the_pew_inside", "chapel",
        start={"grandpa": _at("aisle_back", holding="hymn_books", facing="right"),
               "grandma": _at("back_door", facing="right")},
        prop_start={"pew": {"at": "pew_spot"}},
        actions=[
            _a(0.5, "sit_on", who="grandpa", what="pew", socket="seat_wall", id="sit"),
            _a(1, "walk_to", who="grandma", to="aisle_mid", id="dry"),
        ],
        camera=[_cam(0, "wide"), _cam(1, "close_up", target="grandpa", dur=2)],
        narration=[
            _line(0.5, "Inside it was warm and dry.", cue="sit", word="warm"),
        ],
    ),
)

# A second telling of the wedding: the multi-teller path through cued narration.
WEDDING["narrations"] = {
    "mom": {
        "aisle": [
            _line(0, "Daddy was supposed to walk me, but he was crying.", source="telling:mom:s1"),
            _line(3, "So I walked it by myself.", cue="down_the_aisle", word="walked", source="telling:mom:s2"),
        ],
        "reception": [
            _line(0, "The bouquet was always going to Grandma June.", cue="bouquet_to_grandma", word="bouquet"),
        ],
    },
}

STORIES: dict[str, dict[str, Any]] = {
    "wedding": WEDDING,
    "first_car": FIRST_CAR,
    "birthday": BIRTHDAY,
    "graduation": GRADUATION,
    "kitchen": KITCHEN,
    "church": CHURCH,
    "moving_house": MOVING_HOUSE,
    "rainy_walk": RAINY_WALK,
    "christmas": CHRISTMAS,
    "moving_day": MOVING_DAY,
    "rainy_sunday": RAINY_SUNDAY,
}
