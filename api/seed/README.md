# Demo seed

The stage demo starts with a full tree and books. `seed/demo.py` creates the demo family,
**The Maos** (family id `demo_family`):

- 8 people in three generations: Joseph (1934 to 2011) and Rosa (1938), Luis (1936 to 2015)
  and Carmen (1940); their children David (`dad`, 1962) and Ana (`mom`, 1964); and Sam
  (1990) and Lily (1993). Each has a hand-picked character sheet with a distinct color.
- The hand-written hymn-book scene from `web/public/samples/hymn_books.memory.json`, with
  its prop (`hymn_books`), set (`church_steps_1988`), screenplay and timeline, so David's
  and Ana's books open on a playable scene.
- Five more memories without scenes, from 1958 to 2015, with one or two tellings each
  and two placeholder photos on the 1972 road trip.
- Two open questions, one for Ana and one for Sam.

## Demo accounts

All three use the password **`stitch-demo`**.

| Email | Person | Role |
|---|---|---|
| `david@demo.stitch` | David (`dad`) | admin |
| `ana@demo.stitch` | Ana (`mom`) | member |
| `sam@demo.stitch` | Sam (`sam`) | member |

## Seed a real MongoDB

From `api/`, with `MONGODB_URI` and `MONGODB_DB` set (defaults: `mongodb://localhost:27017`
and `stitch`):

```sh
cd api
uv run python -m seed.demo           # creates the indexes, then seeds; a no-op if the family exists
uv run python -m seed.demo --reset   # deletes the demo data and rebuilds it
```

Seeding is idempotent. `--reset` deletes only demo data: the three demo users (by email),
the `demo_family` family, and its people, memories, questions, props, sets and media. Other
families are never touched. The family document is written last, so a run that was cut
short is cleaned up and rebuilt by the next one.

From code: `seed.demo.seed_demo(db, reset=False) -> DemoSeed`.

## Run the API without MongoDB

Neither MongoDB nor Docker is needed for local frontend work. `seed.fake.install_fake_db()`
points `db.client.get_db()` at an in-memory mongomock database, creates the indexes and seeds
the demo family. Once `api/main.py` calls it on startup when `STITCH_FAKE_DB=1`:

```sh
cd api
STITCH_FAKE_DB=1 uv run uvicorn main:app --reload
```

Then sign in as one of the demo accounts above. Everything is kept in memory and is lost on
restart (including each `--reload`). Vector search is unavailable, so "ask the family" and
prop matching use their keyword fallbacks. mongomock is a dev dependency, which `uv run`
installs by default.
