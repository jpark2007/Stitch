// The frame every view of /join/[code] sits in, and the quiet sheet shown
// while the invite loads. Server-safe (no hooks), so page.tsx can use the
// placeholder as its Suspense fallback.
import type { ReactNode } from "react";

import { PaperCard } from "@/components/flow/PaperCard";

/** One centred column, the same gutters as the other flow pages. */
export function JoinShell(props: { children: ReactNode; width?: "narrow" | "wide" }) {
  const width = props.width === "wide" ? "max-w-[36rem]" : "max-w-[30rem]";
  return (
    <main className="w-full min-w-0 overflow-x-clip px-4 pt-10 pb-20 font-sans text-ink sm:px-6 sm:pt-16">
      <div className={`mx-auto w-full min-w-0 ${width}`}>{props.children}</div>
    </main>
  );
}

/** A loose thread and a line of text while the invitation is fetched. */
export function JoinPlaceholder(props: { label?: string }) {
  return (
    <JoinShell>
      <div role="status" aria-busy="true" aria-live="polite">
        <PaperCard className="flex flex-col items-center gap-4 text-center">
          <span
            aria-hidden="true"
            className="block h-0 w-24 border-t-[1.5px] border-dashed border-rule motion-safe:animate-pulse"
          />
          <p className="font-display text-lg text-ink-soft">{props.label ?? "Opening your invitation…"}</p>
        </PaperCard>
      </div>
    </JoinShell>
  );
}
