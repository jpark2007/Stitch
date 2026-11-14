"""find_set, generate_set_layer: the set designer's tools (ARCHITECTURE.md section 1, Sets).

A set is one place: paper layers at depth 0 (back), 1 (middle) and 2 (front), plus named
marks where the puppets stand (shared/CONTRACTS.md, Marks). Both tools are built by
`make_set_tools(repo, run, art)` and close over the run's family id, so no tool schema
has a family id parameter and nothing the model writes can reach another family.

Layer heights follow the reference sample (`web/public/samples/hymn_books.memory.json`),
which the stage and the compiler are built and tested against: back 1.0, middle 0.45,
front 0.18 stage units. The middle layer holds the main structure the actors stand on
(the sample's `steps_top` is at h = 0.3), and the front layer stays well under a puppet's
height (0.5) so it frames the stage without hiding the actors. (The task brief's 0.7 and
0.35 differ from the sample; a 0.35 front layer would cover most of a 0.5 puppet.)

Ids: a generated set's id is a snake_case slug of the place, unique within the family only
(another family can use the same slug). A set that already exists in the family and was
not made by this run is never overwritten or merged into: generation takes a fresh
suffixed id (`_2`, `_3`, ...) instead. Reusing a saved place means using `find_set`'s id.
Later calls for the same place in the same run update the set this run made.

Concurrent runs: a set is saved only after the image model returns, seconds after its id
was picked. The id is therefore claimed in the process-wide registry
(`agent.tools.claims`, kind "set", owner the run id) when it is picked and released once
the set is saved, so a second run of the same family that names the same place meanwhile
takes the next suffix rather than the same id (after the save, the saved set itself
makes it skip the id). As a second line of defence, the save never merges into a set this run did
not save itself: should one appear at the reserved id anyway (a writer outside the
registry, such as another API process; see the claims module on the single-process
assumption), the layer is saved under a freshly claimed id, with a warning in the log,
and the tool result reports the id actually used.
"""

from __future__ import annotations

import itertools
import json
import logging
import math
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool

from agent.repo import AgentRepo
from agent.run_info import RunInfo
from agent.tools.art import ArtService, build_art_prompt, placeholder_color
from agent.tools.claims import claim_id, owner_of, release_claim
from agent.tools.family import era_for_year
from schemas import SetDef, SetLayer

log = logging.getLogger(__name__)

LayerName = Literal["back", "middle", "front"]

LAYER_DEPTH: dict[str, int] = {"back": 0, "middle": 1, "front": 2}
LAYER_HEIGHT: dict[str, float] = {"back": 1.0, "middle": 0.45, "front": 0.18}
"""Stage units when risen; see the module docstring for why these match the sample."""

IMAGE_WIDTH = 2048
"""A layer spans the full stage width (2 units), so its image is 2048 px wide."""

SET_ID_MAX_CHARS = 40
FIND_LIMIT = 5
NO_MATCH = "no saved place matches; generate one"

BUILTIN_MARK_NAMES = frozenset({"offstage_left", "offstage_right"})
MARK_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
MARK_U_MAX = 1.0
MARK_H_MAX = 1.5
MARK_D_MAX = 1.0


# ------------------------------------------------------------------------ helpers


def layer_size(height: float) -> tuple[int, int]:
    """Image size for a layer `height` units tall: width / height = 2 / layer height, rounded to 16 px."""
    return IMAGE_WIDTH, max(16, round(IMAGE_WIDTH * height / 2 / 16) * 16)


def slugify(place: str, max_chars: int = SET_ID_MAX_CHARS) -> str:
    """A snake_case ASCII id for a place: "St. Mary's Church Steps" -> "st_marys_church_steps"."""
    no_apostrophes = re.sub(r"['\u2018\u2019`]", "", place)
    ascii_text = unicodedata.normalize("NFKD", no_apostrophes).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    slug = slug[:max_chars].rstrip("_")
    return slug or "place"


def _with_suffix(base: str, n: int) -> str:
    """`base` for n == 1, else `base_<n>`, trimmed so the id stays within SET_ID_MAX_CHARS."""
    if n == 1:
        return base
    suffix = f"_{n}"
    return base[: SET_ID_MAX_CHARS - len(suffix)].rstrip("_") + suffix


def era_label(year: int) -> str | None:
    """A short period label for the art prompt, e.g. "1988, the 1980s"."""
    era = era_for_year(year)
    if era is None:
        return str(year)
    if era.startswith("pre"):
        return f"{year}, before {era.removeprefix('pre')}"
    return f"{year}, the {era}"


def validate_marks(marks: dict[str, Any]) -> tuple[dict[str, list[float]], list[str]]:
    """Check marks against CONTRACTS.md. Returns (clean marks, problems); any problem rejects all."""
    problems: list[str] = []
    clean: dict[str, list[float]] = {}
    if not isinstance(marks, dict):
        return {}, ["marks must be an object of name -> [u, h] or [u, h, d]"]
    for name, value in marks.items():
        if not isinstance(name, str) or not MARK_NAME.match(name):
            problems.append(f"mark name {name!r} must be snake_case, e.g. steps_top")
            continue
        if name in BUILTIN_MARK_NAMES:
            problems.append(f"mark {name!r} is built in; do not set it")
            continue
        if (
            not isinstance(value, (list, tuple))
            or len(value) not in (2, 3)
            or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)
            or not all(math.isfinite(x) for x in value)
        ):
            problems.append(f"mark {name!r} must be [u, h] or [u, h, d] numbers, got {value!r}")
            continue
        u, h, *rest = (float(x) for x in value)
        if not 0.0 <= u <= MARK_U_MAX:
            problems.append(f"mark {name!r}: u={u:g} must be between 0 and 1 (0 left edge, 1 right edge)")
        if not 0.0 <= h <= MARK_H_MAX:
            problems.append(f"mark {name!r}: h={h:g} must be between 0 and {MARK_H_MAX:g} (0 is the ground)")
        if rest and not 0.0 <= rest[0] <= MARK_D_MAX:
            problems.append(f"mark {name!r}: d={rest[0]:g} must be between 0 and 1")
        clean[name] = [u, h, *rest]
    return clean, problems


def _layer_brief(layer: SetLayer) -> dict[str, Any]:
    return {
        "depth": int(layer.depth) if float(layer.depth).is_integer() else layer.depth,
        "height": layer.height,
        "art": "art" if layer.art else "placeholder",
    }


def _set_brief(set_def: SetDef) -> dict[str, Any]:
    return {
        "id": set_def.id,
        "name": set_def.name,
        "description": set_def.description,
        "layers": [_layer_brief(layer) for layer in sorted(set_def.layers, key=lambda la: la.depth)],
        "marks": set_def.marks,
    }


def _find_sets(repo: AgentRepo, family_id: str, text: str, limit: int) -> list[SetDef]:
    """repo.find_sets when the repo has it; else a word match over list_sets (older repos)."""
    find = getattr(repo, "find_sets", None)
    if callable(find):
        return list(find(family_id, text, limit=limit))
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    scored: list[tuple[int, str, SetDef]] = []
    for set_def in repo.list_sets(family_id):
        haystack = set(re.findall(r"[a-z0-9]+", f"{set_def.name} {set_def.description or ''} {set_def.id}".lower()))
        score = len(words & haystack)
        if score:
            scored.append((-score, set_def.id, set_def))
    scored.sort(key=lambda s: (s[0], s[1]))
    return [s for _, _, s in scored[:limit]]


@dataclass
class _RunSets:
    """What this run has generated: place slug -> set id, each set's layer descriptions, and
    the ids this run has saved (the only sets a later call may merge into)."""

    by_slug: dict[str, str] = field(default_factory=dict)
    descriptions: dict[str, dict[int, str]] = field(default_factory=dict)
    saved: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def created(self) -> set[str]:
        return set(self.by_slug.values())


# -------------------------------------------------------------------------- tools


def make_set_tools(repo: AgentRepo, run: RunInfo, art: ArtService) -> list[BaseTool]:
    """[find_set, generate_set_layer] for one run, bound to run.family_id."""
    family_id = run.family_id
    allowed_photos = frozenset(run.photo_ids)
    era = era_label(run.fields.year)
    state = _RunSets()

    def _claim_new_id(base: str) -> str:
        """Claim the first free id of `base`, `base_2`, ... for this run (call with state.lock held).

        Free means: not claimed by another run, not used by this run for another place and
        not saved in the family.
        """
        created = state.created
        return claim_id(
            family_id,
            "set",
            run.run_id,
            (_with_suffix(base, n) for n in itertools.count(1)),
            lambda candidate: candidate in created or repo.get_set(family_id, candidate) is not None,
        )

    def _reserve_id(place: str) -> str:
        """The set id for `place` in this run (call with state.lock held)."""
        base = slugify(place)
        if base not in state.by_slug:
            state.by_slug[base] = _claim_new_id(base)
        return state.by_slug[base]

    def _reference_images(photo_id: str | None) -> tuple[list[bytes], str | None]:
        if photo_id is None:
            return [], None
        if photo_id not in allowed_photos:
            return [], f"error: {photo_id!r} is not one of this telling's photos; nothing was saved"
        stored = repo.get_media(family_id, photo_id)
        if stored is None:
            return [], f"error: photo {photo_id!r} could not be read; nothing was saved"
        data, content_type = stored
        if content_type and not content_type.startswith("image/"):
            return [], f"error: {photo_id!r} is not an image ({content_type}); nothing was saved"
        return [data], None

    @tool(parse_docstring=True)
    def find_set(place: str) -> str:
        """Search this family's saved places (sets) before generating one. Returns id, name, description, layers and marks of up to 5 matches.

        Args:
            place: The place in plain words, e.g. "the church steps" or "Grandma's kitchen".
        """
        found = _find_sets(repo, family_id, place, FIND_LIMIT)
        if not found:
            return NO_MATCH
        return json.dumps([_set_brief(s) for s in found], ensure_ascii=False)

    @tool(parse_docstring=True)
    def generate_set_layer(
        place: str,
        layer: LayerName,
        description: str,
        marks: dict[str, list[float]] | None = None,
        reference_photo_id: str | None = None,
    ) -> str:
        """Generate one paper layer of a new place and save it to the place's set. Call once per layer (back, middle, front) with the same place; marks given on any call are merged into the set.

        Args:
            place: The place's name, the same on every call for one place, e.g. "St. Mary's church steps".
            layer: "back" (far background, full height), "middle" (the main structure) or "front" (low foreground pieces).
            description: What this layer shows, from the story and photos only, e.g. "brick church front with wide stone steps".
            marks: Named places where people stand, name -> [u, h] or [u, h, d]. u is 0 (left) to 1 (right), h is the standing height (0 is the ground, at most 1.5), d is the optional depth 0 to 1. Names are snake_case, e.g. {"steps_top": [0.6, 0.3]}.
            reference_photo_id: Optional id of one of this telling's photos to draw the layer from.
        """
        if layer not in LAYER_DEPTH:
            return f"error: layer must be 'back', 'middle' or 'front', got {layer!r}; nothing was saved"
        if not place.strip():
            return "error: place must not be empty; nothing was saved"
        new_marks, problems = validate_marks(marks or {})
        if problems:
            return "error: bad marks; nothing was saved: " + "; ".join(problems)
        references, error = _reference_images(reference_photo_id)
        if error:
            return error

        with state.lock:
            set_id = _reserve_id(place)

        depth = LAYER_DEPTH[layer]
        height = LAYER_HEIGHT[layer]
        prompt = build_art_prompt(description, kind="set_layer", layer=layer, era=era)
        cutout = art.make_layer(prompt, layer=layer, reference_images=references, size=layer_size(height))
        art_ref: str | None = None
        if cutout is not None:
            try:
                art_ref = repo.put_media(family_id, cutout.png, "image/png", kind="cutout")
            except Exception as exc:  # noqa: BLE001 - a storage failure falls back to a placeholder
                log.warning("could not store set layer art, using placeholder: %s: %s", type(exc).__name__, exc)
        if art_ref is not None:
            new_layer = SetLayer(art=art_ref, depth=depth, height=height)
        else:
            new_layer = SetLayer(art=None, depth=depth, height=height, color=placeholder_color(f"{place} {layer}"))

        with state.lock:
            # Re-read the reservation: a parallel call for this place may have moved it.
            base = slugify(place)
            set_id = state.by_slug.get(base, set_id)
            existing = repo.get_set(family_id, set_id)
            if existing is not None and set_id not in state.saved:
                # A set this run never saved sits at the reserved id, so someone else wrote
                # it (a writer outside the claim registry). Never merge into it: move this
                # run's set to a fresh id, which later calls for the place then use too.
                log.warning(
                    "set %r in family %r appeared while run %s drew its layer (claimed by %s); "
                    "saving under a new id instead of merging into it",
                    set_id, family_id, run.run_id, owner_of(family_id, "set", set_id) or "nobody",
                )
                set_id = _claim_new_id(base)
                state.by_slug[base] = set_id
                existing = None
            layers = [la for la in (existing.layers if existing else []) if la.depth != depth]
            layers.append(new_layer)
            layers.sort(key=lambda la: la.depth)
            descriptions = state.descriptions.setdefault(set_id, {})
            descriptions[depth] = description.strip()
            set_def = SetDef(
                id=set_id,
                family_id=family_id,
                name=place.strip(),
                description="; ".join(d for _, d in sorted(descriptions.items()) if d) or None,
                layers=layers,
                marks={**(existing.marks if existing else {}), **new_marks},
            )
            saved = repo.save_set(family_id, set_def)
            state.saved.add(saved.id)
            # Saved: the repo now guards the id, and state.saved keeps it this run's.
            release_claim(family_id, "set", run.run_id, saved.id)

        return json.dumps(
            {
                "setId": saved.id,
                "name": saved.name,
                "layers": [_layer_brief(la) for la in saved.layers],
                "marks": saved.marks,
                "art": "generated" if art_ref is not None else "placeholder",
            },
            ensure_ascii=False,
        )

    return [find_set, generate_set_layer]


__all__ = [
    "LAYER_DEPTH",
    "LAYER_HEIGHT",
    "NO_MATCH",
    "era_label",
    "layer_size",
    "make_set_tools",
    "slugify",
    "validate_marks",
]
