/**
 * One test hook for capture scripts, `window.__stitchMoment`: the playing
 * moment's player, and where its controls are on screen, so a script can
 * switch tellers and pull tabs without guessing.
 *
 *   __stitchMoment.time()        scene time, seconds
 *   __stitchMoment.narrator()    the teller being heard (person id)
 *   __stitchMoment.caption()     the caption showing now, or null
 *   __stitchMoment.playing()     the scene is playing
 *   __stitchMoment.tab(id)       a pull tab's grip on screen (CSS px), how far a full pull travels there,
 *                                whether it takes a pull now (live) and how far out of its slit it is (out)
 *   __stitchMoment.tellers()     each teller label's centre on screen, by teller id (the open spread)
 *   __stitchMoment.shot()        the shot on stage now (id), or null
 *   __stitchMoment.held(id)      the value an interaction is held at, or null
 *
 * The page installs it (installMomentProbe); each pull tab registers where
 * it is (registerTabProbe); the book reports its labels (__stitchBook.labels).
 */

import type { PlaybackController } from "@/components/stage/playback";

export interface ScreenPoint {
  x: number;
  y: number;
}

export interface TabPoint extends ScreenPoint {
  /** Screen distance (CSS px) of a full pull. */
  travel: number;
  /** Screen direction (unit) the tab pulls out along. */
  dx: number;
  dy: number;
  /** It takes a pull now (pullTabGate). */
  live: boolean;
  /** How far out of its slit the card is, 0 home .. 1 out. */
  out: number;
}

export interface MomentProbe {
  time(): number;
  narrator(): string | null;
  caption(): string | null;
  playing(): boolean;
  tab(id: string): TabPoint | null;
  tellers(): Record<string, ScreenPoint>;
  shot(): string | null;
  held(id: string): number | null;
}

declare global {
  interface Window {
    __stitchMoment?: MomentProbe;
    __stitchBook?: { labels?: () => Record<string, ScreenPoint> } & Record<string, unknown>;
  }
}

const tabs = new Map<string, () => TabPoint | null>();

/** A pull tab says where it is on screen. Returns the unregister. */
export function registerTabProbe(id: string, where: () => TabPoint | null): () => void {
  tabs.set(id, where);
  return () => {
    if (tabs.get(id) === where) tabs.delete(id);
  };
}

/** Installs `window.__stitchMoment` for a moment's player. Returns the uninstall. */
export function installMomentProbe(controller: PlaybackController): () => void {
  const probe: MomentProbe = {
    time: () => controller.time,
    narrator: () => controller.getStatus().narrator,
    caption: () => {
      const s = controller.getStatus();
      return controller.compiled.narrator(s.narrator)?.captions[s.captionIndex]?.text ?? null;
    },
    playing: () => controller.getStatus().playing,
    tab: (id) => tabs.get(id)?.() ?? null,
    tellers: () => window.__stitchBook?.labels?.() ?? {},
    shot: () => controller.frame.shot?.id ?? null,
    held: (id) => controller.heldValue(id),
  };
  window.__stitchMoment = probe;
  return () => {
    if (window.__stitchMoment === probe) delete window.__stitchMoment;
  };
}
