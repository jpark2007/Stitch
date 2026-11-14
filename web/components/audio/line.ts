/**
 * Gains that move only along ramps the engine can account for, each ramp
 * kept as data (fades.ts) so its value at any time is known exactly.
 *
 *   GainLine   a level that can be moved again mid-fade (mute taps, the duck,
 *              the book closing): an interrupted fade restarts from exactly
 *              where it was, using cancelAndHoldAtTime where the browser has it
 *   OnceRamp   a gain that moves once, ever: a voice's fade-in, fade-out and
 *              cut are three of these in series, so an interrupted crossfade
 *              never cancels automation (which can snap the value in browsers
 *              without cancelAndHoldAtTime)
 */

import { rampPoints, rampValue, type Ramp, type Shape } from "./fades";

export class OnceRamp {
  private ramp: Ramp | null = null;

  /** `params`: each AudioParam with its scale (the ramp's unit value times it). */
  constructor(
    private readonly params: [AudioParam, number][],
    private readonly rest: number,
  ) {
    for (const [p, s] of params) p.value = rest * s;
  }

  get fired() {
    return this.ramp !== null;
  }

  valueAt(t: number) {
    return this.ramp ? rampValue(this.ramp, t) : this.rest;
  }

  /** Ramp from rest to `to`. Only the first call does anything; returns whether it did. */
  fire(to: number, dur: number, now: number, shape: Shape): boolean {
    if (this.ramp) return false;
    this.ramp = { from: this.rest, to, t0: now, dur: Math.max(0, dur), shape };
    const pts = rampPoints(this.ramp, dur > 1 ? 32 : 16);
    for (const [p, s] of this.params) {
      p.setValueAtTime((dur > 0 ? this.rest : to) * s, now);
      for (let i = 1; i < pts.length; i++) p.linearRampToValueAtTime(pts[i][1] * s, pts[i][0]);
    }
    return true;
  }
}

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
