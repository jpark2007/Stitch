import type { Metadata } from "next";
import { Suspense } from "react";
import { MusicLab } from "./MusicLab";

export const metadata: Metadata = {
  title: "Era music lab · Stitch",
  description: "Audition the generated music for each era, crossfades, ducking and mute.",
};

/**
 * /render/lab/music — the ten era palettes, playable.
 *
 *   ?year=<y>      start on a year (music still waits for the first tap)
 *   ?probe=1       expose window.__eraMusic for the offline verification
 *                  renders (render(cues, seconds) -> WAV as base64)
 */
export default function MusicLabPage() {
  return (
    <Suspense fallback={<main className="min-h-dvh bg-paper" />}>
      <MusicLab />
    </Suspense>
  );
}
