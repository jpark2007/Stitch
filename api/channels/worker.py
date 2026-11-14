"""Explicit, bounded manual ticks. Crashed/uncertain work is never leased to another worker."""

import asyncio
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlsplit
from uuid import uuid4

from db import contacts, conversations, question_bank
from agent.conversation.service import ConversationService


class ChannelWorker:
    def __init__(self, db, *, sender, agent_factory, runs=None, max_contacts: int = 5, web_url=None,
                 transcriber=None, scheduler_enabled=None, clock=None):
        if not 1 <= max_contacts <= 5:
            raise ValueError("Invalid contact batch limit")
        self.db, self.sender, self.agent_factory, self.runs = db, sender, agent_factory, runs
        self.max_contacts = max_contacts
        self.transcriber = transcriber
        self.scheduler_enabled = (os.getenv("STITCH_CHANNELS_SCHEDULER") == "1"
                                  if scheduler_enabled is None else scheduler_enabled is True)
        self.now = clock or (lambda: datetime.now(timezone.utc))
        self.web_url = web_url or os.getenv("STITCH_WEB_URL", "http://localhost:3000")
        parsed = urlsplit(self.web_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
                or any(ord(c) < 33 for c in self.web_url)):
            raise ValueError("STITCH_WEB_URL must be a plain HTTP(S) origin")
        self.web_url = self.web_url.rstrip("/")

    def tick(self) -> dict:
        processed = sent = blocked = 0
        started = time.monotonic()
        candidates = conversations.worker_candidates(self.db, limit=self.max_contacts)
        if self.scheduler_enabled and len(candidates) < self.max_contacts:
            for key in question_bank.discover_contacts(self.db, self.max_contacts - len(candidates)):
                if key not in candidates:
                    candidates.append(key)
        for family, contact_id in candidates:
            if time.monotonic() - started >= 25:
                break  # A single bounded operation may finish after this soft batch deadline.
            token = uuid4().hex
            row = conversations.acquire_worker(self.db, family, contact_id, token)
            if row is None:
                blocked += 1
                continue
            try:
                count, delivered = self._contact(family, contact_id, token, row)
                processed += count
                sent += delivered
                conversations.update_worker(self.db, family, contact_id, token, {"status": "idle"})
            except Exception as exc:
                blocked += 1
                # A model/tool/send may already have acted. Never steal/retry this
                # reservation, including after a process crash or database failure.
                try:
                    from .audio import AudioConversionError
                    reason = "audio_conversion_" + exc.code if isinstance(exc, AudioConversionError) else "reconciliation_required"
                    conversations.update_worker(self.db, family, contact_id, token,
                                                {"status": "blocked", "reason": reason})
                except Exception:
                    pass
        return {"processed": processed, "sent": sent, "blocked": blocked, "scheduler": "manual"}

    def _contact(self, family, contact_id, token, row):
        from . import scheduler
        service = ConversationService.for_contact(self.db, family, contact_id, runs=self.runs)
        identity = contacts.worker_contact(self.db, family, contact_id)
        if identity["channel"] == "imessage" and (identity.get("spaceKind") != "direct" or not identity.get("spaceId")):
            raise PermissionError("Verified direct-message space is required")

        def check():
            service._state()
            current = contacts.worker_contact(self.db, family, contact_id)
            if any(current.get(k) != identity.get(k) for k in ("handle", "channel", "personId", "userId")):
                raise PermissionError("Contact changed")
            if current["channel"] == "imessage" and (current.get("spaceKind") != "direct"
                    or current.get("spaceId") != identity.get("spaceId")):
                raise PermissionError("Verified direct-message proof changed")
            return current

        def update(fields):
            nonlocal row
            row = conversations.update_worker(self.db, family, contact_id, token, fields)

        if (self.scheduler_enabled and not row.get("destination") and identity.get("spaceKind") == "direct"
                and isinstance(identity.get("spaceId"), str) and identity["spaceId"].strip()):
            # Enrollment's signed direct-space proof is trusted routing metadata.
            # The bridge still refuses unknown/unregistered spaces after a restart.
            update({"destination": {"provider": identity["channel"], "spaceId": identity["spaceId"],
                "handle": identity["handle"], "personId": identity["personId"], "principal": service.context.principal}})

        def route(receipt=None):
            current = check()
            if receipt:
                if receipt["provider"] != current["channel"] or receipt["personId"] != current["personId"]:
                    raise PermissionError("Receipt identity changed")
                if receipt.get("principal") != service.context.principal:
                    raise PermissionError("Receipt principal changed or predates worker identity binding")
                if receipt.get("handle") != current["handle"]:
                    raise PermissionError("Receipt handle changed")
                destination = {"provider": receipt["provider"], "spaceId": receipt["spaceId"],
                               "handle": current["handle"], "personId": current["personId"],
                               "principal": service.context.principal}
                update({"destination": destination, "lastInboundAt": self.now()})
            destination = row.get("destination")
            if not destination or (destination["handle"], destination["provider"], destination["personId"],
                                   destination["principal"]) != (current["handle"], current["channel"],
                                                               current["personId"], service.context.principal):
                raise PermissionError("Destination mapping changed")
            if current["channel"] == "imessage" and destination["spaceId"] != current["spaceId"]:
                raise PermissionError("Destination is not the verified direct-message space")
            return destination

        sent = 0

        def deliver():
            nonlocal sent
            for index in range(len(row["outbox"])):
                item = row["outbox"][index]
                if item["status"] == "sent":
                    continue
                if item["status"] == "cancelled":
                    continue
                if item["status"] != "pending":
                    raise RuntimeError("Uncertain outbound send")
                route()
                notification_id = item.get("notificationId")
                def cancel_prompt(expected):
                    question_bank.update_notification(self.db, family, contact_id, notification_id,
                        expected, {"status": "cancelled", "active": False})
                    row["outbox"][index]["status"] = "cancelled"
                    update({"outbox": row["outbox"]})
                def prompt_allowed():
                    current = check()
                    return (self.scheduler_enabled and scheduler.allowed_now(current, self.now())
                        and scheduler.trusted_destination(current, row, service.context.principal)
                        and not conversations.worker_receipts(self.db, family, contact_id))
                if notification_id and not prompt_allowed():
                    cancel_prompt("reserved")
                    continue
                item["status"] = "sending"
                update({"outbox": row["outbox"]})
                if notification_id:
                    question_bank.update_notification(self.db, family, contact_id, notification_id,
                        "reserved", {"status": "sending"})
                check()  # STOP after durable send reservation suppresses the send.
                if notification_id and not prompt_allowed():
                    cancel_prompt("sending")
                    continue
                self.sender.send(item["message"])
                row["outbox"][index]["status"] = "sent"
                update({"outbox": row["outbox"]})
                if notification_id:
                    notice = question_bank.update_notification(self.db, family, contact_id, notification_id,
                        "sending", {"status": "sent", "sentAt": self.now()})
                    if notice.get("questionId"):
                        service.select_question(notice["questionId"])
                sent += 1

        def queue(texts, notification=None):
            destination = route()
            if any(x["status"] not in {"sent", "cancelled"} for x in row["outbox"]):
                raise RuntimeError("Previous outbox is not drained")
            if len(texts) > 10 or sum(len(x.encode()) for x in texts) > 8000:
                raise RuntimeError("Outbound batch too large; operator review required")
            update({"outbox": [{"status": "pending", **({"notificationId": notification["_id"]} if notification else {}), "message": {
                "messageId": "prompt-" + notification["_id"] if notification else "channels-" + uuid4().hex, "provider": destination["provider"],
                "spaceId": destination["spaceId"], "text": text}} for text in texts if text.strip()]})

        def relay():
            state = service._state()
            if not state.get("runId"):
                return
            events = asyncio.run(service.relay_run_events(timeout=0.25, durable=True))
            texts = []
            done = False
            for event in events:
                name, data = event["event"], event["data"]
                if name == "question":
                    texts.append(data["text"])
                elif name == "props_found":
                    names = ", ".join(p.get("name", p["id"]) for p in data["props"])
                    texts.append("Review these props: " + names + ". Reply KEEP to keep them; other edits need the web app.")
                elif name == "merge":
                    texts.append(data.get("text", "Confirm the proposed person match.") + " Reply YES or NO.")
                elif name == "done":
                    memory_id = data.get("memoryId")
                    if memory_id:
                        texts.append("Your story is ready in Stitch: " + self.web_url + "/memories/"
                                     + quote(memory_id, safe="") + "?" + urlencode({"family": family}))
                    done = True
                elif name == "error":
                    texts.append("The studio could not complete this story. Please ask the operator to review it.")
                    done = True
            queue(texts)  # Persist before acknowledging the durable relay buffer.
            service.acknowledge_relay(events)
            update({"pollRun": not done, "runFinished": done})
            deliver()

        if row["outbox"]:
            deliver()
        if row.get("destination") and row.get("pollRun"):
            relay()
        receipts = conversations.worker_receipts(self.db, family, contact_id)
        batch, size, photo_ids = [], 0, []
        for receipt in receipts:
            if batch and (receipt.get("receiptType") == "voice" or batch[0].get("receiptType") == "voice"):
                break  # One voice provider call per contact per tick.
            # Never combine messages from different provider spaces.
            if batch and receipt["spaceId"] != batch[0]["spaceId"]:
                break
            command = receipt["text"].strip().upper()
            if batch and (command in {"HELP", "CALL", "NEW", "SUBMIT", "KEEP", "YES", "NO"} or command.startswith("YEAR ")):
                break
            added = len(receipt["text"].encode()) + (1 if batch else 0)
            candidate_photos = list(dict.fromkeys([*photo_ids, *receipt.get("photoIds", [])]))
            if size + added > 8000 or len(candidate_photos) > 10:
                break
            batch.append(receipt)
            size += added
            photo_ids = candidate_photos
            if command in {"HELP", "CALL", "NEW", "SUBMIT", "KEEP", "YES", "NO"} or command.startswith("YEAR "):
                break
        if not batch:
            if self.scheduler_enabled:
                current = check()
                question = scheduler.plan(self.db, service, current, row, self.now())
                if question:
                    if service._state()["status"] != "draft":
                        service.start_new_story(token)
                        update({"pollRun": False, "runFinished": False})
                    notice = question_bank.reserve_notification(self.db, family, contact_id,
                        question, route(), self.now())
                    if notice:
                        queue([question["text"]], notification=notice)
                        deliver()
            return 0, sent
        for receipt in batch:
            if (receipt.get("principal") != service.context.principal
                    or receipt["personId"] != service.context.person_id
                    or receipt.get("handle") != identity["handle"]
                    or receipt["provider"] != identity["channel"]
                    or (identity["channel"] == "imessage" and receipt["spaceId"] != identity["spaceId"])):
                raise PermissionError("Batch receipt identity changed")
        route(batch[0])
        conversations.reserve_worker_receipts(self.db, family, contact_id, [r["_id"] for r in batch], token)
        check()
        is_voice = batch[0].get("receiptType") == "voice"
        voice_text = None
        if is_voice:
            from .voice import transcribe_receipt
            transcriber = self.transcriber
            if transcriber is None:
                from .transcription import OpenAITranscriber, TranscriptionConfig
                transcriber = OpenAITranscriber(TranscriptionConfig.from_env())
            voice_text = transcribe_receipt(self.db, family, contact_id, batch[0],
                                           transcriber=transcriber, check=check)
            check()
        if photo_ids:
            service.attach_photos(photo_ids)
        text = voice_text if is_voice else "\n".join(r["text"] for r in batch if r["text"].strip())
        state = service._state()
        pending = state.get("pending")
        typed_control = not is_voice and not photo_ids and all(r.get("receiptType") not in {"photo", "voice"} for r in batch)
        command = text.strip().upper()
        if typed_control and command in {"HELP", "CALL"}:
            queue([("Send story words, YEAR followed by its year, then SUBMIT. Reply KEEP for props or YES/NO "
                    "for a person match. NEW starts another draft after the studio finishes. STOP ends messages. "
                    "HELP shows these instructions.") if command == "HELP" else
                   "Live calls are not configured. You can continue by text; voice notes require enabled transcription."])
            deliver()
        elif typed_control and command == "NEW":
            try:
                service.start_new_story(token)
                update({"pollRun": False, "runFinished": False})
                queue(["A new draft is ready. Send your next story, YEAR followed by its year, then SUBMIT."])
            except ValueError:
                queue(["Finish the current studio run and settle its replies before NEW. Your draft was kept."])
            deliver()
        elif pending and text.strip():
            kind = pending["kind"]
            if kind == "answer":
                service.answer_run_question(kind, text=text)
            elif kind == "props" and not is_voice and text.strip().upper() == "KEEP":
                service.answer_run_question(kind)
            elif kind == "merge" and not is_voice and text.strip().upper() in {"YES", "NO"}:
                service.answer_run_question(kind, approve=text.strip().upper() == "YES")
            else:
                queue(["Reply KEEP for the listed props, or YES/NO for the person match. No changes were applied."])
                deliver()
        elif state["status"] == "draft":
            year = None if is_voice else re.fullmatch(r"YEAR\s+(\d{4})", text.strip(), re.IGNORECASE)
            if year:
                try:
                    service.save_story_draft(year=int(year[1]))
                    queue(["Year saved. Reply SUBMIT when your draft is ready."])
                except ValueError:
                    queue(["Please send YEAR followed by a year between 1800 and next year."])
            elif not is_voice and text.strip().upper() == "SUBMIT":
                if not state["draft"].get("year") or not state["draft"].get("text", "").strip():
                    queue(["Send your story and YEAR followed by its year before SUBMIT."])
                else:
                    service.submit_story()
                    update({"pollRun": True})
                    queue(["Your story was submitted. I'll relay the studio's questions here."])
            elif text.strip():
                service.save_story_draft(text=text)
                question_bank.answer_prompt(self.db, family, contact_id, batch[0]["_id"], self.now())
                agent = self.agent_factory(service)
                response = asyncio.run(agent.ainvoke(text, timeout=20))
                check()
                if isinstance(response, dict):
                    messages = response.get("messages", [])
                    response = messages[-1].content if messages else ""
                if isinstance(response, list) and len(response) <= 100:
                    response = "\n".join(block["text"] for block in response
                        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str))
                # Only the interview question is relayed. Draft status is trusted;
                # completion statements/URLs come exclusively from studio events.
                question = response.strip() if isinstance(response, str) else ""
                if (not question.endswith("?") or len(question.encode()) > 6000
                        or re.search(r"https?://|/memories/", question, re.IGNORECASE)):
                    question = "What else would you like to include?"
                queue(["Your words are saved in the draft. " + question
                       + " Send YEAR followed by its year, then SUBMIT when ready."])
            else:
                queue(["Photo added to your draft. Send your story and YEAR followed by its year."])
            deliver()
        elif not pending:
            queue(["Your story is already in the studio. Please wait for its next question or result."])
            deliver()
        conversations.complete_worker_receipts(self.db, family, contact_id, token)
        if service._state().get("runId") and not row.get("runFinished"):
            update({"pollRun": True})
            relay()
        return len(batch), sent


def configured_worker(db):
    from .outbound import BridgeSender
    mode = os.getenv("CHANNELS_CONVERSATION_MODE", "")
    if mode == "scripted":
        # Standalone demo injects its own offline studio; production config must
        # not silently route a scripted interview into an inherited live studio.
        raise ValueError("Scripted mode requires the isolated Channels demo")
    if mode != "real":
        raise ValueError("Explicit real conversation mode is required")
    from agent.conversation.agent import ConversationAgent
    return ChannelWorker(db, sender=BridgeSender(),
                         agent_factory=lambda service: ConversationAgent(service, worker_managed=True))
