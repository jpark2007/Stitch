// The audience screen's holding view (/demo/hold): only the paper and the
// Stitch wordmark, for the moments between scenes of the live demo. No
// chrome, no links, nothing to load; the background is the global paper.
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Stitch",
  robots: { index: false, follow: false },
};

export default function HoldPage() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center px-6 select-none" aria-label="Stitch">
      <p
        className="font-display text-[clamp(4rem,12vw,10rem)] leading-none tracking-[-0.03em] text-ink"
        style={{ fontVariationSettings: '"SOFT" 100, "WONK" 1' }}
      >
        Stitch
      </p>
      <span
        aria-hidden="true"
        className="mt-[clamp(1rem,2.5vw,2rem)] block h-0 w-[clamp(6rem,14vw,12rem)] border-t-2 border-dashed border-accent/55"
      />
    </main>
  );
}
