// Small pieces shared by the add-a-memory screens.
import type { ReactNode } from "react";

import { PaperCard } from "@/components/flow/PaperCard";

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
