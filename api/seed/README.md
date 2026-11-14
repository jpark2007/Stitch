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

Startup logs two lines confirming it: the demo accounts and password, and the agent mode
(scripted runs, see [Agent runs](#agent-runs-stitch_agent)):

```text
INFO:     STITCH_FAKE_DB: in-memory database with demo family 'demo_family'; sign in as david@demo.stitch, ana@demo.stitch, sam@demo.stitch, password 'stitch-demo'
INFO:     STITCH_AGENT=fake: scripted agent runs, no model calls (step 0.6s)
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
A request the API rejects as malformed (422) lists each error's `type`, `loc` and `msg` but
never echoes what was sent, so a too-short signup password is not sent back.

### Add a memory without keys

With `STITCH_FAKE_DB=1` and no `ANTHROPIC_API_KEY`, "add a memory" runs end to end on
scripted runs (`seed/fake_runs.py`): no model calls, no network. In the web app, sign in as
`sam@demo.stitch` (password `stitch-demo`), open the add page, write the memory, pick a
year and attach photos, and submit. The live feed then shows, one step every
`STITCH_FAKE_RUN_STEP` seconds (0.6 by default):

1. a plan (Read the telling, Ask one follow-up, Find the props, Stage the scene, Narrate)
   and the interviewer sub-agent;
2. the follow-up question **"Who else was there that day?"**. The run waits for the
   answer, which is added to the telling as a new paragraph;
3. the prop master, and a prop review with one chip, the family's **hymn books**. The run
   waits for the review: keep it, rename it, replace its photo, or remove it (removed props
   are left out of the memory and the scene);
4. the director, then the memory is saved with the hand-written hymn-book scene (its
   screenplay and timeline, the `hymn_books` prop and the `church_steps_1988` set), recast
   with this run's people: the teller plays the sample's Ana, and the first participant
   who is not the teller plays David (else the teller's spouse, else someone else in the
   family);
5. `scene_ready`, then `done` with the new memory's id.

The memory, with its photos, the answer and the scene, then opens in the **teller's book
and in each participant's book** (`GET /families/demo_family/people/{id}/book`). The scene
is always the hymn-book one, whatever the telling says: it shows the flow, not the agent.
Adding to an existing memory (`memoryId` in the form) only adds a second telling and links
the people to it: no question, no prop review, no new scene.

The same flow with curl (the server above on port 8000; `steps.png` is any PNG, JPEG,
WebP, GIF or HEIC file):

```sh
J=/tmp/stitch-sam.txt; B=localhost:8000/families/demo_family
curl -s -c $J -H 'Content-Type: application/json' \
  -d '{"email":"sam@demo.stitch","password":"stitch-demo"}' localhost:8000/auth/login

curl -s -b $J -F 'text=We sang from the green hymn books on the church steps.' \
  -F year=1998 -F participantIds=lily -F photos=@steps.png $B/memories
# 202 {"runId":"9aef11b519204c80a678f1f8e23e512a","memoryId":null}
RUN=9aef11b519204c80a678f1f8e23e512a      # the runId from the line above

curl -s -N -b $J $B/runs/$RUN/stream      # Server-Sent Events; stays open while it waits
# event: plan / subagent_start / subagent_end, then
# event: question
# data: {"text":"Who else was there that day?"}

curl -s -b $J -H 'Content-Type: application/json' \
  -d '{"kind":"answer","text":"Lily held the umbrella."}' $B/runs/$RUN/answer     # 204
# the stream goes on to
# event: props_found
# data: {"props":[{"id":"hymn_books","name":"hymn books",...,"reused":true}]}

curl -s -b $J -H 'Content-Type: application/json' \
  -d '{"kind":"props","props":[{"id":"hymn_books","action":"keep"}]}' $B/runs/$RUN/answer   # 204
# ... event: scene_ready, then
# event: done
# data: {"memoryId":"6af7c0a09c3bec5caec51c25"}

curl -s -b $J $B/people/sam/book          # the new memory, with its timeline
curl -s -b $J $B/people/lily/book         # the same memory, in the participant's book
```

The stream can be opened at any time: it replays the run from its first event, then
follows it live until `done` or `error`. `STITCH_FAKE_RUN_STEP=0` makes a run finish as
soon as it is answered.

### What does not work in this mode

- The scene of a new memory is always the hand-written hymn-book scene (see above); no
  props, sets or art are generated.
- Vector search is unavailable, so "ask the family" and prop matching use their keyword
  fallbacks.
- Media is kept in an in-memory store instead of GridFS.

## Agent runs (STITCH_AGENT)

`STITCH_AGENT` picks who runs "add a memory" (`POST /families/{fid}/memories` and its
`stream` and `answer` routes). It is read when the app starts:

| Value | What runs |
|---|---|
| `off` | No agent at all, even when `ANTHROPIC_API_KEY` is set. `agent/` is never imported and no studio is built. Submitting a memory answers `503 {"detail":"Could not start the studio"}`; the `stream` and `answer` routes answer `503 {"detail":"The studio is unavailable"}` (after the usual 401/404 checks). Everything else works. |
| `fake` | Scripted runs from `seed/fake_runs.py` (see [above](#add-a-memory-without-keys)): no keys, no network. Works on the in-memory database and on a real MongoDB. |
| `real` | The model studio: startup calls `agent.runs.get_manager()`, which builds `agent.production.production_manager()` from the environment (below) and installs it. |

Unset or empty (or unknown, which also logs a warning), the default is `fake` when
`STITCH_FAKE_DB` is on or `ANTHROPIC_API_KEY` is unset or blank, and `real` otherwise. So
`STITCH_FAKE_DB=1` always gets scripted runs unless `STITCH_AGENT=real` says otherwise, and
a `.env` with an Anthropic key on a real MongoDB gets the model studio. The mode the app
resolved is kept on `app.state.agent_mode`.

`STITCH_FAKE_RUN_STEP` is the pause between the steps of a scripted run, in seconds (a
number >= 0, default `0.6`; anything else logs a warning and uses `0.6`).

### What the real studio reads

`agent/production.py` picks each service from the environment when the studio is built:

| Variable | Used for | When unset |
|---|---|---|
| `ANTHROPIC_API_KEY` | The studio model and Claude vision (photo descriptions, frame checks). Required. | The build fails and startup falls back to scripted runs (below). |
| `STITCH_MODEL`, `STITCH_SUBAGENT_MODEL` | Model ids for the studio and its sub-agents. | The default Claude model; sub-agents use the studio's model. |
| `MONGODB_URI` | Run checkpoints and the agent's `/memories/` notes, on MongoDB (built with a 5 s server-selection timeout: the one network call the build makes). | In memory, so paused runs are lost on restart. Also in memory whenever `STITCH_FAKE_DB` is on. |
| `IMAGE_API_KEY` | Generated prop and set art. | Placeholder art (no image, the prop's color). |
| `ELEVENLABS_API_KEY` | Narration voices. | Silent placeholder audio with real timings. |
| `STITCH_WEB_URL` | Frame checks screenshot the web app's `/render/[memoryId]` page with Playwright. | A Pillow diagram renderer. |

The family data itself always goes through the API's own database, including the
`STITCH_FAKE_DB` in-memory one. Building the studio makes no call to the model, image,
speech or vision services; their clients connect on first use.

If `agent.runs` already has a run manager when the app starts (a test or a script
configured one), real mode keeps it, does not build another, and leaves it to its owner on
shutdown.

### Startup log lines

Each mode logs one line (two for a fallback) after the `STITCH_FAKE_DB` line:

```text
# STITCH_AGENT=off
INFO:     STITCH_AGENT=off: no agent runs (agent/ is not loaded); adding a memory answers 503

# STITCH_AGENT=fake
INFO:     STITCH_AGENT=fake: scripted agent runs, no model calls (step 0.6s)

# STITCH_AGENT=real, studio built
INFO:     STITCH_AGENT=real: agent runs use the model studio

# STITCH_AGENT=real, build failed (here: no ANTHROPIC_API_KEY); the reason is in the warning
WARNING:  STITCH_AGENT=real: the model studio could not be built (agent runs are not configured: ANTHROPIC_API_KEY is not set; the studio needs a Claude model to run (set it in the API's environment and retry)); falling back to scripted runs (no model calls)
INFO:     STITCH_AGENT=fake: scripted agent runs, no model calls (step 0.6s)

# STITCH_AGENT=real with a manager configured before startup
INFO:     STITCH_AGENT=real: keeping the agent run manager configured before startup
```

In real mode the build may also print its own warnings without a level prefix, such as
`agent persistence is in memory (STITCH_FAKE_DB is set): paused runs and /memories/ notes
are lost on restart`. Its `studio services: model=real ... renderer=diagram` summary is
logged at INFO by the `agent.production` logger, which uvicorn's default logging does not
show.

To try each one locally without MongoDB:

```sh
cd api
STITCH_FAKE_DB=1 STITCH_AGENT=real ANTHROPIC_API_KEY= uv run uvicorn main:app --port 8000   # fallback warning
STITCH_FAKE_DB=1 STITCH_AGENT=off uv run uvicorn main:app --port 8000                       # submit answers 503
```

No agent step can stop the API from starting: if even the scripted runs cannot be
installed, startup logs a warning and the run routes answer 503. On shutdown the API stops
the run manager it installed (runs in flight end with an error event) and clears it.

Smoke-test a running API end to end: `uv run python -m seed.smoke --base-url http://localhost:8000` (see seed/smoke.py).

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

With `ANTHROPIC_API_KEY` in that `.env`, adding a memory uses the model studio:
`STITCH_AGENT` defaults to `real`, and startup builds the studio through
`agent.runs.get_manager()` (see [What the real studio reads](#what-the-real-studio-reads)).
With `MONGODB_URI` set and `STITCH_FAKE_DB` off, its run checkpoints and notes also live in
MongoDB (the API still keeps run ids in the process, so a restart ends the runs in flight).
If the build fails, startup logs the reason and falls back to scripted runs.
Without the key, or with `STITCH_AGENT=fake`, runs are scripted on the real database too;
with `STITCH_AGENT=off`, adding a memory answers 503 even when the key is set.
