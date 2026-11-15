# Photon 2.0 Beta transport

This is the explicitly selected Beta integration. It uses REST at
`https://api.photon.codes/v1`, not Stable Spectrum cloud APIs. Only plain text and
images are enabled. Beta documentation currently excludes iMessage voice notes;
the existing backend transcription/CAF modules do not change that limitation.
No real phone, provider or model calls are part of automated tests.

## Configure a supervised run

Use Node 24+ on a POSIX host. Run `npm ci`, `npm run typecheck`, and `npm test`
from `channels/`. Export these values privately; the bridge loads no env file:

| Variable | Meaning |
| --- | --- |
| `CHANNELS_PROVIDER=imessage` | Select cloud instead of default terminal |
| `CHANNELS_PHOTON_VERSION=beta` | Required explicit version; Stable cloud disabled |
| `PHOTON_PROJECT_ID` | Beta `pho_prj_...` project |
| `PHOTON_API_KEY` | Project `pho_sk_...` bearer key with `project:read` and `project:write` |
| `PHOTON_WEBHOOK_SECRET` | Destination's full `whsec_...` signing secret, canonical base64 encoding at least 24 bytes; distinct from API key |
| `PHOTON_AGENT_HANDLE` | Exact assigned agent iMessage number/email, not the human sender |
| `CHANNELS_SHARED_SECRET` | Existing internal API/bridge HMAC secret |
| `CHANNELS_API_URL` | Loopback API origin; default `http://127.0.0.1:8000` |
| `CHANNELS_BRIDGE_PORT` | Signed local `/send` listener; default `3011` |
| `PHOTON_WEBHOOK_PORT` | Separate loopback webhook listener; default `3012` |
| `PHOTON_STATE_PATH` | Absolute private SQLite path; default `~/.local/state/stitch-photon-beta/bridge.sqlite` |

Create/assign the line in the Beta dashboard. A shared line requires the configured
human sender to initiate the conversation. This code does not provision a line,
spend plan capacity, or create a webhook destination. Such provisioning may need
additional permissions; they are not runtime requirements.

Configure a public HTTPS tunnel/reverse proxy forwarding **only**
`POST /photon/webhook` to `127.0.0.1:3012`. Do not expose `/send`. A tunnel is a
separate prerequisite; starting this program does not create one. In the project
dashboard add a destination with API version **2026-07-01** and only the
`imessage.photon` / `message.received` event. Save its signing secret privately.
Start with `npm start`. Occupied ports fail startup without touching other servers.
No live configuration is considered verified until an actual incoming phone
message is accepted and its reply is independently observed.

Use the web settings enrollment flow, then send standalone `LINK <code>` from
the intended human in that direct iMessage conversation. The bridge forwards the
existing signed enrollment schema unchanged. Voice, group conversations, unknown
content, mixed LINK+attachment batches and malformed LINK commands are rejected.
For text/images, grouped parts are forwarded in order using stable
`<Photon message ID>:part:<zero-based index>` IDs; attachment IDs remain Photon's.
Text/caption parts enter as text once, with empty photo captions, so they are not
duplicated. No caller-supplied download URL is used.

## Verification and durability

The recommended `standardwebhooks` library verifies original bytes using the full
secret and the `webhook-id`, `webhook-timestamp`, and `webhook-signature` headers.
Its default five-minute timestamp window and signature rotation support remain
enabled. The header ID must equal the event ID. The bridge pins event source/type,
API version, project at every used nested identity, received status, human sender,
configured agent receiver, and exactly those two participants in a direct space.
It reads current project-scoped space and user resources before processing/sending;
it never infers privacy from an ID. Photo preauthorization occurs before download,
with backend consent checked again during storage.

SQLite stores the verified event and queued work together before returning 204.
Duplicate IDs return 204 only for identical original content; altered payloads
return 409. Persistence failures return 503. Invalid signatures return 400 and no
work is queued. Only the event types above are accepted; subscribe narrowly so
unsupported events do not become permanent rejected deliveries.

One bridge process holds an exclusive database lock. State directory/file must
be owned by the process user with no group/world permissions. SQLite uses full
synchronous writes and a rollback journal. Events are reserved before API effects;
queued rows resume after restart, but crashed processing/blocked rows never auto-retry.
Transient verification, read-only preflight and idempotent text-inbox failures retry
at most eight total attempts, with durable 5/15/30/60/120/240/480-second backoff
(15 minutes 50 seconds of waiting, plus request time). Successful parts
are checkpointed before proceeding, preserving their original message IDs. A lost
LINK or photo-storage acknowledgement is uncertain and never automatically retried.
Exhausted retries remain blocked and produce an operator warning containing only
the validated event ID and an advisory `isSTOP` flag; **STOP is
not guaranteed delivered**. Inspect the ledger/API receipts and apply an unresolved
STOP through the authenticated settings page. Do not reset the batch blindly.

Completed event bodies are removed immediately; their hashes remain for seven days
after completion (the four-day provider retry horizon plus a safety margin). After
that local deduplication horizon, completed IDs may be accepted again. A local
Channels API 403 consent refusal or `/enroll` 400 rejected proof removes the body
immediately and invalidates any cached reply route. An invalid LINK therefore does
not occupy active blocked capacity indefinitely. Photon REST errors never qualify
as local consent refusals: Photon 403 keeps the private event blocked for operator
repair; safe pre-effect Photon 404 requests retry. Local API 404 also retains its
payload and retries safe idempotent inbound requests: the bridge cannot distinguish
an unknown contact from a disabled/unmounted router using its status-only error
contract. This intentionally prefers bounded retention over silently losing STOP.
Other unfinished payloads expire seven days after receipt; their identity and blocked
state remain so uncertainty cannot be erased into a replay. Cached routes expire
after 30 days without a successfully forwarded inbound event. Route-cache capacity
or persistence failure never prevents an already-forwarded STOP/LINK from completing;
replies to an uncached space fail closed until a later event can establish its route.

Reply IDs and body hashes are reserved after current verification/consent checks,
immediately before provider effects. A pre-send refusal leaves no sending reservation.
A successful 202
must have the expected project, sender and direct space before being recorded.
The provider receives a stable `Idempotency-Key`. A lost ACK, crash, timeout, or
failed persistence leaves `sending` uncertain; repeated/restarted attempts are
blocked. Provider acceptance is not recipient delivery or story completion.
Known sent replies return success without another provider request.

No automated recovery/compaction UI is included. Stop the process before operator
inspection; treat the ledger as private family data and do not paste its bodies
into logs/issues. Reconcile uncertain sends against Photon delivery activity and
API receipts. Do not delete the database to bypass uncertainty. Retention/deletion
must be coordinated with family deletion; this separate local spool is not removed
by the API's family purge hook.

## Bounds and remaining qualification

Webhook bodies are capped at 64 KiB, with an absolute ten-second upload deadline,
five-second header timeout, one-second connection checks and at most 64 simultaneous
connections. Queues retain at most 10,000 active events (queued/processing, plus
blocked events with a nonempty body), 50,000 total event identities and 16 MiB of
retained event bodies. Empty blocked tombstones and completed hashes do not consume
active capacity, but remain within the separate identity bound. Duplicates and
content conflicts are checked before capacity: a full ledger still deduplicates
known identical deliveries and rejects changed content without new storage.

Signed opt-out-shaped events have reserved headroom: ordinary events stop at 9,900
active events, 49,000 total identities or 15 MiB of bodies. The remaining 100 active
slots, 1,000 identities and 1 MiB are reserved for normalized STOP/STOPALL/UNSUBSCRIBE
prefixes, and eligible opt-outs drain before ordinary work. This is priority only;
the existing API decides consent semantics and still checks identity. No unexpired
completed hash or uncertain identity is evicted to admit new work. Reserves are
finite and can also fill; callers then receive 503 rather than a false ACK. Monitor
and reconcile before saturation; this is not an unlimited anti-abuse guarantee.

The separate limits of 10,000 reply identities, 1,000 verified routes and a 64 MiB
SQLite page ceiling remain. Pruning runs during queue activity, including idle
drain checks. Uncertain event/reply identities are never pruned. Already oversized
legacy ledgers may require operator maintenance. Disk exhaustion still fails closed;
operators must reconcile rather than delete the ledger or shorten replay protection.
Each event has at most 10 parts and 8,000 total text bytes. REST JSON is bounded to
256 KiB; attachment data to 20 MiB; REST/download deadlines are 15 seconds. A drain
starts no new part after its 60-second deadline. An already started local API call
may take its five-second timeout to settle. Outbound provider work has a shared
four-second deadline across verification/send, with no automatic retry. All
requests use fixed project-scoped HTTPS paths and refuse redirects. Error logs
contain no provider body, message text, code, audio, key or signing secret.

The queue drains one event at a time; signed API ticks run every 60 seconds without
overlap using a separate 45-second, one-attempt client. Shutdown stops listeners/timers and waits for bounded reserved work,
preserving uncertainty. Unit tests exercise fake REST and real loopback signed
webhook/API boundaries, restarts, concurrency, consent refusal and malformed
identities. Real Photon delivery, filesystem crash durability, network failures
and production retention still require qualification. The local ledger is a
single-host supervised deployment, not a distributed queue.

Official sources (Beta only for cloud):

- [API/authentication and current feature limits](https://photon.codes/docs/beta/api-reference)
- [iMessage setup and sender/receiver mapping](https://photon.codes/docs/beta/get-started/imessage)
- [Standard Webhooks delivery contract](https://photon.codes/docs/beta/message-delivery/webhook)
- [Published OpenAPI schemas](https://api.photon.codes/openapi.json)
