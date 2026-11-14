# Contact preferences and recipient-proven enrollment

This slice exports two **unmounted** routers: `channels.settings.router` and
`channels.enrollment.router`. The API owner must mount them and run the existing
`ensure_channel_indexes(db)` hook before use. No `main.py`, shared schema, studio,
provider or frontend file is changed here. `STITCH_CHANNELS=1` gates both routers.
The companion bridge intercepts `LINK <code>` before ordinary story intake.
Actual deployment still requires router mounting and explicit provider setup;
offline tests do not establish that a live line is configured.

## Cookie-authenticated settings

All routes below use the existing `family_member`/session-cookie authentication.
The family must exist for the caller (404 otherwise). A member can manage only
their linked person; an admin can prepare a relative's contact (403 for an
unauthorized person in the caller's family). Missing people return 404.
Membership and person authorization are checked on every operation.

- `GET /families/{fid}/channels/contacts` → `{contacts:[contact]}`. Admins see
  contacts belonging to current family people; members see only their own.
- `GET /families/{fid}/channels/people/{person_id}/contact` → `{contact:null|contact}`.
- `POST /families/{fid}/channels/people/{person_id}/enrollment` → 201
  `{contact,code,expiresAt,instruction}` with body
  `{provider:"imessage",handle,timezone,cadenceDays,quietHours:{start,end}}`.
- `PATCH /families/{fid}/channels/people/{person_id}/contact` → `{contact}`.
  Body contains at least one of `timezone`, `cadenceDays`, `quietHours`.
- `POST /families/{fid}/channels/people/{person_id}/contact/stop` with `{}` → `{contact}`.

`contact` is exactly `{personId,provider,handle,status,timezone,cadenceDays,quietHours}`.
Legacy contacts may have null preferences and provider `terminal`. Managed
enrollments use provider `imessage` and status `pending`, `active`, or `stopped`.
No internal contact ID, user/family ID, space ID, code hash or verification fields
are returned. Settings responses have `Cache-Control: no-store`.

Input models reject extra fields and coercion. Cadence is an integer 1–30 days;
timezone is a valid IANA zone; quiet hours are distinct `HH:MM` values (24-hour
clock). Overnight intervals, such as 21:00–08:00, are valid. Quiet hours use the
stored timezone. Scheduling/DST behavior remains future scheduler work; saving
preferences alone schedules or sends nothing. Null/empty patches and status,
handle or identity patches are rejected.

Handles are E.164 numbers (`+`, then 8–15 digits, no leading zero) or validated
ASCII email addresses (local part ≤64, whole address ≤254 characters). Email is
canonicalized to lowercase for creation and proof comparison. Whitespace,
display-name addresses, consecutive dots and malformed domain labels are rejected.

## Consent proof

Creation sends **no invitation, verification text, call or model request**. It
returns a 256-bit random URL-safe code once, with an explicit instruction that
the recipient must send `LINK <code>` to verify their handle **and opt in** to
family story messages. The instruction also explains STOP. The requester shares
that instruction manually. This API does not supply a destination phone number;
the operator must use the configured Stable Spectrum line, never an invented one.

Only SHA-256 of the code is stored. It expires in 10 minutes and allows at most
five failed guesses. Creation is capped at five attempts per initiating user per
UTC hour, including conflicts. Limit rows have a TTL; code expiry is enforced in
the atomic query, not by eventual TTL deletion. An unexpired pending request
cannot rotate its code; after expiry the same identity may explicitly request a
new one. An active contact cannot be rebound. A stopped contact may explicitly
request **new consent for the same handle/person/user/family only**: it becomes
pending, loses its old verification, and needs a fresh recipient proof. Old codes
remain invalid. There is no direct admin "activate" or "resume" operation.

The separate signed endpoint is:

`POST /internal/channels/enroll`

```json
{"messageId":"sdk-child-id","provider":"imessage","senderId":"+15555550123","spaceId":"sdk-space-id","spaceKind":"direct","code":"43-character-url-safe-code"}
```

It uses the existing exact-body HMAC, nonce replay guard, timestamp window and
16,384-byte body cap. Query strings and unknown fields are refused. IDs are
nonblank valid Unicode of at most 256 UTF-8 bytes. The recipient's signed sender
handle must match the pending contact after documented email canonicalization.
`spaceKind:"direct"` is mandatory and asserted only by the server-side bridge
after checking actual inbound Stable SDK metadata: `space.__platform` is
`imessage`, `space.type` is `dm`, the message is inbound iMessage, and its resolved
space matches. Group/unknown contexts fail closed. Web preferences cannot assert
this field. It is part of the signed raw body, never an unsigned query hint.
Only successful one-use proof returns `{status:"active"}`. Invalid, expired,
locked-out, stopped, already-used or missing proof returns the same generic 400
without exposing family/person identity. Authentication failures retain their
existing 401/409 behavior. A lost successful response must not be retried as a
new activation; the cookie-authenticated settings read reveals current status.

Redemption rechecks the initiating user's existence, current family membership
and authority over the person, plus the person's original user mapping. It
atomically consumes the hash while activating the pending row and recording
`verifiedAt`, `optInAt`, the signed `spaceId` and `spaceKind:"direct"`. Concurrent consumption succeeds
once. STOP changes the compare-and-set predicate and deletes pending proof.
The proof binds the observed direct destination. Proactive scheduling must require
this evidence; legacy contacts without it are not grandfathered in. New pending
enrollment clears both space fields until fresh proof succeeds.

Restart recovery is deliberately incomplete: the bridge registers only spaces
seen in this process's incoming SDK events. Stable SDK `space.get(id)` merely
reconstructs metadata and does not independently verify the recipient; it cannot
safely restore an arbitrary stored destination by itself. Unknown spaces fail
closed until a new inbound event. No automatic new-chat creation or uncertain
delivery replay is implemented.

Cross-family handle reuse is a generic 409 until explicit family selection is
implemented. Deterministic managed-contact IDs serialize concurrent creation for
one handle; a partial unique family/person index prevents managed duplicates.
Legacy ambiguous contact rows are not migrated or silently chosen. Preferences
do not change the enrollment's identity, and list responses never reveal hashes.

## Checks

From `api/`: `uv run pytest tests/channels/test_settings.py`.

Tests use real login cookies, signed requests, seeded disposable mongomock data
and offline concurrency. They cover family/person access, validation, expiry,
attempt/rate limits, STOP, revoked initiators, remapping, concurrent creation and
one-use proof. No live provider, model or database requests are made.
