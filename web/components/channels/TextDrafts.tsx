"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "@/lib/api";
import { Button, ButtonLink } from "@/components/flow/Button";
import { FormError } from "@/components/flow/FormError";
import { createTextDraftsClient, TextDraftsError, type TextDraft } from "./text-drafts";

const listDrafts = createTextDraftsClient(API_BASE_URL);

export function TextDrafts({ familyId, personId }: { familyId: string; personId: string }) {
  const [drafts, setDrafts] = useState<TextDraft[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const refresh = useRef<() => void>(() => {});

  useEffect(() => {
    let live = true;
    let active: AbortController | null = null;
    let failures = 0;
    let denied = false;
    async function load(manual = false) {
      if (active || denied || document.visibilityState !== "visible" || (!manual && failures >= 3)) return;
      const controller = new AbortController(); active = controller;
      const timeout = setTimeout(() => controller.abort(), 10000);
      setBusy(true);
      try {
        const result = await listDrafts(familyId, personId, controller.signal);
        if (!live) return;
        setDrafts(result); setError(""); failures = 0;
      } catch (cause) {
        if (!live) return;
        setDrafts(null);
        setNotice("");
        denied = cause instanceof TextDraftsError && [401, 403, 404].includes(cause.status);
        failures++;
        setError(denied
          ? "Access to these drafts has ended. Sign in again and reload this page to continue."
          : "Your text drafts could not be refreshed. Check your connection and sign-in, then try Refresh drafts.");
      } finally {
        clearTimeout(timeout); active = null;
        if (live) setBusy(false);
      }
    }
    refresh.current = () => void load(true);
    const visible = () => { if (document.visibilityState === "visible") void load(true); };
    void load();
    const timer = setInterval(() => void load(), 5000);
    document.addEventListener("visibilitychange", visible);
    return () => {
      live = false; active?.abort(); clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
      refresh.current = () => {};
    };
  }, [familyId, personId]);

  return <section id="text-drafts" aria-labelledby="text-drafts-title" className="space-y-5 border-t border-rule pt-7">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 id="text-drafts-title" className="font-display text-2xl">Your text drafts</h2>
      <Button variant="secondary" busy={busy} busyLabel="Refreshing…" onClick={() => refresh.current()}>Refresh drafts</Button>
    </div>
    <p className="text-sm leading-relaxed text-ink-soft">Your story replies are saved here as private drafts. Just text your story—no special commands are needed to save it. A draft is not an animated memory yet.</p>
    {error && <FormError message={error} />}
    {!drafts && !error && <p role="status" className="text-sm text-ink-soft">Loading your drafts…</p>}
    {drafts?.length === 0 && <p className="text-sm text-ink-soft">No text drafts yet. Reply to your Stitch conversation to begin.</p>}
    {drafts?.map(draft => <article key={draft.id} className="space-y-3 border-l-2 border-accent pl-4">
      <p className="text-xs text-ink-soft"><time dateTime={draft.createdAt}>{new Date(draft.createdAt).toLocaleString()}</time></p>
      <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{draft.text}</p>
      <Button variant="quiet" onClick={async () => {
        try {
          await navigator.clipboard.writeText(draft.text);
          setNotice("Draft copied. Open Add a memory and paste it into your story.");
        } catch {
          setNotice("Select the draft text above and copy it, then paste it into Add a memory.");
        }
      }}>Copy draft</Button>
    </article>)}
    <p className="text-sm text-ink-soft">To make a memory, copy a draft and paste it into Add a memory. You can review it before submitting.</p>
    <ButtonLink variant="secondary" href={`/add?family=${encodeURIComponent(familyId)}`}>Add a memory</ButtonLink>
    <div aria-live="polite">{notice && <p role="status" className="text-sm text-ink-soft">{notice}</p>}</div>
  </section>;
}
