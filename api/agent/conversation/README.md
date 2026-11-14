# Contact-bound conversation core

This layer collects a text story and year, starts the existing studio, and relays
its follow-up/prop/merge pauses. It is **not wired into the inbox or tick router**
yet. Nothing here sends a message, makes a call, enrolls contacts, or starts an
API background task. A receipt in the inbox still does not generate a story.

## Worker interface

Construct `ConversationService.for_contact(db, family_id, contact_id, runs=...)`
only after trusted transport identity resolution. The optional `runs` stand-in
implements the existing `start_run`, `stream_run`, and `answer_run` public API;
production defaults to `agent.runs`. The service fixes family/person/principal
from the verified, opted-in active contact. The principal is the linked user ID
or `contact:<contactId>`. It rechecks mapping and consent for every action.

- `get_open_questions()` returns only this person's open question IDs/text.
- `select_question(question_id)` accepts only one of those questions. Its target
  memory is resolved server-side and revalidated at submission for second tellings.
- `save_story_draft(text="", year=None, title=None)` appends **new** words and
  updates the year/title. Text is capped at 20,000 characters, title at 200, and
  year at 1800 through next year, matching the current memory route.
- `submit_story()` reserves the draft durably before `start_run`. It returns
  `{runId, duplicate}`; a repeated successful submission returns the same run.
- `await relay_run_events(timeout=1.0, max_events=50)` reads only the bound run,
  persists its replay cursor/pending pause, and closes the stream. Limits are
  ten seconds, 100 new events, 64 KiB per batch, and 10,000 replayed events.
  Closing has a separate one-second deadline. A worker must handle oversized or
  unreconcilable history explicitly, not discard it.
- `answer_run_question(kind, text=None, approve=None)` answers only the pending
  pause using the original principal/run. Props auto-keeps only the relayed IDs;
  merge requires an explicit boolean. It cannot answer a caller-supplied run ID.

All synchronous methods perform database work and belong off an ASGI event loop.
`submit_story` and `answer_run_question` explicitly reject event-loop calls so a
coroutine from the module-level studio API cannot accidentally be stored as an ID.

## Agent interface and limits

`ConversationAgent(service, model=None, checkpointer=None, max_model_calls=32)`
builds `create_deep_agent` lazily at construction, using `studio.make_model()`
unless a model is injected. Imports make no provider call. `invoke(text)` fixes
the LangGraph thread ID to the family/contact and sets a recursion bound of 64.
The model-call budget is persisted in the family-scoped conversation document;
callbacks count nested/subagent and summarization calls too. Consent is checked
before each model call and again before returning output. The model receives no
tools for changing family/person/principal/run IDs or for direct outbound delivery.

The backend is the virtual `StateBackend`, with no host filesystem/shell access.
Default message-history checkpoints are in memory for local tests. Production
must inject a durable checkpointer and serialize invocations for a contact;
drafts, submission reservations, and call counts are already database-backed.
Use one agent instance per contact, never reuse a contact's checkpointer thread
for another family. The caller must deduplicate inbound events before invocation:
draft appends are intentionally not a replacement for the durable inbox worker.

## Recovery boundary

There is currently one draft/run per family/contact. New-story/reset lifecycle is
future work. A durable compare-and-set prevents concurrent duplicate submissions.
If studio start or saving its returned ID fails, the state is `submission_uncertain`
(or remains `submitting` if the database is unavailable). **Do not automatically
retry or clear that reservation:** an operator must reconcile whether a run was
created. A failed answer similarly records an uncertain `sending` state with no
pending answer, preventing blind repeat side effects. This favors avoiding
duplicate stories over claiming exactly-once recovery across independent systems.

No audio/channel schema fields, photos, question-bank selection, settings UI,
scheduling, or cloud delivery are added by this slice. Jonah's hooks and auth
review remain separate prerequisites for mounting Channels in the main app.

## Tests

From `api/`: `uv run pytest tests/channels/test_conversation.py -q`.
Tests use mongomock, an injected fake studio, and the real Deep Agent graph with
the repository's `ScriptedChatModel`. No credentials or live model calls are used.
