# Flow pages in a real browser

What lives here:

- `harness.py`: a small Python harness for driving the flow pages in headless Chromium. It
  starts the offline API and `next dev`, watches each page for console errors, failed
  requests and 5xx responses, takes numbered screenshots, and keeps a log of seams (what
  worked, what broke, and whose fix it is). Sweep scripts build on it.
- `demo_path.py`: the weekend demo path, end to end, against the real API in offline mode,
  with two people in two separate browser contexts.
- `sweep_accounts.py`, `sweep_join.py`, `sweep_add.py`, `sweep_run.py`, `sweep_settings.py`:
  sweeps of one area each, every state at two window sizes, in mock mode and against the
  offline API (see [Sweeps](#sweeps)). A sweep that is not on your branch yet is skipped by
  `run_all.py`.
- `run_all.py`: runs the demo path and every sweep one after another, then prints one
  summary (see [Run everything](#run-everything)).

## Prerequisites

Everything runs from `api/`, in the API's uv environment (it already depends on Playwright).
No MongoDB, Docker, API keys or network: the API is `python -m seed.serve` on an in-memory
database with scripted agent runs.

```sh
cd api && uv sync && uv run playwright install chromium   # Chromium, once
cd ../web && npm ci                                        # the web app's dependencies, once
```

Without Chromium the scripts print
`SKIP: Playwright Chromium is not installed (cd api && uv run playwright install chromium)`
and exit 0.

## Run everything

The one command to run before a demo or a merge:

```sh
cd api && uv run python ../web/components/flow/e2e/run_all.py
```

| Flag | What it does |
|---|---|
| `--only demo,accounts,join,add,run,settings` | Run only these areas (default: every script found). |
| `--mode mock\|real\|both` | Passed to the sweeps (default `both`). The demo path always runs against the offline API. |
| `--out DIR` | Results root (default: a new `stitch-run-all-*` folder in the system temp dir). |
| `--headed` | Show the browser windows. |
| `--unit` | First run the web unit tests: `node --test "components/flow/__tests__/*.test.mjs" "components/agent/__tests__/*.test.mjs"` in `web/`. |

It finds the scripts by file name next to it: `demo_path.py` is the area `demo`, and each
`sweep_<area>.py` is the area `<area>`. An area named in `--only` with no script on your
branch is skipped with a note. Each script runs in its own process on a fresh pair of free
ports, writing into `<out>/<area>/` (its own `seams.json`, `shots/` and logs); the next one
starts once those ports are free again. The demo path gets `--keep-going`, so its report
covers every step.

At the end it writes `<out>/summary.json`:

```json
{"runs": [{"area": "demo", "exit": 0, "seconds": 96.4,
           "seams": {"total": 21, "ok": 19, "failed": 2, "failedByOwner": {"pages": 0, "ethan": 2, "api": 0}},
           "firstFailure": "[ethan] 6b A watches the scene: REQUEST for Ethan: ..."}],
 "failedByOwner": {"pages": 0, "ethan": 2, "api": 0}}
```

`seams` is that run's `seams.json` counts, or `null` (the unit tests, or a script that
wrote none). `firstFailure` is `[owner] step: first detail line` of its first failed seam
(a `pages` or `api` one before an `ethan` one), or `null`. It prints the same as a table,
with each run's first failure under it. The exit code is 1 when any run exited
non-zero, else 0; without Chromium it prints the SKIP line and exits 0.

## Run the demo path

```sh
cd api && uv run python ../web/components/flow/e2e/demo_path.py
```

It takes about a minute, most of it `next dev` compiling pages the first time.

| Flag | What it does |
|---|---|
| `--headed` | Show the browser windows. |
| `--out DIR` | Write the results to `DIR` (default: a new `stitch-e2e-*` folder in the system temp dir, never inside the repo). |
| `--join claim` | (default) A adds B as a relative first; B claims that person from the invite. |
| `--join add-self` | B joins as someone new and says they are married to A. |
| `--keep-going` | Run every step even after a flow-page failure (by default it stops at the first one). |
| `--strict-ethan` | Also exit 1 when one of Ethan's pages has a seam (by default those are reported only). |
| `--api-port N`, `--web-port N` | Fixed ports (default: free ones). The script fails with a clear message if one is busy. |

The steps:

| Step | What it checks |
|---|---|
| 1-3 | A signs up, starts a family, makes an invite link. |
| 4 | (`claim` only) A adds B as a relative, married to A. |
| 5 | B opens the invite, creates an account, joins (claims B, or adds themselves married to A), and follows "Open the family tree" home. |
| 6 | A adds a story with a year, B as a participant and a photo, answers the follow-up ("Who else was there that day?"), keeps the hymn books in the prop review and waits for the finished scene. "Open your page" must be `/memories/<id>?family=<fid>` with the family A just made: a missing or different `family` is a `pages` seam naming the exact href. |
| 6b | A watches the scene: "Watch the scene" on the finished run must be `/render/<id>?family=<fid>` (else `pages`). The script follows it and waits up to 60 s for `<html data-stage-ready="1">` (or `window.__STITCH_READY__`). A stage that never gets ready is an `ethan` seam with a `REQUEST`, a screenshot and the page's browser problems (`harness.app_problems`). |
| 7 | Over the API: the memory is in A's and B's books. |
| 8a | B opens `/add?memoryId=`: second-telling mode with the moment's title and B's question, nothing sent. |
| 8 | B picks the question on `/add` ("Someone asked you"). The moment's title must show on the note or in the second-telling header. B sends a telling; the finished run must link into the moment (`/memories/<id>?family=<fid>`) and home (`/`). |
| 9 | Over the API: B's telling and narration track are on the memory. |
| 10 | Where the finished flow leads: the scene link, the home page (the signed-in family, links into the flow) and B's book page are Ethan's; family settings and `/add` must each have a visible link to `/` (`pages`). |

## What it leaves behind

In the out dir (printed at the start and the end):

- `seams.json`: every check, in order, as
  `{"seams": [{"step", "ok", "owner", "detail"}], "counts": {"total", "ok", "failed", "failedByOwner"}}`.
- `shots/NN-<step>.png`: a full-page screenshot after every step (and at the follow-up, the
  prop review and the second-telling form), numbered in the order taken.
- `api.log` and `web.log`: the two servers' output.

If `--out` points at a folder an earlier run used, that run's `seams.json`, `shots/` and logs
are first moved into `previous-<time>/` inside it. Nothing is deleted, and the summary lists
only this run's screenshots. In a sweep script, `shot()` keeps numbering after the highest
number already in `shots/`, and `shots_taken(out_dir)` lists the shots this process took.

When the stack stops, both process groups are terminated, then killed after 10 s. So is the
telemetry flush that `next dev` spawns outside its group on the way out. No `uvicorn`, `next`
or `node` process outlives the run.

The same table is printed at the end, followed by every `REQUEST` for Ethan.

## If the stack won't start

When `seed.serve` or `next dev` exits or never answers, the harness reads what that start
wrote to `api.log` / `web.log` and puts the cause on the first line of the error
(`harness.diagnose_start_failure`). That line is what the console shows next to `FAIL`, with
up to three lines of context under it; the full log tail is in `seams.json`, and
`run_all.py` repeats the line under its table as `<area> first failure: ...`.

| The console says | What to do |
|---|---|
| `Another next dev is already running in web/ (pid N, ...): stop it with kill N, then run again.` | A `next dev` from this checkout (yours, or a run that did not stop) holds `web/`'s dev lock, whatever port it is on. Run the `kill` it names. |
| `Port N is already in use ...` | Something else listens on the web port you passed with `--web-port`: stop it (`lsof -i :N`) or pick another port. |
| `The API could not listen on port N ...` | Another `seed.serve` or `uvicorn` holds the API port: stop it or pass another `--api-port`. |
| `The web app's dependencies are not installed ...: run npm ci in web/` | `web/node_modules` is missing or incomplete (`Cannot find module`, `next: command not found`). Run `npm ci` in `web/`. |
| `The API's Python environment has no ...: run uv sync in api/` | Run `uv sync` in `api/` and start the script with `uv run` from `api/`. |

Any other failure keeps the plain sentence (`... exited with code 1 before it was ready.` or
`... was not ready after N s.`) followed by the log tail: read `web.log` or `api.log` in the
out dir.

## Owners

| Owner | Means | Fails the run? |
|---|---|---|
| `pages` | The flow pages (`app/(auth)`, `app/families`, `app/join`, `app/add`, `components/flow`, `components/agent`, `lib/api.ts`). | Yes (exit 1) |
| `api` | The API returned the wrong data (a memory missing from a book, no narration track). | Yes (exit 1) |
| `ethan` | Ethan's pages (`/`, `/threads`, `/people/[id]`, `/memories/[id]`, `/render/[id]`). The detail starts with the exact `REQUEST` text to post. | No: reported only, unless `--strict-ethan` |

A step that could not run because an earlier one failed is recorded as `not run: needs step(s) ...`.
If the stack cannot start, or anything else goes wrong outside a step (a busy port, `npx`
missing), that is one `pages` seam with the error, and `seams.json` and the summary are still
written.

Browser problems (console errors, uncaught errors, failed requests, 5xx responses) seen during
a step are recorded as their own seam, owned by whoever owns the page that was open when they
happened (5xx: `api`). Each line reads `[A] kind at <page URL>: text`. A console error also
names the script or resource that logged it as `(source: ...)`, but the owner always comes from
the page URL, so a React warning on `/` is Ethan's even though a `/_next/static` chunk printed it.

These are not problems:

- a 401 from `GET /me`, which is how a page learns nobody is signed in;
- `net::ERR_ABORTED` on a navigation request, or on a request that was in flight when the page
  navigated (a `goto` or a client-side route change);
- `net::ERR_ABORTED` after a 2xx/3xx answer that is not an event stream had already arrived.
  `lib/api.ts`'s `requestVoid()` cancels bodies it does not need, such as `POST /auth/signup`.

Every other abort is reported, for example an event stream that the page cuts off while it
stays on the same URL.

## Sweeps

A sweep walks one area of the flow pages through every state (empty, loading, validation
errors, server errors, success) at two window sizes, in mock mode and against the offline
API, and checks the form kit on the way. Each check is one seam with a screenshot.

| Script | Pages | What it checks |
|---|---|---|
| `sweep_accounts.py` | `/login`, `/signup`, `/families/new` | See below. |
| `sweep_join.py` | `/join/<code>` | The join page: the invite, claiming a person or adding yourself, relationships, and its errors. Its docstring lists every check. |
| `sweep_add.py` | `/add` | The add-a-memory page. Its docstring lists every check. |
| `sweep_run.py` | `/add?run=` | A run under way and finished. Its docstring lists every check. |
| `sweep_settings.py` | `/families/<fid>/settings` | Family settings. Its docstring lists every check. |

```sh
cd api && uv run python ../web/components/flow/e2e/sweep_accounts.py
cd api && uv run python ../web/components/flow/e2e/sweep_join.py
```

### The sweep CLI contract

Every `sweep_*.py` keeps this contract, which `run_all.py` relies on:

- it accepts `--mode mock|real|both`, `--headed`, `--out DIR`, `--api-port N` and
  `--web-port N` (`harness.sweep_args`), and no required argument;
- it writes `seams.json` (`harness.SeamLog`) into the `--out` dir;
- it ends through `harness.finish`: exit 1 when a `pages` or `api` seam failed, else 0;
- without Chromium it prints the SKIP line and exits 0 (`harness.require_chromium`).

The flags (`harness.sweep_args`):

| Flag | What it does |
|---|---|
| `--mode mock\|real\|both` | `mock`: only `next dev` with `NEXT_PUBLIC_API_MOCK=1` (the in-browser mock backend). `real`: `seed.serve` (in-memory database, demo family) and `next dev`. `both` (default): mock, then real, one after the other on the same ports. |
| `--headed` | Show the browser window. |
| `--out DIR` | Write the results to `DIR` (default: a new `stitch-sweep-*` folder in the system temp dir, never inside the repo). |
| `--api-port N`, `--web-port N` | Fixed ports (default: free ones). |

Every page is walked at both `harness.VIEWPORTS`: `desktop` (1280 x 900) and `phone`
(390 x 844), each person in a fresh browser context. A sweep leaves `seams.json` in the out
dir (all modes together), and `<mode>/shots/NN-<mode>-<viewport>-<check>.png`,
`<mode>/web.log` and `real/api.log`. The exit code is 1 when a `pages` or `api` seam
failed, else 0. Without Chromium it prints the SKIP line and exits 0.

Browser problems seen during a check (console errors, uncaught errors, failed requests,
5xx answers) are their own seam, minus `harness.expected_noise()`: Chromium's
"Failed to load resource ... status of 4xx" lines (a 401 on a wrong password, a 409 on a
taken email are how the pages work) and aborted RSC prefetches (`GET ...?_rsc=...
net::ERR_ABORTED`). The failures a check injects on purpose are not problems either.

Server errors and slow answers are injected per request. Against the real API that is
Playwright's `page.route` on the API URL. The mock backend never touches the network, so in
mock mode they go through its fault injection (`window.__stitchMock.inject`, or
`harness.mock_faults_script()` before the backend exists; added by PR #194). On a branch
whose mock backend has no fault injection, those checks are recorded as `SKIPPED` (ok) and
real mode covers them.

### sweep_accounts.py

On each page, in each mode and viewport:

- **Renders**: the heading, every input found by its label (`get_by_label`), no unlabelled
  input, the submit button enabled once the page is interactive.
- **Tab order**: Tab from the top; the stops inside `<main>` run top to bottom (left to
  right on one row), and the fields come in form order.
- **Empty submit**: every required field gets `aria-invalid="true"` and an
  `aria-describedby` that includes its message; focus moves to the first invalid field.
- **Validation errors**: log in: a bad email. Sign up: a blank name, a bad email, a password
  under `PASSWORD_MIN`. Start a family: an empty family name, an empty name, a year in the
  future and before 1850. There is no confirm-password field, so nothing can mismatch.
- **Enter submits**: the 401, the 409 and the bad year are sent by pressing Enter.
- **Wrong password (401)** and **a taken email (409)**: a form-level message (not a generic
  one), focus on it, the email kept.
- **Server errors**: 500, 502, 503 and a dropped connection on `POST /auth/login`,
  `/auth/signup` and `/families`: the message from `lib/api.ts`, focus on it, the button
  enabled again, what was typed kept, still on the page.
- **Pending**: the request is held (real) or delayed (mock) and the button double-clicked:
  the button reads its busy label, is disabled with `aria-busy="true"`, the fields are
  disabled, and exactly one request goes out (real: counted on the network; mock: a second
  "sentinel" fault that a second request would use up).
- **Success**: sign up goes to `/families/new` with your name filled in; starting a family
  goes to its settings with `?welcome=1` (the invite step); log in as a seeded user goes home
  (`/`). Seeded users: `david@demo.stitch` / `stitch-demo` (real), `david@example.com` /
  `DEMO_PASSWORD` (mock).
- **`?next=`**: a signed-out `/families/new` goes to `/login?next=%2Ffamilies%2Fnew` and back
  after logging in; `/signup?next=/threads` lands on `/threads`; `//evil.example/x` and
  `https://evil.example/` are ignored.
- **Optional mark**: "The year you were born (optional)" is the field's accessible name, with
  the space.
- **Typed before hydration**: `next dev` renders `/login` and `/signup` on the server. The
  sweep fills the fields the moment the document arrives, before React hydrates, and checks
  the text is still there afterwards and the form sends it. Also with a stale signed-in hint
  in `localStorage` (the page hides the form while it asks `/me`), and with Enter pressed at
  once (the password must never end up in the URL through the browser's own GET submit).

## The harness's tests

Offline, with no browser and no servers:

```sh
cd api && uv run pytest ../web/components/flow/e2e/test_harness.py -q
```

## Writing a sweep with the harness

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import (VIEWPORTS, PageWatch, SeamLog, app_problems, finish, require_chromium,
                     run_stack, shot, sweep_args, sweep_modes, sweep_out_dir)

args = sweep_args("Sweep the log in page.")
require_chromium()
from playwright.sync_api import sync_playwright

out = sweep_out_dir(args.out)
seams = SeamLog()
for mock in sweep_modes(args.mode):
    mode = "mock" if mock else "real"
    with run_stack(mock=mock, web_port=args.web_port, api_port=args.api_port,
                   out_dir=out / mode) as stack, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        for view, (width, height) in VIEWPORTS.items():
            page = browser.new_context(viewport={"width": width, "height": height}).new_page()
            watch = PageWatch()
            watch.attach(page, f"{mode}/{view}")
            page.goto(f"{stack.web_url}/login")
            shot(page, stack, f"{mode}-{view}-login")
            problems = app_problems(watch)
            seams.record(f"[{mode}/{view}] login renders", not problems, "\n".join(problems))
        browser.close()
raise SystemExit(finish(seams, out))
```

With `--mode both` the second stack reuses the first one's ports: wait for them with
`wait_port_free()` first (`sweep_accounts.py` does).

Drive pages through roles, labels and text only (`get_by_role`, `get_by_label`,
`get_by_text`); never add test ids. With `cacheComponents` on, a page you navigated away from
stays mounted but hidden, so `/signup`'s "Your name" is still in the DOM on `/families/new`:
`get_by_role` skips hidden elements, but add `.filter(visible=True)` to `get_by_label` and
`get_by_text`. Open the app at `localhost`, never `127.0.0.1` (the session cookie is
`SameSite=Lax`); `run_stack` already does. `next dev` may rewrite `web/AGENTS.md`: never
commit that change.
