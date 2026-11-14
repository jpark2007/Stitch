/**
 * One era playing: its score loop, its parts' buses and its fade stages.
 * A crossfade is two of these running briefly in parallel, one fading in
 * while the other fades out and then frees itself.
 *
 *   dry/wet (fade-in x era level) --> fade-out --> cut --> the core's bus/room
 *
 * Each stage moves once (line.ts OnceRamp), so no fade is ever cancelled.
 *
 * `schedule(now, horizon)` is called by the engine's lookahead timer (never
 * from a render loop): it creates the notes that start before
 * `now + horizon`, each at its exact AudioContext time, and walks the loop
 * bar by bar for ever.
 *
 * An era's score and the list of strings it plucks (EraPrep) are made once
 * per core and kept, so a prefetched or revisited era starts with no work.
 */

import { CROSSFADE, dbToGain } from "./fades";
import { OnceRamp } from "./line";
import { STRING_PATCHES, createPartBus, playNote, startBed, type Kit, type PartBus } from "./patches";
import { scoreLoop, type Palette, type ScoreLoop } from "./score";

/** An era made ready to play: its loop, and the strings it plucks (built a few at a time). */
export interface EraPrep {
  loop: ScoreLoop;
  strings: [string, number][];
  /** Strings before this index have been built (the kit may evict, so it is a hint). */
  built: number;
}

export function prepareEra(palette: Palette): EraPrep {
  const loop = scoreLoop(palette);
  const seen = new Set<string>();
  const strings: [string, number][] = [];
  for (const bar of loop.bars) {
    for (const e of bar) {
      const inst = palette.parts[e.part].inst;
      if (!STRING_PATCHES.has(inst)) continue;
      const key = `${inst}:${e.midi}`;
      if (!seen.has(key)) {
        seen.add(key);
        strings.push([inst, e.midi]);
      }
    }
  }
  return { loop, strings, built: 0 };
}

/** Build the era's strings for up to `budgetMs` (at least one). True once all are built. */
export function warmEra(kit: Kit, prep: EraPrep, budgetMs: number): boolean {
  const t0 = clock();
  while (prep.built < prep.strings.length) {
    const [inst, midi] = prep.strings[prep.built];
    if (!kit.hasString(inst, midi)) {
      if (clock() - t0 > budgetMs) return false;
      kit.string(inst, midi);
    }
    prep.built++;
  }
  return true;
}

/** Seconds a note can ring after its last event (longest release, echoes). */
const TAIL = 4.5;
/** Main-thread milliseconds a scheduling pass may spend building strings ahead of need. */
const WARM_BUDGET_MS = 1.5;
/**
 * The first note waits this long after the voice is made, so the first few
 * scheduling passes can build its strings in small slices (it is fading in
 * from silence anyway).
 */
export const VOICE_LEAD_IN = 0.5;

const clock = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

export class EraVoice {
  readonly id: string;
  private readonly loop: ScoreLoop;
  private readonly dry: GainNode;
  private readonly wet: GainNode;
  private readonly stages: GainNode[];
  private readonly fadeIn: OnceRamp;
  private readonly fadeOut: OnceRamp;
  private readonly cut: OnceRamp;
  private readonly buses: (PartBus | null)[];
  private readonly beds: { stop(t: number): void; disconnect(): void }[] = [];
  private bar = 0;
  private barStart: number;
  private cursor = 0;
  /** No notes start at or after this (set when the voice is told to end). */
  private stopAt = Infinity;
  private fresh = true;
  disposed = false;

  constructor(
    private readonly kit: Kit,
    readonly palette: Palette,
    private readonly prep: EraPrep,
    dest: { dry: AudioNode; wet: AudioNode },
    t0: number,
  ) {
    const ctx = kit.ctx;
    this.id = palette.id;
    this.loop = prep.loop;
    this.barStart = t0 + VOICE_LEAD_IN;
    // dry/wet carry the fade-in (scaled by the era's level); then a fade-out
    // stage and a cut stage, each moved at most once.
    this.dry = ctx.createGain();
    this.wet = ctx.createGain();
    const dOut = ctx.createGain();
    const wOut = ctx.createGain();
    const dCut = ctx.createGain();
    const wCut = ctx.createGain();
    this.dry.connect(dOut).connect(dCut).connect(dest.dry);
    this.wet.connect(wOut).connect(wCut).connect(dest.wet);
    this.stages = [this.dry, this.wet, dOut, wOut, dCut, wCut];
    const level = dbToGain(palette.level);
    this.fadeIn = new OnceRamp(
      [
        [this.dry.gain, level],
        [this.wet.gain, level * dbToGain(palette.reverb ?? 0)],
      ],
      0,
    );
    this.fadeOut = new OnceRamp([[dOut.gain, 1], [wOut.gain, 1]], 1);
    this.cut = new OnceRamp([[dCut.gain, 1], [wCut.gain, 1]], 1);
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
  }

  /** Fade in from silence (equal power), once. */
  start(now: number, dur = CROSSFADE) {
    this.fadeIn.fire(1, dur, now, "in");
  }

  /**
   * Fade out (equal power) and stop starting notes once silent. A sooner end
   * (eras flipping faster than they fade) moves the cut stage instead, so no
   * ramp is ever interrupted; anything later than the current end is ignored.
   */
  end(now: number, dur = CROSSFADE) {
    const until = now + dur;
    if (until >= this.stopAt) return;
    if (this.fadeOut.fire(0, dur, now, "out") || this.cut.fire(0, dur, now, "out")) this.stopAt = until;
  }

  get ending() {
    return this.stopAt !== Infinity;
  }

  /** The voice's crossfade level at `t`, 0..1. */
  levelAt(t: number) {
    return this.fadeIn.valueAt(t) * this.fadeOut.valueAt(t) * this.cut.valueAt(t);
  }

  /** True once the voice has ended and its last note has rung out. */
  finished(now: number) {
    return now >= this.stopAt + TAIL;
  }

  /** Schedule every note that starts before `now + horizon`. Returns notes made. */
  schedule(now: number, horizon: number): number {
    if (this.disposed) return 0;
    // Build strings ahead of need in small slices (at least one a pass), but
    // not in the pass that built the voice: that one has done enough.
    if (!this.fresh) warmEra(this.kit, this.prep, WARM_BUDGET_MS);
    this.fresh = false;
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
    for (const b of this.beds) b.stop(at);
    for (const bus of this.buses) bus?.stop(at);
    // Disconnect once the stop time has passed; the fade has made it silent.
    const drop = () => {
      for (const b of this.beds) b.disconnect();
      for (const bus of this.buses) bus?.disconnect();
      for (const g of this.stages) g.disconnect();
    };
    const wait = Math.max(0, at - this.kit.ctx.currentTime);
    if (wait === 0 || typeof setTimeout === "undefined") drop();
    else setTimeout(drop, wait * 1000 + 50);
  }
}
