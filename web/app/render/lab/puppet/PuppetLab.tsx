"use client";

import { Canvas } from "@react-three/fiber";
import { motion } from "framer-motion";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { preloadPaperTextures } from "@/components/paper/materials";
import { POSE_NAMES } from "@/components/stage/puppet/rig";
import { BACKDROP_MASK, LabScene, type LabSettings } from "./LabScene";

const label = (pose: string) => pose.replace(/_/g, " ");

function num(v: string | null): number | null {
  if (v === null || v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export function PuppetLab() {
  const params = useSearchParams();
  const initialPose = params.get("pose");
  const [pose, setPose] = useState(initialPose && POSE_NAMES.includes(initialPose) ? initialPose : "stand");
  const [walk, setWalk] = useState(params.get("walk") === "1");
  const [reach, setReach] = useState(params.get("reach") !== "0");
  const freeze = num(params.get("t"));
  const crowd = Math.max(0, Math.min(24, Math.round(num(params.get("crowd")) ?? 0)));
  const focus = num(params.get("focus"));
  const showUi = params.get("ui") !== "0";

  const [ready, setReady] = useState(false);
  useEffect(() => {
    let live = true;
    preloadPaperTextures({ kinds: ["paper", "card"], wood: false, masks: [BACKDROP_MASK] }).then(
      () => live && setReady(true),
    );
    return () => {
      live = false;
    };
  }, []);

  const settings: LabSettings = { pose, walk, reach, freeze, crowd, focus };

  return (
    <main className="relative h-dvh w-full overflow-hidden bg-paper">
      {ready && (
        <motion.div
          className="absolute inset-0"
          initial={{ opacity: freeze === null ? 0 : 1 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
        >
          <Canvas
            shadows="percentage"
            dpr={[1, 2]}
            camera={{ position: [0, 0.5, 3.05], fov: 27, near: 0.05, far: 40 }}
            gl={{ antialias: true }}
          >
            <LabScene settings={settings} />
          </Canvas>
        </motion.div>
      )}

      {/* Warm vignette, like a lamp over the page. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{ background: "radial-gradient(120% 95% at 50% 40%, transparent 58%, rgb(74 47 29 / 0.28))" }}
      />

      {showUi && (
        <>
          <header className="pointer-events-none absolute left-[var(--gutter)] top-6 sm:top-8">
            <p className="kicker">Stitch · lab</p>
            <h1 className="mt-1 text-2xl sm:text-3xl">The paper puppet</h1>
          </header>

          <p
            id="fps"
            aria-live="off"
            className="kicker pointer-events-none absolute right-[var(--gutter)] top-7 tabular-nums sm:top-9"
          />

          <nav
            aria-label="Puppet controls"
            className="absolute inset-x-0 bottom-5 mx-auto flex w-fit max-w-[calc(100%-2rem)] flex-wrap items-center justify-center gap-x-1 gap-y-1.5 rounded-[var(--radius-card)] bg-paper-light/90 px-3 py-2.5 shadow-lift backdrop-blur-sm sm:bottom-7"
          >
            {POSE_NAMES.map((p) => (
              <Chip key={p} active={p === pose} onClick={() => setPose(p)}>
                {label(p)}
              </Chip>
            ))}
            <span aria-hidden className="mx-2 h-5 w-px border-l border-dashed border-ink-faint/50" />
            <Chip active={walk} onClick={() => setWalk((w) => !w)} pressed>
              walk
            </Chip>
            <Chip active={reach} onClick={() => setReach((r) => !r)} pressed>
              reach
            </Chip>
          </nav>

          {reach && crowd === 0 && focus === null && (
            <p className="caption pointer-events-none absolute bottom-24 right-[calc(var(--gutter)+2rem)] hidden rotate-[-3deg] sm:block">
              drag the yarn ball
            </p>
          )}
        </>
      )}
    </main>
  );
}

function Chip({
  active,
  pressed,
  onClick,
  children,
}: {
  active: boolean;
  pressed?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={pressed ? active : undefined}
      aria-current={!pressed && active ? "true" : undefined}
      className={`rounded-full px-3 py-1 text-sm transition-colors duration-200 ${
        active ? "bg-accent text-paper-light shadow-paper" : "text-ink-soft hover:bg-paper-deep hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}
