"""Props and sets, with vector matching on descriptions.

ARCHITECTURE.md section 5 (`props`, `sets`, the vector index on descriptions) and the
PATCH /families/{fid}/props/{id} semantics in section 6.

Storage: one document per prop or set, camelCase fields. The API `id` (a bare slug
such as 'hymn_books', or a generated ObjectId string) is stored as the plain field `id`,
next to `familyId`, and the storage key is the compound `_id` = "<familyId>:<id>" (see
_key). Ids are therefore unique per family, not globally: two families can each own a
'hymn_books', and neither can reach or block the other's. Every query also filters on
`familyId` as a second line of defence. Documents written before this layout (`_id` =
the bare id, no `id` field) still list with their bare id, but lookups by id only find
them once migrate_legacy_ids has rewritten them; db/client.ensure_indexes runs it on
startup. Each document may also carry an optional
storage-only `embedding` used by the Atlas Vector Search indexes `props_vector` and
`sets_vector` (created by db/client.py).
When no embedding is passed in, saving computes one from "<name>. <description>" with
db.search.embed (the embedder main.py installs from VOYAGE_API_KEY). With no embedder,
or when it fails, nothing is stored and matching uses keywords; saving never fails on it.

Library props are hand-rigged JSON files in shared/props/. They are not stored per
family; they are merged into get_prop, get_props and find_props, and they can never be
patched. A family's saved prop with the same id shadows the library prop.

Every function that reads family data takes `family_id` and filters on it. Not found
or another family's id returns None (or is skipped), never raises.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import TypeVar

import bson
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, OperationFailure

import schemas
from db import media, search

log = logging.getLogger(__name__)

LIBRARY_DIR = Path(__file__).resolve().parents[2] / "shared" / "props"

PROPS = "props"
SETS = "sets"
PROPS_VECTOR_INDEX = "props_vector"
SETS_VECTOR_INDEX = "sets_vector"

M = TypeVar("M", schemas.Prop, schemas.SetDef)


# ------------------------------------------------------------------------- library


@functools.lru_cache(maxsize=8)
def _load_library(directory: Path) -> dict[str, schemas.Prop]:
    props: dict[str, schemas.Prop] = {}
    if not directory.is_dir():
        return props
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
            if not isinstance(raw, dict):
                raise ValueError(f"expected a JSON object, got {type(raw).__name__}")
            raw.setdefault("id", path.stem)
            raw["familyId"] = None
            raw.pop("family_id", None)
            prop = schemas.Prop.model_validate(raw)
        except (ValueError, TypeError) as exc:  # json and pydantic errors
            raise ValueError(f"invalid library prop {path.name}: {exc}") from exc
        if prop.id in props:
            raise ValueError(f"duplicate library prop id {prop.id!r} in {path.name}")
        props[prop.id] = prop
    return props


def library_props(directory: Path | str | None = None) -> dict[str, schemas.Prop]:
    """Built-in props from shared/props/*.json, keyed by id, with family_id None.

    Cached per directory. `directory` (or reassigning the module-level LIBRARY_DIR)
    points it elsewhere, e.g. a tmp dir in tests. Returns copies, so callers may mutate.
    """
    loaded = _load_library(Path(directory if directory is not None else LIBRARY_DIR).resolve())
    return {pid: prop.model_copy(deep=True) for pid, prop in loaded.items()}


library_props.cache_clear = _load_library.cache_clear  # type: ignore[attr-defined]


# ------------------------------------------------------------------ doc <-> model


def _key(family_id: str, api_id: str) -> str:
    """The storage `_id` of a family's prop or set: "<familyId>:<api id>"."""
    return f"{family_id}:{api_id}"


def _api_id(doc: dict) -> str:
    """The API id of a stored document: its `id` field, or for a legacy document (no `id`,
    `_id` = the bare id) its `_id`, with any leading "<familyId>:" removed."""
    if doc.get("id") is not None:
        return str(doc["id"])
    raw = str(doc["_id"])
    family_id = doc.get("familyId")
    if family_id is not None:
        raw = raw.removeprefix(f"{family_id}:")
    return raw


def _to_doc(model: schemas.Prop | schemas.SetDef, family_id: str, api_id: str,
            embedding: list[float] | None) -> dict:
    doc = model.model_dump(by_alias=True, mode="json")
    doc["_id"] = _key(family_id, api_id)
    doc["id"] = api_id
    doc["familyId"] = family_id
    if embedding is not None:
        doc["embedding"] = [float(x) for x in embedding]
    return doc


def _from_doc(cls: type[M], doc: dict) -> M:
    """The model for a stored document: `id` is the API id (never the compound `_id`), and
    only the model's fields are kept (drops `embedding`)."""
    allowed = {f.alias or name for name, f in cls.model_fields.items()}
    data = {k: v for k, v in doc.items() if k in allowed}
    data["id"] = _api_id(doc)
    return cls.model_validate(data)


def _query(family_id: str, api_id: str) -> dict:
    """Filter for one family's document: the compound key, and familyId as defence."""
    return {"_id": _key(family_id, api_id), "familyId": family_id}


_NO_EMBEDDING = {"embedding": 0}


def embedding_text(model: schemas.Prop | schemas.SetDef) -> str:
    """The text a prop's or set's vector is computed from: "<name>. <description>"."""
    name = (model.name or "").strip()
    description = (model.description or "").strip()
    return f"{name}. {description}" if description else name


def _compute_embedding(model: schemas.Prop | schemas.SetDef) -> list[float] | None:
    """The vector for model from the installed embedder, or None. Never raises."""
    try:
        return search.embed(embedding_text(model))
    except Exception:  # noqa: BLE001 - a save must never fail because of its vector
        return None


def _insert(db: Database, coll: str, family_id: str, model: M, api_id: str | None,
            embedding: list[float] | None) -> M:
    api_id = api_id or str(bson.ObjectId())
    if embedding is None:
        embedding = _compute_embedding(model)
    doc = _to_doc(model, family_id, api_id, embedding)
    try:
        db[coll].insert_one(doc)
    except DuplicateKeyError as exc:
        # The key is per family, so this is a duplicate within family_id only.
        raise ValueError(f"{coll[:-1]} id {api_id!r} already exists") from exc
    return _from_doc(type(model), doc)


def _replace(db: Database, coll: str, family_id: str, model: M,
             embedding: list[float] | None) -> M | None:
    current = db[coll].find_one(_query(family_id, model.id))
    if current is None:
        return None
    if embedding is None:
        # The vector is computed from the description (ARCHITECTURE.md section 5), so it
        # is kept while the description is unchanged (a rename keeps it, as in
        # update_prop). Otherwise it is recomputed; with no embedder it is dropped
        # rather than left stale.
        if "embedding" in current and current.get("description") == model.description:
            embedding = current["embedding"]
        else:
            embedding = _compute_embedding(model)
    doc = _to_doc(model, family_id, model.id, embedding)
    result = db[coll].replace_one(_query(family_id, model.id), doc)
    if result.matched_count == 0:
        return None
    return _from_doc(type(model), doc)


def _get(db: Database, coll: str, cls: type[M], family_id: str, api_id: str) -> M | None:
    doc = db[coll].find_one(_query(family_id, api_id), _NO_EMBEDDING)
    return _from_doc(cls, doc) if doc else None


def _get_many(db: Database, coll: str, cls: type[M], family_id: str,
              ids: list[str]) -> dict[str, M]:
    if not ids:
        return {}
    keys = [_key(family_id, i) for i in dict.fromkeys(ids)]
    cursor = db[coll].find({"_id": {"$in": keys}, "familyId": family_id}, _NO_EMBEDDING)
    found = (_from_doc(cls, doc) for doc in cursor)
    return {item.id: item for item in found}


def _list(db: Database, coll: str, cls: type[M], family_id: str) -> list[M]:
    items = [_from_doc(cls, doc) for doc in db[coll].find({"familyId": family_id}, _NO_EMBEDDING)]
    items.sort(key=lambda m: (m.name.casefold(), m.name, m.id))
    return items


# ------------------------------------------------------------------------ matching

_STOPWORDS = frozenset(
    "a an the of and or to in on at for with from by my our your his her their its this that "
    "these those some".split()
)


_VOWELS = frozenset("aeiou")


def _stem(token: str) -> str:
    """Very small plural stripper: books -> book, glasses -> glass, boxes -> box, keys -> key.

    Singular and plural map to the same stem, which need not be a word: '-ies' plurals
    can come from '-y' (cherries, flies) or from '-ie' (cookies, cookie), so '-ies' and
    '-ie' both fold to '-y' (cherry, fly, cooky) and '-y' words stay as they are, short
    ones included (fly, sky). Tokens in other scripts pass through unchanged.
    """
    if token.endswith("ies") and len(token) > 4 and token[-4] not in _VOWELS:
        return token[:-3] + "y"  # cherries, flies, cookies -> cherry, fly, cooky
    if len(token) <= 3:
        return token  # fly, pie, tie, gas
    if token.endswith("ie") and token[-3] not in _VOWELS:
        return token[:-2] + "y"  # cookie, movie -> cooky, movy
    if token.endswith(("sses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


# Letters and digits in any script; underscores and punctuation separate words.
_WORD = re.compile(r"[^\W_]+")


def _tokens(text: str | None) -> list[str]:
    """Word tokens in any script (Hangul, accented Latin, ...), stopwords dropped, stemmed.

    Scripts written without spaces (Chinese, Japanese) come out as one token per run, so
    they match on whole names only.
    """
    if not text:
        return []
    text = unicodedata.normalize("NFKC", text).casefold().replace("'", "").replace("\u2019", "")
    return [_stem(t) for t in _WORD.findall(text) if t not in _STOPWORDS]


def _keyword_score(query: str, name: str, description: str | None) -> float:
    """Plain-text relevance of a prop or set to `query`, 0 = no match.

    Lowercase word tokens with stopwords dropped and plurals stripped. An exact name
    match (same token set) scores above 1.0, above every partial match. Otherwise the
    score is 0.7 * name overlap (shared tokens / the larger token set, so a closer
    name wins) + 0.3 * the share of query tokens found in the description.
    """
    q = set(_tokens(query))
    if not q:
        return 0.0
    n = set(_tokens(name))
    d = set(_tokens(description))
    desc_score = len(q & d) / len(q)
    if n and q == n:
        return 1.0 + 0.1 * desc_score
    name_score = len(q & n) / max(len(q), len(n)) if n else 0.0
    return 0.7 * name_score + 0.3 * desc_score


def _vector_pipeline(index: str, vector: list[float], family_id: str, limit: int) -> list[dict]:
    """Atlas Vector Search pipeline with a familyId pre-filter (and a defensive $match).

    numCandidates and limit stay inside Atlas's bounds (search.vector_sizes).
    """
    num_candidates, limit = search.vector_sizes(limit)
    return [
        {
            "$vectorSearch": {
                "index": index,
                "path": "embedding",
                "queryVector": [float(x) for x in vector],
                "numCandidates": num_candidates,
                "limit": limit,
                "filter": {"familyId": {"$eq": family_id}},
            }
        },
        {"$match": {"familyId": family_id}},
        {"$project": {"embedding": 0}},
    ]


def _vector_search(db: Database, coll: str, index: str, cls: type[M], family_id: str,
                   vector: list[float], limit: int) -> list[M]:
    """Vector results, or [] when $vectorSearch is unavailable (mongomock, plain MongoDB)."""
    try:
        docs = list(db[coll].aggregate(_vector_pipeline(index, vector, family_id, limit)))
    except (OperationFailure, NotImplementedError):
        return []
    return [_from_doc(cls, doc) for doc in docs]


def _keyword_scored(text: str, candidates: list[M]) -> list[tuple[float, M]]:
    """(score, item) for every candidate with score > 0, best first, ties in input order."""
    scored = []
    for order, item in enumerate(candidates):
        score = _keyword_score(text, item.name, item.description)
        if score > 0:
            scored.append((-score, order, item))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [(-neg, item) for neg, _, item in scored]


def _merge(keyword: list[tuple[float, M]], hits: list[M], text: str, limit: int) -> list[M]:
    """Rank the union of keyword matches and vector hits, so neither path hides the other.

    1. Everything that shares a word with the query, vector hit or not, ranked by
       keyword score (exact name matches first, then name overlap above description-only
       overlap), with the vector rank breaking ties. A vector hit gets no boost beyond
       that, so a saved item with no stored embedding (seeded offline, or whose
       description changed), which vector search cannot see, still outranks hits that
       share only a description word like 'old'.
    2. Then vector hits with no word in common, nearest first. These are the
       semantic-only matches (a cornet for "trumpet"), kept last because a nearest
       neighbour is returned even when nothing is related.
    """
    vrank = {h.id: i for i, h in enumerate(hits)}
    scored: dict[str, tuple[float, int, M]] = {}  # id -> (score, input order, item)
    for order, (score, item) in enumerate(keyword):
        scored.setdefault(item.id, (score, order, item))
    for hit in hits:
        if hit.id not in scored:
            score = _keyword_score(text, hit.name, hit.description)
            if score > 0:
                scored[hit.id] = (score, len(keyword) + vrank[hit.id], hit)
    related = sorted(scored.values(),
                     key=lambda t: (-t[0], vrank.get(t[2].id, len(hits)), t[1]))
    out: dict[str, M] = {item.id: item for _, _, item in related}
    for hit in hits:
        out.setdefault(hit.id, hit)
    return list(out.values())[:limit]


def _find(db: Database, coll: str, index: str, cls: type[M], family_id: str, text: str,
          vector: list[float] | None, limit: int, library: dict[str, M]) -> list[M]:
    if limit <= 0:
        return []
    hits = _vector_search(db, coll, index, cls, family_id, vector, limit) if vector is not None else []
    family = _list(db, coll, cls, family_id)
    shadowed = {m.id for m in family}
    candidates = family + [m for pid, m in library.items() if pid not in shadowed]
    return _merge(_keyword_scored(text, candidates), hits, text, limit)


# --------------------------------------------------------------------------- props


def insert_prop(db: Database, family_id: str, prop: schemas.Prop, *, prop_id: str | None = None,
                embedding: list[float] | None = None) -> schemas.Prop:
    """Save a prop to the family. Ignores prop.id and prop.family_id. ValueError on a duplicate id.

    embedding=None computes the vector from name and description (see embedding_text) when
    an embedder is installed; with none, no vector is stored.
    """
    return _insert(db, PROPS, family_id, prop, prop_id, embedding)


def replace_prop(db: Database, family_id: str, prop: schemas.Prop, *,
                 embedding: list[float] | None = None) -> schemas.Prop | None:
    """Full replace by prop.id within the family. None if the family has no such prop.

    With embedding=None the stored vector is kept if the description is unchanged (it is
    computed from the description). Otherwise it is recomputed from name and description
    when an embedder is installed, and dropped when not (it would be stale).
    """
    return _replace(db, PROPS, family_id, prop, embedding)


def get_prop(db: Database, family_id: str, prop_id: str) -> schemas.Prop | None:
    """The family's prop, else the library prop with that id, else None."""
    found = _get(db, PROPS, schemas.Prop, family_id, prop_id)
    if found is not None:
        return found
    return library_props().get(prop_id)


def get_props(db: Database, family_id: str, prop_ids: list[str]) -> list[schemas.Prop]:
    """Props in input order, library included, missing ids skipped."""
    found = _get_many(db, PROPS, schemas.Prop, family_id, prop_ids)
    library = library_props()
    out = []
    for pid in prop_ids:
        prop = found.get(pid) or library.get(pid)
        if prop is not None:
            out.append(prop)
    return out


def list_props(db: Database, family_id: str) -> list[schemas.Prop]:
    """The family's saved props only (no library), ordered by name."""
    return _list(db, PROPS, schemas.Prop, family_id)


def update_prop(db: Database, family_id: str, prop_id: str,
                patch: schemas.PropPatch) -> schemas.Prop | None:
    """PATCH /families/{fid}/props/{id}: rename, and/or replace the art with a photo.

    name sets the name and keeps the stored embedding (it is computed from the
    description, which a rename does not change).

    photo_id sets art={'body': photo_id}, source='photo' and states={}, keeping size,
    anchor, grip, sockets and color; aspect is set from the photo's pixel size when it can
    be read (media.image_aspect: this family's media, EXIF orientation applied), and kept
    otherwise. It also sets parts={}: beyond the letter of the spec, but every part points
    at an art key ('bellows', 'door') that the new art no longer has, so keeping them
    would leave parts with no image. Library props and other families' props return None.
    Checking that the media id exists is the route's job.
    """
    update: dict = {}
    if patch.name is not None:
        update["name"] = patch.name
    if patch.photo_id is not None:
        update.update({"art": {"body": patch.photo_id}, "source": "photo", "states": {}, "parts": {}})
    query = _query(family_id, prop_id)
    if patch.photo_id is not None:
        bare = media.parse_media_ref(patch.photo_id)
        aspect = media.image_aspect(db, family_id, bare) if bare is not None else None
        if aspect is not None:
            update["aspect"] = aspect
    if not update:
        return _get(db, PROPS, schemas.Prop, family_id, prop_id)
    doc = db[PROPS].find_one_and_update(query, {"$set": update}, projection=_NO_EMBEDDING,
                                        return_document=ReturnDocument.AFTER)
    return _from_doc(schemas.Prop, doc) if doc else None


def find_props(db: Database, family_id: str, text: str, *, vector: list[float] | None = None,
               limit: int = 5) -> list[schemas.Prop]:
    """Match a description like 'the old trumpet case' to saved and library props.

    Always: keyword match over the family's props plus the library props it has not
    shadowed, score > 0 only. With a vector, also Atlas Vector Search on `props_vector`
    pre-filtered by familyId, and the two are merged (see _merge): exact name matches
    first, so a saved prop with no embedding is never hidden by an unrelated nearest
    neighbour. When vector search is unavailable (mongomock, plain MongoDB) or returns
    nothing, this is the keyword match alone.
    """
    return _find(db, PROPS, PROPS_VECTOR_INDEX, schemas.Prop, family_id, text, vector, limit,
                 library_props())


# ---------------------------------------------------------------------------- sets


def insert_set(db: Database, family_id: str, set_def: schemas.SetDef, *, set_id: str | None = None,
               embedding: list[float] | None = None) -> schemas.SetDef:
    """Save a set to the family. Ignores set_def.id and set_def.family_id. ValueError on a duplicate id.

    Embedding handling as insert_prop.
    """
    return _insert(db, SETS, family_id, set_def, set_id, embedding)


def replace_set(db: Database, family_id: str, set_def: schemas.SetDef, *,
                embedding: list[float] | None = None) -> schemas.SetDef | None:
    """Full replace by set_def.id within the family. Embedding handling as replace_prop."""
    return _replace(db, SETS, family_id, set_def, embedding)


def get_set(db: Database, family_id: str, set_id: str) -> schemas.SetDef | None:
    return _get(db, SETS, schemas.SetDef, family_id, set_id)


def get_sets(db: Database, family_id: str, set_ids: list[str]) -> list[schemas.SetDef]:
    """Sets in input order, missing ids skipped."""
    found = _get_many(db, SETS, schemas.SetDef, family_id, set_ids)
    return [found[sid] for sid in set_ids if sid in found]


def list_sets(db: Database, family_id: str) -> list[schemas.SetDef]:
    """The family's saved sets, ordered by name."""
    return _list(db, SETS, schemas.SetDef, family_id)


def find_sets(db: Database, family_id: str, text: str, *, vector: list[float] | None = None,
              limit: int = 5) -> list[schemas.SetDef]:
    """As find_props, over the family's sets (there is no set library)."""
    return _find(db, SETS, SETS_VECTOR_INDEX, schemas.SetDef, family_id, text, vector, limit, {})


# ----------------------------------------------------------------------- migration


def migrate_legacy_ids(db: Database) -> int:
    """Rewrite props and sets stored in the old layout (`_id` = the bare API id, no `id`
    field) to the compound key. Returns how many documents were migrated.

    Each legacy document is copied with `_id` = _key(familyId, old _id) and `id` = the
    old _id, every other field (embedding included) kept, and the old document is
    deleted. When the new key already exists the copy is skipped but the old document is
    still deleted. Documents with no familyId cannot be keyed and are left alone. Safe to
    run on every startup: a second run finds nothing to do, and an empty database is fine.
    """
    migrated = 0
    for coll in (PROPS, SETS):
        for doc in list(db[coll].find({"id": {"$exists": False}})):
            family_id = doc.get("familyId")
            if not isinstance(family_id, str) or not family_id:
                log.warning("%s document %r has no familyId; not migrated", coll, doc.get("_id"))
                continue
            old_id = doc["_id"]
            api_id = _api_id(doc)
            new_key = _key(family_id, api_id)
            if old_id == new_key:  # already keyed, only the id field is missing
                db[coll].update_one({"_id": old_id}, {"$set": {"id": api_id}})
                migrated += 1
                continue
            new_doc = {**doc, "_id": new_key, "id": api_id}
            if db[coll].find_one({"_id": new_key}, {"_id": 1}) is None:
                try:
                    db[coll].insert_one(new_doc)
                except DuplicateKeyError:
                    pass  # written concurrently: the new layout already holds it
            db[coll].delete_one({"_id": old_id})
            migrated += 1
    return migrated
