// The family pages' quiet sheets (settings, add a relative): a running
// thread while the page or its family loads, and the dead end for a family
// the viewer isn't in. Both sit in the flow pages' frame, under the "Stitch"
// mark, in the pages' own wide column, so nothing jumps from one to the next.
// Server-safe, so the routes can use the placeholder as their Suspense
// fallback.
import { ButtonLink } from "@/components/flow/Button";
import { FlowFrame } from "@/components/flow/FlowPage";
import { PaperCard } from "@/components/flow/PaperCard";
import { hrefs } from "@/components/flow/session";

/** Both family pages are FlowPage width="wide"; their sheets match. */
type Width = "narrow" | "wide";

/**
 * A loading sheet. `bare` drops the frame, for use inside a page that
 * already has one (in place of its content). `width` is the frame's column
 * (default "wide", the family pages' own).
 */
export function SettingsPlaceholder(props: { label: string; bare?: boolean; width?: Width }) {
  const sheet = (
    <div role="status" aria-busy="true" aria-live="polite">
      <PaperCard className="flex flex-col items-center gap-4 text-center">
        <span
          aria-hidden="true"
          className="block h-0 w-24 border-t-[1.5px] border-dashed border-rule motion-safe:animate-pulse"
        />
        <p className="font-display text-lg text-ink-soft">{props.label}</p>
      </PaperCard>
    </div>
  );
  if (props.bare) return sheet;
  // The same frame and width as SessionGate's placeholder (the screens pass it
  // width="wide"), so nothing jumps when it takes over.
  return <FlowFrame width={props.width ?? "wide"}>{sheet}</FlowFrame>;
}

/** Not one of the viewer's families (or gone): say so calmly, with one way out. */
export function FamilyNotFound(props: { width?: Width } = {}) {
  return (
    <FlowFrame width={props.width ?? "wide"}>
      <PaperCard className="flex flex-col items-center gap-4 text-center">
        <h1 className="font-display text-[1.875rem] leading-[1.15] text-balance break-words text-ink sm:text-[2.25rem]">
          We couldn’t find that family
        </h1>
        <p className="max-w-[24rem] text-[1.0625rem] leading-relaxed text-pretty text-ink-soft">
          It may have been removed, or this link is for a family you haven’t joined.
        </p>
        <hr aria-hidden="true" className="stitch-rule mt-3 w-24" />
        <div className="mt-3">
          <ButtonLink href={hrefs.home()} variant="secondary">
            Go home
          </ButtonLink>
        </div>
      </PaperCard>
    </FlowFrame>
  );
}
