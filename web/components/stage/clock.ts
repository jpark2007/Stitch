"use client";

/**
 * Stage time. Every moving thing on the stage (hinges, camera, particles) is a
 * pure function of one clock, so a frozen time always renders the same frame:
 * the checker's headless captures and seeking depend on that.
 *
 * Without a <StageClock> above it, stage time is the Canvas clock.
 */

import { createContext, createElement, useContext, useMemo, type ReactNode } from "react";
import type { RootState } from "@react-three/fiber";

interface ClockValue {
  /** Fixed stage time in seconds, or null to follow the Canvas clock. */
  frozen: number | null;
  /** Seconds added to the Canvas clock (to start a scene mid-way). */
  offset: number;
}

const ClockContext = createContext<ClockValue>({ frozen: null, offset: 0 });

export function StageClock({ time, offset = 0, children }: { time?: number | null; offset?: number; children?: ReactNode }) {
  const frozen = time ?? null;
  const value = useMemo(() => ({ frozen, offset }), [frozen, offset]);
  return createElement(ClockContext.Provider, { value }, children);
}

/** Read stage time from r3f state: `const now = useStageTime(); useFrame((s) => now(s))`. */
export function useStageTime() {
  const { frozen, offset } = useContext(ClockContext);
  return useMemo(
    () => (state: Pick<RootState, "clock">) => (frozen !== null ? frozen : state.clock.elapsedTime + offset),
    [frozen, offset],
  );
}

/** True when stage time is frozen (a still frame). */
export function useStageFrozen() {
  return useContext(ClockContext).frozen !== null;
}
