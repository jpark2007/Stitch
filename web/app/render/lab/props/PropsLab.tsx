"use client";

/**
 * Props lab: PropActor on a paper stage, one demo at a time.
 *
 *   ?demo=ground   every size class on the ground (default)
 *   ?demo=drive    a car with spinning wheel parts, a basket on its roof
 *   ?demo=states   a suitcase with two states (?state=closed|open to hold one)
 *   ?demo=held     a prop held by its grip on a draggable hand; Drop / Pick up
 *                  (?mode=dropped, ?facing=left)
 *   ?demo=closeup  a close look at placeholder cards
 *   ?t=<seconds>   freeze time, for stills
 *   ?chrome=0      hide the labels and controls
 *
 * When every prop's art is in, <html data-props-ready="1"> is set, for
 * headless screenshots.
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { motion } from "framer-motion";
import { preloadPaperTextures } from "@/components/paper/materials";
import type { PropActorHandle } from "@/components/stage/PropActor";
import {
  CloseupDemo,
  DriveDemo,
  GroundDemo,
  HeldDemo,
  HeldHand,
  StatesDemo,
  type DemoName,
  type ReadyList,
} from "./demos";
import { Framing, LabStage, type Shot } from "./LabStage";

const DEMOS: { id: DemoName; label: string; note: string; shot: Shot }[] = [
  {
    id: "ground",
    label: "On the ground",
    note: "Every size class, art not drawn yet: placeholder cards in each prop's colour.",
    shot: { position: [0, 1.05, 4.5], lookAt: [0, 0.55, 0.4], fov: 30 },
  },
  {
    id: "drive",
    label: "Drive",
    note: "Wheel parts turn as the car rolls. The basket rests on the car's roof socket.",
    shot: { position: [0, 0.75, 4.1], lookAt: [0, 0.42, 0.5], fov: 30 },
  },
  {
    id: "states",
    label: "States",
    note: "One prop, two states, each its own art: closed and open.",
    shot: { position: [0, 0.3, 1.55], lookAt: [0, 0.1, 0.5], fov: 28 },
  },
  {
    id: "held",
    label: "Held",
    note: "Drag the hand. The suitcase hangs from its grip; drop it and it lands on its anchor.",
    shot: { position: [0, 0.48, 2.3], lookAt: [0, 0.26, 0.5], fov: 30 },
  },
  {
    id: "closeup",
    label: "Close-up",
    note: "The sample's hymn books and a ring, as placeholder cards.",
    shot: { position: [0.02, 0.085, 1.0], lookAt: [0.02, 0.04, 0.5], fov: 22 },
  },
];

function num(v: string | null): number | null {
  if (v === null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export default function PropsLab() {
  const params = useSearchParams();
  const demo = DEMOS.find((d) => d.id === params.get("demo")) ?? DEMOS[0];
  const fixedT = num(params.get("t"));
  const chrome = params.get("chrome") !== "0";
  const stateParam = params.get("state");
  const fixedState = stateParam === "open" || stateParam === "closed" ? stateParam : null;
  const dropped = params.get("mode") === "dropped";
  const facing = params.get("facing") === "left" ? -1 : 1;

  const [texturesReady, setTexturesReady] = useState(false);
  const [state, setState] = useState("");
  const [held, setHeld] = useState(!dropped);
  const control = useMemo(() => new HeldHand(dropped, facing), [dropped, facing]);

  useEffect(() => {
    let live = true;
    preloadPaperTextures({ wood: true }).then(() => live && setTexturesReady(true));
    return () => {
      live = false;
    };
  }, []);

  return (
    <main className="fixed inset-0 overflow-hidden bg-wood-dark">
      {texturesReady && (
        <motion.div
          className="absolute inset-0"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
        >
          <Canvas
            shadows="percentage"
            dpr={[1, 2]}
            camera={{ position: demo.shot.position, fov: demo.shot.fov, near: 0.02, far: 40 }}
            gl={{ antialias: true }}
          >
            <Framing shot={demo.shot} />
            <LabStage />
            <Demo demo={demo.id} fixedT={fixedT} fixedState={fixedState} onState={setState} control={control} />
          </Canvas>
        </motion.div>
      )}

      {chrome && (
        <>
          <header className="pointer-events-none absolute left-[var(--gutter)] top-6 max-w-sm rounded-[var(--radius-card)] bg-paper-light/92 px-5 py-4 shadow-paper">
            <p className="kicker">Stitch · stage lab</p>
            <h1 className="mt-1 text-2xl sm:text-3xl">Props</h1>
            <p className="mt-1.5 text-sm leading-snug text-ink-soft">{demo.note}</p>
          </header>

          <nav className="absolute bottom-6 left-1/2 flex -translate-x-1/2 gap-1.5 rounded-[var(--radius-soft)] bg-paper-light/90 p-1.5 shadow-lift">
            {DEMOS.map((d) => (
              <Link
                key={d.id}
                href={`?demo=${d.id}`}
                replace
                scroll={false}
                className={`rounded-[10px] px-3.5 py-1.5 text-sm transition-colors ${
                  d.id === demo.id ? "bg-ink text-paper-light" : "text-ink-soft hover:bg-paper-deep"
                }`}
              >
                {d.label}
              </Link>
            ))}
          </nav>

          {demo.id === "states" && (
            <p className="caption absolute right-[var(--gutter)] top-8 text-3xl">{state}</p>
          )}

          {demo.id === "held" && (
            <div className="absolute right-[var(--gutter)] top-8 flex gap-2">
              <button
                type="button"
                className="rounded-[10px] bg-paper-light px-4 py-2 text-sm shadow-paper transition-colors hover:bg-paper-deep"
                onClick={() => {
                  if (held) control.drop();
                  else control.pickUp();
                  setHeld(!held);
                }}
              >
                {held ? "Drop" : "Pick up"}
              </button>
              <button
                type="button"
                className="rounded-[10px] bg-paper-light px-4 py-2 text-sm shadow-paper transition-colors hover:bg-paper-deep"
                onClick={() => control.turn()}
              >
                Turn hand
              </button>
            </div>
          )}
        </>
      )}
    </main>
  );
}

function Demo({
  demo,
  fixedT,
  fixedState,
  onState,
  control,
}: {
  demo: DemoName;
  fixedT: number | null;
  fixedState: string | null;
  onState: (s: string) => void;
  control: HeldHand;
}) {
  const ready: ReadyList = useRef<(PropActorHandle | null)[]>([]);
  return (
    <>
      <ReadyProbe ready={ready} demo={demo} />
      {demo === "ground" && <GroundDemo ready={ready} />}
      {demo === "drive" && <DriveDemo ready={ready} fixedT={fixedT} />}
      {demo === "states" && <StatesDemo ready={ready} fixedState={fixedState} onState={onState} />}
      {demo === "held" && <HeldDemo ready={ready} control={control} fixedT={fixedT} />}
      {demo === "closeup" && <CloseupDemo ready={ready} />}
    </>
  );
}

/** Sets <html data-props-ready="1"> once every prop in the demo can draw. */
function ReadyProbe({ ready, demo }: { ready: ReadyList; demo: DemoName }) {
  const frames = useRef(0);
  useEffect(() => {
    frames.current = 0;
    delete document.documentElement.dataset.propsReady;
  }, [demo]);
  useFrame(() => {
    const list = ready.current;
    const all = list.length > 0 && list.every((h) => h?.ready);
    // A couple of frames after everything is ready, so it has been drawn.
    frames.current = all ? frames.current + 1 : 0;
    if (frames.current === 3) document.documentElement.dataset.propsReady = "1";
  });
  return null;
}
