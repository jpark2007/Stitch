// Start a family. A thin server shell around the signed-in screen; the
// SessionGate inside it sends signed-out visitors to log in first.
import type { Metadata } from "next";

import { NewFamilyScreen } from "./NewFamilyScreen";

export const metadata: Metadata = {
  title: "Start your family · Stitch",
};

export default function NewFamilyPage() {
  return <NewFamilyScreen />;
}
