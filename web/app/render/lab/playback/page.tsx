// Developer lab: the timeline player on the hymn-books sample, drawn flat.
// /render/lab/playback?t=4.6 freezes at 4.6 s; &narrator=mom starts with Mom's telling.

import type { Metadata } from "next";
import { Suspense } from "react";

import { PlaybackLab, PlaybackLabFallback } from "./PlaybackLab";

export const metadata: Metadata = {
  title: "Playback lab · Stitch",
  robots: { index: false },
};

export default function PlaybackLabPage() {
  // The lab reads ?t and ?narrator on the client, so only this boundary waits for them.
  return (
    <Suspense fallback={<PlaybackLabFallback />}>
      <PlaybackLab />
    </Suspense>
  );
}
