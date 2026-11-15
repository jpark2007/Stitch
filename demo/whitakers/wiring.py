"""How the launcher hooks the family and Stitch Channels into api/main.py without editing it.

- `seed_on_fake_db()`: main's STITCH_FAKE_DB startup calls `seed.fake.install_fake_db`
  (looked up by name at that moment). This points that name at a wrapper that installs the
  in-memory database with the Maos as before, then loads the family from
  STITCH_WHITAKERS_DIR, seeds it into that database, and adds it to replay's families
  (`demo.replay.manager.add_replay_family`), all before main installs the agent runs, so
  replay checks every prepared story against the family that has its cast.
- `wrap_channels(app)`: when the app was built with STITCH_CHANNELS=1 (main mounts the
  Channels routes then), right after startup and replay's own check:
  - a ChannelWorker goes on `app.state.channels_worker`, built as api/seed/channels_demo.py
    builds its own: the scripted conversation (`agent.conversation.demo.ScriptedConversation`)
    and `runs=` the installed ReplayRunManager instance. The worker never uses agent.runs by
    itself (with no `runs` it would preflight the model studio), so every story a contact
    texts or tells starts a run on the replay manager, and a matching one ends on its
    prepared scene;
  - each of family.json's `contacts` gets a pending iMessage enrollment through the same
    public function the web settings page uses (`db.contacts.create_pending_enrollment`;
    consent is never pre-set), and `enrollment_lines()` hands the launcher its LINK code to
    print. Handles are never printed or logged.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from demo.replay import manager as replay_manager

logger = logging.getLogger("uvicorn.error").getChild("whitakers")

CHANNELS_STATE = "channels_enabled"  # api/main.py's app.state flag
_ENROLLED: list[tuple[str, str]] = []  # (person id, LINK code) from the last startup


def seed_on_fake_db() -> None:
    """Make STITCH_FAKE_DB startup seed the family beside the Maos. Idempotent."""
    from seed import fake

    if getattr(fake.install_fake_db, "_whitakers", False):
        return
    original = fake.install_fake_db

    def install_fake_db(*, seed: bool = True) -> Any:
        db = original(seed=seed)
        if seed:
            from demo.whitakers.data import data_dir, load_family
            from demo.whitakers.seed import seed_family

            data = load_family(data_dir(os.environ))
            seed_family(db, data)
            replay_manager.add_replay_family(data.family_id)
            install_fake_db.family = data  # type: ignore[attr-defined]
            logger.info("whitakers: seeded family %r (%d people, %d moments)",
                        data.family_id, len(data.people), len(data.moments))
        return db

    install_fake_db._whitakers = True  # type: ignore[attr-defined]
    install_fake_db.family = None  # type: ignore[attr-defined]
    fake.install_fake_db = install_fake_db  # type: ignore[assignment]


def seeded_family() -> Any:
    """The FamilyData the last startup seeded, or None."""
    from seed import fake

    return getattr(fake.install_fake_db, "family", None)


def channels_worker(db: Any, runs: Any) -> Any:
    """The Channels worker on `runs` (the replay manager), with the scripted conversation."""
    from agent.conversation.demo import ScriptedConversation
    from channels.outbound import BridgeSender
    from channels.worker import ChannelWorker

    return ChannelWorker(db, sender=BridgeSender(), agent_factory=ScriptedConversation, runs=runs)


def enroll_contacts(db: Any, data: Any) -> list[tuple[str, str]]:
    """A pending enrollment for each contact in family.json, as the settings page makes one.

    Returns (person id, LINK code) for each; a contact that cannot be enrolled is logged
    by person id and skipped. The enrolling account is the person's own, else the admin.
    """
    from channels.settings import EnrollmentRequest
    from db import contacts as contacts_db

    out = []
    for contact in data.contacts:
        person = data.person(contact.person_id)
        actor = person.user_id if person is not None and person.user_id else data.admin.user_id
        try:
            body = EnrollmentRequest(provider="imessage", handle=contact.handle, timezone=contact.timezone,
                                     cadenceDays=contact.cadence_days, quietHours=contact.quiet_hours)
            _row, code = contacts_db.create_pending_enrollment(
                db, data.family_id, contact.person_id, actor, body.provider, body.handle,
                body.model_dump(exclude={"provider", "handle"}),
            )
        except Exception as exc:  # noqa: BLE001 - one bad contact never stops the demo
            logger.warning("whitakers: no enrollment for %s (%s)", contact.person_id, type(exc).__name__)
            continue
        out.append((contact.person_id, code))
    return out


def enrollment_lines() -> list[str]:
    """The banner lines for the last startup's enrollments (person ids and codes, never handles)."""
    if not _ENROLLED:
        return []
    width = max(len(name) for name, _ in _ENROLLED)
    lines = ["  Enroll   from each handle, text the agent line within 10 minutes of startup:"]
    lines += [f"           {name.ljust(width)}  LINK {code}" for name, code in _ENROLLED]
    lines.append("           (a fresh code any time: /settings/notifications in the web app)")
    return lines


def wrap_channels(app: Any) -> None:
    """After startup: the Channels worker (scripted mode) and the contacts' enrollments."""
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: Any) -> AsyncIterator[Any]:
        async with inner(application) as state:
            _ENROLLED.clear()
            if getattr(application.state, CHANNELS_STATE, False):
                from db import client

                db = client.get_db()
                manager = replay_manager._configured_manager()
                if not isinstance(manager, replay_manager.ReplayRunManager):
                    raise RuntimeError("whitakers: Channels needs the replay manager, and none is installed")
                application.state.channels_worker = channels_worker(db, manager)
                logger.info("whitakers: Channels worker on replay runs, scripted conversation")
                data = seeded_family()
                if data is not None and data.contacts:
                    _ENROLLED.extend(enroll_contacts(db, data))
                    for line in enrollment_lines():
                        print(line, flush=True)
            yield state

    app.router.lifespan_context = lifespan
