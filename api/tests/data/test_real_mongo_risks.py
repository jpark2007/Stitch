"""Real-server checks for the risks in api/db/REAL_MONGODB.md that test_real_mongo.py does not cover.

Every test takes the `real_db` fixture from conftest.py: it skips unless MONGODB_URI_TEST
is set and drops its throwaway database afterwards, so with no MongoDB the whole file
reports as skipped. Tests marked Atlas-only also skip unless the URI host ends in
`.mongodb.net`. Nothing here connects at import time. Each test names its register row.

Run against a DISPOSABLE database (never the demo one):

    cd api && MONGODB_URI_TEST='<disposable db uri>' uv run pytest -q tests/data/test_real_mongo_risks.py

The Atlas vector search test creates one search index in its throwaway database. On a
shared tier (M0 allows three search indexes per cluster) run it before the app creates its
own three, or against another cluster; it skips when Atlas refuses the index.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

import schemas
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import search as search_db
from db import users as users_db
from screenplay.schema import NarrationLine, NarrationTrack, Screenplay, Shot, Timeline
from seed import demo

pytestmark = pytest.mark.skipif(
    not os.environ.get("MONGODB_URI_TEST"), reason="MONGODB_URI_TEST is not set"
)

FAM = demo.DEMO_FAMILY_ID
GRIDFS_CHUNK = 255 * 1024  # GridFSBucket's default chunk size
INDEX_WAIT_S = float(os.environ.get("STITCH_TEST_SEARCH_INDEX_WAIT_S") or 180)


def _hosts(uri: str) -> list[str]:
    """Host names in a mongodb:// or mongodb+srv:// URI, without resolving DNS."""
    rest = uri.split("://", 1)[-1]
    rest = rest.rsplit("@", 1)[-1]  # drop credentials
    rest = rest.split("/", 1)[0].split("?", 1)[0]
    return [h.rsplit(":", 1)[0].strip("[]").lower() for h in rest.split(",") if h]


def _is_atlas() -> bool:
    hosts = _hosts(os.environ.get("MONGODB_URI_TEST") or "")
    return bool(hosts) and all(h.endswith(".mongodb.net") for h in hosts)


atlas_only = pytest.mark.skipif(not _is_atlas(), reason="needs an Atlas MONGODB_URI_TEST (*.mongodb.net)")


@pytest.fixture(autouse=True)
def _no_embedder():
    search_db.set_embedder(None)
    yield
    search_db.set_embedder(None)


@pytest.fixture
def seeded(real_db):
    db_client.ensure_indexes(real_db)
    demo.seed_demo(real_db)
    return real_db


def _race(n: int, fn: Callable[[int], Any]) -> list[tuple[str, Any]]:
    """Run fn(0..n-1) on n threads released together. [('ok', result) | ('err', exc)]."""
    barrier = threading.Barrier(n)
    out: list[tuple[str, Any]] = [("err", RuntimeError("did not run"))] * n

    def run(i: int) -> None:
        barrier.wait(timeout=30)
        try:
            out[i] = ("ok", fn(i))
        except Exception as exc:  # noqa: BLE001 - the caller asserts on it
            out[i] = ("err", exc)

    threads = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not any(t.is_alive() for t in threads), "a racing write hung"
    return out


# ------------------------------------------------------------- R1: server version, writes


def test_server_version_and_write_options(real_db):
    """Field names with '.'/'$' need 5.0+; $vectorSearch needs Atlas 6.0.11+ or 7.0.2+."""
    version = tuple(int(p) for p in real_db.client.server_info()["version"].split(".")[:3] if p.isdigit())
    assert version >= (5, 0), version
    if _is_atlas():
        assert version >= (6, 0, 11), version
        # Atlas URIs default to retryable writes; the db layer relies on single-document
        # writes being retried across a failover (update_many/delete_many are not).
        assert real_db.client.options.retry_writes is True
    assert real_db.client.write_concern.acknowledged is True
    assert real_db.client.codec_options.tz_aware is True  # the fixture's client, as get_client


# ------------------------------------------------------------- R12: millisecond datetimes


def test_writes_round_trip_to_the_millisecond_on_the_server(real_db):
    stamp = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)
    user = users_db.create_user(real_db, email="ms@x.test", password_hash="h", name="Ms", now=stamp)
    assert users_db.get_user(real_db, user.id) == user

    gran = people_db.create_person(real_db, "f1", schemas.NewPerson(name="Gran"), added_by="u")
    pop = people_db.create_person(real_db, "f1", schemas.NewPerson(name="Pop"), added_by="u")
    created = memories_db.create_memory(
        real_db, "f1", title="Hymns", year=1988, participant_ids=[gran.id],
        telling=schemas.Telling(author_id=gran.id, text="We sang."), now=stamp,
    )
    assert memories_db.get_memory(real_db, "f1", created.id) == created
    added = memories_db.add_telling(
        real_db, "f1", created.id, schemas.Telling(author_id=pop.id, text="Me too.", created_at=stamp)
    )
    assert memories_db.get_memory(real_db, "f1", created.id) == added
    assert added.tellings[1].created_at == stamp.replace(microsecond=678000)


@pytest.mark.xfail(
    reason="fix pending (wave 2, db/families.py): create_invite returns expires_at with "
    "microseconds, every later read has milliseconds",
    strict=False,
)
def test_invite_expiry_round_trips_to_the_millisecond(real_db):
    families_db.create_family(real_db, name="Ms", creator_user_id="u1", family_id="fam_ms")
    invite = families_db.create_invite(real_db, "fam_ms", "u1", now=datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC))
    assert families_db.list_invites(real_db, "fam_ms", active_only=False) == [invite]


# ---------------------------------------------------------------- R13: document size


def test_worst_case_memory_round_trips_on_the_server(seeded):
    """The largest seeded memory, re-saved with 50 narration tracks, stays storable and equal."""
    memories = [memories_db.get_memory(seeded, FAM, mid) for mid in demo.DEMO_MEMORY_IDS]
    largest = max(memories, key=lambda m: len(m.model_dump_json()))
    assert largest.timeline is not None and largest.timeline.narrations
    track = next(iter(largest.timeline.narrations.values()))
    narrations = {f"teller_{i}": track.model_copy(update={"author_id": f"teller_{i}"}) for i in range(50)}
    timeline = largest.timeline.model_copy(update={"narrations": narrations})

    saved = memories_db.save_scene(
        seeded, FAM, largest.id, screenplay=largest.screenplay, timeline=timeline,
        prop_ids=largest.prop_ids, set_ids=largest.set_ids,
    )
    assert saved is not None and saved.version == largest.version + 1
    assert memories_db.get_memory(seeded, FAM, largest.id) == saved
    assert memories_db.set_embedding(seeded, FAM, largest.id, [0.5] * 2048)
    assert memories_db.get_memory(seeded, FAM, largest.id) == saved


# ------------------------------------------------------- R14: '.' and '$' in stored keys


def _odd_id_scene(db, author: str, shot: str) -> schemas.Memory:
    other = people_db.create_person(db, "f1", schemas.NewPerson(name="Teller"), added_by="u", person_id=author)
    gran = people_db.create_person(db, "f1", schemas.NewPerson(name="Gran"), added_by="u")
    memory = memories_db.create_memory(db, "f1", title="Odd", year=1990, participant_ids=[gran.id, other.id])
    line = NarrationLine(t=0.0, text="It rained.")
    screenplay = Screenplay(
        memory_id=memory.id, cast=[gran.id, other.id],
        shots=[Shot(id=shot, set="s", narration=[line])],
        narrations={author: {shot: [line]}},
    )
    timeline = Timeline(
        duration=1.0, tracks=[],
        narrations={author: NarrationTrack(author_id=author, lines=[line])},
    )
    first = memories_db.save_scene(db, "f1", memory.id, screenplay=screenplay, timeline=timeline,
                                   prop_ids=[], set_ids=[])
    second = memories_db.save_scene(db, "f1", memory.id, screenplay=screenplay, timeline=timeline,
                                    prop_ids=[], set_ids=[], title="Odd again")
    assert first.screenplay == screenplay and first.timeline == timeline
    assert second.version == first.version + 1
    return second


def test_dotted_person_and_shot_ids_round_trip_through_narrations(real_db):
    """A '.' in a narrations key (person id, shot id) is stored by $set on MongoDB 5.0+."""
    saved = _odd_id_scene(real_db, "p.dot", "shot.1")
    assert memories_db.get_memory(real_db, "f1", saved.id) == saved


def test_dollar_prefixed_person_id_round_trips_through_narrations(real_db):
    """A '$'-prefixed narrations key, which mongomock always accepts. Unverified on Atlas."""
    saved = _odd_id_scene(real_db, "$p", "$shot")
    assert memories_db.get_memory(real_db, "f1", saved.id) == saved


# ------------------------------------------------------- R9/R10: Atlas vector search


@atlas_only
def test_vector_search_without_an_index_keeps_keyword_answers(seeded):
    """Atlas answers $vectorSearch on a missing index with nothing, silently: keywords must stay."""
    vector = [0.1] * db_client.VECTOR_DIM
    found = search_db._vector_search(seeded, FAM, vector, 5)
    assert found in ([], None), found  # [] is the documented Atlas answer; None = it raised
    query = "Who dropped the hymn books?"
    keyword = [h.memory_id for h in search_db.search_memories(seeded, FAM, query)]
    assert keyword and keyword == [h.memory_id for h in search_db.search_memories(seeded, FAM, query, vector=vector)]

    assert props_db._vector_search(seeded, props_db.PROPS, props_db.PROPS_VECTOR_INDEX, schemas.Prop,
                                   FAM, vector, 5) == []
    for text in ("hymn books", "green sedan"):
        assert props_db.find_props(seeded, FAM, text, vector=vector) == props_db.find_props(seeded, FAM, text)
        assert props_db.find_sets(seeded, FAM, text, vector=vector) == props_db.find_sets(seeded, FAM, text)


@atlas_only
def test_atlas_vector_search_answers_once_its_index_is_queryable(real_db):
    """Pending index: keywords answer. Queryable index: an identical vector is found, family-scoped."""
    db_client.ensure_indexes(real_db)
    gran = people_db.create_person(real_db, "f1", schemas.NewPerson(name="Gran"), added_by="u")
    mine = memories_db.create_memory(
        real_db, "f1", title="Hymn books", year=1988, participant_ids=[gran.id],
        telling=schemas.Telling(author_id=gran.id, text="The hymn books fell down the steps."),
    )
    other = people_db.create_person(real_db, "f2", schemas.NewPerson(name="Other"), added_by="u")
    theirs = memories_db.create_memory(real_db, "f2", title="Elsewhere", year=1988, participant_ids=[other.id])
    vector = [0.0] * db_client.VECTOR_DIM
    vector[0], vector[1] = 1.0, 0.5
    assert memories_db.set_embedding(real_db, "f1", mine.id, vector)
    assert memories_db.set_embedding(real_db, "f2", theirs.id, vector)

    name = db_client.VECTOR_INDEXES["memories"]
    try:
        real_db.memories.create_search_index(
            SearchIndexModel(definition=db_client._vector_definition(), name=name, type="vectorSearch")
        )
    except OperationFailure as exc:
        pytest.skip(f"Atlas refused the search index (tier limit?): {exc}")

    # While the index builds, a vector query must neither raise nor hide the keyword hit.
    pending = search_db.search_memories(real_db, "f1", "hymn books", vector=vector)
    assert [h.memory_id for h in pending] == [mine.id]

    deadline = time.monotonic() + INDEX_WAIT_S
    while True:
        listed = {ix["name"]: ix for ix in real_db.memories.list_search_indexes()}
        if listed.get(name, {}).get("queryable"):
            break
        if time.monotonic() > deadline:
            pytest.skip(f"{name} not queryable after {INDEX_WAIT_S:.0f}s: {listed.get(name)}")
        time.sleep(3)

    # A query sharing no word with the memory: only the vector can find it.
    deadline = time.monotonic() + 60
    while True:
        hits = search_db.search_memories(real_db, "f1", "zzqx unrelated", vector=vector)
        if hits or time.monotonic() > deadline:
            break
        time.sleep(2)
    assert [h.memory_id for h in hits] == [mine.id], "a queryable index found nothing (docs not synced?)"
    assert search_db.VECTOR_MIN_SCORE <= hits[0].score <= 1.0
    assert all(h.memory_id != theirs.id for h in search_db.search_memories(real_db, "f1", "zzqx", vector=vector))


# ------------------------------------------------------------------- R4/R5: real GridFS


@pytest.mark.parametrize(
    "length", [1, GRIDFS_CHUNK - 1, GRIDFS_CHUNK, GRIDFS_CHUNK + 1, 2 * GRIDFS_CHUNK, 3 * GRIDFS_CHUNK + 7]
)
def test_gridfs_chunk_boundaries_round_trip(real_db, length):
    data = bytes((i * 7 + 3) % 256 for i in range(length))
    media_id = media_db.put_media(real_db, "fam_chunks", data, content_type="audio/wav", kind="audio")
    got = media_db.get_media(real_db, "fam_chunks", media_id)
    assert got is not None and got.length == length and got.data == data
    # What a Range request around a chunk boundary is served from.
    edge = min(GRIDFS_CHUNK, length)
    assert got.data[edge - 1 : edge + 1] == data[edge - 1 : edge + 1]
    chunks = real_db[media_db.CHUNKS].count_documents({"files_id": ObjectId(media_id)})
    assert chunks == -(-length // GRIDFS_CHUNK)


def test_concurrent_puts_of_one_fixed_media_id(real_db):
    """Two seeds racing on one fixed id: one file wins intact, the other is a ValueError."""
    media_id = str(ObjectId())
    payloads = [bytes([i]) * (GRIDFS_CHUNK + 10) for i in (1, 2, 3, 4)]
    results = _race(4, lambda i: media_db.put_media(
        real_db, "fam_race", payloads[i], content_type="image/png", media_id=media_id))
    errors = [r for kind, r in results if kind == "err"]
    assert all(isinstance(e, ValueError) for e in errors), errors
    assert sum(1 for kind, _ in results if kind == "ok") == 1
    assert real_db[media_db.FILES].count_documents({"_id": ObjectId(media_id)}) == 1
    got = media_db.get_media(real_db, "fam_race", media_id)
    assert got is not None and got.data in payloads


# --------------------------------------------------------------------- R8: $graphLookup


def test_native_graph_lookup_equals_the_pure_fallback_on_the_demo_seed(seeded):
    docs = list(seeded.people.find({"familyId": FAM}))
    results = list(seeded.people.aggregate(people_db._tree_pipeline(FAM)))
    assert people_db._reached_from_aggregation(results) == people_db._reached_from_docs(docs)
    assert people_db._tree_docs(seeded, FAM) == people_db._tree_from_docs(docs)
    assert sorted(p.id for p in people_db.get_tree(seeded, FAM)) == sorted(demo.DEMO_PERSON_IDS)


# --------------------------------------------------------------- R6/R7: unique races


def test_concurrent_signups_with_one_email_create_one_account(real_db):
    db_client.ensure_indexes(real_db)
    results = _race(6, lambda i: users_db.create_user(
        real_db, email="Race@X.test", password_hash="h", name=f"R{i}"))
    errors = [r for kind, r in results if kind == "err"]
    assert sum(1 for kind, _ in results if kind == "ok") == 1
    assert all(isinstance(e, users_db.EmailTaken) for e in errors), errors
    assert real_db.users.count_documents({"email": "race@x.test"}) == 1


def test_concurrent_creates_and_claims_for_one_user_leave_one_person(real_db):
    db_client.ensure_indexes(real_db)
    for pid in ("c0", "c1", "c2"):
        people_db.create_person(real_db, "f1", schemas.NewPerson(name=pid), added_by="u", person_id=pid)

    def act(i: int):
        if i < 3:
            return people_db.claim_person(real_db, "f1", f"c{i}", "u1")
        return people_db.create_person(real_db, "f1", schemas.NewPerson(name=f"n{i}"), added_by="u", user_id="u1")

    results = _race(6, act)
    errors = [r for kind, r in results if kind == "err"]
    assert all(isinstance(e, people_db.AlreadyClaimed) for e in errors), errors
    assert real_db.people.count_documents({"familyId": "f1", "userId": "u1"}) == 1


def test_concurrent_demotions_always_leave_an_admin(real_db):
    """families.set_member_role compare-and-swaps the whole members array (no arrayFilters)."""
    families_db.create_family(real_db, name="Two admins", creator_user_id="a1", family_id="fam_cas")
    for uid in ("a2", "a3"):
        assert families_db.add_member(real_db, "fam_cas", uid, "admin")

    results = _race(3, lambda i: families_db.set_member_role(real_db, "fam_cas", f"a{i + 1}", "member"))
    errors = [r for kind, r in results if kind == "err"]
    assert all(isinstance(e, families_db.LastAdmin) for e in errors), errors
    family = families_db.get_family(real_db, "fam_cas")
    admins = [m.user_id for m in family.members if m.role == "admin"]
    assert len(admins) == 1 and len(family.members) == 3
    assert sum(1 for kind, _ in results if kind == "ok") == 2


# --------------------------------------------------------------- R15: sort with nulls


def test_book_puts_a_memory_without_created_at_first_on_the_server(real_db):
    gran = people_db.create_person(real_db, "f1", schemas.NewPerson(name="Gran"), added_by="u")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    later = memories_db.create_memory(real_db, "f1", title="Later", year=1990, participant_ids=[gran.id], now=base)
    real_db.memories.insert_one({"_id": "legacy", "familyId": "f1", "title": "Legacy", "year": 1990,
                                 "participantIds": [gran.id], "tellings": [], "propIds": [], "setIds": [],
                                 "version": 1})
    earlier = memories_db.create_memory(real_db, "f1", title="Earlier", year=1990, participant_ids=[gran.id],
                                        now=base - timedelta(days=1))
    assert [m.id for m in memories_db.get_book(real_db, "f1", gran.id)] == ["legacy", earlier.id, later.id]


# ------------------------------------------------------- R20: LangGraph's own indexes


def test_langgraph_persistence_starts_twice_on_one_database(real_db):
    """MongoDBSaver and MongoDBStore create their indexes on every start; the second must not conflict."""
    from langgraph.checkpoint.base import empty_checkpoint

    from agent import studio

    uri = os.environ["MONGODB_URI_TEST"]
    config = {"configurable": {"thread_id": "risk-thread", "checkpoint_ns": ""}}
    for attempt in range(2):
        saver, store = studio.mongo_persistence(uri, db_name=real_db.name)
        try:
            if attempt == 0:
                saver.put(config, empty_checkpoint(), {"source": "input", "step": -1}, {})
                store.put(("fam_risk", "notes"), "k", {"text": "hello"})
            assert saver.get_tuple(config) is not None
            assert store.get(("fam_risk", "notes"), "k").value == {"text": "hello"}
        finally:
            saver.client.close()
    names = set(real_db.list_collection_names())
    assert {"agent_checkpoints", "agent_checkpoint_writes", "agent_store"} <= names
    assert any(ix.get("unique") for ix in real_db.agent_checkpoints.index_information().values())


# ---------------------------------------------- R2/R3: index conflicts (fix pending, wave 2)


@pytest.mark.xfail(
    reason="fix pending (wave 2, db/client.py): ensure_indexes stops at the first index the "
    "server refuses, so every later index and migrate_legacy_ids are skipped",
    strict=False,
)
def test_one_refused_index_does_not_skip_the_others(real_db):
    real_db.people.insert_many([
        {"_id": "a", "familyId": "f", "userId": "u", "parentIds": []},
        {"_id": "b", "familyId": "f", "userId": "u", "parentIds": []},
    ])
    try:
        db_client.ensure_indexes(real_db)
    except OperationFailure:
        pass
    later = {name for name, _keys, _opts in db_client.INDEXES["memories"]}
    assert later <= set(real_db.memories.index_information())


@pytest.mark.xfail(
    reason="fix pending (wave 2, db/client.py): an index with the same keys under another name "
    "(made by hand in the Atlas UI) is an IndexOptionsConflict that stops ensure_indexes",
    strict=False,
)
def test_an_index_with_the_same_keys_under_another_name_is_accepted(real_db):
    real_db.users.create_index([("email", 1)], name="email_1", unique=True)
    db_client.ensure_indexes(real_db)  # must not raise
    assert "revoked_sessions_expiresAt_ttl" in real_db.revoked_sessions.index_information()
