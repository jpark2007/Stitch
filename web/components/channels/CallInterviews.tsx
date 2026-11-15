"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "@/lib/api";
import { Button, ButtonLink, FOCUS_RING } from "@/components/flow/Button";
import { FormError } from "@/components/flow/FormError";
import { CallSettingsError, createCallsClient } from "./calls-client";
import type { CallDraft, CallPairing } from "./calls-client";

const client = createCallsClient(API_BASE_URL);
const errorMessage = (e: unknown) => e instanceof CallSettingsError ? e.message : "We could not confirm that request. Please try again.";

export function CallInterviews(props: { familyId: string; personId: string; personName: string }) {
  if (process.env.NEXT_PUBLIC_STITCH_CALL_AUTO === "1") {
    const phoneNumber = process.env.NEXT_PUBLIC_STITCH_CALL_NUMBER || "+12292875827";
    return (
      <section aria-labelledby="call-interviews-title" className="space-y-5 border-t border-rule pt-7">
        <div>
          <p className="mb-2 text-sm font-semibold uppercase tracking-wider text-accent-deep">Your voice, your story</p>
          <h2 id="call-interviews-title" className="font-display text-2xl">Call interviews</h2>
        </div>
        <p className="text-ink-soft">Call and tell your story. In this demo, calls attach to Rick Macosko’s story.</p>
        <ButtonLink href={`tel:${phoneNumber}`}>Call {phoneNumber}</ButtonLink>
        <p className="text-ink-soft">After hanging up, open Add a memory while signed in as Rick to continue your story.</p>
        <ButtonLink variant="secondary" href={`/add?family=${encodeURIComponent(props.familyId)}`}>Add a memory</ButtonLink>
      </section>
    );
  }
  // Changing identity destroys every local secret, edit and in-flight request.
  return <CallPanel key={`${props.familyId}:${props.personId}`} {...props} />;
}

function CallPanel({ familyId, personId, personName }: { familyId: string; personId: string; personName: string }) {
  const [pairing, setPairing] = useState<CallPairing | null>(null);
  const [drafts, setDrafts] = useState<CallDraft[] | null>(null);
  const [selected, setSelected] = useState<CallDraft | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const request = useRef<AbortController | null>(null);
  const lock = useRef(false);
  const editor = useRef<HTMLTextAreaElement>(null);
  const refreshButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const c = new AbortController(); request.current = c;
    client.list(familyId, personId, c.signal).then(result => {
      if (!c.signal.aborted) setDrafts(result);
    }).catch(e => {
      if (!c.signal.aborted) { setError(errorMessage(e)); setUnavailable(e instanceof CallSettingsError && [403, 404].includes(e.status)); }
    });
    return () => { c.abort(); request.current?.abort(); };
  }, [familyId, personId]);
  useEffect(() => {
    if (!pairing) return;
    const expire = () => { setPairing(null); setNotice("Your call code has expired. Create a new one before calling."); };
    const timer = setTimeout(expire, Math.max(0, Date.parse(pairing.expiresAt) - Date.now()));
    return () => clearTimeout(timer);
  }, [pairing]);
  useEffect(() => { if (selected) editor.current?.focus(); }, [selected]);

  async function act(kind: "pair" | "refresh" | "open" | "delete", id?: string) {
    if (lock.current) return;
    if ((kind === "open" || kind === "refresh") && selected && text !== selected.text
        && !window.confirm("Discard your unsaved edits to this transcript?")) return;
    lock.current = true; setBusy(true); setError(""); setNotice("");
    request.current?.abort();
    const c = new AbortController(); request.current = c;
    if (kind === "pair") setPairing(null); // Rotation can succeed even if its response is lost.
    try {
      if (kind === "pair") {
        const result = await client.pair(familyId, personId, c.signal);
        if (!c.signal.aborted) { setPairing(result); setNotice("Your private call code is ready."); }
      } else if (kind === "open" && id) {
        const result = await client.get(familyId, personId, id, c.signal);
        if (!c.signal.aborted) { setSelected(result); setText(result.text); setConfirmDelete(false); }
      } else if (kind === "delete" && selected) {
        await client.remove(familyId, selected.id, c.signal);
        if (!c.signal.aborted) { setDrafts(d => d?.filter(item => item.id !== selected.id) ?? []); setSelected(null); setText(""); setConfirmDelete(false); setNotice("Call draft deleted."); }
      } else {
        const result = await client.list(familyId, personId, c.signal);
        if (!c.signal.aborted) { setDrafts(result); setSelected(null); setText(""); setConfirmDelete(false); setNotice("Call drafts refreshed."); }
      }
      if (!c.signal.aborted) setUnavailable(false);
    } catch (e) {
      if (!c.signal.aborted) {
        setError(errorMessage(e));
        if (e instanceof CallSettingsError && [401, 403, 404].includes(e.status)) {
          setPairing(null); setSelected(null); setText(""); setDrafts(null); setUnavailable(true);
        }
      }
    } finally { if (!c.signal.aborted) { lock.current = false; setBusy(false); if (kind === "delete") requestAnimationFrame(() => { if (!c.signal.aborted) refreshButton.current?.focus(); }); } }
  }

  async function copy() {
    const c = request.current;
    try {
      if (!navigator.clipboard) throw new Error();
      await navigator.clipboard.writeText(text);
      if (!c?.signal.aborted) setNotice("Transcript copied. Open Add a memory and paste it into your story.");
    } catch { if (!c?.signal.aborted) { setNotice("Copy was unavailable. Select the transcript and copy it manually."); editor.current?.focus(); editor.current?.select(); } }
  }

  return <section aria-labelledby="call-interviews-title" className="space-y-5 border-t border-rule pt-7">
    <div><p className="mb-2 text-sm font-semibold uppercase tracking-wider text-accent-deep">Your voice, your story</p>
      <h2 id="call-interviews-title" className="font-display text-2xl">Call interviews</h2>
      <p className="mt-2 font-medium">{personName} · linked to your account</p>
      <p className="mt-2 text-sm leading-relaxed text-ink-soft">Tell your story by phone, then review your words here. Calls are separate from iMessage; no messaging opt-in is needed.</p></div>
    {!unavailable && <div className="space-y-4 rounded-paper border border-rule bg-paper-deep p-4 sm:p-5">
      <h3 className="font-display text-xl">{pairing ? "Your code for this call" : "Start with a private code"}</h3>
      {!pairing && <><p className="text-sm leading-relaxed">Create a code to save your spoken words as a private draft. You review it before adding anything to your family book.</p>
        <Button busy={busy} onClick={() => void act("pair")}>Agree and create call code</Button></>}
      {pairing && <div className="space-y-4">
        <p className="text-sm">At the start, say <strong>“STITCH”</strong>, then read these {pairing.code.length === 3 ? "three" : pairing.code.replaceAll("-", "").length === 6 ? "six" : "twelve"} digits <strong>one at a time</strong>:</p>
        <code className="block select-all break-words font-mono text-2xl tracking-wider sm:text-3xl"><span aria-hidden="true">STITCH {pairing.code}</span><span className="sr-only">STITCH {pairing.code.replaceAll("-", "").split("").join(" ")}</span></code>
        <p className="text-sm text-ink-soft">Read the digits in English or Chinese. Then tell your story.</p>
        {pairing.phoneNumber ? <a className={`inline-flex min-h-11 items-center justify-center rounded-card bg-accent-deep px-5 py-2.5 font-semibold text-paper-light ${FOCUS_RING}`} href={`tel:${pairing.phoneNumber}`}>Call {pairing.phoneNumber}</a>
          : <p className="text-sm">Ask your family organizer for the Stitch interview number.</p>}
        <p className="text-sm text-ink-soft">Keep it private · use once · expires {new Date(pairing.expiresAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZoneName: "short" })}.</p>
        <p className="text-sm text-ink-soft">Start your call now and finish before the code expires.</p>
        <p className="text-sm text-ink-soft">After the call, refresh your drafts below. Your words may take a moment to arrive.</p>
        <Button variant="quiet" busy={busy} onClick={() => void act("pair")}>Replace call code</Button>
        <p className="text-sm text-ink-soft">Replacing this code cancels the unused one above.</p>
      </div>}
    </div>}
    <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-display text-xl">Your call drafts</h3><Button ref={refreshButton} variant="secondary" disabled={busy} onClick={() => void act("refresh")}>Refresh drafts</Button></div>
    {!drafts && !error && <p role="status" className="text-sm text-ink-soft">Loading your drafts…</p>}
    {drafts?.length === 0 && <p className="border-l-2 border-rule py-2 pl-4 text-sm leading-relaxed text-ink-soft">No call drafts yet. After a paired call, your words will appear here for you to review.</p>}
    {!!drafts?.length && <ul className="divide-y divide-rule">{drafts.map(d => <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
      <time dateTime={d.createdAt} className="text-sm">{new Date(d.createdAt).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</time>
      <Button variant="quiet" disabled={busy} aria-expanded={selected?.id === d.id} aria-label={`Review transcript from ${new Date(d.createdAt).toLocaleString()}`} onClick={() => void act("open", d.id)}>Review transcript</Button>
    </li>)}</ul>}
    {selected && <div className="space-y-4 rounded-paper border border-rule p-4 sm:p-5">
      <div><label htmlFor="call-transcript" className="font-display text-xl">Review your words</label><p id="call-transcript-hint" className="mt-2 text-sm text-ink-soft">Only your side of the conversation. Correct any transcription mistakes before copying. Edits stay on this page; the original draft is unchanged.</p></div>
      <textarea ref={editor} id="call-transcript" dir="auto" aria-describedby="call-transcript-hint" value={text} disabled={busy} onChange={e => { setText(e.target.value); setNotice(""); }} rows={9} className={`w-full resize-y rounded-paper border border-ink-faint bg-paper-light p-3 leading-relaxed ${FOCUS_RING}`} />
      <div className="flex flex-wrap gap-3"><Button disabled={busy || !text.trim()} onClick={() => void copy()}>Copy transcript</Button><ButtonLink variant="secondary" href={`/add?family=${encodeURIComponent(familyId)}`}>Open Add a memory</ButtonLink></div>
      <p className="text-sm text-ink-soft">Paste your copied words into Add a memory, choose the year, and review before submitting. This draft has not been made into a book page.</p>
      {confirmDelete ? <div className="space-y-3 border-t border-rule pt-4"><p>Delete this call draft? This cannot be undone, and your local edits will be cleared.</p><div className="flex flex-wrap gap-2"><Button variant="danger" busy={busy} onClick={() => void act("delete")}>Yes, delete draft</Button><Button variant="quiet" disabled={busy} onClick={() => setConfirmDelete(false)}>Keep draft</Button></div></div>
        : <Button variant="quiet" disabled={busy} onClick={() => setConfirmDelete(true)}>Delete call draft</Button>}
    </div>}
    <div aria-live="polite">{notice && <p role="status" className="text-sm text-ink-soft">{notice}</p>}</div>
    {error && <FormError message={error} />}
  </section>;
}
