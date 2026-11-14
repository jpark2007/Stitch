// Add a relative: someone in the family who may never sign up, like a late
// great-grandparent or a young child (ARCHITECTURE.md section 4 item 5).
// Everything here depends on who is signed in, so the page is a client
// screen; the Suspense boundary lets the static shell prerender while the
// screen reads the URL (useParams, useSearchParams) in the browser.
import type { Metadata } from "next";
import { Suspense } from "react";

import { SettingsPlaceholder } from "../../settings/SettingsPlaceholder";
import { AddRelativeScreen } from "./AddRelativeScreen";

export const metadata: Metadata = {
  title: "Add a relative · Stitch",
};

export default function AddRelativePage() {
  return (
    <Suspense fallback={<SettingsPlaceholder label="Opening your family’s pages…" />}>
      <AddRelativeScreen />
    </Suspense>
  );
}
