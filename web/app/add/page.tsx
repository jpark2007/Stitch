// Add a memory: answer a question (or tell any story), with a year, photos and
// who was there, then watch the studio turn it into a scene. `?memoryId=` adds
// a second telling to an existing moment; `?run=` reattaches to a run already
// under way. Everything below reads the URL and the session in the browser, so
// the prerendered shell is the quiet loading sheet.
import type { Metadata } from "next";
import { Suspense } from "react";

import { AddLoading, AddScreen } from "./AddScreen";

export const metadata: Metadata = {
  title: "Add a memory · Stitch",
};

export default function AddMemoryPage() {
  return (
    <Suspense fallback={<AddLoading />}>
      <AddScreen />
    </Suspense>
  );
}
