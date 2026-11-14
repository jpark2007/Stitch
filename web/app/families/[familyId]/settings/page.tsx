// Family settings: members and roles, the invite link, switching families and
// signing out. Everything here depends on who is signed in, so the page is a
// client screen; the Suspense boundary lets the static shell prerender while
// the screen reads the URL (useParams, useSearchParams) in the browser.
import { Suspense } from "react";

import { SettingsPlaceholder } from "./SettingsPlaceholder";
import { SettingsScreen } from "./SettingsScreen";

export default function FamilySettingsPage() {
  return (
    <Suspense fallback={<SettingsPlaceholder label="Opening your family’s pages…" />}>
      <SettingsScreen />
    </Suspense>
  );
}
