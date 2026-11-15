"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { API_BASE_URL, isApiError } from "@/lib/api";
import type { FamilySummary, Person } from "@/lib/types";
import { SessionGate, useSession } from "@/components/flow/SessionGate";
import { FlowPage } from "@/components/flow/FlowPage";
import { Button } from "@/components/flow/Button";
import { SelectField, TextField } from "@/components/flow/Field";
import { FormError } from "@/components/flow/FormError";
import { ChannelSettingsError, createSettingsClient } from "./client";
import { CallInterviews } from "./CallInterviews";
import { TextDrafts } from "./TextDrafts";
import { preferenceError, preferencesFor, statusDescription, storedTestPromptRequestId, validHandle } from "./settings";
import type { ChannelContact, Enrollment, Preferences } from "./settings";

const client = createSettingsClient(API_BASE_URL);
const section = "space-y-5 border-t border-rule pt-6";

export function NotificationSettings() {
  return <SessionGate requireFamily width="wide"><SettingsScreen /></SessionGate>;
}

function SettingsScreen() {
  const { me } = useSession();
  const search = useSearchParams();
  const router = useRouter();
  const requested = search.get("family");
  const family = requested ? me.families.find(f => f.id === requested) : me.families[0];
  return <FlowPage title="Stories by message & phone" width="wide"
    back={{ href: family ? `/families/${encodeURIComponent(family.id)}/settings` : "/", label: "Family settings" }}
    lede="Let family stories begin with a message. Each person chooses whether to take part.">
    {me.families.length > 1 && <div className="mb-7"><SelectField label="Family" value={family?.id ?? ""}
      onChange={e => router.replace(`/settings/notifications?family=${encodeURIComponent(e.target.value)}`)}>
      {!family && <option value="">Choose a family</option>}
      {me.families.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
    </SelectField></div>}
    {family ? <FamilyContacts key={family.id} family={family} /> : <FormError message="This family is not available in your account." />}
  </FlowPage>;
}

function FamilyContacts({ family }: { family: FamilySummary }) {
  const { api, me, refresh } = useSession();
  const [people, setPeople] = useState<Person[] | null>(null);
  const [error, setError] = useState("");
  const [personId, setPersonId] = useState(family.personId ?? "");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    api.getTree(family.id).then(tree => {
      if (!live) return;
      const eligible = tree.people.filter(p => p.familyId === family.id && (family.role === "admin" || p.userId === me.user.id));
      setPeople(eligible);
      setPersonId(previous => eligible.some(p => p.id === previous) ? previous : eligible[0]?.id ?? "");
    }).catch(e => {
      if (!live) return;
      setError("The family could not be loaded. Refresh your session or try again.");
      if (isApiError(e) && (e.status === 401 || e.status === 403)) void refresh().catch(() => {});
    });
    return () => { live = false; };
  }, [api, family.id, family.role, me.user.id, attempt, refresh]);
  if (error) return <div className="space-y-4"><FormError message={error} /><Button variant="secondary" onClick={() => { setError(""); setAttempt(n => n + 1); }}>Try again</Button><Button variant="quiet" onClick={() => void refresh().catch(() => {})}>Refresh session</Button></div>;
  if (!people) return <p role="status" className="text-ink-soft">Loading family…</p>;
  if (!people.length) return <p className="text-ink-soft">Claim your place in the family tree before connecting a messaging account.</p>;
  const person = people.find(p => p.id === personId);
  return <div className="space-y-7">
    {family.role === "admin" ? <SelectField label="Whose stories?" value={personId} onChange={e => setPersonId(e.target.value)}>
      {people.map(p => <option key={p.id} value={p.id}>{p.name}{p.userId === me.user.id ? " (you)" : ""}</option>)}
    </SelectField> : <p className="font-display text-xl">{person?.name}’s messaging account</p>}
    {person && <ContactPanel key={person.id} familyId={family.id} person={person} />}
    {person?.userId === me.user.id && <TextDrafts key={`${family.id}:${person.id}`} familyId={family.id} personId={person.id} />}
    {person?.userId === me.user.id && <CallInterviews familyId={family.id} personId={person.id} personName={person.name} />}
  </div>;
}

function ContactPanel({ familyId, person }: { familyId: string; person: Person }) {
  const { refresh } = useSession();
  const [contact, setContact] = useState<ChannelContact | null | undefined>(undefined);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    client.get(familyId, person.id, controller.signal).then(c => {
      if (!controller.signal.aborted) { setContact(c); setLoading(false); }
    }).catch(e => {
      if (controller.signal.aborted) return;
      setLoading(false);
      setError(e instanceof ChannelSettingsError ? e.message : "Contact settings could not be loaded.");
      if (e instanceof ChannelSettingsError && (e.status === 401 || e.status === 403)) void refresh().catch(() => {});
    });
    return () => controller.abort();
  }, [familyId, person.id, revision, refresh]);
  function reload() { setError(""); setLoading(true); setContact(undefined); setRevision(n => n + 1); }
  if (loading) return <p role="status" className="text-ink-soft">Loading contact settings…</p>;
  if (error) return <div className="space-y-4"><FormError message={error} /><Button variant="secondary" onClick={reload}>Refresh contact</Button><Button variant="quiet" onClick={() => void refresh().catch(() => {})}>Refresh session</Button></div>;
  return <ContactForm key={revision} familyId={familyId} person={person} initial={contact ?? null} reload={reload} />;
}

function ContactForm({ familyId, person, initial, reload }: { familyId: string; person: Person; initial: ChannelContact | null; reload: () => void }) {
  const { refresh, me } = useSession();
  const [contact, setContact] = useState(initial);
  const [prefs, setPrefs] = useState<Preferences>(() => preferencesFor(initial));
  const [handle, setHandle] = useState(initial?.handle ?? "");
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const request = useRef<AbortController | null>(null);
  const proofHeading = useRef<HTMLParagraphElement | null>(null);
  const errorRegion = useRef<HTMLDivElement | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => { if (enrollment) proofHeading.current?.focus(); }, [enrollment]);
  useEffect(() => { if (error) errorRegion.current?.querySelector<HTMLElement>("[role=alert]")?.focus(); }, [error]);

  async function mutate(kind: "enroll" | "save" | "stop") {
    if (lock.current) return;
    const invalid = kind !== "stop" ? preferenceError(prefs) : null;
    if (invalid) { setError(invalid); return; }
    if (kind === "enroll" && !validHandle(handle.trim())) { setError("Enter an international phone number, such as +12125550123, or an iMessage email address."); return; }
    lock.current = true; setBusy(true); setError(""); setNotice("");
    const controller = new AbortController(); request.current = controller;
    try {
      if (kind === "enroll") {
        const result = await client.enroll(familyId, person.id, handle.trim(), prefs, controller.signal);
        if (controller.signal.aborted) return;
        setContact(result.contact); setHandle(result.contact.handle); setEnrollment(result);
        setNotice("Code created. No message has been sent.");
      } else {
        const result = kind === "stop" ? await client.stop(familyId, person.id, controller.signal) : await client.save(familyId, person.id, prefs, controller.signal);
        if (controller.signal.aborted) return;
        setContact(result); setPrefs(preferencesFor(result));
        if (kind === "stop") setEnrollment(null);
        setNotice(kind === "stop" ? "Messages stopped. Any pending verification code is invalid." : "Preferences saved.");
      }
    } catch (e) {
      if (controller.signal.aborted) return;
      setError(e instanceof ChannelSettingsError ? e.message : "The change could not be confirmed. Refresh the contact before trying again.");
      if (e instanceof ChannelSettingsError && (e.status === 401 || e.status === 403)) void refresh().catch(() => {});
    } finally {
      if (!controller.signal.aborted) { setBusy(false); lock.current = false; }
    }
  }

  const stopped = contact?.status === "stopped";
  const scheduled = process.env.NEXT_PUBLIC_STITCH_SCHEDULER === "1" && contact?.status === "active" && contact.provider === "imessage";
  const savedSchedule = preferencesFor(contact);
  return <div className="space-y-7">
    <section aria-label="Messaging consent" className={section}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="font-display text-2xl">{contact ? "Messaging consent" : "Connect an iMessage account"}</h2>
        {contact && <span className="rounded-paper bg-paper-deep px-2.5 py-1 text-sm font-semibold">{contact.status === "pending" ? "Waiting for opt-in" : contact.status === "active" ? "Opted in" : "Stopped"}</span>}
      </div>
      {contact && <><p className="break-all font-medium">{contact.handle}</p><p className="text-sm leading-relaxed text-ink-soft">{statusDescription(contact.status)}</p></>}
      {!contact && <p className="text-sm leading-relaxed text-ink-soft">Use {person.name}’s phone number or iMessage email. Creating a code does not send an invitation or opt them in.</p>}
      {!contact && <TextField label="Phone number or iMessage email" value={handle} onChange={e => setHandle(e.target.value)} autoComplete="off" maxLength={254} disabled={busy} placeholder="+12125550123" />}
      {enrollment && contact?.status === "pending" && <div className="space-y-3 border-l-2 border-accent pl-4">
        <p ref={proofHeading} tabIndex={-1} className="font-semibold outline-none">Share these instructions with {person.name}</p>
        <p className="text-sm leading-relaxed text-ink-soft">From <strong>{contact.handle}</strong>, send this to your family’s Stitch iMessage conversation to verify the account and agree to receive story messages. Ask the family organizer for the Stitch address.</p>
        <code className="block select-all break-all rounded-paper bg-paper-deep p-3 text-sm">LINK {enrollment.code}</code>
        <Button variant="secondary" onClick={() => {
          void navigator.clipboard?.writeText(`LINK ${enrollment.code}`).then(() => setNotice("LINK message copied. Share it directly with the recipient."))
            .catch(() => setNotice("Select the LINK message above to copy it manually."));
          if (!navigator.clipboard) setNotice("Select the LINK message above to copy it manually.");
        }}>Copy LINK message</Button>
        <p className="text-sm text-ink-soft">Expires {new Date(enrollment.expiresAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZoneName: "short" })}. Send STOP at any time to opt out.</p>
        <p className="text-sm text-ink-soft">This code is shown only here. Refreshing the page hides it; an unused code can be replaced after it expires.</p>
      </div>}
      {contact?.status === "pending" && !enrollment && <p className="text-sm text-ink-soft">Use the instructions shared when the code was created. If it was lost, wait for its 10-minute expiry before creating another.</p>}
      {contact && <div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={busy} onClick={reload}>Refresh status</Button>
        {!stopped && <Button variant="quiet" busy={busy} onClick={() => void mutate("stop")}>Stop messages</Button>}
      </div>}
    </section>

    {person.userId === me.user.id && contact?.status === "active" && contact.provider === "imessage" &&
      <TestPrompt familyId={familyId} personId={person.id} handle={contact.handle} disabled={busy} />}
    <form className={section} aria-busy={busy} onSubmit={e => { e.preventDefault(); void mutate(contact && !stopped ? "save" : "enroll"); }}>
      <div><h2 className="font-display text-2xl">A comfortable rhythm</h2><p className="mt-2 text-sm leading-relaxed text-ink-soft">{scheduled
        ? "Saved preferences control your active story-prompt schedule. Saving does not send a message immediately."
        : "Save preferences for story prompts. Saving these does not turn on a schedule or send a message."}</p></div>
      {scheduled && <p role="status" className="border-l-2 border-accent pl-4 text-sm leading-relaxed text-ink-soft">
        Story prompts are scheduled for {contact.handle} every {savedSchedule.cadenceDays} {savedSchedule.cadenceDays === 1 ? "day" : "days"} while the demo server is running.
        {" "}Quiet hours are {savedSchedule.quietHours.start}–{savedSchedule.quietHours.end} ({savedSchedule.timezone}). Delivery depends on the messaging connection.
      </p>}
      <div className="grid gap-5 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]"><TextField label="Time zone" hint="For example, America/New_York." value={prefs.timezone} disabled={busy}
        onChange={e => setPrefs(p => ({ ...p, timezone: e.target.value }))} required />
      <TextField label="Days between prompts" hint="Between 1 and 30 days." type="number" min={1} max={30} step={1} value={Number.isNaN(prefs.cadenceDays) ? "" : prefs.cadenceDays} disabled={busy}
        onChange={e => setPrefs(p => ({ ...p, cadenceDays: e.target.valueAsNumber }))} required /></div>
      <fieldset><legend className="mb-3 text-sm font-semibold">Quiet hours · in this time zone</legend><div className="grid grid-cols-2 gap-4">
        <TextField label="From" type="time" value={prefs.quietHours.start} disabled={busy} required onChange={e => setPrefs(p => ({ ...p, quietHours: { ...p.quietHours, start: e.target.value } }))} />
        <TextField label="Until" type="time" value={prefs.quietHours.end} disabled={busy} required onChange={e => setPrefs(p => ({ ...p, quietHours: { ...p.quietHours, end: e.target.value } }))} />
      </div><p className="mt-2 text-sm text-ink-soft">Quiet hours can continue past midnight.</p></fieldset>
      {stopped && <p className="text-sm leading-relaxed text-ink-soft">Requesting new consent creates a fresh code for this same account. Messages stay off until the recipient verifies and opts in again.</p>}
      <Button type="submit" busy={busy} busyLabel="Saving…">{stopped ? "Request new consent" : contact ? "Save preferences" : "Create opt-in code"}</Button>
      {contact?.status === "pending" && !enrollment && <Button className="ml-2" variant="quiet" disabled={busy} onClick={() => void mutate("enroll")}>Create replacement code</Button>}
    </form>
    <div aria-live="polite">{notice && <p role="status" className="text-sm text-ink-soft">{notice}</p>}</div>
    {error && <div ref={errorRegion} className="space-y-3"><FormError message={error} /><div className="flex flex-wrap gap-2"><Button variant="secondary" disabled={busy} onClick={reload}>Refresh contact</Button><Button variant="quiet" disabled={busy} onClick={() => void refresh().catch(() => {})}>Refresh session</Button></div></div>}
  </div>;
}

function TestPrompt({ familyId, personId, handle, disabled }: { familyId: string; personId: string; handle: string; disabled: boolean }) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<"sent" | "pending" | "uncertain" | null>(null);
  const [message, setMessage] = useState("");
  const lock = useRef(false);
  const requestId = useRef<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const storageKey = `stitch:test-prompt:${encodeURIComponent(familyId)}:${encodeURIComponent(personId)}`;
  useEffect(() => () => controller.current?.abort(), []);

  async function send() {
    if (lock.current || disabled || status === "sent") return;
    lock.current = true;
    setBusy(true);
    setMessage("");
    try {
      // Keep the same operation across retries and refreshes, including a lost response.
      if (!requestId.current) {
        requestId.current = storedTestPromptRequestId(sessionStorage, storageKey, () => crypto.randomUUID());
      }
    } catch {
      setMessage("Your browser could not keep track of this request. No message was sent. Enable session storage and try again.");
      setBusy(false); lock.current = false;
      return;
    }
    const abort = new AbortController(); controller.current = abort;
    try {
      const result = await client.testPrompt(familyId, personId, requestId.current, abort.signal);
      if (abort.signal.aborted) return;
      setStatus(result.status);
      setMessage(result.status === "sent"
        ? "The messaging service accepted your prompt. Check your phone; delivery is not yet confirmed."
        : result.status === "pending"
          ? "This request is still being processed. Check its status below; this will not send another prompt."
          : "Delivery could not be confirmed. Check your phone. This request will not be resent automatically; contact the demo organizer if it is missing.");
    } catch (error) {
      if (abort.signal.aborted) return;
      setStatus("uncertain");
      const code = error instanceof ChannelSettingsError ? error.status : 0;
      setMessage(code === 403 ? "Only your own verified, opted-in account can receive a test. Refresh your contact settings."
        : code === 409 ? "Another message may still be processing. Check your phone or ask the demo organizer before trying again."
        : code === 503 ? "Test messaging is unavailable. Ask the demo organizer to check the connection."
        : code === 401 ? "Sign in again to check this request."
        : "The result could not be confirmed. Check your phone or check this same request again; a new prompt will not be sent.");
    } finally {
      if (!abort.signal.aborted) { setBusy(false); lock.current = false; }
    }
  }

  return <section className={section} aria-labelledby="test-prompt-title">
    <div><h2 id="test-prompt-title" className="font-display text-2xl">Try a story prompt</h2>
      <p className="mt-2 text-sm leading-relaxed text-ink-soft">Sends one prompt now to your connected account, {handle}, even during quiet hours. This does not start an automatic schedule.</p></div>
    <Button variant="secondary" disabled={disabled || status === "sent"} busy={busy} busyLabel="Checking…" onClick={() => void send()}>
      {status === "sent" ? "Prompt sent" : status ? "Check this request" : "Send test prompt"}
    </Button>
    <div aria-live="polite">{message && <p role="status" className="text-sm leading-relaxed text-ink-soft">{message}</p>}</div>
  </section>;
}
