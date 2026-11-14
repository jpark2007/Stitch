"""Props and sets, with vector matching on descriptions.

ARCHITECTURE.md section 5 (`props`, `sets`, the vector index on descriptions) and the
PATCH /families/{fid}/props/{id} semantics in section 6.

Storage: one document per prop or set, `_id` = API `id` (a str), camelCase fields,
`familyId` on every document, and an optional storage-only `embedding` used by the
Atlas Vector Search indexes `props_vector` and `sets_vector` (created by db/client.py).

Library props are hand-rigged JSON files in shared/props/. They are not stored per
family; they are merged into get_prop, get_props and find_props, and they can never be
patched. A family's saved prop with the same id shadows the library prop.

Every function that reads family data takes `family_id` and filters on it. Not found
or another family's id returns None (or is skipped), never raises.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import TypeVar

import bson
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, OperationFailure

import schemas

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


def _to_doc(model: schemas.Prop | schemas.SetDef, family_id: str, doc_id: str,
            embedding: list[float] | None) -> dict:
    doc = model.model_dump(by_alias=True, mode="json")
    doc.pop("id", None)
    doc["_id"] = doc_id
    doc["familyId"] = family_id
    if embedding is not None:
        doc["embedding"] = [float(x) for x in embedding]
    return doc


def _from_doc(cls: type[M], doc: dict) -> M:
    """Map `_id` to `id` and keep only the model's fields (drops `embedding`)."""
    allowed = {f.alias or name for name, f in cls.model_fields.items()}
    data = {k: v for k, v in doc.items() if k in allowed}
    data["id"] = str(doc["_id"])
    return cls.model_validate(data)


_NO_EMBEDDING = {"embedding": 0}


def _insert(db: Database, coll: str, family_id: str, model: M, doc_id: str | None,
            embedding: list[float] | None) -> M:
    doc_id = doc_id or str(bson.ObjectId())
    doc = _to_doc(model, family_id, doc_id, embedding)
    try:
        db[coll].insert_one(doc)
    except DuplicateKeyError as exc:
        raise ValueError(f"{coll[:-1]} id {doc_id!r} already exists") from exc
    return _from_doc(type(model), doc)


def _replace(db: Database, coll: str, family_id: str, model: M,
             embedding: list[float] | None) -> M | None:
    current = db[coll].find_one({"_id": model.id, "familyId": family_id})
    if current is None:
        return None
    if embedding is None and "embedding" in current:
        # Keep the stored vector only while the text it was computed from is unchanged.
        if current.get("name") == model.name and current.get("description") == model.description:
            embedding = current["embedding"]
    doc = _to_doc(model, family_id, model.id, embedding)
    result = db[coll].replace_one({"_id": model.id, "familyId": family_id}, doc)
    if result.matched_count == 0:
        return None
    return _from_doc(type(model), doc)


def _get(db: Database, coll: str, cls: type[M], family_id: str, doc_id: str) -> M | None:
    doc = db[coll].find_one({"_id": doc_id, "familyId": family_id}, _NO_EMBEDDING)
    return _from_doc(cls, doc) if doc else None


def _get_many(db: Database, coll: str, cls: type[M], family_id: str,
              ids: list[str]) -> dict[str, M]:
    if not ids:
        return {}
    cursor = db[coll].find({"_id": {"$in": list(dict.fromkeys(ids))}, "familyId": family_id},
                           _NO_EMBEDDING)
    return {str(doc["_id"]): _from_doc(cls, doc) for doc in cursor}


def _list(db: Database, coll: str, cls: type[M], family_id: str) -> list[M]:
    items = [_from_doc(cls, doc) for doc in db[coll].find({"familyId": family_id}, _NO_EMBEDDING)]
    items.sort(key=lambda m: (m.name.casefold(), m.name, m.id))
    return items


# ------------------------------------------------------------------------ matching

_STOPWORDS = frozenset(
    "a an the of and or to in on at for with from by my our your his her their its this that "
    "these those some".split()
)


def _stem(token: str) -> str:
    """Very small plural stripper: books -> book, glasses -> glass, boxes -> box, keys -> key."""
    if len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("sses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _tokens(text: str | None) -> list[str]:
    if not text:
        return []
    text = text.lower().replace("'", "").replace("’", "")
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text) if t not in _STOPWORDS]


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
    """Atlas Vector Search pipeline with a familyId pre-filter (and a defensive $match)."""
    return [
        {
            "$vectorSearch": {
                "index": index,
                "path": "embedding",
                "queryVector": [float(x) for x in vector],
                "numCandidates": max(limit, 1) * 20,
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


def _keyword_rank(text: str, candidates: list[M], limit: int) -> list[M]:
    scored = []
    for order, item in enumerate(candidates):
        score = _keyword_score(text, item.name, item.description)
        if score > 0:
            scored.append((-score, order, item))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [item for _, _, item in scored[:limit]]


def _find(db: Database, coll: str, index: str, cls: type[M], family_id: str, text: str,
          vector: list[float] | None, limit: int, library: dict[str, M]) -> list[M]:
    if limit <= 0:
        return []
    if vector is not None:
        hits = _vector_search(db, coll, index, cls, family_id, vector, limit)
        if hits:
            # Top up with matching library items the family has not shadowed.
            seen = {h.id for h in hits}
            if library:
                seen |= {str(d["_id"]) for d in db[coll].find({"familyId": family_id}, {"_id": 1})}
            extra = _keyword_rank(text, [m for pid, m in library.items() if pid not in seen],
                                  limit - len(hits))
            return hits + extra
    family = _list(db, coll, cls, family_id)
    shadowed = {m.id for m in family}
    candidates = family + [m for pid, m in library.items() if pid not in shadowed]
    return _keyword_rank(text, candidates, limit)


# --------------------------------------------------------------------------- props


def insert_prop(db: Database, family_id: str, prop: schemas.Prop, *, prop_id: str | None = None,
                embedding: list[float] | None = None) -> schemas.Prop:
    """Save a prop to the family. Ignores prop.id and prop.family_id. ValueError on a duplicate id."""
    return _insert(db, PROPS, family_id, prop, prop_id, embedding)


def replace_prop(db: Database, family_id: str, prop: schemas.Prop, *,
                 embedding: list[float] | None = None) -> schemas.Prop | None:
    """Full replace by prop.id within the family. None if the family has no such prop.

    With embedding=None the stored vector is kept if name and description are unchanged,
    and dropped otherwise (it would be stale).
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

    photo_id sets art={'body': photo_id}, source='photo', states={} and parts={} (parts
    point at art keys that no longer exist), keeping size, anchor, grip, sockets, aspect
    and color. Library props and other families' props return None. Checking that the
    media id exists is the route's job.
    """
    update: dict = {}
    if patch.name is not None:
        update["name"] = patch.name
    if patch.photo_id is not None:
        update.update({"art": {"body": patch.photo_id}, "source": "photo", "states": {}, "parts": {}})
    query = {"_id": prop_id, "familyId": family_id}
    if not update:
        return _get(db, PROPS, schemas.Prop, family_id, prop_id)
    doc = db[PROPS].find_one_and_update(query, {"$set": update}, projection=_NO_EMBEDDING,
                                        return_document=ReturnDocument.AFTER)
    return _from_doc(schemas.Prop, doc) if doc else None


def find_props(db: Database, family_id: str, text: str, *, vector: list[float] | None = None,
               limit: int = 5) -> list[schemas.Prop]:
    """Match a description like 'the old trumpet case' to saved and library props.

    With a vector: Atlas Vector Search on `props_vector` pre-filtered by familyId, topped
    up with keyword-matched library props. Without one, or when vector search is
    unavailable or returns nothing: keyword match over the family's props plus the
    library, best first, score > 0 only.
    """
    return _find(db, PROPS, PROPS_VECTOR_INDEX, schemas.Prop, family_id, text, vector, limit,
                 library_props())


# ---------------------------------------------------------------------------- sets


def insert_set(db: Database, family_id: str, set_def: schemas.SetDef, *, set_id: str | None = None,
               embedding: list[float] | None = None) -> schemas.SetDef:
    """Save a set to the family. Ignores set_def.id and set_def.family_id. ValueError on a duplicate id."""
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
