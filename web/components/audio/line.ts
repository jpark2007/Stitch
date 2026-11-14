/**
 * A gain that moves only along ramps the engine can account for: one or more
 * AudioParams driven together (each with its own scale), with the ramp kept
 * as data (fades.ts) so an interrupted fade restarts from exactly where it
 * was. That is what keeps quick era changes, mute taps and ducking click-free.
 */

import { rampPoints, rampValue, type Ramp, type Shape } from "./fades";

type Holdable = AudioParam & { cancelAndHoldAtTime?: (t: number) => AudioParam };

export class GainLine {
  private ramp: Ramp;

  constructor(
    private readonly params: [AudioParam, number][],
    value: number,
  ) {
    this.ramp = { from: value, to: value, t0: 0, dur: 0, shape: "smooth" };
    for (const [p, s] of params) p.value = value * s;
  }

  /** The line's value at `t` (unit scale). */
  valueAt(t: number) {
    return rampValue(this.ramp, t);
  }

  get target() {
    return this.ramp.to;
  }

  /** The time the current ramp settles. */
  get settles() {
    return this.ramp.t0 + this.ramp.dur;
  }

  /** Ramp from wherever the line is at `now` to `to` over `dur` seconds. */
  to(to: number, dur: number, now: number, shape: Shape = "smooth") {
    const from = this.valueAt(now);
    this.ramp = { from, to, t0: now, dur: Math.max(0, dur), shape };
    const pts = rampPoints(this.ramp, dur > 1 ? 32 : 16);
    for (const [p, s] of this.params) {
      const hp = p as Holdable;
      if (hp.cancelAndHoldAtTime) hp.cancelAndHoldAtTime(now);
      else p.cancelScheduledValues(now);
      p.setValueAtTime((dur > 0 ? from : to) * s, now);
      for (let i = 1; i < pts.length; i++) p.linearRampToValueAtTime(pts[i][1] * s, pts[i][0]);
    }
  }
}
