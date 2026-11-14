// One photo film: while it is being made, a calm waiting sheet that checks
// every couple of seconds; once ready, the player; if it failed, a plain
// message and the way to try again. `?family=` picks the family. Everything
// below reads the URL and the session in the browser, so the prerendered
// shell is the quiet loading sheet.
import type { Metadata } from "next";
import { Suspense } from "react";

import { FilmLoading, FilmScreen } from "./FilmScreen";

export const metadata: Metadata = {
  title: "Film · Stitch",
};

export default function FilmRoutePage() {
  return (
    <Suspense fallback={<FilmLoading />}>
      <FilmScreen />
    </Suspense>
  );
}
