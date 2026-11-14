# Live transport and browser checks

Run on macOS or Linux with Python 3.12+ and uv, from the repository root:

```sh
cd api
uv sync
uv run playwright install chromium
uv run pytest ../qa -q
```

On Linux, Chromium may also need system libraries (`uv run playwright install --with-deps chromium`).
To run only HTTP transport coverage, use `uv run pytest ../qa/integration -q`;
to run only Chromium coverage, use `uv run pytest ../qa/e2e -q`.
The browser tests fail if Chromium is missing; they are not silently skipped.

These three tests launch the actual `api/main.py` startup in a separate uvicorn process,
using the current Python interpreter. A bound loopback socket is inherited by uvicorn
to avoid a free-port race. Two tiny local pages provide allowed and disallowed browser
origins on different ports. Startup, requests and stream reads have deadlines, and
fixtures stop their processes, browser contexts and HTTP servers even on failure.

The API process receives an allowlisted environment, a generated disposable JWT secret,
`STITCH_FAKE_DB=1`, `STITCH_AGENT=fake`, and `COOKIE_SECURE=0`. It does not load `.env`
or inherit database, model or tracing credentials. No credentials, MongoDB, Next.js
server, paid services or persistent data are needed. All writes use the disposable
seeded in-memory family. Server logs are in pytest's temporary `stitch-live` directory.

Coverage:

- Actual startup, seeded login, multipart submission and an open SSE connection through
  follow-up answer, prop approval and completion, then persisted scenes in both books.
- Chromium cross-origin credential inclusion/omission, HttpOnly cookie visibility and logout.
- Chromium enforcement of a rejected JSON-login CORS preflight from a disallowed origin.

`e2e/` covers the **browser/API boundary**, not full product UI end-to-end behavior.
It does not render Next.js login, tree, book, add-memory or stage pages; those flows,
visual quality, real MongoDB and real model runs remain separate checks. The scripted
agent validates transport and wiring, not generated story quality. This suite supplements
the existing API and web unit suites rather than replacing or duplicating their detailed
validation and isolation cases. Run those separately using the repository instructions.
