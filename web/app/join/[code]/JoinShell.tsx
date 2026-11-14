// The frame every view of /join/[code] sits in, and the quiet sheet shown
// while the invite loads. Server-safe (no hooks), so page.tsx can use the
// placeholder as its Suspense fallback.
import type { ReactNode } from "react";

import { FlowFrame } from "@/components/flow/FlowPage";
import { PaperCard } from "@/components/flow/PaperCard";

/**
 * The shared flow frame (the "Stitch" mark top left, linking home, and one
 * centred column), so the invite looks like every other flow page. The join
 * views draw their own centred headings, so this is the frame without
 * FlowPage's title block.
 */
export function JoinShell(props: { children: ReactNode; width?: "narrow" | "wide" }) {
  return <FlowFrame width={props.width ?? "narrow"}>{props.children}</FlowFrame>;
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
