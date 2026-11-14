"use client";

import { useFrame } from "@react-three/fiber";
import { motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { preloadPaperTextures } from "@/components/paper/materials";
import { blendPose, stagePoses, type CameraPose } from "@/components/stage/CameraRig";
import { preloadCutouts } from "@/components/stage/cutout";
import { EFFECT_KINDS, type EffectKind } from "@/components/stage/Effects";
import { COVER_SPAN, Stage } from "@/components/stage/Stage";
import { smootherstep } from "@/components/stage/timeline";
import { DEMO_CUTOUTS, DemoDiorama, demoCamera, DEMO_OPEN_AT } from "./demo";

export interface RenderViewProps {
  memoryId: string;
  /** Frozen stage time, or null to play. */
  time: number | null;
  /** Scrubbed pop-up state, or null to play the opening. */
  open: number | null;
  /** Effect override: a kind, "none", or null for the scene's default. */
  effect: string | null;
  /** Stage time at which the set folds back down, or null. */
  closeAt: number | null;
  /** Depth of field on. */
  dof?: boolean;
}

/** The closed book, centred: the intro state a scrubbed ?open=0 starts from. */
const CLOSED_POSE: CameraPose = { position: [1.45, 4.6, 5.1], target: [0.0, 0.0, 0.2], fov: 30 };

/** Marks the document once a few frames have actually been drawn. */
function ReadyProbe() {
  const frames = useRef(0);
  useFrame(() => {
    frames.current += 1;
    if (frames.current === 3) document.documentElement.dataset.stageReady = "1";
  });
  return null;
}

export function RenderView({ memoryId, time, open, effect, closeAt, dof = false }: RenderViewProps) {
  const demo = memoryId === "demo";
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let live = true;
    Promise.all([
      preloadPaperTextures({ kinds: ["paper", "card"], wood: true }),
      demo ? preloadCutouts(DEMO_CUTOUTS) : Promise.resolve(),
    ]).then(() => live && setReady(true));
    return () => {
      live = false;
    };
  }, [demo]);

  if (!ready) return null;

  // Scrubbing the pop-up implies a still frame.
  const frozen = time ?? (open !== null ? 0 : null);
  const effectKind: EffectKind | null | undefined =
    effect === null ? undefined : (EFFECT_KINDS as readonly string[]).includes(effect) ? (effect as EffectKind) : null;

  return (
    <motion.div
      className="absolute inset-0"
      initial={{ opacity: frozen === null ? 0 : 1 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
    >
      <Stage
        className="absolute inset-0"
        time={frozen}
        open={open ?? true}
        openAt={DEMO_OPEN_AT}
        closeAt={closeAt}
        dof={dof}
        camera={
          open !== null
            ? blendPose(CLOSED_POSE, stagePoses().wide, smootherstep(open / COVER_SPAN))
            : demo
              ? demoCamera
              : stagePoses().wide
        }
        overlay={<ReadyProbe />}
      >
        {demo && <DemoDiorama effect={effectKind} />}
      </Stage>
      {/* Soft lens vignette. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{ background: "radial-gradient(120% 95% at 50% 42%, transparent 58%, rgb(20 12 6 / 0.5))" }}
      />
    </motion.div>
  );
}
