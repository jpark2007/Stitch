"use client";

// This week's question, by phone. For one person of the family:
// - pairing through the Channels settings routes (components/channels/client.ts,
//   reused as it is): their contact and its status; a phone number gets a
//   pairing code, shown large so it can be read out on stage, and the
//   contact is read again every 3 s until the phone has texted it; "Stop
//   texting this phone" behind a confirm step. A 404 from those routes means
//   Channels is off on this server: one calm line instead of all of that.
// - the question: "Set as this week's question" (createQuestion), and the
//   person's open questions when they are the signed-in person's own
//   (getQuestions answers only for the caller's person).
// - "Send it now" (POST .../prompt-now, phone.ts), which a server may not
//   have yet: then the button is off with one honest line.
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { ChannelSettingsError, createSettingsClient } from "@/components/channels/client";
import type { ChannelContact, Enrollment } from "@/components/channels/settings";
import { API_BASE_URL, isApiError, type StitchApi } from "@/lib/api";
import type { FamilySummary, Person, Question } from "@/lib/types";

import {
  PHONE_ERROR,
  PHONE_HINT,
  PROMPT_NOW_UNAVAILABLE,
  TEXTING_OFF,
  channelErrorCopy,
  demoPreferences,
  expiryCopy,
  normalizePhone,
  probePromptNow,
  promptNow,
  statusCopy,
  type ContactStatus,
} from "./phone";
import { ActionLine, Chip, Dot, FIELD_ERROR, IDLE, INPUT, LABEL, Panel, Pill, type ActionState } from "./ui";

const settings = createSettingsClient(API_BASE_URL);
const CONTACT_POLL_MS = 3000;
const MAX_QUESTION = 300;

type ContactState =
  | { kind: "loading" }
  | { kind: "off" }
  | { kind: "error"; status: number; text: string }
  | { kind: "ready"; contact: ChannelContact | null };

function browserZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone ?? null;
  } catch {
    return null;
  }
}

export function PhonePanel(props: { api: StitchApi; family: FamilySummary; people: Person[] | null }) {
  const { api, family, people } = props;
  const [personId, setPersonId] = useState<string>(family.personId ?? "");
  const id = useId();
  const chosen = people?.find((p) => p.id === personId) ?? null;
  // Until the tree loads (or when the remembered choice is not in it), fall back to the first person.
  const person = chosen ?? (people && people.length > 0 ? people[0] : null);

  return (
    <Panel title="This week’s question" kicker="By phone">
      {people === null ? (
        <p className="text-sm text-ink-soft">Loading the family…</p>
      ) : !person ? (
        <p className="text-sm text-ink-soft">No one is in this family’s tree yet.</p>
      ) : (
        <>
          <label htmlFor={`${id}-person`} className={LABEL}>
            For
          </label>
          <select
            id={`${id}-person`}
            value={person.id}
            onChange={(e) => setPersonId(e.target.value)}
            className={`${INPUT} cursor-pointer appearance-none bg-[url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2012%208'%3E%3Cpath%20d='M1%201.5l5%205%205-5'%20fill='none'%20stroke='%235c4e43'%20stroke-width='1.5'/%3E%3C/svg%3E")] bg-[length:0.75rem] bg-[right_0.25rem_center] bg-no-repeat pr-6 font-semibold`}
          >
            {people.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.id === family.personId ? " (you)" : p.userId ? "" : " (no account)"}
              </option>
            ))}
          </select>
          <PersonPhone key={person.id} api={api} family={family} person={person} />
        </>
      )}
    </Panel>
  );
}

function PersonPhone(props: { api: StitchApi; family: FamilySummary; person: Person }) {
  const { api, family, person } = props;
  const fid = family.id;
  const [contact, setContact] = useState<ContactState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);

  const apply = useCallback(
    (e: unknown) => {
      if (e instanceof ChannelSettingsError && e.status === 404) return { kind: "off" } as const;
      const status = e instanceof ChannelSettingsError ? e.status : 0;
      const fallback = e instanceof Error ? e.message : "Contact settings could not be loaded.";
      return { kind: "error", status, text: channelErrorCopy(status, fallback, person.name, Boolean(person.userId)) } as const;
    },
    [person.name, person.userId],
  );

  // Read the contact, then again every 3 s while it waits for the phone's code.
  const pending = contact.kind === "ready" && contact.contact?.status === "pending";
  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const read = () =>
      settings.get(fid, person.id, controller.signal).then(
        (c) => {
          if (controller.signal.aborted) return;
          setContact((prev) =>
            prev.kind === "ready" && sameContact(prev.contact, c) ? prev : { kind: "ready", contact: c },
          );
          if (c?.status === "pending") timer = window.setTimeout(read, CONTACT_POLL_MS);
        },
        (e: unknown) => {
          if (controller.signal.aborted) return;
          setContact(apply(e));
        },
      );
    void read();
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
    // `pending` restarts the polling after an enrollment makes the contact pending.
  }, [fid, person.id, attempt, pending, apply]);

  const status: ContactStatus = contact.kind === "ready" ? (contact.contact?.status ?? "none") : "none";
  const active = contact.kind === "ready" && status === "active";

  return (
    <div className="mt-4 space-y-4">
      {contact.kind === "off" ? (
        <p className="flex items-start gap-2 rounded-card bg-paper-deep/60 px-3 py-2 text-sm text-ink-soft">
          <Dot tone="off" />
          <span className="-mt-1">{TEXTING_OFF}</span>
        </p>
      ) : contact.kind === "loading" ? (
        <p role="status" className="text-sm text-ink-soft">
          Reading {person.name}’s phone…
        </p>
      ) : contact.kind === "error" ? (
        <div className="space-y-2">
          <p role="alert" className="text-sm leading-snug text-[#9c3b2a]">
            {contact.text}
          </p>
          {contact.status !== 403 ? (
            <Chip
              onClick={() => {
                setContact({ kind: "loading" });
                setAttempt((n) => n + 1);
              }}
            >
              Try again
            </Chip>
          ) : null}
        </div>
      ) : (
        <Pairing
          fid={fid}
          person={person}
          contact={contact.contact}
          enrollment={enrollment}
          onEnrolled={(en) => {
            setEnrollment(en);
            setContact({ kind: "ready", contact: en.contact });
          }}
          onStopped={(c) => {
            setEnrollment(null);
            setContact({ kind: "ready", contact: c });
          }}
          onError={(e) => apply(e)}
        />
      )}

      <QuestionForm api={api} family={family} person={person} />

      {/* Only once the contact has loaded: Channels is on and this person's phone is ours to manage. */}
      {contact.kind === "ready" ? <SendNow fid={fid} person={person} active={active} /> : null}
    </div>
  );
}

function sameContact(a: ChannelContact | null, b: ChannelContact | null): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function Pairing(props: {
  fid: string;
  person: Person;
  contact: ChannelContact | null;
  enrollment: Enrollment | null;
  onEnrolled: (e: Enrollment) => void;
  onStopped: (c: ChannelContact) => void;
  onError: (e: unknown) => { kind: "off" } | { kind: "error"; status: number; text: string };
}) {
  const { fid, person, contact, enrollment } = props;
  const status: ContactStatus = contact?.status ?? "none";
  const copy = statusCopy(status);
  const [phone, setPhone] = useState("");
  const [phoneError, setPhoneError] = useState<string | null>(null);
  const [action, setAction] = useState<ActionState>(IDLE);
  const [confirmStop, setConfirmStop] = useState(false);
  const busy = useRef(false);
  const id = useId();
  const tone = status === "active" ? "good" : status === "pending" ? "waiting" : "off";
  // The code shown is only for the pending contact it was made for.
  const shownCode = enrollment && status === "pending" ? enrollment : null;
  const canPair = status === "none" || status === "stopped" || (status === "pending" && !shownCode);
  // Pending turned active while this page watched: say so clearly.
  const [seen, setSeen] = useState(status);
  const [justPaired, setJustPaired] = useState(false);
  if (seen !== status) {
    setSeen(status);
    setJustPaired(seen === "pending" && status === "active");
    if (seen === "pending" && status === "active") setAction(IDLE);
  }

  async function pair(e: React.FormEvent) {
    e.preventDefault();
    if (busy.current) return;
    const handle = normalizePhone(phone);
    if (!handle) {
      setPhoneError(PHONE_ERROR);
      return;
    }
    setPhoneError(null);
    busy.current = true;
    setAction({ kind: "pending", label: "Asking the server for a pairing code…" });
    try {
      const en = await settings.enroll(fid, person.id, handle, demoPreferences(browserZone()));
      props.onEnrolled(en);
      setAction(IDLE); // the code card and the status line say what happens next
    } catch (err) {
      const r = props.onError(err);
      setAction({ kind: "error", text: r.kind === "off" ? TEXTING_OFF : r.text });
    } finally {
      busy.current = false;
    }
  }

  async function stop() {
    if (busy.current) return;
    busy.current = true;
    setAction({ kind: "pending", label: "Stopping…" });
    try {
      const c = await settings.stop(fid, person.id);
      props.onStopped(c);
      setConfirmStop(false);
      setAction({ kind: "done", text: `Stopped. ${person.name}’s phone gets no more messages; any waiting code no longer works.` });
    } catch (err) {
      const r = props.onError(err);
      setAction({ kind: "error", text: r.kind === "off" ? TEXTING_OFF : r.text });
    } finally {
      busy.current = false;
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Pill tone={tone === "good" ? "good" : tone === "waiting" ? "waiting" : "neutral"}>
          <Dot tone={tone} />
          {copy.label}
        </Pill>
        {contact ? <span className="font-mono text-sm text-ink-soft">{contact.handle}</span> : null}
      </div>
      <p className="text-[0.8125rem] leading-snug text-ink-soft">{copy.line}</p>

      {justPaired && status === "active" ? (
        <p role="status" className="rounded-card bg-[#e3ead9] px-3 py-2 font-display text-[1.125rem] leading-snug text-[#3f5a35]">
          Paired. {person.name}’s phone has opted in.
        </p>
      ) : null}

      {shownCode ? <CodeCard enrollment={shownCode} /> : null}

      {status === "pending" && !shownCode ? (
        <p className="text-[0.8125rem] leading-snug text-ink-soft">
          A code made earlier is still waiting; it can’t be shown again. Codes last 10 minutes: after that, ask for a
          new one here.
        </p>
      ) : null}

      {canPair ? (
        <form onSubmit={pair} noValidate className="flex flex-wrap items-end gap-2">
          <div className="min-w-[12rem] flex-1">
            <label htmlFor={`${id}-phone`} className={LABEL}>
              {status === "stopped" ? "Pair a phone again" : "Phone number"}
            </label>
            <input
              id={`${id}-phone`}
              type="tel"
              inputMode="tel"
              autoComplete="off"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="+1 555 555 0100"
              aria-invalid={phoneError ? true : undefined}
              aria-describedby={`${id}-phone-hint${phoneError ? ` ${id}-phone-error` : ""}`}
              className={`${INPUT} font-mono`}
            />
            <p id={`${id}-phone-hint`} className="mt-0.5 text-xs text-ink-faint">
              {PHONE_HINT}
            </p>
            {phoneError ? (
              <p id={`${id}-phone-error`} className={FIELD_ERROR}>
                {phoneError}
              </p>
            ) : null}
          </div>
          <Chip type="submit" tone="accent" disabled={action.kind === "pending"} className="mb-5">
            Get a pairing code
          </Chip>
        </form>
      ) : null}

      {status === "active" || status === "pending" ? (
        confirmStop ? (
          <div className="flex flex-wrap items-center gap-2 rounded-card bg-[#f7e4dc]/70 px-3 py-2">
            <span className="text-sm text-ink">Stop texting {contact?.handle}?</span>
            <Chip tone="danger" onClick={() => void stop()} disabled={action.kind === "pending"}>
              Yes, stop
            </Chip>
            <Chip tone="quiet" onClick={() => setConfirmStop(false)}>
              Keep texting
            </Chip>
          </div>
        ) : (
          <Chip tone="quiet" className="-ml-3" onClick={() => setConfirmStop(true)}>
            Stop texting this phone
          </Chip>
        )
      ) : null}

      <ActionLine state={action} />
    </div>
  );
}

/** The pairing code, large: read out or shown on stage, the phone texts it to opt in. */
function CodeCard(props: { enrollment: Enrollment }) {
  const { enrollment } = props;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const expiry = expiryCopy(enrollment.expiresAt, now);
  return (
    <div className="rounded-card border border-dashed border-accent/60 bg-paper px-4 py-3">
      <p className="kicker">Text this from the phone</p>
      <p className="mt-1.5 font-mono text-[1.375rem] leading-snug font-bold break-all text-ink select-all">
        LINK {enrollment.code}
      </p>
      <p className="mt-2 text-[0.8125rem] leading-snug text-ink-soft">{enrollment.instruction}</p>
      {expiry.text ? (
        <p className={`mt-1.5 text-[0.8125rem] font-semibold ${expiry.expired ? "text-[#9c3b2a]" : "text-ink-soft"}`}>
          {expiry.text}
        </p>
      ) : null}
      <p role="status" aria-live="polite" className="mt-1.5 inline-flex items-center gap-2 text-[0.8125rem] text-ink-faint">
        <span aria-hidden="true" className="block h-0 w-5 border-t-[1.5px] border-dashed border-accent motion-safe:animate-pulse" />
        Watching for the phone’s reply…
      </p>
    </div>
  );
}

function QuestionForm(props: { api: StitchApi; family: FamilySummary; person: Person }) {
  const { api, family, person } = props;
  const fid = family.id;
  const own = family.personId === person.id;
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<ActionState>(IDLE);
  const [questions, setQuestions] = useState<Question[] | null>(null);
  const [listTry, setListTry] = useState(0);
  const id = useId();

  useEffect(() => {
    if (!own) return;
    let live = true;
    api.getQuestions(fid).then(
      (r) => {
        if (live) setQuestions(r.questions.filter((q) => q.personId === person.id));
      },
      () => {
        if (live) setQuestions(null);
      },
    );
    return () => {
      live = false;
    };
  }, [api, fid, own, person.id, listTry]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = text.trim();
    if (!q) {
      setError("Write the question first.");
      return;
    }
    if (q.length > MAX_QUESTION) {
      setError(`Keep it to ${MAX_QUESTION} characters.`);
      return;
    }
    setError(null);
    setAction({ kind: "pending", label: "Saving the question…" });
    try {
      const saved = await api.createQuestion(fid, { personId: person.id, text: q });
      setText("");
      setAction({ kind: "done", text: `This week’s question for ${person.name}: “${saved.text}”` });
      setListTry((n) => n + 1);
    } catch (err) {
      setAction({
        kind: "error",
        text: isApiError(err) && err.status !== 0 && err.detail ? err.detail : "The question wasn’t saved. Check the connection and try again.",
      });
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-2 border-t border-rule pt-3">
      <label htmlFor={`${id}-q`} className={LABEL}>
        Question for {person.name}
      </label>
      <div className="flex flex-wrap items-end gap-2">
        <input
          id={`${id}-q`}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="How did you and Grace meet?"
          maxLength={MAX_QUESTION + 50}
          autoComplete="off"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-q-error` : undefined}
          className={`${INPUT} min-w-[12rem] flex-1`}
        />
        <Chip type="submit" tone="accent" disabled={action.kind === "pending"}>
          Set as this week’s question
        </Chip>
      </div>
      {error ? (
        <p id={`${id}-q-error`} className={FIELD_ERROR}>
          {error}
        </p>
      ) : null}
      <ActionLine state={action} />
      {own && questions && questions.length > 0 ? (
        <div>
          <p className="kicker mt-1">Open questions for you</p>
          <ul className="mt-1 max-h-[6.5rem] list-none space-y-0.5 overflow-y-auto p-0 text-[0.8125rem] text-ink-soft">
            {questions.map((q) => (
              <li key={q.id} className="truncate">
                “{q.text}”
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </form>
  );
}

// Whether the server has the route does not depend on the person: ask once per
// family and page load (a "no" stays until the page is reloaded). An unknown
// answer is not kept, so the next person asks again.
const probes = new Map<string, Promise<"available" | "unavailable" | "unknown">>();
function probeOnce(fid: string, personId: string) {
  let p = probes.get(fid);
  if (!p) {
    p = probePromptNow(API_BASE_URL, fid, personId).then((r) => {
      if (r === "unknown") probes.delete(fid);
      return r;
    });
    probes.set(fid, p);
  }
  return p;
}

function SendNow(props: { fid: string; person: Person; active: boolean }) {
  const { fid, person, active } = props;
  const [available, setAvailable] = useState<"available" | "unavailable" | "unknown" | "checking">("checking");
  const [action, setAction] = useState<ActionState>(IDLE);

  useEffect(() => {
    let live = true;
    probeOnce(fid, person.id).then((r) => {
      if (live) setAvailable(r);
    });
    return () => {
      live = false;
    };
  }, [fid, person.id]);

  async function send() {
    setAction({ kind: "pending", label: `Sending the question to ${person.name}’s phone…` });
    const r = await promptNow(API_BASE_URL, fid, person.id);
    if (r.kind === "unavailable") {
      probes.set(fid, Promise.resolve("unavailable"));
      setAvailable("unavailable");
      setAction(IDLE);
    } else if (r.kind === "sent") setAction({ kind: "done", text: `Sent. ${person.name}’s phone has the question.` });
    else if (r.kind === "blocked") setAction({ kind: "error", text: `Not sent: ${r.reason}` });
    else setAction({ kind: "error", text: r.message });
  }

  const off = available === "unavailable";
  return (
    <div className="space-y-1.5 border-t border-rule pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <Chip
          tone="accent"
          disabled={off || !active || available === "checking" || action.kind === "pending"}
          onClick={() => void send()}
        >
          Send it now
        </Chip>
        {!off && !active ? <span className="text-[0.8125rem] text-ink-soft">Pair {person.name}’s phone first.</span> : null}
      </div>
      {off ? <p className="text-[0.8125rem] leading-snug text-ink-soft">{PROMPT_NOW_UNAVAILABLE}</p> : null}
      <ActionLine state={action} />
    </div>
  );
}
