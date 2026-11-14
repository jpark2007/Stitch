"use client";

// Who has joined the family, on family settings. Presentational: the page
// builds the rows with memberRows() and passes the remove and role-change
// calls. Roles are shown as text tags. For an admin, each row they may change
// gets a quiet "Make admin" or "Make member" (on their own row "Step down as
// admin", only while another admin remains), and each row they may remove a
// quiet "Remove". Every action opens an inline confirm; a failed one shows
// its message on that row, and one that worked is announced.
import { useEffect, useId, useRef, useState } from "react";

import { Button, FOCUS_RING } from "./Button";
import { FormError } from "./FormError";
import { PaperCard } from "./PaperCard";
import {
  removeMemberOutcome,
  roleAfter,
  roleChangeCopy,
  roleChangeError,
  type MemberRow,
  type RoleAction,
  type RoleChangeCopy,
} from "./members";

/**
 * The kit's quiet button look for the row actions, sized to their text. The
 * kit's Button reserves room for a busy spinner even when idle, which would
 * open wide gaps between two quiet actions; these never show busy themselves
 * (their confirm's button does).
 */
const ROW_ACTION =
  "inline-flex min-h-11 items-center rounded-card border border-transparent bg-transparent px-3 py-2.5 " +
  "font-sans text-base font-semibold leading-tight text-accent-deep underline-offset-[0.2em] select-none " +
  "not-disabled:hover:underline not-disabled:hover:decoration-1 disabled:cursor-not-allowed disabled:opacity-60 " +
  FOCUS_RING;

/** "Ana", or "this family member" when we don't know their name. */
function whoOf(row: MemberRow): string {
  return row.personId ? row.name : "this family member";
}

/**
 * Which inline confirm is open: removing the member, or changing their role.
 * A role change keeps the row as it was when the confirm opened, so the
 * prompt doesn't shift while the page catches up with the new role.
 */
type Confirming =
  | { userId: string; action: "remove" }
  | { userId: string; action: "role"; row: MemberRow; roleAction: RoleAction; copy: RoleChangeCopy };

type Pending =
  | { kind: "remove"; userId: string } // back on this row's Remove button
  | { kind: "role"; userId: string } // back on this row's role button, else the heading
  | { kind: "confirm"; userId: string } // the row's confirm prompt
  | { kind: "afterRemoval"; userId: string; index: number }; // once the row is gone

export function MemberList(props: {
  rows: MemberRow[];
  onRemove: (userId: string) => Promise<void>;
  /** Resolves once the page shows the new role (and, after stepping down, the new session). */
  onChangeRole: (userId: string, role: MemberRow["role"]) => Promise<void>;
}) {
  const { rows, onRemove, onChangeRole } = props;
  const uid = useId();
  const headingId = `${uid}heading`;

  const [confirming, setConfirming] = useState<Confirming | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [announcement, setAnnouncement] = useState("");
  // A visible line under the list for news about someone no longer in it.
  const [notice, setNotice] = useState("");
  // A failed change for someone whose row has since gone (they left): shown under the list.
  const orphanErrors = Object.entries(errors)
    .filter(([userId]) => !rows.some((r) => r.userId === userId))
    .map(([, message]) => message);

  const headingRef = useRef<HTMLHeadingElement>(null);
  const removeRefs = useRef(new Map<string, HTMLButtonElement>());
  const roleRefs = useRef(new Map<string, HTMLButtonElement>());
  const confirmRefs = useRef(new Map<string, HTMLDivElement>());
  const pendingFocus = useRef<Pending | null>(null);

  useEffect(() => {
    const pending = pendingFocus.current;
    if (!pending) return;
    let el: HTMLElement | null | undefined = null;
    if (pending.kind === "remove") el = removeRefs.current.get(pending.userId);
    else if (pending.kind === "role") {
      // After a change the row may have moved, or (after stepping down) lost
      // its controls; then the list's heading takes focus.
      el = roleRefs.current.get(pending.userId) || headingRef.current;
    } else if (pending.kind === "confirm") el = confirmRefs.current.get(pending.userId);
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

  function openConfirm(row: MemberRow, action: Confirming["action"]) {
    if (action === "role" && !row.roleAction) return;
    // Clear this row's message and any about people no longer listed.
    setErrors((prev) => {
      const keep = Object.entries(prev).filter(
        ([userId]) => userId !== row.userId && rows.some((r) => r.userId === userId),
      );
      return keep.length === Object.keys(prev).length ? prev : Object.fromEntries(keep);
    });
    setAnnouncement("");
    setNotice("");
    setConfirming(
      action === "role" && row.roleAction
        ? { userId: row.userId, action, row, roleAction: row.roleAction, copy: roleChangeCopy(row, row.roleAction) }
        : { userId: row.userId, action: "remove" },
    );
    pendingFocus.current = { kind: "confirm", userId: row.userId };
  }

  /** Close the confirm and go back to the button that opened it. */
  function cancelConfirm(open: Confirming) {
    setConfirming(null);
    pendingFocus.current = { kind: open.action, userId: open.userId };
  }

  async function remove(row: MemberRow) {
    if (busyId) return;
    const index = rows.findIndex((r) => r.userId === row.userId);
    clearError(row.userId);
    setBusyId(row.userId);
    try {
      await onRemove(row.userId);
      setConfirming(null);
      setAnnouncement(`${row.personId ? row.name : "The family member"} was removed from the family.`);
      pendingFocus.current = { kind: "afterRemoval", userId: row.userId, index: Math.max(0, index) };
    } catch (err) {
      const outcome = removeMemberOutcome(err, row);
      setConfirming(null);
      if (outcome.gone) {
        // Already gone (the page has fetched the family again): say so, quietly.
        setAnnouncement(outcome.message);
        setNotice(outcome.message);
        pendingFocus.current = { kind: "afterRemoval", userId: row.userId, index: Math.max(0, index) };
      } else {
        setErrors((prev) => ({ ...prev, [row.userId]: outcome.message }));
        pendingFocus.current = { kind: "remove", userId: row.userId };
      }
    } finally {
      setBusyId(null);
    }
  }

  async function changeRole(open: Extract<Confirming, { action: "role" }>) {
    if (busyId) return;
    const { row, roleAction, copy } = open;
    clearError(row.userId);
    setBusyId(row.userId);
    try {
      await onChangeRole(row.userId, roleAfter(roleAction));
      setAnnouncement(copy.done);
    } catch (err) {
      setErrors((prev) => ({ ...prev, [row.userId]: roleChangeError(err, row) }));
    } finally {
      setConfirming(null);
      setBusyId(null);
      pendingFocus.current = { kind: "role", userId: row.userId };
    }
  }

  return (
    <PaperCard aria-labelledby={headingId}>
      <h2
        id={headingId}
        ref={headingRef}
        tabIndex={-1}
        className={`rounded-paper font-display text-[1.5rem] leading-tight text-ink ${FOCUS_RING}`}
      >
        Who&rsquo;s here
      </h2>

      {rows.length === 0 ? (
        <p className="mt-4 text-[0.9375rem] text-ink-soft italic">No one has joined yet.</p>
      ) : (
        <ul className="mt-4 list-none p-0" aria-labelledby={headingId}>
          {rows.map((row) => {
            const open = confirming?.userId === row.userId ? confirming : null;
            const busy = busyId === row.userId;
            const error = errors[row.userId];
            const questionId = `${uid}q-${row.userId}`;
            const detailId = `${uid}d-${row.userId}`;
            const role = row.roleAction ? roleChangeCopy(row, row.roleAction) : null;
            return (
              <li
                key={row.userId}
                className="border-b border-dashed border-rule py-3 last:border-b-0 last:pb-0 first:pt-0"
              >
                <div className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1">
                  <span
                    aria-hidden="true"
                    className="size-3 shrink-0 rounded-full bg-ink-faint ring-1 ring-rule"
                    style={row.color ? { backgroundColor: row.color } : undefined}
                  />
                  <span className="min-w-0 flex-1 break-words text-base leading-snug text-ink">
                    <span className={row.personId ? undefined : "italic text-ink-soft"}>
                      {row.name}
                    </span>
                    {row.isYou ? <span className="text-ink-soft"> (you)</span> : null}
                    <span className="sr-only">, </span>
                    <RoleLabel role={row.role} />
                  </span>
                  {(role || row.removable) && !open ? (
                    <div className="-mr-3 flex shrink-0 flex-wrap items-center">
                      {role ? (
                        <button
                          ref={(el) => {
                            if (el) roleRefs.current.set(row.userId, el);
                            else roleRefs.current.delete(row.userId);
                          }}
                          type="button"
                          className={ROW_ACTION}
                          disabled={busyId !== null}
                          onClick={() => openConfirm(row, "role")}
                        >
                          <span>
                            {role.labelBefore}
                            {role.labelHidden ? <span className="sr-only">{role.labelHidden}</span> : null}
                            {role.labelAfter}
                          </span>
                        </button>
                      ) : null}
                      {row.removable ? (
                        <button
                          ref={(el) => {
                            if (el) removeRefs.current.set(row.userId, el);
                            else removeRefs.current.delete(row.userId);
                          }}
                          type="button"
                          className={ROW_ACTION}
                          disabled={busyId !== null}
                          onClick={() => openConfirm(row, "remove")}
                        >
                          <span>
                            Remove<span className="sr-only"> {whoOf(row)}</span>
                          </span>
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </div>

                {open ? (
                  <div
                    ref={(el) => {
                      if (el) confirmRefs.current.set(row.userId, el);
                      else confirmRefs.current.delete(row.userId);
                    }}
                    role="group"
                    aria-labelledby={questionId}
                    aria-describedby={open.action === "role" ? detailId : undefined}
                    tabIndex={-1}
                    onKeyDown={(e) => {
                      if (e.key === "Escape" && !busy) {
                        e.stopPropagation();
                        cancelConfirm(open);
                      }
                    }}
                    className={`mt-2 rounded-paper bg-paper px-4 py-3 ${FOCUS_RING}`}
                  >
                    {open.action === "role" ? (
                      <>
                        <p id={questionId} className="text-[0.9375rem] leading-relaxed text-ink">
                          {open.copy.question}
                        </p>
                        <p id={detailId} className="mt-1 text-[0.9375rem] leading-relaxed text-pretty text-ink-soft">
                          {open.copy.detail}
                        </p>
                      </>
                    ) : (
                      <p id={questionId} className="text-[0.9375rem] leading-relaxed text-ink">
                        Remove {whoOf(row)} from the family? Their place in the tree stays.
                      </p>
                    )}
                    <div className="mt-3 flex flex-wrap gap-2">
                      {open.action === "role" ? (
                        <Button
                          variant="secondary"
                          busy={busy}
                          busyLabel={open.copy.busy}
                          onClick={() => changeRole(open)}
                        >
                          {open.copy.confirm}
                        </Button>
                      ) : (
                        <Button variant="danger" busy={busy} onClick={() => remove(row)}>
                          Confirm
                        </Button>
                      )}
                      <Button variant="quiet" disabled={busy} onClick={() => cancelConfirm(open)}>
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

      {orphanErrors.length ? (
        <div className="mt-4">
          <FormError message={[...new Set(orphanErrors)].join(" ")} />
        </div>
      ) : null}
      {notice ? (
        <p className="mt-4 border-t border-dashed border-rule pt-3 text-[0.9375rem] leading-relaxed text-ink-soft">
          {notice}
        </p>
      ) : null}

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
          ? "border-accent-deep/40 text-accent-deep"
          : "border-rule text-ink-soft",
      ].join(" ")}
    >
      {admin ? "Admin" : "Member"}
    </span>
  );
}
