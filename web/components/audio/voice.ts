/**
 * One era playing: its score loop, its parts' buses and a crossfade gain.
 * A crossfade is two of these running briefly in parallel, one fading in
 * while the other fades out and then frees itself.
 *
 * `schedule(now, horizon)` is called by the engine's lookahead timer (never
 * from a render loop): it creates the notes that start before
 * `now + horizon`, each at its exact AudioContext time, and walks the loop
 * bar by bar for ever.
 */

import { CROSSFADE, dbToGain, type Shape } from "./fades";
import { GainLine } from "./line";
import { createPartBus, playNote, startBed, type Kit, type PartBus } from "./patches";
import { scoreLoop, type Palette, type ScoreLoop } from "./score";

/** Seconds a note can ring after its last event (longest release, echoes). */
const TAIL = 4.5;
/** Strings buffers to build per scheduling pass while warming up. */
const WARM_PER_PASS = 3;

export class EraVoice {
  readonly id: string;
  private readonly loop: ScoreLoop;
  private readonly dry: GainNode;
  private readonly wet: GainNode;
  private readonly line: GainLine;
  private readonly buses: (PartBus | null)[];
  private readonly beds: AudioScheduledSourceNode[] = [];
  private bar = 0;
  private barStart: number;
  private cursor = 0;
  /** No notes start at or after this (set when the voice is told to end). */
  private stopAt = Infinity;
  private warm: [string, number][];
  disposed = false;

  constructor(
    private readonly kit: Kit,
    readonly palette: Palette,
    dest: { dry: AudioNode; wet: AudioNode },
    t0: number,
  ) {
    const ctx = kit.ctx;
    this.id = palette.id;
    this.loop = scoreLoop(palette);
    this.barStart = t0;
    this.dry = ctx.createGain();
    this.wet = ctx.createGain();
    this.dry.connect(dest.dry);
    this.wet.connect(dest.wet);
    const level = dbToGain(palette.level);
    this.line = new GainLine(
      [
        [this.dry.gain, level],
        [this.wet.gain, level * dbToGain(palette.reverb ?? 0)],
      ],
      0,
    );
    this.buses = palette.parts.map((part) => {
      const bus = createPartBus(
        kit,
        { fx: part.fx, gainDb: part.gain, pan: part.pan, send: part.send, beatDur: this.loop.beatDur, t: t0 },
        this.dry,
        this.wet,
      );
      if (part.role === "bed") {
        const src = startBed(kit, part.inst, bus.input, t0);
        if (src) this.beds.push(src);
      }
      return bus;
    });
    // The strings this loop plucks, built a few at a time ahead of need.
    const seen = new Set<string>();
    this.warm = [];
    for (const bar of this.loop.bars) {
      for (const e of bar) {
        const inst = palette.parts[e.part].inst;
        if (inst !== "guitar" && inst !== "eguitar" && inst !== "upright") continue;
        const key = `${inst}:${e.midi}`;
        if (!seen.has(key)) {
          seen.add(key);
          this.warm.push([inst, e.midi]);
        }
      }
    }
  }

  /** Fade the voice to `to` (0..1) from wherever it is now. */
  fade(to: number, dur: number, now: number, shape?: Shape) {
    this.line.to(to, dur, now, shape ?? (to >= this.line.valueAt(now) ? "in" : "out"));
  }

  /** Fade out and stop starting notes once silent. */
  end(now: number, dur = CROSSFADE) {
    // Already ending sooner? Keep the sooner end.
    const until = now + dur;
    if (until >= this.stopAt) return;
    this.fade(0, dur, now, "out");
    this.stopAt = until;
  }

  get ending() {
    return this.stopAt !== Infinity;
  }

  /** The voice's crossfade level at `t`, 0..1. */
  levelAt(t: number) {
    return this.line.valueAt(t);
  }

  /** True once the voice has ended and its last note has rung out. */
  finished(now: number) {
    return now >= this.stopAt + TAIL;
  }

  /** Schedule every note that starts before `now + horizon`. Returns notes made. */
  schedule(now: number, horizon: number): number {
    if (this.disposed) return 0;
    for (let i = 0; i < WARM_PER_PASS && this.warm.length; i++) {
      const [inst, midi] = this.warm.shift()!;
      this.kit.string(inst, midi);
    }
    const until = Math.min(now + horizon, this.stopAt);
    const { bars, barDur } = this.loop;
    const n = bars.length;
    // Far behind (a stalled timer): skip whole bars instead of playing a pile-up.
    if (this.barStart + barDur < now - 1) {
      const skip = Math.floor((now - this.barStart) / barDur);
      this.bar += skip;
      this.barStart += skip * barDur;
      this.cursor = 0;
    }
    let made = 0;
    while (this.barStart < until) {
      const evs = bars[this.bar % n];
      while (this.cursor < evs.length) {
        const e = evs[this.cursor];
        const t = this.barStart + e.t;
        if (t >= until) return made;
        this.cursor++;
        // A note that should already have started is dropped, not bunched up.
        if (t < now - 0.03) continue;
        const bus = this.buses[e.part];
        if (!bus) continue;
        playNote(this.kit, this.palette.parts[e.part].inst, bus.input, {
          t: Math.max(t, now),
          dur: e.dur,
          midi: e.midi,
          vel: e.vel,
        });
        made++;
      }
      this.bar++;
      this.barStart += barDur;
      this.cursor = 0;
    }
    return made;
  }

  /** Free everything. Call once `finished`, or after a fade to silence. */
  dispose(at: number) {
    if (this.disposed) return;
    this.disposed = true;
    for (const b of this.beds) {
      try {
        b.stop(at);
      } catch {
        // Already stopped.
      }
    }
    for (const bus of this.buses) bus?.stop(at);
    // Disconnect once the stop time has passed; the fade has made it silent.
    const drop = () => {
      for (const b of this.beds) b.disconnect();
      for (const bus of this.buses) bus?.disconnect();
      this.dry.disconnect();
      this.wet.disconnect();
    };
    const wait = Math.max(0, at - this.kit.ctx.currentTime);
    if (wait === 0 || typeof setTimeout === "undefined") drop();
    else setTimeout(drop, wait * 1000 + 50);
  }
}
