// Ask the family: type a question, get the page it's on. `?q=` is the
// question (shareable, reload-safe, Back returns to it); `?family=` the
// family asked. Everything below reads the URL and the session in the
// browser, so the prerendered shell is the quiet loading sheet.
import type { Metadata } from "next";
import { Suspense } from "react";

import { AskLoading, AskScreen } from "@/components/flow/AskFamily";

export const metadata: Metadata = {
  title: "Ask the family · Stitch",
};

export default function AskPage() {
  return (
    <Suspense fallback={<AskLoading />}>
      <AskScreen />
    </Suspense>
  );
}
