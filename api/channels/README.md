# Channels text inbox foundation

This slice authenticates text messages and stores durable receipts. It does **not**
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
