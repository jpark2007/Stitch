# Phone interview review drafts

This receiver accepts signed ElevenLabs `post_call_transcription` events. It does
not place calls, synthesize speech, run Studio, execute spoken commands, or publish
a memory. Caller ID and provider-supplied family metadata are never identity proof.
No iMessage enrollment is needed. The signed-in user's current family membership
and their linked `person.userId` determine whose private draft receives the words.
An admin cannot obtain a code for another person.

## Configuration and setup

Set `STITCH_CHANNELS=1`, `STITCH_CHANNELS_ELEVENLABS=1`,
`ELEVENLABS_WEBHOOK_SECRET` (at least 32 UTF-8 bytes, no surrounding whitespace),
and `ELEVENLABS_AGENT_IDS` (comma-separated allowed agent IDs). Optional
`STITCH_CHANNELS_CALL_NUMBER` is an E.164 phone number displayed with pairing;
absent means `null`. The receiver needs no ElevenLabs API key. Flags off return
404; incomplete configuration returns 503. Never commit credentials or log codes,
webhook bodies, transcripts, or phone numbers.

Configure a post-call transcription webhook in ElevenLabs pointing only to
`POST /internal/channels/elevenlabs/post-call`. Use the webhook's signing secret,
not an API key. See `channels/CALLS.md` for the separately scoped exact-path proxy;
do not expose the whole local API through a tunnel. Enable provider retries and
monitor delivery failures. A 200 acknowledges a durable review draft or its exact
duplicate, not a published book. Signature/configuration failures need operator
correction; 4xx deliveries generally are not retried by ElevenLabs.

Minimal agent prompt adjustment for a **fictional test call**:

> Ask the caller to say STITCH followed by all twelve pairing digits in a single,
> standalone turn before their story. Wait until they finish; do not interrupt
> digits. Then invite a short fictional family story in their preferred English
> or Chinese. Explain that a successfully paired call appears as a private draft
> for the signed-in person to review. Never say the memory or book has been saved.

Keep the existing fictional-test restriction until an actual signed test call is
verified. ASR must yield `STITCH 1234-5678-9012` (digits may have spaces/hyphens).
Ordinary surrounding punctuation and a comma/colon after STITCH are accepted in
a standalone marker of at most 96 characters. Spelled-out numbers, missing digits,
extra narrative around the marker, redacted
turns, or multiple markers are rejected; identity is never guessed from speech.
Repeating the numeric code inside another sentence rejects the transcript so the
secret is not retained. The pairing turn is excluded from the stored story.

## Cookie-authenticated interface

All paths below start `/families/{fid}/channels` and require `family_member`.

- `POST /people/{person_id}/call-pairing {}` → 201
  `{code, expiresAt, personId, instruction, phoneNumber}`. Twelve random decimal
  digits displayed 4-4-4, valid one hour; only a hash is stored. An explicit new
  request rotates the previous unused code. Only the user's own linked person
  is eligible; an in-progress attachment returns 409 rather than replacing proof.
- `GET /call-drafts` → `{drafts:[{id,personId,createdAt,text}]}`, newest 50 own drafts.
- `GET /call-drafts/{id}` → `{draft:{id,personId,createdAt,text}}`.
- `DELETE /call-drafts/{id}` → 204; missing/inaccessible → 404. This removes text
  and preserves a hash-only receipt so webhook retries cannot recreate it.

Review and edit locally, then use the existing Add a memory submission flow.
The original source draft is immutable. Do not put transcript text or pairing
codes in URLs. There is no automatic Studio submission or consent change:
spoken SUBMIT, STOP, and KEEP remain ordinary transcript text.

Pair issuance has an atomic family limit of 10 attempts/minute; attachment has
20 attempts/family/minute and 5 attempts/provider-call/minute. These are bounded
burst controls, not daily quotas. A fixed-window boundary can allow two adjacent
bursts. Responses are 429 with `Retry-After: 60`; counters expire after two minutes.
Exact completed retries do not consume another attempt.

## Verification and storage boundaries

Signature format follows the [official SDK verifier](https://raw.githubusercontent.com/elevenlabs/elevenlabs-python/main/src/elevenlabs/webhooks_custom.py):
`ElevenLabs-Signature: t=<epoch>,v0=<hex>`, HMAC-SHA256 over timestamp, a period,
and the exact raw bytes. This receiver additionally rejects future timestamps,
duplicate/oversized headers and uses constant-time comparison. Maximum absolute
clock skew is 30 minutes; body limit 256 KiB and streaming deadline two seconds.
Signature is checked before JSON parsing. Only allowed agents with a completed
post-call transcript and exactly one user pairing turn can bind a draft.

Only user turns are retained, joined by newlines, capped at 20,000 characters;
Chinese is unchanged. Assistant/tool turns, analysis, caller metadata and the raw
payload are not retained. `channelMessages` uses distinct `call-pairing`,
`call-draft`, and expiring `call-rate` kinds, never worker `inbound` records.
Only pairing tickets carry `tokenHash`, with a unique sparse index; draft receipts
carry `proofHash` instead. A unique agent/conversation
hash reserves a draft before an atomic single-use token claim. A retry can resume
an interrupted binding only for the exact original body hash. Changed content
returns 409. Existing exact retries may return 200 after token expiry, but a new
call cannot consume an expired/used token. Deleted drafts never reappear.

Membership, user existence, linked-person identity and membership `joinedAt` are
rechecked during binding, on replay and on every read. Leaving and rejoining
cannot revive an old code or draft. Historical person reassignment away and back
between checks is not independently versioned by the existing person model.
Family-bound records remain in the existing family-purged collection. Global
counter records for unknown calls cannot yet carry a family: they contain only a
call hash, count and expiry, and disappear by their two-minute TTL. Deletion leaves receipt
identity/proof hashes but no transcript; removing the family purges those too.

## Local evidence and first live check

Run `cd api && uv run pytest tests/channels/test_elevenlabs.py`. Tests use signed
synthetic JSON, cookie login and an isolated database; they do not establish ASR
accuracy, phone connectivity, production Mongo failover or live webhook delivery.

For the first live test, sign in as the person who will review the fictional
story, mint their code, and call the configured agent. Read its standalone marker,
then a short fictional Chinese or English story. End the call and verify the
provider delivery status and that only that user's draft appears. Check exact
Chinese text and edit it before choosing Add a memory. If pairing fails, obtain a
fresh code and retry the call; do not manually infer identity or reuse another
person's code. No live test is claimed by this implementation.

Provider references: [post-call payloads](https://elevenlabs.io/docs/eleven-agents/workflows/post-call-webhooks),
[webhook delivery behavior](https://elevenlabs.io/docs/eleven-api/resources/webhooks).
