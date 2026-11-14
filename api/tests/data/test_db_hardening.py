"""Regression tests for db/ bugs that mongomock hides but real MongoDB or Atlas would hit.

Each test names the divergence it guards. Everything here runs on mongomock or on small
fakes of the pymongo surface; the real-server variants are in test_real_mongo.py and skip
unless MONGODB_URI_TEST is set. Atlas Vector Search itself is not exercised anywhere
offline: these tests pin the index definitions and pipeline shapes Atlas would receive.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import mongomock
import pytest
from bson import ObjectId
from gridfs.errors import CorruptGridFile
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

import schemas
from db import client as db_client
from db import embeddings
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import search as search_db
from db import sessions as sessions_db
from db import users as users_db

FAM = "fam_hard"


@pytest.fixture(autouse=True)
def _no_embedder():
    search_db.set_embedder(None)
    yield
    search_db.set_embedder(None)


# ------------------------------------------------------------ 1. vector index vs pipelines


def _filter_paths(flt: Any) -> set[str]:
    """Every field path a MongoDB-style filter names ($and/$or/$nor and operators walked)."""
    paths: set[str] = set()
    if isinstance(flt, dict):
        for key, value in flt.items():
            if key in ("$and", "$or", "$nor"):
                for clause in value:
                    paths |= _filter_paths(clause)
            elif key.startswith("$"):
                continue
            else:
                paths.add(key)
    return paths


class _CapturingCollection:
    """A mongomock collection whose aggregate records the pipeline, then fails like plain MongoDB."""

    def __init__(self, inner, name: str, calls: list[tuple[str, list[dict]]]):
        self._inner, self._name, self._calls = inner, name, calls

    def aggregate(self, pipeline, *args, **kwargs):
        if pipeline and "$vectorSearch" in pipeline[0]:
            self._calls.append((self._name, pipeline))
            raise OperationFailure("$vectorSearch stage is only allowed on MongoDB Atlas")
        return self._inner.aggregate(pipeline, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _CapturingDb:
    def __init__(self, inner):
        self._inner = inner
        self.calls: list[tuple[str, list[dict]]] = []

    def __getitem__(self, name):
        return _CapturingCollection(self._inner[name], name, self.calls)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


def _pipelines_from_the_real_code_paths(mdb) -> list[tuple[str, list[dict]]]:
    """Run every function that issues a $vectorSearch, and capture what it sends."""
    db = _CapturingDb(mdb)
    vector = [0.1] * db_client.VECTOR_DIM
    search_db.search_memories(db, FAM, "hymn books", vector=vector, limit=5)
    props_db.find_props(db, FAM, "old trumpet case", vector=vector, limit=5)
    props_db.find_sets(db, FAM, "church steps", vector=vector, limit=5)
    return db.calls


def test_every_vector_search_filter_field_is_declared_in_its_index(mdb):
    """Atlas rejects a $vectorSearch filter on a field the index does not declare as 'filter'."""
    calls = _pipelines_from_the_real_code_paths(mdb)
    assert {coll for coll, _ in calls} == set(db_client.VECTOR_INDEXES), calls

    definition = db_client._vector_definition()
    declared_filters = {f["path"] for f in definition["fields"] if f["type"] == "filter"}
    vector_fields = {f["path"]: f for f in definition["fields"] if f["type"] == "vector"}

    for collection, pipeline in calls:
        assert list(pipeline[0]) == ["$vectorSearch"], "$vectorSearch must be the first stage"
        stage = pipeline[0]["$vectorSearch"]
        # The index the stage names is the one ensure_search_indexes creates on that collection.
        assert stage["index"] == db_client.VECTOR_INDEXES[collection]
        used = _filter_paths(stage.get("filter"))
        assert used, f"{collection}: a vector search must be pre-filtered by family"
        assert used <= declared_filters, f"{collection}: filter fields {used - declared_filters} not in the index"
        assert stage["path"] in vector_fields
        assert len(stage["queryVector"]) == vector_fields[stage["path"]]["numDimensions"]
        assert 1 <= stage["limit"] <= stage["numCandidates"] <= search_db.MAX_NUM_CANDIDATES


def test_vector_index_definition_matches_the_embedder():
    definition = db_client._vector_definition()
    vector = next(f for f in definition["fields"] if f["type"] == "vector")
    assert vector == {
        "type": "vector",
        "path": "embedding",
        "numDimensions": db_client.VECTOR_DIM,
        "similarity": "cosine",
    }
    assert {"type": "filter", "path": "familyId"} in definition["fields"]
    # The embedder asks Voyage for exactly the index's dimension.
    sent: list[dict] = []

    def post(url, payload, headers, timeout):
        sent.append(payload)
        return {"data": [{"embedding": [0.0] * payload["output_dimension"]}]}

    fn = embeddings.voyage_embedder("k", post=post)
    assert len(fn("hello")) == db_client.VECTOR_DIM
    assert sent[0]["output_dimension"] == db_client.VECTOR_DIM


def test_search_index_model_shape_for_the_installed_pymongo():
    """pymongo builds a createSearchIndexes entry with type vectorSearch (needs pymongo >= 4.7)."""
    model = SearchIndexModel(definition=db_client._vector_definition(), name="memories_vector", type="vectorSearch")
    assert model.document == {
        "name": "memories_vector",
        "definition": db_client._vector_definition(),
        "type": "vectorSearch",
    }


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(1, (20, 1)), (5, (100, 5)), (50, (1000, 50)), (600, (10_000, 600)), (50_000, (10_000, 10_000)), (0, (20, 1))],
)
def test_vector_sizes_stay_inside_atlas_bounds(limit, expected):
    """numCandidates above 10,000 is rejected by Atlas; a big limit used to send limit * 20."""
    assert search_db.vector_sizes(limit) == expected


def test_large_limits_never_build_a_rejected_vector_stage():
    for limit in (500, 501, 1000, 20_000):
        for pipeline in (
            search_db.vector_pipeline(FAM, [0.0], limit),
            props_db._vector_pipeline("props_vector", [0.0], FAM, limit),
        ):
            stage = pipeline[0]["$vectorSearch"]
            assert 1 <= stage["limit"] <= stage["numCandidates"] <= 10_000, (limit, stage)


# ------------------------------------------------- 2. ensure_search_indexes against "Atlas"


class _FakeSearchCollection:
    def __init__(self, existing: list[dict] | None = None, fail: Exception | None = None):
        self.indexes = list(existing or [])
        self.fail = fail
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []

    def list_search_indexes(self):
        if self.fail:
            raise self.fail
        return iter(self.indexes)

    def create_search_index(self, model):
        self.created.append(model.document)
        return model.document["name"]

    def update_search_index(self, name, definition):
        self.updated.append((name, definition))


class _FakeAtlasDb:
    def __init__(self, collections: dict[str, _FakeSearchCollection], present: set[str] | None = None):
        self.collections = collections
        self.present = set(collections) if present is None else set(present)
        self.made: list[str] = []

    def __getitem__(self, name):
        return self.collections[name]

    def list_collection_names(self):
        return sorted(self.present)

    def create_collection(self, name):
        self.present.add(name)
        self.made.append(name)


def _atlas_index(name: str, definition: dict, kind: str = "vectorSearch") -> dict:
    return {"id": "x", "name": name, "type": kind, "status": "READY", "queryable": True,
            "latestDefinition": definition}


def test_ensure_search_indexes_creates_missing_vector_indexes():
    db = _FakeAtlasDb({c: _FakeSearchCollection() for c in db_client.VECTOR_INDEXES}, present=set())
    created = db_client.ensure_search_indexes(db)
    assert created == list(db_client.VECTOR_INDEXES.values())
    for collection, name in db_client.VECTOR_INDEXES.items():
        assert db[collection].created == [
            {"name": name, "definition": db_client._vector_definition(), "type": "vectorSearch"}
        ]
    # Atlas cannot index a collection that does not exist yet: they were made first.
    assert sorted(db.made) == sorted(db_client.VECTOR_INDEXES)


def test_ensure_search_indexes_leaves_a_matching_index_alone():
    wanted = db_client._vector_definition()
    # Atlas may echo extra defaults on a field; that is not a stale index.
    echoed = {"fields": [{**f, "quantization": "none"} if f["type"] == "vector" else f for f in wanted["fields"]]}
    db = _FakeAtlasDb({c: _FakeSearchCollection([_atlas_index(n, echoed)])
                       for c, n in db_client.VECTOR_INDEXES.items()})
    assert db_client.ensure_search_indexes(db) == []
    assert all(not c.created and not c.updated for c in db.collections.values())


@pytest.mark.parametrize(
    "stale",
    [
        # Made by hand in the Atlas UI: the vector field only, no familyId filter.
        {"fields": [{"type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "cosine"}]},
        # Built for another EMBEDDING_DIM.
        {"fields": [{"type": "vector", "path": "embedding", "numDimensions": 7, "similarity": "cosine"},
                    {"type": "filter", "path": "familyId"}]},
        # Another similarity.
        {"fields": [{"type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "euclidean"},
                    {"type": "filter", "path": "familyId"}]},
        {},
    ],
)
def test_ensure_search_indexes_repairs_a_stale_definition(stale, monkeypatch, caplog):
    """Before: a same-named index was skipped, so a missing filter field broke every query."""
    monkeypatch.setattr(db_client, "VECTOR_DIM", 1024)
    db = _FakeAtlasDb({c: _FakeSearchCollection([_atlas_index(n, stale)])
                       for c, n in db_client.VECTOR_INDEXES.items()})
    caplog.set_level(logging.WARNING)
    assert db_client.ensure_search_indexes(db) == []
    for collection, name in db_client.VECTOR_INDEXES.items():
        assert db[collection].created == []
        assert db[collection].updated == [(name, db_client._vector_definition())]
    assert any("stale definition" in r.getMessage() for r in caplog.records)


def test_ensure_search_indexes_does_not_touch_a_same_named_text_index(caplog):
    db = _FakeAtlasDb({c: _FakeSearchCollection([_atlas_index(n, {"mappings": {}}, kind="search")])
                       for c, n in db_client.VECTOR_INDEXES.items()})
    caplog.set_level(logging.WARNING)
    assert db_client.ensure_search_indexes(db) == []
    assert all(not c.created and not c.updated for c in db.collections.values())
    assert any("not vectorSearch" in r.getMessage() for r in caplog.records)


def test_ensure_search_indexes_is_best_effort_when_listing_fails(caplog):
    db = _FakeAtlasDb({c: _FakeSearchCollection(fail=OperationFailure("not Atlas"))
                       for c in db_client.VECTOR_INDEXES})
    caplog.set_level(logging.WARNING)
    assert db_client.ensure_search_indexes(db) == []
    assert len([r for r in caplog.records if "not created" in r.getMessage()]) == 3


def test_vector_definition_covers():
    wanted = db_client._vector_definition()
    assert db_client.vector_definition_covers(wanted, wanted)
    extra = {"fields": [*wanted["fields"], {"type": "filter", "path": "year"}]}
    assert db_client.vector_definition_covers(extra, wanted)
    as_float = {"fields": [{**f, "numDimensions": float(f["numDimensions"])} if f["type"] == "vector" else f
                           for f in wanted["fields"]]}
    assert db_client.vector_definition_covers(as_float, wanted)
    assert not db_client.vector_definition_covers(None, wanted)
    assert not db_client.vector_definition_covers({"fields": wanted["fields"][:1]}, wanted)


# ------------------------------------------------------------------- 3. keyword fallback


def _telling_memory(db, text: str, *, title: str = "A day", year: int = 1990) -> str:
    person = people_db.create_person(db, FAM, schemas.NewPerson(name="Gran"), added_by="u")
    return memories_db.create_memory(
        db, FAM, title=title, year=year, participant_ids=[person.id],
        telling=schemas.Telling(author_id=person.id, text=text),
    ).id


def test_sentence_split_is_linear_on_long_whitespace_runs(mdb):
    """`\\s*\\n+\\s*` backtracked over every newline-free whitespace run: quadratic (40k spaces ~ 4 s)."""
    # The runs must not follow sentence punctuation (that alternative consumed them in one go).
    filler = "We sang hymns" + " " * 30_000 + "and then the books fell." + "\n\n  The end" + " \t" * 15_000
    _telling_memory(mdb, filler, title="Hymn books")
    start = time.perf_counter()
    quotes = search_db.ask_quotes(mdb, FAM, "books fell")
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"ask took {elapsed:.2f}s on a long whitespace run"
    assert len(quotes) == 1
    assert quotes[0].text.startswith("We sang hymns ") and quotes[0].text.endswith(" and then the books fell.")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("One.  \n\n  Two", ["One.", "Two"]),
        ("line one   \n   line two\n\nline three", ["line one", "line two", "line three"]),
        ("No break at all", ["No break at all"]),
        ("\n\n  \n", []),
    ],
)
def test_sentence_split_semantics_unchanged(text, expected):
    assert search_db._sentences(text) == expected


@pytest.mark.parametrize("query", ["(a+)+$", ".*", "[", "hymn|books", "\\", "$where", "{\"$gt\": \"\"}"])
def test_keyword_search_treats_regex_and_operator_text_literally(mdb, query):
    """No regex or operator is ever built from user text: these neither crash nor match everything."""
    _telling_memory(mdb, "We dropped the hymn books on the steps.", title="Hymn books")
    _telling_memory(mdb, "Grandpa drove the green sedan.", title="Road trip", year=1975)
    hits = search_db.search_memories(mdb, FAM, query)
    titles = [h.title for h in hits]
    if query == "hymn|books":
        assert titles == ["Hymn books"]  # punctuation separates words; still only the matching memory
    else:
        assert titles == []
    assert props_db.find_props(mdb, FAM, query) == [] or query == "hymn|books"


def test_keyword_search_is_case_insensitive_and_stably_ordered(mdb):
    for year in (1990, 1980, 1985):
        _telling_memory(mdb, "The hymn books fell again.", title=f"Hymn books {year}", year=year)
    first = [h.memory_id for h in search_db.search_memories(mdb, FAM, "HYMN Books")]
    second = [h.memory_id for h in search_db.search_memories(mdb, FAM, "hymn books")]
    assert first == second and len(first) == 3
    # Equal scores: earlier year first, then id.
    assert [h.year for h in search_db.search_memories(mdb, FAM, "hymn")] == [1980, 1985, 1990]


# ----------------------------------------------------------------------- 4. tz-awareness


def test_real_client_is_built_tz_aware(monkeypatch):
    """pymongo's MongoClient defaults to tz_aware=False; get_client must turn it on."""
    monkeypatch.setattr(db_client, "_client", None)
    built = db_client.get_client()
    try:
        assert built.codec_options.tz_aware is True
        assert built.codec_options.tzinfo in (None, UTC) or built.codec_options.tzinfo.utcoffset(None) == timedelta(0)
    finally:
        built.close()
        monkeypatch.setattr(db_client, "_client", None)


@pytest.fixture
def naive_db():
    """Like a real MongoClient left at its default tz_aware=False: datetimes come back naive."""
    client = mongomock.MongoClient()
    assert client.codec_options.tz_aware is False
    yield client["stitch_naive"]
    client.close()


def test_invite_expiry_is_naive_safe(naive_db):
    """Comparing a naive stored expiresAt with datetime.now(UTC) would raise TypeError."""
    families_db.create_family(naive_db, name="N", creator_user_id="u1", family_id="fam_n")
    invite = families_db.create_invite(naive_db, "fam_n", "u1")  # real clock, as the route does
    stored = naive_db.families.find_one({"_id": "fam_n"})["invites"][0]["expiresAt"]
    assert isinstance(stored, datetime) and stored.tzinfo is None  # the divergence is real here

    found = families_db.find_active_invite(naive_db, invite.code)  # default now: aware
    assert found is not None and found[1].expires_at.tzinfo is not None
    assert [i.code for i in families_db.list_invites(naive_db, "fam_n")] == [invite.code]
    assert families_db.find_active_invite(naive_db, invite.code, now=datetime.now(UTC) + timedelta(days=15)) is None
    # A naive `now` from a caller is UTC too.
    assert families_db.find_active_invite(naive_db, invite.code, now=datetime.now(UTC).replace(tzinfo=None))
    assert families_db.add_member(naive_db, "fam_n", "u2")
    assert [s.id for s in families_db.list_family_summaries(naive_db, "u2")] == ["fam_n"]
    family = families_db.get_family(naive_db, "fam_n")
    assert all(m.joined_at.tzinfo is not None for m in family.members)


def test_session_revocation_and_expiry_is_naive_safe(naive_db):
    """Revocation stores a BSON date for the TTL index, from aware or naive input alike."""
    db_client.ensure_indexes(naive_db)
    aware = datetime.now(UTC) + timedelta(days=30)
    sessions_db.revoke_session(naive_db, "jti-aware", aware)
    sessions_db.revoke_session(naive_db, "jti-naive", aware.replace(tzinfo=None))
    assert sessions_db.is_revoked(naive_db, "jti-aware")
    assert sessions_db.is_revoked(naive_db, "jti-naive")
    assert not sessions_db.is_revoked(naive_db, "jti-other")
    for doc in naive_db.revoked_sessions.find():
        assert isinstance(doc["expiresAt"], datetime)  # a BSON date, so the TTL index applies
    ttl = naive_db.revoked_sessions.index_information()["revoked_sessions_expiresAt_ttl"]
    assert ttl["expireAfterSeconds"] == 0 and ttl["key"] == [("expiresAt", 1)]
    with pytest.raises(ValueError):
        sessions_db.revoke_session(naive_db, "jti-str", "2026-01-01T00:00:00Z")  # not a date: TTL would ignore it


def test_users_created_at_is_aware_on_a_naive_client(naive_db):
    user = users_db.create_user(naive_db, email="n@x.test", password_hash="h", name="N")
    assert user.created_at.tzinfo is not None
    # Read back through a naive client the stored value is naive; nothing compares it.
    assert users_db.get_user(naive_db, user.id).created_at is not None


# ------------------------------------------------------------- 5. duplicate-key mapping


def test_create_user_with_a_taken_id_is_not_reported_as_email_taken(mdb):
    db_client.ensure_indexes(mdb)
    users_db.create_user(mdb, email="a@x.test", password_hash="h", name="A", user_id="u1")
    with pytest.raises(ValueError, match="user id already exists") as caught:
        users_db.create_user(mdb, email="b@x.test", password_hash="h", name="B", user_id="u1")
    assert not isinstance(caught.value, users_db.EmailTaken)
    with pytest.raises(users_db.EmailTaken):
        users_db.create_user(mdb, email="A@X.test", password_hash="h", name="C", user_id="u2")
    with pytest.raises(users_db.EmailTaken):  # same id and same email: the email is what is taken
        users_db.create_user(mdb, email="a@x.test", password_hash="h", name="A", user_id="u1")
    assert mdb.users.count_documents({}) == 1


def test_create_family_with_a_taken_id_raises_value_error(mdb):
    families_db.create_family(mdb, name="One", creator_user_id="u1", family_id="f1")
    with pytest.raises(ValueError, match="family id already exists"):
        families_db.create_family(mdb, name="Two", creator_user_id="u2", family_id="f1")
    assert families_db.get_family(mdb, "f1").name == "One"


# --------------------------------------------------------------------------- 6. media


def test_get_media_on_a_corrupt_gridfs_file_is_none_not_an_error(mdb, monkeypatch):
    """A files doc whose chunks are gone made open_download_stream().read() raise CorruptGridFile."""
    oid = ObjectId()
    mdb[media_db.FILES].insert_one({
        "_id": oid, "length": 10, "chunkSize": 261120, "filename": "x.png",
        "metadata": {"familyId": FAM, "kind": "photo", "contentType": "image/png"},
    })

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            raise CorruptGridFile("no chunk #0")

    class Bucket:
        def open_download_stream(self, _oid):
            return Stream()

    monkeypatch.setattr(media_db, "_is_mock", lambda _db: False)
    monkeypatch.setattr(media_db, "_bucket", lambda _db: Bucket())
    assert media_db.media_exists(mdb, FAM, str(oid)) is True
    assert media_db.get_media(mdb, FAM, str(oid)) is None
    assert media_db.image_aspect(mdb, FAM, str(oid)) is None


def test_delete_family_media_removes_file_documents_before_chunks(mdb):
    """Chunks first left readable-looking file docs with no data if the purge stopped halfway."""
    oid = ObjectId()
    mdb[media_db.FILES].insert_one({"_id": oid, "length": 3, "metadata": {"familyId": FAM}})
    mdb[media_db.CHUNKS].insert_one({"files_id": oid, "n": 0, "data": b"abc"})
    order: list[str] = []

    class Spy:
        def __init__(self, name):
            self._name, self._inner = name, mdb[name]

        def delete_many(self, *args, **kwargs):
            order.append(self._name)
            return self._inner.delete_many(*args, **kwargs)

        def __getattr__(self, attr):
            return getattr(self._inner, attr)

    class SpyDb:
        def __getitem__(self, name):
            return Spy(name)

    assert media_db.delete_family_media(SpyDb(), FAM) == 1
    assert order == [media_db.INLINE, media_db.FILES, media_db.CHUNKS]
    assert mdb[media_db.CHUNKS].count_documents({}) == 0


# ------------------------------------------------------------------ 7. update operators


def test_answered_questions_match_a_missing_memory_id(mdb):
    """`memoryId: {$in: [None, id]}` must match a question stored with no memoryId field at all."""
    person = people_db.create_person(mdb, FAM, schemas.NewPerson(name="Gran"), added_by="u")
    mdb.questions.insert_one({"_id": "q_legacy", "familyId": FAM, "personId": person.id,
                              "text": "What did you sing?", "status": "open"})
    memories_db.create_memory(
        mdb, FAM, title="Hymns", year=1988, participant_ids=[person.id],
        telling=schemas.Telling(author_id=person.id, question=" What did you sing? ", text="Hymns."),
    )
    assert mdb.questions.find_one({"_id": "q_legacy"})["status"] == "answered"


def test_save_scene_update_documents_never_touch_one_path_twice(mdb, monkeypatch):
    """MongoDB rejects an update naming one path in two operators; mongomock does not."""
    person = people_db.create_person(mdb, FAM, schemas.NewPerson(name="Gran"), added_by="u")
    memory = memories_db.create_memory(mdb, FAM, title="Hymns", year=1988, participant_ids=[person.id])
    sent: list[dict] = []
    coll = mdb["memories"]

    class SpyMemories:
        def find_one_and_update(self, flt, update, *args, **kwargs):
            sent.append(update)
            return coll.find_one_and_update(flt, update, *args, **kwargs)

        def __getattr__(self, attr):
            return getattr(coll, attr)

    class SpyDb:
        def __getitem__(self, name):
            return SpyMemories() if name == "memories" else mdb[name]

    timeline = memories_db.Timeline(duration=1.0, tracks=[])
    memories_db.save_scene(SpyDb(), FAM, memory.id, screenplay=None, timeline=timeline,
                           prop_ids=[], set_ids=[], title="New")
    memories_db.save_scene(SpyDb(), FAM, memory.id, screenplay=None, timeline=timeline,
                           prop_ids=["p"], set_ids=[])
    memories_db.save_scene(SpyDb(), FAM, memory.id, screenplay=None, timeline=None,
                           prop_ids=[], set_ids=[])
    assert sent
    for update in sent:
        paths = [p for op in update.values() for p in op]
        assert len(paths) == len(set(paths)), update
        for a in paths:
            for b in paths:
                assert a == b or not b.startswith(a + "."), (a, b)
    assert memories_db.get_memory(mdb, FAM, memory.id).version == 3
