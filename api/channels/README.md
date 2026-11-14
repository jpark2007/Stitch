# Channels inbox foundation

This slice authenticates text and photo messages and stores durable receipts. It does **not**
generate stories, enroll or verify contacts, send messages, schedule prompts, or
make calls. The companion terminal bridge can exercise the transport without
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
and returns `{"processed":0,"scheduler":"inactive"}` until scheduling is implemented.

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
crash qualification remains required. No worker or outbound delivery is wired here.
