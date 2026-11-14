# The studio: first real run

This is the runbook for the first run of the agent studio with real keys, in front of people. Read it top to bottom once before the demo, and keep it open during it.

## What the studio is

The studio is the Deep Agents graph that turns a family's story into a staged pop-up memory: one main agent with eight sub-agents (interviewer, storyboarder, prop_master, set_designer, director, narrator, checker, linker), the screenplay compiler, and services for art, speech, vision and frame rendering. [ARCHITECTURE.md section 2](../../ARCHITECTURE.md#2-the-agent-deep-agents) describes it. `agent/production.py` picks each service from the environment; `agent/preflight.py` reports those picks, and checks them, before you start.

Run modes, set with `STITCH_AGENT` when the API starts:

| `STITCH_AGENT` | What "add a memory" does |
|---|---|
| `real` | Runs the model studio (`agent.production`). Needs `ANTHROPIC_API_KEY`. If the studio cannot be built (no key, a malformed voice map, an unreachable MongoDB, see below), startup logs a warning and falls back to `fake`. |
| `fake` | Scripted runs from `seed/fake_runs.py`: no model calls, no keys. The feed plays a canned run; the family's own story is not staged. |
| `off` | No agent; the add-a-memory routes answer 503. |
| unset | `fake` when `STITCH_FAKE_DB` is on or `ANTHROPIC_API_KEY` is unset, otherwise `real`. |

Offline, without the API or any key, `uv run python -m agent.demo --fake --auto` runs one whole memory with a scripted brain and fake services on the real compiler. `uv run python -m agent.demo --fake --auto --weekend` runs the whole stage demo path the same way: mom tells the story with a photo, the memory lands in her book and dad's, dad adds his second telling, and the last line (`timeline: valid, ...`) checks the saved timeline against the contract. Both exit 0. Without `--fake`, `agent.demo` (with or without `--weekend`) runs the real studio and exits 2 with `ANTHROPIC_API_KEY is not set` when the key is missing.

## Environment

### Where to set it

Put the variables in `api/.env`. The API (`main.load_env_files`) reads `api/.env` first, then the repo-root `.env`; the first file that sets a name wins, and a variable already set in the shell wins over both files. `STITCH_NO_DOTENV=1` turns the files off (and they are never read under pytest). `python -m agent.preflight` reads the same files in the same order and prints which ones it loaded. Write `NAME=value`, with no quotes and no spaces around `=`.

### Every variable, by service

| Variable | Service | Default | If unset |
|---|---|---|---|
| `STITCH_AGENT` | agent | see the table above | `real` with a key, else `fake` |
| `ANTHROPIC_API_KEY` | model, vision, critic | none (required, no fallback) | No studio: `STITCH_AGENT=real` falls back to scripted runs. Preflight exits 1. |
| `STITCH_MODEL` | model | `claude-opus-5-5` (`studio.DEFAULT_MODEL`) | The default model |
| `STITCH_SUBAGENT_MODEL` | model | the studio model | Sub-agents use the studio model |
| `STITCH_VISION_MODEL` | vision | `claude-opus-5-5` (does not follow `STITCH_MODEL`) | The default model |
| `MONGODB_URI` | persistence | `mongodb://localhost:27017` for family data | Runs, checkpoints and `/memories/` notes are kept in memory and lost on restart |
| `MONGODB_DB` | persistence | `stitch` | |
| `STITCH_FAKE_DB` | persistence | off | On (`1`, `true`, `yes`): everything is in an in-memory database seeded with the demo family, and `MONGODB_URI` is ignored |
| `IMAGE_API_KEY` | art | none | Placeholder art |
| `IMAGE_API_URL` | art | `https://api.openai.com/v1` | |
| `IMAGE_MODEL` | art | `gpt-image-1` | |
| `ELEVENLABS_API_KEY` | tts | none | Silent narration with estimated word timings |
| `ELEVENLABS_MODEL` | tts | `eleven_multilingual_v2` | |
| `ELEVENLABS_VOICES` | tts | none | Every voice uses the default voice. Format: `voice_a=<id>,voice_b=<id>` (the studio asks for `voice_a` to `voice_d`) |
| `ELEVENLABS_DEFAULT_VOICE` | tts | `21m00Tcm4TlvDq8ikWAM` (Rachel) | |
| `STITCH_WEB_URL` | renderer | none | The checker critiques labelled diagrams instead of screenshots |
| `STITCH_MAX_MODEL_CALLS` | limits | `600` | see [Time and cost limits](#time-and-cost-limits) |
| `STITCH_MAX_IMAGES_PER_RUN` | limits | `24` | see [Time and cost limits](#time-and-cost-limits) |
| `STITCH_MAX_GRAPH_STEPS` | limits | `1000` | |
| `STITCH_MODEL_TIMEOUT_S` | limits | `120` | |
| `STITCH_MODEL_MAX_RETRIES` | limits | `2` | |
| `STITCH_RUN_TIMEOUT_S` | limits | `1200` | |

A model id (`STITCH_MODEL`, `STITCH_SUBAGENT_MODEL`, `STITCH_VISION_MODEL`, `IMAGE_MODEL`, `ELEVENLABS_MODEL`) that is blank or only spaces means the default, exactly as if it were unset, and a value with spaces around it is used stripped (`agent.guards.env_value`, which production and preflight both use, so the model preflight names is the model the studio calls).

Not studio variables, but the API reads them too: `JWT_SECRET` (without it sessions end when the API restarts) and `VOYAGE_API_KEY` (without it "ask the family" and prop matching use keyword search). Preflight never prints them either.

### What preflight checks without the network

Secrets first: a key never prints, only `set (N chars), format ok` or `set (N chars)` and the problem. `MONGODB_URI` prints as scheme and hosts only; `STITCH_WEB_URL` and `IMAGE_API_URL` print without credentials or query string. Everything preflight writes (text, `--json`, provider errors and any log line) goes through one scrub that removes every secret value in the environment, raw, URL-decoded and URL-encoded, before a provider error is cut to 300 characters.

Surrounding quotes, whitespace or a newline in a key or URL are errors. A model id is shown as the studio will use it: blank reads `blank, treated as unset (default: ...)`, and spaces around it are a warning only (the studio strips them); quotes or spaces inside it are errors. `ANTHROPIC_API_KEY` should start with `sk-ant-`, `IMAGE_API_KEY` with `sk-` when `IMAGE_API_URL` is the OpenAI default, and `ELEVENLABS_API_KEY` with `sk_`; these prefix checks are warnings only. `ELEVENLABS_VOICES` and `ELEVENLABS_DEFAULT_VOICE` are checked with the TTS adapter's own rules (an error when the ElevenLabs key is set, because the studio then refuses to build). `MONGODB_URI` must start with `mongodb://` or `mongodb+srv://`; a password with an unescaped `/`, `?`, `:`, `@` or `%` is an error (pymongo would refuse or misread it: percent-encode it with `urllib.parse.quote_plus`), and `--live` skips the Mongo ping until it is fixed, because pymongo's error would quote part of the password. Each `STITCH_*` limit shows whether it is set and the value in force; an invalid value is a warning, because the studio keeps the default.

## First real run, step by step

All commands run from `api/` unless they say otherwise.

1. **Install.** `uv sync`, then `uv run playwright install chromium` (the checker's screenshots need it once `STITCH_WEB_URL` is set).

2. **Set the keys in `api/.env`.** Start from the repo's `.env.example` (`cp ../.env.example .env` if you have none). At least `ANTHROPIC_API_KEY`; for the full experience also `IMAGE_API_KEY`, `ELEVENLABS_API_KEY` and `MONGODB_URI`, plus `JWT_SECRET`. Set `STITCH_WEB_URL=http://localhost:3000` so the checker sees screenshots of the real stage (the web app must be running, step 7; see [The render page](#the-render-page-and-the-checker)). Consider `STITCH_MAX_IMAGES_PER_RUN=12` for the demo to cap image cost.

3. **Run preflight.**

   ```bash
   uv run python -m agent.preflight
   ```

   It prints which `.env` files it loaded, then one block per service: `[service]  mode: <mode>`, its variables (set or unset, never a secret), any ERROR or warning, and for a fallback a `fallback:` line saying what the family will see. The last block, `[limits]`, lists the run limits. It ends with the `studio services: ...` line the API will log (when the model key is set) and `result: N error(s), M warning(s); exit C`.

   Reading the modes: `real`, `mongo`, `playwright` and `screenplay.compiler` are the real thing. `placeholder` (art), `fake` (tts: silent), `diagram` (renderer), `memory` (persistence) and `FakeCompiler` are fallbacks. `missing` (model, vision) means no `ANTHROPIC_API_KEY`: the studio cannot be built at all.

   Exit codes: `0` when everything required is set and well formed (warnings never fail); `1` on any error; `2` for bad arguments. Fix every ERROR. Read every warning and decide.

4. **Run preflight against the providers.**

   ```bash
   uv run python -m agent.preflight --live
   ```

   One minimal call per configured service, and `skipped` for the ones running as fallbacks:

   | Service | Call | Cost |
   |---|---|---|
   | model | Anthropic `models.retrieve` for each model id, then one `messages.create` with `max_tokens=1` on the studio model (checks billing and credit) | a fraction of a cent |
   | vision | `models.retrieve` for `STITCH_VISION_MODEL` | free |
   | art | `GET /models/{IMAGE_MODEL}` on the image API (no generation) | free |
   | tts | `GET /v1/voices/{id}` on ElevenLabs for each voice | free |
   | persistence | a MongoDB ping (5 second timeout); the default `mongodb://localhost:27017` when `MONGODB_URI` is unset | free |
   | renderer | with `STITCH_WEB_URL` set, opens `{STITCH_WEB_URL}/render/draft-preflight?t=0.000` in headless Chromium with a minimal scene injected (`agent.tools.frames.probe_render_page`) | free |

   Each line reads `live: pass`, `live: warn`, `live: fail` or `live: skipped`, followed by the detail; a failure shows the provider's own error, scrubbed of every key and cut to 300 characters. `warn` never fails the exit code. `--json` prints the same report as a JSON list for scripts.

5. **Dry run the real studio without the database.** `agent.demo` does not read `.env` itself, so hand it the file:

   ```bash
   STITCH_FAKE_DB=1 uv run --env-file .env python -m agent.demo --auto
   ```

   It prints `mode: real`, then the `studio services: ...` line. It then prints one line per run event, answers any question from the studio automatically, and ends with `result: done, memory <id>, N event(s)` (exit 0) or the error (exit 1). Add `--out /tmp/memory.json` to keep the saved memory. Drop `STITCH_FAKE_DB=1` to run against `MONGODB_URI` with the demo family seeded.

6. **Start the API with the real studio.**

   ```bash
   STITCH_AGENT=real uv run uvicorn main:app --reload
   ```

   Look for `STITCH_AGENT=real: agent runs use the model studio`. If you see `STITCH_AGENT=real: the model studio could not be built (...); falling back to scripted runs`, stop: the reason is in the parentheses, and preflight shows it too.

7. **Start the web app**, in a second terminal: `cd web && npm ci && npm run dev`, then open http://localhost:3000 (use `localhost`, not `127.0.0.1`: the session cookie is SameSite=Lax). If the API is not on port 8000, start the web app with `NEXT_PUBLIC_API_URL=http://localhost:<port>`.

8. **Use the web flow**: sign in (or sign up and start a family), add a memory with a story and photos, and watch the agent feed until the memory opens on the stage.

## The `studio services:` line

`agent.production.describe()` names every service choice in one line, and `python -m agent.preflight` prints the exact line the API will build (`preflight.expected_log_line`). With only `ANTHROPIC_API_KEY` set it is:

```
studio services: model=real subagent_model=studio repo=mongo persistence=memory art=placeholder tts=fake vision=real critic=real renderer=diagram compiler=screenplay.compiler limits=images=24 steps=1000 model_timeout=120s model_retries=2 run_timeout=1200s
```

(The `limits` label leaves out the model call cap; preflight's `[limits]` block shows it.)

With every key set, `MONGODB_URI` reachable and `STITCH_WEB_URL` set you want `persistence=mongo art=real tts=real renderer=playwright`.

| Label | Values |
|---|---|
| `model` | `real`: Claude via `STITCH_MODEL` |
| `subagent_model` | `studio` (sub-agents share the studio model) or the `STITCH_SUBAGENT_MODEL` id |
| `repo` | `mongo`: the family data, through the API's database (mongomock under `STITCH_FAKE_DB`) |
| `persistence` | `mongo` (runs survive a restart) or `memory` (they do not) |
| `art` | `real` or `placeholder` |
| `tts` | `real` (ElevenLabs) or `fake` (silent) |
| `vision`, `critic` | `real`: Claude vision |
| `renderer` | `playwright` (screenshots of the web stage, diagrams per render when it cannot) or `diagram` |
| `compiler` | `screenplay.compiler` or `FakeCompiler` |
| `limits` | the run limits in force (`RunLimits.label()`) |

`agent.demo` prints it as its first line. The API logs it at INFO from the `agent.production` logger, which uvicorn's default logging does not show; the startup lines it does show are the `STITCH_AGENT=...` ones and the warnings for in-memory persistence and diagram frames.

## Fallbacks

Every service except the model has a fallback, so a missing or failing key degrades the memory instead of failing the run.

| Service | When | What it looks like on stage |
|---|---|---|
| model, vision, critic | `ANTHROPIC_API_KEY` unset | **No fallback**: no studio. `STITCH_AGENT=real` falls back to scripted runs at startup; preflight exits 1. |
| vision, critic | A Claude vision call failing in the middle of a run | The run goes on. `describe_photo` answers `could not describe photo <id>: <ExceptionType>`, so that photo is staged from the story text alone; `inspect_frames` answers `could not inspect frames: <ExceptionType>`, and the checker passes without a critique. |
| art | `IMAGE_API_KEY` unset; one generation or background cut-out failing (that prop or layer only); the run used its image budget (`STITCH_MAX_IMAGES_PER_RUN`, `0` means always) | **Placeholder art**: props and set layers are flat shapes in a placeholder color instead of painted cut-paper art. Cut-outs of uploaded photos still work while the cut-out service does. |
| tts | `ELEVENLABS_API_KEY` unset; or an ElevenLabs call failing in the middle of a run (a 401, a quota, a timeout) and its one retry failing too | **Silent narration**: no voice plays, but the narration text still highlights word by word on estimated timings (0.3 s a word). The run goes on; after a mid-run failure only that teller's voice pass is silent, and the narrator's tool result carries a `warning: the speech service failed` line. |
| renderer | `STITCH_WEB_URL` unset; or, per render, the page unreachable, Playwright or Chromium missing, or the page lacking the render contract | **Diagram frames**: the family sees nothing different; the checker critiques labelled diagrams of the stage instead of screenshots, so it cannot catch art or layout problems (a puppet hidden behind scenery). |
| persistence | `MONGODB_URI` unset, or `STITCH_FAKE_DB` on | **In memory**: a run in progress, and the studio's notes about each person, are lost if the API restarts. Saved memories are in the family database and survive, unless `STITCH_FAKE_DB` is on. |
| persistence | `MONGODB_URI` set but unreachable (or its credentials refused) when the API starts | **In memory**, as above: the studio still builds, logs one error naming only the exception type (`MongoDB at MONGODB_URI could not be used (ServerSelectionTimeoutError); falling back to in-memory persistence`) and the services line says `persistence=memory`. `--live` shows the failed ping first: fix it, or unset `MONGODB_URI`, before going on stage. |
| compiler | `screenplay.compiler` has no `compile()` | **FakeCompiler** timelines (preflight warns). |
| agent | `STITCH_AGENT=fake`, or `real` that fell back | **Scripted runs**: the feed plays a canned run; the story the family typed is not staged. |

**Silent narration in the middle of a run.** With `ELEVENLABS_API_KEY` set, `synthesize_narration` calls ElevenLabs once more when a call fails; if the retry fails too, it voices that teller with silent audio and estimated word timings, logs a warning (`TTS failed for <teller>; narrating silently`) and the run carries on. The memory still plays, just without a voice. Run `--live` before the demo: a voice check that passes makes this unlikely.

## Time and cost limits

Six environment variables bound a run (`agent.guards.RunLimits`). A blank, unparsable or out-of-range value never stops the API: it logs one warning naming the variable and the default is used; preflight shows it as a warning under `[limits]`. The two per-run caps are `STITCH_MAX_MODEL_CALLS` (cost) and `STITCH_RUN_TIMEOUT_S` (time); `STITCH_MAX_IMAGES_PER_RUN` caps image cost on top.

| Variable | Default | Bounds |
|---|---|---|
| `STITCH_MAX_MODEL_CALLS` | `600` (at least `100`) | Chat model calls in one run, the studio's and every sub-agent's together (the eight named ones, deepagents' general-purpose one and any summarization call), across every answer, every resume and an API restart. A normal run makes roughly 60 to 200; the fake studio's runs make 23 to 57. The call past the cap is never sent: the run ends with an error event (`this run reached its limit of N model calls; please try again`) and the feed offers **Tell it again**. This is what stops a sub-agent stuck in a loop. |
| `STITCH_MAX_IMAGES_PER_RUN` | `24` | Image generations in one run; past it, the rest of the run's art is placeholder art. `0` means placeholder art only (no image cost at all). The count survives an API restart, so a restored run never gets a second budget. |
| `STITCH_MAX_GRAPH_STEPS` | `1000` (at least `50`) | Steps in one studio graph stream (LangGraph's `recursion_limit`), so one stream of a looping studio stops. **Not a per-run cap**: the run manager starts a new stream, with a fresh step budget, on every resume (each answer to a question, each rejected malformed question, a restart), and a sub-agent runs under the recursion limit deepagents binds on it (9999), not this one. Use `STITCH_MAX_MODEL_CALLS` and `STITCH_RUN_TIMEOUT_S` to bound a whole run. |
| `STITCH_MODEL_TIMEOUT_S` | `120` | Seconds one Claude request may take. |
| `STITCH_MODEL_MAX_RETRIES` | `2` | Retries of a failed Claude request. |
| `STITCH_RUN_TIMEOUT_S` | `1200` | Seconds one run may take in all. The clock counts only while the run is running, never while it waits on the teller to answer a question, so a family that takes ten minutes to answer does not time out. The run manager (`agent/runs.py`) ends a run past it with an error event (`the studio took too long on this story (over N minutes); please try again`), and the feed offers **Tell it again**. |

## The render page and the checker

The checker's Playwright renderer opens `{STITCH_WEB_URL}/render/{memoryId}?t=<seconds>`, puts the scene in `window.__STITCH_RENDER__` before the page loads, and waits for `window.__STITCH_READY__ === true` (the contract on Issue #1). `preflight --live` probes it and reports one of:

| Probe answer | `live:` | Meaning |
|---|---|---|
| `contract` | `pass` | The page draws the injected scene: the checker sees screenshots of the real stage. |
| `legacy` | `warn` | The page sets only `data-stage-ready` and lacks `window.__STITCH_RENDER__`/`__STITCH_READY__` (a web checkout from before the contract): the checker will see diagrams. |
| `unreachable` | `fail` | The page did not load or never became ready (is the web app running at `STITCH_WEB_URL`?). Renders fall back to diagrams. |
| `no_playwright` | `warn` | Playwright or its Chromium is not installed (`uv run playwright install chromium`): diagrams. |

**State today: `contract`.** `web/app/render/[memoryId]` on main implements the contract (`TimelineView.tsx` reads the injected `window.__STITCH_RENDER__`, honours `?t=` and sets `window.__STITCH_READY__ = true`; it landed with "Render: honour the checker's render contract"). Probing a local `npm run dev` of main with headless Chromium answers `contract`. So set `STITCH_WEB_URL=http://localhost:3000` with the web app running, and run `preflight --live` after the web app has started: the first request compiles the route, so the probe allows 30 seconds. If it answers `legacy`, your web checkout is older than the contract (`grep -r __STITCH_READY__ web/app/render` finds nothing): update it. Either way a render never fails a run: without the contract each render falls back to diagrams and, after a `legacy` answer, stays on diagrams for ten minutes without opening a browser.

## Troubleshooting by symptom

- **401 / `authentication_error` / `invalid x-api-key`** (Anthropic), **`HTTP 401: Incorrect API key provided`** (image API) or **`HTTP 401`** on `/v1/voices` (ElevenLabs): the key is wrong, revoked or for another account. Check preflight's format line for that key first: `wrapped in quotes`, `whitespace` or `newline` means the value in `.env` or the shell is not the bare key. A variable already set in the shell wins over `.env`: `env | grep -c ANTHROPIC_API_KEY` tells you whether the shell has one.
- **`NotFoundError` on `models.retrieve <id>`**: the model id in `STITCH_MODEL`, `STITCH_SUBAGENT_MODEL` or `STITCH_VISION_MODEL` does not exist or this key has no access to it. Unset it to use the default. A `messages.create` failure after the models passed usually means billing: no credit, or a spend limit.
- **`HTTP 404` on `GET /models/<IMAGE_MODEL>`**: the model id is wrong for that provider, or the provider at `IMAGE_API_URL` does not serve an OpenAI-style `/models` endpoint. The second case does not stop generation; check by making one memory.
- **MongoDB `ServerSelectionTimeoutError` / timed out**: the cluster is not reachable from this network. On Atlas, add this machine's IP to the Network Access list; check that the URI's user and password are right; on a venue network that blocks port 27017, use a phone hotspot or run with `STITCH_FAKE_DB=1`. A ping of `mongodb://localhost:27017 (db.client's default)` means `MONGODB_URI` is not set at all.
- **The startup log says `falling back to scripted runs`**: run `uv run python -m agent.preflight`; the reason is one of its errors.
- **A run is slow**: `STITCH_MODEL_TIMEOUT_S` bounds each Claude request and `STITCH_RUN_TIMEOUT_S` the whole run; lower `STITCH_MAX_IMAGES_PER_RUN` to cut image time.
- **`this run reached its limit of N model calls`**: the studio or one of its sub-agents kept calling the model (usually a director or checker going round in circles). Tell it again; if a normal story hits it, raise `STITCH_MAX_MODEL_CALLS` (the run's `model_calls` is in its run record) and look at the API log for the sub-agent that looped.
- **A model id seems ignored**: run preflight. The `[model]`, `[vision]`, `[art]` and `[tts]` blocks show the model id each service will use; a blank value is the default.

## If a run errors in front of people

Do not debug on stage.

1. **Tell it again.** A run that ends with its own error shows the error in the agent feed with a **Tell it again** button. It brings the add-a-memory form back filled in with what that run was sent (story and photos), so the teller presses send once more. Most mid-run failures (a provider timeout, a rate limit) pass on a second try. If the feed shows **Retry** instead, the connection dropped, not the run: Retry reconnects to the same run.
2. **Scripted runs on the same app**: if the second telling fails too, restart the API with `STITCH_AGENT=fake uv run uvicorn main:app`. The web flow works the same; adding a memory plays a scripted run.
3. **The seeded offline demo**: `uv run python -m seed.serve` (API on http://localhost:8000 with the demo family in memory, scripted runs, no MongoDB or keys), then `cd web && npm run dev`. Sign in as `david@demo.stitch`, `ana@demo.stitch` or `sam@demo.stitch`, password `stitch-demo`. See `api/seed/README.md`.

Afterwards, reproduce the failure from the terminal with `agent.demo` and the same story (step 5), with the API's log open: the error event names the step.
