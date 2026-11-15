# Demo seed

## Quick start: the offline demo in one command

From a fresh clone (needs only [uv](https://docs.astral.sh/uv/); no MongoDB, Docker or API
keys):

```sh
cd api && uv sync && uv run python -m seed.serve
```

It starts the API at `http://localhost:8000` on an in-memory database seeded with the demo
family (`STITCH_FAKE_DB=1`), with scripted "add a memory" runs. It prints a banner, then
uvicorn's log, and is ready when you see `Application startup complete`:

```text
Stitch offline demo: in-memory database with the demo family (The Maos), scripted runs
  API      http://localhost:8000   (health: http://localhost:8000/health)
  Web app  http://localhost:3000   (cd web && npm install && npm run dev)
           open it at localhost, not 127.0.0.1: the session cookie is SameSite=Lax,
           and the browser treats the two as different sites
  Sign in  (password stitch-demo for every account)
           david@demo.stitch  David Mao (admin)
           ana@demo.stitch    Ana Mao (member)
           sam@demo.stitch    Sam Mao (member)
  No MongoDB, Docker or API keys needed.
  Ctrl+C to stop; data resets on every restart.

INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     No .env file found (looked for .../api/.env, .../.env)
INFO:     STITCH_FAKE_DB: in-memory database with demo family 'demo_family'; sign in as david@demo.stitch, ana@demo.stitch, sam@demo.stitch, password 'stitch-demo'
INFO:     STITCH_AGENT=fake: scripted agent runs, no model calls (step 0.6s)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

(With a `.env` file the third log line names the files it loaded instead.) Startup takes a
few seconds; the first `uv sync` downloads the dependencies once.

The demo accounts all use the password **`stitch-demo`**:

| Email | Person | Role |
|---|---|---|
| `david@demo.stitch` | David (`dad`) | admin |
| `ana@demo.stitch` | Ana (`mom`) | member |
| `sam@demo.stitch` | Sam (`sam`) | member |

**The web app.** In a second terminal, `cd web && npm install && npm run dev`, then open
**http://localhost:3000** (not `127.0.0.1:3000`, see the banner) and sign in with an
account above. The web app calls `http://localhost:8000` by default.

**Check it with curl** (a second terminal):

```sh
curl -s localhost:8000/health
# {"status":"ok"}
curl -s -H 'Content-Type: application/json' \
  -d '{"email":"sam@demo.stitch","password":"stitch-demo"}' localhost:8000/auth/login
# {"user":{"id":"demo_user_sam","email":"sam@demo.stitch","name":"Sam Mao",...},
#  "families":[{"id":"demo_family","name":"The Maos","role":"member","personId":"sam"}]}
```

More curl calls are in [Smoke check](#smoke-check), and `uv run python -m seed.smoke`
drives a whole "add a memory" run against it (see [The smoke checker](#the-smoke-checker-seedsmoke)).

**Stop it** with Ctrl+C in its terminal.

Options: `--port N` (default 8000), `--host H` (default `127.0.0.1`), `--reload` (restart
on code changes under `api/`). `--help` lists them.

### Troubleshooting

- **`port 8000 is in use: is the API already running? pass --port`**: another server
  (often an API you started earlier) has the port. Stop it, or use another port and point
  the web app at it: `uv run python -m seed.serve --port 8001`, then
  `NEXT_PUBLIC_API_URL=http://localhost:8001 npm run dev` in `web/`.
- **Everything resets on restart.** The database lives in the server process: every
  restart, including each `--reload`, seeds the demo family again from scratch, and
  accounts, families and memories you created are gone. Sessions survive a restart (the
  demo users are re-created with the same ids, and sessions are signed with a fixed,
  public, dev-only `JWT_SECRET` when you have not set one in the environment or a `.env`
  file), but an account you signed up for does not, so sign in again.
- **Signed in, but the web app acts signed out**: you opened `127.0.0.1:3000`; use
  `localhost:3000`.
- **Sign-in fails with a CORS error in the browser console**: `npm run dev` printed
  `Port 3000 is in use ..., using available port 3001 instead`. The API only allows
  `localhost:3000` and `127.0.0.1:3000`; stop the other web server, or allow the new
  origin: `STITCH_CORS_ORIGINS=http://localhost:3000,http://localhost:3001 uv run python -m seed.serve`.
- **It never needs MongoDB, Docker or keys.** It sets `STITCH_FAKE_DB=1` and leaves
  `STITCH_AGENT` alone, so runs are scripted even when a `.env` holds an
  `ANTHROPIC_API_KEY` (set `STITCH_AGENT=real` yourself to try the model studio on the
  in-memory database). `VOYAGE_API_KEY` is ignored in this mode.
- **What it does not exercise**: MongoDB itself (the database is mongomock), Atlas Vector
  Search (unavailable here, so "ask the family" and prop matching use their keyword
  fallbacks; Vector Search has not been verified against a real Atlas cluster), GridFS
  (media is kept in memory), and real model runs (no model, image, speech or vision calls;
  every new memory gets the hand-written hymn-book scene). See
  [What does not work in this mode](#what-does-not-work-in-this-mode) and, for the real
  thing, [Run the API on a real MongoDB](#run-the-api-on-a-real-mongodb).

`python -m seed.serve` is a thin wrapper: the same server is
`STITCH_FAKE_DB=1 uv run uvicorn main:app --port 8000` (see
[Run the API without MongoDB](#run-the-api-without-mongodb)).

## Presenter run sheet

A three-minute stage demo on `uv run python -m seed.serve` plus the web app. Each step says
how it was checked:

- **[HTTP]**: run against `seed.serve` on a fresh start, with the same requests the web app
  makes (sign-in, `GET` routes, the multipart submit and the run's event stream) or with
  `seed.smoke`. What it prints is quoted.
- **[read from web code]**: taken from the web app's code on `origin/main` (14 November),
  not clicked through in a browser.

**Before going on stage** (two terminals, from the repo root):

1. `cd api && uv sync && uv run python -m seed.serve`; wait for
   `Application startup complete`. **[HTTP]** `/health` answers `{"status":"ok"}`.
2. `cd web && npm install && npm run dev`, then open **http://localhost:3000** (not
   `127.0.0.1`). **[read from web code]**
3. Rehearse the "hear Mom's version" step once with `seed.smoke` (below), then stop
   `seed.serve` with Ctrl+C and start it again: answering Ana's question closes it, and a
   restart brings back the seeded state. **[HTTP]**

**On stage:**

| At | Do | What it shows | Checked |
|---|---|---|---|
| 0:00 | Sign in at `/login` as `david@demo.stitch`, password `stitch-demo`. | David Mao, admin of The Maos, playing `dad`. | **[HTTP]** `POST /auth/login` answers 200 with `"families":[{"id":"demo_family","name":"The Maos","role":"admin","personId":"dad"}]`. The page: **[read from web code]**. |
| 0:20 | Open **Family threads** (`/threads`, linked from the home page). | The Maos: 8 people in three generations and their 7 moments, 1958 to 2019, as threads along time. | **[HTTP]** `GET /families/demo_family/threads` answers 200 with `people` and `moments`; `GET .../tree` has 8 people. The page loads both for the signed-in family: **[read from web code]**. The home page (`/`) still draws the web app's built-in demo tree, not the seeded family: **[read from web code]**. |
| 0:40 | Open **David's book**, then **Sam's first bike** (1997). Pull **"Pull to push Sam off down the street"**, then switch the teller from David to Sam: "I noticed." | The red bike, Dad running behind; two tellings over the same action. | **[HTTP]** `GET /families/demo_family/people/dad/book` lists five moments (`demo_green_sedan_trip`, `sample_hymn_books`, `demo_sams_first_bike`, `demo_lily_graduation`, `demo_sam_wedding_2019`), each with its screenplay and timeline; the bike's timeline has the tabs "Pull to push Sam off down the street", "Pull to ring the bell" and "Pull to spin the back wheel", and narrations `dad` and `sam`. **Playing it in the web app is not wired yet** **[read from web code]**: on `origin/main`, `/people/[id]` is a placeholder and `/memories/[id]` opens only the built-in book at `/memories/demo`. Until the book loads real moments, show `/memories/demo` here. |
| 1:20 | **Hear Mom's version.** Sign out (**Settings**, `/families/demo_family/settings`, has **Sign out** at the bottom) and sign in as `ana@demo.stitch`. Open `/add`: under **"Someone asked you"** is "Sam says you hugged him so hard at the wedding that his flower fell off. How do you remember that day?". Press **"Answer this"**, write two sentences, submit. | The live feed: plan, interviewer, narrator, then done. A second telling has no follow-up question and no prop review. | **[HTTP]** `POST /auth/logout` answers 204 and a later `GET /me` with the same cookie jar answers 401; Ana then signs in (200). Ana's `GET /families/demo_family/questions` lists it (id `5eed00020000000000000003`, `memoryId` `demo_sam_wedding_2019`). `uv run python -m seed.smoke --base-url http://localhost:8000 --email ana@demo.stitch --memory-id demo_sam_wedding_2019` printed `PASS done: memory demo_sam_wedding_2019 in 5.6s, 10 events, every one valid`, `mom's telling is on it, narrated in 2 line(s); version 1 -> 2`, and afterwards the question was closed. The Sign out button, the buttons and the feed: **[read from web code]**. |
| 2:00 | Play **Sam's wedding** and switch to Mom's track. | The same action, in Ana's words. | **[HTTP]** `GET /families/demo_family/memories/demo_sam_wedding_2019` has `timeline.narrations` `sam`, `dad`, `mom`; Mom's lines have cues and word timings in timeline time (no audio: scripted runs make no speech). Playback in the web app: not wired yet, as above **[read from web code]**. |
| 2:20 | **Add a new memory.** Sign out the same way and sign in as `sam@demo.stitch`, open `/add`, write a few sentences that mention "Grandpa Joe", pick a year, attach a photo, submit. Answer the follow-up, say whether Grandpa Joe is Joseph, keep the hymn books. | The feed: plan; "Who else was there that day?"; "Is Grandpa Joe the Joseph in your family tree?"; the prop review with the family's hymn books; the director; done. The new memory, with the hand-written hymn-book scene recast with Sam, is in Sam's book. | **[HTTP]** `uv run python -m seed.smoke --base-url http://localhost:8000 --email sam@demo.stitch` printed `PASS question`, `PASS merge`, `PASS props_found: ... 1 kept`, `PASS done: ... 16 events, every one valid` and `PASS book: sam's book lists it`. The form and feed: **[read from web code]**. |

The same steps with Sam on Lily's graduation (`--email sam@demo.stitch --memory-id
demo_lily_graduation`) also passed **[HTTP]**: Sam's track joins Ana's and Lily's, and in
the hall Sam is the one in the second row who points at Lily after the bow.

## The demo family

The stage demo starts with a full tree and books. `seed/demo.py` creates the demo family,
**The Maos** (family id `demo_family`):

- 8 people in three generations: Joseph (1934 to 2011) and Rosa (1938), Luis (1936 to 2015)
  and Carmen (1940); their children David (`dad`, 1962) and Ana (`mom`, 1964); and Sam
  (1990) and Lily (1993). Each has a hand-picked character sheet: a distinct color and
  signature item, a build (small, short, average or tall) and a note on how they move
  (Joseph's cane slows him down, Lily walks briskly). Luis's straw hat and Sam's cap are
  props (`itemPropId`), so they wear them on stage.
- Seven moments from 1958 to 2019, every one with a **compiled scene** (screenplay and
  timeline), so every book opens on playable pages. They are listed in
  [The scenes](#the-scenes) below.
- The props and sets those scenes use, with placeholder art (`art` null, a `color`) unless
  a hand-made image is in `seed/demo_family/art/` (see [Hand-made art](#hand-made-art)).
- Open questions that ask a participant who has not told a moment yet for their version,
  and one general prompt. See [Demo accounts and their questions](#demo-accounts-and-their-questions).
- Five traditions for the Book of Family Traditions (`GET /families/demo_family/traditions`,
  oldest first): `tamales_christmas_eve`, `sunday_pancakes`, `summer_road_trip` (with the
  peach-stand photo), `hymns_after_dinner` and `rosas_easter_hats`. Same ids and text as the
  web mock (`web/components/flow/mock/demoTraditions.ts`); the mock's `rosa`, `luis` and
  `maya` are `grandma_rosa`, `grandpa_luis` and `lily` here (`seed.demo.DEMO_TRADITIONS`).

### The scenes

One row per moment (`seed.demo.MOMENTS`), oldest first. The first teller is the default
narrator; every other teller has their own narration track (`Timeline.narrations`), so a
book can play each person's version over the same action. Every shot uses the `fold`
transition (the page folds into the next shot) and has short `pageText`, and every puppet and prop on
stage has a click-to-explain note. "Tab" is a pull tab and what it drives
([CONTRACTS.md, Screenplay](../../shared/CONTRACTS.md#screenplay)).

| Memory id | Year | Title | Tellers | Sets, in shot order | What to show |
|---|---|---|---|---|---|
| `demo_rosa_joseph_wedding` | 1958 | Rosa and Joseph's wedding | Rosa (`grandma_rosa`), David (`dad`) | `church_steps_1988`, then `st_marys_churchyard_1958` | `rain` and a grey dawn (`time_of_day` dawn) on the same church steps as the 1988 hymn books; Joseph enters and hands Rosa his umbrella (`hand_to`). Fold to the churchyard: the sun comes out (`time_of_day` day), the umbrella closes, `confetti` over Rosa, Joseph kneels over his soggy socks, Rosa points and hugs him. Tabs: open the umbrella (prop state), the socks (scrub), bring them together (scrub). |
| `demo_sunday_tamales` | 1970 | Sunday tamales at Carmen's | Ana (`mom`) | `carmen_kitchen_1970`, then `carmen_kitchen_1970` | `steam` from the tamale pot in both shots; Carmen sets the pot on the table (`place_on` its `top` socket), Ana holds the lumpy tamale, Luis enters in his straw hat and sits on his chair (`sit_on` its `seat`). Fold to a second shot in the same kitchen: Ana hands Luis the lumpy one, sits on her stool, Luis cheers. Tabs: lift the pot lid (prop part `lid`), give Luis the lumpy one (scrub). Carmen has an open question. |
| `demo_green_sedan_trip` | 1972 | The road trip in the green sedan | David (`dad`, with two photos), Rosa (`grandma_rosa`) | `mao_driveway_1972`, then `coast_road_1972` | The green sedan: Joseph, Rosa and David get in at its `driver`, `passenger` and `back_seat` sockets, exhaust `smoke`, and Joseph drives off (`drive_to`). Fold to the coast road, everyone still seated: the car pulls up with more `smoke`, all three get out, Joseph kneels at the flat tire, David sits on the fruit crate and Rosa hands him the peaches. Tabs: spin the front wheel (prop part `wheel_front`), drive to the peach stand (scrub). |
| `sample_hymn_books` | 1988 | How I met your mother | David (`dad`), Ana (`mom`) | `church_steps_1988` | The reference sample (a copy of `web/public/samples/hymn_books.screenplay.json`), one shot: David enters, Ana drops the hymn books, David picks them up and hands them back, Ana bows her head. Tab: drop the books (scrub). |
| `demo_sams_first_bike` | 1997 | Sam's first bike | David (`dad`), Sam (`sam`) | `maple_street_1997`, then `maple_corner_1997` | The red bike: Sam gets on at its `driver` socket and rides (`drive_to`) while Dad runs behind; the camera uses every move (`wide`, `close_up`, `dolly_to`, `pan_to`, `pull_back`). Fold to the corner, Sam still on the bike: Dad lets go, the bell rings (`set_state`), Dad follows, Sam gets off and waves, Dad hugs him. Tabs: push Sam off (scrub), ring the bell (prop state), spin the back wheel (prop part `wheel_back`). |
| `demo_lily_graduation` | 2015 | Lily's graduation | Ana (`mom`), Lily (`lily`) | `school_hall_2015`, then `school_lawn_2015` | Six puppets, and the whole family in the audience: the grandmothers start seated in the front row and put on matching hats (`put_on`); Ana, Sam and David sit in the second row behind them (a row of marks at depth 0.38, each on their own folding chair). Lily trips on the stage steps into a bow; Sam points at her (he saw it first), Rosa stands up to cheer and sits back down, Ana leans toward the stage; Lily picks up her diploma at the lectern while David stands up for a photo and sits again. Fold to the lawn: Lily hands Mom the diploma, a hug, `confetti`, Sam points (the Thanksgiving joke), Dad waves. Tabs: make Lily bow, make Rosa cheer, hand Mom the diploma (all scrub). Sam, David and Rosa have open questions; Sam's asks what he saw "from your seat". |
| `demo_sam_wedding_2019` | 2019 | Sam's wedding in the orchard | Sam (`sam`), David (`dad`) | `wedding_garden_2019`, then `reception_tent_2019` | Ana and David start on the garden bench (its `left` and `right` sockets) and Ana leans toward David; Sam slumps, Lily walks up the aisle and hands him the ring box, which he opens (`set_state`). Fold to the reception tent at dusk (`time_of_day` dusk): Mom hugs Sam, his boutonniere drops, `confetti`, Dad waves, Lily picks the flower up and hands it back. Tabs: open the ring box (prop state), Mom's hug (scrub). Ana's open question here is "hear Mom's version" (below). |

**Vocabulary the demo exercises.** Across the seven scenes: the actions `walk_to`,
`run_to`, `enter`, `follow`, `turn_to`, `lean_toward`, `point_at`, `wave`, `hug`, `kneel`,
`pose` (`bow_head`, `arms_raised`, `reach`, `slumped`), `sit_on`, `stand_up`, `get_in`,
`get_out`, `drive_to`, `pick_up`, `hold`, `hand_to`, `drop`, `place_on`, `put_on` and
`set_state`; all five effects, `confetti`, `steam`, `rain`, `smoke` and `time_of_day`
(dawn, day, dusk); all five camera moves; the `fold` transition between every pair of
shots; starting a shot seated (`start.in` on a chair, bench, bike or car socket: seven
people start seated somewhere, five of them in the graduation hall); marks with a depth
(`[u, h, d]`: the graduation's second row of chairs stands at `d` 0.38, behind the front row
on the acting plane); marks with a height (the hall's stage at `h` 0.25, the church's top
step); the built-in `offstage_left` and `offstage_right` marks (entrances, and props parked
off stage until their shot); and pull tabs of all three drive types (`scrub`, `prop_state`,
`prop_part`). Not exercised: the actions `exit`, `open`, `close` and `ride_to`, the `none`
transition, and sliders. The vocabulary itself is `shared/vocabulary.json`.

#### The props, by moment

Every prop a seeded moment uses (`seed/demo_family/props.json`; placeholder art, a `color`):

| Moment | Props |
|---|---|
| Rosa and Joseph's wedding | `black_umbrella` (states `closed`, `open`) |
| Sunday tamales at Carmen's | `tamale_pot` (part `lid`), `lumpy_tamale`, `kitchen_table` (socket `top`), `kitchen_chair` and `kitchen_stool` (socket `seat`), `luis_straw_hat` (Luis's signature item) |
| The road trip in the green sedan | `green_sedan` (sockets `driver`, `passenger`, `back_seat`; parts `wheel_front`, `wheel_back`), `peaches`, `fruit_crate` (sockets `seat`, `top`), `road_map` |
| How I met your mother | `hymn_books` |
| Sam's first bike | `red_bike` (socket `driver`; parts `wheel_front`, `wheel_back`; states `quiet`, `ringing`), `sam_cap` (Sam's signature item) |
| Lily's graduation | `diploma`, `lectern` (socket `top`), `rosa_hat`, `carmen_hat`, and five folding chairs with a `seat`: `folding_chair_rosa` and `folding_chair_carmen` in the front row, `folding_chair_mom`, `folding_chair_sam` and `folding_chair_dad` in the second |
| Sam's wedding in the orchard | `ring_box` (states `closed`, `open`), `garden_bench` (sockets `left`, `right`), `boutonniere` |

### Demo accounts and their questions

Every account's password is **`stitch-demo`** (`seed.demo.DEMO_PASSWORD`). Questions are
listed by `GET /families/demo_family/questions` for the signed-in person, and in the web
app under "Someone asked you" on the add page (`/add`).

| Email | Person | Open questions |
|---|---|---|
| `david@demo.stitch` | David (`dad`), admin | Lily's graduation (a second telling) |
| `ana@demo.stitch` | Ana (`mom`), member | Sam's wedding (a second telling: **"hear Mom's version"**); the day Lily was born (a general prompt, starts a new memory) |
| `sam@demo.stitch` | Sam (`sam`), member | Lily's graduation (a second telling) |

Lily, Rosa and Carmen also have questions (Sam's wedding, Lily's graduation, Sunday
tamales), but no account, so nobody can answer them in the demo. A question that points
at a moment closes when that person's telling of it is saved.

#### Hear Mom's version (offline)

Ana's question, "Sam says you hugged him so hard at the wedding that his flower fell off.
How do you remember that day?", points at `demo_sam_wedding_2019`, which Sam and David
have told. Answering it adds Ana's telling and gives the scene a third narration track,
`narrations.mom`, over the same action. With `python -m seed.serve` running:

- **Web app**: sign in as `ana@demo.stitch`, open the add page (`/add`), press "Answer
  this" on that note (the year and people come from the moment), write a few sentences
  and send. The scripted run shows the interviewer and the narrator, then `done`; there is
  no follow-up question and no prop review for a second telling.
- **Smoke checker** (it checks that `timeline.narrations.mom` has lines and the version
  went up), from `api/` in a second terminal:

  ```sh
  uv run python -m seed.smoke --email ana@demo.stitch --memory-id demo_sam_wedding_2019 \
    --text "I hugged him so hard his flower fell off. Lily found it under the bench."
  # ... mom's telling is on it, narrated in 2 line(s); version 1 -> 2   (on a fresh server)
  # smoke OK: run ..., memory demo_sam_wedding_2019, 10 events (plan, subagent_start, ...)
  ```

- **curl**:

  ```sh
  J=/tmp/stitch-ana.txt; B=localhost:8000/families/demo_family
  curl -s -c $J -H 'Content-Type: application/json' \
    -d '{"email":"ana@demo.stitch","password":"stitch-demo"}' localhost:8000/auth/login
  curl -s -b $J $B/questions                 # the wedding question, memoryId demo_sam_wedding_2019
  RUN=$(curl -s -b $J -F 'text=I hugged him so hard his flower fell off. Lily found it under the bench.' \
    -F year=2019 -F memoryId=demo_sam_wedding_2019 $B/memories \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["runId"])')   # 202 {"runId":...}
  curl -s -N -b $J $B/runs/$RUN/stream       # plan, subagent_start/end, ..., scene_ready, done
  curl -s -b $J $B/memories/demo_sam_wedding_2019 | python3 -c \
    'import json,sys; t=json.load(sys.stdin)["memory"]["timeline"]; print(list(t["narrations"]))'
  # ['sam', 'dad', 'mom']
  ```

The new track's lines carry cues and word timings in timeline time, and no audio (scripted
runs make no speech). The same works for Sam or David on `demo_lily_graduation`. Each run
changes the in-memory database; restart `seed.serve` to get the seeded state back.

## Edit the demo scenes

Everything the scenes are made of is plain JSON in `seed/demo_family/`:

| File | What it is |
|---|---|
| `<memoryId>.screenplay.json` | One hand-authored `Screenplay` per moment (`api/screenplay/schema.py`; conventions in `shared/CONTRACTS.md`). `sample_hymn_books.screenplay.json` is a copy of `web/public/samples/hymn_books.screenplay.json`; adjust the copy here, never the sample. |
| `props.json` | The family's props (`schemas.Prop`): size, aspect, anchor, grip, sockets (seats, table tops, the car's `driver`, `passenger` and `back_seat`), parts (spinning wheels, a pot lid) and states. |
| `sets.json` | The family's sets (`schemas.SetDef`): paper layers and named marks `[u, h]`. |
| `art/` | Optional hand-made PNGs for those props and set layers (see [Hand-made art](#hand-made-art)). Empty: every prop and layer draws its placeholder. |

The moments' titles, years, participants and tellings, and the seeded questions, are in
`seed/demo.py` (`EXTRA_MEMORIES`, `DEMO_QUESTIONS`; the hymn-book memory comes from the
sample). The first telling's author tells the scene's default narration (`Shot.narration`),
and every other teller has their lines in `Screenplay.narrations`.

After an edit, recompile and check everything with

```sh
cd api
uv run pytest tests/data/test_seed_scenes.py tests/data/test_seed_staging.py tests/data/test_seed.py tests/data/test_seed_readme.py
```

`test_seed_readme.py` checks that this README still names every moment, set, prop, demo
account, compiled effect and seed exit code, so update [The scenes](#the-scenes) and
[The props, by moment](#the-props-by-moment) when you add or change one.
`test_seed_staging.py` samples every compiled shot and checks the blocking: everyone on
stage, nobody standing on anyone, nothing floating, people facing whoever they hand
things to, no frozen stretches, no popping, no prop on stage before its shot, no two props
drawn into each other (its module docstring lists the rules).

Two rules the tests enforce that are easy to trip over:

- **A prop that first appears in a later shot is parked off stage in the earlier ones.**
  The compiler keys a prop from the first shot that names it, and the stage holds a track's
  first key before it, so a fruit crate placed only in the road trip's second shot would
  stand in the 1972 driveway for the whole first shot. Give it
  `"propStart": {"fruit_crate": {"at": "offstage_right"}}` in every earlier shot (the road
  trip does this for `fruit_crate` and `peaches`). A prop a shot does not name is moved off
  stage by the compiler after its last shot, so only the earlier shots need this.
- **Nobody may vanish into the set.** Puppets and set layers are flat placeholder colours,
  so a character colour (`seed.demo.demo_people`) must differ from every layer colour of
  every set that person is on stage in, by a CIELAB distance (delta E) of at least 10.
  One known clash remains: `church_steps_1988`'s front band is `#7d9a5b`, Joseph's own
  green, so in the wedding's first shot his legs merge into the band. The seed cannot change
  it alone: the set is the web sample's (`web/public/samples/hymn_books.memory.json`), and
  `tests/data/test_seed.py` requires the seeded copy to equal the sample's exactly. The scene
  test lists this one clash as pinned by the web sample and accepts nothing else; when the
  sample's front band changes (a stone grey such as `#8f8a80` clears the threshold),
  `sets.json` must follow it and the pinned entry can go.

`seed_demo` compiles every screenplay with the real compiler
(`screenplay.compiler.compile`) against these props and sets and the demo people
**before** it deletes or writes anything, and stores the timeline the compiler produced.
A screenplay that does not compile stops the seed with a `DemoCompileError` naming the
memory id and listing every exact `CompileError`, for example:

```text
demo moment 'demo_green_sedan_trip' (demo_green_sedan_trip.screenplay.json) does not compile; 1 error(s):
  - [busy] shot s2, t=9.5: grandma_rosa cannot hand_to "peaches"; grandma_rosa is still walking until t=9.69
```

A malformed or inconsistent file stops it with a `DemoDataError` naming the file: invalid
JSON or an invalid model, duplicate ids, a screenplay whose `memoryId` is not its file
name, a screenplay file for no moment, someone on stage who is not a participant, a
participant who was not born yet (or had died) that year, or tellers that do not match the
tellings. `test_seed_scenes.py` also recompiles each stored scene from the database and
checks it equals the stored timeline, runs the agent's scene review (it must have no
warnings), and checks the vocabulary coverage above.

### Effects

The effects (`confetti`, `steam`, `rain`, `smoke`, `time_of_day`) are compiled like every
other action, through the compiler's effect path, and land in each stored
`Timeline.effects`: rain on the 1958 wedding, steam over the tamales, exhaust smoke from
the sedan, confetti at the 1958 wedding, the graduation and Sam's wedding, a grey dawn
that turns to day once the 1958 rain has passed, and dusk falling on the 2019 party.
Nothing authored is ever left out of the stored screenplay. An effect that cannot be
placed (say `at` names someone not on stage) fails the seed like any other `CompileError`.
`test_seed_scenes.py` checks that every authored effect is in the stored timeline.

### Hand-made art

Drop PNGs into `seed/demo_family/art/` and the next seed (`seed.serve` start, or
`python -m seed.demo --reset`) draws them instead of the placeholders: `<propId>.png` for a
prop's body, `<propId>.<artKey>.png` for its other art keys (a part such as
`green_sedan.wheel.png`, or the art a state switches to such as `black_umbrella.open.png`),
and `<setId>.back.png` / `.middle.png` / `.front.png` for set layers. The full rules are in
[`demo_family/art/README.md`](demo_family/art/README.md). Each file is cut out like studio
art and stored as a `cutout` under a fixed media id (`seed.demo.demo_art_media_id`); a body
image also sets the prop's aspect and anchor, and the scenes are compiled with them. A file
that cannot be used (unknown name, not an image, too big, nothing opaque, or a body whose
shape would stop a scene compiling) is a warning naming it, and that prop or layer keeps its
placeholder; it never stops the seed. Images saved with transparency cost about 30 ms each
at start; a white background is removed on every start (about 0.5 s each), so run
`uv run python -m seed.demo --cut-out-art` once to save them with transparency.
`tests/data/test_seed_demo_art.py` covers it.

## Preflight a real MongoDB

Before the API or the seed touches a real database (the Atlas cluster on demo day), check it
end to end. Put `MONGODB_URI` (and `MONGODB_DB` if not `stitch`) in `api/.env` or the
repo-root `.env`, then from `api/`:

```sh
cd api
uv run python -m seed.preflight              # the table below; about 10 to 70 seconds on Atlas
uv run python -m seed.preflight --wait 180   # give freshly created vector indexes longer to build
uv run python -m seed.preflight --json       # the same rows as JSON
```

Options: `--timeout-ms N` (server selection and connect timeout, default 5000), `--wait
SECONDS` (how long to poll vector indexes that are still building, and a vector search that
finds nothing yet; default 60, `0` does not wait), `--skip-checkpointer` (skip the LangGraph
check). Like the API, it reads the environment, then `api/.env`, then `../.env`, and a
variable already set wins (`STITCH_NO_DOTENV=1` turns the files off). That includes
`EMBEDDING_DIM`: the vector indexes it creates have the same `numDimensions` as the API's
queries, even when `EMBEDDING_DIM` is set only in a `.env` file (`seed.demo` does the same).

It never prints, logs or puts in the JSON the URI's user, password or query string: only the
scheme and hosts (`mongodb+srv://<credentials redacted>@cluster0.xxxxx.mongodb.net`) and the
database name, and every error text is scrubbed of them. Everything it writes goes into a
throwaway family `preflight_<12 hex>` and account `preflight-<hex>@preflight.invalid`, which
it deletes again at the end, **even when a check failed**, and then checks that no document
with those ids is left anywhere. It never calls Voyage or any model: the only network it
uses is MongoDB.

What a healthy Atlas cluster should look like (illustrative: the preflight has only run
against mongomock here, see [What is not verified](#what-is-not-verified)):

```text
Stitch MongoDB preflight
  target    mongodb+srv://<credentials redacted>@cluster0.xxxxx.mongodb.net
  database  stitch

CHECK                         STATUS  SECONDS  DETAIL
connect                       PASS       0.41  ping answered within 5000 ms
server                        PASS       0.09  MongoDB 8.0.4; topology replica set atlas-xxxxx-shard-0; is_atlas yes (.mongodb.net host: yes; atlasVersion: 20250115.0.0)
indexes                       PASS       1.73  0 present, 11 created (users_email_unique, families_members_userId, ...)
vector index memories_vector  PASS      31.02  created now; ready (status READY, queryable); the app wants numDimensions 1024 (EMBEDDING_DIM)
vector index props_vector     PASS      31.02  created now; ready (status READY, queryable); the app wants numDimensions 1024 (EMBEDDING_DIM)
vector index sets_vector      PASS      31.02  created now; ready (status READY, queryable); the app wants numDimensions 1024 (EMBEDDING_DIM)
round trip                    PASS       1.12  wrote and read back users, families, people x2, memories, questions, props, sets, traditions, revoked_sessions, media (GridFS: media.files + 1 chunk(s) in media.chunks)
tree                          PASS       0.08  get_tree ok (2 people, 2 generations); native $graphLookup reached both
search                        PASS       2.20  $vectorSearch ran: 1 hit(s), the throwaway memory among them; keyword fallback found the memory for 'lighthouse'
checkpointer                  PASS       0.61  checkpoint put/get_tuple/put_writes/delete_thread in agent_checkpoints, agent_checkpoint_writes; store put/get/delete in agent_store
cleanup                       PASS       0.35  deleted family preflight_3f9a1c0b7e42 and its account, session and checkpoints; 0 documents left

PREFLIGHT PASS
```

On a second run the indexes say `11 present, 0 created` and the vector indexes `already
existed; ready`. What each row checks:

| Row | What it does | PASS / WARN / FAIL |
|---|---|---|
| `connect` | Pings within `--timeout-ms`. | FAIL (exit 3) with one sentence: unreachable or not on the Atlas IP access list, authentication failed, a malformed URI (an unescaped character in the password is named, never quoted), or a DNS SRV lookup failure. Nothing else runs. |
| `server` | Version (`buildInfo`), topology (`hello`: standalone, replica set or sharded), and `is_atlas`: true when a host ends in `.mongodb.net` or the server answers `atlasVersion` (both signals are shown). | WARN when the version cannot be read (mongomock). |
| `indexes` | `db.client.ensure_indexes`, then every index of `db.client.INDEXES` as present (already there) or created. | FAIL with the server's message on a spec conflict (an index with the same name and another definition). |
| `vector index <name>` | One row per Atlas Vector Search index (`memories_vector`, `props_vector`, `sets_vector`): `db.client.ensure_search_indexes_report`, then `db.client.search_index_status` polled every 5 s for up to `--wait` seconds. Says whether it already existed, was created now, or had a stale definition that was updated. | PASS ready. WARN pending (with "re-run with --wait 180"), stale, or unsupported on a server that is not Atlas. FAIL when a search index of another type has the name, or when Atlas refuses to create it (the server's own message is shown). |
| `round trip` | One document per collection through the `db/` functions, read back and compared: `users`, `families`, two `people` (parent and child), `memories` (with a telling), `questions`, `props`, `sets`, `traditions`, `revoked_sessions`, and one media file (real GridFS on a real server: the file is checked in `media.files` and `media.chunks`). | FAIL names the step that failed. `tree` and `search` are then skipped; cleanup still runs. |
| `tree` | `db.people.get_tree`, then the native `$graphLookup` pipeline once directly. | WARN "via the Python fallback" when the server refuses `$graphLookup` (the API falls back silently). |
| `search` | Puts a synthetic unit vector (`EMBEDDING_DIM` long) on the throwaway memory, runs one `$vectorSearch` (polled for up to `--wait` seconds while it finds nothing: Atlas syncs new documents within seconds), then the keyword search, which must find the memory. | PASS when the vector search finds it. WARN when it ran but found nothing yet (index pending or not synced), or is unavailable off Atlas. FAIL when it is unavailable on Atlas, or the keyword fallback misses. |
| `checkpointer` | The model studio's LangGraph persistence (`agent.studio.mongo_persistence`): one checkpoint put, read back, a pending write, `delete_thread`; one store item put, read, deleted; the documents are checked in `agent_checkpoints`, `agent_checkpoint_writes` and `agent_store`. | SKIP with `--skip-checkpointer`. |
| `cleanup` | Always runs after a successful connect: purges the throwaway family (and its media), deletes its account, revoked session, checkpoints and store item, then looks in every collection for anything left with those ids. | FAIL lists the leftovers by collection and id. |

The last line is `PREFLIGHT PASS`, `PREFLIGHT PASS with N warnings` or `PREFLIGHT FAIL:
<checks>`. Exit codes (constants in `seed.preflight`):

| Exit code | Constant | Meaning |
|---|---|---|
| 0 | `seed.preflight.EXIT_OK` | Every check passed; warnings allowed. |
| 1 | `seed.preflight.EXIT_FAILED` | A check failed (the last line names it). |
| 2 | (argparse) | Bad command-line arguments. |
| 3 | `seed.preflight.EXIT_NO_DATABASE` | `MONGODB_URI` is not set ("MONGODB_URI is not set (looked in the environment, api/.env, ../.env)"), or the `connect` row failed. |

Two Atlas things to know:

- **The IP access list.** A `connect` FAIL that says "Could not reach MongoDB before the
  timeout" on Atlas is almost always this machine's IP missing from the cluster's access
  list: Atlas > Security > Network Access > Add IP Address (venue Wi-Fi changes it).
- **Three search indexes, at most.** The M0 (free) and Flex tiers allow at most 3 search
  indexes per cluster, and Stitch needs exactly 3 (`memories_vector`, `props_vector`,
  `sets_vector`). Any other search index on the cluster, in any database, makes one of
  ours fail to create; the row shows Atlas's own message. Drop the extra index in Atlas >
  Atlas Search, then re-run the preflight.

Known mongomock-only risks and how to verify each on a real database: [../db/REAL_MONGODB.md](../db/REAL_MONGODB.md)

## Seed a real MongoDB

One command from `api/`, safe to re-run:

```sh
cd api
uv run python -m seed.demo           # indexes, vector indexes (best effort), then seeds; a no-op if the family exists
uv run python -m seed.demo --reset   # deletes the demo data and rebuilds it
```

It reads `MONGODB_URI` and `MONGODB_DB` from the environment, then `api/.env`, then
`../.env`, like the API (a variable already set wins; `STITCH_NO_DOTENV=1` turns the files
off), so `uv run --env-file .env ...` is no longer needed (still accepted; its values are
set first, so they win). It prints the target as scheme and hosts only, never the
credentials or query string:

```text
Seeding mongodb+srv://<credentials redacted>@cluster0.xxxxx.mongodb.net, database 'stitch' ...
Vector search indexes: created memories_vector, props_vector, sets_vector (python -m seed.preflight shows their status).
Demo family 'demo_family' (The Maos) seeded in database 'stitch'.
```

With `MONGODB_URI` unset it says so before connecting, so nothing is seeded somewhere
unexpected: `MONGODB_URI is not set (looked in the environment, api/.env, ../.env): seeding
db.client's default, mongodb://localhost:27017.` It creates the indexes, then tries the
Atlas Vector Search indexes and reports them in one line (created, already present, or "not
supported on this server (not Atlas)"; that never stops the seed). On a new cluster, run
`uv run python -m seed.preflight` first.

Seeding is idempotent. `--reset` deletes only demo data: the three demo users (by email),
the `demo_family` family, and its people, memories, questions, props, sets, traditions and media. Other
families are never touched. The family document is written last, with every member in the
same insert, so a run that was cut short is cleaned up and rebuilt by the next one; a
database where the family exists but a demo account is missing is rebuilt too
(`seed.demo.demo_is_complete`).

The CLI's exit codes (constants in `seed.demo`):

| Exit code | Constant | Meaning |
|---|---|---|
| 0 | `EXIT_OK` | Seeded, or already present (nothing written). |
| 1 | `EXIT_BAD_DATA` | A file in `seed/demo_family/` is malformed or does not compile (`DemoDataError` or `DemoCompileError`); nothing was deleted or written. |
| 2 | (argparse) | Bad command-line arguments, for example `--bogus`. |
| 3 | `EXIT_NO_DATABASE` | MongoDB is unreachable, refused the login, or refused the writes. |

Errors are one readable sentence on stderr, never a traceback, and never the URI's
credentials or query string (the same sentences as the preflight's `connect` row). For
example, with nothing listening on the URI (it gives up after 5 seconds):

```text
$ MONGODB_URI='mongodb://localhost:1/' uv run python -m seed.demo; echo $?
Seeding mongodb://localhost:1, database 'stitch' ...
Demo seed failed: Could not reach MongoDB before the timeout: the server is down or unreachable, or on Atlas, add this machine's IP address to the cluster's IP access list (Atlas > Security > Network Access) (ServerSelectionTimeoutError: localhost:1: [Errno 61] Connection refused ...). Check MONGODB_URI and MONGODB_DB (or run `uv run python -m seed.preflight` for a full check), or run the API with STITCH_FAKE_DB=1 to use the in-memory demo database instead.
3
```

**A reseed is deterministic.** Every seed and every `--reset` writes the same ids:
the people, memories, props, sets and traditions use the ids in the files and in `seed/demo.py`, the
two road-trip photos are `seed.demo.DEMO_PHOTO_IDS` (`5eed00010000000000000001` and
`5eed00010000000000000002`), and the seven questions are `seed.demo.DEMO_QUESTION_IDS`
(`5eed00020000000000000001` to `5eed00020000000000000007`, in `DEMO_QUESTIONS` order, with
creation times a second apart; `GET /families/demo_family/questions` lists a person's open
ones newest first, so Ana sees the general prompt before the wedding question). Two seeded databases hold the same documents apart from
timestamps and salted password hashes (`tests/data/test_seed_robustness.py`), so every
teammate's `STITCH_FAKE_DB` demo matches the rehearsed one and a media URL such as
`/families/demo_family/media/5eed00010000000000000001` works on every machine.

From code: `seed.demo.seed_demo(db, reset=False) -> DemoSeed`.

## Run the API without MongoDB

`python -m seed.serve` ([Quick start](#quick-start-the-offline-demo-in-one-command)) is the
easy way. Underneath, it is `STITCH_FAKE_DB=1`: on startup `api/main.py` calls
`seed.fake.install_fake_db()`, and the API runs on an in-memory mongomock database with the
indexes created and the demo family seeded. The same server with plain uvicorn:

```sh
cd api && uv sync && STITCH_FAKE_DB=1 uv run uvicorn main:app --reload --port 8000
```

It logs the same `STITCH_FAKE_DB` and `STITCH_AGENT` lines as the quick start, after a line
naming the `.env` files it loaded (see
[Run the API on a real MongoDB](#run-the-api-on-a-real-mongodb)). `STITCH_FAKE_DB` accepts
`1`, `true` or `yes`. mongomock is a dev dependency, which `uv sync` and `uv run` install
by default.

### The frontend

The web app calls the API at `NEXT_PUBLIC_API_URL`, which defaults to
`http://localhost:8000`. Open it at `http://localhost:3000`, not `127.0.0.1` (the session
cookie is `SameSite=Lax`). CORS allows `http://localhost:3000` and `http://127.0.0.1:3000`,
with credentials. To allow other origins (another port, a tunnel), list them all in
`STITCH_CORS_ORIGINS`, comma-separated; `*` is ignored because credentials are on.

### Sessions and restarts

Any restart, including each `--reload`, wipes the in-memory database and seeds the demo
family again. Sessions are signed with `JWT_SECRET`; with plain uvicorn and no
`JWT_SECRET`, the API picks a random secret per process (and logs a warning), so every
restart also signs everyone out. `seed.serve` sets a fixed dev-only secret instead when you
have none; with uvicorn, set one yourself:

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
# David's book: his five moments, each with its screenplay and timeline
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
   answer, which is added to the telling as a new paragraph; then, only when the telling
   calls someone by a family title and a name that is nearly someone in the tree ("Grandpa
   Joe" for Joseph), the pause **"Is Grandpa Joe the Joseph in your family tree?"**, which
   waits for yes or no (yes adds Joseph to the memory; details in `seed/fake_runs.py`);
3. the prop master, and a prop review with one chip, the family's **hymn books**. The run
   waits for the review: keep it, rename it, replace its photo, or remove it (removed props
   are left out of the memory and the scene);
4. the director, then the memory is saved with the hand-written hymn-book scene (its
   screenplay and timeline, the `hymn_books` prop and the `church_steps_1988` set), recast
   with this run's people: the teller plays the sample's Ana, and the first participant
   who is not the teller plays David (else the teller's spouse, else someone else in the
   family). The sample's narration is replaced by the teller's own words: one line per
   sentence of the telling as saved (with the follow-up answer), over the same action. The
   teller is the default narrator and the only narration track; whoever plays David is in
   the scene and the memory's participants but narrates nothing until they tell it too.
   The sample's page text and explanations ("One Sunday in 1988...", "Dad was 26...") are
   rewritten for this telling: each shot's storybook text is the first sentence of its part
   of the telling (at most 16 words, then "..."; the title when a shot has no sentence), and
   there is one explanation per person and prop in the scene. For the curl run below:
   "Sam is telling this story, from 1998.", "Lily was there with Sam." (a spouse,
   parent or child in the tree is named as one instead: "Ana's spouse David was
   there.") and "The hymn books from Sam's story.", with the prop's name after any rename
   in the review. Pull tabs keep scrubbing the same action when the teller's narration
   pins it;
5. `scene_ready`, then `done` with the new memory's id.

The memory, with its photos, the answer and the scene, then opens in the **teller's book
and in each participant's book** (`GET /families/demo_family/people/{id}/book`). The scene
is always the hymn-book one, whatever the telling says: it shows the flow, not the agent.
Adding to an existing memory (`memoryId` in the form) only adds a second telling and links
the people to it: no question, no prop review, no new scene. The new teller's words become
their own narration track over the same action (the first teller stays the default
narrator); a retelling by the default narrator replaces the default narration, with the
action and its pull-tab windows where they were.

Two tellings of the same moment at once (two people answering together, or one person
sending twice) are safe: each run re-reads the moment under a per-moment lock just before
it narrates and saves, so the later one builds on the earlier one's saved scene. Every
teller keeps their narration track, the version goes up once per telling, and a person's
stored telling always matches their track. The scene is saved only if nobody else saved it
since that read (`save_scene(expected_version=...)`); otherwise the run re-reads and tries
again, up to three times, then ends with an error and leaves the moment as it was.

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
  props, sets or art are generated. Only its words are this telling's: the narration, the
  page text and the explanations; the action, the set, the prop and the pull tab's label
  ("Pull to drop the books") stay the sample's.
- The per-moment lock lives in one API process. Scripted runs never span processes, and a
  write from anywhere else is caught by the version check instead.
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

### The smoke checker (seed.smoke)

`seed/smoke.py` drives "add a memory" over HTTP against a running API, exactly like the web
app (cookie session, multipart submit, the run's event stream, answers), and checks each
step: login, membership, teller, submit, stream (every event validates against
`schemas.RUN_EVENTS`), the saved memory and the teller's book. Exit code 0 means every
check passed, 1 that one failed (the summary line names it), 2 that the API is not
reachable. The full list of checks and options is in the module docstring.

With `uv run python -m seed.serve` running, from `api/` in a second terminal:

```sh
# A new memory, as David (the default account): question, prop review, the hymn-book scene.
uv run python -m seed.smoke --base-url http://localhost:8000

# A second telling: --memory-id adds the account's telling to an existing moment and also
# checks that timeline.narrations[<teller>] has lines and the memory's version went up.
uv run python -m seed.smoke --email ana@demo.stitch --memory-id demo_sam_wedding_2019
uv run python -m seed.smoke --email sam@demo.stitch --memory-id demo_lily_graduation
```

With `--memory-id`, the moment's own year is sent and a check confirms the moment exists
first. Telling the same moment again replaces that account's earlier telling and track.
For the real model studio, start the API on a real MongoDB with the keys (below) and run
the same commands; a real run takes minutes (`--timeout`, default 900 seconds).

## Run the API on a real MongoDB

Put `MONGODB_URI` (and `JWT_SECRET`, model keys) in `api/.env` (`cp .env.example api/.env`
from the repo root), check the database, seed once, then start the server. All three read
the `.env` files themselves:

```sh
cd api
uv run python -m seed.preflight   # PREFLIGHT PASS (warnings allowed) before going further
uv run python -m seed.demo        # safe to re-run: a no-op once the demo family is there
uv run uvicorn main:app --reload
```

`main.py` loads `api/.env`, then the repo-root `../.env`, when it is imported, before
anything reads the environment. Neither file overrides a variable that is already set: the
shell environment wins, then `api/.env`, then `../.env`. `--env-file` is still accepted
(`uv run uvicorn main:app --env-file ../.env`), and since uvicorn applies it first, its
values win over both files. Startup logs which files it loaded (paths only, never values):

```text
INFO:     Environment loaded from /path/to/Stitch/api/.env (variables already set win)
```

`STITCH_NO_DOTENV=1` turns the loading off, and it never happens under pytest, so tests do
not read your keys.

On startup the API creates its indexes, then tries the Atlas Vector Search indexes. Neither
can stop it from starting: if MongoDB is unreachable or is not Atlas, it logs a warning and
serves anyway (`/health` still answers), and data routes fail until the database is back.

### Vector search (Voyage AI)

"Ask the family" and prop and set matching use Atlas Vector Search when the API has an
embedder, and keyword search otherwise. The embedder is Voyage AI, configured from:

| Variable | Used for | When unset |
|---|---|---|
| `VOYAGE_API_KEY` | Embedding "ask" queries and the name and description of each prop and set saved (`"<name>. <description>"`, stored as `embedding`). | No embedder: keyword search only, no vectors stored. |
| `VOYAGE_MODEL` | The Voyage model. | `voyage-3.5` |
| `EMBEDDING_DIM` | The vector length requested from Voyage and the `numDimensions` of the vector indexes (`memories_vector`, `props_vector`, `sets_vector`). voyage-3.5 accepts 256, 512, 1024 or 2048. | `1024` |

Startup logs one line saying whether embeddings are on (never the key):

```text
INFO:     Vector search embeddings on: Voyage AI voyage-3.5, 1024 dimensions
INFO:     Vector search embeddings off (VOYAGE_API_KEY not set): ask and prop matching use keywords
```

A failed Voyage call never fails a request or a save: that query uses keywords, and that
prop or set is saved without a vector. With `STITCH_FAKE_DB` on there is never an embedder
(mongomock has no `$vectorSearch`, and the fake mode makes no network calls), even with
`VOYAGE_API_KEY` set. Changing `EMBEDDING_DIM` after the indexes exist needs the vector
indexes dropped and recreated. Props and sets seeded by `seed.demo` have no vectors (the
CLI installs no embedder); keyword matching still finds them, and a later save of one adds
its vector.

With `ANTHROPIC_API_KEY` in that `.env`, adding a memory uses the model studio:
`STITCH_AGENT` defaults to `real`, and startup builds the studio through
`agent.runs.get_manager()` (see [What the real studio reads](#what-the-real-studio-reads)).
With `MONGODB_URI` set and `STITCH_FAKE_DB` off, its run checkpoints and notes also live in
MongoDB (the API still keeps run ids in the process, so a restart ends the runs in flight).
If the build fails, startup logs the reason and falls back to scripted runs.
Without the key, or with `STITCH_AGENT=fake`, runs are scripted on the real database too;
with `STITCH_AGENT=off`, adding a memory answers 503 even when the key is set.

## What is not verified

MongoDB, Docker and Atlas were not available where this demo was built and checked, so:

- The offline demo (`python -m seed.serve`, `STITCH_FAKE_DB=1`) and the whole test suite
  run on mongomock, an in-memory imitation of MongoDB, not on MongoDB itself.
- Atlas Vector Search has never run: in demo mode there is no embedder, and "ask the
  family" and prop and set matching use their keyword fallbacks. The vector indexes and
  `$vectorSearch` queries are untested against a real Atlas cluster.
- The real-MongoDB tests (`tests/data/test_real_mongo.py`, and the real run in
  `tests/data/test_seed_preflight.py`) skip unless `MONGODB_URI_TEST` points at a
  disposable database.
- The preflight (`python -m seed.preflight`) itself has only run against mongomock, through
  its test seams, and against nothing listening (the unreachable, DNS and bad-URI
  sentences). Its sample PASS table above is illustrative, not captured. Every Atlas branch
  (search index statuses, the M0 limit message, `$vectorSearch`, `atlasVersion`, GridFS,
  the LangGraph checkpointer and store on MongoDB) is exercised only by fakes shaped from
  Atlas's documented answers until it runs against the real cluster. The same goes for
  `python -m seed.demo` on a real database.
- Nothing here was run in Docker.
- No browser click-through: the second-telling path and the
  [Presenter run sheet](#presenter-run-sheet) were checked over HTTP (`seed.smoke` and
  plain requests against `seed.serve`); every web step is read from the web app's code and
  marked as such. On `origin/main` the web book does not play the seeded scenes yet.
- The seeded scenes were judged from their compiled timelines (the staging and scene
  tests), not watched on the stage: the second row's depth, the new colours and the
  audience's reactions have not been looked at in a rendered frame.
