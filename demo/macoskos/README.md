# The Macoskos: the demo family

A second demo family for the live demo: 17 people across four generations, 13 pre-made moments, Higgsfield art for the hero scenes, and two stories that are told live and replayed onto prepared scenes ("Marmalade comes home", typed on /add, and the canoe story, told by phone). The names and stories are invented.

The family's real photos are **not** in this repo. Without them every spread shows no photo, and the launcher prints a warning per missing file. To get them, ask Ethan, then drop the files into `data/photos/` with the file names the warnings list (for example `wedding.jpg`).

The 13 seeded moments and both prepared live stories include **Eric — Smooth,
Trustworthy** narration from ElevenLabs, the same voice used by the call agent.
The recordings in `data/narration/` contain only the fictional demo stories;
there are no API keys or family photos in that folder. A fresh clone plays them
offline. They are imported into the normal family-scoped media store, so they
use the same authenticated audio route as other narration. Each telling has its
own recording. Private `--data-dir` copies also use these bundled recordings
when their narration text and shot timing match.

For edited or new **prepared replay** narration, set `ELEVENLABS_API_KEY` in your
local environment. `ELEVENLABS_DEFAULT_VOICE` overrides Eric and
`ELEVENLABS_MODEL` overrides `eleven_multilingual_v2`. Newly generated tracks are
cached under `~/.cache/stitch/demo-narration` (override with
`STITCH_DEMO_NARRATION_CACHE`), never written into this repository. Startup and
`--check` never generate audio or spend credits, even with a key present. A stale
or missing recording is not reused for changed words: the launcher logs a warning
and that telling keeps the browser speech fallback. Set `STITCH_DEMO_NARRATION=off`
to disable demo recordings. Other demo families are unaffected.

These recordings preserve the prepared animation timing and extend its ending
when speech needs more time; they do not retime each action to individual spoken
words. The saved word timings drive narration captions.

## Run it

From the repo root (`cd api && uv sync` once, and `cd web && npm ci` once):

```sh
uv run --project api python -m demo.whitakers.serve        # API on :8000, seeds the Macoskos beside the Maos
cd web && npx next dev -p 3000                              # web on :3000
```

Sign in as `jenna@macosko.demo` with the password `stitch-demo`. Use a fresh browser window, because the session cookie is shared across localhost ports. `LIVE_SCRIPT.md` has the exact text for the live stories and the other accounts.

The launcher reads `~/stitch-private/demo-family/data` when it exists, else this folder; `--data-dir DIR` or `STITCH_WHITAKERS_DIR` picks another. `--check` validates everything and exits. Each live story replays only as a new memory, so restart the launcher before telling one a second time.

For the phone story: add `--channels` with `STITCH_CHANNELS_ELEVENLABS=1`, `CHANNELS_SHARED_SECRET`, `ELEVENLABS_WEBHOOK_SECRET` and `ELEVENLABS_AGENT_IDS` in the environment (never commit them), and see `api/channels/ELEVENLABS.md` and `channels/CALLS.md` for the webhook proxy and tunnel.
