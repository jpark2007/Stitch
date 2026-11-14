import type { Metadata } from "next";
import { Suspense } from "react";
import ThreadsScreen from "@/components/threads/ThreadsScreen";
import { ThreadsSkeleton } from "@/components/threads/ThreadsPaper";
import "./threads.css";

export const metadata: Metadata = {
  title: "Threads · Stitch",
  description: "The whole family as stitched thread along time.",
};

function first(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/**
 * Reads ?zoom, ?focus and ?highlight (which only seed the view), ?family
 * (which of the user's families to open) and ?demo=1 (the demo family). The
 * family's threads load in the browser, where the session cookie is.
 */
async function Threads({ searchParams }: Pick<PageProps<"/threads">, "searchParams">) {
  const q = await searchParams;
  return (
    <ThreadsScreen
      zoom={first(q.zoom)}
      focus={first(q.focus)}
      highlight={first(q.highlight)}
      family={first(q.family)}
      demo={first(q.demo) === "1"}
    />
  );
}

export default function ThreadsPage(props: PageProps<"/threads">) {
  return (
    <Suspense fallback={<ThreadsSkeleton />}>
      <Threads searchParams={props.searchParams} />
    </Suspense>
  );
}
