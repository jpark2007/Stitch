"use client";

/**
 * Stage time. Every moving thing on the stage (hinges, camera, particles) is a
 * pure function of one clock, so a frozen time always renders the same frame:
 * the checker's headless captures and seeking depend on that.
 *
 * Without a <StageClock> above it, stage time is the Canvas clock.
 */

import { useFrame, useThree, type RootState } from "@react-three/fiber";
import { createContext, createElement, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";

interface ClockValue {
  /** Fixed stage time in seconds, or null to follow the Canvas clock. */
  frozen: number | null;
  /** Seconds added to the Canvas clock (to start a scene mid-way). */
  offset: number;
  /** An outside clock (a timeline player's scene time) that wins over both. */
  source: (() => number) | null;
}

const ClockContext = createContext<ClockValue>({ frozen: null, offset: 0, source: null });

export interface StageClockProps {
  time?: number | null;
  offset?: number;
  /**
   * Read stage time from this instead (e.g. `() => player.time`), so hinges,
   * particles and the camera follow a timeline player's clock: paused,
   * scrubbed or frozen. Keep it stable (useCallback / useMemo).
   */
  source?: (() => number) | null;
  children?: ReactNode;
}

export function StageClock({ time, offset = 0, source = null, children }: StageClockProps) {
  const frozen = time ?? null;
  const value = useMemo(() => ({ frozen, offset, source }), [frozen, offset, source]);
  return createElement(ClockContext.Provider, { value }, children);
}

/** Read stage time from r3f state: `const now = useStageTime(); useFrame((s) => now(s))`. */
export function useStageTime() {
  const { frozen, offset, source } = useContext(ClockContext);
  return useMemo(
    () =>
      source
        ? () => source()
        : (state: Pick<RootState, "clock">) => (frozen !== null ? frozen : state.clock.elapsedTime + offset),
    [frozen, offset, source],
  );
}

/** True when stage time is frozen (a still frame). */
export function useStageFrozen() {
  return useContext(ClockContext).frozen !== null;
}

/* ------------------------------------------------------------------------ */
/* Shadows on demand                                                         */
/* ------------------------------------------------------------------------ */

/**
 * A shadow caster moved this frame: redraw the shadow maps before this
 * frame renders. Every stage mover calls it (hinges while they swing,
 * particles while they fly). It costs nothing when shadow maps update every
 * frame anyway, and it is what keeps <StaticShadows> correct.
 */
export function markShadowsDirty(state: Pick<RootState, "gl">) {
  state.gl.shadowMap.needsUpdate = true;
}

/**
 * Switch the Canvas's shadow maps to on-demand updates: they are redrawn only
 * on frames where a mover called markShadowsDirty (plus a few warm-up frames),
 * so a settled diorama renders its shadows once instead of every frame.
 * It affects the whole Canvas, so mount it only where every moving caster
 * marks itself (the standalone <Stage> does; a host with its own moving
 * casters should not, or should mark them too).
 */
export function StaticShadows() {
  const get = useThree((s) => s.get);
  const warm = useRef(0);
  useEffect(() => {
    const sm = get().gl.shadowMap;
    sm.autoUpdate = false;
    sm.needsUpdate = true;
    warm.current = 0;
    return () => {
      sm.autoUpdate = true;
    };
  }, [get]);
  // Runs before the movers' frames (priority -1), so they can only add to it.
  useFrame((state) => {
    if (warm.current < 4) {
      warm.current += 1;
      state.gl.shadowMap.needsUpdate = true;
    }
  }, -1);
  return null;
}
