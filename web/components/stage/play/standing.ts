/**
 * How things on the stage stand up with the page.
 *
 * In a pop-up book nothing floats: a figure is glued to a layer and rises
 * with it. The timeline only folds set layers, so the stage carries everything
 * standing on the page along with them:
 *
 *  - on the page (y = 0): a puppet or grounded prop hinges about its own feet
 *    with the layer standing behind it (the nearest layer at or behind its z);
 *  - on a raised mark (a riser, see risersFor): it rides the riser's top as
 *    that parallel-fold platform sinks towards the reader, and hinges about
 *    its feet on it.
 *
 * Folded flat, a figure lies towards the reader just above the folded cards.
 * Risen (fold 1) every mount is the identity, so the timeline's positions are
 * used exactly as compiled.
 *
 * Pure (type imports only): node runs the tests.
 */

import type { SetDef } from "@/lib/types";
import type { PlaybackFrame, SetLayerFrame } from "../playback";

/** Stage z of a layer depth (0 back, 1 middle, 2 front). */
export const layerZ = (depth: number) => depth * 0.35;

/**
 * Fold to hinge rise. CONTRACTS.md: `fold` is 0 flat .. 1 risen and the stage
 * plays it, it does not decide it, so this is the fold itself (clamped). Any
 * easing or spring in a rise is the compiler's, authored as fold keys.
 */
export function foldToRise(fold: number): number {
  return fold <= 0 ? 0 : fold >= 1 ? 1 : fold;
}

/** Hinge angle for a rise: 0 upright, π/2 flat towards the reader. */
export const riseAngle = (rise: number) => (1 - rise) * (Math.PI / 2);

/** A paper platform under a raised mark. */
export interface Riser {
  /** Mark name */
  key: string;
  /** Centre x, top height, standing z (stage units) */
  x: number;
  h: number;
  z: number;
  halfWidth: number;
  /** Index into set.layers of the layer whose fold it follows (the one behind it). */
  layer: number;
  /** Colour of that layer (the riser is cut from the same card). */
  color: string | null;
}

/** Riser depth (z) and card thickness: shared by the riser drawing and the mount maths. */
export const RISER_DEPTH = 0.24;
export const RISER_THICK = 0.007;
/** How high a figure lies above the page when folded flat (above the stacked cards). */
const FLAT_LIFT = 0.045;

/** The layer standing at or behind stage z (the back layer when none is). */
function layerBehind(set: SetDef, z: number): number {
  let layer = 0;
  let best = -Infinity;
  set.layers.forEach((l, i) => {
    const lz = layerZ(l.depth);
    if (lz <= z + 1e-6 && lz > best) {
      best = lz;
      layer = i;
    }
  });
  return layer;
}

/** A riser under every mark with a standing surface above the page, kept clear of lower marks nearby. */
export function risersFor(set: SetDef): Riser[] {
  const marks = Object.entries(set.marks ?? {}).map(([name, m]) => ({
    name,
    x: 2 * (m[0] ?? 0.5) - 1,
    h: m[1] ?? 0,
    z: m[2] ?? 0.5,
  }));
  const out: Riser[] = [];
  for (const m of marks) {
    if (!(m.h > 0.02)) continue;
    let room = 0.16;
    for (const o of marks) if (o !== m && o.h < m.h - 0.02) room = Math.min(room, Math.abs(o.x - m.x) - 0.07);
    const layer = layerBehind(set, m.z);
    out.push({ key: m.name, x: m.x, h: m.h, z: m.z, halfWidth: Math.max(0.08, room), layer, color: set.layers[layer]?.color ?? null });
  }
  return out;
}

/** The transform that stands a point up with the page: rotate by `angle` about the pivot, then move by (0, dy, dz). */
export interface Mount {
  angle: number;
  dy: number;
  dz: number;
}

interface SetStanding {
  set: SetDef;
  /** Layer frames by layer index (null when that layer has no fold track). */
  folds: (SetLayerFrame | null)[];
  risers: Riser[];
}

export class Standing {
  private readonly sets: SetStanding[];
  private readonly byId = new Map<string, SetStanding>();
  private readonly frame: PlaybackFrame;
  private readonly reveal: (() => number) | null;

  /**
   * @param reveal  an outer cap on every fold, 0..1 (the book's page turn:
   *                the whole scene folds flat as a leaf comes over it).
   */
  constructor(sets: readonly SetDef[], frame: PlaybackFrame, reveal: (() => number) | null = null) {
    this.frame = frame;
    this.reveal = reveal;
    this.sets = [];
    for (const set of sets) {
      const folds = set.layers.map((_, i) => frame.layersByTarget.get(`set:${set.id}.layer${i}`) ?? null);
      if (!folds.some(Boolean)) continue;
      const s = { set, folds, risers: risersFor(set) };
      this.sets.push(s);
      this.byId.set(set.id, s);
    }
  }

  /** A layer's fold now: its track, capped by the reveal (1 without a track). */
  fold(lf: SetLayerFrame | null | undefined): number {
    const f = lf ? lf.fold : 1;
    if (!this.reveal) return f;
    const r = this.reveal();
    return r < f ? (r > 0 ? r : 0) : f;
  }

  /** The risers of a set (for drawing them). */
  risers(setId: string): readonly Riser[] {
    return this.byId.get(setId)?.risers ?? [];
  }

  /**
   * The mount for something standing at stage (x, y, z) now, in the current
   * shot's set. Writes into `out`; identity when the set is risen or there is
   * no set.
   */
  mount(x: number, y: number, z: number, out: Mount): Mount {
    out.angle = 0;
    out.dy = 0;
    out.dz = 0;
    const shotSet = this.frame.shot?.set;
    const s = (shotSet !== undefined ? this.byId.get(shotSet) : undefined) ?? this.sets[0];
    if (!s) return out;
    if (y > 0.02) {
      for (const r of s.risers) {
        if (Math.abs(r.h - y) > 0.04 || Math.abs(r.x - x) > r.halfWidth + 0.06) continue;
        const rise = foldToRise(this.fold(s.folds[r.layer]));
        const a = riseAngle(rise);
        const k = 1 - smooth(rise);
        // The riser's top rides its walls (FloatingLayer): height H·cos a
        // (plus a little card lift when flat) and H·sin a towards the reader.
        const H = r.h - RISER_THICK;
        out.angle = a;
        out.dy = H * (Math.cos(a) - 1) + k * RISER_THICK * 1.6 + k * FLAT_LIFT;
        out.dz = H * Math.sin(a);
        return out;
      }
    }
    const rise = foldToRise(this.fold(s.folds[layerBehind(s.set, z)]));
    out.angle = riseAngle(rise);
    out.dy = (1 - smooth(rise)) * FLAT_LIFT;
    return out;
  }
}

function smooth(x: number): number {
  const k = x <= 0 ? 0 : x >= 1 ? 1 : x;
  return k * k * (3 - 2 * k);
}
