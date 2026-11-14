"use client";

// Who has joined the family, on family settings. Presentational: the page
// builds the rows with memberRows() and passes the remove call. Roles are
// shown as text and are read-only (there is no role-change route). Rows the
// viewer may remove get a quiet "Remove" with an inline confirm; a failed
// removal shows its message on that row.
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "./Button";
import { FormError } from "./FormError";
import { PaperCard } from "./PaperCard";
import type { MemberRow } from "./members";

const FALLBACK_DOT = "#8f8173";

function messageOf(err: unknown): string {
  if (err instanceof Error && err.message.trim()) return err.message;
  return "Something went wrong. Please try again.";
}

/** "Ana", or "this family member" when we don't know their name. */
function whoOf(row: MemberRow): string {
  return row.personId ? row.name : "this family member";
}

type Pending =
  | { kind: "remove"; userId: string } // back on this row's Remove button
  | { kind: "confirm"; userId: string } // the row's confirm prompt
  | { kind: "afterRemoval"; userId: string; index: number }; // once the row is gone

export function MemberList(props: { rows: MemberRow[]; onRemove: (userId: string) => Promise<void> }) {
  const { rows, onRemove } = props;
  const uid = useId();
  const headingId = `${uid}heading`;

  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [announcement, setAnnouncement] = useState("");

  const headingRef = useRef<HTMLHeadingElement>(null);
  const removeRefs = useRef(new Map<string, HTMLButtonElement>());
  const confirmRefs = useRef(new Map<string, HTMLDivElement>());
  const pendingFocus = useRef<Pending | null>(null);

  useEffect(() => {
    const pending = pendingFocus.current;
    if (!pending) return;
    let el: HTMLElement | null | undefined = null;
    if (pending.kind === "remove") el = removeRefs.current.get(pending.userId);
    else if (pending.kind === "confirm") el = confirmRefs.current.get(pending.userId);
    else {
      // Wait until the page has dropped the removed row.
      if (rows.some((r) => r.userId === pending.userId)) return;
      // The next removable row down, else the nearest one up, else the heading.
      const after = rows.slice(pending.index).find((r) => r.removable);
      const before = rows.slice(0, pending.index).reverse().find((r) => r.removable);
      const target = after ?? before;
      el = (target && removeRefs.current.get(target.userId)) || headingRef.current;
    }
    if (el && !(el instanceof HTMLButtonElement && el.disabled)) {
      el.focus();
      pendingFocus.current = null;
    }
  });

  function clearError(userId: string) {
    setErrors((prev) => {
      if (!(userId in prev)) return prev;
      const next = { ...prev };
      delete next[userId];
      return next;
    });
  }

  function openConfirm(row: MemberRow) {
    clearError(row.userId);
    setAnnouncement("");
    setConfirmingId(row.userId);
    pendingFocus.current = { kind: "confirm", userId: row.userId };
  }

  function cancelConfirm(userId: string) {
    setConfirmingId(null);
    pendingFocus.current = { kind: "remove", userId };
  }

  async function remove(row: MemberRow) {
    if (busyId) return;
    const index = rows.findIndex((r) => r.userId === row.userId);
    clearError(row.userId);
    setBusyId(row.userId);
    try {
      await onRemove(row.userId);
      setConfirmingId(null);
      setAnnouncement(`${row.personId ? row.name : "The family member"} was removed from the family.`);
      pendingFocus.current = { kind: "afterRemoval", userId: row.userId, index: Math.max(0, index) };
    } catch (err) {
      setErrors((prev) => ({ ...prev, [row.userId]: messageOf(err) }));
      setConfirmingId(null);
      pendingFocus.current = { kind: "remove", userId: row.userId };
    } finally {
      setBusyId(null);
    }
  }

  return (
    <PaperCard aria-labelledby={headingId}>
      <h2
        id={headingId}
        ref={headingRef}
        tabIndex={-1}
        className="rounded-[var(--radius-paper,2px)] font-serif text-[1.5rem] leading-tight text-[var(--ink,#2f2620)] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[var(--accent,#b9573d)]"
      >
        Who&rsquo;s here
      </h2>

      {rows.length === 0 ? (
        <p className="mt-4 text-[0.9375rem] text-[var(--ink-soft,#5c4e43)] italic">No one has joined yet.</p>
      ) : (
        <ul className="mt-4 list-none p-0" aria-labelledby={headingId}>
          {rows.map((row) => {
            const confirming = confirmingId === row.userId;
            const busy = busyId === row.userId;
            const error = errors[row.userId];
            const questionId = `${uid}q-${row.userId}`;
            return (
              <li
                key={row.userId}
                className="border-b border-dashed border-[var(--rule,#2f262024)] py-3 last:border-b-0 last:pb-0 first:pt-0"
              >
                <div className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1">
                  <span
                    aria-hidden="true"
                    className="size-3 shrink-0 rounded-full ring-1 ring-[var(--rule,#2f262024)]"
                    style={{ backgroundColor: row.color || FALLBACK_DOT }}
                  />
                  <span className="min-w-0 flex-1 break-words text-base leading-snug text-[var(--ink,#2f2620)]">
                    <span className={row.personId ? undefined : "italic text-[var(--ink-soft,#5c4e43)]"}>
                      {row.name}
                    </span>
                    {row.isYou ? <span className="text-[var(--ink-soft,#5c4e43)]"> (you)</span> : null}
                    <span className="sr-only">, </span>
                    <RoleLabel role={row.role} />
                  </span>
                  {row.removable && !confirming ? (
                    <Button
                      ref={(el) => {
                        if (el) removeRefs.current.set(row.userId, el);
                        else removeRefs.current.delete(row.userId);
                      }}
                      variant="quiet"
                      className="-mr-3 shrink-0"
                      onClick={() => openConfirm(row)}
                    >
                      Remove<span className="sr-only"> {whoOf(row)}</span>
                    </Button>
                  ) : null}
                </div>

                {confirming ? (
                  <div
                    ref={(el) => {
                      if (el) confirmRefs.current.set(row.userId, el);
                      else confirmRefs.current.delete(row.userId);
                    }}
                    role="group"
                    aria-labelledby={questionId}
                    tabIndex={-1}
                    onKeyDown={(e) => {
                      if (e.key === "Escape" && !busy) {
                        e.stopPropagation();
                        cancelConfirm(row.userId);
                      }
                    }}
                    className="mt-2 rounded-[var(--radius-paper,2px)] bg-[var(--paper,#f5eee1)] px-4 py-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent,#b9573d)]"
                  >
                    <p id={questionId} className="text-[0.9375rem] leading-relaxed text-[var(--ink,#2f2620)]">
                      Remove {whoOf(row)} from the family? Their place in the tree stays.
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button variant="danger" busy={busy} onClick={() => remove(row)}>
                        Confirm
                      </Button>
                      <Button variant="quiet" disabled={busy} onClick={() => cancelConfirm(row.userId)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : null}

                {error ? (
                  <div className="mt-2">
                    <FormError message={error} />
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>
    </PaperCard>
  );
}

/** "Admin" or "Member", as text in a small paper tag (never colour alone). */
function RoleLabel({ role }: { role: MemberRow["role"] }) {
  const admin = role === "admin";
  return (
    <span
      className={[
        "ml-2 inline-block rounded-full border px-2 py-px align-[0.1em] text-xs font-medium tracking-wide",
        admin
          ? "border-[var(--accent-deep,#8f3f2a)]/40 text-[var(--accent-deep,#8f3f2a)]"
          : "border-[var(--rule,#2f262024)] text-[var(--ink-soft,#5c4e43)]",
      ].join(" ")}
    >
      {admin ? "Admin" : "Member"}
    </span>
  );
}
