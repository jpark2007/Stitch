// Join a family from an invite link, then claim or add your place in the tree.
// The code is runtime data (cacheComponents), so it is read inside Suspense
// and the static shell is the loading sheet. Everything after that happens in
// the browser: JoinScreen asks the API (or the mock) who is signed in.
import type { Metadata } from "next";
import { Suspense } from "react";

import { JoinPlaceholder } from "./JoinShell";
import { JoinScreen } from "./JoinScreen";

export const metadata: Metadata = {
  title: "You’re invited · Stitch",
  description: "Join your family’s pop-up storybook on Stitch.",
};

export default function JoinFamilyPage({ params }: PageProps<"/join/[code]">) {
  return (
    <Suspense fallback={<JoinPlaceholder />}>
      <JoinWithCode params={params} />
    </Suspense>
  );
}

async function JoinWithCode({ params }: { params: PageProps<"/join/[code]">["params"] }) {
  const { code } = await params;
  return <JoinScreen code={code} />;
}
