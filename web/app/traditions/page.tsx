import type { Metadata } from "next";
import { Suspense } from "react";
import { BookLoading } from "@/components/book/moment/MomentBook";
import { TraditionsPage } from "./TraditionsPage";

export const metadata: Metadata = {
  title: "Book of Traditions · Stitch",
};

// The Book of Family Traditions, as a pop-up book. The query is read inside
// the Suspense boundary so the shell can still be prerendered; the book
// itself is fetched in the browser (the session cookie belongs to the API).
export default function TraditionsRoute(props: PageProps<"/traditions">) {
  return (
    <main className="min-h-[100dvh]" style={{ background: "#1a110b" }}>
      <Suspense fallback={<BookLoading />}>
        <TraditionsPage searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}
