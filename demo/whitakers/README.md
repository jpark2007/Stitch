# A second demo family

`python -m demo.whitakers.serve` is the offline demo (`api/seed/serve.py`: in-memory
database, scripted agent runs, the Maos exactly as they are) **plus a second family**
loaded from a private data folder, in **replay mode** (`demo/replay/`): a typed telling on
`/add` that matches a prepared story ends on that story's scene, in either family.

Nothing in `api/` or `web/` changes. The family's data never lives in this repo: it stays
in the data folder (default `~/stitch-private/demo-family/data`). The tests use a made-up
family in `tests/fixture/data/`, which is also the smallest working example of the format.

## Run it

From the repo root (the Python environment is the api project's; `cd api && uv sync` once):

```sh
# check the data: load and compile every moment and every prepared story, then exit
uv run --project api python -m demo.whitakers.serve --check

# serve on port 8030, the web app on 3030
STITCH_CORS_ORIGINS=http://localhost:3030 \
uv run --project api python -m demo.whitakers.serve --port 8030
cd web && NEXT_PUBLIC_API_URL=http://localhost:8030 npx next dev -p 3030
```

| Flag | |
|---|---|
| `--data-dir DIR` | The family's data folder. Default `$STITCH_WHITAKERS_DIR`, else `~/stitch-private/demo-family/data`. |
| `--replay-dir DIR` | Prepared stories. Default `$STITCH_REPLAY_DIR`, else `<data-dir>/replays/` when it exists, else `~/stitch-private/replays`. |
| `--port N`, `--host H`, `--reload` | As `seed.serve` (default 127.0.0.1:8000). The data reloads on every restart. |
| `--check` | Load and compile everything, print the summary, exit. |
| `--channels` | Also mount Stitch Channels (below). |

Every variable `seed.serve` reads works the same (`STITCH_CORS_ORIGINS`,
`STITCH_FAKE_RUN_STEP`, `STITCH_NO_DOTENV`, ...), except `STITCH_AGENT`, which is always
`fake`. Sign in with the Maos' accounts (`david@`, `ana@`, `sam@demo.stitch`, password
`stitch-demo`) or with the accounts in `family.json`.

Exit codes: `0` done; `1` the port is taken; `2` the data or a prepared story is wrong. On
`2`, stderr lists **every** problem, by file and id: each moment that does not compile with
each exact compiler error, each missing photo, person or prop, each story that does not
compile against its family. If `family.json` or a moment is wrong, the stories' files are
still read (and their errors named), but they are compiled only once the family loads.

The banner and the log name counts, ids and folder names only: never names, tellings,
emails, handles or passwords.

### With Stitch Channels

```sh
read -s CHANNELS_SHARED_SECRET; export CHANNELS_SHARED_SECRET
uv run --project api python -m demo.whitakers.serve --port 8030 --channels
```

`--channels` mounts Channels the way `api/seed/channels_demo.py` runs it: it needs
`CHANNELS_SHARED_SECRET` (the same rule: at least 32 UTF-8 bytes, no edge whitespace; exit
`2` otherwise), and sets `STITCH_CHANNELS=1`, `STITCH_CHANNELS_WORKER=1` and
`STITCH_CHANNELS_TRANSCRIPTION=0` (unless already set). `api/main.py` mounts the routes and
indexes itself. After startup the launcher puts a `ChannelWorker` on
`app.state.channels_worker`, built as `channels_demo.py` builds its own: the scripted
conversation (`agent.conversation.demo.ScriptedConversation`) and `runs=` the installed
replay manager. So a story texted in from the Photon bridge, or reviewed after an
ElevenLabs call, runs on the same replay manager as `/add` and ends on its prepared scene
when it matches. The bridge points `CHANNELS_API_URL` at this port; replies go to
`CHANNELS_BRIDGE_URL` (default `http://127.0.0.1:3011`). Avoid ports 8011, 3023 and 3003
(Channels rehearsals).

- **Calls** need no contact: every account's person has `userId` set to its user, which is
  what the own-person pairing code binds a call's transcript to.
- **Texts** are optional: each `contacts` entry in `family.json` gets a *pending* iMessage
  enrollment through `db.contacts.create_pending_enrollment`, exactly as the web settings
  page makes one (as the person's own user when it has an account, else as the admin).
  Consent is never pre-set. The banner prints each person id with its `LINK <code>`; text
  that from the handle within 10 minutes of startup to opt in (a fresh code any time from
  `/settings/notifications`).

## The data folder

```text
data/
  family.json                    the family, its accounts, people, contacts, open questions
  moments/<memoryId>/moment.json one folder per memory; the folder name is its id
  moments/<memoryId>/screenplay.json
  props/*.json                   JSON lists of Prop, merged (any file names)
  sets/*.json                    JSON lists of SetDef, merged
  photos/                        photos the tellings attach, by file name
  art/                           prop and set art, by file name (optional)
  replays/<story>/story.json     prepared stories for replay mode (demo/replay format)
```

Ids are global across families: no person, memory or user id may be one of the Maos'
(`dad`, `mom`, `sam`, `lily`, `grandpa_joseph`, `grandma_rosa`, `grandpa_luis`,
`grandma_carmen`; `demo_*` memories). Ids are letters, digits, `_` and `-`. Every key is
camelCase, and an unknown key is an error (so a typo is caught).

### family.json

Mirrors the `_P` table and `DEMO_ACCOUNTS` in `api/seed/demo.py`.

```json
{
  "family": {"id": "whitakers", "name": "The Whitakers"},
  "accounts": [
    {"email": "evelyn@example.com", "password": "...", "name": "Evelyn Whitaker",
     "personId": "evelyn", "role": "admin"},
    {"email": "tom@example.com", "password": "...", "name": "Tom Whitaker",
     "personId": "tom"}
  ],
  "people": [
    {"id": "harold", "name": "Harold", "born": 1931, "died": 2009,
     "color": "#7d9a5b", "item": "pocket watch", "build": "tall",
     "notes": "hands in his pockets, rocks on his heels"},
    {"id": "evelyn", "name": "Evelyn", "born": 1934, "spouses": ["harold"],
     "color": "#c77d9b", "item": "garden gloves", "build": "short"},
    {"id": "tom", "name": "Tom", "born": 1962, "parents": ["harold", "evelyn"],
     "item": "fishing hat", "itemPropId": "tom_fishing_hat"}
  ],
  "contacts": [
    {"personId": "evelyn", "imessage": "+15555550123", "timezone": "America/New_York",
     "cadenceDays": 7, "quietHours": {"start": "21:00", "end": "08:00"}}
  ],
  "questions": [
    {"personId": "tom", "text": "What do you remember about the lake house?", "memoryId": "lake_1970"}
  ]
}
```

| Field | |
|---|---|
| `family.id`, `family.name` | The family's id (not `demo_family`) and its name on the tree. |
| `accounts[]` | Who can sign in. `email`, `password`, `name`, `personId` (the person this account is), `role` (`"admin"` or `"member"`, default member; at least one admin, and the first admin creates the family). Optional `id`: the user id, default `<familyId>_user_<personId>`. The account's person gets `userId` = that user. |
| `people[]` | The tree, in any order (stored parents first). `id`, `name`; optional `born`, `died` (years), `parents` (at most two person ids), `spouses` (person ids; links go both ways), and the puppet: `color` (hex), `item` (signature item, words), `build` (`small`, `short`, `average`, `tall`; default average), `notes` (how they move), `itemPropId` (a prop worn on stage in any scene that lists it). With no `color`, the first unused color and item are picked, as the app does. |
| `contacts[]` | Optional, used with `--channels`. `personId`, `imessage` (an E.164 phone number or an email address); optional `timezone` (IANA, default America/New_York), `cadenceDays` (1 to 30, default 7), `quietHours` (`HH:MM` `start` and `end`, default 21:00 to 08:00). One per person. |
| `questions[]` | Optional open questions on the person's page: `personId`, `text`, optional `memoryId` (a moment). |

### moments/&lt;memoryId&gt;/moment.json

Mirrors the `_M` table in `api/seed/demo.py`. Either the moment's own `screenplay.json`:

```json
{
  "title": "The lake house",
  "year": 1970,
  "place": "Lake Winnipesaukee",
  "people": ["harold", "evelyn", "tom"],
  "photos": ["lake_dock.jpg"],
  "tellings": [
    {"teller": "evelyn", "question": "What were summers like?", "text": "...", "order": 1},
    {"teller": "tom", "text": "...", "order": 2, "photos": ["tom_canoe.jpg"]}
  ]
}
```

or a Maos screenplay recast with this family (no `screenplay.json` then):

```json
{
  "title": "Sunday pancakes",
  "year": 1975,
  "tellings": [{"teller": "evelyn", "text": "..."}],
  "reuse": {
    "screenplay": "demo_sunday_tamales",
    "cast": {"mom": "evelyn", "grandma_carmen": "harold_mother", "grandpa_luis": "harold"},
    "props": {"tamale_pot": "pancake_pan"},
    "sets": {}
  }
}
```

| Field | |
|---|---|
| `title`, `year` | Required. |
| `place` | Optional; a note for the author (memories have no place field). |
| `tellings[]` | `teller` (person id), `text`; optional `question` (what was asked), `photos` (file names in `photos/`), `order` (sorts the tellings; otherwise file order). The **first** telling's teller tells the shots' own `narration`; every other teller needs a track in the screenplay's `narrations`, and every narrator needs a telling. With `reuse` and no `tellings`, the Maos' moment's tellings are used, by the mapped people. |
| `photos` | Optional photos for the first telling (before its own). |
| `people` | Optional participants. The cast and the tellers are always added. Everyone must be alive in `year`. With `reuse` and no `people`, the Maos' moment's participants, mapped. |
| `screenplay` | The screenplay file name in the folder, default `screenplay.json`. |
| `reuse` | Instead of a screenplay file: `screenplay` is a Maos memory id (`demo_sunday_tamales`, `demo_sams_first_bike`, `demo_green_sedan_trip`, `demo_rosa_joseph_wedding`, `demo_lily_graduation`, `demo_sam_wedding_2019`, `sample_hymn_books`); `cast` maps **every** Maos cast member and narrator to a person here (the error names any left out); `props` and `sets` optionally rename prop and set ids to this family's own. Use it to start; to change the staging, copy the file from `api/seed/demo_family/` into the folder and edit it. |

`screenplay.json` is a `Screenplay`, the same schema as
`api/seed/demo_family/*.screenplay.json`: its `memoryId` is the folder name and its cast
uses this family's person ids. Each moment is compiled with the real compiler at load;
the stored scene is exactly what the book plays.

A screenplay may use, without defining them: this family's `props/` and `sets/`; the shared
library props (`shared/props/`); and the Maos' own props and sets
(`api/seed/demo_family/props.json`, `sets.json`), which are then copied into this family
with the same ids.

### props/*.json, sets/*.json

JSON lists of `Prop` and `SetDef`, the same schema as `api/seed/demo_family/props.json`
and `sets.json`. Every file in each folder is read and merged; an id defined twice is an
error. Their `familyId` is set to this family.

### Art (optional)

Props and sets start on the placeholder art (`"art": null`), so Higgsfield art can be
dropped in later with no code change:

- `null` picks up a file named for the id when there is one, `.png`, `.webp`, `.jpg` or
  `.jpeg`: `art/<propId>.png` for a prop's `body`, `art/<propId>.<key>.png` for its other
  art keys, `art/<setId>.layer<n>.png` for a set's layer `n` (from 0, in the order
  listed).
- a file name (`"body": "pan_cutout.png"`) uses `art/<file>`; when it is not there yet,
  the placeholder, with a warning.
- `"media/<id>"` is kept as it is.

Each art file becomes a cutout in the family's media; a prop with no `aspect` gets its
art's width over height.

### photos/

PNG, JPEG, WebP or GIF up to 20 MB (convert HEIC to JPEG first). Each file is stored once
in the family's media, under an id derived from the family id and file name, the same on
every start. A photo no telling uses is a warning.

### replays/

Prepared stories in the `demo/replay` format (see `demo/replay/README.md`: `story.json`
with `match.text`, plus a screenplay). Their cast uses this family's person ids; a story
replays in whichever family has its whole cast, so this folder may hold stories for the
Maos too. A story may point its `screenplayFile` at a moment's screenplay
(`"../../moments/lake_1970/screenplay.json"`) to replay a scene already in the book as a
new memory. At startup each story is compiled against its `familyId`, else the first
family with its whole cast.

## Files

| | |
|---|---|
| `serve.py` | The launcher: environment, `--check`, banner, uvicorn. |
| `app.py` | `demo.whitakers.app:app`: `api/main.py`'s app, seeding the family on fake-database startup, in replay mode, with the Channels worker. |
| `wiring.py` | The hooks into `api/main.py`'s startup (no edits there): seeding, the Channels worker, enrollments. |
| `data.py` | Loading and checking the data folder; compiling the moments. No database. |
| `seed.py` | Writing the loaded family through `api/db/` functions only. |

## Tests

```sh
uv run --project api pytest demo -q
```

They cover loading the fixture (people order, linked accounts, art and placeholder art,
photos, recast screenplays), every error being named at once, seeding beside the Maos
(idempotent), signing in and reading the tree, book, memory, media and threads through the
real routes, replay in both families (and neither family replaying the other's story), the
Channels worker on the replay manager with pending enrollments, and the launcher's
environment, `--check` and exit codes.
