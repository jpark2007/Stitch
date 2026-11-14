/**
 * The music graph on one AudioContext (live or offline), with no timers and
 * no idea of mute or gestures: engine.ts drives it live, offline.ts drives it
 * through OfflineAudioContext.suspend() for the verification renders.
 *
 *   voices --dry--> bus --> warmth --> level --> trim --> ceiling --> duck --> out
 *          --wet--> room (convolver) --^
 *
 *   warmth   a gentle high shelf so nothing is bright
 *   level    the transport: mute, the book open or closed
 *   trim     the music's loudness: each era's `level` evens them out at one
 *            K-weighted loudness (-24.5 LUFS, about -26 dBFS RMS; measured over
 *            a minute, within 0.2 dB), so turning a spread never changes the
 *            volume and a voice sits well on top. Loudness, not peak: plucked
 *            eras peak higher than padded ones at the same perceived level
 *   ceiling  a soft clipper that can never exceed -12 dBFS (transparent
 *            where the music lives; it only rounds the odd pluck's peak)
 *   duck     narration ducking, last so it is an exact -10 dB: before the
 *            ceiling, the clipper would give back part of the drop on peaks
 *
 * No compressor: one would undo the duck and pump with the eras' beats.
 */

import { reverbImpulse } from "./dsp";
import type { EraId } from "./eras";
import { CEILING, CROSSFADE, DUCK_DB, DUCK_DOWN, DUCK_UP, dbToGain, softClipCurve } from "./fades";
import { GainLine } from "./line";
import { PALETTES } from "./palettes";
import { createKit, type Kit } from "./patches";
import { EraVoice, prepareEra, warmEra, type EraPrep } from "./voice";

/** Scheduling horizon: notes are created this far ahead of the clock. */
export const LOOKAHEAD = 0.4;
/** At most this many voices sound at once (a quick flip cuts the oldest short). */
const MAX_VOICES = 2;
/** How fast an extra voice is cut when eras flip faster than they fade. */
const CUT = 0.35;
/** Master trim (dB); each palette's `level` then evens the eras out. */
const TRIM_DB = 2;

export interface MeterReading {
  /** Linear peak over the last ~40 ms, 0..1. */
  peak: number;
  /** Linear RMS over the same window. */
  rms: number;
}

export class MusicCore {
  readonly kit: Kit;
  private readonly bus: GainNode;
  private readonly room: ConvolverNode;
  private readonly duck: GainLine;
  private readonly level: GainLine;
  private readonly nodes: AudioNode[];
  private readonly trim: GainNode;
  private readonly out: AudioNode;
  private voices: EraVoice[] = [];
  private readonly preps = new Map<EraId, EraPrep>();
  private warming: EraId[] = [];
  private era: EraId | null = null;
  private analyser: AnalyserNode | null = null;
  private scratch: Float32Array<ArrayBuffer> | null = null;
  private disposed = false;

  constructor(
    readonly ctx: BaseAudioContext,
    destination: AudioNode,
  ) {
    this.kit = createKit(ctx);
    this.bus = ctx.createGain();
    const duck = ctx.createGain();
    const level = ctx.createGain();
    const warmth = ctx.createBiquadFilter();
    warmth.type = "highshelf";
    warmth.frequency.value = 5200;
    warmth.gain.value = -4;
    this.trim = ctx.createGain();
    this.trim.gain.value = dbToGain(TRIM_DB);
    const ceiling = ctx.createWaveShaper();
    ceiling.curve = softClipCurve(2049, CEILING) as Float32Array<ArrayBuffer>;
    ceiling.oversample = "2x";
    this.bus.connect(warmth).connect(level).connect(this.trim).connect(ceiling).connect(duck).connect(destination);
    this.out = duck;

    this.room = ctx.createConvolver();
    this.room.normalize = false;
    const [l, r] = reverbImpulse(ctx.sampleRate, 2.6, 3);
    const ir = ctx.createBuffer(2, l.length, ctx.sampleRate);
    ir.copyToChannel(l as Float32Array<ArrayBuffer>, 0);
    ir.copyToChannel(r as Float32Array<ArrayBuffer>, 1);
    this.room.buffer = ir;
    const roomReturn = ctx.createGain();
    roomReturn.gain.value = dbToGain(-9);
    this.room.connect(roomReturn).connect(this.bus);

    this.duck = new GainLine([[duck.gain, 1]], 1);
    this.level = new GainLine([[level.gain, 1]], 0);
    this.nodes = [this.bus, duck, level, warmth, this.trim, ceiling, this.room, roomReturn];
  }

  get currentEra() {
    return this.era;
  }

  /** An era is set, or a voice is still ringing out. */
  get sounding() {
    return this.era !== null || this.voices.length > 0;
  }

  get voiceCount() {
    return this.voices.length;
  }

  /**
   * Change era at `now`: the old voice fades out while the new one fades in
   * (equal power); null just fades out. Flipping faster than the fade cuts
   * the oldest voice short so no more than two ever sound.
   */
  setEra(id: EraId | null, now: number, fade = CROSSFADE) {
    if (this.disposed || id === this.era) return;
    this.era = id;
    for (const v of this.voices) v.end(now, fade);
    if (id) {
      const live = this.voices.filter((v) => !v.disposed);
      // Cut the quietest outgoing voices to make room.
      const excess = live.length + 1 - MAX_VOICES;
      if (excess > 0) {
        live
          .slice()
          .sort((a, b) => a.levelAt(now) - b.levelAt(now))
          .slice(0, excess)
          .forEach((v) => v.end(now, Math.min(CUT, fade)));
      }
      const v = new EraVoice(this.kit, PALETTES[id], this.prep(id), { dry: this.bus, wet: this.room }, now + 0.02);
      v.start(now, fade);
      this.voices.push(v);
    }
  }

  /** The era's score and string list, made once per core. */
  private prep(id: EraId): EraPrep {
    let p = this.preps.get(id);
    if (!p) {
      p = prepareEra(PALETTES[id]);
      this.preps.set(id, p);
    }
    return p;
  }

  /**
   * Make an era ready before it is asked for (the next spread's): its score
   * now, its strings a slice at a time on the following passes (warm()).
   */
  prefetch(id: EraId) {
    if (this.disposed || this.warming.includes(id)) return;
    const p = this.prep(id);
    if (p.built < p.strings.length) this.warming.push(id);
  }

  /** True while a prefetched era still has strings to build. */
  get warmingUp() {
    return this.warming.length > 0;
  }

  /** Build prefetched strings for up to `budgetMs`. */
  warm(budgetMs: number) {
    while (this.warming.length) {
      if (!warmEra(this.kit, this.prep(this.warming[0]), budgetMs)) return;
      this.warming.shift();
    }
  }

  setDucked(on: boolean, now: number) {
    const to = on ? dbToGain(DUCK_DB) : 1;
    if (this.duck.target === to) return;
    this.duck.to(to, on ? DUCK_DOWN : DUCK_UP, now, "smooth");
  }

  /** The transport level (mute, book open). */
  setLevel(to: number, dur: number, now: number) {
    if (this.level.target === to && now >= this.level.settles) return;
    this.level.to(to, dur, now, "smooth");
  }

  levelAt(t: number) {
    return this.level.valueAt(t);
  }

  /** Schedule ahead and retire finished voices. Returns notes created. */
  tick(now: number, horizon = LOOKAHEAD): number {
    if (this.disposed) return 0;
    let made = 0;
    for (const v of this.voices) made += v.schedule(now, horizon);
    if (this.voices.some((v) => v.finished(now))) {
      this.voices = this.voices.filter((v) => {
        if (!v.finished(now)) return true;
        v.dispose(now);
        return false;
      });
    }
    return made;
  }

  /**
   * Free the voices that are fading out. Called as the engine goes to sleep,
   * with the transport level already at 0, so cutting them cannot click.
   */
  retireEnding(now: number) {
    if (!this.voices.some((v) => v.ending)) return;
    this.voices = this.voices.filter((v) => {
      if (!v.ending) return true;
      v.dispose(now);
      return false;
    });
  }

  /** The output level, for the lab's meter (the analyser is made on first use). */
  meter(): MeterReading {
    if (!this.analyser) {
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 2048;
      this.out.connect(this.analyser);
      this.scratch = new Float32Array(this.analyser.fftSize);
    }
    const buf = this.scratch!;
    this.analyser.getFloatTimeDomainData(buf);
    let peak = 0;
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
      const s = buf[i];
      const a = s < 0 ? -s : s;
      if (a > peak) peak = a;
      sum += s * s;
    }
    return { peak, rms: Math.sqrt(sum / buf.length) };
  }

  /** Fade out over `fade` seconds from `now`, then free every node. */
  dispose(now: number, fade = 0.15) {
    if (this.disposed) return;
    this.disposed = true;
    this.level.to(0, fade, now, "smooth");
    const end = now + fade + 0.02;
    for (const v of this.voices) v.dispose(end);
    this.voices = [];
    const drop = () => {
      for (const n of this.nodes) n.disconnect();
      this.analyser?.disconnect();
    };
    if (typeof setTimeout === "undefined") drop();
    else setTimeout(drop, (fade + 0.08) * 1000);
  }
}
