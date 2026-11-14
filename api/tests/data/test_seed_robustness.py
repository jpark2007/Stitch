"""The demo has to survive being run the way teammates run it (mongomock throughout).

- reseed: seed_demo twice keeps every id and session; reset after real use restores the
  demo family exactly and leaves the demo users' other families and all non-demo data
  alone, with no orphans; an interrupted reset heals on the next run; CLI exit codes.
- purge: purge_family removes every family-scoped document in the data layer's
  collections, GridFS media included, and nothing of any other family.
- startup: install_fake_db() cold start stays inside a time budget, and its timelines are
  byte-identical to a fresh compile.
- determinism: two fresh fake databases hold the same documents (ignoring timestamps and
  salted password hashes), so every teammate's demo matches the rehearsed one.
- db edge cases a live demo can hit: unicode and emoji names, very long text, renamed or
  replaced props under a stored scene, media ids from another family.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import mongomock
import pytest
from bson import ObjectId
from pymongo.errors import ServerSelectionTimeoutError

import auth
import schemas
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import search as search_db
from db import sessions as sessions_db
from db import users as users_db
from screenplay import compiler
from seed import demo
from seed.demo import (
    DEMO_ACCOUNTS,
    DEMO_FAMILY_ID,
    DEMO_MEMORY_IDS,
    DEMO_PASSWORD,
    DEMO_QUESTIONS,
    compile_demo_scenes,
    seed_demo,
)
from seed.fake import install_fake_db

API_DIR = Path(__file__).resolve().parents[2]
DAVID, ANA, SAM = (a.user_id for a in DEMO_ACCOUNTS)
STARTUP_BUDGET_SECONDS = 20.0


# ------------------------------------------------------------------------------ helpers

def _fresh_db(name: str = "stitch_robust") -> Any:
    db = mongomock.MongoClient(tz_aware=True)[name]
    db_client.ensure_indexes(db)
    return db


def _strip(value: Any) -> Any:
    """A document with every timestamp and salted password hash removed (recursively)."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items()
                if not isinstance(v, datetime) and k != "passwordHash"}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _dump(db, *, family_id: str | None = None) -> dict[str, list]:
    """Every collection, sorted by _id, without timestamps. With family_id: only that family."""
    out: dict[str, list] = {}
    for name in sorted(db.list_collection_names()):
        if family_id is None:
            query: dict = {}
        elif name == "families":
            query = {"_id": family_id}
        elif name.startswith(f"{media_db.BUCKET}."):
            query = {"metadata.familyId": family_id}
        elif name in families_db.FAMILY_SCOPED:
            query = {"familyId": family_id}
        else:
            continue
        out[name] = [_strip(d) for d in db[name].find(query, sort=[("_id", 1)])]
    return out


def _session_user(db, token: str) -> str | None:
    """What auth resolves a session cookie to (None when the session no longer works)."""
    user = auth._user_from_cookie(token, db)
    return user.id if user else None


def _orphans(db) -> list[str]:
    """Family-scoped documents whose family, person or memory no longer exists."""
    families = {d["_id"] for d in db.families.find({}, {"_id": 1})}
    found: list[str] = []
    people = {(d["familyId"], d["_id"]) for d in db.people.find({}, {"familyId": 1})}
    for coll in families_db.FAMILY_SCOPED:
        for doc in db[coll].find({}, {"familyId": 1}):
            if doc.get("familyId") not in families:
                found.append(f"{coll}/{doc['_id']} (family {doc.get('familyId')})")
    for doc in db.media.inline.find({}, {"metadata": 1}):
        if doc["metadata"]["familyId"] not in families:
            found.append(f"media/{doc['_id']}")
    for q in db.questions.find({}):
        if (q["familyId"], q["personId"]) not in people:
            found.append(f"question {q['_id']} for missing person {q['personId']}")
        if q.get("memoryId") and not db.memories.count_documents({"_id": q["memoryId"], "familyId": q["familyId"]}):
            found.append(f"question {q['_id']} for missing memory {q['memoryId']}")
    for m in db.memories.find({}):
        for pid in [*m.get("participantIds", []), *(t["authorId"] for t in m.get("tellings", []))]:
            if (m["familyId"], pid) not in people:
                found.append(f"memory {m['_id']} names missing person {pid}")
        for photo in (p for t in m.get("tellings", []) for p in t.get("photoIds", [])):
            if not media_db.media_exists(db, m["familyId"], photo):
                found.append(f"memory {m['_id']} names missing photo {photo}")
    for family in db.families.find({}):
        for member in family["members"]:
            if users_db.get_user(db, member["userId"]) is None:
                found.append(f"family {family['_id']} member {member['userId']} has no account")
    return found


@pytest.fixture
def seeded():
    db = _fresh_db()
    seed_demo(db)
    return db


@pytest.fixture
def restore_db():
    yield
    db_client.set_db(None)


# ------------------------------------------------------------------------------- reseed

def test_seeding_again_keeps_every_id_and_every_session(seeded):
    db = seeded
    tokens = {a.user_id: auth.create_session_token(a.user_id) for a in DEMO_ACCOUNTS}
    before = _dump(db)

    for _ in range(2):
        assert seed_demo(db).created is False

    assert _dump(db) == before
    for user_id, token in tokens.items():
        assert _session_user(db, token) == user_id
    for coll in ("people", "memories", "props", "sets", "questions"):
        ids = [d["_id"] for d in db[coll].find({}, {"_id": 1})]
        assert len(ids) == len(set(ids)), coll


def _use_the_demo(db) -> dict[str, Any]:
    """Everything a demo audience might do before someone resets the demo."""
    now = datetime(2026, 11, 14, 12, 0)
    stranger = users_db.create_user(db, email="guest@example.com", password_hash="x", name="Guest")
    # David adds a memory and a person to the demo family and uploads a photo there.
    uncle = people_db.create_person(db, DEMO_FAMILY_ID, schemas.NewPerson(name="Uncle Ray"),
                                    added_by=DAVID)
    memories_db.create_memory(db, DEMO_FAMILY_ID, title="Added on stage", year=2020,
                              participant_ids=["sam", uncle.id],
                              telling=schemas.Telling(author_id="dad", text="We added this live."))
    media_db.put_media(db, DEMO_FAMILY_ID, b"stage photo", content_type="image/png")
    memories_db.create_question(db, DEMO_FAMILY_ID, person_id=uncle.id, text="Where were you?")
    # Sam answers his question about Lily's graduation.
    memories_db.add_telling(db, DEMO_FAMILY_ID, "demo_lily_graduation",
                            schemas.Telling(author_id="sam", text="I saw her trip. Then the bow."))
    assert not memories_db.open_questions(db, DEMO_FAMILY_ID, "sam")
    # A guest joins the demo family and claims Lily; David promotes Ana.
    families_db.add_member(db, DEMO_FAMILY_ID, stranger.id, "member", now=now)
    people_db.claim_person(db, DEMO_FAMILY_ID, "lily", stranger.id)
    families_db.set_member_role(db, DEMO_FAMILY_ID, ANA, "admin")
    invite = families_db.create_invite(db, DEMO_FAMILY_ID, DAVID)
    assert invite is not None
    # David starts a second family with its own person, memory, question and photo.
    second = families_db.create_family(db, name="David's other family", creator_user_id=DAVID, now=now)
    me = people_db.create_person(db, second.id, schemas.NewPerson(name="David"), added_by=DAVID, user_id=DAVID)
    memories_db.create_memory(db, second.id, title="Moving day", year=2001, participant_ids=[me.id])
    memories_db.create_question(db, second.id, person_id=me.id, text="What did you pack first?")
    media_db.put_media(db, second.id, b"other photo", content_type="image/png")
    # Ana joins the guest's family and claims herself there.
    guests = families_db.create_family(db, name="The Guests", creator_user_id=stranger.id, now=now)
    ana_there = people_db.create_person(db, guests.id, schemas.NewPerson(name="Ana"), added_by=stranger.id)
    families_db.add_member(db, guests.id, ANA, "member", now=now)
    people_db.claim_person(db, guests.id, ana_there.id, ANA)
    return {"stranger": stranger.id, "second": second.id, "guests": guests.id, "invite": invite.code}


def test_reset_after_real_use_restores_the_demo_exactly_and_keeps_everything_else(seeded):
    db = seeded
    pristine = _dump(_seeded_copy(), family_id=DEMO_FAMILY_ID)
    used = _use_the_demo(db)
    tokens = {a.user_id: auth.create_session_token(a.user_id) for a in DEMO_ACCOUNTS}
    guest_token = auth.create_session_token(used["stranger"])
    outside = {fid: _dump(db, family_id=fid) for fid in (used["second"], used["guests"])}
    guest_account = _strip(db.users.find_one({"_id": used["stranger"]}))

    assert seed_demo(db, reset=True).created is True

    # The demo family is exactly a fresh seed again (same ids, same members and roles).
    assert _dump(db, family_id=DEMO_FAMILY_ID) == pristine
    assert families_db.find_active_invite(db, used["invite"]) is None
    # The demo users' other families, and the guest, are untouched.
    for fid, snapshot in outside.items():
        assert _dump(db, family_id=fid) == snapshot
    assert _strip(db.users.find_one({"_id": used["stranger"]})) == guest_account
    assert [s.id for s in families_db.list_family_summaries(db, DAVID)] == [DEMO_FAMILY_ID, used["second"]]
    assert [s.id for s in families_db.list_family_summaries(db, ANA)] == [DEMO_FAMILY_ID, used["guests"]]
    assert families_db.get_member_role(db, used["second"], DAVID) == "admin"
    assert people_db.get_person_for_user(db, used["guests"], ANA) is not None
    assert [s.id for s in families_db.list_family_summaries(db, used["stranger"])] == [used["guests"]]
    # Sessions survive: the accounts come back under the same ids.
    for user_id, token in tokens.items():
        assert _session_user(db, token) == user_id
    assert _session_user(db, guest_token) == used["stranger"]
    # Nothing left dangling anywhere.
    assert _orphans(db) == []


def _seeded_copy():
    db = _fresh_db("stitch_pristine")
    seed_demo(db)
    return db


def test_a_logged_out_demo_session_stays_logged_out_after_reset(seeded):
    db = seeded
    token = auth.create_session_token(DAVID)
    claims = auth.decode_session_claims(token)
    sessions_db.revoke_session(db, claims["jti"], datetime.fromtimestamp(claims["exp"]).astimezone())

    seed_demo(db, reset=True)

    assert _session_user(db, token) is None


def test_an_interrupted_reset_heals_on_the_next_plain_run(seeded, monkeypatch):
    """Bug: the reset deleted the accounts before the family. A run killed in between left
    the family with no accounts, and the next seed_demo() saw the family and did nothing,
    so no demo login worked until someone knew to pass --reset."""
    db = seeded
    real_delete = users_db.delete_users

    def delete_then_die(*args, **kwargs):
        real_delete(*args, **kwargs)
        raise KeyboardInterrupt  # the teammate hit Ctrl-C mid-reset

    monkeypatch.setattr(users_db, "delete_users", delete_then_die)
    with pytest.raises(KeyboardInterrupt):
        seed_demo(db, reset=True)
    monkeypatch.setattr(users_db, "delete_users", real_delete)

    assert seed_demo(db).created is True
    assert demo.demo_is_complete(db)
    login = users_db.get_login(db, "ana@demo.stitch")
    assert login is not None and auth.verify_password(login[1], DEMO_PASSWORD)
    assert _dump(db, family_id=DEMO_FAMILY_ID) == _dump(_seeded_copy(), family_id=DEMO_FAMILY_ID)


def test_a_demo_with_its_accounts_deleted_is_rebuilt_without_reset(seeded):
    db = seeded
    users_db.delete_users(db, emails=[], user_ids=[SAM])
    assert not demo.demo_is_complete(db)

    assert seed_demo(db).created is True
    assert users_db.get_user(db, SAM) is not None and demo.demo_is_complete(db)


def test_the_family_document_is_written_with_every_member_at_once(mdb, monkeypatch):
    """Bug: the family was created with David only and Ana and Sam were added after it. A
    run that stopped in between left a family that counted as a finished seed, without
    Ana and Sam."""
    def no_add_member(*args, **kwargs):
        raise AssertionError("the seed must not add members after the family exists")

    monkeypatch.setattr(families_db, "add_member", no_add_member)
    seed_demo(mdb)
    family = families_db.get_family(mdb, DEMO_FAMILY_ID)
    assert [(m.user_id, m.role) for m in family.members] == [(a.user_id, a.role) for a in DEMO_ACCOUNTS]
    joined = [m.joined_at for m in family.members]
    assert joined == sorted(joined) and len(set(joined)) == len(joined)


def test_create_family_with_members(mdb):
    family = families_db.create_family(
        mdb, name="Two", creator_user_id="u1",
        members=[schemas.Member(user_id="u2", role="member"), schemas.Member(user_id="u3", role="admin")],
    )
    assert [(m.user_id, m.role) for m in family.members] == [("u1", "admin"), ("u2", "member"), ("u3", "admin")]
    assert families_db.get_member_role(mdb, family.id, "u3") == "admin"
    for bad in ([schemas.Member(user_id="u1", role="member")],
                [schemas.Member(user_id="u2", role="member"), schemas.Member(user_id="u2", role="admin")],
                [schemas.Member(user_id="", role="member")]):
        with pytest.raises(ValueError):
            families_db.create_family(mdb, name="Bad", creator_user_id="u1", members=bad)
    assert mdb.families.count_documents({}) == 1


# ---------------------------------------------------------------------------------- CLI

def test_cli_exit_codes_and_messages(restore_db, capsys, tmp_path, monkeypatch):
    db = mongomock.MongoClient(tz_aware=True)["stitch_cli"]
    db_client.set_db(db)

    assert demo.main([]) == demo.EXIT_OK
    out = capsys.readouterr()
    assert "seeded" in out.out and out.err == ""
    assert demo.main([]) == demo.EXIT_OK and "already present" in capsys.readouterr().out
    assert demo.main(["--reset"]) == demo.EXIT_OK and "seeded" in capsys.readouterr().out

    with pytest.raises(SystemExit) as bad_args:
        demo.main(["--wipe-everything"])
    assert bad_args.value.code == 2
    capsys.readouterr()

    # Bad demo data: one message naming the file on stderr, and the existing demo stays.
    before = _dump(db)
    broken = tmp_path / "demo_family"
    broken.mkdir()
    for path in demo.DATA_DIR.iterdir():
        (broken / path.name).write_text(path.read_text())
    (broken / "props.json").write_text("[not json")
    monkeypatch.setattr(demo, "DATA_DIR", broken)
    assert demo.main(["--reset"]) == demo.EXIT_BAD_DATA
    out = capsys.readouterr()
    assert "props.json" in out.err and "Traceback" not in out.err and out.out == ""
    assert _dump(db) == before
    monkeypatch.setattr(demo, "DATA_DIR", API_DIR / "seed" / "demo_family")

    # MongoDB unreachable: a hint, not a traceback.
    def unreachable(_db):
        raise ServerSelectionTimeoutError("localhost:27017: [Errno 61] Connection refused")

    monkeypatch.setattr(db_client, "ensure_indexes", unreachable)
    assert demo.main([]) == demo.EXIT_NO_DATABASE
    err = capsys.readouterr().err
    assert "MONGODB_URI" in err and "STITCH_FAKE_DB=1" in err and "Traceback" not in err


def test_cli_help_runs_as_a_module():
    done = subprocess.run([sys.executable, "-m", "seed.demo", "--help"], cwd=API_DIR,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "--reset" in done.stdout


# -------------------------------------------------------------------------------- purge

def _populate(db, family_id: str, user_id: str) -> None:
    """One of everything a family can own, written through the db layer, plus GridFS-shaped
    media as a real server stores it (mongomock uses the inline store for put_media)."""
    families_db.create_family(db, name=f"Family {family_id}", creator_user_id=user_id, family_id=family_id)
    me = people_db.create_person(db, family_id, schemas.NewPerson(name="Me \U0001f600"), added_by=user_id,
                                 user_id=user_id)
    memory = memories_db.create_memory(db, family_id, title="A day", year=2000, participant_ids=[me.id],
                                       telling=schemas.Telling(author_id=me.id, text="It was a day."))
    memories_db.set_embedding(db, family_id, memory.id, [0.1, 0.2, 0.3])
    memories_db.create_question(db, family_id, person_id=me.id, text="And then?", memory_id=memory.id)
    sample = demo.load_sample()
    props_db.insert_prop(db, family_id, sample.props[0], prop_id="hymn_books", embedding=[0.5, 0.5])
    props_db.insert_set(db, family_id, sample.sets[0], set_id="church", embedding=[0.5, 0.5])
    # A prop and a set in the legacy storage layout (_id = bare id, no `id` field).
    db.props.insert_one({**sample.props[0].model_dump(by_alias=True, mode="json"),
                         "_id": f"legacy_{family_id}", "familyId": family_id})
    db.sets.insert_one({**sample.sets[0].model_dump(by_alias=True, mode="json"),
                        "_id": f"legacy_set_{family_id}", "familyId": family_id})
    db.props.update_one({"_id": f"legacy_{family_id}"}, {"$unset": {"id": ""}})
    db.sets.update_one({"_id": f"legacy_set_{family_id}"}, {"$unset": {"id": ""}})
    media_db.put_media(db, family_id, b"inline bytes", content_type="image/png")
    file_id = ObjectId()
    db[media_db.FILES].insert_one({"_id": file_id, "length": 6, "chunkSize": 261120, "filename": "x.png",
                                   "metadata": {"familyId": family_id, "kind": "photo",
                                                "contentType": "image/png"}})
    db[media_db.CHUNKS].insert_many([{"files_id": file_id, "n": 0, "data": b"abc"},
                                     {"files_id": file_id, "n": 1, "data": b"def"}])


def _family_refs(db, family_id: str) -> list[str]:
    """Every document anywhere in the database that belongs to family_id."""
    found = []
    file_ids = {d["_id"] for d in db[media_db.FILES].find({"metadata.familyId": family_id}, {"_id": 1})}
    for name in db.list_collection_names():
        for doc in db[name].find({}):
            if (doc.get("familyId") == family_id or (doc.get("metadata") or {}).get("familyId") == family_id
                    or (name == "families" and doc["_id"] == family_id) or doc.get("files_id") in file_ids):
                found.append(f"{name}/{doc['_id']}")
    return found


def test_purge_family_removes_everything_of_one_family_and_nothing_else(mdb):
    db_client.ensure_indexes(mdb)
    _populate(mdb, "fam_a", "user_a")
    _populate(mdb, "fam_b", "user_b")
    chunks_b = list(mdb[media_db.CHUNKS].find({"files_id": {"$in": [
        d["_id"] for d in mdb[media_db.FILES].find({"metadata.familyId": "fam_b"})]}}))
    assert len(chunks_b) == 2
    b_before = _dump(mdb, family_id="fam_b")
    users_before = list(mdb.users.find({}))
    assert len(_family_refs(mdb, "fam_a")) >= 11

    families_db.purge_family(mdb, "fam_a")

    assert _family_refs(mdb, "fam_a") == []
    assert _dump(mdb, family_id="fam_b") == b_before
    assert list(mdb[media_db.CHUNKS].find({"files_id": chunks_b[0]["files_id"]})) == chunks_b
    assert list(mdb.users.find({})) == users_before


def test_every_family_scoped_collection_is_purged(mdb):
    """Any collection in which the data layer writes a familyId must be one purge_family
    covers: FAMILY_SCOPED or the media bucket. A new family-scoped collection that is not
    added there fails this test."""
    db_client.ensure_indexes(mdb)
    _populate(mdb, "fam_a", "user_a")
    seed_demo(mdb)
    scoped = {name for name in mdb.list_collection_names()
              if mdb[name].count_documents({"$or": [{"familyId": {"$exists": True}},
                                                    {"metadata.familyId": {"$exists": True}}]})}
    media = {media_db.FILES, media_db.INLINE}
    assert scoped - media == set(families_db.FAMILY_SCOPED)


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_purge_refuses_a_blank_family_id(seeded, bad):
    before = _dump(seeded)
    with pytest.raises(ValueError):
        families_db.purge_family(seeded, bad)
    assert _dump(seeded) == before


# ------------------------------------------------------------------------------ startup

def test_install_fake_db_cold_start_is_inside_the_budget():
    """A fresh interpreter (imports, indexes, compiling every moment, writing the family)."""
    script = (
        "import time; t = time.perf_counter()\n"
        "from seed.fake import install_fake_db\n"
        "install_fake_db()\n"
        "print(f'{time.perf_counter() - t:.3f}')\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("MONGODB_URI", "VOYAGE_API_KEY")}
    started = time.perf_counter()
    done = subprocess.run([sys.executable, "-c", script], cwd=API_DIR, env=env,
                          capture_output=True, text=True, timeout=120)
    wall = time.perf_counter() - started
    assert done.returncode == 0, done.stderr
    measured = float(done.stdout.strip().splitlines()[-1])
    print(f"install_fake_db() cold: {measured:.2f}s in-process, {wall:.2f}s with interpreter start")
    assert measured < STARTUP_BUDGET_SECONDS


def _timeline_json(timeline) -> str:
    return json.dumps(timeline.model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=True)


def test_compiled_timelines_are_byte_identical_and_stored_as_compiled(restore_db):
    first = {mid: _timeline_json(s.timeline) for mid, s in compile_demo_scenes().items()}
    second = {mid: _timeline_json(s.timeline) for mid, s in compile_demo_scenes().items()}
    assert list(first) == list(DEMO_MEMORY_IDS)
    assert first == second

    db = install_fake_db()
    for memory_id, expected in first.items():
        stored = db.memories.find_one({"_id": memory_id})["timeline"]
        assert json.dumps(stored, sort_keys=True) == expected, memory_id


# -------------------------------------------------------------------------- determinism

def test_two_fresh_fake_databases_hold_the_same_documents(restore_db):
    """Bug: the demo photos and questions got fresh random ids on every seed, so the road
    trip's tellings, the question list and every media URL differed between machines."""
    first = _dump(install_fake_db())
    second = _dump(install_fake_db())
    assert first == second
    for coll in ("memories", "people", "props", "sets", "questions", media_db.INLINE, "families", "users"):
        assert first[coll], coll

    trip = next(m for m in first["memories"] if m["_id"] == "demo_green_sedan_trip")
    assert [p for t in trip["tellings"] for p in t.get("photoIds", [])] == list(demo.DEMO_PHOTO_IDS)
    assert sorted(q["_id"] for q in first["questions"]) == list(demo.DEMO_QUESTION_IDS)
    assert all(ObjectId.is_valid(i) for i in (*demo.DEMO_PHOTO_IDS, *demo.DEMO_QUESTION_IDS))


def test_seeded_questions_list_in_a_fixed_order(seeded):
    """Newest first, as listed: Ana's general prompt (the last question) comes first."""
    mom = [q.text for q in memories_db.open_questions(seeded, DEMO_FAMILY_ID, "mom")]
    expected = [text for pid, text, _ in reversed(DEMO_QUESTIONS) if pid == "mom"]
    assert mom == expected


# ----------------------------------------------------------------------- db edge cases

def test_fixed_media_and_question_ids(mdb):
    fixed = demo.DEMO_PHOTO_IDS[0]
    assert media_db.put_media(mdb, "f1", b"x", content_type="image/png", media_id=f"media/{fixed}") == fixed
    with pytest.raises(ValueError, match="already exists"):
        media_db.put_media(mdb, "f2", b"y", content_type="image/png", media_id=fixed)  # other family too
    with pytest.raises(ValueError, match="not a valid id"):
        media_db.put_media(mdb, "f1", b"y", content_type="image/png", media_id="nope")
    assert media_db.get_media(mdb, "f1", fixed).data == b"x"
    assert media_db.get_media(mdb, "f2", fixed) is None

    families_db.create_family(mdb, name="F", creator_user_id="u", family_id="f1")
    person = people_db.create_person(mdb, "f1", schemas.NewPerson(name="P"), added_by="u")
    q = memories_db.create_question(mdb, "f1", person_id=person.id, text="Why?", question_id="q1")
    assert q.id == "q1"
    with pytest.raises(ValueError, match="already exists"):
        memories_db.create_question(mdb, "f1", person_id=person.id, text="Again?", question_id="q1")
    with pytest.raises(ValueError):
        memories_db.create_question(mdb, "f1", person_id=person.id, text="Blank?", question_id=" ")
    assert mdb.questions.count_documents({}) == 1


@pytest.mark.parametrize("name", [
    "Abuela \U0001f33a Ñoño",
    "외할머니 李",
    "Zoé",  # decomposed accent: stored as given, never normalised away
    "\U0001f468‍\U0001f469‍\U0001f467",  # a ZWJ family emoji
    "A" * 5000,
])
def test_unicode_emoji_and_long_person_names_round_trip(seeded, name):
    db = seeded
    person = people_db.create_person(db, DEMO_FAMILY_ID, schemas.NewPerson(name=name, born=1950,
                                     parent_ids=["grandpa_joseph"]), added_by=DAVID)
    assert people_db.get_person(db, DEMO_FAMILY_ID, person.id).name == name
    assert name in [p.name for p in people_db.get_tree(db, DEMO_FAMILY_ID)]
    assert name in [p.name for p in people_db.list_people(db, DEMO_FAMILY_ID)]
    renamed = people_db.update_person(db, DEMO_FAMILY_ID, person.id, schemas.PersonPatch(name=name + "!"))
    assert renamed.name == name + "!"
    memory = memories_db.create_memory(db, DEMO_FAMILY_ID, title=name, year=1990, participant_ids=[person.id],
                                       telling=schemas.Telling(author_id=person.id, text=name))
    assert memories_db.get_book(db, DEMO_FAMILY_ID, person.id)[0].title == name
    assert memory.id in [m.id for m in memories_db.moment_summaries(db, DEMO_FAMILY_ID)]


def test_emoji_prop_names_are_saved_listed_and_found(seeded):
    """Bug: a prop named only by an emoji had no search tokens, so find_props could never
    return it, not even for its exact name."""
    db = seeded
    base = props_db.get_prop(db, DEMO_FAMILY_ID, "sam_cap")
    cake = props_db.insert_prop(db, DEMO_FAMILY_ID, base.model_copy(update={"name": "\U0001f382",
                                                                           "description": None}))
    horn = props_db.insert_prop(db, DEMO_FAMILY_ID, base.model_copy(update={
        "name": "\U0001f3ba trumpet", "description": "Abuelo's \U0001f3ba, dented"}), prop_id="horn")

    assert {cake.id, "horn"} <= {p.id for p in props_db.list_props(db, DEMO_FAMILY_ID)}
    assert [p.id for p in props_db.find_props(db, DEMO_FAMILY_ID, "\U0001f382")] == [cake.id]
    assert props_db.find_props(db, DEMO_FAMILY_ID, "trumpet")[0].id == horn.id
    # A symbol beside a word is not a token, so the exact-name score is unchanged.
    assert props_db._keyword_score("trumpet", horn.name, horn.description) >= 1.0
    assert props_db.find_props(db, DEMO_FAMILY_ID, "") == []


def test_very_long_tellings_store_list_and_search(seeded):
    db = seeded
    text = "We drove to the coast and ate cold watermelon on the sand. " * 4000  # about 240 KB
    memory = memories_db.create_memory(db, DEMO_FAMILY_ID, title="The longest summer " + "\U0001f30a" * 200,
                                       year=2001, participant_ids=["sam"],
                                       telling=schemas.Telling(author_id="sam", text=text))
    assert memories_db.get_memory(db, DEMO_FAMILY_ID, memory.id).tellings[0].text == text
    hits = search_db.search_memories(db, DEMO_FAMILY_ID, "watermelon")
    assert memory.id in [h.memory_id for h in hits]
    quotes = search_db.ask_quotes(db, DEMO_FAMILY_ID, "watermelon")
    assert quotes and all(len(q.text) < 500 for q in quotes)


def _recompile(db, memory_id: str):
    memory = memories_db.get_memory(db, DEMO_FAMILY_ID, memory_id)
    return memory, compiler.compile(
        memory.screenplay,
        sets={s.id: s for s in props_db.get_sets(db, DEMO_FAMILY_ID, memory.set_ids)},
        props={p.id: p for p in props_db.get_props(db, DEMO_FAMILY_ID, memory.prop_ids)},
        people={p.id: p for p in people_db.list_people(db, DEMO_FAMILY_ID)},
    )


@pytest.mark.parametrize("memory_id", DEMO_MEMORY_IDS)
def test_a_stored_scene_still_compiles_after_its_props_are_renamed_or_replaced(seeded, memory_id):
    db = seeded
    memory = memories_db.get_memory(db, DEMO_FAMILY_ID, memory_id)
    for prop_id in memory.prop_ids:
        renamed = props_db.update_prop(db, DEMO_FAMILY_ID, prop_id,
                                       schemas.PropPatch(name=f"Grandma's \U0001f9f6 {prop_id}"))
        assert renamed is not None and renamed.id == prop_id
        current = props_db.get_prop(db, DEMO_FAMILY_ID, prop_id)
        replaced = props_db.replace_prop(db, DEMO_FAMILY_ID, current.model_copy(update={
            "description": "the real one, from the attic", "color": "#123456"}))
        assert replaced is not None and replaced.id == prop_id

    memory, result = _recompile(db, memory_id)
    assert result.ok, [e.message for e in result.errors]
    assert [p.id for p in props_db.get_props(db, DEMO_FAMILY_ID, memory.prop_ids)] == memory.prop_ids
    # The stored scene is unchanged by edits to its props.
    assert _timeline_json(result.timeline) == _timeline_json(memory.timeline)


def test_a_scene_with_a_pull_tab_on_a_part_still_compiles_after_a_photo_swap(seeded):
    db = seeded
    photo = media_db.put_media(db, DEMO_FAMILY_ID, demo._placeholder_png("car", "#fff", "#ccc", "#5e8c4a"),
                               content_type="image/png")
    props_db.update_prop(db, DEMO_FAMILY_ID, "green_sedan", schemas.PropPatch(photo_id=photo))
    _, result = _recompile(db, "demo_green_sedan_trip")
    assert result.ok, [e.message for e in result.errors]


def test_media_of_another_family_is_invisible_in_every_form(seeded):
    db = seeded
    theirs = media_db.put_media(db, "someone_else", demo._placeholder_png("x", "#fff", "#000", "#f00"),
                                content_type="image/png")
    ours = demo.DEMO_PHOTO_IDS[0]
    for ref in (theirs, media_db.media_ref(theirs), f" {theirs} "):
        assert media_db.get_media(db, DEMO_FAMILY_ID, ref) is None
        assert not media_db.media_exists(db, DEMO_FAMILY_ID, ref)
        assert media_db.image_aspect(db, DEMO_FAMILY_ID, ref) is None
        assert media_db.delete_media(db, DEMO_FAMILY_ID, ref) is False
    for ref in (ours, media_db.media_ref(ours)):
        assert media_db.get_media(db, "someone_else", ref) is None
        assert media_db.delete_media(db, "someone_else", ref) is False
    assert media_db.delete_family_media(db, "someone_else") == 1
    assert media_db.get_media(db, DEMO_FAMILY_ID, ours) is not None
    assert media_db.image_aspect(db, DEMO_FAMILY_ID, ours) == pytest.approx(320 / 240, abs=1e-4)
