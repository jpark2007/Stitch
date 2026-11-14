// The family's photo films: taped covers with title, year and length, newest
// first, and the way to make a new one. `?family=` picks the family. The list
// depends on who is signed in, so it renders in the browser; the prerendered
// shell is the quiet loading sheet.
import type { Metadata } from "next";
import { Suspense } from "react";

import { FilmsLoading, FilmsScreen } from "./FilmsScreen";

export const metadata: Metadata = {
  title: "Films · Stitch",
};

export default function FilmsPage() {
  return (
    <Suspense fallback={<FilmsLoading />}>
      <FilmsScreen />
    </Suspense>
  );
}
