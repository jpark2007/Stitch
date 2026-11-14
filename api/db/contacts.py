"""Trusted contact lookup. Enrollment and verification are not implemented here."""

from datetime import datetime, timezone


def ensure_contact_indexes(db) -> None:
    db.contacts.create_index([("familyId", 1), ("handle", 1)], unique=True,
                             name="channel_contact_family_handle")
    db.contacts.create_index([("channel", 1), ("handle", 1)],
                             name="channel_contact_lookup")


def matching_contacts(db, provider: str, handle: str) -> list[dict]:
    """Identity discovery is deliberately cross-family; no family data is returned to callers.

    This is the one trusted-handle lookup needed before a family can be selected.
    It cannot authorize an action: resolve_contact also checks the person's family.
    """
    return list(db.contacts.find({"channel": provider, "handle": handle}))


def resolve_contact(db, provider: str, handle: str) -> dict:
    rows = matching_contacts(db, provider, handle)
    if not rows:
        raise LookupError("Unknown contact")
    if len(rows) != 1:
        raise ValueError("Family selection required")
    contact = rows[0]
    family_id, person_id = contact.get("familyId"), contact.get("personId")
    if not family_id or not person_id or not db.people.find_one(
        {"familyId": family_id, "_id": person_id}, {"_id": 1}
    ):
        raise LookupError("Unknown contact")
    if contact.get("status") != "active" or not contact.get("verifiedAt") or not contact.get("optInAt"):
        raise PermissionError("Contact is not opted in")
    return contact


def stop_contacts(db, provider: str, handle: str, rows: list[dict]) -> None:
    """STOP applies to every mapped family; it never needs family disambiguation."""
    for row in rows:
        db.contacts.update_one(
            {"_id": row["_id"], "familyId": row["familyId"], "channel": provider, "handle": handle},
            {"$set": {"status": "stopped", "stoppedAt": datetime.now(timezone.utc)}},
        )


def worker_contact(db, family_id: str, contact_id: str) -> dict:
    """Check both the selected family and current handle ambiguity before sending."""
    original = db.contacts.find_one({"familyId": family_id, "_id": contact_id})
    if original is None:
        raise LookupError("Unknown contact")
    resolved = resolve_contact(db, original["channel"], original["handle"])
    if resolved["familyId"] != family_id or str(resolved["_id"]) != contact_id:
        raise PermissionError("Contact mapping changed")
    return resolved


def seed_terminal_contact(db, family_id: str) -> None:
    """Disposable Channels demo fixture, called only with its isolated database."""
    import schemas
    from .people import create_person
    now = datetime.now(timezone.utc)
    db.families.insert_one({"_id": family_id, "name": "Terminal demo"})
    create_person(db, family_id, schemas.NewPerson(name="Demo teller", born=1980),
                  added_by=None, person_id="terminal-person")
    db.contacts.insert_one({"_id": "terminal-contact", "familyId": family_id,
        "personId": "terminal-person", "handle": "terminal-user", "channel": "terminal",
        "status": "active", "verifiedAt": now, "optInAt": now})
