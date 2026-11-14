"use client";

/**
 * Era music for a component: play the music of `year`'s era while `active`
 * (the book is open), ducked under narration while `ducked`.
 *
 *   const music = useEraMusic(spread?.year ?? null, { active: view.opened, ducked: narrating });
 *   useEffect(() => music.prefetchYear(nextSpread?.year ?? null), [music, nextSpread]);
 *
 * The engine lives in a ref and is driven from effects, so audio never
 * causes a React render, and the returned handle is stable (safe in effect
 * dependencies). The engine is created with the latest era, activity and
 * ducking; StrictMode's double mount creates and disposes a first engine
 * that never made a sound (engines are lazy).
 */

import { useEffect, useMemo, useRef } from "react";
import { createEraMusic, type EraMusic } from "./engine";
import { eraForYear } from "./eras";

export interface EraMusicHandle {
  /** Make the era of an upcoming year (the next spread's) ready to start instantly. */
  prefetchYear(year: number | null): void;
  /** The engine, for meters and stats; null before mount and after unmount. */
  engine(): EraMusic | null;
}

export function useEraMusic(year: number | null, opts: { active: boolean; ducked?: boolean }): EraMusicHandle {
  const music = useRef<EraMusic | null>(null);
  const era = eraForYear(year);
  const { active } = opts;
  const ducked = opts.ducked ?? false;
  // The latest values, for the engine made on mount (effects run in order).
  const latest = useRef({ era, active, ducked });
  useEffect(() => {
    latest.current = { era, active, ducked };
  });

  useEffect(() => {
    const m = createEraMusic(latest.current);
    music.current = m;
    return () => {
      m.dispose();
      if (music.current === m) music.current = null;
    };
  }, []);

  useEffect(() => {
    music.current?.setEra(era);
  }, [era]);

  useEffect(() => {
    music.current?.setActive(active);
  }, [active]);

  useEffect(() => {
    music.current?.setDucked(ducked);
  }, [ducked]);

  return useMemo<EraMusicHandle>(
    () => ({
      prefetchYear: (y) => music.current?.prefetch(eraForYear(y)),
      engine: () => music.current,
    }),
    [],
  );
}
