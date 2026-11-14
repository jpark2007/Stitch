"""Data model and API bodies (ARCHITECTURE.md sections 5 and 6, DESIGN.md additions).

JSON is camelCase; Python attributes are snake_case. API JSON uses `id`; the db layer
maps it to MongoDB's `_id`. Response models never contain `passwordHash`.

Contract file: additive changes only, and post INTERFACE: on Issue #1 for each one.
web/lib/types.ts is generated from these models by shared/gen_types.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from screenplay.schema import (
    VOCABULARY,
    Caption,
    CompileError,
    Mark,
    Model,
    Screenplay,
    Timeline,
)

Role = Literal["admin", "member"]
Build = Literal[tuple(VOCABULARY["builds"])]  # type: ignore[valid-type]
PropSize = Literal[tuple(VOCABULARY["sizes"])]  # type: ignore[valid-type]
PropSource = Literal["library", "generated", "photo"]
QuestionStatus = Literal["open", "answered", "dismissed"]
RunStatus = Literal["running", "waiting", "done", "error"]


# ---------------------------------------------------------------- users and families

class User(Model):
    """A login. Safe to return: there is deliberately no password field."""

    id: str
    email: str
    name: str
    created_at: datetime | None = None


class Member(Model):
    user_id: str
    role: Role
    joined_at: datetime | None = None


class Invite(Model):
    code: str
    created_by: str
    expires_at: datetime
    revoked: bool = False


class Family(Model):
    id: str
    name: str
    members: list[Member] = Field(default_factory=list)
    invites: list[Invite] = Field(default_factory=list)


class FamilySummary(Model):
    """One family the current user belongs to."""

    id: str
    name: str
    role: Role
    person_id: str | None = Field(default=None, description="The caller's person in this family")


# ------------------------------------------------------------------------------ people

class CharacterSheet(Model):
    color: str = Field(description="Hex body color. Also the person's thread color on the threads screen.")
    item: str | None = Field(default=None, description='Signature item, e.g. "blue flat cap"')
    build: Build = "average"
    item_prop_id: str | None = Field(default=None, description="Prop worn in the head socket")
    notes: str | None = Field(default=None, description='How they move, e.g. "walks slowly with a cane"')


class Person(Model):
    id: str
    family_id: str
    user_id: str | None = Field(default=None, description="null until someone claims this person")
    name: str
    born: int | None = None
    died: int | None = None
    parent_ids: list[str] = Field(default_factory=list)
    spouse_ids: list[str] = Field(default_factory=list)
    character: CharacterSheet
    added_by: str | None = None


class NewPerson(Model):
    name: str
    born: int | None = None
    died: int | None = None
    parent_ids: list[str] = Field(default_factory=list)
    spouse_ids: list[str] = Field(default_factory=list)
    character: CharacterSheet | None = Field(default=None, description="Picked automatically when omitted")


class PersonPatch(Model):
    name: str | None = None
    born: int | None = None
    died: int | None = None
    parent_ids: list[str] | None = None
    spouse_ids: list[str] | None = None
    character: CharacterSheet | None = None


# ------------------------------------------------------------------- props and sets

class PropPart(Model):
    art: str = Field(description="Key into the prop's `art` map")
    at: list[float] = Field(description="[u, v] fraction of the prop image")
    spin: bool = False


class Prop(Model):
    """A saved prop and its rig (ARCHITECTURE.md section 1)."""

    id: str
    family_id: str | None = Field(default=None, description="null for built-in library props")
    name: str
    description: str | None = None
    source: PropSource = "generated"
    size: PropSize
    art: dict[str, str | None] = Field(description='Art key -> media id, e.g. {"body": "media/a1"}. null = placeholder card.')
    color: str | None = Field(default=None, description="Placeholder color when art is missing")
    aspect: float = Field(default=1.0, description="Image width / height")
    anchor: list[float] = Field(default_factory=lambda: [0.5, 1.0], description="Where it meets the ground, image fraction")
    grip: list[float] | None = Field(default=None, description="Where a hand holds it, image fraction")
    sockets: dict[str, list[float]] = Field(default_factory=dict)
    parts: dict[str, PropPart] = Field(default_factory=dict)
    states: dict[str, dict[str, str]] = Field(
        default_factory=dict, description="State name -> art overrides (art key -> media id). {} = no visual change."
    )


class PropPatch(Model):
    name: str | None = None
    photo_id: str | None = Field(default=None, description="Replace the art with an uploaded photo of the real thing")


class SetLayer(Model):
    art: str | None = Field(description="Media id of the cutout. null = placeholder card.")
    depth: float = Field(description="0 = back, 1 = middle, 2 = front. Stage z = depth * 0.35.")
    height: float = Field(description="Height in stage units when risen")
    color: str | None = Field(default=None, description="Placeholder color when art is missing")


class SetDef(Model):
    """A saved place: paper layers plus named marks."""

    id: str
    family_id: str | None = None
    name: str
    description: str | None = None
    layers: list[SetLayer]
    marks: dict[str, Mark] = Field(default_factory=dict)


# ---------------------------------------------------------------------------- memories

class Telling(Model):
    """One person's answer about a moment."""

    author_id: str = Field(description="Person id")
    question: str | None = None
    text: str
    photo_ids: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class Memory(Model):
    """A moment: one real-world event, shared across the books of everyone in it."""

    id: str
    family_id: str
    title: str
    year: int
    era: str | None = Field(default=None, description="Era id from shared/vocabulary.json, derived from year")
    participant_ids: list[str] = Field(default_factory=list)
    tellings: list[Telling] = Field(default_factory=list)
    screenplay: Screenplay | None = None
    timeline: Timeline | None = None
    prop_ids: list[str] = Field(default_factory=list)
    set_ids: list[str] = Field(default_factory=list)
    version: int = 1


class MomentSummary(Model):
    """A moment without its scene, for the threads screen and lists."""

    id: str
    title: str
    year: int
    participant_ids: list[str]
    era: str | None = None
    telling_author_ids: list[str] = Field(default_factory=list)


class Question(Model):
    id: str
    family_id: str
    person_id: str
    text: str
    memory_id: str | None = None
    status: QuestionStatus = "open"


# ----------------------------------------------------------------------- auth routes

class SignupRequest(Model):
    email: str
    password: str = Field(min_length=8)
    name: str


class LoginRequest(Model):
    email: str
    password: str


class MeResponse(Model):
    user: User
    families: list[FamilySummary]


class SessionResponse(Model):
    """GET /session: who is signed in. Always 200; `user` is null when nobody is."""

    user: User | None = None
    families: list[FamilySummary] = Field(default_factory=list)


# --------------------------------------------------------------------- family routes

class CreateFamilyRequest(Model):
    name: str
    person: NewPerson = Field(description="The creator's own person, the first node in the tree")


class CreateFamilyResponse(Model):
    family: FamilySummary
    person: Person


class InviteResponse(Model):
    code: str
    path: str = Field(description='"/join/<code>"')
    expires_at: datetime


class PersonSummary(Model):
    id: str
    name: str
    born: int | None = None
    color: str | None = None


class InvitePreview(Model):
    family_id: str
    family_name: str
    unclaimed_people: list[PersonSummary]


class JoinRequest(Model):
    """Exactly one of person_id (claim) or person (add yourself)."""

    person_id: str | None = None
    person: NewPerson | None = None


class JoinResponse(Model):
    family: FamilySummary
    person: Person


# -------------------------------------------------------------------- content routes

class TreeResponse(Model):
    family_id: str
    family_name: str
    people: list[Person]


class BookResponse(Model):
    person: Person
    memories: list[Memory] = Field(description="Ordered by year")
    props: list[Prop] = Field(default_factory=list, description="Every prop the memories reference")
    sets: list[SetDef] = Field(default_factory=list, description="Every set the memories reference")
    people: list[Person] = Field(default_factory=list, description="Everyone who appears in the memories")


class MemoryResponse(Model):
    memory: Memory
    props: list[Prop] = Field(default_factory=list)
    sets: list[SetDef] = Field(default_factory=list)
    people: list[Person] = Field(default_factory=list)


class ThreadsResponse(Model):
    """GET /families/{fid}/threads: every moment in the family, ordered by year."""

    people: list[PersonSummary]
    moments: list[MomentSummary]


class PropsResponse(Model):
    props: list[Prop]


class SetsResponse(Model):
    sets: list[SetDef]


class QuestionsResponse(Model):
    questions: list[Question]


class SubmitMemoryFields(Model):
    """Form fields of POST /families/{fid}/memories (multipart; photos are files named `photos`)."""

    text: str
    year: int
    question: str | None = None
    title: str | None = None
    memory_id: str | None = Field(default=None, description="Set to add a second telling to an existing moment")
    participant_ids: list[str] = Field(default_factory=list)


class SubmitMemoryResponse(Model):
    run_id: str
    memory_id: str | None = None


class PropChip(Model):
    """A prop the agent found, shown for family review."""

    id: str
    name: str
    description: str | None = None
    size: PropSize
    art: str | None = Field(default=None, description="Media id of a preview")
    reused: bool = False


class PropEdit(Model):
    id: str
    action: Literal["keep", "rename", "remove", "replace_photo"]
    name: str | None = None
    photo_id: str | None = None


class AnswerRequest(Model):
    """POST /families/{fid}/runs/{id}/answer. Resumes the paused run."""

    kind: Literal["answer", "props", "merge"]
    text: str | None = Field(default=None, description="kind=answer: the reply to the follow-up question")
    props: list[PropEdit] | None = Field(default=None, description="kind=props: the reviewed prop list")
    approve: bool | None = Field(default=None, description="kind=merge: whether the two people are the same")


class AskRequest(Model):
    query: str


class AskQuote(Model):
    memory_id: str
    author_id: str
    text: str
    title: str | None = None
    year: int | None = None


class AskBest(Model):
    """The single memory an ask points at: the one to open."""

    memory_id: str
    title: str | None = None
    year: int | None = None
    person_id: str | None = Field(
        default=None,
        description="A person whose book holds it: the teller of the quoted telling, else the first participant",
    )
    confident: bool = Field(description="True only when this memory clearly leads the other candidates")


class AskResponse(Model):
    answer: str
    quotes: list[AskQuote]
    best: AskBest | None = Field(default=None, description="The single memory to open; null when nothing fits")
    mode: Literal["search", "model"] | None = Field(default=None, description="How it was answered")


class MediaUploadResponse(Model):
    """POST /families/{fid}/media (multipart field `file`)."""

    id: str = Field(description="Bare media id; reference it as 'media/<id>' or pass it as a photo id")


class MemberRolePatch(Model):
    """PATCH /families/{fid}/members/{userId}. Admin only; the last admin cannot be demoted."""

    role: Role


class ErrorResponse(Model):
    detail: str


# ---------------------------------------------------------------- family traditions
# The Book of Family Traditions: things the family does again and again, as opposed to
# a moment (one real-world event). Routes: GET/POST /families/{fid}/traditions and
# GET/PATCH/DELETE /families/{fid}/traditions/{id}.
#
# Bounds live on the request models (a 422 list when broken), not on the response, so
# a stored tradition always reads back. Text is trimmed before it is measured.

TraditionCategory = Literal["food", "holiday", "trip", "music", "game", "faith", "craft", "other"]
TraditionWhenKind = Literal["yearly", "seasonal", "monthly", "weekly", "occasional"]

MAX_TRADITION_TITLE_CHARS = 200  # as a memory's title (routes/memories.py MAX_TITLE_CHARS)
MAX_TRADITION_DESCRIPTION_CHARS = 5_000
MAX_TRADITION_LABEL_CHARS = 100
MAX_TRADITION_PEOPLE = 100  # as a memory's participants (MAX_PARTICIPANTS)
MAX_TRADITION_PHOTOS = 10  # as a memory's photos (MAX_PHOTOS)
MIN_TRADITION_YEAR = 1800  # as a memory's year (MIN_YEAR); the latest is this year

TraditionTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TRADITION_TITLE_CHARS)]
TraditionDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_TRADITION_DESCRIPTION_CHARS)]


def _check_started_year(year: int | None) -> int | None:
    if year is not None and not MIN_TRADITION_YEAR <= year <= datetime.now(UTC).year:
        raise ValueError(f"Started year must be between {MIN_TRADITION_YEAR} and this year")
    return year


class TraditionWhen(Model):
    """When a tradition happens. `label` is free text ("Christmas Eve", "every Sunday"), at
    most 100 characters after trimming; `month` is 1-12 for the month it happens in, if any."""

    kind: TraditionWhenKind
    label: Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_TRADITION_LABEL_CHARS)] | None = Field(
        default=None, description='e.g. "Christmas Eve", "every Sunday"'
    )
    month: int | None = Field(default=None, ge=1, le=12, description="1 = January ... 12 = December")


class Tradition(Model):
    """Something the family does again and again: one page in the Book of Family Traditions."""

    id: str
    family_id: str
    title: str
    description: str = Field(description="May be empty")
    category: TraditionCategory
    when: TraditionWhen
    participant_ids: list[str] = Field(description="Person ids of the people who keep it")
    photo_ids: list[str] = Field(description="Media ids (as returned by POST /families/{fid}/media)")
    started_year: int | None = Field(default=None, description="The year it began, when known")
    created_by: str = Field(description="User id of whoever added it")
    created_at: datetime
    updated_at: datetime


class TraditionsResponse(Model):
    """GET /families/{fid}/traditions: every tradition in the family."""

    traditions: list[Tradition]


class CreateTraditionRequest(Model):
    """POST /families/{fid}/traditions. Bounds (422 when broken): title 1-200 characters
    after trimming; description at most 5,000 (may be empty); when.label at most 100;
    when.month 1-12; at most 100 participantIds and 10 photoIds; startedYear from 1800 to
    this year."""

    title: TraditionTitle
    description: TraditionDescription = ""
    category: TraditionCategory
    when: TraditionWhen
    participant_ids: list[str] = Field(default_factory=list, max_length=MAX_TRADITION_PEOPLE)
    photo_ids: list[str] = Field(default_factory=list, max_length=MAX_TRADITION_PHOTOS)
    started_year: int | None = None

    _started_year = field_validator("started_year")(_check_started_year)


class UpdateTraditionRequest(Model):
    """PATCH /families/{fid}/traditions/{id}. Every field is optional; an omitted field is
    left as it is and `when` is replaced whole. Send `startedYear: null` to clear it (the
    route tells null from omitted with `model_fields_set`). Same bounds as
    CreateTraditionRequest."""

    title: TraditionTitle | None = None
    description: TraditionDescription | None = None
    category: TraditionCategory | None = None
    when: TraditionWhen | None = None
    participant_ids: list[str] | None = Field(default=None, max_length=MAX_TRADITION_PEOPLE)
    photo_ids: list[str] | None = Field(default=None, max_length=MAX_TRADITION_PHOTOS)
    started_year: int | None = None

    _started_year = field_validator("started_year")(_check_started_year)


# ------------------------------------------------------------------- run stream (SSE)
# GET /families/{fid}/runs/{id}/stream sends `event: <name>` with `data: <JSON of the model>`.

class PlanItem(Model):
    text: str
    status: Literal["pending", "in_progress", "completed"] = "pending"


class PlanEvent(Model):
    todos: list[PlanItem]


class SubagentStartEvent(Model):
    name: str
    task: str | None = None


class SubagentEndEvent(Model):
    name: str
    summary: str | None = None


class QuestionEvent(Model):
    """Needs input: answer with AnswerRequest(kind="answer")."""

    text: str


class PropsFoundEvent(Model):
    """Needs review: answer with AnswerRequest(kind="props")."""

    props: list[PropChip]


class MergeEvent(Model):
    """Needs approval: answer with AnswerRequest(kind="merge")."""

    text: str
    person_id: str
    candidate_name: str


class CompileErrorEvent(Model):
    errors: list[CompileError]
    round: int = 1


class FrameShot(Model):
    t: float
    media_id: str


class FramesEvent(Model):
    frames: list[FrameShot]
    critique: str | None = None


class SceneReadyEvent(Model):
    memory_id: str
    version: int = 1


class DoneEvent(Model):
    memory_id: str


class ErrorEvent(Model):
    message: str


RUN_EVENTS: dict[str, type[Model]] = {
    "plan": PlanEvent,
    "subagent_start": SubagentStartEvent,
    "subagent_end": SubagentEndEvent,
    "question": QuestionEvent,
    "props_found": PropsFoundEvent,
    "merge": MergeEvent,
    "compile_error": CompileErrorEvent,
    "frames": FramesEvent,
    "scene_ready": SceneReadyEvent,
    "done": DoneEvent,
    "error": ErrorEvent,
}

__all__ = [name for name, obj in list(globals().items()) if isinstance(obj, type) and issubclass(obj, Model)] + [
    "RUN_EVENTS",
    "Caption",
]
