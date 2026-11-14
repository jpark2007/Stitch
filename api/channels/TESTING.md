# Channels browser regression

`tests/channels/test_browser_e2e.py` is an explicit opt-in regression for the
local Channels demo. Normal `uv run pytest` skips it with a prerequisite message.
It runs real local HTTP, Chromium, the existing login form/session cookie,
protected API routes and the existing **scripted** studio. It never substitutes
HTTP responses, calls a model, sends a phone message, or uploads audio.

## Prepare an isolated checkout

Use a separate worktree containing the integrated Channels demo and settings UI.
Do not run this test in a checkout already running Next dev. It checks the Next
lock and refuses to stop someone else's server. The frontend checkout must not
contain `.env`, `.env.local`, `.env.development`, or `.env.development.local`.

Install prerequisites yourself, outside pytest:

```sh
# From that checkout, Node.js 20.9+ and npm available on PATH:
cd web
npm ci
cd ../api
uv sync --locked
uv run playwright install chromium
```

Linux may also need the normal Playwright Chromium OS libraries. Missing Node,
npm, frontend dependencies, or a launchable Chromium is an actionable failure
when the test is enabled; it does not silently skip or install anything. The
owned-process cleanup currently targets macOS and Linux.

```sh
# Default behavior: one clearly explained skip.
uv run pytest tests/channels/test_browser_e2e.py -q

# Actual browser/API run:
STITCH_CHANNELS_BROWSER_TEST=1 uv run pytest tests/channels/test_browser_e2e.py -q
```

The existing Next layout uses `next/font/google`: its first compilation may
fetch static font assets. This is not a hermetic frontend build. Application
data, studio work and browser requests use local synthetic fixtures; provider
credentials are not passed into either child process. No live generation,
Photon, OpenAI transcription, speech accuracy or Atlas behavior is qualified by
this regression.

## What it proves

- The real login screen establishes an authenticated cookie session.
- A signed synthetic PNG and terminal story pass through YEAR, SUBMIT, studio
  follow-up and KEEP into a saved memory with compiled screenplay/timeline.
- The original photo is retained and returned by the protected media endpoint.
- The completion link uses this test's actual preview port and opens the saved
  book in Chromium. The deterministic open spread is captured, with no page or
  console errors.
- The settings page creates a pending contact on the same disposable database,
  saves cadence preferences via PATCH, and stops the contact. A signed cloud
  LINK proof is deliberately rejected: this offline demo cannot activate a real
  messaging account. Mobile overflow and anonymous/cross-family denials are
  checked.

The test injects a file-capturing terminal sender through the public demo factory;
it does **not** exercise the TypeScript bridge. Its protocol, real SDK streaming,
HMAC interoperability and bounded downloads have separate tests under `channels/`.
The API exposes no new test/debug endpoint. Only the existing public demo factory
and routers are composed in a private temporary child harness.

## Isolation and artifacts

The API receives an already reserved loopback socket. Next receives a fresh
unused loopback port, with bounded retries if another listener wins the release
race. Only test-owned process groups are terminated on success or failure; no
port-based or executable-wide kill is used. Startup, HTTP, browser operations and
shutdown have deadlines. The children receive an environment allowlist, a
synthetic process-only HMAC secret and no inherited provider/proxy credentials.
The demo creates its own ephemeral JWT and discards its in-memory database on
shutdown.

Screenshots (`book.png`, `pending-settings.png`, `stopped-settings-mobile.png`)
and child logs are in pytest's private temporary directory. Pending verification
codes are masked in screenshots, and the HMAC secret is never written to a file
or command argument. Retained pytest artifacts contain synthetic data only and
can be inspected after a run. The generated Next cache belongs to this isolated
checkout and is not removed automatically.
