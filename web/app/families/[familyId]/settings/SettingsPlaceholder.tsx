// A quiet sheet with a running thread, while the page or its family loads.
// Server-safe, so the route can use it as its Suspense fallback.
import { PaperCard } from "@/components/flow/PaperCard";

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
  // Same frame as SessionGate's placeholder, so nothing jumps when it takes over.
  return (
    <div className="mx-auto flex min-h-[70dvh] w-full max-w-md items-center px-4 py-12">
      <div className="w-full">{sheet}</div>
    </div>
  );
}
