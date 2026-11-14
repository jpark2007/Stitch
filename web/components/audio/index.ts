/**
 * Era music: gentle generated music per era, synthesised live with WebAudio.
 *
 *   useEraMusic(year, { active, ducked })   React: the book's one-liner
 *   createEraMusic()                        imperative: setEra / setDucked / setActive / dispose
 *   eraForYear(year)                        the vocabulary's era boundaries
 *
 * See engine.ts for the lifecycle (lazy, gesture-started, mute-obeying) and
 * palettes.ts for what each era sounds like. /render/lab/music auditions it.
 */

export { createEraMusic, type EraMusic, type EraMusicStats } from "./engine";
export { useEraMusic } from "./useEraMusic";
export { ERAS, ERA_IDS, eraForYear, eraMidYear, isEraId, type EraId, type EraRange } from "./eras";
export { PALETTES } from "./palettes";
export type { MeterReading } from "./core";
