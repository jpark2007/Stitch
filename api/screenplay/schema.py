"""Screenplay and timeline models (ARCHITECTURE.md section 3, DESIGN.md contract additions).

The screenplay is what the director agent writes. The timeline is what the compiler
outputs and the stage plays. Conventions (axes, units, channel grammar, interpolation)
are in shared/CONTRACTS.md. JSON is camelCase; Python attributes are snake_case.

Contract file: additive changes only, and post INTERFACE: on Issue #1 for each one.
web/lib/types.ts is generated from these models by shared/gen_types.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"
VOCABULARY = json.loads((SHARED_DIR / "vocabulary.json").read_text())

ActionVerb = Literal[tuple(VOCABULARY["actions"])]  # type: ignore[valid-type]
CameraMove = Literal[tuple(VOCABULARY["camera"])]  # type: ignore[valid-type]
EffectKind = Literal[tuple(VOCABULARY["effects"])]  # type: ignore[valid-type]
Transition = Literal[tuple(VOCABULARY["transitions"])]  # type: ignore[valid-type]
InteractionKind = Literal[tuple(VOCABULARY["interactionKinds"])]  # type: ignore[valid-type]


class Model(BaseModel):
    """Base for every contract model: camelCase JSON, unknown fields rejected."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=to_camel,
        serialize_by_alias=True,
    )


# A mark is [u, h] or [u, h, d]: u = fraction across the stage (0 left, 1 right),
# h = height above the ground in stage units, d = optional depth (stage z).
Mark = list[float]


# --------------------------------------------------------------------------- shared

class WordTiming(Model):
    t: float = Field(description="Seconds from the start of the timeline")
    word: str


class NarrationLine(Model):
    t: float = Field(description="Screenplay: seconds from shot start. Timeline: seconds from timeline start.")
    text: str
    source: str | None = Field(default=None, description='Where the words come from, e.g. "telling:dad:s2"')
    cue: str | None = Field(
        default=None,
        description="Action this line is timed to: an action id, else the first action in the shot whose `do` equals it",
    )
    cue_word: str | None = Field(default=None, description="The word the cued action lands on (default: the last word)")
    words: list[WordTiming] | None = Field(default=None, description="Word timestamps from TTS, timeline time")


class InteractionDrive(Model):
    """What a pull tab or slider controls. Exactly one style, chosen by `type`."""

    type: Literal["scrub", "prop_state", "prop_part"]
    # scrub: dragging the tab scrubs timeline time between `from` and `to` for `targets`
    from_: float | None = Field(default=None, alias="from")
    to: float | None = None
    targets: list[str] | None = Field(default=None, description='Track targets to scrub, e.g. ["prop:hymn_books"]; default all')
    # prop_state: toggles or steps through states
    prop: str | None = None
    states: list[str] | None = None
    # prop_part: drives one moving part across a range
    part: str | None = None
    channel: Literal["rot", "offset"] | None = None
    range: list[float] | None = Field(default=None, description="[min, max] for the part channel")


class Interaction(Model):
    id: str
    kind: InteractionKind
    label: str = Field(description='Printed next to the tab, e.g. "Pull to drive the car"')
    shot: str | None = Field(default=None, description="Shot id it belongs to; default every shot")
    page: Literal["stage", "story"] = "stage"
    at: list[float] = Field(description="[u, v] position on that page, fractions from top-left")
    drives: InteractionDrive


class Explanation(Model):
    """Click-to-explain text for one cast member or prop."""

    target: str = Field(description='"puppet:<personId>" or "prop:<propId>"')
    text: str


# ----------------------------------------------------------------------- screenplay

class StartState(Model):
    at: str = Field(description="Mark name, or offstage_left / offstage_right")
    holding: str | None = Field(default=None, description="Prop id held at shot start")
    pose: str | None = None
    facing: Literal["left", "right"] | None = None
    in_: str | None = Field(default=None, alias="in", description='Seated in "<propId>.<socket>"')


class PropStart(Model):
    at: str | None = Field(default=None, description="Mark where the prop stands at shot start")
    state: str | None = None
    on: str | None = Field(default=None, description='Resting on "<propId>.<socket>"')


class CameraCue(Model):
    t: float
    move: CameraMove
    target: str | None = Field(default=None, description="Person id, prop id or mark")
    dur: float | None = None


class Action(Model):
    t: float = Field(description="Seconds from shot start")
    do: ActionVerb
    id: str | None = Field(default=None, description="Optional name so narration cues can point at this action")
    who: str | None = Field(default=None, description="Person id")
    to: str | None = Field(default=None, description="Mark (movement) or person id (hand_to)")
    from_: str | None = Field(default=None, alias="from")
    what: str | None = Field(default=None, description="Prop id")
    target: str | None = Field(default=None, description="Person id, prop id or mark")
    on: str | None = Field(default=None, description="Prop id or mark to place on")
    socket: str | None = None
    pose: str | None = None
    state: str | None = None
    at: str | None = Field(default=None, description="Effects: mark, person or prop")
    value: str | None = Field(default=None, description="time_of_day value")
    dur: float | None = None


class Shot(Model):
    id: str
    set: str = Field(description="Set id")
    marks: dict[str, Mark] = Field(default_factory=dict, description="Extra or overriding marks for this shot")
    start: dict[str, StartState] = Field(default_factory=dict, description="Person id -> where they begin")
    prop_start: dict[str, PropStart] = Field(default_factory=dict, description="Prop id -> where it begins if not held")
    camera: list[CameraCue] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    narration: list[NarrationLine] = Field(default_factory=list, description="The default telling's narration")
    transition: Transition = "fold"
    page_text: str | None = Field(default=None, description="Short storybook text printed on the page for this shot")


class Screenplay(Model):
    memory_id: str
    cast: list[str] = Field(description="Person ids")
    props: list[str] = Field(default_factory=list, description="Prop ids")
    shots: list[Shot]
    narrations: dict[str, dict[str, list[NarrationLine]]] | None = Field(
        default=None,
        description="Other tellings: authorId -> shotId -> narration lines. The action is shared.",
    )
    interactions: list[Interaction] | None = None
    explanations: list[Explanation] | None = None


# ------------------------------------------------------------------------- timeline

KeyValue = Union[float, list[float], str, None]


class Track(Model):
    target: str = Field(description='"puppet:<id>", "prop:<id>", "set:<id>.layer<n>" or "camera"')
    channel: str = Field(description="See the channel table in shared/CONTRACTS.md")
    keys: list[tuple[float, KeyValue]] = Field(description="[t, value] pairs, sorted by t")


class Caption(Model):
    t: float
    text: str


class TimelineShot(Model):
    id: str
    set: str
    start: float = Field(description="When this shot's set begins to rise")
    action_start: float = Field(description="When screenplay t=0 of this shot happens")
    end: float
    transition: Transition = "fold"
    page_text: str | None = None


class TimelineEffect(Model):
    t: float
    kind: EffectKind
    at: list[float] | None = Field(default=None, description="Stage position [x, y, z]")
    dur: float | None = None
    value: str | None = None


class NarrationTrack(Model):
    """One telling's narration over the shared action."""

    author_id: str
    audio: str | None = Field(default=None, description="Media id of the narration audio")
    captions: list[Caption] = Field(default_factory=list)
    lines: list[NarrationLine] = Field(default_factory=list)


class Timeline(Model):
    duration: float
    audio: str | None = Field(default=None, description="Media id for the default telling's narration")
    tracks: list[Track]
    captions: list[Caption] = Field(default_factory=list, description="The default telling's captions")
    shots: list[TimelineShot] = Field(default_factory=list)
    effects: list[TimelineEffect] = Field(default_factory=list)
    narrations: dict[str, NarrationTrack] | None = Field(
        default=None, description="authorId -> narration track. Switching teller swaps audio and captions only."
    )
    default_narrator: str | None = Field(default=None, description="authorId whose narration is `audio`/`captions`")
    feet_planted: bool | None = Field(
        default=None,
        description="true when root.position.y already includes foot planting for walk/run keys; the stage must then not plant feet itself",
    )
    interactions: list[Interaction] | None = None
    explanations: list[Explanation] | None = None


class CompileError(Model):
    """Exact, machine-readable compiler error fed back to the director."""

    code: str = Field(description='Stable id, e.g. "not_holding", "unknown_mark", "out_of_reach"')
    message: str = Field(description='e.g. shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it')
    shot: str | None = None
    t: float | None = None
    who: str | None = None
    do: str | None = None


class CompileResult(Model):
    ok: bool
    timeline: Timeline | None = None
    errors: list[CompileError] = Field(default_factory=list)
