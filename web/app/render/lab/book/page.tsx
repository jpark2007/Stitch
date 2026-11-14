import type { Metadata } from "next";
import { Suspense } from "react";
import { BookStageLab } from "./BookStageLab";

export const metadata: Metadata = {
  title: "Book stage lab · Stitch",
  description: "The timeline stage playing inside the 3D book's right-page slot.",
};

/**
 * /render/lab/book — the hymn_books sample playing in the book's stage slot,
 * through the same API the book will use (TimelineScene + reveal).
 *
 *   ?t=<seconds>     freeze scene time
 *   ?focus=right     look closely at the stage page
 *   ?still=1         no parallax or hints (screenshots)
 */
export default function BookStageLabPage() {
  return (
    <Suspense fallback={<main className="h-dvh" style={{ background: "#1a110b" }} />}>
      <main className="h-dvh" style={{ background: "#1a110b" }}>
        <BookStageLab />
      </main>
    </Suspense>
  );
}
