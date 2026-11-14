# Channels text and photo bridge

This bridge uses **Stable Spectrum**, pinned as `spectrum-ts` 12.16.0. Its default
provider is the credential-free terminal. It forwards text and photo attachments
to the Channels inbox and reports HTTP receipts. A receipt is **not** a generated
story or proof that the conversation worker completed. Cloud iMessage mode is
explicitly enabled as described below. Calls, voice notes and models are not part
of this bridge. This remains a local rehearsal tool, not a production service.

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
are needed or used in terminal mode. Add a photo using the terminal provider's
attachment interface; only the SDK attachment stream is read, never a user URL.

## Opt-in cloud iMessage mode

For a supervised rehearsal with consenting contacts, set `CHANNELS_PROVIDER=imessage`
and supply `SPECTRUM_PROJECT_ID` and `SPECTRUM_PROJECT_SECRET` in the process
environment, alongside the shared internal secret. Then use the same `npm start`
command. This mode connects a **real** Stable Spectrum cloud line and can send
replies through `/send`; it is never selected merely because credentials exist.
Projects with Beta IDs beginning `pho_prj_` are rejected. No cloud messages or
calls are sent by the automated tests. This implementation has not been verified
against a live Photon line.

The backend must have the Channels router, active verified contacts, and the
photo route available. Jonah owns main's gated router mount and a body-limit
exception of 27,978,412 bytes for exactly `/internal/channels/inbound-photo` when
`STITCH_CHANNELS=1`. Until that hook lands, main may reject photo requests at its
existing JSON limit. All other API request limits must remain unchanged. The
bridge does not edit API mounting, shared contracts, or the studio save path.

Photo authorization happens before download and is checked again by the
backend before storage. Unknown, ambiguous, or nonconsenting contacts do not
cause a download. JPEG, PNG, WEBP, GIF, HEIC and HEIF are allowed; the API must
verify actual bytes rather than trusting the provider's MIME declaration.
Downloads stop at 20 MiB or 15 seconds and cancel the SDK reader on failure.
The SDK's stream-opening method has no abort parameter; a late stream is canceled
when it resolves. Empty, oversized and unsupported attachments fail explicitly.
Combined text/photo and multi-photo messages retain each SDK child's own message
ID and order; photos keep their distinct attachment IDs. Text arrives separately
from the photo, so the worker owns combining it into a story draft.

## Internal protocol

All endpoints use `POST` and UTF-8 JSON. Unknown fields are rejected. The body
limit is 16,384 bytes except for the exact photo endpoint below. IDs are nonblank strings of at most 256 UTF-8 bytes; text
is nonblank and at most 8,000 UTF-8 bytes. Unpaired Unicode surrogates are invalid.
Blank detection uses the union of Python/JavaScript whitespace: U+0009–000D,
U+001C–0020, U+0085, U+00A0, U+1680, U+2000–200A, U+2028–2029, U+202F, U+205F,
U+3000, U+FEFF. Strings are not normalized or trimmed in transit.

- `/internal/channels/inbound`: `{messageId, provider, spaceId, senderId, text}`.
- `/internal/channels/authorize`: `{provider, senderId}`; must return `{allowed:true}`
  before any photo bytes are downloaded. It does not reveal family or person IDs.
- `/internal/channels/inbound-photo`: `{messageId, provider, spaceId, senderId,
  attachmentId, mimeType, dataBase64, caption?}`. Caption defaults to empty and is
  at most 8,000 UTF-8 bytes. Strict base64 decodes to at most 20,971,520 bytes;
  raw JSON is at most 27,978,412 bytes. The HMAC covers this entire raw body.
  The API's `202 {accepted:true, duplicate:boolean}` acknowledges the photo receipt,
  not a completed story. Photo IDs for the story are assigned by the backend.
- `/internal/channels/tick`: `{}`, sent every 60 seconds without overlapping
  requests. This slice's backend reports readiness, not scheduled prompts.
- Bridge `/send`: `{messageId, provider, spaceId, text}`. Only the explicitly
  configured provider and a space observed from this process's inbound stream
  can receive a reply. Spaces and delivery IDs are scoped by provider.

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
a durable outbox/replay strategy before unattended cloud delivery. Capacities
fail closed rather than evicting live records. The inbox owns durable inbound
deduplication independently of these local transport caches.

Official references: [Spectrum getting started](https://photon.codes/docs/spectrum-ts/getting-started),
[terminal provider](https://photon.codes/docs/spectrum-ts/providers/terminal/setup-and-usage).
[iMessage provider](https://photon.codes/docs/spectrum-ts/providers/imessage).
Do not mix these with Photon Beta 2.0 APIs or credentials.
