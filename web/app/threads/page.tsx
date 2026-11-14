import type { Metadata } from "next";
import { Suspense } from "react";
import ThreadsView from "@/components/threads/ThreadsView";
import type { ThreadMoment, ThreadPerson } from "@/components/threads/model";
import { demoFamily, demoMoments, demoPeople } from "@/components/tree/demoFamily";

export const metadata: Metadata = {
  title: "Threads · Stitch",
  description: "The whole family as stitched thread along time.",
};

// Demo data until the threads route lands (DESIGN.md "Threads data").
const people: ThreadPerson[] = demoPeople.map((p) => ({
  id: p.id,
  name: p.name,
  born: p.born,
  parentIds: p.parentIds,
  spouseIds: p.spouseIds,
  color: p.character.color,
}));

const moments: ThreadMoment[] = demoMoments.map((m) => ({
  id: m.id,
  title: m.title,
  year: m.year,
  participantIds: m.participantIds,
}));

function first(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/** Reads ?zoom, ?focus and ?highlight, which only seed the view. */
async function Threads({ searchParams }: Pick<PageProps<"/threads">, "searchParams">) {
  const q = await searchParams;
  return (
    <ThreadsView
      familyName={demoFamily.name}
      people={people}
      moments={moments}
      zoom={first(q.zoom)}
      focus={first(q.focus)}
      highlight={first(q.highlight)}
    />
  );
}

export default function ThreadsPage(props: PageProps<"/threads">) {
  return (
    <Suspense fallback={null}>
      <Threads searchParams={props.searchParams} />
    </Suspense>
  );
}
