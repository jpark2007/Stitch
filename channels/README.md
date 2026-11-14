# Channels bridge: Photon Beta and offline terminal

Cloud iMessage uses **Photon 2.0 Beta REST and signed webhooks**, explicitly enabled
with the configuration in [BETA.md](BETA.md). Beta supports text and images;
**Beta iMessage voice notes are not supported**. Stable cloud startup is disabled.
The default credential-free terminal still uses `spectrum-ts` 12.16.0 solely for
its local UI. Existing voice adapters/tests remain available for offline testing.
A receipt is **not** a generated story or proof of delivery to a person. API hooks
and live phone qualification remain separate requirements; module availability
does not establish end-to-end readiness.
This remains a local rehearsal tool, not a production service.

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
This bridge does not load `.env` files automatically. Start the disposable backend
from `api/` with `uv run python -m seed.channels_demo --worker --port 8011`, using
the same exported secret. It provisions a trusted terminal contact and an offline
scripted studio. Set `CHANNELS_API_URL=http://127.0.0.1:8011` in the bridge terminal.
For the shared-database browser preview, follow [the demo guide](../api/channels/DEMO.md).
Main does not yet mount the Channels routers; Jonah owns the gated production
integration described in [the API checklist](../api/channels/README.md#integration-hooks-api-owner).
Then run:

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

Follow [the Beta setup and recovery contract](BETA.md). Required cloud settings are
`CHANNELS_PROVIDER=imessage`, `CHANNELS_PHOTON_VERSION=beta`, `PHOTON_PROJECT_ID`,
`PHOTON_API_KEY`, `PHOTON_WEBHOOK_SECRET`, and the assigned `PHOTON_AGENT_HANDLE`.
Stable credentials do not activate this path. No cloud messages or calls are sent
by automated tests; live Photon qualification remains required.

The backend must have the Channels router, active verified contacts, and the
photo route available. Jonah owns main's gated router mount and a body-limit
exception of 27,978,412 bytes for exactly `/internal/channels/inbound-photo` when
`STITCH_CHANNELS=1`. Until that hook lands, main may reject photo requests at its
existing JSON limit. All other API request limits must remain unchanged. The
bridge does not edit API mounting, shared contracts, or the studio save path.

Voice intake is implemented at `/internal/channels/inbound-voice` in
`api/channels/routes.py`, with validation/storage in `api/channels/voice.py` and
consent-checked processing in `api/channels/worker.py`. Jonah's production mount
and exact 27,978,412-byte middleware allowance are still required. This bridge
does not install those hooks. Intake validates audio, checks consent and reserves
durable processing before the worker invokes transcription; uncertain operations
are held for reconciliation instead of blindly retried.
No OpenAI credential belongs in the bridge. The server-side transcription adapter
and its explicit opt-in are documented in [TRANSCRIPTION.md](../api/channels/TRANSCRIPTION.md).
The terminal demo disables transcription; it is not a live voice rehearsal.

Photo authorization happens before download and is checked again by the
backend before storage. Unknown, ambiguous, or nonconsenting contacts do not
cause a download. JPEG, PNG, WEBP, GIF, HEIC and HEIF are allowed; the API must
verify actual bytes rather than trusting the provider's MIME declaration.
Downloads stop at 20 MiB or 15 seconds. Beta downloads use only fixed project-scoped
attachment endpoints, never a supplied URL. The following SDK stream behavior
applies to the offline terminal/legacy adapter tests, not the Beta cloud path:
The SDK's stream-opening method has no abort parameter; a late stream is canceled
when it resolves. Empty, oversized and unsupported attachments fail explicitly.
Combined text/photo and multi-photo messages retain each SDK child's own message
ID and order; photos keep their distinct attachment IDs. Text arrives separately
from the photo, so the worker owns combining it into a story draft.

Offline/legacy voice content uses the SDK `Voice.stream()` API, never `read()`, a filename,
or a user-supplied URL. Sender preauthorization happens before opening the stream.
Allowed MIME types match the transcription adapter: `audio/mpeg`, `audio/mp3`,
`audio/mpga`, `audio/mp4`, `audio/m4a`, `audio/x-m4a`, `audio/wav`, `audio/x-wav`,
`audio/webm`, `audio/ogg`, `audio/flac`, `audio/x-flac`, plus native iMessage
`audio/x-caf`. CAF is preserved unchanged for the backend's optional bounded
afconvert/ffmpeg conversion to verified WAV before transcription. Deployments
without a converter block CAF before a provider request. Other unsupported
containers are rejected; this bridge performs no conversion and does not trust
MIME as proof of file contents. Voice downloads share the 20 MiB limit and one
absolute 15-second deadline covering stream opening and all reads; stalled or
oversized streams cancel, including streams that open after the timeout. Grouped
text/voice messages retain child IDs and order. A missing optional SDK voice ID
uses the exact child message ID as its deterministic attachment ID. Redelivery
keeps these identifiers unchanged. Receipts are not transcripts or saved stories.

## Internal protocol

### Recipient LINK commands

Before ordinary text intake, the bridge intercepts `LINK <code>` (case-insensitive
command, exactly one 43-character URL-safe code). All LINK-prefixed malformed
commands are rejected with a generic error; they never enter the story inbox and
the bridge never logs their code. Grouped SDK children pass through this dispatch
individually, retaining child message IDs.

Beta enrollment requires a verified `message.received` webhook from the configured
project and agent, a direct iMessage space, distinct human/agent participants, and
fresh project-scoped space/user reads. Mixed grouped LINK content is rejected
before any part is forwarded. Unknown metadata, groups and outbound events fail
closed. The terminal cannot enroll an iMessage contact.

The signed request is `POST /internal/channels/enroll` with
`{messageId,provider:"imessage",senderId,spaceId,spaceKind:"direct",code}` under
the normal 16,384-byte limit. The backend must mount the separate enrollment
router. It atomically consumes pending proof and stores the observed direct
destination. There is exactly one HTTP attempt for enrollment; after a lost
response, inspect the authenticated settings page instead of automatically
replaying a one-use code. No verification message is sent by creating a pending
enrollment, and terminal-only rehearsals cannot activate iMessage contacts.

Beta persists verified route proofs and rechecks actual space/user resources
before replies after restart. Unknown destinations fail closed. Queued events
resume; uncertain processing/sends do not. Terminal routing remains process-local.

### Signed requests

All endpoints use `POST` and UTF-8 JSON. Unknown fields are rejected. The body
limit is 16,384 bytes except for the exact photo and voice endpoints below. IDs are nonblank strings of at most 256 UTF-8 bytes; text
is nonblank and at most 8,000 UTF-8 bytes. Unpaired Unicode surrogates are invalid.
Blank detection uses the union of Python/JavaScript whitespace: U+0009–000D,
U+001C–0020, U+0085, U+00A0, U+1680, U+2000–200A, U+2028–2029, U+202F, U+205F,
U+3000, U+FEFF. Strings are not normalized or trimmed in transit.

- `/internal/channels/inbound`: `{messageId, provider, spaceId, senderId, text}`.
- `/internal/channels/authorize`: `{provider, senderId}`; must return `{allowed:true}`
  before any photo or voice bytes are downloaded. It does not reveal family or person IDs.
- `/internal/channels/inbound-photo`: `{messageId, provider, spaceId, senderId,
  attachmentId, mimeType, dataBase64, caption?}`. Caption defaults to empty and is
  at most 8,000 UTF-8 bytes. Strict base64 decodes to at most 20,971,520 bytes;
  raw JSON is at most 27,978,412 bytes. The HMAC covers this entire raw body.
  The API's `202 {accepted:true, duplicate:boolean}` acknowledges the photo receipt,
  not a completed story. Photo IDs for the story are assigned by the backend.
- `/internal/channels/inbound-voice`: `{messageId, provider, spaceId, senderId,
  attachmentId, mimeType, dataBase64}`. No caption or URL field is accepted.
  Strict base64 decodes to at most 20,971,520 bytes and raw JSON is at most
  27,978,412 bytes. Same raw-body HMAC and stable retry identity as photos.
  The API modules implement storage and worker processing; production mounting
  remains pending. A receipt alone never confirms a transcript or saved story.
- `/internal/channels/tick`: `{}`, sent every 60 seconds without overlapping
  requests. The API returns an inactive result unless `STITCH_CHANNELS_WORKER=1`.
  Enabled ticks process receipts and replies; production additionally requires
  `CHANNELS_CONVERSATION_MODE=real`. Proactive prompts separately require
  `STITCH_CHANNELS_SCHEDULER=1`; see [worker and scheduler details](../api/channels/README.md).
- Bridge `/send`: `{messageId, provider, spaceId, text}`. Only the explicitly
  configured provider and a space proven by an authenticated inbound event
  can receive a reply. Spaces and delivery IDs are scoped by provider.

Headers are `X-Channels-Timestamp` (epoch seconds), `X-Channels-Nonce` (canonical
lowercase UUID), and `X-Channels-Signature` (lowercase hex HMAC-SHA256). Sign the
exact bytes `METHOD + "\n" + PATH + "\n" + timestamp + "\n" + nonce + "\n" + rawBody`
using `CHANNELS_SHARED_SECRET`. Verification is constant-time and accepts clocks
within 300 seconds. Query strings are not supported. API calls use a five-second
timeout, at most three attempts for terminal (one for all Beta API forwards), and no redirects. Retries retain the message ID
but get a new nonce. Permanent HTTP errors are not retried.

The terminal bridge's replay cache and outbound delivery records are **process-local**.
Concurrent/repeated `/send` calls with the same ID send once, and a conflicting
body gets 409. Failed or timed-out sends remain uncertain and are not sent again
in this process. Stop rather than automatically retry an uncertain delivery.
After restart those records are lost: do not replay outbound requests across
restarts. Use a new shared secret for each local rehearsal. Production requires
a durable outbox/replay strategy before unattended cloud delivery. Capacities
fail closed rather than evicting live records. The inbox owns durable inbound
deduplication independently of these local transport caches.

Beta uses a durable event/reply ledger; see [BETA.md](BETA.md) for uncertainty,
capacity and recovery limits. Its `/send` HMAC nonce cache is process-local, but
the durable reply ID prevents repeating a send across restart. Official references:
[Beta API](https://photon.codes/docs/beta/api-reference),
[Beta webhooks](https://photon.codes/docs/beta/message-delivery/webhook), and the
offline [terminal provider](https://photon.codes/docs/spectrum-ts/providers/terminal/setup-and-usage).
