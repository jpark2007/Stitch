"""Real-compiler readiness: the director's examples and FakeCompiler checked against the real one.

The real compiler (`screenplay.compiler.compile`) lands one PR at a time. Every test that needs
it takes the `real` fixture, which skips while `agent.tools.compile.load_compiler()` returns
FakeCompiler, so these switch on by themselves as the compiler lands. Every verb but the effects
has a real handler on main; a case or example the real compiler rejects only because it does not
dispatch an effect verb yet (`unsupported_verb` / `not_an_effect` on confetti, steam, rain, smoke
or time_of_day) skips, naming that gap, and turns into a parity check by itself once it does.
The corpus and the doc snippets are stored here; the fake-only checks at the bottom always run.

Scene inputs are built the way production builds them: the sample family seeded into an
InMemoryRepo (`agent.repo.load_sample_family`) plus a few extra props and a set, looked up with
`agent.wiring.scene_for`.
"""

from __future__ import annotations

import ast
import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

import schemas
from agent.fake_compiler import FakeCompiler
from agent.repo import InMemoryRepo, load_sample_family
from agent.tools.compile import Compiler, SceneInputs, SceneLookupError, load_compiler, parse_and_compile
from agent.wiring import scene_for
from screenplay.schema import CompileResult, Screenplay, Timeline

REPO = Path(__file__).resolve().parents[3]
API = REPO / "api"
DIRECTING = API / "agent" / "skills" / "directing"
DIRECTOR_PROMPT = API / "agent" / "prompts" / "director.md"
SKILL = DIRECTING / "SKILL.md"
SKILL_SAMPLE = DIRECTING / "hymn_books.screenplay.json"
FAMILY = "fam_readiness"

SAMPLE: dict = json.loads(SKILL_SAMPLE.read_text())

# Props and a set the doc examples and the fake_studio drafts use, beside the sample family's.
EXTRA_PROPS = [
    schemas.Prop(
        id="suitcase", family_id=FAMILY, name="suitcase", description="a brown leather suitcase",
        size="carried", art={"body": None}, color="#7a5230", aspect=1.4, anchor=[0.5, 1.0], grip=[0.5, 0.0],
        states={"closed": {}, "open": {}},
    ),
    schemas.Prop(
        id="green_sedan_1985", family_id=FAMILY, name="green sedan", description="a green 1985 sedan",
        size="vehicle", art={"body": None, "wheel": None}, color="#3c6e47", aspect=2.2, anchor=[0.5, 1.0],
        sockets={"driver": [0.6, 0.45], "passenger": [0.4, 0.45]},
        parts={"wheel_front": {"art": "wheel", "at": [0.78, 0.9], "spin": True},
               "wheel_back": {"art": "wheel", "at": [0.22, 0.9], "spin": True}},
    ),
]
EXTRA_SETS = [
    schemas.SetDef(
        id="town_square_1990", family_id=FAMILY, name="town square",
        layers=[schemas.SetLayer(art=None, depth=d, height=h, color="#c9b48a") for d, h in ((0, 1.2), (1, 0.8), (2, 0.3))],
        marks={"left_spot": [0.3, 0], "right_spot": [0.7, 0]},
    ),
]


def make_repo() -> InMemoryRepo:
    repo = InMemoryRepo()
    load_sample_family(repo, FAMILY)
    for prop in EXTRA_PROPS:
        repo.save_prop(FAMILY, prop)
    for set_def in EXTRA_SETS:
        repo.save_set(FAMILY, set_def)
    return repo


def make_lookup(repo: InMemoryRepo) -> Callable[[Screenplay], SceneInputs]:
    """`scene_for` exactly; when an id is missing (a deliberately broken case), the whole family
    instead, so the compiler itself has to report it."""
    lookup = scene_for(repo, FAMILY)

    def scene_of(sp: Screenplay) -> SceneInputs:
        try:
            return lookup(sp)
        except SceneLookupError:
            return SceneInputs(
                sets={s.id: s for s in repo.list_sets(FAMILY)},
                props={p.id: p for p in repo.list_props(FAMILY)},
                people={p.id: p for p in repo.list_people(FAMILY)},
            )

    return scene_of


def compile_text(text: str, compiler: Compiler, lookup: Callable[[Screenplay], SceneInputs]) -> CompileResult:
    return parse_and_compile(text, compiler, lookup)


# ===================================================================== corpus

# The fake_studio director's valid drafts (origin/jonah/agent-agent-fake-studio,
# api/agent/fake_studio.py build_screenplay), frozen for four contexts: two people and a prop, two
# people, one person and a prop on the sample family's church steps, and two people and a prop on
# a generated set (town_square_1990). `bad_screenplay` is the same draft with `to: "nowhere"`.
FAKE_STUDIO_DRAFTS: dict[str, dict] = {'pair_prop_church': {'memoryId': 'draft',
                      'cast': ['dad', 'mom'],
                      'props': ['hymn_books'],
                      'shots': [{'id': 's1',
                                 'set': 'church_steps_1988',
                                 'start': {'dad': {'at': 'steps_left',
                                                   'facing': 'right',
                                                   'holding': 'hymn_books'},
                                           'mom': {'at': 'curb', 'facing': 'left'}},
                                 'camera': [{'t': 0, 'move': 'wide'}],
                                 'actions': [{'t': 0.5,
                                              'do': 'walk_to',
                                              'who': 'dad',
                                              'to': 'meet',
                                              'id': 'walk'},
                                             {'t': 4.11,
                                              'do': 'hand_to',
                                              'who': 'dad',
                                              'what': 'hymn_books',
                                              'to': 'mom',
                                              'id': 'hand_over'},
                                             {'t': 5.31, 'do': 'wave', 'who': 'mom', 'id': 'wave'}],
                                 'narration': [],
                                 'transition': 'fold',
                                 'pageText': 'In 1988, Dad and Mom at the church steps with the hymn books.',
                                 'marks': {'meet': [0.78, 0.0]}}],
                      'interactions': [{'id': 'pull_beat',
                                        'kind': 'pull_tab',
                                        'label': 'Pull to hand over the hymn books',
                                        'shot': 's1',
                                        'page': 'stage',
                                        'at': [0.85, 0.82],
                                        'drives': {'type': 'scrub',
                                                   'from': 4.11,
                                                   'to': 5.01,
                                                   'targets': ['prop:hymn_books', 'puppet:dad']}}],
                      'explanations': [{'target': 'puppet:dad', 'text': 'Dad tells this story.'},
                                       {'target': 'puppet:mom', 'text': "Mom was there, in Dad's telling."},
                                       {'target': 'prop:hymn_books',
                                        'text': "The hymn books from Dad's telling."}]},
 'pair_church': {'memoryId': 'draft',
                 'cast': ['dad', 'mom'],
                 'props': [],
                 'shots': [{'id': 's1',
                            'set': 'church_steps_1988',
                            'start': {'dad': {'at': 'steps_left', 'facing': 'right'},
                                      'mom': {'at': 'curb', 'facing': 'left'}},
                            'camera': [{'t': 0, 'move': 'wide'}],
                            'actions': [{'t': 0.5, 'do': 'walk_to', 'who': 'dad', 'to': 'meet', 'id': 'walk'},
                                        {'t': 4.11, 'do': 'wave', 'who': 'mom', 'id': 'wave'}],
                            'narration': [],
                            'transition': 'fold',
                            'pageText': 'In 1988, Dad and Mom at the church steps.',
                            'marks': {'meet': [0.78, 0.0]}}],
                 'interactions': [{'id': 'pull_beat',
                                   'kind': 'pull_tab',
                                   'label': 'Pull to walk Dad across',
                                   'shot': 's1',
                                   'page': 'stage',
                                   'at': [0.85, 0.82],
                                   'drives': {'type': 'scrub',
                                              'from': 0.5,
                                              'to': 3.81,
                                              'targets': ['puppet:dad']}}],
                 'explanations': [{'target': 'puppet:dad', 'text': 'Dad tells this story.'},
                                  {'target': 'puppet:mom', 'text': "Mom was there, in Dad's telling."}]},
 'solo_prop_church': {'memoryId': 'draft',
                      'cast': ['mom'],
                      'props': ['hymn_books'],
                      'shots': [{'id': 's1',
                                 'set': 'church_steps_1988',
                                 'start': {'mom': {'at': 'steps_left',
                                                   'facing': 'right',
                                                   'holding': 'hymn_books'}},
                                 'camera': [{'t': 0, 'move': 'wide'}],
                                 'actions': [{'t': 0.5,
                                              'do': 'walk_to',
                                              'who': 'mom',
                                              'to': 'curb',
                                              'id': 'walk'},
                                             {'t': 4.8,
                                              'do': 'hold',
                                              'who': 'mom',
                                              'what': 'hymn_books',
                                              'id': 'hold'}],
                                 'narration': [],
                                 'transition': 'fold',
                                 'pageText': 'In 1988, Mom at the church steps with the hymn books.'}],
                      'interactions': [{'id': 'pull_beat',
                                        'kind': 'pull_tab',
                                        'label': 'Pull to walk Mom across',
                                        'shot': 's1',
                                        'page': 'stage',
                                        'at': [0.85, 0.82],
                                        'drives': {'type': 'scrub',
                                                   'from': 0.5,
                                                   'to': 4.5,
                                                   'targets': ['puppet:mom']}}],
                      'explanations': [{'target': 'puppet:mom', 'text': 'Mom tells this story.'},
                                       {'target': 'prop:hymn_books',
                                        'text': "The hymn books from Mom's telling."}]},
 'pair_prop_generated_set': {'memoryId': 'draft',
                             'cast': ['mom', 'dad'],
                             'props': ['hymn_books'],
                             'shots': [{'id': 's1',
                                        'set': 'town_square_1990',
                                        'start': {'mom': {'at': 'left_spot',
                                                          'facing': 'right',
                                                          'holding': 'hymn_books'},
                                                  'dad': {'at': 'right_spot', 'facing': 'left'}},
                                        'camera': [{'t': 0, 'move': 'wide'}],
                                        'actions': [{'t': 0.5,
                                                     'do': 'walk_to',
                                                     'who': 'mom',
                                                     'to': 'meet',
                                                     'id': 'walk'},
                                                    {'t': 2.4,
                                                     'do': 'hand_to',
                                                     'who': 'mom',
                                                     'what': 'hymn_books',
                                                     'to': 'dad',
                                                     'id': 'hand_over'},
                                                    {'t': 3.6, 'do': 'wave', 'who': 'dad', 'id': 'wave'}],
                                        'narration': [],
                                        'transition': 'fold',
                                        'pageText': 'In 1990, Mom and Dad at the town square with the hymn '
                                                    'books.',
                                        'marks': {'meet': [0.58, 0.0]}}],
                             'interactions': [{'id': 'pull_beat',
                                               'kind': 'pull_tab',
                                               'label': 'Pull to hand over the hymn books',
                                               'shot': 's1',
                                               'page': 'stage',
                                               'at': [0.85, 0.82],
                                               'drives': {'type': 'scrub',
                                                          'from': 2.4,
                                                          'to': 3.3,
                                                          'targets': ['prop:hymn_books', 'puppet:mom']}}],
                             'explanations': [{'target': 'puppet:mom', 'text': 'Mom tells this story.'},
                                              {'target': 'puppet:dad',
                                               'text': "Dad was there, in Mom's telling."},
                                              {'target': 'prop:hymn_books',
                                               'text': "The hymn books from Mom's telling."}]}}


def _sample(edit: Callable[[dict], None] | None = None) -> dict:
    data = copy.deepcopy(SAMPLE)
    if edit is not None:
        edit(data)
    return data


def _shot(data: dict, i: int = 0) -> dict:
    return data["shots"][i]


def _actions(data: dict, i: int = 0) -> list[dict]:
    return data["shots"][i]["actions"]


def _base(actions: list[dict], start: dict | None = None, **shot: object) -> dict:
    """One shot on the church steps: Dad at steps_foot, Mom on steps_top holding the hymn books."""
    return {
        "memoryId": "m1",
        "cast": ["dad", "mom"],
        "props": ["hymn_books"],
        "shots": [{
            "id": "s1",
            "set": "church_steps_1988",
            "start": start or {
                "dad": {"at": "steps_foot", "facing": "right"},
                "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"},
            },
            "camera": [{"t": 0, "move": "wide"}],
            "actions": actions,
            "pageText": "A test page.",
            **shot,
        }],
    }


def _two_shots(data: dict) -> None:
    data["shots"].append({
        "id": "s2",
        "set": "church_steps_1988",
        "start": {"dad": {"at": "steps_foot", "facing": "right"},
                  "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"}},
        "camera": [{"t": 0, "move": "wide"}, {"t": 3, "move": "pull_back", "dur": 2}],
        "actions": [{"t": 0.5, "who": "mom", "do": "walk_to", "to": "curb"},
                    {"t": 3, "who": "dad", "do": "wave", "target": "mom"}],
        "transition": "fold",
        "pageText": "Mom carried the hymn books home.",
    })


def _sedan(actions: list[dict], start: dict | None = None, **extra: object) -> dict:
    data = _base(actions, start=start or {"dad": {"at": "steps_left", "facing": "right"}}, **extra)
    data["cast"] = ["dad"]
    data["props"] = ["green_sedan_1985"]
    data["shots"][0]["propStart"] = {"green_sedan_1985": {"at": "steps_foot"}}
    return data


def _suitcase(actions: list[dict], start: dict | None = None, **extra: object) -> dict:
    """Mom at the foot of the steps, the suitcase up on steps_top: its handle at her hand height
    (open and close do not bend down, so a suitcase on the floor at her feet is out of reach)."""
    data = _base(actions, start=start or {"mom": {"at": "by_steps", "facing": "right"}},
                 marks={"by_steps": [0.53, 0.0]}, **extra)
    data["cast"] = ["mom"]
    data["props"] = ["suitcase"]
    data["shots"][0]["propStart"] = {"suitcase": {"at": "steps_top", "state": "closed"}}
    return data


def _carried_suitcase(actions: list[dict]) -> dict:
    """Mom at the foot of the steps carrying the suitcase (no propStart)."""
    data = _suitcase(actions, start={"mom": {"at": "by_steps", "holding": "suitcase", "facing": "right"}})
    del data["shots"][0]["propStart"]
    return data


def _with_confetti(data: dict) -> None:
    _actions(data).append({"t": 8.5, "do": "confetti", "at": "steps_top", "dur": 2})


@dataclass(frozen=True)
class Case:
    screenplay: dict
    ok: bool  # what the real compiler should say (and the fake must agree)
    codes: tuple[str, ...] = ()  # error codes expected from the fake, in order (not-ok cases)


def _corpus() -> dict[str, Case]:
    c: dict[str, Case] = {
        # ------------------------------------------------------------ valid
        "hymn_books": Case(_sample(), True),
        "hymn_books_without_second_telling": Case(_sample(lambda d: d.pop("narrations")), True),
        "two_shots_carry_holding": Case(_sample(_two_shots), True),
        "pick_up_walks_up_first": Case(_base(
            [{"t": 0, "who": "mom", "do": "drop", "what": "hymn_books"},
             {"t": 0.5, "who": "dad", "do": "walk_to", "to": "steps_left"},
             {"t": 3, "who": "dad", "do": "pick_up", "what": "hymn_books"},
             {"t": 6, "who": "dad", "do": "hold", "what": "hymn_books"}]), True),
        "hug_walks_up_first": Case(_base(
            [{"t": 0, "who": "mom", "do": "walk_to", "to": "curb"},
             {"t": 3, "who": "dad", "do": "hug", "target": "mom"}]), True),
        "body_verbs": Case(_base(
            [{"t": 0, "who": "dad", "do": "wave", "target": "mom"},
             {"t": 0, "who": "mom", "do": "pose", "pose": "arms_raised", "dur": 1},
             {"t": 1.5, "who": "dad", "do": "point_at", "target": "curb"},
             {"t": 3, "who": "dad", "do": "kneel", "dur": 1},
             {"t": 3, "who": "mom", "do": "lean_toward", "target": "dad"},
             {"t": 5, "who": "mom", "do": "pose", "pose": "bow_head"}]), True),
        "walk_off_turn_and_follow": Case(_base(
            [{"t": 0, "who": "dad", "do": "turn_to", "target": "curb"},
             {"t": 0.5, "who": "mom", "do": "walk_to", "to": "curb"},
             {"t": 0.5, "who": "dad", "do": "follow", "target": "mom"},
             {"t": 6, "who": "mom", "do": "exit", "to": "offstage_right"}]), True),
        "suitcase_opens": Case(_suitcase(
            [{"t": 0.5, "who": "mom", "do": "open", "what": "suitcase", "id": "open_case"}],
            narration=[]), True),
        # the directing skill's fix for opening something carried: set it down at hand height first
        "place_on_then_open": Case(_carried_suitcase(
            [{"t": 0.5, "who": "mom", "do": "place_on", "what": "suitcase", "on": "steps_top"},
             {"t": 1.3, "who": "mom", "do": "open", "what": "suitcase"},
             {"t": 3, "who": "mom", "do": "close", "what": "suitcase"},
             {"t": 4, "who": "mom", "do": "pick_up", "what": "suitcase"}]), True),
        # the real follow reads the leader's root track, where kneeling lowers the root
        "follow_a_kneeling_leader": Case(_base(
            [{"t": 4, "who": "mom", "do": "kneel"},
             {"t": 4, "who": "dad", "do": "follow", "target": "mom"}]), True),
        "sedan_drives_off": Case(_sedan(
            [{"t": 0, "who": "dad", "do": "get_in", "what": "green_sedan_1985"},
             {"t": 3, "who": "dad", "do": "drive_to", "what": "green_sedan_1985", "to": "curb"}]), True),
        **{f"fake_studio_{name}": Case(copy.deepcopy(draft), True) for name, draft in FAKE_STUDIO_DRAFTS.items()},
        # ----------------------------------------------------------- broken
        "fake_studio_bad_screenplay": Case(
            _bad_fake_studio(FAKE_STUDIO_DRAFTS["pair_prop_church"]), False, ("unknown_mark",)),
        "unknown_verb": Case(_sample(lambda d: _actions(d).append({"t": 8, "who": "mom", "do": "juggle"})),
                             False, ("invalid_screenplay",)),
        "unknown_mark": Case(_sample(lambda d: _actions(d)[2].update(to="altar")), False, ("unknown_mark",)),
        "unknown_prop_id": Case(_sample(lambda d: _actions(d)[3].update(what="umbrella")), False, ("unknown_prop",)),
        "unknown_prop_listed": Case(_sample(lambda d: (d["props"].append("umbrella"),
                                                       _actions(d)[3].update(what="umbrella"))),
                                    False, ("unknown_prop",)),
        # listed in props, used by nothing, missing from the scene inputs: still unknown_prop
        "unused_prop_missing_from_the_scene": Case(_sample(lambda d: d["props"].append("umbrella")),
                                                   False, ("unknown_prop",)),
        "hand_to_receiver_out_of_reach": Case(_base(
            [{"t": 1, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"}],
            start={"dad": {"at": "steps_left", "facing": "right"},
                   "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"}}),
            False, ("out_of_reach",)),
        # dad's pick_up starts while he is still walking (until 5.31), so he never gets the books
        "overlapping_actions_one_actor": Case(_sample(lambda d: _actions(d)[3].update(t=4.0)), False,
                                              ("busy", "not_holding")),
        # moving the "dropped" line later drags the drop and the uncued actions after it along
        "cue_shift_runs_into_next_action": Case(
            _sample(lambda d: _shot(d)["narration"][1].update(t=3.25)), False, ("busy",)),
        "hand_to_taker_still_walking": Case(_base(
            [{"t": 0, "who": "dad", "do": "walk_to", "to": "curb"},
             {"t": 0.5, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"}]), False, ("busy",)),
        "missing_set": Case(_sample(lambda d: _shot(d).update(set="the_moon")), False, ("unknown_set",)),
        "walk_on_without_enter": Case(_base(
            [{"t": 0, "who": "dad", "do": "walk_to", "to": "steps_left"}],
            start={"dad": {"at": "offstage_left", "facing": "right"},
                   "mom": {"at": "steps_top", "holding": "hymn_books", "facing": "left"}}),
            False, ("offstage",)),
        "enter_while_on_stage": Case(_base([{"t": 0, "who": "dad", "do": "enter", "to": "steps_left"}]),
                                     False, ("already_on_stage",)),
        "drop_not_holding": Case(_base([{"t": 0, "who": "dad", "do": "drop", "what": "hymn_books"}]),
                                 False, ("not_holding",)),
        "pick_up_held_prop": Case(_base([{"t": 0, "who": "dad", "do": "pick_up", "what": "hymn_books"}]),
                                  False, ("already_held",)),
        "hand_to_twice": Case(_base(
            [{"t": 0, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"},
             {"t": 2, "who": "mom", "do": "hand_to", "what": "hymn_books", "to": "dad"}]), False, ("not_holding",)),
        "unknown_cue": Case(_sample(lambda d: _shot(d)["narration"][1].update(cue="fumble")),
                            False, ("cue_unknown_action",)),
        "cue_word_not_in_line": Case(_sample(lambda d: _shot(d)["narration"][1].update(cueWord="tumbled")),
                                     False, ("cue_word_missing",)),
        "explanation_unknown_target": Case(
            _sample(lambda d: d["explanations"].append({"target": "prop:umbrella", "text": "An umbrella."})),
            False, ("explanation_unknown_target",)),
        "drive_without_driver": Case(_sedan(
            [{"t": 0, "who": "dad", "do": "drive_to", "what": "green_sedan_1985", "to": "curb"}]),
            False, ("not_driver",)),
        # open/close never reach a held prop: the free arm comes up short of the holding hand
        "open_held_prop": Case(_base([{"t": 0.5, "who": "mom", "do": "open", "what": "hymn_books"}]),
                               False, ("out_of_reach",)),
        "close_held_prop_tall": Case(_base(
            [{"t": 0.5, "who": "dad", "do": "close", "what": "hymn_books"}],
            start={"dad": {"at": "steps_foot", "holding": "hymn_books", "facing": "right"},
                   "mom": {"at": "steps_top", "facing": "left"}}), False, ("out_of_reach",)),
        "open_carried_suitcase": Case(_carried_suitcase([{"t": 0.5, "who": "mom", "do": "open", "what": "suitcase"}]),
                                      False, ("out_of_reach",)),
        "hold_then_open": Case(_base(
            [{"t": 0.5, "who": "mom", "do": "hold", "what": "hymn_books"},
             {"t": 2, "who": "mom", "do": "open", "what": "hymn_books"}]), False, ("out_of_reach",)),
        # kneeling lowers the shoulders: the suitcase handle up on the step is out of reach
        "kneel_then_open": Case(_suitcase(
            [{"t": 0, "who": "mom", "do": "kneel"},
             {"t": 2, "who": "mom", "do": "open", "what": "suitcase"}]), False, ("out_of_reach",)),
        # steps_foot to steps_top is exactly HUG_GAP + HUG_SLACK in x; rounded roots make it a walk
        "kneeling_hug_needs_a_walk": Case(_base(
            [{"t": 0, "who": "mom", "do": "kneel"},
             {"t": 3, "who": "mom", "do": "hug", "target": "dad"}]), False, ("not_standing",)),
        "pick_up_above_the_shoulder": Case(_base(
            [{"t": 0.5, "who": "dad", "do": "pick_up", "what": "hymn_books"}],
            start={"dad": {"at": "steps_foot", "facing": "right"}, "mom": {"at": "curb", "facing": "left"}},
            marks={"shelf": [0.0, 0.9]}, propStart={"hymn_books": {"at": "shelf"}}), False, ("out_of_reach",)),
        "open_out_of_reach": Case(_suitcase(
            [{"t": 0, "who": "mom", "do": "open", "what": "suitcase"}],
            start={"mom": {"at": "steps_left", "facing": "right"}}), False, ("out_of_reach",)),
        # ---------------------------------------------------------- effects
        # shared/CONTRACTS.md and the vocabulary allow effects, so the fake accepts them; the real
        # compiler skips this case (EFFECTS_GAP) until compile() dispatches effect verbs.
        "effect_confetti": Case(_sample(_with_confetti), True),
    }
    return c


def _bad_fake_studio(draft: dict) -> dict:
    """fake_studio's bad_screenplay: the valid draft with the first action sent to "nowhere"."""
    bad = copy.deepcopy(draft)
    bad["shots"][0]["actions"][0]["to"] = "nowhere"
    return bad


# ================================================================ doc snippets

_FENCE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)
_INLINE = re.compile(r"`(\{.*?\})`")


def doc_snippets() -> list[tuple[str, dict]]:
    """Every JSON object written as an example in director.md and directing/SKILL.md, as
    (where, object). Templates with `<placeholders>` are skipped: they are not examples."""
    out: list[tuple[str, dict]] = []
    for path in (DIRECTOR_PROMPT, SKILL):
        text = path.read_text()
        found = [m.group(1) for m in _FENCE.finditer(text)] + [m.group(1) for m in _INLINE.finditer(text)]
        for raw in found:
            if "<" in raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append((f"{path.name}: {raw[:60]}", obj))
    return out


def screenplay_for_snippet(obj: dict) -> dict:
    """A whole screenplay that uses the snippet the way the docs describe it."""
    if "type" in obj:  # an interaction's drives
        prop = obj.get("prop")
        if prop == "suitcase":
            data = _suitcase([{"t": 0.5, "who": "mom", "do": "open", "what": "suitcase"}])
        elif prop == "green_sedan_1985":
            data = _sedan([{"t": 0, "who": "dad", "do": "get_in", "what": "green_sedan_1985"},
                           {"t": 3, "who": "dad", "do": "drive_to", "what": "green_sedan_1985", "to": "curb"}])
        else:
            data = _sample()
        data["interactions"] = [{"id": "tab", "kind": "pull_tab", "label": "Pull", "shot": "s1", "page": "stage",
                                 "at": [0.85, 0.82], "drives": obj}]
        return data
    if "drives" in obj:  # a whole interaction
        data = _sample()
        data["interactions"] = [obj]
        return data
    if set(obj) == {"target", "text"}:  # an explanation
        data = _sample()
        data["explanations"] = [e for e in data["explanations"] if e["target"] != obj["target"]] + [obj]
        return data
    if "narrations" in obj:
        data = _sample()
        data["narrations"] = obj["narrations"]
        return data
    if "do" in obj or "move" in obj:  # an action or camera cue from the worked example
        data = _sample()
        key = "actions" if "do" in obj else "camera"
        assert obj in _shot(data)[key], f"{obj} is not in the worked example"
        return data
    raise AssertionError(f"no rule to compile doc snippet {obj}")


# ================================================================== fixtures

CORPUS: dict[str, Case] = _corpus()


@pytest.fixture(scope="module")
def real() -> Compiler:
    compiler = load_compiler()
    if isinstance(compiler, FakeCompiler):
        pytest.skip(
            "screenplay.compiler.compile does not exist on this branch yet (load_compiler() returns "
            "FakeCompiler); these tests switch on when the compiler lands"
        )
    return compiler


@pytest.fixture(scope="module")
def lookup() -> Callable[[Screenplay], SceneInputs]:
    return make_lookup(make_repo())


# ============================================================ real compiler


def test_skill_sample_compiles_cleanly_and_its_timeline_validates(real, lookup):
    res = compile_text(SKILL_SAMPLE.read_text(), real, lookup)
    assert res.ok and res.errors == [], [e.message for e in res.errors]
    tl = Timeline.model_validate(res.timeline.model_dump(mode="json", by_alias=True))
    sp = Screenplay.model_validate(SAMPLE)
    # pageText survives into the timeline, shot for shot (TimelineShot.pageText)
    assert [(s.id, s.page_text) for s in tl.shots] == [(s.id, s.page_text) for s in sp.shots]
    assert all(s.page_text for s in tl.shots)
    assert tl.explanations == sp.explanations
    assert [i.id for i in tl.interactions] == [i.id for i in sp.interactions]
    assert set(tl.narrations) == {"dad", "mom"} and tl.default_narrator == "dad"


@pytest.mark.parametrize("where, snippet", doc_snippets(), ids=[w for w, _ in doc_snippets()])
def test_doc_snippets_compile_cleanly(real, lookup, where, snippet):
    res = compile_text(json.dumps(screenplay_for_snippet(snippet)), real, lookup)
    _skip_effects_gap(res)
    assert res.ok and res.errors == [], (where, [e.message for e in res.errors])


EFFECT_VERBS = frozenset(json.loads((REPO / "shared" / "vocabulary.json").read_text())["effects"])
EFFECTS_GAP = "screenplay.compiler.compile does not dispatch effect verbs yet"
EFFECTS_GAP_CODES = {"unsupported_verb", "not_an_effect"}


def _skip_effects_gap(res: CompileResult) -> None:
    """The one known compiler-side gap: screenplay/effects.py exists, but compile() emits
    `effects=[]` and has no handler for an effect verb. Skip, naming the gap, only while the real
    compiler says so for an effect verb; any other unsupported_verb is a real failure."""
    verbs = sorted({e.do for e in res.errors if e.code in EFFECTS_GAP_CODES and e.do in EFFECT_VERBS})
    if verbs:
        pytest.skip(f"{EFFECTS_GAP} ({', '.join(verbs)}: unsupported_verb)")


def test_effects_gap_skip_is_narrow():
    """The dynamic skip fires on an effect verb only, never on a verb the compiler should run."""
    from screenplay.schema import CompileError

    gap = CompileResult(ok=False, errors=[CompileError(code="unsupported_verb", message="m", do="confetti")])
    with pytest.raises(pytest.skip.Exception, match=re.escape(EFFECTS_GAP)):
        _skip_effects_gap(gap)
    for code, do in (("unsupported_verb", "follow"), ("busy", "confetti"), ("unsupported_verb", None)):
        _skip_effects_gap(CompileResult(ok=False, errors=[CompileError(code=code, message="m", do=do)]))


@pytest.mark.parametrize("name", list(CORPUS))
def test_fake_and_real_agree_on_ok_versus_not_ok(real, lookup, name):
    case = CORPUS[name]
    text = json.dumps(case.screenplay)
    got_real = compile_text(text, real, lookup)
    got_fake = compile_text(text, FakeCompiler(), lookup)
    real_msgs = [f"{e.code}: {e.message}" for e in got_real.errors]
    fake_msgs = [f"{e.code}: {e.message}" for e in got_fake.errors]
    _skip_effects_gap(got_real)
    assert got_real.ok == case.ok, (name, real_msgs)
    assert got_fake.ok == got_real.ok, (name, {"real": real_msgs, "fake": fake_msgs})
    if not got_real.ok:
        # Asserted for this stored corpus only: on an arbitrary screenplay that both reject, the
        # fake can report an extra code (see "Positions are logical" in the FakeCompiler docstring).
        # the fake's codes are ones the real compiler reported here ...
        assert {e.code for e in got_fake.errors} <= {e.code for e in got_real.errors}, (name, real_msgs, fake_msgs)
        # ... at the same places, in the same path style ("screenplay", "shot s1", "shot s1, t=5",
        # "shot s1, dad's narration line 1")
        heads = lambda res: {e.message.split(": ", 1)[0] for e in res.errors}  # noqa: E731
        assert heads(got_fake) <= heads(got_real), (name, real_msgs, fake_msgs)
        assert [(e.shot, e.t, e.who, e.do) for e in got_fake.errors] == [
            (e.shot, e.t, e.who, e.do) for e in got_real.errors], (name, real_msgs, fake_msgs)


# Codes the agent's compile tool adds around any compiler (agent/tools/compile.py), not the fake's.
AGENT_CODES = {"invalid_json", "invalid_screenplay", "missing_file", "compiler_crash", "no_timeline",
               "unknown_reference"}


_ERROR_CALLS = {"StateError", "NarrationError", "CompileError", "error", "_error", "add"}


def _codes_by_function(path: Path) -> dict[str, set[str]]:
    """String error codes in a module, by the top-level function or method they appear in
    ("" for module level): the code argument of StateError / NarrationError / CompileError /
    the error helpers / validate's `add(site, code, reason)`."""
    out: dict[str, set[str]] = {}

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and isinstance(node, (ast.Module, ast.ClassDef)):
                name = child.name  # a top-level function or a method; nested functions count for it
            if isinstance(child, ast.Call):
                func = child.func
                fname = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
                if fname in _ERROR_CALLS:
                    for arg in [*child.args[:2], *(kw.value for kw in child.keywords if kw.arg == "code")]:
                        if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                                and re.fullmatch(r"[a-z][a-z_]+", arg.value)):
                            out.setdefault(name, set()).add(arg.value)
            visit(child, name)

    visit(ast.parse(path.read_text()), "")
    return out


def _error_codes_in(path: Path) -> set[str]:
    return set().union(*_codes_by_function(path).values())


def real_compiler_codes() -> set[str]:
    return set().union(*(_error_codes_in(p) for p in sorted((API / "screenplay").glob("*.py"))))


def fake_compiler_codes(skip_verbs: set[str] = frozenset()) -> set[str]:
    """Every code FakeCompiler can emit: its own, plus those of the real modules it calls
    (validate.py, state.py, narration.py). Codes found only in the fake's handlers for
    `skip_verbs` (`do_<verb>`) are left out."""
    by_fn = _codes_by_function(API / "agent" / "fake_compiler.py")
    skipped = {f"do_{v}" for v in skip_verbs}
    own = set().union(*(codes for fn, codes in by_fn.items() if fn not in skipped))
    shared = set().union(*(_error_codes_in(API / "screenplay" / f"{m}.py") for m in ("validate", "state", "narration")))
    return own | shared


def test_every_fake_error_code_is_a_real_compiler_code(real):
    import screenplay.compiler as real_module

    real_codes = real_compiler_codes()
    assert {"unknown_mark", "busy", "out_of_reach", "not_holding"} <= real_codes  # the scan works
    # Verbs the real compiler cannot run yet (unsupported_verb) cannot raise their own codes there.
    missing = set(json.loads((REPO / "shared" / "vocabulary.json").read_text())["actions"]) - set(real_module.HANDLERS)
    # shared/CONTRACTS.md defines no error codes of its own; the real compiler's set is the reference.
    assert fake_compiler_codes(missing) - real_codes == set(), f"real compiler has no handler yet for {sorted(missing)}"


# ================================================================ fake only (runs on main)


def test_doc_examples_are_json_or_templates():
    """Every `{...}` example in the docs is either real JSON (compiled above) or a template with
    <placeholders> / schema notation, so no example escapes the compile check by a typo."""
    assert len(doc_snippets()) >= 10
    for path in (DIRECTOR_PROMPT, SKILL):
        text = path.read_text()
        for raw in [m.group(1) for m in _FENCE.finditer(text)] + [m.group(1) for m in _INLINE.finditer(text)]:
            try:
                json.loads(raw)
            except json.JSONDecodeError:
                assert "<" in raw or '"?' in raw, f"{path.name}: example is neither JSON nor a template: {raw}"


@pytest.mark.parametrize("where, snippet", doc_snippets(), ids=[w for w, _ in doc_snippets()])
def test_doc_snippets_compile_with_the_fake(lookup, where, snippet):
    res = compile_text(json.dumps(screenplay_for_snippet(snippet)), FakeCompiler(), lookup)
    assert res.ok, (where, [e.message for e in res.errors])


@pytest.mark.parametrize("name", list(CORPUS))
def test_corpus_fake_verdict(lookup, name):
    """The fake's own answer for every corpus case, effects included (no real compiler needed)."""
    case = CORPUS[name]
    res = compile_text(json.dumps(case.screenplay), FakeCompiler(), lookup)
    msgs = [f"{e.code}: {e.message}" for e in res.errors]
    assert res.ok == case.ok, msgs
    if case.ok:
        Timeline.model_validate(res.timeline.model_dump(mode="json", by_alias=True))
    else:
        assert tuple(e.code for e in res.errors) == case.codes, msgs
        assert {e.code for e in res.errors} <= fake_compiler_codes() | AGENT_CODES, msgs


def test_corpus_covers_the_required_breakages():
    required = {"unknown_verb", "unknown_mark", "unknown_prop_id", "hand_to_receiver_out_of_reach",
                "overlapping_actions_one_actor", "missing_set", "fake_studio_bad_screenplay"}
    assert required <= set(CORPUS)
    assert {"hymn_books", *(f"fake_studio_{n}" for n in FAKE_STUDIO_DRAFTS)} <= {n for n, c in CORPUS.items() if c.ok}


def _live_fake_studio_drafts() -> list[tuple[str, dict, dict]]:
    """(name, valid draft, bad draft) from agent/fake_studio.py itself, for the frozen contexts."""
    fake_studio = pytest.importorskip("agent.fake_studio", reason="agent/fake_studio.py is not on this branch yet")
    repo = make_repo()
    out = []
    for name, draft in FAKE_STUDIO_DRAFTS.items():
        shot = draft["shots"][0]
        ctx = {
            "teller": draft["cast"][0], "cast": draft["cast"], "setId": shot["set"],
            "props": [{"id": p, "name": p.replace("_", " ")} for p in draft["props"]],
            "marks": dict(repo.get_set(FAMILY, shot["set"]).marks),
        }
        out.append((name, fake_studio.build_screenplay(ctx), fake_studio.bad_screenplay(ctx)))
    return out


def _check_live_drafts(compiler: Compiler, lookup) -> None:
    for name, good, bad in _live_fake_studio_drafts():
        res = compile_text(json.dumps(good), compiler, lookup)
        assert res.ok, (name, [e.message for e in res.errors])
        res = compile_text(json.dumps(bad), compiler, lookup)
        assert not res.ok and [e.code for e in res.errors] == ["unknown_mark"], name
        assert "nowhere" in res.errors[0].message


def test_live_fake_studio_drafts_with_the_fake(lookup):
    """When agent/fake_studio.py is on this branch: its valid drafts compile, bad_screenplay fails."""
    _check_live_drafts(FakeCompiler(), lookup)


def test_live_fake_studio_drafts_with_the_real_compiler(real, lookup):
    _check_live_drafts(real, lookup)
