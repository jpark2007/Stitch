/**
 * Time of day as a light grade: a timeline's `time_of_day` effect (dawn,
 * day, dusk, night) re-lights the reading lamp instead of washing colour over
 * the paper, so night really dims and cools the scene.
 *
 * A grade is multipliers and tints for StageLights' lamp (key), fill and
 * bounce, rim and hemisphere. "day" (and no time_of_day effect at all) is the
 * identity: the stage's normal warm lamp. The scene blends the frame's
 * previous and current time of day by its mix into one mutable StageGrade
 * every frame (EffectBindings), and the host's lights apply it (<StageLights
 * grade>). Allocation-free after createGrade().
 */

import { Color } from "three";

export interface StageGrade {
  /** Lamp (key) intensity multiplier, and a tint multiplied into its colour */
  key: number;
  readonly keyTint: Color;
  /** Fill and bounce multiplier */
  fill: number;
  /** Rim multiplier */
  rim: number;
  /** Hemisphere multiplier and tint */
  hemi: number;
  readonly hemiTint: Color;
}

interface GradeDef {
  key: number;
  keyTint: Color;
  fill: number;
  rim: number;
  hemi: number;
  hemiTint: Color;
}

const def = (key: number, keyTint: string, fill: number, rim: number, hemi: number, hemiTint: string): GradeDef => ({
  key,
  keyTint: new Color(keyTint),
  fill,
  rim,
  hemi,
  hemiTint: new Color(hemiTint),
});

/** Parsed once. Dusk keeps the rim strong so cut edges still glow; night is moonlit. */
const GRADES: Readonly<Record<string, GradeDef>> = {
  day: def(1, "#ffffff", 1, 1, 1, "#ffffff"),
  dawn: def(0.84, "#ffdcd0", 0.9, 1.15, 0.9, "#f4dcd6"),
  dusk: def(0.74, "#ffb487", 0.72, 1.35, 0.8, "#e7bfa4"),
  night: def(0.5, "#9fb4ff", 0.42, 0.6, 0.55, "#7f95d6"),
};

/** A fresh identity grade (day). */
export function createGrade(): StageGrade {
  return { key: 1, keyTint: new Color(1, 1, 1), fill: 1, rim: 1, hemi: 1, hemiTint: new Color(1, 1, 1) };
}

/**
 * Writes the blend of time of day `from` -> `to` at `mix` (0..1, eased) into
 * `out` and returns it. Null or unknown names are day.
 */
export function blendGrade(out: StageGrade, from: string | null, to: string | null, mix: number): StageGrade {
  const b = GRADES[to ?? "day"] ?? GRADES.day;
  const a = GRADES[from ?? to ?? "day"] ?? b;
  const m = Math.min(1, Math.max(0, mix));
  const k = m * m * (3 - 2 * m);
  out.key = a.key + (b.key - a.key) * k;
  out.fill = a.fill + (b.fill - a.fill) * k;
  out.rim = a.rim + (b.rim - a.rim) * k;
  out.hemi = a.hemi + (b.hemi - a.hemi) * k;
  out.keyTint.copy(a.keyTint).lerp(b.keyTint, k);
  out.hemiTint.copy(a.hemiTint).lerp(b.hemiTint, k);
  return out;
}
