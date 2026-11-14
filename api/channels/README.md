# Channels inbox foundation

This package authenticates text and photo messages and stores durable receipts.
An opt-in manual worker connects them to the conversation/studio and signed replies.
It does not enroll or verify contacts, schedule prompts, or make calls. The
companion terminal bridge can exercise the transport without
Photon credentials. No `.env` is loaded by this package and imports start no work.

## Integration hooks (API owner)

- Mount `channels.routes.router` only behind `STITCH_CHANNELS=1`. The router also
  checks that exact flag on every request, even if accidentally mounted.
- Call `channels.ensure_channel_indexes(db)` during enabled startup, off the event
  loop. It creates contact lookup/uniqueness indexes and message lookup/TTL indexes.
- Add `contacts` and `channelMessages` to family deletion along with the planned
  `conversations` and `notifications`. Message records contain `familyId`; auth
  nonce records contain no family/message content and expire after their validity
  window. Their uniqueness is enforced by Mongo's built-in `_id` index.
- Set `CHANNELS_SHARED_SECRET` to a random secret of at least 32 UTF-8 bytes, shared
  only with the bridge. This router needs Jonah's independent authentication review
  before merging. No main/router/auth/schema hooks are changed in this branch.

## Protocol

`POST /internal/channels/inbound` receives exactly:

```json
{"messageId":"example-1","provider":"terminal","spaceId":"room-1","senderId":"alice","text":"My grandmother taught me to bake."}
```

IDs are nonblank and at most 256 UTF-8 bytes, text is nonblank and at most 8000
UTF-8 bytes, and the raw body is limited to 16384 bytes. Unknown fields and query
parameters are rejected. Provider is `terminal` or `imessage`; accepting the label
does not configure or enable a cloud provider.

Headers: `X-Channels-Timestamp` (epoch seconds), `X-Channels-Nonce` (canonical
lowercase UUID), `X-Channels-Signature` (lowercase hex HMAC-SHA256). Sign the exact
UTF-8 concatenation of method, path, timestamp and nonce separated by newlines,
then one newline and the **raw body bytes**:

```text
POST\n/internal/channels/inbound\n<timestamp>\n<nonce>\n<raw body>
```

Timestamps more than 300 seconds in either direction fail. Comparisons use constant
time. Nonces are durably consumed before parsing; retries need a new nonce and the
same message ID. Duplicate IDs with identical content return `duplicate: true`;
different content with that ID returns 409. Mongo's unique insert prevents concurrent
duplicate receipts. This does not claim exactly-once processing by a future worker.

A successful receipt is HTTP 202 `{"accepted":true,"duplicate":false}`. It means
stored, **not** submitted to the studio. Private content is not echoed in errors.
`POST /internal/channels/tick` accepts exactly `{}` with the same authentication
and returns `{"processed":0,"scheduler":"inactive"}` unless the worker below is enabled.

## Contact boundary

Only trusted, pre-provisioned contacts work in this slice. A contact has `_id`,
`familyId`, `personId`, `handle` matching `senderId`, `channel` matching the provider,
`status: "active"`, and non-null `verifiedAt`/`optInAt`. Its person must exist in that
same family. Do not use production phone numbers as test fixtures. Enrollment,
verification codes and pending opt-in invitations are later work, not bypasses here.

Handle discovery must cross families before a family can be selected; no family
data is returned to the bridge. Unknown handles return 404, ambiguous mappings 409,
and nonconsenting contacts 403. Multi-family selection is not implemented. STOP and
UNSUBSCRIBE are processed before any future agent: they stop all contacts for that
exact provider/handle, with family-scoped updates, and cannot stop other handles.

## Tests

From `api/`, run `uv sync --locked` then `uv run pytest tests/channels -q`.
Tests use mongomock and fake contact records, never provider credentials or live
database/model services. They cover tampering, freshness, replay across app restarts,
concurrent duplicates, content collisions, UTF-8/body bounds, consent, cross-family
mapping, STOP and the explicit inactive tick. Real Mongo durability and production
delivery remain separate integration checks.

## Signed photos and authorization

`POST /internal/channels/authorize` accepts exactly `{ "provider": "terminal",
"senderId": "alice" }` and returns `{ "allowed": true }`. It uses the same HMAC
and contact checks, exposing no internal IDs. Unknown/ambiguous/unconsenting
contacts return 404/409/403. Authorization is a preflight, not a lasting grant.

`POST /internal/channels/inbound-photo` accepts exactly `messageId`, `provider`,
`spaceId`, `senderId`, `attachmentId`, `mimeType`, `dataBase64`, and optional
`caption` (default empty). IDs have the same 256-byte bound; caption allows 8000
UTF-8 bytes. The HMAC covers the complete raw JSON, including metadata and base64;
URLs and multipart are unsupported. `messageId` is the SDK's normalized per-content
child Message.id, not the shared parent GUID for grouped attachments. Reusing a
message ID for different attachment IDs, text, bytes, or metadata returns 409.

Canonical base64 decodes to at most 20 MiB. Only the exact photo path permits a
27,978,412-byte body; other signed paths retain 16,384 bytes. The main application's
body-limit middleware must also allow that exact path (API-owner hook). Pillow
verifies and fully decodes JPEG/PNG/WebP/GIF, capped at 40 million cumulative pixels
and 200 frames. HEIC/HEIF requires the optional real `pillow-heif` decoder; absent
that dependency, requests return 415. Container signatures alone are not accepted.
No decoder dependency is added by this slice.

Contact mapping and consent are checked before decoding, before upload, and before
final receipt persistence. The family and media ID come from the server. Success
returns 202 `{ "accepted": true, "duplicate": false }`, without exposing photo IDs.
A family-scoped receipt in `channelMessages` has `receiptType: "photo"`,
`status: "received"`, `text` containing the caption, and `photoIds` for a trusted
worker to pass to `ConversationService.attach_photos`. It contains no base64.
The worker must refresh consent before consuming any receipt, including after STOP.

A durable reservation grants only one writer permission to call `put_media` for
a deterministic ID. Retries can finalize an already complete file only after
checking family, kind, MIME and content hash. An incomplete or uncertain upload
stays `photo_pending`/`uploading` and returns 503; it is never blindly uploaded again.
An operator must reconcile those records and any orphaned GridFS chunks. A retry
can recover a complete file after receipt persistence fails. This is not an
exactly-once guarantee across Mongo/GridFS APIs; tests use mongomock and real-Mongo
crash qualification remains required.

## Manual worker (off by default)

`STITCH_CHANNELS_WORKER=1` enables the existing signed tick route, returning
`{processed, sent, blocked, scheduler:"manual"}`. Processing runs off the ASGI event
loop; there is no scheduler or long-lived API worker task. Production also requires
explicit `CHANNELS_CONVERSATION_MODE=real`; unset/unknown modes return 503. Scripted
mode is available only through the isolated demo's injected conversation/studio.

`CHANNELS_BRIDGE_URL` defaults to `http://127.0.0.1:3011` and accepts only numeric
loopback HTTP origins. Signed outbound `POST /send` uses the existing exact
`{messageId,provider,spaceId,text}` schema. No redirects, environment proxies, model
destinations or automatic retries are allowed. An exchange has a five-second total
deadline and a 1024-byte acknowledgement limit. `STITCH_WEB_URL` is a validated
HTTP(S) origin (default `http://localhost:3000`); actual studio `done` produces
`<origin>/memories/<encoded-id>?family=<encoded-family>`. Set a reachable origin
before using phone delivery. A model response or run receipt never creates a link.

Each tick selects at most five available contacts and scans at most 100 records per
queue source. A persisted opaque cursor rotates scan windows and source priority,
so locked records cannot permanently starve later work. It contains no story data.
Each contact processes at most ten receipts, ten deduplicated photos and 8000 UTF-8
text bytes from one space. Commands form separate batches. Use a tick after each
input: story words, `YEAR 1998`, then `SUBMIT` when ready. The worker durably saves
the teller's exact new words and parses the year; the model cannot append or submit.
Its bounded interview question can be relayed after the save. Studio follow-ups
accept text, prop review only `KEEP`, and person matching only `YES` or `NO`.
Other prop edits require the web app.

The interview has a 20-second cancellation deadline, 32 persisted model calls and
a recursion bound. The tick stops starting contacts after 25 seconds. This is a
soft batch bound: in-flight bounded model, relay, database and outbound work must
settle. Framework tools already running cannot be forcibly undone. Cancellation
or failure leaves a durable reservation; no uncertain operation is repeated.

Family-scoped `conversations` documents with `kind:"channelWorker"` hold the
per-contact idle→running reservation and outbox. Success releases the reservation;
failure blocks it for reconciliation. Crashes leave running reservations that are
never stolen. Receipts transition received→processing→processed. Intake snapshots
`principal` and `handle`; every batch member must still match both, person and
provider. Legacy receipts missing snapshots require operator review, not silent
migration. Consent/ambiguity is checked before processing and immediately before
each send. STOP revokes consent before best-effort receipts, even on ID collision.

Outbox messages are persisted before pending→sending→sent transitions. A lost
acknowledgement leaves an uncertain send, never automatically retried. Operators
must reconcile provider delivery before recovering it. Studio cursor/pending state
and `relayBuffer` are persisted together; the buffer is acknowledged only after
outbox persistence, preventing silent loss of questions or book links. Database
failures may leave queued but unsent replies; do not blindly clear reservations.

This slice supports one draft/run per contact. Reset, durable production graph
history, recovery tooling, distributed studio restart, enrollment, proactive prompts
and voice remain deferred. Real Mongo crash behavior and live-provider delivery
are not established by the mongomock checks.

## Offline story demo

From `api/`, use the shared-secret setup in `seed.channels_demo --help`, then run
`uv run python -m seed.channels_demo --worker --port 8011`. Start the terminal
bridge separately. Send story words, `YEAR 1998`, `SUBMIT`, ticking after each input.
From `channels/`, a third terminal with the same secret can sign a manual tick:

```sh
node --experimental-strip-types --input-type=module -e 'import {ApiClient} from "./src/bridge.ts"; console.log(await new ApiClient("http://127.0.0.1:8011", process.env.CHANNELS_SHARED_SECRET, {attempts:1,timeoutMs:90000}).post("/internal/channels/tick", {}));'
```

Answer the studio follow-up, tick, send `KEEP`, and tick again. The existing offline
`ScriptedRunManager` compiles and saves the repository's sample-based hymn-book
scene; no art/model/provider service is called. The terminal bridge prints replies.
The standalone database is disposable and separate from the main web app: its
completion URL identifies the saved memory, but opening it in a different app
database will not show that memory. The integration test verifies the actual saved
telling, photo, compiled result and completion event. This is not a shared-web demo.

## Voice-note intake (explicit opt-in)

Signed `POST /internal/channels/inbound-voice` accepts exactly
`{messageId,provider,spaceId,senderId,attachmentId,mimeType,dataBase64}`. There is no
caption, URL, family ID or caller media ID. IDs are capped at 256 UTF-8 bytes;
canonical base64 decodes to at most 20 MiB and the raw body to 27,978,412 bytes.
This allowance applies only to the exact photo and voice paths; all other signed
routes retain their 16,384-byte cap. The API owner's main middleware needs the same
narrow voice-path allowance. This slice does not change main.py.

Voice requires `STITCH_CHANNELS_TRANSCRIPTION=1`, valid server-side `OPENAI_API_KEY`
and the approved optional `CHANNELS_TRANSCRIPTION_MODEL` configuration, or a trusted
injected offline transcriber in tests. Importing/receiving audio never calls a
provider. The independently reviewed [transcription adapter](TRANSCRIPTION.md)
defaults to the user-approved `gpt-4o-mini-transcribe` and sends only when the worker
explicitly invokes it. No secret value is included in responses or logs.

The MIME allowlist is the adapter's `MIME_FILENAMES`: MP3/MPEG/MPGA, MP4/M4A, PCM WAV,
WebM, Ogg and FLAC, including its documented aliases. PCM WAV frames are checked for
complete declared data. Compressed formats receive bounded container/header sanity
checks that reject obvious non-audio and MIME mismatches; these checks do **not**
prove decodability, authenticity or duration. The opted-in provider performs actual
decoding. No local decoder, URL fetch, ffprobe or shell converter is invoked.
**Native iMessage voice notes can arrive as `audio/x-caf`, which is unsupported in
this checkpoint. A separate bounded CAF conversion slice is still required; this
is not a complete native iMessage voice feature.**

202 `{accepted:true,duplicate:boolean}` means audio storage completed, not that a
transcript or story exists. Family-scoped receipts have `receiptType:"voice"`,
server-generated `audioId`, `audioSha256`, `audioMimeType`, identity snapshots,
`status:"received"` and `transcriptionStatus:"pending"`. Audio is stored through
public media APIs as `kind:"audio"`; raw bytes/base64 are never put in the receipt.
Text/photo/voice share message-ID collision protection. The existing single-writer
upload reservation and completed-file recovery apply to voice too; uncertain
partial uploads are never blindly repeated.

The worker processes at most one voice receipt per contact per tick. It holds the
contact/receipt reservation, rechecks identity and consent, atomically reserves
`transcriptionStatus:"transcribing"`, and makes one transcription call under a
60-second total deadline. Consent/mapping is checked before the call, after its
response, and before draft use. A nonblank transcript of at most 8000 UTF-8 bytes
is persisted with `transcriptionStatus:"complete"` before it can enter the draft.
Failure, cancellation or uncertain persistence records `uncertain` (or leaves the
durable transcribing state if the database is unavailable) and blocks the worker.
There are no automatic provider retries. Outbound failure cannot cause another
transcription. Operators must reconcile stored receipt/provider results before
recovering a blocked operation.

Transcribed words can answer an actual studio text follow-up or be appended to a
draft. They cannot authorize SUBMIT, KEEP or YES/NO, and spoken YEAR is not parsed as
a command; type those commands explicitly. The raw recording remains family-scoped
in media. This slice does not add shared `audioId`/telling fields or recording
playback: the studio receives transcript text through its existing interface only.
Those shared contracts and book playback remain Jonah/Ethan work.

The byte and request-time limits do **not** cap audio duration, provider processing
time after cancellation, or billing. Compressed audio may represent much more than
its byte size suggests. There is no proven minute estimate or per-account spending
budget here; contact-frequency/budget policy is deferred. Tests use synthetic PCM
and structural compressed fixtures with injected transcribers, not real recordings,
provider accounts or live decoding. Run `uv run pytest tests/channels/test_voice.py
tests/channels/test_transcription.py -q` from `api/`.
