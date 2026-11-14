"use client";

/**
 * Timeline.effects (CONTRACTS.md: a list, not tracks: `{t, kind, at, dur,
 * value}`) played through the stage's paper particles.
 *
 * - confetti, steam, rain, smoke: one <PaperEffect> each, emitting from `at`
 *   (stage space) between t and t + dur. Particles are pure functions of
 *   stage time, which here is the player's scene time, so they pause, scrub
 *   and freeze with the scene.
 * - time_of_day: with the host's light `grade` (the standalone stage), the
 *   frame's timeOfDayFrom -> timeOfDay blend by timeOfDayMix re-lights the
 *   lamp (./grade: night dims and cools it, dusk warms it and keeps the rim).
 *   A host without one (the book's own lights) gets a soft tinted fill
 *   instead (dawn and dusk warm the paper, night adds a blue wash).
 */

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import { Color, type HemisphereLight } from "three";
import { PaperEffect, type EffectKind } from "../Effects";
import { blendGrade, type StageGrade } from "./grade";
import { PRIORITY, useScene } from "./SceneProvider";

const PARTICLES: ReadonlySet<string> = new Set<EffectKind>(["confetti", "steam", "rain", "smoke"]);
/** Effects with no duration run this long (seconds). */
const DEFAULT_DUR = 3;

export interface EffectBindingsProps {
  /**
   * The host lights' time-of-day grade (<StageLights grade>): blended here
   * from the frame every frame, so time_of_day re-lights the lamp. Without
   * one, time_of_day falls back to a soft tinted fill of its own.
   */
  grade?: StageGrade | null;
}

export function EffectBindings({ grade = null }: EffectBindingsProps) {
  const { scene } = useScene();
  const effects = useMemo(
    () =>
      (scene.timeline.effects ?? [])
        .map((e, i) => ({ e, i }))
        .filter(({ e }) => PARTICLES.has(e.kind)),
    [scene.timeline.effects],
  );
  const hasTimeOfDay = useMemo(() => (scene.timeline.effects ?? []).some((e) => e.kind === "time_of_day"), [scene.timeline.effects]);
  return (
    <>
      {effects.map(({ e, i }) => {
        const at = e.at && e.at.length >= 3 ? ([e.at[0], e.at[1], e.at[2]] as [number, number, number]) : ([0, 0.4, 0.5] as [number, number, number]);
        return (
          <PaperEffect
            key={i}
            kind={e.kind as EffectKind}
            origin={at}
            ground={e.kind === "confetti" || e.kind === "rain" ? 0 : undefined}
            start={e.t}
            end={e.t + (e.dur ?? DEFAULT_DUR)}
            seed={i + 1}
          />
        );
      })}
      {grade ? <GradeBinding grade={grade} /> : hasTimeOfDay && <TimeOfDayLight />}
    </>
  );
}

/** Blends the frame's time of day into the host's light grade (day when there is none). */
function GradeBinding({ grade }: { grade: StageGrade }) {
  const { frame } = useScene();
  useFrame(() => {
    blendGrade(grade, frame.timeOfDayFrom, frame.timeOfDay, frame.timeOfDayMix);
  }, PRIORITY.pose);
  return null;
}

/** Tint and strength of the fill light per time of day (parsed once). */
const TIMES: Record<string, { sky: Color; ground: Color; intensity: number }> = {
  dawn: { sky: new Color("#ffc7a3"), ground: new Color("#c99a7a"), intensity: 0.35 },
  day: { sky: new Color("#fff4e2"), ground: new Color("#d8c2a0"), intensity: 0 },
  dusk: { sky: new Color("#ff9f6e"), ground: new Color("#8a5a4a"), intensity: 0.45 },
  night: { sky: new Color("#5d7bb8"), ground: new Color("#2a3350"), intensity: 0.55 },
};

function TimeOfDayLight() {
  const { frame } = useScene();
  const light = useRef<HemisphereLight>(null);
  useFrame(() => {
    const l = light.current;
    if (!l) return;
    const to = TIMES[frame.timeOfDay ?? "day"] ?? TIMES.day;
    const from = TIMES[frame.timeOfDayFrom ?? frame.timeOfDay ?? "day"] ?? to;
    const k = frame.timeOfDayMix;
    l.color.copy(from.sky).lerp(to.sky, k);
    l.groundColor.copy(from.ground).lerp(to.ground, k);
    l.intensity = from.intensity + (to.intensity - from.intensity) * k;
  });
  return <hemisphereLight ref={light} intensity={0} />;
}
