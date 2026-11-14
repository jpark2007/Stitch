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

Neither MongoDB nor Docker is needed for local frontend work. With `STITCH_FAKE_DB=1`,
`api/main.py` calls `seed.fake.install_fake_db()` on startup: the API runs on an in-memory
mongomock database with the indexes created and the demo family seeded.

```sh
cd api && uv sync && STITCH_FAKE_DB=1 uv run uvicorn main:app --reload --port 8000
```

Startup logs one line confirming it, with the demo accounts and password:

```text
INFO:     STITCH_FAKE_DB: in-memory database with demo family 'demo_family'; sign in as david@demo.stitch, ana@demo.stitch, sam@demo.stitch, password 'stitch-demo'
```

`STITCH_FAKE_DB` accepts `1`, `true` or `yes`. mongomock is a dev dependency, which
`uv sync` and `uv run` install by default.

### The frontend

The web app calls the API at `NEXT_PUBLIC_API_URL`, which defaults to
`http://localhost:8000`, so `npm run dev` in `web/` works against this server with no
setup. Open the web app at `http://localhost:3000`, not `127.0.0.1`: the session cookie is
`SameSite=Lax`, and `localhost` and `127.0.0.1` are different sites to the browser.

CORS allows `http://localhost:3000` and `http://127.0.0.1:3000`, with credentials. To allow
other origins (another port, a tunnel), list them all, comma-separated; `*` is ignored
because credentials are on:

```sh
STITCH_CORS_ORIGINS=http://localhost:3000,http://localhost:3001 STITCH_FAKE_DB=1 uv run uvicorn main:app --reload --port 8000
```

### Sessions and restarts

Everything lives in the server process. Any restart, including each `--reload` after a code
change, wipes the in-memory database and seeds the demo family again from scratch: accounts,
families and memories you created are gone.

Sessions are signed with `JWT_SECRET`. Without it the API picks a random secret per process
(and logs a warning), so every restart also signs everyone out. Set it to keep a browser
session valid across restarts (the demo users are re-created with the same ids):

```sh
JWT_SECRET=any-local-dev-secret-at-least-32-chars STITCH_FAKE_DB=1 uv run uvicorn main:app --reload --port 8000
```

### Smoke check

With the server running, in a second terminal:

```sh
curl -s localhost:8000/health
# {"status":"ok"}

curl -s -c /tmp/stitch-cookies.txt -H 'Content-Type: application/json' \
  -d '{"email":"david@demo.stitch","password":"stitch-demo"}' localhost:8000/auth/login
# {"user":{"id":"demo_user_david","email":"david@demo.stitch","name":"David Mao",...},
#  "families":[{"id":"demo_family","name":"The Maos","role":"admin","personId":"dad"}]}

curl -s -b /tmp/stitch-cookies.txt localhost:8000/me
# the same {"user":...,"families":[...]} body

curl -s -b /tmp/stitch-cookies.txt localhost:8000/families/demo_family/tree
# {"familyId":"demo_family","familyName":"The Maos","people":[...8 people...]}

curl -s -b /tmp/stitch-cookies.txt localhost:8000/families/demo_family/people/dad/book
# David's book, including sample_hymn_books with its screenplay and timeline
```

Without the cookie, family routes return 401; with another family's id they return 404.

### What does not work in this mode

- **Adding a memory needs the agent.** `POST /families/{fid}/memories` returns
  `503 {"detail":"Could not start the studio"}` until agent runs (`agent/runs.py`) are
  wired; everything else, including reading the seeded books and scenes, works.
- Vector search is unavailable, so "ask the family" and prop matching use their keyword
  fallbacks.
- Media is kept in an in-memory store instead of GridFS.

## Run the API on a real MongoDB

Put `MONGODB_URI` (and `JWT_SECRET`, model keys) in the repo-root `.env`, seed once, then
start the server with that file. Nothing reads `.env` implicitly:

```sh
cd api
uv run --env-file ../.env python -m seed.demo
uv run uvicorn main:app --env-file ../.env
```

On startup the API creates its indexes, then tries the Atlas Vector Search indexes. Neither
can stop it from starting: if MongoDB is unreachable or is not Atlas, it logs a warning and
serves anyway (`/health` still answers), and data routes fail until the database is back.
