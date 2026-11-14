# Flow pages in a real browser

Two things live here:

- `harness.py`: a small Python harness for driving the flow pages in headless Chromium. It
  starts the offline API and `next dev`, watches each page for console errors, failed
  requests and 5xx responses, takes numbered screenshots, and keeps a log of seams (what
  worked, what broke, and whose fix it is). Sweep scripts build on it.
- `demo_path.py`: the weekend demo path, end to end, against the real API in offline mode,
  with two people in two separate browser contexts.

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
| `--api-port N`, `--web-port N` | Fixed ports (default: free ones). The script fails with a clear message if one is busy. |

The steps: A signs up and starts a family, makes an invite link (and, with `claim`, adds B
as a relative). B opens the invite, creates an account, joins, and follows "See the family
tree" home. A adds a story with a year, B as a participant and a photo, answers the
follow-up question ("Who else was there that day?"), keeps the hymn books in the prop review
and waits for the finished scene. The script checks over the API that the memory is in both
books, opens `/add?memoryId=` as B (second telling by moment), then answers B's open question
from `/add` (second telling by question), and checks B's telling and narration track on the
memory. Last, it looks at where the finished flow leads: the scene link, the home page and
B's book page.

## What it leaves behind

In the out dir (printed at the start and the end):

- `seams.json`: every check, in order, as
  `{"seams": [{"step", "ok", "owner", "detail"}], "counts": {"total", "ok", "failed", "failedByOwner"}}`.
- `shots/NN-<step>.png`: a full-page screenshot after every step (and at the follow-up, the
  prop review and the second-telling form), numbered in the order taken.
- `api.log` and `web.log`: the two servers' output.

The same table is printed at the end, followed by every `REQUEST` for Ethan.

## Owners

| Owner | Means | Fails the run? |
|---|---|---|
| `pages` | The flow pages (`app/(auth)`, `app/families`, `app/join`, `app/add`, `components/flow`, `components/agent`, `lib/api.ts`). | Yes (exit 1) |
| `api` | The API returned the wrong data (a memory missing from a book, no narration track). | Yes (exit 1) |
| `ethan` | Ethan's pages (`/`, `/people/[id]`, `/memories/[id]`). The detail starts with the exact `REQUEST` text to post. | No: reported only |

A step that could not run because an earlier one failed is recorded as `not run: needs step(s) ...`.
Browser problems (console errors, uncaught errors, failed requests, 5xx responses) seen during
a step are recorded as their own seam, owned by whoever owns the page they happened on (5xx:
`api`). Two things are not problems: a 401 from `GET /me` (how a page learns nobody is signed
in) and `net::ERR_ABORTED` (a navigation cancelling requests in flight).

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
from harness import PageWatch, SeamLog, require_chromium, run_stack, shot

require_chromium()
from playwright.sync_api import sync_playwright

seams = SeamLog()
with run_stack(mock=True) as stack, sync_playwright() as pw:   # mock=False for the real API
    page = pw.chromium.launch().new_page()
    watch = PageWatch()
    watch.attach(page, "A")
    page.goto(f"{stack.web_url}/login")
    shot(page, stack, "login")
    seams.record("login renders", not watch.problems, "\n".join(watch.problems))
seams.write(stack.out_dir)
```

Drive pages through roles, labels and text only (`get_by_role`, `get_by_label`,
`get_by_text`); never add test ids. With `cacheComponents` on, a page you navigated away from
stays mounted but hidden, so `/signup`'s "Your name" is still in the DOM on `/families/new`:
`get_by_role` skips hidden elements, but add `.filter(visible=True)` to `get_by_label` and
`get_by_text`. Open the app at `localhost`, never `127.0.0.1` (the session cookie is
`SameSite=Lax`); `run_stack` already does. `next dev` may rewrite `web/AGENTS.md`: never
commit that change.
