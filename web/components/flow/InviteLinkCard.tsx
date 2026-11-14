"use client";

// The invite link card on family settings. Presentational: the page passes
// the live invite (pickActiveInvite) and the create and revoke calls, and
// this card shows the link, copies it, says how long it has left and turns it
// off after an inline confirm. A rejected call shows its message in the card.
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "./Button";
import { TextField } from "./Field";
import { FormError } from "./FormError";
import { PaperCard } from "./PaperCard";
import { describeExpiry, inviteUrl } from "./invites";

type FocusTarget = "create" | "link" | "turnOff" | "confirm";

const COPIED = "Link copied";
const COPY_BY_HAND = "Press Ctrl+C / ⌘C to copy";

function messageOf(err: unknown): string {
  if (err instanceof Error && err.message.trim()) return err.message;
  return "Something went wrong. Please try again.";
}

export function InviteLinkCard(props: {
  invite: { code: string; expiresAt: string } | null;
  origin: string;
  now?: Date;
  onCreate: () => Promise<void>;
  onRevoke: (code: string) => Promise<void>;
}) {
  const { invite, origin, now, onCreate, onRevoke } = props;
  const uid = useId();
  const headingId = `${uid}heading`;
  const questionId = `${uid}question`;

  const [busy, setBusy] = useState<"create" | "revoke" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [copyMessage, setCopyMessage] = useState("");

  const createRef = useRef<HTMLButtonElement>(null);
  const linkRef = useRef<HTMLInputElement>(null);
  const turnOffRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLDivElement>(null);
  // Where focus goes once the next render has the element (a button that
  // was busy, or the link that replaces the create button).
  const pendingFocus = useRef<FocusTarget | null>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const target = pendingFocus.current;
    if (!target) return;
    const el = {
      create: createRef.current,
      link: linkRef.current,
      turnOff: turnOffRef.current,
      confirm: confirmRef.current,
    }[target];
    if (el && !(el instanceof HTMLButtonElement && el.disabled)) {
      el.focus();
      pendingFocus.current = null;
    }
  });

  useEffect(
    () => () => {
      if (copyTimer.current) clearTimeout(copyTimer.current);
    },
    [],
  );

  const code = invite?.code ?? null;
  const url = code ? inviteUrl(origin, code) : "";
  const expiry = invite ? describeExpiry(invite.expiresAt, now ?? new Date()) : "";

  async function create() {
    if (busy) return;
    setError(null);
    setBusy("create");
    try {
      await onCreate();
      pendingFocus.current = "link";
    } catch (err) {
      setError(messageOf(err));
      pendingFocus.current = "create";
    } finally {
      setBusy(null);
    }
  }

  async function revoke() {
    if (busy || !code) return;
    setError(null);
    setBusy("revoke");
    try {
      await onRevoke(code);
      setConfirming(false);
      setCopyMessage("");
      pendingFocus.current = "create";
    } catch (err) {
      setError(messageOf(err));
      setConfirming(false);
      pendingFocus.current = "turnOff";
    } finally {
      setBusy(null);
    }
  }

  function showCopyMessage(message: string, clearAfterMs: number | null) {
    if (copyTimer.current) clearTimeout(copyTimer.current);
    copyTimer.current = null;
    setCopyMessage(message);
    if (clearAfterMs !== null) {
      copyTimer.current = setTimeout(() => setCopyMessage(""), clearAfterMs);
    }
  }

  function selectLink() {
    const input = linkRef.current;
    if (!input) return;
    input.focus();
    input.select();
  }

  async function copy() {
    if (!url) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("no clipboard");
      await navigator.clipboard.writeText(url);
      showCopyMessage(COPIED, 4000);
    } catch {
      selectLink();
      showCopyMessage(COPY_BY_HAND, null);
    }
  }

  function openConfirm() {
    setError(null);
    setConfirming(true);
    pendingFocus.current = "confirm";
  }

  function cancelConfirm() {
    setConfirming(false);
    pendingFocus.current = "turnOff";
  }

  return (
    <PaperCard aria-labelledby={headingId}>
      <h2 id={headingId} className="font-serif text-[1.5rem] leading-tight text-[var(--ink,#2f2620)]">
        Invite your family
      </h2>
      <p className="mt-2 text-[0.9375rem] leading-relaxed text-pretty text-[var(--ink-soft,#5c4e43)]">
        Anyone with the link can join the family for 14 days, and you can turn it off at any time.
      </p>

      <div className="mt-6 flex flex-col gap-5">
        {error ? <FormError message={error} /> : null}

        {!invite ? (
          <div>
            <Button ref={createRef} busy={busy === "create"} busyLabel="Making a link…" onClick={create}>
              Make an invite link
            </Button>
          </div>
        ) : (
          <>
            <div>
              <TextField
                ref={linkRef}
                label="Invite link"
                hint={expiry || undefined}
                type="text"
                readOnly
                value={url}
                spellCheck={false}
                autoComplete="off"
                onFocus={(e) => e.currentTarget.select()}
                onClick={(e) => {
                  const el = e.currentTarget;
                  if (el.selectionStart === el.selectionEnd) el.select();
                }}
                className="truncate text-[0.9375rem]"
              />
              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
                <Button variant="secondary" onClick={copy}>
                  Copy link
                </Button>
                <p
                  role="status"
                  aria-live="polite"
                  className="min-h-[1.5em] text-[0.9375rem] leading-snug text-[var(--ink-soft,#5c4e43)]"
                >
                  {copyMessage === COPIED ? (
                    <span className="inline-flex items-center gap-1.5">
                      <Tick />
                      {copyMessage}
                    </span>
                  ) : (
                    copyMessage
                  )}
                </p>
              </div>
            </div>

            <div className="border-t border-dashed border-[var(--rule,#2f262024)] pt-5">
              {confirming ? (
                <div
                  ref={confirmRef}
                  role="group"
                  aria-labelledby={questionId}
                  tabIndex={-1}
                  onKeyDown={(e) => {
                    if (e.key === "Escape" && !busy) {
                      e.stopPropagation();
                      cancelConfirm();
                    }
                  }}
                  className="rounded-[var(--radius-paper,2px)] bg-[var(--paper,#f5eee1)] px-4 py-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent,#b9573d)]"
                >
                  <p id={questionId} className="text-[0.9375rem] leading-relaxed text-[var(--ink,#2f2620)]">
                    Turn it off? People who haven&rsquo;t joined yet won&rsquo;t be able to use it.
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button variant="danger" busy={busy === "revoke"} onClick={revoke}>
                      Confirm
                    </Button>
                    <Button variant="quiet" disabled={busy === "revoke"} onClick={cancelConfirm}>
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <Button ref={turnOffRef} variant="danger" onClick={openConfirm}>
                  Turn off this link
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </PaperCard>
  );
}

/** A small stitched tick beside "Link copied". */
function Tick() {
  return (
    <svg aria-hidden="true" viewBox="0 0 12 12" className="size-3 shrink-0">
      <path
        d="M2 6.5 4.8 9 10 3"
        fill="none"
        stroke="var(--accent-deep, #8f3f2a)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
