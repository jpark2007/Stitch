// Bare stage for headless frame capture by the checker (and for eyeballing
// the stage while it is built).
//
//   /render/demo                 the hand-built test diorama, playing
//   /render/demo?t=4.5           freeze stage time at 4.5 s
//   /render/demo?open=0.5        freeze the pop-up at half open (time frozen at ?t or 0)
//   /render/demo?effect=rain     swap the effect: confetti | steam | rain | smoke | none
//   /render/demo?close=10        fold the set back down at 10 s (front pieces first)
//   /render/demo?dof             soft depth of field focused on the camera target
//   /render/<anything else>      an empty stage
//
// Once the first frames have rendered, <html data-stage-ready="1"> is set so a
// capture script can wait for it.

import { Suspense } from "react";
import { RenderView } from "./RenderView";

type Query = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

function num(v: string | string[] | undefined): number | null {
  const s = one(v);
  if (s === undefined || s.trim() === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

async function RenderParams({ params, searchParams }: { params: Promise<{ memoryId: string }>; searchParams: Promise<Query> }) {
  const [{ memoryId }, q] = await Promise.all([params, searchParams]);
  const open = num(q.open);
  return (
    <RenderView
      memoryId={memoryId}
      time={num(q.t)}
      open={open === null ? null : Math.min(1, Math.max(0, open))}
      effect={one(q.effect) ?? null}
      closeAt={num(q.close)}
      dof={q.dof !== undefined && one(q.dof) !== "0"}
    />
  );
}

export default function RenderPage({ params, searchParams }: PageProps<"/render/[memoryId]">) {
  return (
    <main className="fixed inset-0 bg-[#24180f]">
      <Suspense fallback={null}>
        <RenderParams params={params} searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
