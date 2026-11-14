// Small pieces shared by the add-a-memory screens.
import Link from "next/link";
import { Fragment, type ReactNode } from "react";

import { FOCUS_RING } from "@/components/flow/Button";
import { PaperCard } from "@/components/flow/PaperCard";
import type { FamilyChoice } from "@/components/flow/addPage";
import { hrefs } from "@/components/flow/session";

/** window.sessionStorage, or null where it is missing or blocked. */
export function sessionStore(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

/** The path and query of this page, for coming back after logging in. */
export function currentPath(): string {
  return `${window.location.pathname}${window.location.search}`;
}

/** A quiet sheet with a running thread while something loads. */
export function LoadingSheet(props: { label: string }) {
  return (
    <div role="status" aria-busy="true" aria-live="polite">
      <PaperCard className="flex flex-col items-center gap-4 py-10 text-center sm:py-12">
        <span
          aria-hidden="true"
          className="block h-0 w-24 border-t-[1.5px] border-dashed border-rule motion-safe:animate-pulse"
        />
        <p className="font-display text-lg text-ink-soft">{props.label}</p>
      </PaperCard>
    </div>
  );
}

/** A gentle, non-alarming note: a slip with an accent edge. */
export function Notice(props: { children: ReactNode }) {
  return (
    <p
      role="status"
      className="min-w-0 rounded-paper border border-rule border-l-[3px] border-l-accent bg-paper-light px-4 py-3 text-[0.9375rem] leading-snug text-pretty break-words text-ink-soft"
    >
      {props.children}
    </p>
  );
}

const SWITCH_LINK =
  `rounded-paper font-medium text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2 ${FOCUS_RING}`;

/** "A", "A or B", "A, B or C": what goes before link `i` of `n`. */
function joiner(i: number, n: number): string {
  if (i === 0) return "";
  return i === n - 1 ? " or " : ", ";
}

/**
 * One quiet line under the title for someone in two or more families: which
 * family the memory goes into and, unless `switchable` is false, a link to
 * each other family's plain form (no moment, no run: a moment belongs to one
 * family). Renders nothing for one family, so that page stays as it was.
 * `onSwitch` is told the family about to be opened, before the page changes,
 * so the form can hand what was typed on to it.
 */
export function FamilyLine(props: {
  choice: FamilyChoice;
  switchable?: boolean;
  onSwitch?: (familyId: string) => void;
}) {
  const { choice, switchable = true, onSwitch } = props;
  const { current, others, show } = choice;
  if (!show || !current) return null;
  const links = switchable ? others : [];
  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1 text-[0.9375rem] leading-snug text-ink-soft">
      <p className="min-w-0 break-words">
        Adding to <span className="font-semibold text-ink">{current.name}</span>
      </p>
      {links.length ? (
        <>
          <span aria-hidden="true" className="text-ink-soft/50">
            &middot;
          </span>
          <nav aria-label="Add to another family" className="min-w-0 break-words">
            Switch to{" "}
            {links.map((f, i) => (
              <Fragment key={f.id}>
                {joiner(i, links.length)}
                <Link
                  href={hrefs.add({ familyId: f.id })}
                  onClick={onSwitch ? () => onSwitch(f.id) : undefined}
                  className={SWITCH_LINK}
                >
                  {f.name}
                </Link>
              </Fragment>
            ))}
          </nav>
        </>
      ) : null}
    </div>
  );
}

/**
 * A page lede with the family line above it when there is one to show; the
 * lede alone (unchanged) otherwise.
 */
export function ledeWithFamily(choice: FamilyChoice, lede?: ReactNode, switchable = true): ReactNode {
  if (!choice.show || !choice.current) return lede;
  return (
    <>
      <FamilyLine choice={choice} switchable={switchable} />
      {lede == null || lede === false ? null : <p className="mt-3 text-pretty">{lede}</p>}
    </>
  );
}

/**
 * ledeWithFamily as a component, for a page whose switch links hand the
 * typed story on (`onSwitch`). The lede goes in a paragraph either way.
 */
export function FamilyLede(props: {
  choice: FamilyChoice;
  onSwitch?: (familyId: string) => void;
  children?: ReactNode;
}) {
  const { choice, onSwitch, children } = props;
  const lede = children == null || children === false ? null : <p className="text-pretty">{children}</p>;
  if (!choice.show || !choice.current) return lede;
  return (
    <>
      <FamilyLine choice={choice} onSwitch={onSwitch} />
      {lede ? <div className="mt-3">{lede}</div> : null}
    </>
  );
}
