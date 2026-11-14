"""Offline guards for the mongomock-versus-real-MongoDB risks in api/db/REAL_MONGODB.md.

Everything here runs on mongomock or small fakes of the pymongo surface, with no server
and no network. Each test names the register row (R<n>) it guards. The real-server halves
are in test_real_mongo_risks.py (and test_real_mongo.py), which skip without
MONGODB_URI_TEST. Nothing here proves Atlas behaviour; it pins what the code sends and
stores, so a change that would only break on a real server fails here first.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import bson
import mongomock
import pytest
from bson import ObjectId
from gridfs.errors import FileExists
from pymongo.errors import DuplicateKeyError, OperationFailure

import schemas
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import search as search_db
from db import users as users_db
from screenplay.schema import Caption, Timeline
from seed import demo

FAM = demo.DEMO_FAMILY_ID

# Far below MongoDB's 16 MB document limit (16,777,216 bytes). The largest seeded document
# (a memory with screenplay, timeline and two tellings) is about 50 KB.
PER_DOCUMENT_BUDGET = 256 * 1024
# A memory told by this many people, every one with their own narration track, plus the
# largest embedding Voyage returns (2048 floats), must still fit this budget.
WORST_CASE_TELLERS = 50
WORST_CASE_BUDGET = 4 * 1024 * 1024
BSON_LIMIT = 16 * 1024 * 1024


@pytest.fixture(autouse=True)
def _no_embedder() -> Iterator[None]:
    search_db.set_embedder(None)
    yield
    search_db.set_embedder(None)


def _fresh_db():
    return mongomock.MongoClient(tz_aware=True)[f"stitch_risk_{ObjectId()}"]


@pytest.fixture(scope="module")
def seeded():
    """The demo family on mongomock, seeded once for the read-only checks in this module."""
    db = _fresh_db()
    db_client.ensure_indexes(db)
    demo.seed_demo(db)
    return db


def _all_documents(db) -> Iterator[tuple[str, dict]]:
    for name in sorted(db.list_collection_names()):
        for doc in db[name].find():
            yield name, doc


def _walk_keys(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """(key, path to it) for every dict key at any depth."""
    if isinstance(value, dict):
        for key, inner in value.items():
            where = f"{path}/{key}"
            yield key, where
            yield from _walk_keys(inner, where)
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _walk_keys(inner, f"{path}[{index}]")


# ------------------------------------------------------------------ R13: document size


def test_every_seeded_document_is_bson_encodable_and_under_budget(seeded):
    sizes: dict[str, tuple[int, Any]] = {}
    for name, doc in _all_documents(seeded):
        size = len(bson.encode(doc))  # raises on anything BSON cannot hold
        if size > sizes.get(name, (0, None))[0]:
            sizes[name] = (size, doc["_id"])
        assert size < PER_DOCUMENT_BUDGET, (name, doc["_id"], size)
    for name in ("users", "families", "people", "memories", "questions", "props", "sets", "traditions"):
        assert name in sizes, f"the demo seed wrote no {name}"


def test_worst_case_memory_stays_far_below_the_16mb_limit(seeded):
    """The largest seeded memory, told by WORST_CASE_TELLERS people, with a 2048-d embedding.

    Each extra teller adds one telling, one Timeline.narrations track and one
    Screenplay.narrations entry; the largest of each in the seed stands in for all of them.
    """
    memories = list(seeded.memories.find({"familyId": FAM}))
    largest = max(memories, key=lambda d: len(bson.encode(d)))

    def biggest(values: list[Any]) -> int:
        return max((len(bson.encode({"v": v})) for v in values), default=0)

    track = biggest([v for m in memories for v in ((m.get("timeline") or {}).get("narrations") or {}).values()])
    lines = biggest([v for m in memories for v in ((m.get("screenplay") or {}).get("narrations") or {}).values()])
    telling = biggest([t for m in memories for t in m.get("tellings") or []])
    assert track and lines and telling, "the seed has no second telling to measure"

    embedding = len(bson.encode({"embedding": [0.123456789] * 2048}))
    projected = len(bson.encode(largest)) + WORST_CASE_TELLERS * (track + lines + telling) + embedding
    assert projected < WORST_CASE_BUDGET, projected
    assert WORST_CASE_BUDGET <= BSON_LIMIT // 4


def test_save_scene_refuses_a_scene_real_mongodb_could_not_store(mdb):
    """mongomock stores a document over 16 MB; a real server refuses it mid-request."""
    person = people_db.create_person(mdb, "f1", schemas.NewPerson(name="Gran"), added_by="u")
    memory = memories_db.create_memory(mdb, "f1", title="Hymns", year=1988, participant_ids=[person.id])
    small = memories_db.Timeline(duration=1.0, tracks=[])
    assert memories_db.save_scene(mdb, "f1", memory.id, screenplay=None, timeline=small,
                                  prop_ids=[], set_ids=[]).version == 1

    huge = Timeline(duration=1.0, tracks=[], captions=[Caption(t=0.0, text="x" * (memories_db.MAX_SCENE_BYTES + 1))])
    with pytest.raises(ValueError, match="too large"):
        memories_db.save_scene(mdb, "f1", memory.id, screenplay=None, timeline=huge, prop_ids=[], set_ids=[])
    stored = memories_db.get_memory(mdb, "f1", memory.id)
    assert stored.version == 1 and stored.timeline.captions == []
    assert memories_db.MAX_SCENE_BYTES < BSON_LIMIT


# --------------------------------------------------------- R14: '$' and '.' in stored keys


def test_no_seeded_key_starts_with_a_dollar_or_contains_a_dot(seeded):
    """Narrations are keyed by person id and shot id, props by art, part and state names.

    MongoDB 5.0+ can store such keys but cannot address them in a query or an update path,
    and older servers refuse them; the seed must not depend on either.
    """
    bad = [
        (name, doc["_id"], where)
        for name, doc in _all_documents(seeded)
        for key, where in _walk_keys(doc)
        if key.startswith("$") or "." in key
    ]
    assert bad == []


_PATH_SEGMENT = re.compile(r"^(?:[A-Za-z][A-Za-z0-9]*|\d+|\$)$")
_UPDATE_METHODS = ("update_one", "update_many", "find_one_and_update")


def test_update_paths_never_embed_an_id(monkeypatch):
    """Every update path the db layer builds is a field name, an array index or '$'.

    A dotted path built from an id (`narrations.<personId>`) breaks on a real server as soon
    as an id holds a '.' or starts with '$'; mongomock is more forgiving.
    """
    sent: list[dict] = []
    for method in _UPDATE_METHODS:
        original = getattr(mongomock.collection.Collection, method)

        def spy(self, flt, update, *args, _original=original, **kwargs):
            if isinstance(update, dict):
                sent.append(update)
            return _original(self, flt, update, *args, **kwargs)

        monkeypatch.setattr(mongomock.collection.Collection, method, spy)

    db = _fresh_db()
    db_client.ensure_indexes(db)
    demo.seed_demo(db)
    # The other writes an API session makes, on the seeded family.
    memories_db.add_telling(db, FAM, demo.HYMN_MEMORY_ID,
                            schemas.Telling(author_id="dad", text="Edited: the hymn books fell."))
    memories_db.link_participants(db, FAM, demo.HYMN_MEMORY_ID, ["dad"])
    memories_db.set_embedding(db, FAM, demo.HYMN_MEMORY_ID, [0.1, 0.2])
    memories_db.set_question_status(db, FAM, demo.DEMO_QUESTION_IDS[0], "answered")
    invite = families_db.create_invite(db, FAM, "demo_user_david")
    families_db.revoke_invite(db, FAM, invite.code)
    families_db.add_member(db, FAM, "demo_user_sam", "admin")
    families_db.set_member_role(db, FAM, "demo_user_sam", "member")
    families_db.add_member(db, FAM, "new_user")
    people_db.update_person(db, FAM, "dad", schemas.PersonPatch(name="David Mao"))
    people_db.claim_person(db, FAM, "grandpa_luis", "new_user")
    families_db.remove_member(db, FAM, "new_user")
    props_db.update_prop(db, FAM, "hymn_books", schemas.PropPatch(name="Hymnals"))

    assert sent
    for update in sent:
        for operator, fields in update.items():
            assert operator.startswith("$"), update
            for path in fields:
                bad = [s for s in path.split(".") if not _PATH_SEGMENT.match(s)]
                assert not bad, (operator, path)


# ---------------------------------------------------------- R12: millisecond datetimes


MICROS = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)


def test_memory_writes_return_what_later_reads_return(mdb):
    """BSON dates keep milliseconds. A write that returns microseconds disagrees with every read."""
    person = people_db.create_person(mdb, "f1", schemas.NewPerson(name="Gran"), added_by="u")
    created = memories_db.create_memory(
        mdb, "f1", title="Hymns", year=1988, participant_ids=[person.id],
        telling=schemas.Telling(author_id=person.id, text="We sang."), now=MICROS,
    )
    assert created.tellings[0].created_at == MICROS.replace(microsecond=678000)
    assert memories_db.get_memory(mdb, "f1", created.id) == created

    other = people_db.create_person(mdb, "f1", schemas.NewPerson(name="Pop"), added_by="u")
    added = memories_db.add_telling(
        mdb, "f1", created.id, schemas.Telling(author_id=other.id, text="I sang too.", created_at=MICROS)
    )
    assert memories_db.get_memory(mdb, "f1", created.id) == added
    # The real clock (microseconds almost always set) and a naive caller value, too.
    fresh = memories_db.create_memory(
        mdb, "f1", title="Now", year=2000, participant_ids=[person.id],
        telling=schemas.Telling(author_id=person.id, text="Today."),
    )
    assert memories_db.get_memory(mdb, "f1", fresh.id) == fresh
    naive = memories_db.create_memory(
        mdb, "f1", title="Naive", year=2001, participant_ids=[person.id],
        telling=schemas.Telling(author_id=person.id, text="Then.", created_at=MICROS.replace(tzinfo=None)),
    )
    assert naive.tellings[0].created_at == MICROS.replace(microsecond=678000)
    assert memories_db.get_memory(mdb, "f1", naive.id) == naive


def test_user_writes_return_what_later_reads_return(mdb):
    user = users_db.create_user(mdb, email="ms@x.test", password_hash="h", name="Ms", now=MICROS)
    assert users_db.get_user(mdb, user.id) == user
    assert user.created_at.microsecond % 1000 == 0


# ------------------------------------------------------------------ R8: $graphLookup


def test_native_graph_lookup_equals_the_pure_fallback_on_the_demo_seed(seeded):
    docs = list(seeded.people.find({"familyId": FAM}))
    results = list(seeded.people.aggregate(people_db._tree_pipeline(FAM)))
    assert results, "mongomock ran no $graphLookup"
    assert people_db._reached_from_aggregation(results) == people_db._reached_from_docs(docs)
    native = people_db._tree_docs(seeded, FAM)
    assert native == people_db._tree_from_docs(docs)
    assert sorted(d["_id"] for d in native) == sorted(demo.DEMO_PERSON_IDS)


def test_a_real_server_refusing_graph_lookup_is_logged_and_still_answers(seeded, caplog):
    class Refusing:
        def __init__(self, inner):
            self._inner = inner

        def aggregate(self, *_args, **_kwargs):
            raise OperationFailure("$graphLookup refused", code=40324)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    class Db:
        def __getitem__(self, name):
            return Refusing(seeded[name]) if name == people_db.COLLECTION else seeded[name]

    expected = people_db.get_tree(seeded, FAM)
    with caplog.at_level(logging.WARNING, logger="db.people"):
        assert people_db.get_tree(Db(), FAM) == expected
    assert any("$graphLookup failed" in r.getMessage() for r in caplog.records)


# ----------------------------------------------------- R9/R10: Atlas silent empty vector


class _AtlasWithoutIndex:
    """A db whose $vectorSearch answers [] without raising, as Atlas does for a missing,
    pending or not-yet-synced index (mongomock raises NotImplementedError instead)."""

    def __init__(self, inner):
        self._inner = inner

    def __getitem__(self, name):
        inner = self._inner[name]

        class Coll:
            def aggregate(self, pipeline, *args, **kwargs):
                if pipeline and "$vectorSearch" in pipeline[0]:
                    return iter([])
                return inner.aggregate(pipeline, *args, **kwargs)

            def __getattr__(self, attr):
                return getattr(inner, attr)

        return Coll()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_an_empty_vector_answer_never_drops_keyword_hits_in_search(seeded):
    vector = [0.1] * 8
    atlas = _AtlasWithoutIndex(seeded)
    assert search_db._vector_search(atlas, FAM, vector, 5) == []  # the silent path
    for query in ("Who dropped the hymn books?", "the green sedan", "nothing matches zzqx"):
        keyword = search_db.search_memories(seeded, FAM, query)
        with_vector = search_db.search_memories(atlas, FAM, query, vector=vector)
        assert [(h.memory_id, h.score, h.quotes) for h in with_vector] == [
            (h.memory_id, h.score, h.quotes) for h in keyword
        ], query
    assert search_db.search_memories(atlas, FAM, "Who dropped the hymn books?", vector=vector)


def test_an_empty_vector_answer_never_drops_keyword_hits_in_props(seeded):
    vector = [0.1] * 8
    atlas = _AtlasWithoutIndex(seeded)
    for text in ("hymn books", "the old green sedan", "zzqx"):
        assert props_db.find_props(atlas, FAM, text, vector=vector) == props_db.find_props(seeded, FAM, text), text
        assert props_db.find_sets(atlas, FAM, text, vector=vector) == props_db.find_sets(seeded, FAM, text), text
    assert props_db.find_props(atlas, FAM, "hymn books", vector=vector)


# ------------------------------------------------------------------ R5: GridFS fixed ids


def test_a_taken_fixed_media_id_on_gridfs_is_a_value_error(mdb, monkeypatch):
    """Real GridFS reports a lost id race as FileExists, which the inline store never raises."""
    calls: list[ObjectId] = []

    class Bucket:
        def upload_from_stream_with_id(self, oid, *_args, **_kwargs):
            calls.append(oid)
            raise FileExists(f"file with _id {oid!r} already exists")

    monkeypatch.setattr(media_db, "_is_mock", lambda _db: False)
    monkeypatch.setattr(media_db, "_bucket", lambda _db: Bucket())
    oid = ObjectId()
    with pytest.raises(ValueError, match="media id already exists"):
        media_db.put_media(mdb, "f1", b"png", content_type="image/png", media_id=str(oid))
    assert calls == [oid]

    class DupBucket(Bucket):
        def upload_from_stream_with_id(self, oid, *_args, **_kwargs):
            raise DuplicateKeyError("E11000 duplicate key")

    monkeypatch.setattr(media_db, "_bucket", lambda _db: DupBucket())
    with pytest.raises(ValueError, match="media id already exists"):
        media_db.put_media(mdb, "f1", b"png", content_type="image/png", media_id=str(ObjectId()))


# --------------------------------------------------------------- R15: sort with nulls


def test_book_puts_a_memory_without_created_at_first(mdb):
    """BSON sort order: a missing field sorts as null, before every date (mongomock agrees)."""
    person = people_db.create_person(mdb, "f1", schemas.NewPerson(name="Gran"), added_by="u")
    later = memories_db.create_memory(mdb, "f1", title="Later", year=1990, participant_ids=[person.id],
                                      now=datetime(2026, 1, 1, tzinfo=UTC))
    mdb.memories.insert_one({"_id": "legacy", "familyId": "f1", "title": "Legacy", "year": 1990,
                             "participantIds": [person.id], "tellings": [], "propIds": [], "setIds": [],
                             "version": 1})
    earlier = memories_db.create_memory(mdb, "f1", title="Earlier", year=1990, participant_ids=[person.id],
                                        now=datetime(2026, 1, 1, tzinfo=UTC) - timedelta(days=1))
    assert [m.id for m in memories_db.get_book(mdb, "f1", person.id)] == ["legacy", earlier.id, later.id]


# ------------------------------------------------- R2: one bad index stops the rest (W2)


@pytest.mark.xfail(
    reason="fix pending (wave 2, db/client.py): ensure_indexes stops at the first index the "
    "server refuses, so every later index and migrate_legacy_ids are skipped",
    strict=False,
)
def test_one_refused_index_does_not_skip_the_others():
    db = _fresh_db()
    # Two people claimed by one user in one family: the partial unique index cannot be built.
    db.people.insert_many([
        {"_id": "a", "familyId": "f", "userId": "u", "parentIds": []},
        {"_id": "b", "familyId": "f", "userId": "u", "parentIds": []},
    ])
    try:
        db_client.ensure_indexes(db)
    except DuplicateKeyError:
        pass
    later = [name for name, _keys, _opts in db_client.INDEXES["memories"]]
    assert set(later) <= set(db.memories.index_information())
