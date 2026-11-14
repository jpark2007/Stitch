import type { Metadata } from "next";
import { Suspense } from "react";
import { BookLoading, MemoryBook } from "./MemoryBook";

export const metadata: Metadata = {
  title: "A moment · Stitch",
};

// A single moment, opened as a book. The params are read inside the Suspense
// boundary so the shell can still be prerendered.
export default function MemoryPage(props: PageProps<"/memories/[id]">) {
  return (
    <main className="min-h-[100dvh]" style={{ background: "#1a110b" }}>
      <Suspense fallback={<BookLoading />}>
        <MemoryBook params={props.params} searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}
