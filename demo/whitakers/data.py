"""A family's data directory: loading, checking and compiling it. No database, no network.

The directory (STITCH_WHITAKERS_DIR, default ~/stitch-private/demo-family/data) holds

    family.json                     the family, its accounts, people, contacts and questions
    moments/<memoryId>/moment.json  one folder per moment, with its screenplay.json
    props/*.json, sets/*.json       JSON lists of Prop and SetDef, merged
    photos/                         the photos tellings attach, by file name
    art/                            prop and set art, by file name (optional)
    replays/                        prepared stories for replay mode (demo/replay format)

README.md documents every field. `load_family` reads it all, checks it, compiles every
moment's screenplay with the real compiler, and returns a `FamilyData` ready to seed. Every
problem it finds is collected, and a `WhitakerDataError` lists them all at once.

Art files are optional: a prop or set layer that names an art file that is not there yet
gets the placeholder art (null), with a warning, so art can be dropped in later. Every
photo and art file gets a media id derived from the family id and its file name, so the
ids are the same on every start.

Nothing here logs or prints the data: errors and warnings name files and ids only.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

import schemas
from db import people as people_db
from db.media import MAX_BYTES
from db.props import library_props
from screenplay import compiler
from screenplay.narration import default_author
from screenplay.schema import Screenplay, Timeline
from seed import demo
from seed.fake_runs import remap_ids

DATA_DIR_ENV = "STITCH_WHITAKERS_DIR"
DEFAULT_DATA_DIR = Path("~/stitch-private/demo-family/data")
# The committed copy (no family photos: those stay private and are dropped into its photos/).
REPO_DATA_DIR = Path(__file__).resolve().parent.parent / "macoskos" / "data"
FAMILY_FILE = "family.json"
MOMENTS_DIR = "moments"
MOMENT_FILE = "moment.json"
DEFAULT_SCREENPLAY = "screenplay.json"
PROPS_DIR = "props"
SETS_DIR = "sets"
PHOTOS_DIR = "photos"
ART_DIR = "art"
REPLAYS_DIR = "replays"

IMAGE_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
ART_EXTENSIONS = (".png", ".webp", ".jpg", ".jpeg")
_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_PHONE = re.compile(r"^\+[1-9][0-9]{7,14}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class WhitakerDataError(ValueError):
    """The data directory is malformed, inconsistent or does not compile. Lists every error."""

    def __init__(self, directory: Path, errors: list[str]) -> None:
        self.directory = directory
        self.errors = list(errors)
        lines = "\n".join(f"  - {e}" for e in self.errors)
        super().__init__(f"{len(self.errors)} error(s) in {directory}:\n{lines}")


def data_dir(environ: Mapping[str, str] = os.environ) -> Path:
    """The data directory: STITCH_WHITAKERS_DIR, else the private default when it exists,
    else the committed copy in demo/macoskos/data. `~` is expanded."""
    raw = (environ.get(DATA_DIR_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    private = DEFAULT_DATA_DIR.expanduser().resolve()
    return private if private.is_dir() or not REPO_DATA_DIR.is_dir() else REPO_DATA_DIR


# ------------------------------------------------------------------------------ the files


class _Strict(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class FamilySpec(_Strict):
    id: str
    name: str


class AccountSpec(_Strict):
    email: str
    password: str
    name: str
    person_id: str
    role: schemas.Role = "member"
    id: str | None = None


class PersonSpec(_Strict):
    id: str
    name: str
    born: int | None = None
    died: int | None = None
    parents: list[str] = Field(default_factory=list)
    spouses: list[str] = Field(default_factory=list)
    color: str | None = None
    item: str | None = None
    build: schemas.Build = "average"
    notes: str | None = None
    item_prop_id: str | None = None


class QuietHoursSpec(_Strict):
    start: str = "21:00"
    end: str = "08:00"


class ContactSpec(_Strict):
    person_id: str
    imessage: str  # an E.164 phone number or an email address
    timezone: str = "America/New_York"
    cadence_days: int = Field(default=7, ge=1, le=30)
    quiet_hours: QuietHoursSpec = Field(default_factory=QuietHoursSpec)


class QuestionSpec(_Strict):
    person_id: str
    text: str
    memory_id: str | None = None


class FamilyFile(_Strict):
    family: FamilySpec
    accounts: list[AccountSpec]
    people: list[PersonSpec]
    contacts: list[ContactSpec] = Field(default_factory=list)
    questions: list[QuestionSpec] = Field(default_factory=list)


class TellingSpec(_Strict):
    teller: str
    text: str
    question: str | None = None
    photos: list[str] = Field(default_factory=list)
    order: int | None = None


class ReuseSpec(_Strict):
    screenplay: str
    cast: dict[str, str]
    props: dict[str, str] = Field(default_factory=dict)
    sets: dict[str, str] = Field(default_factory=dict)


class MomentFile(_Strict):
    title: str
    year: int
    place: str | None = None
    people: list[str] | None = None
    tellings: list[TellingSpec] | None = None
    photos: list[str] = Field(default_factory=list)
    screenplay: str | None = None
    reuse: ReuseSpec | None = None


# ------------------------------------------------------------------------------ loaded


@dataclass(frozen=True)
class Account:
    user_id: str
    email: str
    password: str
    name: str
    person_id: str
    role: schemas.Role


@dataclass(frozen=True)
class Contact:
    person_id: str
    handle: str
    timezone: str
    cadence_days: int
    quiet_hours: dict[str, str]


@dataclass(frozen=True)
class Telling:
    author_id: str
    question: str | None
    text: str
    photos: tuple[str, ...]  # file names in photos/


@dataclass(frozen=True)
class Moment:
    id: str
    title: str
    year: int
    participant_ids: list[str]
    tellings: list[Telling]
    screenplay: Screenplay
    timeline: Timeline
    prop_ids: list[str]
    set_ids: list[str]
    reused: str | None  # the demo screenplay it reuses, if any


@dataclass(frozen=True)
class Question:
    question_id: str
    person_id: str
    text: str
    memory_id: str | None


@dataclass(frozen=True)
class MediaFile:
    media_id: str
    path: Path
    content_type: str
    kind: Literal["photo", "cutout"]


@dataclass(frozen=True)
class FamilyData:
    """Everything the seed writes for the family, checked and compiled."""

    directory: Path
    family_id: str
    name: str
    accounts: list[Account]
    people: list[schemas.Person]  # parents before children
    props: list[schemas.Prop]
    sets: list[schemas.SetDef]
    media: list[MediaFile]
    photo_ids: dict[str, str]  # photo file name -> media id
    moments: list[Moment]  # oldest first
    questions: list[Question]
    contacts: list[Contact]
    warnings: list[str] = field(default_factory=list)

    @property
    def admin(self) -> Account:
        return next(a for a in self.accounts if a.role == "admin")

    @property
    def replay_dir(self) -> Path:
        return self.directory / REPLAYS_DIR

    def person(self, person_id: str) -> schemas.Person | None:
        return next((p for p in self.people if p.id == person_id), None)

    def scene_people(self) -> dict[str, schemas.Person]:
        return {p.id: p for p in self.people}


# ------------------------------------------------------------------------------ helpers


def media_id(family_id: str, kind: str, filename: str) -> str:
    """A fixed, valid ObjectId string for a data file, the same on every start."""
    return hashlib.sha256(f"stitch-demo-family\n{family_id}\n{kind}\n{filename}".encode()).hexdigest()[:24]


def question_id(family_id: str, n: int) -> str:
    return hashlib.sha256(f"stitch-demo-family\n{family_id}\nquestion\n{n}".encode()).hexdigest()[:24]


def _pydantic_lines(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '(top)'}: {e['msg']}" for e in exc.errors())


def _read_json(path: Path, errors: list[str], where: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"{where}: file not found")
    except json.JSONDecodeError as exc:
        errors.append(f"{where}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
    return None


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _image(path: Path, root: Path, errors: list[str], what: str) -> tuple[str, float] | None:
    """(content type, width / height) of an image file, or None with an error."""
    where = f"{what} {_rel(path, root)}"
    content_type = IMAGE_TYPES.get(path.suffix.lower())
    if content_type is None:
        hint = " (convert HEIC to JPEG first)" if path.suffix.lower() in (".heic", ".heif") else ""
        errors.append(f"{where}: not a supported image type{hint}; use {', '.join(sorted(IMAGE_TYPES))}")
        return None
    size = path.stat().st_size
    if size == 0 or size > MAX_BYTES:
        errors.append(f"{where}: {size} bytes; media must be 1 to {MAX_BYTES} bytes")
        return None
    try:
        with Image.open(io.BytesIO(path.read_bytes())) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        errors.append(f"{where}: not a readable image ({type(exc).__name__})")
        return None
    return content_type, (width / height if height else 1.0)


class _Files:
    """The photo and art files the data uses, each stored once under a fixed media id."""

    def __init__(self, root: Path, family_id: str, errors: list[str], warnings: list[str]) -> None:
        self.root, self.family_id, self.errors, self.warnings = root, family_id, errors, warnings
        self.media: dict[str, MediaFile] = {}
        self.photo_ids: dict[str, str] = {}
        self._aspects: dict[str, float] = {}

    def _file(self, folder: str, name: str) -> Path | None:
        if not name or Path(name).name != name or name.startswith("."):
            self.errors.append(f"{folder}/{name}: a file name, with no folders")
            return None
        return self.root / folder / name

    def photo(self, name: str, where: str) -> str | None:
        """The media id of photos/<name>; a warning when it is missing (the telling is
        saved without it, so a copy without the private photos still runs)."""
        if name in self.photo_ids:
            return self.photo_ids[name]
        path = self._file(PHOTOS_DIR, name)
        if path is None:
            return None
        if not path.is_file():
            self.warnings.append(f"{where}: no photo {PHOTOS_DIR}/{name}; shown without it")
            return None
        found = _image(path, self.root, self.errors, "photo")
        if found is None:
            return None
        mid = media_id(self.family_id, "photo", name)
        self.media[mid] = MediaFile(mid, path, found[0], "photo")
        self.photo_ids[name] = mid
        return mid

    def art(self, value: str | None, where: str, *, stem: str) -> str | None:
        """An art value as stored. null: art/<stem>.<png|webp|jpg|jpeg> when there is one, else
        null (the placeholder). "media/<id>": kept. A file name: art/<file> as its media ref
        ("media/<id>"), or null with a warning when that file is not there yet."""
        if value is not None and value.startswith("media/"):
            return value
        if value is None:
            value = next((f"{stem}{ext}" for ext in ART_EXTENSIONS if (self.root / ART_DIR / f"{stem}{ext}").is_file()),
                         None)
            if value is None:
                return None
        path = self._file(ART_DIR, value)
        if path is None:
            return None
        if not path.is_file():
            self.warnings.append(f"{where}: no art file {ART_DIR}/{value} yet; placeholder art")
            return None
        mid = media_id(self.family_id, "art", value)
        if mid not in self.media:
            found = _image(path, self.root, self.errors, "art")
            if found is None:
                return None
            self.media[mid] = MediaFile(mid, path, found[0], "cutout")
            self._aspects[mid] = found[1]
        return f"media/{mid}"

    def aspect(self, ref: str | None) -> float | None:
        return self._aspects.get(ref.removeprefix("media/")) if ref else None


def _load_list(folder: Path, root: Path, model: type[BaseModel], errors: list[str]) -> list[tuple[dict, str]]:
    """Every item of every *.json list in `folder`, as (raw dict, where), in file order."""
    out: list[tuple[dict, str]] = []
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        where = _rel(path, root)
        raw = _read_json(path, errors, where)
        if raw is None:
            continue
        if not isinstance(raw, list):
            errors.append(f"{where}: expected a JSON list of {model.__name__} objects")
            continue
        for i, item in enumerate(raw):
            ident = item.get("id") if isinstance(item, dict) else None
            if not isinstance(item, dict):
                errors.append(f"{where}: item {i} is not an object")
                continue
            out.append((item, f"{where} item {i} ({ident!r})"))
    return out


def _props(root: Path, family_id: str, files: _Files, errors: list[str]) -> dict[str, schemas.Prop]:
    props: dict[str, schemas.Prop] = {}
    for item, where in _load_list(root / PROPS_DIR, root, schemas.Prop, errors):
        try:
            prop = schemas.Prop.model_validate(item)
        except ValidationError as exc:
            errors.append(f"{where}: not a valid Prop: {_pydantic_lines(exc)}")
            continue
        if prop.id in props:
            errors.append(f"{where}: prop id {prop.id!r} is defined twice")
            continue
        art = {
            key: files.art(value, f"prop {prop.id} art {key!r}", stem=prop.id if key == "body" else f"{prop.id}.{key}")
            for key, value in prop.art.items()
        }
        update: dict[str, Any] = {"family_id": family_id, "art": art}
        if "aspect" not in item and (aspect := files.aspect(art.get("body"))) is not None:
            update["aspect"] = round(aspect, 4)
        props[prop.id] = prop.model_copy(update=update)
    return props


def _sets(root: Path, family_id: str, files: _Files, errors: list[str]) -> dict[str, schemas.SetDef]:
    sets: dict[str, schemas.SetDef] = {}
    for item, where in _load_list(root / SETS_DIR, root, schemas.SetDef, errors):
        try:
            set_def = schemas.SetDef.model_validate(item)
        except ValidationError as exc:
            errors.append(f"{where}: not a valid SetDef: {_pydantic_lines(exc)}")
            continue
        if set_def.id in sets:
            errors.append(f"{where}: set id {set_def.id!r} is defined twice")
            continue
        layers = [
            layer.model_copy(update={"art": files.art(layer.art, f"set {set_def.id} layer {i}",
                                                      stem=f"{set_def.id}.layer{i}")})
            for i, layer in enumerate(set_def.layers)
        ]
        sets[set_def.id] = set_def.model_copy(update={"family_id": family_id, "layers": layers})
    return sets


def _people_order(specs: list[PersonSpec], errors: list[str]) -> list[PersonSpec]:
    """Parents before children (a parent cycle is an error), otherwise in file order."""
    by_id = {p.id: p for p in specs}
    done: dict[str, PersonSpec] = {}
    remaining = [p for p in specs]
    while remaining:
        ready = [p for p in remaining if all(q in done or q not in by_id for q in p.parents)]
        if not ready:
            errors.append(f"people: parents form a cycle among {', '.join(sorted(p.id for p in remaining))}")
            break
        for p in ready:
            done[p.id] = p
        remaining = [p for p in remaining if p.id not in done]
    return list(done.values())


def _people(spec: FamilyFile, family_id: str, errors: list[str]) -> list[schemas.Person]:
    ids = [p.id for p in spec.people]
    for pid in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"people: person id {pid!r} is listed twice")
    taken = set(demo.DEMO_PERSON_IDS)
    for p in spec.people:
        if not _ID.match(p.id):
            errors.append(f"people: person id {p.id!r} must be letters, digits, _ or -")
        if p.id in taken:
            errors.append(f"people: person id {p.id!r} is a demo family (Maos) id; person ids are global")
    by_id = {p.id: p for p in spec.people}
    spouses: dict[str, set[str]] = {p.id: set() for p in spec.people}
    for p in spec.people:
        for ref, kind in [*((q, "parent") for q in p.parents), *((q, "spouse") for q in p.spouses)]:
            if ref not in by_id:
                errors.append(f"people: {p.id}'s {kind} {ref!r} is not a person")
            elif ref == p.id:
                errors.append(f"people: {p.id} is their own {kind}")
        if len(p.parents) > 2:
            errors.append(f"people: {p.id} has more than two parents")
        if p.born is not None and p.died is not None and p.died < p.born:
            errors.append(f"people: {p.id} died ({p.died}) before they were born ({p.born})")
        for q in p.parents:
            parent = by_id.get(q)
            if parent and p.born is not None and parent.born is not None and p.born < parent.born:
                errors.append(f"people: {p.id} (born {p.born}) is born before their parent {q} (born {parent.born})")
        for q in p.spouses:
            if q in by_id and q != p.id:
                spouses[p.id].add(q)
                spouses[q].add(p.id)
    # A person with no color gets the first unused one (and, with no item, the first unused
    # item), as db.people does for a person added without a character sheet.
    used_colors = {p.color for p in spec.people if p.color}
    used_items = {p.item for p in spec.people if p.item}
    characters: dict[str, schemas.CharacterSheet] = {}
    for p in spec.people:
        color, item = p.color, p.item
        if color is None:
            picked = people_db.pick_character(used_colors, used_items)
            color, item = picked.color, item or picked.item
            used_colors.add(color)
            used_items.add(item or "")
        characters[p.id] = schemas.CharacterSheet(color=color, item=item, build=p.build, notes=p.notes,
                                                  item_prop_id=p.item_prop_id)
    linked = {a.person_id: a.id or account_user_id(family_id, a.person_id) for a in spec.accounts}
    admin = next((a for a in spec.accounts if a.role == "admin"), None)
    added_by = (admin.id or account_user_id(family_id, admin.person_id)) if admin else None
    people = []
    for p in _people_order(spec.people, errors):
        people.append(schemas.Person(
            id=p.id, family_id=family_id, user_id=linked.get(p.id), name=p.name, born=p.born, died=p.died,
            parent_ids=list(p.parents), spouse_ids=sorted(spouses.get(p.id, ())),
            character=characters[p.id], added_by=added_by,
        ))
    return people


def account_user_id(family_id: str, person_id: str) -> str:
    return f"{family_id}_user_{person_id}"


def _accounts(spec: FamilyFile, family_id: str, errors: list[str]) -> list[Account]:
    people = {p.id for p in spec.people}
    demo_emails = {e.casefold() for e in demo.DEMO_EMAILS}
    seen_emails: set[str] = set()
    seen_people: set[str] = set()
    seen_ids: set[str] = set(demo.DEMO_USER_IDS)
    out = []
    for i, a in enumerate(spec.accounts):
        where = f"accounts[{i}]"
        email = a.email.strip().lower()
        if "@" not in email:
            errors.append(f"{where}: email is not an email address")
        if email in demo_emails:
            errors.append(f"{where}: email is a demo family (Maos) account")
        if email in seen_emails:
            errors.append(f"{where}: email is used by another account")
        seen_emails.add(email)
        if not a.password.strip():
            errors.append(f"{where}: password is blank")
        if a.person_id not in people:
            errors.append(f"{where}: personId {a.person_id!r} is not a person")
        if a.person_id in seen_people:
            errors.append(f"{where}: person {a.person_id!r} already has an account")
        seen_people.add(a.person_id)
        user_id = a.id or account_user_id(family_id, a.person_id)
        if user_id in seen_ids:
            errors.append(f"{where}: user id {user_id!r} is taken")
        seen_ids.add(user_id)
        out.append(Account(user_id, email, a.password, a.name.strip() or a.name, a.person_id, a.role))
    if not any(a.role == "admin" for a in spec.accounts):
        errors.append("accounts: at least one account needs role \"admin\" (the first admin creates the family)")
    return out


def _contacts(spec: FamilyFile, errors: list[str]) -> list[Contact]:
    people = {p.id for p in spec.people}
    out, seen_people, seen_handles = [], set(), set()
    for i, c in enumerate(spec.contacts):
        where = f"contacts[{i}]"
        handle = c.imessage.strip()
        if "@" in handle:
            handle = handle.lower()
        if c.person_id not in people:
            errors.append(f"{where}: personId {c.person_id!r} is not a person")
        if c.person_id in seen_people:
            errors.append(f"{where}: person {c.person_id!r} has two contacts")
        if not (_PHONE.match(handle) or _EMAIL.match(handle)):
            errors.append(f"{where}: imessage must be an E.164 phone number (like +15555550123) or an email address")
        if handle in seen_handles:
            errors.append(f"{where}: imessage handle is used by another contact")
        seen_people.add(c.person_id)
        seen_handles.add(handle)
        out.append(Contact(c.person_id, handle, c.timezone, c.cadence_days, c.quiet_hours.model_dump()))
    return out


# ------------------------------------------------------------------------------ moments


@dataclass
class _Scene:
    """What a moment's scene uses, for compiling: everything the family has, plus the demo
    family's props and sets that a screenplay uses and the family does not define."""

    people: dict[str, schemas.Person]
    props: dict[str, schemas.Prop]
    sets: dict[str, schemas.SetDef]
    family_id: str
    borrowed_props: dict[str, schemas.Prop] = field(default_factory=dict)
    borrowed_sets: dict[str, schemas.SetDef] = field(default_factory=dict)
    _demo_props: dict[str, schemas.Prop] | None = None
    _demo_sets: dict[str, schemas.SetDef] | None = None
    _library: dict[str, schemas.Prop] | None = None

    def library(self) -> dict[str, schemas.Prop]:
        if self._library is None:
            self._library = library_props()
        return self._library

    def prop(self, prop_id: str) -> schemas.Prop | None:
        if prop_id in self.props:
            return self.props[prop_id]
        if prop_id in self.library():
            return self.library()[prop_id]
        if self._demo_props is None:
            self._demo_props = {p.id: p for p in demo.load_props()}
        found = self._demo_props.get(prop_id)
        if found is not None:
            found = found.model_copy(update={"family_id": self.family_id})
            self.borrowed_props[prop_id] = found
        return found

    def set(self, set_id: str) -> schemas.SetDef | None:
        if set_id in self.sets:
            return self.sets[set_id]
        if self._demo_sets is None:
            self._demo_sets = {s.id: s for s in demo.load_sets()}
        found = self._demo_sets.get(set_id)
        if found is not None:
            found = found.model_copy(update={"family_id": self.family_id})
            self.borrowed_sets[set_id] = found
        return found


def _demo_moment(screenplay_id: str) -> Any | None:
    return next((m for m in demo.MOMENTS if m.id == screenplay_id), None)


def reuse_screenplay(memory_id: str, reuse: ReuseSpec) -> tuple[Screenplay, dict[str, str], Any]:
    """A demo family screenplay with its ids renamed for another family. Pure.

    Returns (the screenplay, the person id map, the demo moment). Raises ValueError naming
    the demo person ids the cast map leaves out, or an unknown screenplay id.
    """
    base_moment = _demo_moment(reuse.screenplay)
    if base_moment is None:
        known = ", ".join(demo.DEMO_MEMORY_IDS)
        raise ValueError(f"reuse.screenplay {reuse.screenplay!r} is not a demo screenplay (one of {known})")
    base = demo.load_screenplay(reuse.screenplay)
    needed = dict.fromkeys([*base.cast, *demo.tellers(base)])
    missing = [pid for pid in needed if pid not in reuse.cast]
    if missing:
        raise ValueError(f"reuse.cast does not map {', '.join(missing)} (the screenplay's cast and narrators)")
    ids = {**reuse.props, **reuse.sets, **reuse.cast}
    data = remap_ids(base.model_dump(mode="json", by_alias=True, exclude_none=True), ids)
    data["memoryId"] = memory_id
    return Screenplay.model_validate(data), dict(reuse.cast), base_moment


def _alive(person: schemas.Person, year: int) -> bool:
    return (person.born is None or person.born <= year) and (person.died is None or person.died >= year)


def _moment(
    folder: Path, root: Path, scene: _Scene, files: _Files, errors: list[str]
) -> Moment | None:
    memory_id = folder.name
    where = f"{MOMENTS_DIR}/{memory_id}"
    start = len(errors)
    if not _ID.match(memory_id):
        errors.append(f"{where}: the folder name is the memory id: letters, digits, _ or -")
    if memory_id in demo.DEMO_MEMORY_IDS:
        errors.append(f"{where}: {memory_id!r} is a demo family (Maos) memory id; memory ids are global")
    raw = _read_json(folder / MOMENT_FILE, errors, f"{where}/{MOMENT_FILE}")
    if raw is None:
        return None
    try:
        spec = MomentFile.model_validate(raw)
    except ValidationError as exc:
        errors.append(f"{where}/{MOMENT_FILE}: {_pydantic_lines(exc)}")
        return None
    if spec.reuse is not None and spec.screenplay is not None:
        errors.append(f"{where}: give \"screenplay\" or \"reuse\", not both")
        return None

    reused_moment = None
    person_map: dict[str, str] = {}
    if spec.reuse is not None:
        try:
            screenplay, person_map, reused_moment = reuse_screenplay(memory_id, spec.reuse)
        except (ValueError, demo.DemoDataError) as exc:
            errors.append(f"{where}: {exc}")
            return None
    else:
        sp_name = spec.screenplay or DEFAULT_SCREENPLAY
        sp_raw = _read_json(folder / sp_name, errors, f"{where}/{sp_name}")
        if sp_raw is None:
            return None
        try:
            screenplay = Screenplay.model_validate(sp_raw)
        except ValidationError as exc:
            errors.append(f"{where}/{sp_name}: not a valid Screenplay: {_pydantic_lines(exc)}")
            return None
        if screenplay.memory_id != memory_id:
            errors.append(f"{where}/{sp_name}: memoryId is {screenplay.memory_id!r}, expected {memory_id!r}")

    # Tellings: as written, else (reuse) the demo moment's, told by the mapped people.
    if spec.tellings is not None:
        specs = sorted(enumerate(spec.tellings), key=lambda it: (it[1].order is None, it[1].order or 0, it[0]))
        tellings = [Telling(t.teller, t.question, t.text, tuple(t.photos)) for _, t in specs]
    elif reused_moment is not None:
        tellings = []
        for t in reused_moment.tellings:
            if t.author_id not in person_map:
                errors.append(f"{where}: reuse.cast does not map {t.author_id} (a teller of {reused_moment.id}); "
                              "map them or write \"tellings\"")
                continue
            tellings.append(Telling(person_map[t.author_id], t.question, t.text, ()))
    else:
        errors.append(f"{where}: no tellings")
        return None
    if not tellings:
        errors.append(f"{where}: no tellings")
        return None
    if spec.photos:
        first = tellings[0]
        tellings[0] = Telling(first.author_id, first.question, first.text, (*spec.photos, *first.photos))
    for t in tellings:
        if not t.text.strip():
            errors.append(f"{where}: {t.author_id}'s telling has no text")
        for name in t.photos:
            files.photo(name, where)

    # Participants: as written, else (reuse) the demo moment's, mapped; then the cast and tellers.
    if spec.people is not None:
        participants = list(spec.people)
    elif reused_moment is not None:
        participants = [person_map[p] for p in reused_moment.participant_ids if p in person_map]
    else:
        participants = list(screenplay.cast)
    participants = list(dict.fromkeys([*participants, *screenplay.cast, *(t.author_id for t in tellings)]))

    people = scene.people
    for pid in participants:
        person = people.get(pid)
        if person is None:
            errors.append(f"{where}: {pid!r} is not a person of the family")
        elif not _alive(person, spec.year):
            errors.append(f"{where}: {pid} was not alive in {spec.year} (born {person.born}, died {person.died})")
    authors = [t.author_id for t in tellings]
    if len(set(authors)) != len(authors):
        errors.append(f"{where}: one telling per teller")
    narrators = demo.tellers(screenplay)
    if narrators != set(authors):
        errors.append(f"{where}: the screenplay is narrated by {sorted(narrators)}, but the tellings are by "
                      f"{sorted(set(authors))} (the first telling's teller tells the shots' narration, every "
                      "other teller needs a track in \"narrations\")")
    elif default_author(screenplay) != authors[0]:
        errors.append(f"{where}: the shots' narration should be {authors[0]}'s, the first telling's teller")

    sets: dict[str, schemas.SetDef] = {}
    props: dict[str, schemas.Prop] = {}
    set_ids = list(dict.fromkeys(shot.set for shot in screenplay.shots))
    for sid in set_ids:
        found = scene.set(sid)
        if found is None:
            errors.append(f"{where}: no set {sid!r} in {SETS_DIR}/ (or the demo family's sets)")
        else:
            sets[sid] = found
    for pid in dict.fromkeys(screenplay.props):
        found = scene.prop(pid)
        if found is None:
            errors.append(f"{where}: no prop {pid!r} in {PROPS_DIR}/ (or the library or the demo family's props)")
        else:
            props[pid] = found
    if len(errors) > start:
        return None
    try:
        result = compiler.compile(screenplay, sets=sets, props=props,
                                  people={pid: people[pid] for pid in screenplay.cast})
    except Exception as exc:  # noqa: BLE001 - a compiler crash is a compile error, named
        errors.append(f"{where}: the compiler crashed ({type(exc).__name__}: {exc})")
        return None
    if not result.ok or result.timeline is None:
        lines = "".join(f"\n      [{e.code}] {e.message}" for e in result.errors) or " no timeline"
        errors.append(f"{where}: the screenplay does not compile:{lines}")
        return None
    return Moment(
        id=memory_id, title=spec.title.strip(), year=spec.year, participant_ids=participants, tellings=tellings,
        screenplay=screenplay, timeline=result.timeline, prop_ids=list(dict.fromkeys(screenplay.props)),
        set_ids=set_ids, reused=spec.reuse.screenplay if spec.reuse else None,
    )


# ------------------------------------------------------------------------------ the family


def load_family(directory: Path) -> FamilyData:
    """Load, check and compile a data directory. Raises WhitakerDataError listing every problem."""
    root = Path(directory).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    if not root.is_dir():
        raise WhitakerDataError(root, ["the data directory does not exist (set STITCH_WHITAKERS_DIR or --data-dir)"])
    raw = _read_json(root / FAMILY_FILE, errors, FAMILY_FILE)
    if raw is None:
        raise WhitakerDataError(root, errors)
    try:
        spec = FamilyFile.model_validate(raw)
    except ValidationError as exc:
        raise WhitakerDataError(root, [f"{FAMILY_FILE}: {e}" for e in _pydantic_lines(exc).split("; ")]) from exc

    family_id = spec.family.id
    if not _ID.match(family_id):
        errors.append(f"{FAMILY_FILE}: family.id must be letters, digits, _ or -")
    if family_id == demo.DEMO_FAMILY_ID:
        errors.append(f"{FAMILY_FILE}: family.id {family_id!r} is the demo family (Maos)")
    if not spec.family.name.strip():
        errors.append(f"{FAMILY_FILE}: family.name is blank")

    accounts = _accounts(spec, family_id, errors)
    people = _people(spec, family_id, errors)
    contacts = _contacts(spec, errors)
    files = _Files(root, family_id, errors, warnings)
    props = _props(root, family_id, files, errors)
    sets = _sets(root, family_id, files, errors)
    scene = _Scene(people={p.id: p for p in people}, props=props, sets=sets, family_id=family_id)
    for person in people:
        item = person.character.item_prop_id
        if item is not None and scene.prop(item) is None:
            errors.append(f"people: {person.id}'s itemPropId {item!r} is not a prop in {PROPS_DIR}/")

    moments: list[Moment] = []
    moments_dir = root / MOMENTS_DIR
    if moments_dir.is_dir():
        for folder in sorted(p for p in moments_dir.iterdir() if p.is_dir()):
            if not (folder / MOMENT_FILE).is_file():
                warnings.append(f"{MOMENTS_DIR}/{folder.name}: no {MOMENT_FILE}; skipped")
                continue
            moment = _moment(folder, root, scene, files, errors)
            if moment is not None:
                moments.append(moment)
    else:
        warnings.append(f"no {MOMENTS_DIR}/ folder: the family has no moments")
    moments.sort(key=lambda m: (m.year, m.id))

    memory_ids = {m.id for m in moments}
    questions = []
    for i, q in enumerate(spec.questions):
        where = f"questions[{i}]"
        if q.person_id not in scene.people:
            errors.append(f"{where}: personId {q.person_id!r} is not a person")
        if q.memory_id is not None and q.memory_id not in memory_ids:
            errors.append(f"{where}: memoryId {q.memory_id!r} is not a moment")
        if not q.text.strip():
            errors.append(f"{where}: text is blank")
        questions.append(Question(question_id(family_id, i + 1), q.person_id, q.text.strip(), q.memory_id))

    photos_dir = root / PHOTOS_DIR
    if photos_dir.is_dir():
        unused = sorted(p.name for p in photos_dir.iterdir()
                        if p.is_file() and not p.name.startswith(".") and p.name not in files.photo_ids)
        if unused:
            warnings.append(f"{PHOTOS_DIR}/: not used by any telling: {', '.join(unused)}")

    if errors:
        raise WhitakerDataError(root, errors)
    return FamilyData(
        directory=root,
        family_id=family_id,
        name=spec.family.name.strip(),
        accounts=accounts,
        people=people,
        props=[*props.values(), *scene.borrowed_props.values()],
        sets=[*sets.values(), *scene.borrowed_sets.values()],
        media=list(files.media.values()),
        photo_ids=dict(files.photo_ids),
        moments=moments,
        questions=questions,
        contacts=contacts,
        warnings=warnings,
    )


def summary(data: FamilyData) -> str:
    """One line for the banner: counts only, never names, emails or text."""
    photos = sum(1 for m in data.media if m.kind == "photo")
    art = sum(1 for m in data.media if m.kind == "cutout")
    return (f"{len(data.people)} people, {len(data.moments)} moments, {len(data.accounts)} accounts, "
            f"{photos} photos, {art} art files")
