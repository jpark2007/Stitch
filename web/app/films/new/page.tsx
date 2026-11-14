// Make a film: pick 8 to 40 photos of one day or trip, tell what happened,
// and Stitch turns it into a short narrated film. `?family=` picks the
// family. Everything below reads the URL and the session in the browser, so
// the prerendered shell is the quiet loading sheet.
import type { Metadata } from "next";
import { Suspense } from "react";

import { NewFilmLoading, NewFilmScreen } from "./NewFilmScreen";

export const metadata: Metadata = {
  title: "Make a film · Stitch",
};

export default function NewFilmPage() {
  return (
    <Suspense fallback={<NewFilmLoading />}>
      <NewFilmScreen />
    </Suspense>
  );
}
