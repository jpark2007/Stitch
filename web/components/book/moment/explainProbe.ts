/**
 * The test hook for click-to-explain, `window.__stitchExplain`, installed
 * while a moment's stage can explain something (ExplainNote's layer):
 *
 *   __stitchExplain.targets()   what is clickable now: [{ target, x, y }], a point on each cutout's paper (CSS px)
 *   __stitchExplain.open(t)     open the note for target t ("puppet:dad"); true if it opened
 *   __stitchExplain.close()     close the open note
 *   __stitchExplain.current()   the open note's target, or null
 *   __stitchExplain.notes()     the notes standing or moving, with their card's screen box (CSS px) and phase
 *   __stitchExplain.hold(ms)    hold rising notes `ms` into their rise, for stills of the animation (null lets them go)
 *
 * Capture scripts click the points `targets()` gives, so they exercise the
 * real hit-testing; open / close are for jumping straight to a state.
 */

export interface ExplainPoint {
  target: string;
  x: number;
  y: number;
}

export interface ExplainNoteBox {
  target: string;
  phase: string;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface ExplainProbe {
  targets(): ExplainPoint[];
  open(target: string): boolean;
  close(): void;
  current(): string | null;
  notes(): ExplainNoteBox[];
  hold(ms: number | null): void;
}

declare global {
  interface Window {
    __stitchExplain?: ExplainProbe;
  }
}

/** Installs the hook; returns the uninstall (which leaves a newer hook alone). */
export function registerExplainProbe(probe: ExplainProbe): () => void {
  if (typeof window === "undefined") return () => {};
  window.__stitchExplain = probe;
  return () => {
    if (window.__stitchExplain === probe) delete window.__stitchExplain;
  };
}
