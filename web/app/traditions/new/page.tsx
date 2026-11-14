// Add a tradition to the Book of Family Traditions: its name, what kind it is,
// how often it happens, the story of it, who takes part, photos and the year
// it started. `?family=` picks the family, the way /add does. Everything below
// reads the URL and the session in the browser, so the prerendered shell is
// the quiet loading sheet.
import type { Metadata } from "next";
import { Suspense } from "react";

import { NewTraditionLoading, NewTraditionScreen } from "./NewTraditionScreen";

export const metadata: Metadata = {
  title: "Add a tradition · Stitch",
};

export default function NewTraditionPage() {
  return (
    <Suspense fallback={<NewTraditionLoading familyId={null} />}>
      <NewTraditionScreen />
    </Suspense>
  );
}
