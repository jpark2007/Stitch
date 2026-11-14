"""Hand-written director drafts for the story corpus (test data, not collected by pytest).

Each builder is what a good director would write for one family story: `def <story>(ctx) ->
dict`, returning camelCase screenplay JSON built only from the real ids in the fake
studio's stage context (agent.fake_studio.FakeStory.screenplay): teller, tellerName, text,
year, place, cast (the teller first, then the participants in the order the run lists
them), names, props ([{id, name, size}] as the family kept them), setId and marks.

The fake set designer makes one set per run, so a multi-shot story plays its shots on that
set with each shot's own marks (`Shot.marks`). Narration is left empty: the fake narrator
writes it (each shot's first line cued to the shot's first named action), so every action a
line may cue has an `id`.

The drafts follow the directing skill (api/agent/skills/directing/SKILL.md) as the real
compiler enforces it: `enter` from offstage before any `walk_to`, floor pick-ups that walk up
and bend by themselves, `place_on` within reach, vehicles boarded with `get_in` (driver first
for `drive_to`; `ride_to` only with someone in the driver socket; nobody gets out while it
moves), effects aimed at someone or something on stage, camera moves from
shared/vocabulary.json with targets on stage, `pageText` on every shot, one explanation per
cast member and prop, and a `pull_tab` that scrubs one physical beat.

Stage geometry (shared/CONTRACTS.md): a mark `[u, h]` stands at x = 2u - 1; walking covers
0.35 stage units a second, running 0.8, driving 0.9.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

__all__ = ["STORY_BUILDERS", "prop_id"]

Ctx = dict[str, Any]
Draft = dict[str, Any]


# --------------------------------------------------------------------------- helpers


def _words(name: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", name.casefold()))


def prop_id(ctx: Ctx, name: str) -> str:
    """The id of the kept prop called `name` (its name, or failing that its id, shares every word).

    Raises KeyError naming the props the run kept, so a story whose prop went missing fails
    with a clear message instead of a compile error about an unknown prop.
    """
    want = _words(name)
    props = ctx.get("props") or []
    for prop in props:
        if want <= _words(str(prop.get("name") or "")):
            return str(prop["id"])
    for prop in props:
        if want <= _words(str(prop["id"]).replace("_", " ")):
            return str(prop["id"])
    raise KeyError(f"no kept prop called {name!r}; the run kept {[(p['id'], p.get('name')) for p in props]}")


def _name(ctx: Ctx, person_id: str) -> str:
    return str((ctx.get("names") or {}).get(person_id) or person_id)


def _a(t: float, do: str, **params: Any) -> dict[str, Any]:
    """An action; `frm` stands for the `from` parameter."""
    if "frm" in params:
        params["from"] = params.pop("frm")
    return {"t": t, "do": do, **params}


def _cam(t: float, move: str, **params: Any) -> dict[str, Any]:
    return {"t": t, "move": move, **params}


def _at(mark: str, **kw: Any) -> dict[str, Any]:
    if "in_" in kw:
        kw["in"] = kw.pop("in_")
    return {"at": mark, **kw}


def _shot(
    sid: str,
    ctx: Ctx,
    *,
    marks: dict[str, list[float]],
    start: dict[str, Any],
    actions: list[dict[str, Any]],
    camera: list[dict[str, Any]],
    page_text: str,
    prop_start: dict[str, Any] | None = None,
    transition: str = "fold",
) -> dict[str, Any]:
    shot: dict[str, Any] = {
        "id": sid,
        "set": ctx["setId"],
        "marks": marks,
        "start": start,
        "camera": camera,
        "actions": actions,
        "narration": [],
        "transition": transition,
        "pageText": page_text,
    }
    if prop_start:
        shot["propStart"] = prop_start
    return shot


def _scrub(shot: str, label: str, t_from: float, t_to: float, targets: list[str], *, ident: str = "pull_beat") -> dict:
    return {
        "id": ident,
        "kind": "pull_tab",
        "label": label,
        "shot": shot,
        "page": "stage",
        "at": [0.86, 0.84],
        "drives": {"type": "scrub", "from": t_from, "to": t_to, "targets": targets},
    }


def _explain(target: str, text: str) -> dict[str, str]:
    return {"target": target, "text": text}


def _screenplay(
    ctx: Ctx,
    cast: list[str],
    props: list[str],
    shots: list[dict[str, Any]],
    interactions: list[dict[str, Any]],
    explanations: list[dict[str, str]],
) -> Draft:
    return {
        "memoryId": "draft",
        "cast": cast,
        "props": props,
        "shots": shots,
        "interactions": interactions,
        "explanations": explanations,
    }


def _people(ctx: Ctx, n: int) -> list[str]:
    cast = list(ctx.get("cast") or [])
    if len(cast) < n:
        raise KeyError(f"the story needs {n} people cast, the run cast {cast}")
    return cast[:n]


# --------------------------------------------------------------------------- stories
#
# Each shot opens with a little unnamed setup (someone walks on, an effect starts) and its
# first named action, the beat the narrator cues, comes 2 to 3.5 s in: late enough that the
# opening line's last word, at 0.25 s a word, lands on it as written. Then nothing moves when
# the narration is compiled, and only the voice pass's real word timings nudge the action,
# so the camera cues stay with the beats they frame.


def two_people_meeting(ctx: Ctx) -> Draft:
    """Teller (he) runs up through the rain to her umbrella at a bus stop; she shares it."""
    he, she = _people(ctx, 2)
    umbrella = prop_id(ctx, "umbrella")
    marks = {"corner": [0.15, 0.0], "beside_stop": [0.56, 0.0], "stop": [0.68, 0.0]}
    s1 = _shot(
        "s1", ctx, marks=marks,
        start={he: _at("offstage_left", facing="right"), she: _at("stop", holding=umbrella, facing="left")},
        actions=[
            _a(0, "rain"),
            # Offstage, he can only enter; then he runs the rest of the way.
            _a(0.5, "enter", who=he, frm="offstage_left", to="corner"),                  # x -1.3 -> -0.7: 1.71 s
            _a(2.4, "run_to", who=he, to="beside_stop", id="run_up"),                  # x -0.7 -> 0.12: 1.03 s
            _a(3.6, "hold", who=she, what=umbrella, id="umbrella_up"),
            _a(3.6, "pose", who=he, pose="slumped", dur=1.0),
        ],
        camera=[_cam(0, "wide"), _cam(2.4, "pan_to", target=he, dur=1.5), _cam(4.0, "close_up", target=she, dur=1.5)],
        page_text=f"Rain poured on the bus stop, and {_name(ctx, he)} ran for the only umbrella there.",
    )
    s2 = _shot(
        "s2", ctx, marks=marks,
        start={he: _at("beside_stop", facing="right"), she: _at("stop", holding=umbrella, facing="left")},
        actions=[
            _a(0, "rain"),
            _a(0.4, "lean_toward", who=she, target=he, dur=1.4),
            _a(2.2, "hand_to", who=she, what=umbrella, to=he, id="share"),           # 0.12 apart in u
            _a(3.4, "hold", who=he, what=umbrella, id="over_us"),
            _a(4.2, "lean_toward", who=she, target=he, dur=2.0, id="close"),
            _a(4.2, "time_of_day", value="dusk", dur=2.0),
        ],
        camera=[_cam(0, "dolly_to", target=umbrella, dur=2.0), _cam(4.2, "close_up", target=she, dur=1.5),
                _cam(6.2, "pull_back", dur=2.0)],
        page_text=f"{_name(ctx, she)} shared her umbrella, and two buses went by while they talked.",
    )
    return _screenplay(
        ctx, [he, she], [umbrella], [s1, s2],
        [_scrub("s2", "Pull to share the umbrella", 2.2, 3.1, [f"prop:{umbrella}", f"puppet:{she}", f"puppet:{he}"])],
        [
            _explain(f"puppet:{he}", f"{_name(ctx, he)} tells this story: he ran through the rain to the bus stop."),
            _explain(f"puppet:{she}", f"{_name(ctx, she)} was the only one at the stop with an umbrella."),
            _explain(f"prop:{umbrella}", "The umbrella she held out over both of them."),
        ],
    )


def car_trip(ctx: Ctx) -> Draft:
    """The teller drives the family's station wagon to the lake; everyone piles out."""
    dad, mom, kid = _people(ctx, 3)
    car = prop_id(ctx, "station wagon")
    basket = prop_id(ctx, "picnic basket")
    drive = {"porch": [0.1, 0.0], "walk": [0.25, 0.0], "car_spot": [0.42, 0.0], "lawn": [0.85, 0.0],
             "down_the_road": [0.88, 0.0]}
    s1 = _shot(
        "s1", ctx, marks=drive,
        start={dad: _at("porch", facing="right"), mom: _at("walk", holding=basket, facing="right"),
               kid: _at("lawn", facing="left")},
        prop_start={car: {"at": "car_spot"}},
        actions=[
            # Everyone gets in (each walks to the door first), the driver last; the car leaves
            # only after the last one has sat down.
            _a(0.5, "get_in", who=mom, what=car, socket="passenger"),
            _a(0.8, "get_in", who=kid, what=car, socket="back_seat"),
            _a(2.8, "get_in", who=dad, what=car, socket="driver", id="dad_in"),
            _a(6.0, "drive_to", who=dad, what=car, to="down_the_road", id="drive_off"),  # x -0.16 -> 0.76: 1.02 s
        ],
        camera=[_cam(0, "wide"), _cam(3.0, "dolly_to", target=car, dur=1.5), _cam(6.0, "pan_to", target="down_the_road", dur=1.5)],
        page_text="Every summer the family drove the old station wagon up to the lake.",
    )
    lake = {"road_in": [0.2, 0.0], "lakeside": [0.55, 0.0], "water": [0.9, 0.0]}
    s2 = _shot(
        "s2", ctx, marks=lake,
        start={dad: _at("road_in", in_=f"{car}.driver"), mom: _at("road_in", in_=f"{car}.passenger", holding=basket),
               kid: _at("road_in", in_=f"{car}.back_seat")},
        prop_start={car: {"at": "road_in"}},
        actions=[
            _a(0.2, "smoke", at=car, dur=1.8),
            # A passenger's ride_to: everyone in the car rides along, the driver at the wheel.
            _a(2.1, "ride_to", who=kid, what=car, to="lakeside", id="pull_in"),       # x -0.6 -> 0.1: 0.78 s
            _a(3.4, "get_out", who=kid, what=car, id="kid_out"),
            _a(4.0, "run_to", who=kid, to="water", id="to_water"),
            _a(4.1, "get_out", who=mom, what=car),
            _a(4.1, "get_out", who=dad, what=car),
            _a(5.5, "pose", who=kid, pose="arms_raised", dur=1.2, id="splash"),
        ],
        camera=[_cam(0, "wide"), _cam(3.4, "close_up", target=kid, dur=1.5), _cam(6.0, "pull_back", dur=2.0)],
        page_text=f"At the water {_name(ctx, kid)} was out of the car before the engine stopped.",
    )
    return _screenplay(
        ctx, [dad, mom, kid], [car, basket], [s1, s2],
        [_scrub("s1", "Pull to drive to the lake", 6.0, 7.0,
                [f"prop:{car}", f"puppet:{dad}", f"puppet:{mom}", f"puppet:{kid}"])],
        [
            _explain(f"puppet:{dad}", f"{_name(ctx, dad)} drove the whole way with the windows down."),
            _explain(f"puppet:{mom}", f"{_name(ctx, mom)} packed the picnic basket for the lake."),
            _explain(f"puppet:{kid}", f"{_name(ctx, kid)} rode in the back and ran straight for the water."),
            _explain(f"prop:{car}", "The family's old station wagon, the car of every summer trip."),
            _explain(f"prop:{basket}", f"The picnic basket {_name(ctx, mom)} packed."),
        ],
    )


def birthday(ctx: Ctx) -> Draft:
    """The teller carries the cake to the picnic table; the child blows out the candles; confetti."""
    mom, kid, dad = _people(ctx, 3)
    cake = prop_id(ctx, "birthday cake")
    table = prop_id(ctx, "picnic table")
    # by_table is within 0.09 in u of the table, close enough to set the cake on its top.
    marks = {"porch": [0.12, 0.0], "by_table": [0.5, 0.0], "table_spot": [0.58, 0.0],
             "kid_close": [0.66, 0.0], "kid_spot": [0.72, 0.0], "dad_spot": [0.86, 0.0]}
    s1 = _shot(
        "s1", ctx, marks=marks,
        start={mom: _at("porch", holding=cake, facing="right"), kid: _at("kid_spot", facing="left"),
               dad: _at("dad_spot", facing="left")},
        prop_start={table: {"at": "table_spot"}},
        actions=[
            _a(0.3, "walk_to", who=mom, to="by_table"),                                # x -0.76 -> 0.0: 2.17 s
            _a(3.0, "place_on", who=mom, what=cake, on=table, socket="top", id="cake_down"),
            _a(4.4, "pose", who=kid, pose="arms_raised", dur=1.0, id="cheer"),
        ],
        camera=[_cam(0, "wide"), _cam(3.0, "dolly_to", target=cake, dur=1.5), _cam(4.6, "pan_to", target=kid, dur=1.5)],
        page_text=f"For {_name(ctx, kid)}'s birthday, {_name(ctx, mom)} carried the cake out to the picnic table.",
    )
    s2 = _shot(
        "s2", ctx, marks=marks,
        start={mom: _at("by_table", facing="right"), kid: _at("kid_spot", facing="left"),
               dad: _at("dad_spot", facing="left")},
        prop_start={table: {"at": "table_spot"}, cake: {"on": f"{table}.top"}},
        actions=[
            _a(0.3, "walk_to", who=kid, to="kid_close"),
            _a(2.6, "lean_toward", who=kid, target=cake, dur=1.5, id="blow"),
            _a(4.4, "confetti", at=kid, dur=2.5, id="confetti"),
            _a(4.4, "pose", who=dad, pose="arms_raised", dur=1.5),
            _a(4.6, "hug", who=mom, target=kid, dur=1.5, id="hug"),
        ],
        camera=[_cam(0, "close_up", target=cake, dur=1.5), _cam(4.4, "pull_back", dur=2.0)],
        page_text=f"{_name(ctx, kid)} blew out every candle, and {_name(ctx, dad)} threw confetti over everyone.",
    )
    return _screenplay(
        ctx, [mom, kid, dad], [cake, table], [s1, s2],
        [_scrub("s2", "Pull to blow out the candles", 2.6, 4.1, [f"puppet:{kid}"])],
        [
            _explain(f"puppet:{mom}", f"{_name(ctx, mom)} tells this story; she carried the cake out herself."),
            _explain(f"puppet:{kid}", f"It was {_name(ctx, kid)}'s birthday."),
            _explain(f"puppet:{dad}", f"{_name(ctx, dad)} threw the confetti."),
            _explain(f"prop:{cake}", "The birthday cake, candles and all."),
            _explain(f"prop:{table}", "The picnic table in the back yard where the cake was set down."),
        ],
    )


def wedding(ctx: Ctx) -> Draft:
    """The teller walks the aisle to him at the altar; confetti on the way out."""
    bride, groom = _people(ctx, 2)
    bouquet = prop_id(ctx, "bouquet")
    s1 = _shot(
        "s1", ctx,
        marks={"aisle": [0.35, 0.0], "altar": [0.62, 0.0], "altar_right": [0.76, 0.0]},
        start={bride: _at("offstage_left", holding=bouquet, facing="right"), groom: _at("altar_right", facing="left")},
        actions=[
            _a(0.3, "enter", who=bride, frm="offstage_left", to="aisle"),                    # x -1.3 -> -0.3: 2.86 s
            _a(3.4, "walk_to", who=bride, to="altar", id="down_the_aisle"),                  # x -0.3 -> 0.24: 1.54 s
            _a(5.2, "hug", who=groom, target=bride, dur=2.0, id="the_hug"),
        ],
        camera=[_cam(0, "wide"), _cam(3.4, "pan_to", target=bride, dur=1.5), _cam(5.2, "close_up", target=groom, dur=1.5)],
        page_text=f"{_name(ctx, bride)} walked down the aisle with her daisies to {_name(ctx, groom)}.",
    )
    s2 = _shot(
        "s2", ctx,
        marks={"steps": [0.45, 0.0], "steps_right": [0.57, 0.0]},
        start={bride: _at("steps", holding=bouquet, facing="right"), groom: _at("steps_right", facing="left")},
        actions=[
            _a(0.3, "pose", who=groom, pose="arms_raised", dur=1.0),
            _a(0.5, "hold", who=bride, what=bouquet),
            _a(2.6, "confetti", at="steps", dur=3.0, id="confetti"),
            _a(2.8, "wave", who=bride, dur=1.5, id="wave"),
        ],
        camera=[_cam(0, "wide"), _cam(2.6, "dolly_to", target=bride, dur=2.0), _cam(5.0, "pull_back", dur=2.0)],
        page_text="Everyone threw confetti on the church steps.",
    )
    return _screenplay(
        ctx, [bride, groom], [bouquet], [s1, s2],
        [_scrub("s1", "Pull to walk down the aisle", 3.4, 4.9, [f"puppet:{bride}", f"prop:{bouquet}"])],
        [
            _explain(f"puppet:{bride}", f"{_name(ctx, bride)} tells this story of her wedding day."),
            _explain(f"puppet:{groom}", f"{_name(ctx, groom)} waited at the altar and forgot every word of his vows."),
            _explain(f"prop:{bouquet}", "The bouquet of daisies she carried down the aisle."),
        ],
    )


def cooking_together(ctx: Ctx) -> Draft:
    """Grandmother and granddaughter at the stove: steam, the wooden spoon handed over, a taste."""
    gran, kid = _people(ctx, 2)
    pot = prop_id(ctx, "soup pot")
    stove = prop_id(ctx, "stove")
    spoon = prop_id(ctx, "wooden spoon")
    marks = {"stove_spot": [0.24, 0.0], "by_stove": [0.36, 0.0], "beside_gran": [0.45, 0.0], "door": [0.86, 0.0]}
    s1 = _shot(
        "s1", ctx, marks=marks,
        start={gran: _at("by_stove", holding=spoon, facing="right"), kid: _at("door", facing="left")},
        prop_start={stove: {"at": "stove_spot"}, pot: {"on": f"{stove}.burner"}},
        actions=[
            _a(0.3, "steam", at=pot, dur=4.0),
            _a(0.5, "walk_to", who=kid, to="beside_gran"),                             # x 0.72 -> -0.1: 2.34 s
            _a(3.2, "hand_to", who=gran, what=spoon, to=kid, id="spoon"),              # 0.09 apart in u
        ],
        camera=[_cam(0, "close_up", target=pot, dur=1.5), _cam(1.6, "wide", dur=1.5), _cam(3.2, "dolly_to", target=kid, dur=1.5)],
        page_text=f"Every Sunday {_name(ctx, kid)} helped {_name(ctx, gran)} make the soup.",
    )
    s2 = _shot(
        "s2", ctx, marks=marks,
        start={gran: _at("by_stove", facing="right"), kid: _at("beside_gran", holding=spoon, facing="left")},
        prop_start={stove: {"at": "stove_spot"}, pot: {"on": f"{stove}.burner"}},
        actions=[
            _a(0, "steam", at=pot, dur=5.0),
            _a(0.4, "point_at", who=gran, target=pot, dur=1.5),
            _a(2.8, "pose", who=kid, pose="reach", dur=1.5, id="stir"),
            _a(5.0, "pose", who=kid, pose="arms_raised", dur=1.0, id="funny_face"),
            _a(5.0, "lean_toward", who=gran, target=kid, dur=1.5, id="laugh"),
        ],
        camera=[_cam(0, "dolly_to", target=kid, dur=1.5), _cam(5.0, "pull_back", dur=2.0)],
        page_text=f"{_name(ctx, kid)} stirred, tasted, and made the funniest face.",
    )
    return _screenplay(
        ctx, [gran, kid], [pot, stove, spoon], [s1, s2],
        [_scrub("s1", "Pull to hand over the spoon", 3.2, 4.1, [f"prop:{spoon}", f"puppet:{gran}", f"puppet:{kid}"])],
        [
            _explain(f"puppet:{gran}", f"{_name(ctx, gran)} tells this story; the soup was her Sunday recipe."),
            _explain(f"puppet:{kid}", f"{_name(ctx, kid)} helped every Sunday."),
            _explain(f"prop:{pot}", "The soup pot, always steaming."),
            _explain(f"prop:{stove}", "The old stove in the kitchen."),
            _explain(f"prop:{spoon}", f"The wooden spoon {_name(ctx, gran)} handed over."),
        ],
    )


def first_bike(ctx: Ctx) -> Draft:
    """The teller runs beside his daughter's first bike ride, lets go, and she rides on alone."""
    dad, kid = _people(ctx, 2)
    bike = prop_id(ctx, "bicycle")
    marks = {"dad_spot": [0.08, 0.0], "kid_spot": [0.16, 0.0], "bike_spot": [0.24, 0.0],
             "mid_path": [0.6, 0.0], "beside_mid": [0.48, 0.0], "pond": [0.9, 0.0]}
    s1 = _shot(
        "s1", ctx, marks=marks,
        start={dad: _at("dad_spot", facing="right"), kid: _at("kid_spot", facing="right")},
        prop_start={bike: {"at": "bike_spot"}},
        actions=[
            # A generated vehicle has a driver seat; the bike's rider sits in it.
            _a(0.3, "get_in", who=kid, what=bike, socket="driver"),
            _a(2.6, "drive_to", who=kid, what=bike, to="mid_path", id="ride"),     # x -0.52 -> 0.2: 0.8 s
            _a(2.6, "run_to", who=dad, to="beside_mid", id="run_beside"),         # x -0.84 -> -0.04: 1.0 s
        ],
        camera=[_cam(0, "wide"), _cam(2.6, "pan_to", target=kid, dur=1.5), _cam(4.2, "close_up", target=dad, dur=1.5)],
        page_text=f"{_name(ctx, dad)} taught {_name(ctx, kid)} to ride her red bicycle in the park.",
    )
    s2 = _shot(
        "s2", ctx, marks=marks,
        start={dad: _at("beside_mid", facing="right"), kid: _at("mid_path", in_=f"{bike}.driver")},
        prop_start={bike: {"at": "mid_path"}},
        actions=[
            _a(0.3, "pose", who=dad, pose="reach", dur=1.5),
            _a(2.4, "pose", who=dad, pose="arms_raised", dur=1.0, id="let_go"),
            _a(2.4, "drive_to", who=kid, what=bike, to="pond", id="ride_off"),     # x 0.2 -> 0.8: 0.67 s
            _a(4.0, "wave", who=dad, target=kid, dur=1.5, id="wave"),
            _a(4.0, "get_out", who=kid, what=bike, id="off_bike"),
            _a(4.6, "pose", who=kid, pose="arms_raised", dur=1.0),
        ],
        camera=[_cam(0, "dolly_to", target=dad, dur=1.5), _cam(2.4, "pan_to", target="pond", dur=1.5),
                _cam(4.6, "pull_back", dur=2.0)],
        page_text=f"He let go, and {_name(ctx, kid)} rode all the way to the pond on her own.",
    )
    return _screenplay(
        ctx, [dad, kid], [bike], [s1, s2],
        [_scrub("s2", "Pull to let go of the bike", 2.4, 3.1, [f"prop:{bike}", f"puppet:{kid}"])],
        [
            _explain(f"puppet:{dad}", f"{_name(ctx, dad)} tells this story; he held the seat and ran beside her."),
            _explain(f"puppet:{kid}", f"{_name(ctx, kid)} learned to ride that day."),
            _explain(f"prop:{bike}", "Her red bicycle, the first she ever rode."),
        ],
    )


def moving_house(ctx: Ctx) -> Draft:
    """Two people carry boxes from the floor up the walk and set them down on the new porch."""
    mom, dad = _people(ctx, 2)
    box = prop_id(ctx, "moving box")
    dishes = prop_id(ctx, "box of dishes")
    walk = {"truck": [0.1, 0.0], "mom_spot": [0.18, 0.0], "box_spot": [0.3, 0.0], "dishes_spot": [0.4, 0.0],
            "porch_left": [0.68, 0.0], "porch": [0.8, 0.0]}
    s1 = _shot(
        "s1", ctx, marks=walk,
        start={dad: _at("offstage_left", facing="right"), mom: _at("mom_spot", facing="right")},
        prop_start={box: {"at": "box_spot"}, dishes: {"at": "dishes_spot"}},
        actions=[
            _a(0.3, "enter", who=dad, frm="offstage_left", to="truck"),                # x -1.3 -> -0.8: 1.43 s
            # Floor pick-ups: each walks up to the box and bends down for it by itself.
            _a(0.5, "pick_up", who=mom, what=dishes),
            _a(3.0, "pick_up", who=dad, what=box, id="lift"),
            _a(3.6, "walk_to", who=mom, to="porch_left"),
            _a(5.6, "walk_to", who=dad, to="porch", id="carry"),
        ],
        camera=[_cam(0, "wide"), _cam(5.6, "pan_to", target=dad, dur=2.0), _cam(8.0, "close_up", target=mom, dur=1.5)],
        page_text=f"Moving day: {_name(ctx, dad)} carried the heaviest box up the walk.",
    )
    porch = {"porch_left": [0.68, 0.0], "porch": [0.8, 0.0], "pile_left": [0.6, 0.0], "pile_right": [0.88, 0.0]}
    s2 = _shot(
        "s2", ctx, marks=porch,
        start={dad: _at("porch", holding=box, facing="right"), mom: _at("porch_left", holding=dishes, facing="left")},
        actions=[
            _a(0.4, "pose", who=mom, pose="slumped", dur=1.2),
            _a(2.6, "place_on", who=dad, what=box, on="pile_right", id="set_down"),
            _a(2.6, "place_on", who=mom, what=dishes, on="pile_left"),
            _a(4.8, "pose", who=mom, pose="sit", dur=2.0, id="sit"),
            _a(4.8, "pose", who=dad, pose="sit", dur=2.0),
        ],
        camera=[_cam(0, "close_up", target=box, dur=1.5), _cam(2.6, "wide", dur=1.5), _cam(4.8, "pull_back", dur=2.0)],
        page_text="They set the boxes down on the new porch and sat on the steps.",
    )
    return _screenplay(
        ctx, [mom, dad], [box, dishes], [s1, s2],
        [_scrub("s1", "Pull to lift the box", 3.0, 5.0, [f"prop:{box}", f"puppet:{dad}"])],
        [
            _explain(f"puppet:{mom}", f"{_name(ctx, mom)} tells this story of moving into the house."),
            _explain(f"puppet:{dad}", f"{_name(ctx, dad)} carried the heaviest box himself."),
            _explain(f"prop:{box}", "The heaviest moving box."),
            _explain(f"prop:{dishes}", f"The box of dishes {_name(ctx, mom)} carried the whole way."),
        ],
    )


def one_person(ctx: Ctx) -> Draft:
    """One person alone in the garden at dusk, watering the roses."""
    (mom,) = _people(ctx, 1)
    can = prop_id(ctx, "watering can")
    s1 = _shot(
        "s1", ctx,
        marks={"shed": [0.15, 0.0], "roses": [0.7, 0.0]},
        start={mom: _at("shed", holding=can, facing="right")},
        actions=[
            _a(0.3, "time_of_day", value="dusk", dur=2.0),
            _a(0.3, "hold", who=mom, what=can),
            _a(2.1, "walk_to", who=mom, to="roses", id="down_the_row"),               # x -0.7 -> 0.4: 3.14 s
            _a(5.5, "pose", who=mom, pose="bow_head", dur=2.0, id="talk"),
        ],
        camera=[_cam(0, "wide"), _cam(2.1, "pan_to", target=mom, dur=1.5), _cam(5.5, "close_up", target=can, dur=1.5),
                _cam(7.8, "pull_back", dur=2.0)],
        page_text=f"Every evening {_name(ctx, mom)} watered the roses and talked to them.",
    )
    return _screenplay(
        ctx, [mom], [can], [s1],
        [_scrub("s1", "Pull to walk down the row", 2.1, 5.2, [f"puppet:{mom}", f"prop:{can}"])],
        [
            _explain(f"puppet:{mom}", f"{_name(ctx, mom)} tells this story; she kept the roses going."),
            _explain(f"prop:{can}", "The old watering can she filled every evening."),
        ],
    )


STORY_BUILDERS: dict[str, Callable[[Ctx], Draft]] = {
    "two_people_meeting": two_people_meeting,
    "car_trip": car_trip,
    "birthday": birthday,
    "wedding": wedding,
    "cooking_together": cooking_together,
    "first_bike": first_bike,
    "moving_house": moving_house,
    "one_person": one_person,
}
