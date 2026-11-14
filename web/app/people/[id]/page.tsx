import type { Metadata } from "next";
import { Suspense } from "react";
import { BookLoading } from "@/components/book/moment/MomentBook";
import { PersonBook } from "./PersonBook";

export const metadata: Metadata = {
  title: "A book · Stitch",
};

// One person's pop-up storybook. The params are read inside the Suspense
// boundary so the shell can still be prerendered; the book itself is
// fetched in the browser (the session cookie belongs to the API).
export default function PersonBookPage(props: PageProps<"/people/[id]">) {
  return (
    <main className="min-h-[100dvh]" style={{ background: "#1a110b" }}>
      <Suspense fallback={<BookLoading />}>
        <PersonBook params={props.params} searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}
