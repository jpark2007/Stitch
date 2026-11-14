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
| `PHOTON_WEBHOOK_SECRET` | Destination's full `whsec_...` signing secret; distinct from API key |
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
queued rows resume after restart, but processing/blocked rows never auto-retry.
Completed event bodies are removed while their hashes remain; blocked rows retain
private payloads for reconciliation. A failed part can leave earlier parts accepted.
Inspect the API receipts before deciding how to recover; never reset the whole batch
blindly, especially a consumed LINK code.

Reply IDs and body hashes are reserved before provider effects. A successful 202
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

Webhook bodies are capped at 64 KiB; request/socket timeouts are 10 seconds. Queues
retain at most 10,000 events and 10,000 replies, with 1,000 verified routes and a
64 MiB SQLite page ceiling. Capacity fails closed rather than evicting replay IDs.
Each event has at most 10 parts and 8,000 total text bytes. REST JSON is bounded to
256 KiB; attachment data to 20 MiB; REST/download deadlines are 15 seconds. A drain
starts no new part after its 60-second deadline. An already started local API call
may take its five-second timeout to settle. Outbound provider work has a shared
four-second deadline across verification/send, with no automatic retry. All
requests use fixed project-scoped HTTPS paths and refuse redirects. Error logs
contain no provider body, message text, code, audio, key or signing secret.

The queue drains one event at a time; signed API ticks run every 60 seconds without
overlap. Shutdown stops listeners/timers and waits for bounded reserved work,
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
