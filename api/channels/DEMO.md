# Disposable Channels book demo

This opt-in demo keeps the terminal contact, scripted studio and existing
protected book API on **one in-memory database**. It makes no model, phone,
Atlas or transcription requests, even with inherited service credentials.
Restarting deletes its stories and invalidates its login sessions. It never
imports `main.py` or loads `.env` files. The default command without `--web`
retains the receipt-only server on port 8011; `--worker` remains explicit.

Use a checkout containing this demo and the Channels worker. Leave other agents'
servers and checkouts running. These commands use API 8012, web 3002 and terminal
bridge 3012; occupied ports should be handled by choosing new ports, not killing
existing processes. All servers bind only to loopback.

## API and browser preview

In terminal A, from the repository root:

```sh
cd api
uv sync --locked
# Enter a new random shared secret, at least 32 UTF-8 bytes, at the silent prompt.
# Enter the same value in the bridge/tick terminals; never put it in a command.
read -s CHANNELS_SHARED_SECRET
export CHANNELS_SHARED_SECRET
uv run python -m seed.channels_demo --web --worker --port 8012 --preview-port 3002 --bridge-port 3012
```

In terminal B, create a separate frontend checkout, from the same repository root:

```sh
git worktree add --detach /tmp/stitch-channels-web-preview HEAD
cd /tmp/stitch-channels-web-preview/web
npm ci
NEXT_PUBLIC_API_URL=http://127.0.0.1:8012 NEXT_PUBLIC_API_MOCK=0 npm run dev -- --hostname 127.0.0.1 --port 3002
```

If that worktree path already exists, select a new unused path. The frontend
variable is `NEXT_PUBLIC_API_URL`, **not** `NEXT_PUBLIC_API_BASE`. Shell values
override any Next development environment defaults. No frontend file edits are
needed. Use a separate browser profile/private session for this demo: session
cookies are scoped to the hostname, not its port, so a shared browser profile
could replace another localhost preview's cookie.

Open `http://127.0.0.1:3002/login` and sign in with the existing synthetic demo
account `david@demo.stitch`, password `stitch-demo`. These are public fixture
credentials, not a real account. Open
`http://127.0.0.1:3002/people/dad?family=demo_family&state=open` to see David's book.
Use normal protected pages; `?demo=1` and `/memories/demo` select standalone sample
data and do not demonstrate a newly saved Channels story.

## Feed a scripted story through the terminal

In terminal C, from the repository root:

```sh
cd channels
npm ci
read -s CHANNELS_SHARED_SECRET
export CHANNELS_SHARED_SECRET
export CHANNELS_PROVIDER=terminal
export CHANNELS_API_URL=http://127.0.0.1:8012
export CHANNELS_BRIDGE_PORT=3012
npm start
```

This uses the SDK terminal provider; no Photon credentials are needed. Its first
start may download its terminal UI binary. The contact `terminal-user` is linked
to David (`dad`) in `demo_family` by a disposable-only DB fixture. Unknown
senders and cloud providers fail closed, including the future voice intake path.

Send each input, then wait for the next bridge heartbeat (at most 60 seconds)
before answering the worker's reply. For a quicker demo, use terminal D in
`channels/`, with the same silently entered/exported shared secret:

```sh
CHANNELS_API_URL=http://127.0.0.1:8012 node --input-type=module -e 'import { ApiClient } from "./src/bridge.ts"; const r = await new ApiClient(process.env.CHANNELS_API_URL, process.env.CHANNELS_SHARED_SECRET).post("/internal/channels/tick", {}); console.log("Tick HTTP", r.status);'
```

Use these inputs in order, ticking after each:

1. `We dropped the hymn books on the church steps.`
2. `YEAR 1998`
3. `SUBMIT`
4. When asked who was there: `I was alone.`
5. When asked to approve the studio proposal: `KEEP`

Tick again as needed until the terminal returns a `/memories/<id>?family=demo_family`
link. Open that relative path on `http://127.0.0.1:3002`; David's book now includes
the newly saved memory. Optional photos sent before the text remain attached to
the telling. The scripted studio compiles a real playable payload, but the scene
is a fixed offline rehearsal, not an AI interpretation of the text. Receipt 202
alone is never proof of a saved story. Voice transcription is deliberately disabled.

## Isolation and verification

The API reuses existing auth/family/tree/book/memory/media read routes and account
login/logout/signup routes. Normal session and family checks apply: anonymous
book/media requests return 401; a different family's user gets 404. Studio REST
writes, enrollment and external services are not mounted. CORS permits only
`localhost` and `127.0.0.1` on `--preview-port`. A fresh process-only JWT secret
replaces inherited JWT configuration during the demo; it is never printed.

Run `uv run pytest tests/channels/test_demo.py` from `api/`. Tests exercise
login → signed photo/story → scripted approval → compiled memory/book/media reads
in the same database, anonymous/nonmember denial, consent STOP, environment
restoration and disposal. Browser visual review is a separate check.

Stop only your demo processes with Ctrl+C in their own terminals. The in-memory
database is discarded on API shutdown; the extra worktree can be retained for
another rehearsal. Do not stop or restart an existing port-3000 preview.
