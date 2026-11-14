"""find_prop, generate_prop, confirm_props: the prop master's tools (ARCHITECTURE.md
section 1 "Props" and section 2).

Every tool is built by ``make_prop_tools(repo, run, art)`` and closes over
``run.family_id``: no tool schema has a family id, so nothing the model writes can read
or change another family's props or media.

- ``find_prop`` searches the family's saved props, then the built-in library.
- ``generate_prop`` draws a new prop in the locked style, cuts it out, auto-rigs it
  (anchor from the shape, grip hint snapped to the shape's edge), optionally draws
  separate moving parts and state images, and saves it. With no art service, or when a
  call fails, it saves a placeholder card (``art: null`` plus a ``color``) so the story can
  always be staged.
- ``confirm_props`` is the family's review. The studio gates it with
  ``ask.INTERRUPT_ON``, so the run pauses before it executes; runs.py turns that pause
  into the ``props_found`` event and the family's answer into an ``edit`` decision whose
  args carry ``edits`` (``ask.answer_to_resume``). The tool then applies those edits.
  It emits no event itself.

Invalid input from the model (a size outside the vocabulary, part coordinates outside
the image, a photo that is not one of this story's) comes back as an ``error: ...``
string, never an exception, so the model can correct itself.
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
import threading
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, ValidationError

import schemas
from agent.repo import AgentRepo
from agent.run_info import RunInfo
from agent.tools.art import ArtService, Cutout, build_art_prompt, placeholder_color, snap_grip
from agent.tools.ask import CONFIRM_PROPS
from agent.tools.family import era_for_year
from screenplay.schema import VOCABULARY

log = logging.getLogger(__name__)

SIZES: tuple[str, ...] = tuple(VOCABULARY["sizes"])
FIND_LIMIT = 5
ID_MAX_CHARS = 40
NAME_MAX_CHARS = 80
MAX_PARTS = 6
MAX_STATES = 4
BODY = "body"
NO_MATCH = "no saved or library prop matches; generate one"

_SNAKE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


# --------------------------------------------------------------------------- #
# Tool argument schemas
# --------------------------------------------------------------------------- #


class PartSpec(BaseModel):
    """One separately moving piece of a generated prop (a lid, a wheel, a door)."""

    name: str = Field(description="snake_case part name, e.g. 'lid' or 'wheel_front'. Not 'body'.")
    description: str = Field(description="What the part looks like on its own, e.g. 'a round black tire'.")
    at: list[float] = Field(
        description="[u, v] where the part's pivot sits on the body image: fractions 0..1, [0, 0] top-left."
    )
    spin: bool = Field(default=False, description="True when the part spins around its center, like a wheel.")


class StateSpec(BaseModel):
    """One visual mode of a prop (open, closed, lit)."""

    name: str = Field(description="snake_case state name, e.g. 'open' or 'closed'.")
    description: str = Field(
        description=(
            "How the prop looks in this state, e.g. 'lid open, hymn sheets visible'. "
            "Leave empty for the state the body image already shows."
        )
    )


class FindPropArgs(BaseModel):
    description: str = Field(description="The object as the story describes it, e.g. 'dad's old trumpet case'.")


class GeneratePropArgs(BaseModel):
    name: str = Field(description="Short name, e.g. 'trumpet case'.")
    description: str = Field(
        description="One line on how it looks, from the story and photos. Describe the body without its parts."
    )
    size: str = Field(description=f"Size class, one of: {', '.join(SIZES)}.")
    grip: list[float] | None = Field(
        default=None,
        description=(
            "[u, v] point hint where a hand holds it, as image fractions with [0, 0] top-left "
            "(e.g. [0.5, 0.0] for a handle on top). Snapped to the nearest edge of the shape. "
            "Omit for things nobody holds."
        ),
    )
    reference_photo_id: str | None = Field(
        default=None, description="Id of one of this story's photos that shows the real object."
    )
    parts: list[PartSpec] | None = Field(
        default=None, description="Separate moving parts, only when the story moves them."
    )
    states: list[StateSpec] | None = Field(
        default=None, description="Visual states, only when the story changes them."
    )


class ConfirmPropsArgs(BaseModel):
    props: list[schemas.PropChip] = Field(
        description=(
            "Every prop of the scene, once: "
            "{id, name, description, size, art (its preview media id or null), reused}."
        )
    )
    edits: list[schemas.PropEdit] | None = Field(
        default=None, description="Leave empty. The family's review fills this in."
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def prop_slug(name: str) -> str:
    """A snake_case id for ``name``: ascii a-z0-9_ only, at most 40 characters."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:ID_MAX_CHARS].strip("_")
    return slug or "prop"


def era_label(year: int) -> str | None:
    """Words for the era of ``year`` (vocabulary eras): '1980s', or 'before 1940'."""
    era = era_for_year(year)
    if era is None:
        return None
    if era.startswith("pre") and era[3:].isdigit():
        return f"before {era[3:]}"
    return era


def _finite_pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value):
        return None
    return float(value[0]), float(value[1])


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


def _round(values: Iterable[float]) -> list[float]:
    return [round(v, 4) for v in values]


def _body_ref(prop: schemas.Prop) -> str | None:
    return prop.art.get(BODY)


_STOPWORDS = frozenset(
    "the and with for from her his their our old new that this was were its one some very".split()
)


def _words(text: str) -> set[str]:
    """Lowercase content words, with a trailing plural 's' dropped so 'books' meets 'book'."""
    words = (w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in _STOPWORDS)
    return {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words}


def search_props(props: Sequence[schemas.Prop], text: str, *, limit: int = FIND_LIMIT) -> list[schemas.Prop]:
    """Word-overlap match of ``text`` against prop names and descriptions.

    The family's own props rank before library props (family_id None), then by how many
    words match. Used when the repo has no ``find_props`` of its own.
    """
    wanted = _words(text)
    if not wanted or limit <= 0:
        return []
    scored: list[tuple[int, int, str, schemas.Prop]] = []
    for prop in props:
        name_words = _words(prop.name.replace("_", " ")) | _words(prop.id.replace("_", " "))
        words = name_words | _words(prop.description or "")
        hits = len(wanted & words)
        if hits == 0:
            continue
        name_hits = len(wanted & name_words)
        library = 1 if prop.family_id is None else 0
        scored.append((library, -(hits + name_hits), prop.id, prop))
    scored.sort(key=lambda s: s[:3])
    return [s[3] for s in scored[:limit]]


def _find(repo: AgentRepo, family_id: str, text: str, limit: int) -> list[schemas.Prop]:
    finder = getattr(repo, "find_props", None)
    if callable(finder):
        return list(finder(family_id, text, limit=limit))
    return search_props(repo.list_props(family_id), text, limit=limit)


def _validated(prop: schemas.Prop, **update: Any) -> schemas.Prop:
    """Apply ``update`` and re-validate, so an edit can never save an invalid prop."""
    data = prop.model_dump(mode="json", by_alias=False)
    data.update(update)
    return schemas.Prop.model_validate(data)


def _image_size(data: bytes) -> tuple[int, int] | None:
    """Pixel size of an image after its EXIF orientation, or None when it cannot be read."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            upright = ImageOps.exif_transpose(im)
            w, h = upright.size
    except Exception:  # noqa: BLE001 - any unreadable upload
        return None
    return (w, h) if w > 0 and h > 0 else None


def _summary(prop: schemas.Prop) -> dict[str, Any]:
    return {
        "id": prop.id,
        "name": prop.name,
        "description": prop.description,
        "size": prop.size,
        "source": prop.source,
        "aspect": round(prop.aspect, 4),
        "grip": _round(prop.grip) if prop.grip is not None else None,
        "parts": list(prop.parts),
        "states": list(prop.states),
        "preview": _body_ref(prop),
    }


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #


def make_prop_tools(repo: AgentRepo, run: RunInfo, art: ArtService) -> list[BaseTool]:
    """Build [find_prop, generate_prop, confirm_props] for one run, bound to ``run.family_id``."""
    family_id = run.family_id
    allowed_photos = frozenset(run.photo_ids)
    id_lock = threading.Lock()

    def put_png(png: bytes) -> str:
        return repo.put_media(family_id, png, "image/png", kind="cutout")

    def unique_id(name: str) -> str:
        base = prop_slug(name)
        candidate, n = base, 2
        while repo.get_prop(family_id, candidate) is not None:
            suffix = f"_{n}"
            candidate = base[: ID_MAX_CHARS - len(suffix)].rstrip("_") + suffix
            n += 1
        return candidate

    # ---------------------------------------------------------------- find_prop

    def find_prop(description: str) -> str:
        """Search this family's saved props, then the built-in library, for an object the
        story mentions. Returns a JSON list of matches (id, name, description, size,
        source, aspect, grip, parts, states, and preview: the body's media id or null),
        best first, or a note that nothing matches so you should generate one."""
        text = " ".join(str(description).split())
        if not text:
            return "error: describe the object to look for"
        try:
            found = _find(repo, family_id, text, FIND_LIMIT)
        except Exception as exc:  # noqa: BLE001 - a failed search must not end the run
            log.warning("find_prop failed: %s: %s", type(exc).__name__, exc)
            return "error: the prop search failed; generate the prop instead"
        if not found:
            return NO_MATCH
        return json.dumps([_summary(p) for p in found])

    # ------------------------------------------------------------ generate_prop

    def _generate(
        name: str,
        description: str,
        size: str,
        grip: Any,
        reference_photo_id: str | None,
        parts: Sequence[PartSpec | dict[str, Any]] | None,
        states: Sequence[StateSpec | dict[str, Any]] | None,
    ) -> str:
        name = " ".join(str(name).split())[:NAME_MAX_CHARS].strip()
        description = " ".join(str(description).split())
        if not name:
            return "error: the prop needs a name"
        if size not in SIZES:
            return f"error: size {size!r} is not a size class; use one of {', '.join(SIZES)}"

        hint: tuple[float, float] | None = None
        if grip is not None:
            pair = _finite_pair(grip)
            if pair is None:
                return "error: grip must be two numbers [u, v] (image fractions, [0, 0] top-left) or omitted"
            hint = (_clamp01(pair[0]), _clamp01(pair[1]))

        part_specs: list[PartSpec] = []
        for raw in parts or []:
            try:
                spec = raw if isinstance(raw, PartSpec) else PartSpec.model_validate(raw)
            except ValidationError:
                return "error: each part needs a name, a description and at: [u, v]"
            if not _SNAKE.match(spec.name) or spec.name == BODY:
                return f"error: part name {spec.name!r} must be snake_case (a-z, 0-9, _) and not 'body'"
            if any(p.name == spec.name for p in part_specs):
                return f"error: part {spec.name!r} is listed twice"
            at = _finite_pair(spec.at)
            if at is None or not all(0.0 <= x <= 1.0 for x in at):
                return f"error: part {spec.name!r} needs at: [u, v] with both numbers between 0 and 1"
            part_specs.append(spec)
        if len(part_specs) > MAX_PARTS:
            return f"error: at most {MAX_PARTS} parts per prop"

        state_specs: list[StateSpec] = []
        for raw in states or []:
            try:
                spec_s = raw if isinstance(raw, StateSpec) else StateSpec.model_validate(raw)
            except ValidationError:
                return "error: each state needs a name and a description"
            if not _SNAKE.match(spec_s.name):
                return f"error: state name {spec_s.name!r} must be snake_case (a-z, 0-9, _)"
            if any(s.name == spec_s.name for s in state_specs):
                return f"error: state {spec_s.name!r} is listed twice"
            state_specs.append(spec_s)
        if len(state_specs) > MAX_STATES:
            return f"error: at most {MAX_STATES} states per prop"

        references: tuple[bytes, ...] = ()
        if reference_photo_id is not None:
            if reference_photo_id not in allowed_photos:
                return f"error: unknown photo {reference_photo_id!r}; use one of this story's photo ids or none"
            stored = repo.get_media(family_id, reference_photo_id)
            if stored is None:
                return f"error: unknown photo {reference_photo_id!r}; use one of this story's photo ids or none"
            data, content_type = stored
            if content_type and not content_type.startswith("image/"):
                return f"error: {reference_photo_id!r} is not an image"
            references = (data,)

        era = era_label(run.fields.year)
        cutout: Cutout | None = art.make_cutout(
            build_art_prompt(f"{name}: {description}", kind="prop", era=era),
            reference_images=references,
        )

        art_map: dict[str, str | None] = {BODY: None}
        prop_parts: dict[str, schemas.PropPart] = {}
        prop_states: dict[str, dict[str, str]] = {}
        aspect, anchor = 1.0, [0.5, 1.0]
        grip_point: list[float] | None = list(hint) if hint is not None else None

        if cutout is not None:
            art_map[BODY] = put_png(cutout.png)
            aspect, anchor = cutout.aspect, list(cutout.anchor)
            if hint is not None:
                grip_point = snap_grip(cutout.png, hint)
            # Parts and states are drawn with the body as a reference so they match it.
            related = (cutout.png, *references)
            for part in part_specs:
                piece = art.make_cutout(
                    build_art_prompt(f"{name}, {part.name}: {part.description}", kind="part", era=era),
                    reference_images=related,
                )
                art_map[part.name] = put_png(piece.png) if piece is not None else None
            for state in state_specs:
                if not state.description.strip():
                    prop_states[state.name] = {}
                    continue
                look = art.make_cutout(
                    build_art_prompt(f"{name}, {state.name}: {state.description}", kind="state", era=era),
                    reference_images=related,
                )
                prop_states[state.name] = {BODY: put_png(look.png)} if look is not None else {}
        else:
            # Placeholder card: every part key exists with no art; states change nothing.
            for part in part_specs:
                art_map[part.name] = None
            for state in state_specs:
                prop_states[state.name] = {}

        for part in part_specs:
            u, v = _finite_pair(part.at)  # type: ignore[misc] - validated above
            prop_parts[part.name] = schemas.PropPart(art=part.name, at=[u, v], spin=part.spin)

        fields: dict[str, Any] = dict(
            family_id=family_id,
            name=name,
            description=description or None,
            source="generated",
            size=size,
            art=art_map,
            color=placeholder_color(name),
            aspect=aspect,
            anchor=anchor,
            grip=grip_point,
            parts=prop_parts,
            states=prop_states,
        )
        # The model may generate several props in parallel (tool calls run on threads):
        # pick the id and save under one lock so two props with the same name never share an id.
        with id_lock:
            saved = repo.save_prop(family_id, schemas.Prop(id=unique_id(name), **fields))
        return json.dumps(
            {
                "id": saved.id,
                "name": saved.name,
                "size": saved.size,
                "art": "generated" if cutout is not None else "placeholder",
                "preview": _body_ref(saved),
                "aspect": round(saved.aspect, 4),
                "anchor": _round(saved.anchor),
                "grip": _round(saved.grip) if saved.grip is not None else None,
                "parts": list(saved.parts),
                "states": list(saved.states),
            }
        )

    def generate_prop(
        name: str,
        description: str,
        size: str,
        grip: list[float] | None = None,
        reference_photo_id: str | None = None,
        parts: list[PartSpec] | None = None,
        states: list[StateSpec] | None = None,
    ) -> str:
        """Draw a new paper prop in the house style, cut it out, rig it and save it to the
        family. Use only when find_prop has no match. Returns JSON with the prop id, its
        preview media id (null for a placeholder card), aspect, anchor, the snapped grip,
        and the part and state names to use in the screenplay. "art" is "placeholder" when
        no image could be made: the prop still works on stage as a colored card."""
        try:
            return _generate(name, description, size, grip, reference_photo_id, parts, states)
        except Exception as exc:  # noqa: BLE001 - a failed prop must not end the run
            log.warning("generate_prop failed: %s: %s", type(exc).__name__, exc)
            return f"error: could not make the prop ({type(exc).__name__}); try again or simplify it"

    # ------------------------------------------------------------ confirm_props

    def _replace_photo(prop: schemas.Prop, photo_id: str) -> tuple[schemas.Prop | None, str | None]:
        """Return (updated prop, None) or (None, problem)."""
        try:
            stored = repo.get_media(family_id, photo_id)
        except Exception as exc:  # noqa: BLE001 - e.g. a malformed id
            log.warning("confirm_props could not read %r: %s: %s", photo_id, type(exc).__name__, exc)
            stored = None
        if stored is None:
            return None, f"{prop.id}: photo {photo_id!r} not found; kept the current art"
        data, content_type = stored
        if content_type and not content_type.startswith("image/"):
            return None, f"{prop.id}: {photo_id!r} is not an image; kept the current art"
        cutout = art.cut_out(data)
        if cutout is not None:
            body, aspect, anchor = put_png(cutout.png), cutout.aspect, list(cutout.anchor)
        else:
            size_px = _image_size(data)
            if size_px is None:
                return None, f"{prop.id}: photo {photo_id!r} could not be read; kept the current art"
            body, aspect, anchor = photo_id, size_px[0] / size_px[1], [0.5, 1.0]
        updated = _validated(
            prop,
            family_id=family_id,
            source="photo",
            art={BODY: body},
            aspect=aspect,
            anchor=anchor,
            parts={},
            states={},
        )
        return updated, None

    def _confirm(
        props: Sequence[schemas.PropChip | dict[str, Any]],
        edits: Sequence[schemas.PropEdit | dict[str, Any]] | None,
    ) -> str:
        problems: list[str] = []
        chips: list[schemas.PropChip] = []
        for raw in props:
            try:
                chip = raw if isinstance(raw, schemas.PropChip) else schemas.PropChip.model_validate(raw)
            except ValidationError:
                problems.append(f"skipped a malformed prop chip: {raw!r}"[:200])
                continue
            if any(c.id == chip.id for c in chips):
                continue
            chips.append(chip)
        chip_ids = {c.id for c in chips}

        by_id: dict[str, list[schemas.PropEdit]] = {}
        for raw in edits or []:
            try:
                edit = raw if isinstance(raw, schemas.PropEdit) else schemas.PropEdit.model_validate(raw)
            except ValidationError:
                problems.append(f"ignored a malformed edit: {raw!r}"[:200])
                continue
            if edit.id not in chip_ids:
                problems.append(f"ignored an edit for {edit.id!r}: it is not in the reviewed list")
                continue
            by_id.setdefault(edit.id, []).append(edit)

        lines: list[str] = []
        removed: list[str] = []
        for chip in chips:
            chip_edits = by_id.get(chip.id, [])
            if any(e.action == "remove" for e in chip_edits):
                removed.append(chip.id)
                continue
            prop = repo.get_prop(family_id, chip.id)
            if prop is None:
                problems.append(f"{chip.id}: no saved prop with this id; generate it or leave it out")
                continue
            renamed = photo = False
            for edit in chip_edits:
                if edit.action == "rename":
                    new_name = " ".join((edit.name or "").split())[:NAME_MAX_CHARS].strip()
                    if not new_name:
                        problems.append(f"{chip.id}: rename without a name; kept {prop.name!r}")
                        continue
                    prop = _validated(prop, family_id=family_id, name=new_name)
                    renamed = True
                elif edit.action == "replace_photo":
                    if not edit.photo_id:
                        problems.append(f"{chip.id}: replace_photo without a photo; kept the current art")
                        continue
                    updated, problem = _replace_photo(prop, edit.photo_id)
                    if problem:
                        problems.append(problem)
                        continue
                    assert updated is not None
                    prop = updated
                    photo = True
            if renamed or photo:
                prop = repo.save_prop(family_id, prop)
            status = "photo" if photo else "renamed" if renamed else "kept"
            lines.append(f"{prop.id} | {prop.name} | size={prop.size} | {status}")

        lines.append(f"removed: {', '.join(removed) if removed else 'none'}")
        lines.extend(f"problem: {p}" for p in problems)
        return "\n".join(lines)

    def confirm_props(
        props: list[schemas.PropChip],
        edits: list[schemas.PropEdit] | None = None,
    ) -> str:
        """Show every prop of the scene to the family for review, once, with all props
        together. The run pauses until they answer. The result has one line per kept prop
        "<propId> | <name> | size=<size> | kept|renamed|photo", then "removed: <ids>"
        (removed props leave the scene), then any problems. Use the names and ids it returns."""
        try:
            return _confirm(props, edits)
        except Exception as exc:  # noqa: BLE001 - the review must not end the run
            log.warning("confirm_props failed: %s: %s", type(exc).__name__, exc)
            return f"error: could not apply the review ({type(exc).__name__}); keep the props as they were"

    return [
        StructuredTool.from_function(func=find_prop, name="find_prop", args_schema=FindPropArgs),
        StructuredTool.from_function(func=generate_prop, name="generate_prop", args_schema=GeneratePropArgs),
        StructuredTool.from_function(func=confirm_props, name=CONFIRM_PROPS, args_schema=ConfirmPropsArgs),
    ]


__all__ = [
    "NO_MATCH",
    "SIZES",
    "ConfirmPropsArgs",
    "FindPropArgs",
    "GeneratePropArgs",
    "PartSpec",
    "StateSpec",
    "era_label",
    "make_prop_tools",
    "prop_slug",
    "search_props",
]
