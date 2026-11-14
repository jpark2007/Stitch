// Bare stage for headless frame capture by the checker (and for eyeballing
// the stage while it is built).
//
//   /render/demo                 the hand-built test diorama, playing
//   /render/demo?t=4.5           freeze stage time at 4.5 s
//   /render/demo?open=0.5        freeze the pop-up at half open (time frozen at ?t or 0)
//   /render/demo?effect=rain     swap the effect: confetti | steam | rain | smoke | none
//   /render/demo?close=10        fold the set back down at 10 s (front pieces first)
//   /render/demo?dof             soft depth of field focused on the camera target
//   /render/hymn_books           the reference sample's compiled timeline, played end to
//                                end: sets rising, puppets, props, camera, captions
//   /render/hymn_books?t=6       freeze scene time at 6 s
//   /render/hymn_books?narrator=mom   start with Mom's telling (a tab per teller switches)
//   /render/hymn_books?autoplay=0     wait for play (default 1)
//   /render/hymn_books?ui=0      hide the tabs and transport (captions stay)
//   /render/hymn_books?fps       print the frame rate in a corner (it is always in
//                                <html data-fps> for capture scripts)
//   /render/<id>?family=<fid>    a saved moment from the API, played like the sample
//                                (all the options above apply)
//   /render/<id> with window.__STITCH_RENDER__ = <MemoryResponse> injected before any
//                                script runs (the checker, api/agent/tools/frames.py): that
//                                scene, paused at ?t, bare (no chrome, no captions); the
//                                memory may be an unsaved draft, so nothing is fetched
//   /render/<anything else>      an empty stage
//
// Once the frame is fully drawn (art loaded, shadows settled) the page sets
// window.__STITCH_READY__ = true and <html data-stage-ready="1"> for capture scripts.

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
      narrator={one(q.narrator) ?? null}
      autoplay={one(q.autoplay) !== "0"}
      ui={one(q.ui) !== "0"}
      fps={q.fps !== undefined && one(q.fps) !== "0"}
      family={one(q.family)?.trim() || null}
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
