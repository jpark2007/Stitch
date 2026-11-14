"use client";

/**
 * Era music for a component: play the music of `year`'s era while `active`
 * (the book is open), ducked under narration while `ducked`.
 *
 *   useEraMusic(spread?.year ?? null, { active: view.opened, ducked: narrating });
 *
 * The engine lives in a ref and is driven from effects, so audio never
 * causes a React render; StrictMode's double mount creates and disposes a
 * first engine that never made a sound (engines are lazy). Returns the ref
 * for diagnostics (meter, stats).
 */

import { useEffect, useRef } from "react";
import { createEraMusic, type EraMusic } from "./engine";
import { eraForYear } from "./eras";

export function useEraMusic(year: number | null, opts: { active: boolean; ducked?: boolean }) {
  const music = useRef<EraMusic | null>(null);
  const era = eraForYear(year);
  const { active } = opts;
  const ducked = opts.ducked ?? false;

  // Declared first, so on every mount the engine exists before the effects
  // below hand it the current era, activity and ducking.
  useEffect(() => {
    const m = createEraMusic({ active: false });
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

  return music;
}
