# Replay mode

The offline demo (`api/seed/serve.py`) answers every "add a memory" with the scripted run:
a realistic agent feed and crafting book, ending on the canned hymn-book scene. Replay mode
keeps all of that, but when the telling matches a **prepared story**, the run ends on that
story's own polished scene instead.

The run looks the same either way: the same plan, interviewer, follow-up question, merge
question when one applies, props review, director, narration and linker steps, at the same
pace (`STITCH_FAKE_RUN_STEP`, default 0.6 s per step). Only the scene differs. Anything that
matches no prepared story behaves exactly like `STITCH_AGENT=fake` today, and so does every
second telling.

Nothing in `api/` or `web/` changes. `demo/replay/manager.py` subclasses
`seed.fake_runs.ScriptedRunManager`, and `demo/replay/app.py` makes `api/main.py` install it
in place of the scripted manager.

## Run it

From the repo root (the Python environment is the api project's; `cd api && uv sync` once):

```sh
# the example story, on port 8010
STITCH_REPLAY_DIR=demo/replay/example \
STITCH_CORS_ORIGINS=http://localhost:3010 \
uv run --project api python -m demo.replay.serve --port 8010

# the web app against it, from web/
NEXT_PUBLIC_API_URL=http://localhost:8010 npx next dev -p 3010
```

With no `STITCH_REPLAY_DIR` (or `--replay-dir`) the stories come from
`~/stitch-private/replays`. On port 8000 with the web app on 3000, no other variable is
needed: `uv run --project api python -m demo.replay.serve`.

The launcher is `python -m seed.serve` plus replay: the in-memory demo family (The Maos;
password `stitch-demo` for `david@`, `ana@` and `sam@demo.stitch`), the dev JWT secret when
none is set, the port check and banner, `--host`, `--port` and `--reload`. Every variable
`seed.serve` reads works the same (`STITCH_CORS_ORIGINS`, `STITCH_FAKE_RUN_STEP`,
`STITCH_NO_DOTENV`, ...), except `STITCH_AGENT`, which is always `fake`. The banner lists the
prepared stories by folder name, and the log says which one each run matched (never the text).

Before it serves, the launcher loads every story and compiles it against the demo family. A
story that does not compile stops it with exit code 2, naming the story and every compiler
error. `--check` does only that and exits:

```sh
uv run --project api python -m demo.replay.serve --check --replay-dir ~/stitch-private/replays
```

Without the launcher (same app, any uvicorn flags), from the repo root:

```sh
STITCH_FAKE_DB=1 PYTHONPATH=.:api uv run --project api uvicorn demo.replay.app:app --port 8010
```

## Author a prepared story

Keep real stories out of this repo: put them in `~/stitch-private/replays/` (or any folder
that `STITCH_REPLAY_DIR` names). Each story is one folder:

```text
~/stitch-private/replays/
  crafting_book/
    story.json
    screenplay.json
    props.json        optional
    sets.json         optional
```

`story.json`:

```json
{
  "match": {
    "text": "The telling, word for word, as you will type it on /add.",
    "keywords": ["crafting", "glue"],
    "minOverlap": 0.8
  },
  "title": "The crafting book",
  "year": 2004,
  "question": "Who else was at the table?",
  "reviewProps": ["crafting_book", "glue_pot"],
  "screenplayFile": "screenplay.json",
  "propsFile": "props.json",
  "setsFile": "sets.json"
}
```

| Field | |
|---|---|
| `match.text` | The telling you will type. Required. |
| `match.keywords` | Optional words or phrases. When the text is not close enough, a telling with every keyword still matches. |
| `match.minOverlap` | How close a telling must be, 0 to 1 (default 0.8). |
| `title`, `year` | Optional. The saved memory's title and year; otherwise what was typed. |
| `question` | Optional follow-up question; otherwise "Who else was there that day?". |
| `familyId` | Optional. Only tellings in this family match. |
| `reviewProps` | Optional. The props shown in the props review; otherwise every prop of the screenplay except the cast's signature items. |
| `screenplayFile` | The scene. Required. |
| `propsFile`, `setsFile` | Optional props and sets the scene uses that the family does not have yet. |

File names are relative to the story folder. `STITCH_REPLAY_DIR` may also point at one story
folder directly.

The files use the formats in `api/seed/demo_family/`:

- `screenplay.json` is a `Screenplay` (like `demo_sunday_tamales.screenplay.json`). Its cast
  uses the person ids of the family it is told in: for the demo family, `grandpa_joseph`,
  `grandma_rosa`, `grandpa_luis`, `grandma_carmen`, `dad`, `mom`, `sam` and `lily`. It needs
  narration in the shots' `narration` lines. Write it in the teller's voice: whoever tells the
  story live becomes its narrator (each line's source becomes `telling:<teller>:s<n>`), and
  `narrations` for other people are dropped, because nobody else has told it yet. The
  authored `pageText` and `explanations` are kept as written.
- `props.json` and `sets.json` are JSON lists of `Prop` and `SetDef` (like the demo family's
  `props.json` and `sets.json`). A prop or set the family already has is reused as it is.
  One it lacks is created in the family from these files (keeping its id when free,
  otherwise `<id>_<familyId>`). Ids the family has, or library props in `shared/props/`, need
  no entry.

At run time, the props review applies to the prepared props: rename and replace the photo
work as in the scripted runs, and remove leaves a prop out of the scene. When the scene
cannot compile without that prop (someone holds it, say), the prop stays and the feed says
"Kept the X: the scene needs it." The telling is saved as in the scripted runs (the typed
text, the follow-up answer as a new paragraph, and the uploaded photos). The participants
are the teller, the cast and anyone named.

A story replays in any family that has its whole cast: a telling in one family is only
matched against the stories whose cast is in that family, so two families' stories never
shadow each other. At startup a story is compiled against its `familyId`, else the first
family in `manager.REPLAY_FAMILIES` with its whole cast (the demo family, plus any family a
launcher adds with `add_replay_family`, as `demo/whitakers/` does), else the demo family,
which then names the missing cast.

If a matched story cannot be used for a run (a set or prop is missing there, or it fails to
compile there), the log says why and the run ends on the scripted scene, never on an error.

### Matching

Both texts are normalized: case folded, apostrophes dropped, every other punctuation mark
read as a space, runs of whitespace collapsed. Then, for each story:

1. **exact**: the normalized texts are equal;
2. **overlap**: the word overlap (Dice over the two word multisets: twice the shared words
   over all words) is at least `minOverlap`, so a typo or a dropped word still matches;
3. **keywords**: when `keywords` are given, every one appears as whole words.

The best match wins: exact, then overlap, then keywords, with ties going to the higher
overlap and then the folder name. Two stories with the same normalized text are refused at
startup. Only a new memory is ever matched; a second telling of any memory is the scripted
second telling.

### Test a story

1. `uv run --project api python -m demo.replay.serve --check --replay-dir <dir>` compiles it.
2. Start the launcher and the web app as in "Run it", sign in as the teller, go to `/add`,
   paste `match.text`, submit and answer the crafting book. The log line
   `replay: run ... matched prepared story '<folder>'` confirms the match, and the saved
   memory opens on the prepared scene in the book.
3. Submit some other telling: it ends on the hymn-book scene.

The example in `demo/replay/example/sunday_tamales/` replays the demo family's own
`demo_sunday_tamales` screenplay (its files point at `api/seed/demo_family/`). Its narration
is Ana's, so it reads best signed in as `ana@demo.stitch`; signed in as David, David narrates
Ana's lines.

## Tests

From the repo root:

```sh
uv run --project api pytest demo/replay/tests -q
```

They cover matching, loading and validating stories (a broken one fails by name), a
non-matching telling getting the scripted hymn-book scene with the same events, a matching
one streaming the same event sequence and saving the compiled prepared screenplay, the props
review on prepared props, second tellings of a replayed memory, the app wiring through the
real routes, and the launcher's exit codes. `test_manager.py` also pins the private methods
of `ScriptedRunManager` that replay overrides or calls (`manager.SEAMS`), so a change there
fails these tests instead of silently replaying the hymn-book scene.
