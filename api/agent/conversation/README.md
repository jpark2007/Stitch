# Contact-bound conversation core

This layer collects a text story and year, starts the existing studio, and relays
its follow-up/prop/merge pauses. The opt-in manual `channels.worker` connects this
service to inbox receipts and signed replies. This package itself sends no message,
makes no call, enrolls no contact, and starts no API background task. A receipt does
not generate a story while the worker is disabled.

## Worker interface

Construct `ConversationService.for_contact(db, family_id, contact_id, runs=...)`
only after trusted transport identity resolution. The optional `runs` stand-in
implements the existing `start_run`, `stream_run`, and `answer_run` public API;
production defaults to `agent.runs`. The service fixes family/person/principal
from the verified, opted-in active contact. The principal is the linked user ID
or `contact:<contactId>`. It rechecks mapping and consent for every action.
For iMessage the frozen context also binds the observed handle, channel, direct
space ID/kind, verifiedAt and optInAt. Every `_state` check, including the inherited
callback before each model/subagent invocation, compares that complete proof.
Fresh consent in the same space does not revive an older in-flight invocation.

- `get_open_questions()` returns only this person's open question IDs/text.
- `select_question(question_id)` accepts only one of those questions. Its target
  memory is resolved server-side and revalidated at submission for second tellings.
- `save_story_draft(text="", year=None, title=None)` appends **new** words and
  updates the year/title. Text is capped at 20,000 characters, title at 200, and
  year at 1800 through next year, matching the current memory route.
- `attach_photos(photo_ids)` is trusted-worker-only, never a model tool. It validates
  family and photo kind through public media APIs, deduplicates IDs, and stores up
  to ten photos in the current draft. Submission revalidates those photos before
  passing them to the existing studio; no caller-controlled family is accepted.
- `submit_story()` reserves the draft durably before `start_run`. It returns
  `{runId, duplicate}`; a repeated successful submission returns the same run.
  The same atomic reservation charges one of three studio submissions per contact
  per UTC day. Failed/uncertain starts keep their charge; NEW cannot reset it.
  A known configuration failure or exhausted daily cap leaves the draft unchanged.
- `await relay_run_events(timeout=1.0, max_events=50)` reads only the bound run,
  persists its replay cursor/pending pause, and closes the stream. Limits are
  ten seconds, 100 new events, 64 KiB per batch, and 10,000 replayed events.
  Closing has a separate one-second deadline. A worker must handle oversized or
  unreconcilable history explicitly, not discard it.
- `answer_run_question(kind, text=None, approve=None)` answers only the pending
  pause using the original principal/run. Props auto-keeps only the relayed IDs;
  merge requires an explicit boolean. It cannot answer a caller-supplied run ID.
- Worker relay calls add `durable=True`: cursor/pending state and `relayBuffer`
  persist atomically. An unacknowledged buffer is returned again without reading
  more events. `acknowledge_relay(events)` clears only that exact buffer, after
  outbox persistence. Direct callers retain the non-buffered default.

All synchronous methods perform database work and belong off an ASGI event loop.
`submit_story` and `answer_run_question` explicitly reject event-loop calls so a
coroutine from the module-level studio API cannot accidentally be stored as an ID.

## Agent interface and limits

`ConversationAgent(service, model=None, checkpointer=None, max_model_calls=32)`
builds `create_deep_agent` lazily at construction, using `studio.make_model()`
unless a model is injected. Imports make no provider call. `invoke(text)` fixes
the LangGraph thread ID to the family/contact and sets a recursion bound of 64.
The model-call budget is persisted per UTC day in the family-scoped conversation document;
callbacks count nested/subagent and summarization calls too. Consent is checked
before each model call and again before returning output. The model receives no
tools for changing family/person/principal/run IDs or for direct outbound delivery.
`modelBudgetDay` and `modelCalls` hold the current UTC-day interview window;
`studioBudgetDay` and `studioSubmissions` hold the separate studio window. Counters
roll forward atomically, never backward, and remain bounded across agent rebuilds.
Legacy model counts without a date are charged to the first observed day rather
than silently granting a new budget. UTC midnight restores the next day's allowance.

`worker_managed=True` exposes only the read-only `get_open_questions` tool. The worker saves
raw words/year, and handles SUBMIT/KEEP/YES/NO and actual studio events itself.
`await ainvoke(text, timeout=20)` supports cancellation and supplies the persisted
draft as context; its pending worker reservation remains held if an outcome is
uncertain. `ScriptedConversation` returns an explicit offline interview question;
it does not fake successful generation. The demo's actual studio is the existing
`seed.fake_runs.ScriptedRunManager`, which compiles and saves its scripted scene.

The backend is the virtual `StateBackend`, with no host filesystem/shell access.
Default message-history checkpoints are in memory for local tests. Production
must inject a durable checkpointer and serialize invocations for a contact;
drafts, submission reservations, and call counts are already database-backed.
Use one agent instance per contact, never reuse a contact's checkpointer thread
for another family. The caller must deduplicate inbound events before invocation:
draft appends are intentionally not a replacement for the durable inbox worker.

## Recovery boundary

There is one active draft/run per family/contact. Trusted worker
`start_new_story(worker_token)` requires an actual persisted terminal studio event,
fully drained outbox/relay, no pending/uncertain answer or submission, and the
current durable worker reservation. It archives the last ten run summaries, clears
the completed draft/run, and increments the thread generation. It never resets
the current day's model/studio budgets and is not exposed to the model. Typed NEW and the
eligible manual scheduler use the same gate. Question selection is immutable within
a draft, preserving a scheduled second-telling target. A durable compare-and-set
prevents concurrent duplicate submissions.
If studio start or saving its returned ID fails, the state is `submission_uncertain`
(or remains `submitting` if the database is unavailable). **Do not automatically
retry or clear that reservation:** an operator must reconcile whether a run was
created. A failed answer similarly records an uncertain `sending` state with no
pending answer, preventing blind repeat side effects. This favors avoiding
duplicate stories over claiming exactly-once recovery across independent systems.

The worker preflights the combined draft length, combined photo count, known model
configuration and interview budget before changing either text or photos. Rejected
messages receive a fixed explanation, are marked processed, and must be resent if
the teller wants to try later; existing draft words/photos remain unchanged. The
worker stays idle and usable. If a read-only worker interview reaches its callback
budget mid-graph, already saved words remain and a fixed limit reply is delivered.
Provider timeouts and unknown exceptions are still uncertain, not ordinary limits.

For a new, explicitly marked pre-effect failure only, an operator may stop ticks
and invoke `db.conversations.resume_preflight_worker(db, family_id, contact_id)`.
It requires `reason="preflight_only"`, `preflightOnly=True`, current contact authority,
an open draft and no run, pending answer, relay buffer, uncertain outbox or voice
processing. It restores only that worker token's receipt reservations. Recovery
uses a held `recovering` state; a partial database failure needs manual inspection.
Old `reconciliation_required`, crashed running workers, uncertain submissions,
transcription and sends are deliberately ineligible. This helper is not an HTTP
route, a model tool, or a way to clear budget/uncertainty markers.

No shared audio/channel schema fields or settings UI are added here. The separately
flagged manual worker scheduler selects public neutral or person-bound open questions
and sends through its signed outbox. Jonah's hooks and auth
review remain separate prerequisites for mounting Channels in the main app.

## Tests

From `api/`: `uv run pytest tests/channels/test_conversation.py -q`.
Tests use mongomock, an injected fake studio, and the real Deep Agent graph with
the repository's `ScriptedChatModel`. No credentials or live model calls are used.
