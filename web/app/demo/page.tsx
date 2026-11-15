// Backstage: the demo control page (/demo?family=<fid>). The operator runs
// the live demo from here; a second window shows the audience what it should
// see. Not linked from any navigation. It depends on who is signed in, so it
// renders in the browser; the prerendered shell is a quiet loading line.
import type { Metadata } from "next";
import { Suspense } from "react";

import { DemoControl, DemoLoading } from "@/components/demo/DemoControl";

export const metadata: Metadata = {
  title: "Backstage · Stitch",
  robots: { index: false, follow: false },
};

export default function DemoPage() {
  return (
    <Suspense fallback={<DemoLoading />}>
      <DemoControl />
    </Suspense>
  );
}
