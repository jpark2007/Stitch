// The family pages' quiet sheets (settings, add a relative): a running
// thread while the page or its family loads, and the dead end for a family
// the viewer isn't in. Both sit in the flow pages' frame, under the "Stitch"
// mark. Server-safe, so the routes can use the placeholder as their Suspense
// fallback.
import { ButtonLink } from "@/components/flow/Button";
import { FlowFrame } from "@/components/flow/FlowPage";
import { PaperCard } from "@/components/flow/PaperCard";
import { hrefs } from "@/components/flow/session";

/**
 * A loading sheet. `bare` drops the frame, for use inside a page that
 * already has one (in place of its content).
 */
export function SettingsPlaceholder(props: { label: string; bare?: boolean }) {
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
  // The same frame and width as SessionGate's placeholder, so nothing jumps when it takes over.
  return <FlowFrame>{sheet}</FlowFrame>;
}

/** Not one of the viewer's families (or gone): say so calmly, with one way out. */
export function FamilyNotFound() {
  return (
    <FlowFrame>
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
