"""Run the API without MongoDB: an in-memory mongomock database, seeded with the demo family.

api/main.py calls `install_fake_db()` on startup when STITCH_FAKE_DB=1. Data lives in the
process and is lost on restart. Atlas Vector Search is unavailable, so search falls back to
keywords, and media is kept in the db/media.py inline store instead of GridFS.
"""

from __future__ import annotations

from pymongo.database import Database

from db import client


def install_fake_db(*, seed: bool = True) -> Database:
    """Point db.client.get_db() at a fresh in-memory database, with indexes and the demo family.

    Returns the database. Call `db.client.set_db(None)` to go back to the real one.
    """
    try:
        import mongomock
    except ImportError as exc:  # pragma: no cover - mongomock is a dev dependency
        raise RuntimeError(
            "STITCH_FAKE_DB needs mongomock, a dev dependency of the API. "
            "Run the server with `uv run` from api/ (uv installs dev dependencies by default), "
            "or `uv sync --group dev`."
        ) from exc

    db = mongomock.MongoClient(tz_aware=True)[client.MONGODB_DB]
    client.set_db(db)
    client.ensure_indexes(db)
    if seed:
        from seed.demo import seed_demo

        seed_demo(db)
    return db
