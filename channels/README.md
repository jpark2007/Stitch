# Channels terminal bridge

This first slice uses **Stable Spectrum**, pinned as `spectrum-ts` 12.16.0, with
only its credential-free terminal provider. It forwards text to the Channels
inbox and reports an HTTP receipt. A receipt is **not** a generated story.
No phone messages, calls, media, models, conversation agent, scheduler, or
notification settings are implemented here. Do not use it for production.

## Run locally

Requires Node 24+ and npm. From the repository root:

```sh
cd channels
npm ci
npm run typecheck
npm test
```

Set `CHANNELS_SHARED_SECRET` to the same randomly generated value (at least
32 UTF-8 bytes) in both local processes. Keep it out of source control and logs.
This bridge does not load `.env` files automatically. Start the backend with the
Channels router and a verified/opted-in terminal contact first, following
`api/channels/README.md` on the inbox branch. Main does not yet mount that router;
Jonah owns the gated integration. Then run:

```sh
# In this same terminal, with CHANNELS_SHARED_SECRET exported:
npm start
```

`CHANNELS_API_URL` defaults to `http://127.0.0.1:8000` and must be a loopback HTTP
origin without credentials, paths, query, or fragment. The bridge listens only
on `127.0.0.1:3011`; `CHANNELS_BRIDGE_PORT` can select another port. It does not
reuse or stop an existing server; an occupied port fails startup.

The Spectrum terminal provider downloads its `tuichat` binary on first use, then
opens a local chat UI (or a readline interface when stdin is piped). Type a text
message. The terminal reports receipt status or a sanitized failure; request text
and API response bodies are not logged by this bridge. Unknown or ambiguous
contacts require backend enrollment/resolution, not a client-supplied family ID.
Ctrl+C stops the bridge, heartbeat and terminal provider. Inherited
`SPECTRUM_PROJECT_ID`, `SPECTRUM_PROJECT_SECRET`, and `SPECTRUM_WEBHOOK_SECRET` are
removed from this process before Spectrum starts, preventing a cloud project
lookup; this does not modify your shell environment. No provider credentials
are needed or used.

## Internal protocol

All endpoints use `POST` and UTF-8 JSON. Unknown fields are rejected. The body
limit is 16,384 bytes. IDs are nonblank strings of at most 256 UTF-8 bytes; text
is nonblank and at most 8,000 UTF-8 bytes. Unpaired Unicode surrogates are invalid.
Blank detection uses the union of Python/JavaScript whitespace: U+0009–000D,
U+001C–0020, U+0085, U+00A0, U+1680, U+2000–200A, U+2028–2029, U+202F, U+205F,
U+3000, U+FEFF. Strings are not normalized or trimmed in transit.

- `/internal/channels/inbound`: `{messageId, provider, spaceId, senderId, text}`.
- `/internal/channels/tick`: `{}`, sent every 60 seconds without overlapping
  requests. This slice's backend reports readiness, not scheduled prompts.
- Bridge `/send`: `{messageId, provider, spaceId, text}`. Only `provider: "terminal"`
  and a space observed from this process's inbound stream can receive a reply.
  `imessage` is reserved in the shared envelope, never enabled by this executable.

Headers are `X-Channels-Timestamp` (epoch seconds), `X-Channels-Nonce` (canonical
lowercase UUID), and `X-Channels-Signature` (lowercase hex HMAC-SHA256). Sign the
exact bytes `METHOD + "\n" + PATH + "\n" + timestamp + "\n" + nonce + "\n" + rawBody`
using `CHANNELS_SHARED_SECRET`. Verification is constant-time and accepts clocks
within 300 seconds. Query strings are not supported. API calls use a five-second
timeout, at most three attempts, and no redirects. Retries retain the message ID
but get a new nonce. Permanent HTTP errors are not retried.

The bridge's replay cache and outbound delivery records are **process-local**.
Concurrent/repeated `/send` calls with the same ID send once, and a conflicting
body gets 409. Failed or timed-out sends remain uncertain and are not sent again
in this process. Stop rather than automatically retry an uncertain delivery.
After restart those records are lost: do not replay outbound requests across
restarts. Use a new shared secret for each local rehearsal. Production requires
a durable outbox/replay strategy before cloud delivery is enabled. Capacities
fail closed rather than evicting live records. The inbox owns durable inbound
deduplication independently of these local transport caches.

Official references: [Spectrum getting started](https://photon.codes/docs/spectrum-ts/getting-started),
[terminal provider](https://photon.codes/docs/spectrum-ts/providers/terminal/setup-and-usage).
Do not mix these with Photon Beta 2.0 APIs or credentials.
