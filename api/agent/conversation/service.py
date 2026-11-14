"""Synchronous draft/submission tools and a bounded async studio-event adapter.

Call synchronous methods off an ASGI event loop. Transport and scheduling are
deliberately separate: nothing here starts a background task or sends a message.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone

import schemas
from db import conversations, media, memories


def _worker_only() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("Call synchronous conversation tools off the event loop")


@dataclass(frozen=True)
class ContactContext:
    family_id: str
    contact_id: str
    person_id: str
    principal: str


class ConversationService:
    def __init__(self, db, context: ContactContext, *, runs=None):
        self.db = db
        self.context = context
        self._runs = runs
        self._state()

    @classmethod
    def for_contact(cls, db, family_id: str, contact_id: str, *, runs=None):
        contact = conversations.active_contact(db, family_id, contact_id)
        context = ContactContext(family_id, contact_id, contact["personId"],
                                 contact.get("userId") or f"contact:{contact_id}")
        return cls(db, context, runs=runs)

    @property
    def thread_id(self) -> str:
        base = "channels:" + conversations.conversation_key(self.context.family_id, self.context.contact_id)
        generation = self._state().get("generation", 0)
        return base if generation == 0 else f"{base}:{generation}"

    def _state(self) -> dict:
        c = self.context
        contact = conversations.active_contact(self.db, c.family_id, c.contact_id)
        if (contact["personId"] != c.person_id
                or (contact.get("userId") or f"contact:{c.contact_id}") != c.principal):
            raise PermissionError("Contact identity changed")
        return conversations.load_conversation(self.db, c.family_id, c.contact_id, c.person_id, c.principal)

    def _update(self, row: dict, fields: dict, *, status: str | None = None) -> dict:
        c = self.context
        return conversations.update_conversation(self.db, c.family_id, c.contact_id,
                                                  row["revision"], fields, status=status)

    @property
    def runs(self):
        if self._runs is None:
            from agent import runs
            self._runs = runs
        return self._runs

    def get_open_questions(self) -> list[dict]:
        self._state()
        c = self.context
        return [{"id": q.id, "text": q.text} for q in memories.open_questions(
            self.db, c.family_id, c.person_id)]

    def _question(self, question_id: str):
        c = self.context
        for q in memories.open_questions(self.db, c.family_id, c.person_id):
            if q.id == question_id:
                return q
        raise ValueError("Question is not open for this contact")

    def select_question(self, question_id: str) -> dict:
        row = self._state()
        if row["draft"].get("questionId") not in {None, question_id}:
            raise ValueError("This draft is already bound to another question")
        question = self._question(question_id)
        draft = dict(row["draft"], questionId=question.id)
        self._update(row, {"draft": draft}, status="draft")
        return {"question": question.text}

    def save_story_draft(self, text: str = "", year: int | None = None,
                         title: str | None = None) -> dict:
        row = self._state()
        if not isinstance(text, str) or len(text) > 20_000:
            raise ValueError("Story text must be at most 20000 characters")
        if year is not None and (type(year) is not int or not 1800 <= year <= datetime.now(timezone.utc).year + 1):
            raise ValueError("Year must be between 1800 and next year")
        if title is not None and (not isinstance(title, str) or len(title) > 200):
            raise ValueError("Title must be at most 200 characters")
        draft = dict(row["draft"])
        if text.strip():
            draft["text"] = "\n".join(x for x in (draft["text"], text.strip()) if x)
        if len(draft["text"]) > 20_000:
            raise ValueError("Combined story is longer than 20000 characters")
        if year is not None:
            draft["year"] = year
        if title is not None:
            draft["title"] = title.strip() or None
        self._update(row, {"draft": draft}, status="draft")
        return draft

    def submit_story(self) -> dict:
        _worker_only()
        row = self._state()
        if row["status"] == "submitted":
            return {"runId": row["runId"], "duplicate": True}
        if row["status"] != "draft":
            raise RuntimeError("Submission outcome uncertain; operator reconciliation required")
        draft = row["draft"]
        if not draft.get("text", "").strip() or draft.get("year") is None:
            raise ValueError("Collect the story and year before submitting")
        question = self._question(draft["questionId"]) if draft.get("questionId") else None
        photo_ids = self._validate_photos(draft.get("photoIds", []))
        fields = schemas.SubmitMemoryFields(text=draft["text"], year=draft["year"],
                                            title=draft.get("title"),
                                            question=question.text if question else None,
                                            memory_id=question.memory_id if question else None,
                                            participant_ids=[self.context.person_id])
        reserved = self._update(row, {"status": "submitting"}, status="draft")
        try:
            # Recheck STOP after acquiring the durable reservation, before side effects.
            self._state()
            c = self.context
            self._validate_photos(photo_ids)
            run_id = self.runs.start_run(c.family_id, c.principal, c.person_id, fields, photo_ids)
            if not isinstance(run_id, str) or not run_id:
                raise RuntimeError("Studio did not return a run identifier")
            self._update(reserved, {"status": "submitted", "runId": run_id})
        except Exception:
            # Even a failed start may have persisted/launched a run. Never guess/retry it.
            try:
                self._update(reserved, {"status": "submission_uncertain"})
            except Exception:
                pass  # The durable 'submitting' reservation remains fail-closed.
            raise RuntimeError("Submission outcome uncertain; operator reconciliation required") from None
        return {"runId": run_id, "duplicate": False}

    def _validate_photos(self, photo_ids: list[str]) -> list[str]:
        if not isinstance(photo_ids, list) or len(photo_ids) > 10:
            raise ValueError("Attach at most 10 photos")
        normalized = []
        for ref in photo_ids:
            photo_id = media.parse_media_ref(ref)
            found = media.get_media(self.db, self.context.family_id, photo_id) if photo_id else None
            if found is None or found.kind != "photo":
                raise ValueError("Photo is not available in this family")
            if photo_id not in normalized:
                normalized.append(photo_id)
        return normalized

    def attach_photos(self, photo_ids: list[str]) -> dict:
        """Trusted worker method, deliberately absent from the model's tool set."""
        row = self._state()
        new_ids = self._validate_photos(photo_ids)
        draft = dict(row["draft"])
        combined = list(dict.fromkeys([*draft.get("photoIds", []), *new_ids]))
        draft["photoIds"] = self._validate_photos(combined)
        self._state()
        self._update(row, {"draft": draft}, status="draft")
        return {"photoCount": len(combined)}

    async def relay_run_events(self, *, timeout: float = 1.0, max_events: int = 50,
                               durable: bool = False) -> list[dict]:
        if not 0 < timeout <= 10 or not 1 <= max_events <= 100:
            raise ValueError("Event read limits are out of range")
        row = await asyncio.to_thread(self._state)
        if row.get("relayBuffer"):
            return row["relayBuffer"]
        if not row.get("runId"):
            raise ValueError("No submitted run")
        stream = await asyncio.to_thread(self.runs.stream_run, self.context.family_id, row["runId"])
        events, cursor, pending = [], row["cursor"], row.get("pending")
        terminal = row.get("terminalEvent")
        index, byte_count = 0, 0
        try:
            async with asyncio.timeout(timeout):
                async for name, payload in stream:
                    index += 1
                    if index > 10_000:
                        raise RuntimeError("Run history exceeds the bounded replay window")
                    if index <= row["cursor"]:
                        continue
                    data = payload.model_dump(mode="json", by_alias=True)
                    byte_count += len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
                    if byte_count > 65_536:
                        raise RuntimeError("Run event batch exceeds the size limit")
                    events.append({"event": name, "data": data})
                    cursor = index
                    if name in {"question", "props_found", "merge"}:
                        pending = {"kind": {"question": "answer", "props_found": "props", "merge": "merge"}[name],
                                   "data": data}
                    if name in {"done", "error"}:
                        pending = None
                        terminal = {"event": name, "memoryId": data.get("memoryId"),
                                    "endedAt": datetime.now(timezone.utc)}
                    if len(events) >= max_events or name in {"done", "error"}:
                        break
        except TimeoutError:
            pass
        finally:
            await asyncio.wait_for(stream.aclose(), timeout=1.0)
        await asyncio.to_thread(self._state)  # A STOP during polling prevents disclosure.
        fields = {"cursor": cursor, "pending": pending, "terminalEvent": terminal}
        if durable:
            fields["relayBuffer"] = events
        await asyncio.to_thread(self._update, row, fields)
        return events

    def acknowledge_relay(self, events: list[dict]) -> None:
        """Trusted worker: acknowledge only after durable outbox persistence."""
        row = self._state()
        if row.get("relayBuffer", []) != events:
            raise RuntimeError("Relay batch changed")
        self._update(row, {"relayBuffer": []})

    def can_start_new_story(self, worker: dict) -> bool:
        row = self._state()
        return (worker.get("familyId") == self.context.family_id
            and worker.get("contactId") == self.context.contact_id
            and worker.get("status") == "running" and worker.get("runFinished") is True
            and not worker.get("pollRun")
            and all(x["status"] in {"sent", "cancelled"} for x in worker.get("outbox", []))
            and row["status"] == "submitted" and bool(row.get("runId"))
            and (row.get("terminalEvent") or {}).get("event") in {"done", "error"}
            and not row.get("relayBuffer") and not row.get("pending")
            and row.get("answerStatus") != "sending")

    def start_new_story(self, worker_token: str) -> dict:
        """Trusted worker only; never a model tool or a reset of uncertain work."""
        _worker_only()
        c = self.context
        worker = conversations.locked_worker(self.db, c.family_id, c.contact_id, worker_token)
        if not self.can_start_new_story(worker):
            raise ValueError("Finish the current studio run and settle its replies before NEW")
        row = self._state()
        archive = [*row.get("archive", []), {"runId": row["runId"], **row["terminalEvent"]}][-10:]
        generation = row.get("generation", 0) + 1
        self._update(row, {"status": "draft", "draft": {"text": ""}, "runId": None, "cursor": 0,
            "pending": None, "relayBuffer": [], "terminalEvent": None, "answerStatus": None,
            "generation": generation, "archive": archive}, status="submitted")
        # modelCalls is deliberately lifetime-scoped and never reset here.
        return {"started": True, "generation": generation}

    def answer_run_question(self, kind: str, text: str | None = None,
                            approve: bool | None = None) -> dict:
        _worker_only()
        row = self._state()
        pending = row.get("pending")
        if not pending or kind != pending["kind"]:
            raise ValueError("Answer kind does not match the pending run question")
        if kind == "answer" and (not isinstance(text, str) or not text.strip() or len(text) > 20_000):
            raise ValueError("An answer requires 1 to 20000 characters")
        if kind == "merge" and type(approve) is not bool:
            raise ValueError("A merge requires an explicit yes or no")
        # No model-supplied prop ids: this slice keeps the exact reviewed chips.
        props = [schemas.PropEdit(id=p["id"], action="keep") for p in pending["data"]["props"]] if kind == "props" else None
        answer = schemas.AnswerRequest(kind=kind, text=text if kind == "answer" else None,
                                       approve=approve if kind == "merge" else None, props=props)
        reserved = self._update(row, {"pending": None, "answerStatus": "sending"})
        try:
            self._state()
            c = self.context
            self.runs.answer_run(c.family_id, row["runId"], c.principal, answer)
            self._update(reserved, {"answerStatus": "sent"})
        except Exception:
            raise RuntimeError("Answer outcome uncertain; refresh/reconcile before another answer") from None
        return {"answered": kind}
