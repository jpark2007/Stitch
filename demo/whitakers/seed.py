"""Write a loaded family (demo/whitakers/data.py) into a database, through the db/ functions only.

`seed_family(db, data)` writes the accounts, people, props, sets, photos and art, moments
(each telling, then the compiled scene) and open questions, and the family document last,
with every member in one insert, so a family document marks a complete seed (as
seed.demo does). It is a no-op when the family is already complete. Every id (people,
users, memories, media, questions) comes from the data, so every start writes the same ones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pymongo.database import Database

import auth
import schemas
from db import families as families_db
from db import media as media_db
from db import memories as memories_db
from db import people as people_db
from db import props as props_db
from db import users as users_db

from demo.whitakers.data import FamilyData
from demo.whitakers.narration import attach_narration


def family_is_complete(db: Database, data: FamilyData) -> bool:
    """The family document exists and every account can sign in to it."""
    if families_db.get_family(db, data.family_id) is None:
        return False
    found = {u.id: u.email for u in users_db.get_users(db, [a.user_id for a in data.accounts])}
    return all(found.get(a.user_id) == a.email for a in data.accounts)


def _purge(db: Database, data: FamilyData) -> None:
    families_db.purge_family(db, data.family_id)
    users_db.delete_users(db, emails=[a.email for a in data.accounts], user_ids=[a.user_id for a in data.accounts])


def seed_family(db: Database, data: FamilyData) -> bool:
    """Seed the family. True when it was written, False when it was already complete."""
    if family_is_complete(db, data):
        return False
    _purge(db, data)
    fid = data.family_id
    admin = data.admin
    base = datetime.now(UTC)

    for account in data.accounts:
        users_db.create_user(db, email=account.email, password_hash=auth.hash_password(account.password),
                             name=account.name, user_id=account.user_id, now=base)

    created: set[str] = set()
    for person in data.people:
        people_db.create_person(
            db, fid,
            schemas.NewPerson(
                name=person.name, born=person.born, died=person.died, parent_ids=person.parent_ids,
                # The later spouse links back to the earlier one (create_person adds both ways).
                spouse_ids=[s for s in person.spouse_ids if s in created], character=person.character,
            ),
            added_by=admin.user_id, user_id=person.user_id, person_id=person.id,
        )
        created.add(person.id)

    for prop in data.props:
        props_db.insert_prop(db, fid, prop, prop_id=prop.id)
    for set_def in data.sets:
        props_db.insert_set(db, fid, set_def, set_id=set_def.id)
    for media in data.media:
        media_db.put_media(db, fid, media.path.read_bytes(), content_type=media.content_type,
                           filename=media.path.name, kind=media.kind, media_id=media.media_id)

    for offset, moment in enumerate(data.moments):
        tellings = [
            schemas.Telling(author_id=t.author_id, question=t.question, text=t.text,
                            photo_ids=[data.photo_ids[name] for name in t.photos if name in data.photo_ids])
            for t in moment.tellings
        ]
        memories_db.create_memory(
            db, fid, title=moment.title, year=moment.year, participant_ids=moment.participant_ids,
            telling=tellings[0], prop_ids=moment.prop_ids, set_ids=moment.set_ids, memory_id=moment.id,
            now=base + timedelta(seconds=offset),
        )
        for telling in tellings[1:]:
            memories_db.add_telling(db, fid, moment.id, telling)
        # Startup and --check are offline, even when credentials happen to be set.
        timeline = attach_narration(db, fid, moment.screenplay, moment.timeline, allow_generate=False)
        memories_db.save_scene(db, fid, moment.id, screenplay=moment.screenplay, timeline=timeline,
                               prop_ids=moment.prop_ids, set_ids=moment.set_ids)

    for offset, q in enumerate(data.questions):
        memories_db.create_question(db, fid, person_id=q.person_id, text=q.text, memory_id=q.memory_id,
                                    question_id=q.question_id, now=base + timedelta(seconds=offset))

    others = [a for a in data.accounts if a.user_id != admin.user_id]
    families_db.create_family(
        db, name=data.name, creator_user_id=admin.user_id, family_id=fid, now=base,
        members=[schemas.Member(user_id=a.user_id, role=a.role, joined_at=base + timedelta(seconds=i))
                 for i, a in enumerate(others, start=1)],
    )
    return True
