"""People and the $graphLookup family tree (ARCHITECTURE.md sections 4 and 5).

Every function takes the database first and, for family data, the family id second.
Every filter includes ``familyId``, so another family's ids behave exactly like ids
that do not exist: reads return None or nothing, and relations naming them are
rejected with InvalidRelation.

Stored documents use camelCase fields and a string ``_id``; the API ``id`` is ``_id``.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, OperationFailure

import schemas

log = logging.getLogger(__name__)

COLLECTION = "people"

# Warm, distinguishable body colors, in pick order. The first two are the sample
# scene's David and Ana, so the demo family's first two people match it.
PALETTE: list[str] = [
    "#3f6fb5",  # cornflower blue (David)
    "#c0504d",  # brick red (Ana)
    "#7d9a5b",  # sage green
    "#d9973a",  # marigold
    "#8a5a9e",  # plum
    "#b5654a",  # terracotta
    "#2f8f8a",  # teal
    "#c77d9b",  # dusty rose
    "#6b4f3a",  # walnut
    "#e0b84f",  # mustard
    "#4f6d7a",  # slate
    "#a3453a",  # rust
    "#5e8c4a",  # moss
    "#d4826a",  # coral clay
    "#7a6ab0",  # lavender
    "#9a7b4f",  # ochre brown
]

# Signature items a puppet wears or carries, in pick order.
ITEMS: list[str] = [
    "blue flat cap",
    "red scarf",
    "straw hat",
    "round glasses",
    "pearl necklace",
    "bow tie",
    "knitted cardigan",
    "walking cane",
    "flower in hair",
    "pocket watch",
    "wool beret",
    "leather satchel",
    "yellow raincoat",
    "headscarf",
    "suspenders",
    "baseball cap",
    "polka-dot apron",
    "fedora",
]


class InvalidRelation(ValueError):
    """A bad relation or year: an unknown, other-family or duplicate id, a cycle, a spouse
    in the person's own lineage, death before birth, or a child born before a parent."""


class AlreadyClaimed(Exception):
    """The person belongs to another user, or the user already has a person in this family."""


# ------------------------------------------------------------------ character sheets


def _first_unused(options: list[str], used: set[str]) -> str:
    used_lower = {u.lower() for u in used if isinstance(u, str)}
    for option in options:
        if option.lower() not in used_lower:
            return option
    return options[len(used) % len(options)]


def pick_character(used_colors: set[str], used_items: set[str]) -> schemas.CharacterSheet:
    """First unused color and item in list order (case-insensitive); cycle once all are used."""
    return schemas.CharacterSheet(
        color=_first_unused(PALETTE, used_colors),
        item=_first_unused(ITEMS, used_items),
        build="average",
    )


def _family_character_usage(db: Database, family_id: str) -> tuple[set[str], set[str]]:
    colors: set[str] = set()
    items: set[str] = set()
    for doc in db[COLLECTION].find({"familyId": family_id}, {"character": 1}):
        character = doc.get("character") or {}
        if character.get("color"):
            colors.add(character["color"])
        if character.get("item"):
            items.add(character["item"])
    return colors, items


# -------------------------------------------------------------------------- mapping


def _to_person(doc: dict[str, Any]) -> schemas.Person:
    """Stored document to API model. Only known fields are copied, so storage-only ones drop."""
    return schemas.Person.model_validate(
        {
            "id": doc["_id"],
            "familyId": doc["familyId"],
            "userId": doc.get("userId"),
            "name": doc["name"],
            "born": doc.get("born"),
            "died": doc.get("died"),
            "parentIds": list(doc.get("parentIds") or []),
            "spouseIds": list(doc.get("spouseIds") or []),
            "character": doc["character"],
            "addedBy": doc.get("addedBy"),
        }
    )


def _to_summary(doc: dict[str, Any]) -> schemas.PersonSummary:
    return schemas.PersonSummary(
        id=doc["_id"],
        name=doc["name"],
        born=doc.get("born"),
        color=(doc.get("character") or {}).get("color"),
    )


def _character_doc(character: schemas.CharacterSheet) -> dict[str, Any]:
    return character.model_dump(mode="json", by_alias=True)


def _user_has_other_person(db: Database, family_id: str, user_id: str, person_id: str) -> bool:
    """True if user_id is linked to a person in this family other than person_id."""
    return bool(
        db[COLLECTION].count_documents(
            {"familyId": family_id, "userId": user_id, "_id": {"$ne": person_id}}, limit=1
        )
    )


# ------------------------------------------------------------------------ validation


def _existing_ids(db: Database, family_id: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    cursor = db[COLLECTION].find({"familyId": family_id, "_id": {"$in": list(ids)}}, {"_id": 1})
    return {doc["_id"] for doc in cursor}


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _validate_relations(
    db: Database,
    family_id: str,
    parent_ids: list[str] | None,
    spouse_ids: list[str] | None,
    *,
    self_id: str | None,
) -> None:
    """Raise InvalidRelation unless every id exists in this family and the shape is sane."""
    if parent_ids is not None:
        if len(parent_ids) > 2:
            raise InvalidRelation("a person has at most 2 parents")
        if len(set(parent_ids)) != len(parent_ids):
            raise InvalidRelation("duplicate parent id")
    named = list(parent_ids or []) + list(spouse_ids or [])
    if self_id is not None and self_id in named:
        raise InvalidRelation("a person cannot be their own parent or spouse")
    if parent_ids and spouse_ids and set(parent_ids) & set(spouse_ids):
        raise InvalidRelation("a person cannot be both a parent and a spouse")
    missing = set(named) - _existing_ids(db, family_id, named)
    if missing:
        raise InvalidRelation(f"unknown person id(s) in this family: {sorted(missing)}")


def _descendant_ids(docs: list[dict[str, Any]], person_id: str) -> set[str]:
    """Everyone below person_id through parentIds, in the given docs (pure)."""
    children: dict[str, list[str]] = {}
    for doc in docs:
        for parent in doc.get("parentIds") or []:
            children.setdefault(parent, []).append(doc["_id"])
    seen: set[str] = set()
    queue = deque([person_id])
    while queue:
        for child in children.get(queue.popleft(), []):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return seen


def _ancestor_ids(docs: list[dict[str, Any]], person_id: str) -> set[str]:
    """Everyone above person_id through parentIds, in the given docs (pure).

    Parent ids with no doc in ``docs`` are still included; the walk stops there.
    """
    parents = {doc["_id"]: list(doc.get("parentIds") or []) for doc in docs}
    seen: set[str] = set()
    queue = deque([person_id])
    while queue:
        for parent in parents.get(queue.popleft(), []):
            if parent not in seen:
                seen.add(parent)
                queue.append(parent)
    seen.discard(person_id)  # only possible with a cycle in bad data
    return seen


def _lineage_docs(db: Database, family_id: str) -> list[dict[str, Any]]:
    return list(
        db[COLLECTION].find({"familyId": family_id}, {"_id": 1, "name": 1, "parentIds": 1, "spouseIds": 1})
    )


SPOUSE_IN_LINEAGE = "a person cannot be married to their own ancestor or descendant"


def _reject_spouse_in_lineage(spouse_ids: list[str], lineage: set[str]) -> None:
    if lineage & set(spouse_ids):
        raise InvalidRelation(SPOUSE_IN_LINEAGE)


def _walk(edges: dict[str, list[str]], start: str) -> set[str]:
    """Everyone reachable from start along edges, start itself excluded (pure)."""
    seen: set[str] = set()
    queue = deque([start])
    while queue:
        for nxt in edges.get(queue.popleft(), []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    seen.discard(start)
    return seen


def _reject_lineage_marriages_after_reparent(
    after_docs: list[dict[str, Any]],
    person_id: str,
    old_spouses: list[str],
    spouses_after: list[str],
    below: set[str],
    old_ancestors: set[str],
) -> None:
    """A parents patch on person_id gives person_id and every descendant new ancestors.

    Reject the patch when any of them would end up married to an ancestor or a
    descendant. Spouse links are read from both sides, so a one-sided legacy link
    (only the ancestor lists the descendant) is caught too. The links are taken as
    they will be after the patch: a spouse the same patch removes from person_id is
    unlinked from both sides (so "W is my parent, not my spouse" is one valid patch),
    and an added spouse links back. ``after_docs`` are the family's docs with
    person_id's parentIds already replaced; no extra reads.
    """
    parents: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}
    spouses: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    for doc in after_docs:
        parents[doc["_id"]] = list(doc.get("parentIds") or [])
        for parent in parents[doc["_id"]]:
            children.setdefault(parent, []).append(doc["_id"])
        spouses[doc["_id"]] = set(doc.get("spouseIds") or [])
        names[doc["_id"]] = doc.get("name") or doc["_id"]
    # Mirror the symmetric spouse update that update_person applies after the checks.
    for removed in set(old_spouses) - set(spouses_after):
        spouses.get(removed, set()).discard(person_id)
    for added in set(spouses_after) - set(old_spouses):
        spouses.setdefault(added, set()).add(person_id)
    spouses[person_id] = set(spouses_after)

    def reject(one: str, other: str) -> None:
        # Name the pair: the bad link may sit anywhere in the branch, and the person
        # editing needs to know which marriage to fix.
        first, second = sorted((names.get(one, one), names.get(other, other)))
        raise InvalidRelation(f"{SPOUSE_IN_LINEAGE} ({first} and {second})")

    affected = {person_id} | below
    for member in sorted(affected):
        lineage = _walk(parents, member) | _walk(children, member)
        clash = spouses.get(member, set()) & lineage
        if clash:
            reject(member, min(clash))

    # Reverse direction: a new ancestor whose own spouse list names someone in the
    # affected branch, even when that person's list does not name them back.
    new_ancestors = _walk(parents, person_id) - old_ancestors
    for ancestor in sorted(new_ancestors):
        clash = spouses.get(ancestor, set()) & affected
        if clash:
            reject(ancestor, min(clash))


def _year_docs(db: Database, query: dict[str, Any]) -> list[dict[str, Any]]:
    """People matching query that have a known birth year, oldest first, then by name."""
    docs = db[COLLECTION].find({**query, "born": {"$ne": None}}, {"_id": 1, "name": 1, "born": 1})
    return sorted(
        (d for d in docs if isinstance(d.get("born"), int)),
        key=lambda d: (d["born"], d.get("name") or "", d["_id"]),
    )


def check_lifespan(born: int | None, died: int | None) -> None:
    """InvalidRelation when both years are known and died is before born.

    Public so a route can check a new person's years before writing anything else.
    """
    if born is not None and died is not None and died < born:
        raise InvalidRelation("a person cannot die before they were born")


def _check_born_after_parents(
    db: Database, family_id: str, born: int | None, parent_ids: list[str]
) -> None:
    """A child's birth year must not be earlier than a parent's. Unknown years pass."""
    if born is None or not parent_ids:
        return
    for parent in _year_docs(db, {"familyId": family_id, "_id": {"$in": list(parent_ids)}}):
        if born < parent["born"]:
            raise InvalidRelation(
                f"a person cannot be born before their parent ({parent['name']}, born {parent['born']})"
            )


def _check_born_before_children(db: Database, family_id: str, person_id: str, born: int | None) -> None:
    """A parent's birth year must not be later than any child's. Unknown years pass."""
    if born is None:
        return
    for child in _year_docs(db, {"familyId": family_id, "parentIds": person_id}):
        if child["born"] < born:
            raise InvalidRelation(
                f"a person cannot be born after their child ({child['name']}, born {child['born']})"
            )


# ------------------------------------------------------------------------------ CRUD


def create_person(
    db: Database,
    family_id: str,
    new: schemas.NewPerson,
    *,
    added_by: str | None,
    user_id: str | None = None,
    person_id: str | None = None,
) -> schemas.Person:
    """Add a person to the family tree, auto-picking a character sheet when none is given.

    Raises InvalidRelation for bad parent/spouse ids or impossible years (died before
    born, born before a parent), AlreadyClaimed when ``user_id``
    already has a person in this family, and ValueError for a duplicate ``person_id``.

    One person per user per family is enforced by the unique partial index
    ``people_userId_familyId_unique`` (db/client.py), so of two racing creates or a
    create racing a claim only one write lands. The early lookup is just a fast path.
    For databases without that index, a post-insert check also counts the user's
    people here and, if another exists, deletes the new document and raises
    AlreadyClaimed before any spouse is linked back to it.
    """
    people = db[COLLECTION]
    parent_ids = list(new.parent_ids)
    spouse_ids = _dedupe(new.spouse_ids)

    if person_id is not None and people.find_one({"_id": person_id}, {"_id": 1}) is not None:
        raise ValueError(f"person id already exists: {person_id}")
    _validate_relations(db, family_id, parent_ids, spouse_ids, self_id=person_id)
    if parent_ids and spouse_ids:
        # A new person has no descendants, but a spouse may still be a grandparent or
        # further up: the ancestors are the parents plus each parent's ancestors.
        docs = _lineage_docs(db, family_id)
        ancestors = set(parent_ids)
        for parent in parent_ids:
            ancestors |= _ancestor_ids(docs, parent)
        _reject_spouse_in_lineage(spouse_ids, ancestors)
    check_lifespan(new.born, new.died)
    _check_born_after_parents(db, family_id, new.born, parent_ids)
    if user_id is not None and people.find_one({"familyId": family_id, "userId": user_id}, {"_id": 1}):
        raise AlreadyClaimed("this user already has a person in this family")

    character = new.character
    if character is None:
        character = pick_character(*_family_character_usage(db, family_id))

    doc = {
        "_id": person_id if person_id is not None else str(ObjectId()),
        "familyId": family_id,
        "userId": user_id,
        "name": new.name,
        "born": new.born,
        "died": new.died,
        "parentIds": parent_ids,
        "spouseIds": spouse_ids,
        "character": _character_doc(character),
        "addedBy": added_by,
    }
    try:
        people.insert_one(doc)
    except DuplicateKeyError as exc:
        # Never read exc.details: mongomock leaves it None. Look at the data instead. If
        # our _id is free, the clash was on the one-person-per-user index even when the
        # other person is gone again by now (unclaimed or rolled back).
        if user_id is not None and (
            _user_has_other_person(db, family_id, user_id, doc["_id"])
            or not people.count_documents({"_id": doc["_id"]}, limit=1)
        ):
            raise AlreadyClaimed("this user already has a person in this family") from exc
        raise ValueError(f"person id already exists: {doc['_id']}") from exc

    if user_id is not None and _user_has_other_person(db, family_id, user_id, doc["_id"]):
        # Only reachable without the unique index: another create or a claim for this
        # user landed between our check and our insert. Yield to it, leaving nothing.
        people.delete_one({"_id": doc["_id"], "familyId": family_id})
        raise AlreadyClaimed("this user already has a person in this family")

    if spouse_ids:
        people.update_many(
            {"familyId": family_id, "_id": {"$in": spouse_ids}},
            {"$addToSet": {"spouseIds": doc["_id"]}},
        )
    return _to_person(doc)


def get_person(db: Database, family_id: str, person_id: str) -> schemas.Person | None:
    doc = db[COLLECTION].find_one({"_id": person_id, "familyId": family_id})
    return _to_person(doc) if doc else None


def get_people(db: Database, family_id: str, person_ids: list[str]) -> list[schemas.Person]:
    """People in input order; missing or other-family ids are skipped, repeats returned once."""
    wanted = _dedupe(person_ids)
    if not wanted:
        return []
    found = {
        doc["_id"]: doc
        for doc in db[COLLECTION].find({"familyId": family_id, "_id": {"$in": wanted}})
    }
    return [_to_person(found[pid]) for pid in wanted if pid in found]


def list_people(db: Database, family_id: str) -> list[schemas.Person]:
    cursor = db[COLLECTION].find({"familyId": family_id}).sort([("name", 1), ("_id", 1)])
    return [_to_person(doc) for doc in cursor]


def get_person_for_user(db: Database, family_id: str, user_id: str) -> schemas.Person | None:
    doc = db[COLLECTION].find_one({"familyId": family_id, "userId": user_id})
    return _to_person(doc) if doc else None


def unclaimed_people(db: Database, family_id: str) -> list[schemas.PersonSummary]:
    """People nobody has claimed yet, for the join screen's "I'm ..." picker."""
    cursor = db[COLLECTION].find({"familyId": family_id, "userId": None}).sort(
        [("name", 1), ("_id", 1)]
    )
    return [_to_summary(doc) for doc in cursor]


def person_summaries(db: Database, family_id: str) -> list[schemas.PersonSummary]:
    """Everyone as summaries, ordered like get_tree; color is the character color."""
    return [_to_summary(doc) for doc in _tree_docs(db, family_id)]


def update_person(
    db: Database, family_id: str, person_id: str, patch: schemas.PersonPatch
) -> schemas.Person | None:
    """Apply only the fields set on the patch. None if the person is not in this family.

    Raises InvalidRelation for bad relations, including making someone their own ancestor
    or married to one. A parents change is checked for this person and every descendant,
    since all of them gain the new ancestors; that error names the two people involved.
    Also raised for impossible years (died before born, born before a parent, born after
    a child). Unknown years are never rejected.
    Spouse links stay symmetric: added spouses link back, removed spouses unlink.
    """
    people = db[COLLECTION]
    current = people.find_one({"_id": person_id, "familyId": family_id})
    if current is None:
        return None

    fields = patch.model_fields_set
    updates: dict[str, Any] = {}

    if "name" in fields and patch.name is not None:
        updates["name"] = patch.name
    if "born" in fields:
        updates["born"] = patch.born
    if "died" in fields:
        updates["died"] = patch.died
    if "character" in fields and patch.character is not None:
        updates["character"] = _character_doc(patch.character)

    new_parents = list(patch.parent_ids) if "parent_ids" in fields and patch.parent_ids is not None else None
    new_spouses = _dedupe(patch.spouse_ids) if "spouse_ids" in fields and patch.spouse_ids is not None else None
    parents_after: list[str] = new_parents if new_parents is not None else list(current.get("parentIds") or [])

    if new_parents is not None or new_spouses is not None:
        # Existence is checked for the lists being replaced; the overlap and lineage
        # checks look at the resulting relations, including the unchanged half.
        _validate_relations(db, family_id, new_parents, new_spouses, self_id=person_id)
        spouses_after = new_spouses if new_spouses is not None else list(current.get("spouseIds") or [])
        if set(parents_after) & set(spouses_after):
            raise InvalidRelation("a person cannot be both a parent and a spouse")
        # One read of the family's parent links serves both lineage checks below.
        family_docs = _lineage_docs(db, family_id)
        below = _descendant_ids(family_docs, person_id)
        if new_parents is not None and below & set(new_parents):
            raise InvalidRelation("a person cannot be their own ancestor")
        # Ancestors as they will be after this patch, so a parents-only change that
        # makes an existing spouse an ancestor is caught too. Descendants do not depend
        # on this person's own parentIds.
        after_docs = [
            {**doc, "parentIds": parents_after} if doc["_id"] == person_id else doc
            for doc in family_docs
        ]
        _reject_spouse_in_lineage(spouses_after, below | _ancestor_ids(after_docs, person_id))
        if new_parents is not None:
            # New parents give new ancestors to this person and to everyone below, so
            # a descendant married to one of them (Z wed to W, then W becomes Z's
            # grandparent) must be rejected as well.
            _reject_lineage_marriages_after_reparent(
                after_docs,
                person_id,
                list(current.get("spouseIds") or []),
                spouses_after,
                below,
                _ancestor_ids(family_docs, person_id),
            )
        if new_parents is not None:
            updates["parentIds"] = new_parents
        if new_spouses is not None:
            updates["spouseIds"] = new_spouses

    # Year sanity against the resulting values. Only the parts a patch touches are
    # checked, so older data that predates these rules can still be renamed or recolored.
    born_after = patch.born if "born" in fields else current.get("born")
    if "born" in fields or "died" in fields:
        check_lifespan(born_after, patch.died if "died" in fields else current.get("died"))
    if "born" in fields or new_parents is not None:
        _check_born_after_parents(db, family_id, born_after, parents_after)
    if "born" in fields:
        _check_born_before_children(db, family_id, person_id, born_after)

    if updates:
        people.update_one({"_id": person_id, "familyId": family_id}, {"$set": updates})

    if new_spouses is not None:
        old_spouses = set(current.get("spouseIds") or [])
        added = [s for s in new_spouses if s not in old_spouses]
        removed = [s for s in old_spouses if s not in set(new_spouses)]
        if added:
            people.update_many(
                {"familyId": family_id, "_id": {"$in": added}},
                {"$addToSet": {"spouseIds": person_id}},
            )
        if removed:
            people.update_many(
                {"familyId": family_id, "_id": {"$in": removed}},
                {"$pull": {"spouseIds": person_id}},
            )

    return get_person(db, family_id, person_id)


def claim_person(
    db: Database, family_id: str, person_id: str, user_id: str
) -> schemas.Person | None:
    """Link an unclaimed person to user_id ("I'm David"). Atomic on the person document.

    None if the person is not in this family. AlreadyClaimed if someone else holds the
    person or the user already has a different person here. Re-claiming is a no-op.

    One person per user per family is enforced by the unique partial index
    ``people_userId_familyId_unique`` (db/client.py): a claim racing another claim or a
    create for the same user fails with DuplicateKeyError, raised as AlreadyClaimed.
    For databases without that index, a post-write recount undoes our claim if the user
    ended up with another person here.
    """
    people = db[COLLECTION]
    mine = people.find_one({"familyId": family_id, "userId": user_id})
    if mine is not None:
        if mine["_id"] == person_id:
            return _to_person(mine)
        raise AlreadyClaimed("this user already has a person in this family")

    try:
        doc = people.find_one_and_update(
            {"_id": person_id, "familyId": family_id, "userId": None},
            {"$set": {"userId": user_id}},
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise AlreadyClaimed("this user already has a person in this family") from exc
    if doc is None:
        existing = people.find_one({"_id": person_id, "familyId": family_id})
        if existing is None:
            return None
        if existing.get("userId") == user_id:
            return _to_person(existing)
        raise AlreadyClaimed("this person is already claimed")

    # Two concurrent claims of different people by the same user can both pass the check
    # above. If that happened, undo ours so the user never ends up with two people.
    others = people.count_documents(
        {"familyId": family_id, "userId": user_id, "_id": {"$ne": person_id}}
    )
    if others:
        people.update_one(
            {"_id": person_id, "familyId": family_id, "userId": user_id},
            {"$set": {"userId": None}},
        )
        raise AlreadyClaimed("this user already has a person in this family")
    return _to_person(doc)


# ------------------------------------------------------------------------------ tree


def _tree_pipeline(family_id: str) -> list[dict[str, Any]]:
    """Roots (no parents) of this family, each with every descendant via parentIds."""
    return [
        {"$match": {"familyId": family_id, "parentIds": []}},
        {
            "$graphLookup": {
                "from": COLLECTION,
                "startWith": "$_id",
                "connectFromField": "_id",
                "connectToField": "parentIds",
                "as": "descendants",
                "depthField": "depth",
                "restrictSearchWithMatch": {"familyId": family_id},
            }
        },
    ]


def _reached_from_aggregation(results: list[dict[str, Any]]) -> set[str]:
    reached: set[str] = set()
    for root in results:
        reached.add(root["_id"])
        reached.update(d["_id"] for d in root.get("descendants") or [])
    return reached


def _reached_from_docs(docs: list[dict[str, Any]]) -> set[str]:
    """What _tree_pipeline reaches, computed in Python: roots plus their descendants."""
    reached: set[str] = set()
    for doc in docs:
        if doc.get("parentIds") == []:  # same as the pipeline's {"parentIds": []}
            reached.add(doc["_id"])
            reached |= _descendant_ids(docs, doc["_id"])
    return reached


def _order_tree(docs: list[dict[str, Any]], reached: set[str]) -> list[dict[str, Any]]:
    """Assign generations and sort. Returns copies of docs with a ``generation`` key.

    Roots are 0 and a child is 1 + the max of its parents. Someone with no parents takes
    the max generation of their spouses, so a married-in spouse sits beside their partner.
    Iterates to a fixed point; generations are capped at len(docs) so a parent cycle in
    bad data still terminates. People the lookup did not reach stay at generation 0.
    """
    by_id = {doc["_id"]: doc for doc in docs}
    gen = {pid: 0 for pid in by_id}
    cap = len(by_id)
    active = [pid for pid in by_id if pid in reached]
    parents = {
        pid: [p for p in (by_id[pid].get("parentIds") or []) if p in reached and p in by_id]
        for pid in active
    }
    spouses = {
        pid: [s for s in (by_id[pid].get("spouseIds") or []) if s in reached and s in by_id]
        for pid in active
    }

    changed = True
    while changed:
        changed = False
        for pid in active:
            if parents[pid]:
                value = 1 + max(gen[p] for p in parents[pid])
            else:
                value = max([0] + [gen[s] for s in spouses[pid]])
            value = min(value, cap)
            if value > gen[pid]:
                gen[pid] = value
                changed = True

    def sort_key(pid: str) -> tuple[Any, ...]:
        born = by_id[pid].get("born")
        return (gen[pid], born is None, born or 0, by_id[pid].get("name") or "", pid)

    return [{**by_id[pid], "generation": gen[pid]} for pid in sorted(by_id, key=sort_key)]


def _tree_from_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The whole tree computation from raw people docs, without $graphLookup (pure)."""
    return _order_tree(docs, _reached_from_docs(docs))


def _tree_docs(db: Database, family_id: str) -> list[dict[str, Any]]:
    people = db[COLLECTION]
    docs = list(people.find({"familyId": family_id}))
    try:
        results = list(people.aggregate(_tree_pipeline(family_id)))
    except NotImplementedError:
        return _tree_from_docs(docs)  # a fake without $graphLookup: expected, not logged
    except OperationFailure as exc:
        # Every MongoDB server supports $graphLookup, so a real server refusing it is worth
        # a line in the log (the answer is still right: the pure walk computes the same).
        log.warning("$graphLookup failed for the family tree (%s); using the pure fallback", exc)
        return _tree_from_docs(docs)
    return _order_tree(docs, _reached_from_aggregation(results))


def get_tree(db: Database, family_id: str) -> list[schemas.Person]:
    """Everyone in the family, ordered by generation, then birth year (unknown last), then name."""
    return [_to_person(doc) for doc in _tree_docs(db, family_id)]
