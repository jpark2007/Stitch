/**
 * The music as numbers: an era's palette (palettes.ts) becomes a loop of bars,
 * each a sorted list of note events in seconds from the bar's start. Nothing
 * here touches WebAudio, so the same score drives the live scheduler, the
 * offline render and the unit tests (score.test.ts).
 *
 *   scoreLoop(palette)  -> { barDur, bars: NoteEvent[][] }
 *
 * Everything is seeded from the palette's id, so an era always plays the
 * same piece; the loop repeats exactly, so it plays forever without a seam.
 *
 * Pure and free of runtime imports (types only): Node's test runner imports
 * it directly.
 */

/* ------------------------------------------------------------------------ */
/* Types                                                                     */
/* ------------------------------------------------------------------------ */

export interface NoteEvent {
  /** Seconds from the bar's start (swing and humanising applied). */
  t: number;
  /** Seconds the note is held (its release rings on after). */
  dur: number;
  midi: number;
  /** 0..1 */
  vel: number;
  /** Index of the part in `palette.parts`. */
  part: number;
}

export type Mode = "major" | "minor" | "dorian";

interface PartBase {
  /** Instrument patch name (patches.ts). */
  inst: string;
  /** Part level in dB. */
  gain: number;
  pan?: number;
  /** Reverb send, 0..1. */
  send?: number;
  /** Bars of the loop this part plays: "x" plays, "." rests. Default all. */
  bars?: string;
  /** Effects on the part's bus (patches.ts). */
  fx?: PartFx[];
  /** Velocity, 0..1 (default 0.7). */
  vel?: number;
  /** Timing jitter in seconds (default 0.006). */
  humanize?: number;
}

/** A held chord, one per chord change. */
export interface PadPart extends PartBase {
  role: "pad";
  range: [number, number];
  voices: number;
}

/**
 * Rhythmic chords. `pattern` has one character per grid step: "x" hit,
 * "X" accent, "d" down-strum, "u" up-strum (lighter, upper strings), "." rest.
 */
export interface ChordsPart extends PartBase {
  role: "chords";
  range: [number, number];
  voices: number;
  grid: number;
  pattern: string;
  /** Held length in steps (default 1). */
  len?: number;
  /** Seconds between strings of a strum (default 0). */
  strum?: number;
}

/** Bass line. walk: a quarter-note walk that leads into the next chord. */
export interface BassPart extends PartBase {
  role: "bass";
  range: [number, number];
  grid: number;
  pattern: string;
  style: "root" | "rootFifth" | "walk";
  len?: number;
}

/** Broken chord: each "x" plays the next chord tone in `order`. */
export interface ArpPart extends PartBase {
  role: "arp";
  range: [number, number];
  grid: number;
  pattern: string;
  order: "up" | "updown" | "down" | "random";
  len?: number;
}

/**
 * Travis picking: "b" thumb (alternating root and fifth in `bass`), "t" a
 * treble chord tone in `range`, "p" pinch (both together), "." rest.
 */
export interface PickPart extends PartBase {
  role: "pick";
  range: [number, number];
  bass: [number, number];
  grid: number;
  pattern: string;
  len?: number;
}

/** A sparse, seeded melody built from a two-bar motif. */
export interface MelodyPart extends PartBase {
  role: "melody";
  range: [number, number];
  /** Notes per two-bar cell, roughly (2..6). */
  density: number;
  grid: number;
}

/** Unpitched hits: `midi` only picks the patch's colour. */
export interface DrumPart extends PartBase {
  role: "drum";
  grid: number;
  pattern: string;
  len?: number;
}

/** A texture that runs for the voice's life (vinyl crackle). No events. */
export interface BedPart extends PartBase {
  role: "bed";
}

export type Part = PadPart | ChordsPart | BassPart | ArpPart | PickPart | MelodyPart | DrumPart | BedPart;

export type PartFx =
  | { kind: "tremolo"; rate: number; depth: number }
  | { kind: "autopan"; rate: number; depth: number }
  | { kind: "chorus"; rate: number; depth: number; delay: number; mix: number }
  | { kind: "wobble"; rate: number; depth: number }
  | { kind: "echo"; beats: number; feedback: number; mix: number; tone: number }
  | { kind: "lowpass"; freq: number; q?: number; lfoRate?: number; lfoDepth?: number }
  | { kind: "highpass"; freq: number };

export interface Palette {
  id: string;
  /** Shown in the lab: "Parlour waltz". */
  name: string;
  /** Shown in the lab: "music box, oom-pah piano". */
  character: string;
  bpm: number;
  beatsPerBar: number;
  /** Swing: 0.5 straight, ~0.62 swung (applies to `swingGrid` steps). */
  swing: number;
  /** Steps per beat the swing applies to (2 = eighths, 4 = sixteenths). */
  swingGrid?: number;
  /** MIDI note of the key's tonic (any octave). */
  tonic: number;
  mode: Mode;
  /** One chord per bar in roman numerals ("I", "vi7", "bVImaj7"); "a|b" splits a bar. */
  progression: string[];
  /** Loop length in bars (a multiple of the progression length). */
  loopBars: number;
  /** Era level trim in dB. */
  level: number;
  /** Reverb return in dB for this era (default 0). */
  reverb?: number;
  parts: Part[];
}

export interface ScoreLoop {
  barDur: number;
  beatDur: number;
  bars: NoteEvent[][];
}

/* ------------------------------------------------------------------------ */
/* Seeded randomness                                                         */
/* ------------------------------------------------------------------------ */

/** FNV-1a: a stable 32-bit seed from a string. */
export function hashSeed(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** mulberry32: a small fast PRNG in [0, 1). */
export function seededRandom(seed: number | string): () => number {
  let a = typeof seed === "string" ? hashSeed(seed) : seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* ------------------------------------------------------------------------ */
/* Harmony                                                                   */
/* ------------------------------------------------------------------------ */

const MAJOR_STEPS = [0, 2, 4, 5, 7, 9, 11];
const MODES: Record<Mode, number[]> = {
  major: [0, 2, 4, 5, 7, 9, 11],
  minor: [0, 2, 3, 5, 7, 8, 10],
  dorian: [0, 2, 3, 5, 7, 9, 10],
};

export interface Chord {
  /** Pitch class of the root, 0..11. */
  root: number;
  /** Semitones above the root, ascending, root first. */
  intervals: number[];
  symbol: string;
}

const NUMERALS = ["VII", "III", "VI", "IV", "II", "V", "I"];
const NUMERAL_DEGREE: Record<string, number> = { I: 0, II: 1, III: 2, IV: 3, V: 4, VI: 5, VII: 6 };

/**
 * A roman-numeral chord in a key. Upper case is major, lower case minor;
 * suffixes: 6, 7, maj7, 9, maj9, add9, 69, 13, sus2, sus4, 7sus4, dim, m7b5.
 * Degrees count from the major scale, so minor keys write "bVI", "bVII".
 */
export function parseChord(symbol: string, tonic: number): Chord {
  let s = symbol.trim();
  let shift = 0;
  if (s[0] === "b") {
    shift = -1;
    s = s.slice(1);
  } else if (s[0] === "#") {
    shift = 1;
    s = s.slice(1);
  }
  const head = NUMERALS.find((n) => s.toUpperCase().startsWith(n));
  if (!head) throw new Error(`bad chord: ${symbol}`);
  const written = s.slice(0, head.length);
  const minor = written === written.toLowerCase();
  const suffix = s.slice(head.length);
  const root = (((tonic + MAJOR_STEPS[NUMERAL_DEGREE[head]] + shift) % 12) + 12) % 12;
  const third = minor ? 3 : 4;
  const triad = [0, third, 7];
  const byQuality: Record<string, number[]> = {
    "": triad,
    "6": [0, third, 7, 9],
    "7": minor ? [0, 3, 7, 10] : [0, 4, 7, 10],
    maj7: [0, third, 7, 11],
    "9": minor ? [0, 3, 7, 10, 14] : [0, 4, 7, 10, 14],
    maj9: [0, 4, 7, 11, 14],
    add9: [0, third, 7, 14],
    "69": [0, third, 7, 9, 14],
    "13": [0, 4, 10, 14, 21],
    sus2: [0, 2, 7],
    sus4: [0, 5, 7],
    "7sus4": [0, 5, 7, 10],
    dim: [0, 3, 6],
    m7b5: [0, 3, 6, 10],
  };
  const intervals = byQuality[suffix];
  if (!intervals) throw new Error(`bad chord quality: ${symbol}`);
  return { root, intervals, symbol };
}

/** Pitch classes of the key's scale. */
export function scalePitchClasses(tonic: number, mode: Mode): number[] {
  return MODES[mode].map((s) => (tonic + s) % 12);
}

/** Every MIDI note in [lo, hi] whose pitch class is in `pcs`, ascending. */
export function notesInRange(pcs: number[], lo: number, hi: number): number[] {
  const set = new Set(pcs.map((p) => ((p % 12) + 12) % 12));
  const out: number[] = [];
  for (let n = lo; n <= hi; n++) if (set.has(n % 12)) out.push(n);
  return out;
}

/**
 * Voice a chord with `n` notes inside [lo, hi], closest to the range's centre
 * so consecutive chords voice-lead smoothly without carrying state. The root
 * is always present (lowest where the range allows); the fifth goes first
 * when there are fewer voices than tones.
 */
export function voiceChord(chord: Chord, n: number, lo: number, hi: number): number[] {
  const tones = chord.intervals.map((i) => (chord.root + i) % 12);
  // Which tones to keep: drop the fifth, then extensions, when short of voices.
  let keep = tones.slice();
  if (n < keep.length) {
    const fifth = (chord.root + 7) % 12;
    const withoutFifth = keep.filter((p, i) => !(p === fifth && i > 0 && keep.length > 3));
    keep = withoutFifth.slice(0, Math.max(n, 1));
  }
  const centre = (lo + hi) / 2;
  let best: number[] = [];
  let bestScore = Infinity;
  // Try each inversion of a close stack starting in the range.
  for (let start = lo; start <= hi; start++) {
    if (!keep.includes(start % 12)) continue;
    const notes: number[] = [start];
    let k = keep.indexOf(start % 12);
    let cur = start;
    while (notes.length < n) {
      k = (k + 1) % keep.length;
      let next = cur + 1;
      while (next % 12 !== keep[k]) next++;
      if (next > hi) break;
      notes.push(next);
      cur = next;
    }
    if (notes.length < Math.min(n, keep.length)) continue;
    const mean = notes.reduce((a, b) => a + b, 0) / notes.length;
    // Prefer the root in the bass, then closeness to the centre.
    const rootLow = notes[0] % 12 === chord.root ? 0 : 2.5;
    const score = Math.abs(mean - centre) + rootLow;
    if (score < bestScore) {
      bestScore = score;
      best = notes;
    }
  }
  return best;
}

/** The note of pitch class `pc` in [lo, hi] closest to `near`. */
export function nearestNote(pc: number, lo: number, hi: number, near: number): number {
  let best = lo;
  let d = Infinity;
  for (let n = lo; n <= hi; n++) {
    if (((n % 12) + 12) % 12 !== ((pc % 12) + 12) % 12) continue;
    const dd = Math.abs(n - near);
    if (dd < d) {
      d = dd;
      best = n;
    }
  }
  return best;
}

/* ------------------------------------------------------------------------ */
/* Timing                                                                    */
/* ------------------------------------------------------------------------ */

/**
 * Seconds from the bar start of `step` on a grid of `grid` steps per beat,
 * swung when the grid matches the palette's swing grid.
 */
export function stepTime(step: number, grid: number, beatDur: number, swing: number, swingGrid: number): number {
  const stepDur = beatDur / grid;
  let t = step * stepDur;
  if (swing !== 0.5 && grid === swingGrid && step % 2 === 1) t += (swing - 0.5) * 2 * stepDur;
  return t;
}

/* ------------------------------------------------------------------------ */
/* Generation                                                                */
/* ------------------------------------------------------------------------ */

interface BarCtx {
  pal: Palette;
  bar: number;
  beatDur: number;
  barDur: number;
  /** Chord at a fraction 0..1 of the bar. */
  chordAt: (frac: number) => Chord;
  /** The first chord of the next bar (for walking bass). */
  nextChord: Chord;
  rnd: () => number;
}

function chordsOfBar(pal: Palette, bar: number): Chord[] {
  const sym = pal.progression[bar % pal.progression.length];
  return sym.split("|").map((s) => parseChord(s, pal.tonic));
}

function playsBar(part: Part, bar: number): boolean {
  if (!part.bars) return true;
  return part.bars[bar % part.bars.length] !== ".";
}

function jitter(rnd: () => number, amount: number) {
  return (rnd() * 2 - 1) * amount;
}

function steps(pattern: string, cb: (step: number, ch: string) => void) {
  for (let i = 0; i < pattern.length; i++) if (pattern[i] !== ".") cb(i, pattern[i]);
}

function velOf(part: Part, rnd: () => number, accent = 1) {
  const v = (part.vel ?? 0.7) * accent * (0.9 + rnd() * 0.18);
  return Math.max(0.05, Math.min(1, v));
}

function genPart(part: Part, index: number, c: BarCtx, out: NoteEvent[]) {
  const { pal, beatDur, barDur, rnd } = c;
  const swingGrid = pal.swingGrid ?? 2;
  const hum = part.humanize ?? 0.006;
  const push = (t: number, dur: number, midi: number, vel: number) => {
    const tt = Math.max(0, Math.min(barDur - 0.002, t + (t > 0 ? jitter(rnd, hum) : Math.abs(jitter(rnd, hum)))));
    out.push({ t: tt, dur: Math.max(0.03, dur), midi, vel, part: index });
  };
  const at = (step: number, grid: number) => stepTime(step, grid, beatDur, pal.swing, swingGrid);

  switch (part.role) {
    case "bed":
      return;
    case "pad": {
      // One held chord per chord change, overlapping the next slightly.
      const sym = pal.progression[c.bar % pal.progression.length].split("|");
      const seg = barDur / sym.length;
      sym.forEach((_, i) => {
        const chord = c.chordAt((i + 0.5) / sym.length);
        for (const n of voiceChord(chord, part.voices, part.range[0], part.range[1])) {
          push(i * seg, seg + 0.15, n, velOf(part, rnd));
        }
      });
      return;
    }
    case "chords": {
      const len = (part.len ?? 1) * (beatDur / part.grid);
      const total = beatDur * pal.beatsPerBar;
      steps(part.pattern, (s, ch) => {
        const t = at(s, part.grid);
        const chord = c.chordAt(t / total);
        let notes = voiceChord(chord, part.voices, part.range[0], part.range[1]);
        let accent = ch === "X" ? 1.2 : 1;
        let strum = part.strum ?? 0;
        if (ch === "u") {
          // Up-strums catch the top strings only, high to low, lighter.
          notes = notes.slice(Math.floor(notes.length / 2)).reverse();
          accent = 0.7;
          strum *= 0.8;
        }
        notes.forEach((n, i) => push(t + i * strum, len - i * strum, n, velOf(part, rnd, accent * (1 - i * 0.04))));
      });
      return;
    }
    case "bass": {
      const len = (part.len ?? 1) * (beatDur / part.grid);
      const [lo, hi] = part.range;
      let k = 0;
      const hits: number[] = [];
      steps(part.pattern, (s) => hits.push(s));
      hits.forEach((s, i) => {
        const t = at(s, part.grid);
        const chord = c.chordAt(t / barDur);
        const rootNote = nearestNote(chord.root, lo, hi, lo + 5);
        let midi = rootNote;
        if (part.style === "rootFifth") {
          midi = k % 2 === 0 ? rootNote : nearestNote(chord.root + 7, lo, hi, rootNote);
        } else if (part.style === "walk") {
          // Root, chord tone, chord tone, then a step into the next root.
          const tones = notesInRange(
            chord.intervals.slice(0, 4).map((x) => chord.root + x),
            lo,
            hi,
          ).filter((n) => n >= rootNote && n <= rootNote + 12);
          if (i === 0) midi = rootNote;
          else if (i === hits.length - 1) {
            const target = nearestNote(c.nextChord.root, lo, hi, rootNote);
            midi = Math.max(lo, Math.min(hi, target + (rnd() < 0.5 ? -1 : 1)));
          } else {
            const upper = tones.filter((n) => n !== rootNote);
            midi = upper.length ? upper[(i - 1 + Math.floor(rnd() * 2)) % upper.length] : rootNote;
          }
        }
        k++;
        push(t, len, midi, velOf(part, rnd, i === 0 ? 1.08 : 1));
      });
      return;
    }
    case "arp": {
      const len = (part.len ?? 1) * (beatDur / part.grid);
      let k = 0;
      steps(part.pattern, (s) => {
        const t = at(s, part.grid);
        const chord = c.chordAt(t / barDur);
        const tones = notesInRange(
          chord.intervals.map((x) => chord.root + x),
          part.range[0],
          part.range[1],
        );
        if (!tones.length) return;
        let idx: number;
        if (part.order === "up") idx = k % tones.length;
        else if (part.order === "down") idx = tones.length - 1 - (k % tones.length);
        else if (part.order === "random") idx = Math.floor(rnd() * tones.length);
        else {
          const cyc = Math.max(1, tones.length * 2 - 2);
          const p = k % cyc;
          idx = p < tones.length ? p : cyc - p;
        }
        k++;
        push(t, len, tones[idx], velOf(part, rnd, k === 1 ? 1.1 : 1));
      });
      return;
    }
    case "pick": {
      const len = (part.len ?? 2) * (beatDur / part.grid);
      let thumb = 0;
      let treble = 0;
      steps(part.pattern, (s, ch) => {
        const t = at(s, part.grid);
        const chord = c.chordAt(t / barDur);
        if (ch === "b" || ch === "p") {
          const root = nearestNote(chord.root, part.bass[0], part.bass[1], part.bass[0] + 4);
          const midi = thumb % 2 === 0 ? root : nearestNote(chord.root + 7, part.bass[0], part.bass[1], root + 5);
          thumb++;
          push(t, len * 1.6, midi, velOf(part, rnd, 1.05));
        }
        if (ch === "t" || ch === "p") {
          const tones = voiceChord(chord, 3, part.range[0], part.range[1]);
          // Walk the top strings: middle, top, middle, upper-middle...
          const order = [1, 2, 0, 2];
          const midi = tones[order[treble % order.length] % tones.length];
          treble++;
          push(t, len, midi, velOf(part, rnd, 0.85));
        }
      });
      return;
    }
    case "drum": {
      const len = (part.len ?? 1) * (beatDur / part.grid);
      steps(part.pattern, (s, ch) => {
        const accent = ch === "X" ? 1.25 : ch === "o" ? 0.6 : 1;
        push(at(s, part.grid), len, 60, velOf(part, rnd, accent));
      });
      return;
    }
    case "melody":
      genMelody(part, index, c, out, at);
      return;
  }
}

/**
 * A two-bar motif (rhythm and contour) seeded per era, restated through the
 * loop: bars 0-1 state it, 2-3 answer it a step higher, 4-5 restate, 6-7
 * cadence to a long chord tone. Each bar is computed on its own (no state
 * carried between bars), so the loop is exactly repeatable.
 */
function genMelody(part: MelodyPart, index: number, c: BarCtx, out: NoteEvent[], at: (s: number, g: number) => number) {
  const { pal, barDur } = c;
  const perBar = part.grid * pal.beatsPerBar;
  const motifRnd = seededRandom(`${pal.id}:motif:${index}`);
  // Motif rhythm over two bars: `density` onsets, biased to strong beats.
  const total = perBar * 2;
  const onsets = new Set<number>([0]);
  let guard = 0;
  while (onsets.size < Math.max(1, Math.round(part.density)) && guard++ < 200) {
    const s = Math.floor(motifRnd() * total);
    const strong = s % part.grid === 0;
    if (strong || motifRnd() < 0.35) onsets.add(s);
  }
  const rhythm = [...onsets].sort((a, b) => a - b);
  // Contour: scale-step moves, gentle (mostly steps, the odd third).
  const contour = rhythm.map((_, i) => (i === 0 ? 0 : [-1, 1, -1, 1, 2, -2, 0][Math.floor(motifRnd() * 7)]));

  const cell = Math.floor(c.bar / 2) % 4;
  const half = c.bar % 2;
  const scale = notesInRange(scalePitchClasses(pal.tonic, pal.mode), part.range[0], part.range[1]);
  if (!scale.length) return;
  const mid = Math.floor(scale.length / 2);
  // The cell's starting scale index: statement, answer (up a step), restatement, cadence.
  const startIdx = mid + [0, 1, 0, -1][cell];
  let idx = startIdx;
  rhythm.forEach((s, i) => {
    idx += contour[i];
    if (Math.floor(s / perBar) !== half) return;
    const local = s % perBar;
    const t = at(local, part.grid);
    const chord = c.chordAt(t / barDur);
    let midi = scale[Math.max(0, Math.min(scale.length - 1, idx))];
    // Strong beats land on a chord tone (the nearest one).
    if (local % part.grid === 0) {
      const tones = notesInRange(chord.intervals.map((x) => chord.root + x), part.range[0], part.range[1]);
      if (tones.length) midi = tones.reduce((b, n) => (Math.abs(n - midi) < Math.abs(b - midi) ? n : b), tones[0]);
    }
    const next = rhythm[i + 1] ?? total;
    let dur = Math.min(next - s, part.grid * 2) * (c.beatDur / part.grid);
    const last = cell === 3 && i === rhythm.length - 1;
    if (last) {
      // Cadence: settle on the chord's root, held.
      const tones = notesInRange([chord.root], part.range[0], part.range[1]);
      if (tones.length) midi = tones.reduce((b, n) => (Math.abs(n - midi) < Math.abs(b - midi) ? n : b), tones[0]);
      dur = Math.max(dur, c.beatDur * 2);
    }
    const tt = Math.max(0, t + (local > 0 ? jitter(c.rnd, part.humanize ?? 0.008) : 0));
    out.push({ t: Math.min(barDur - 0.002, tt), dur, midi, vel: velOf(part, c.rnd, local === 0 ? 1.05 : 0.95), part: index });
  });
}

/** One bar of the loop (bar is taken modulo `loopBars`). */
export function barEvents(pal: Palette, bar: number): NoteEvent[] {
  const b = ((bar % pal.loopBars) + pal.loopBars) % pal.loopBars;
  const beatDur = 60 / pal.bpm;
  const barDur = beatDur * pal.beatsPerBar;
  const chords = chordsOfBar(pal, b);
  const next = chordsOfBar(pal, (b + 1) % pal.loopBars)[0];
  const out: NoteEvent[] = [];
  pal.parts.forEach((part, i) => {
    if (!playsBar(part, b)) return;
    const ctx: BarCtx = {
      pal,
      bar: b,
      beatDur,
      barDur,
      chordAt: (f) => chords[Math.min(chords.length - 1, Math.floor(Math.max(0, f) * chords.length))],
      nextChord: next,
      rnd: seededRandom(`${pal.id}:bar${b}:part${i}`),
    };
    genPart(part, i, ctx, out);
  });
  out.sort((a, z) => a.t - z.t || a.part - z.part || a.midi - z.midi);
  return out;
}

/** The whole loop, computed once per voice. */
export function scoreLoop(pal: Palette): ScoreLoop {
  const beatDur = 60 / pal.bpm;
  const bars: NoteEvent[][] = [];
  for (let b = 0; b < pal.loopBars; b++) bars.push(barEvents(pal, b));
  return { beatDur, barDur: beatDur * pal.beatsPerBar, bars };
}

/** Equal temperament, A4 = 440 Hz. */
export function midiToFreq(m: number): number {
  return 440 * Math.pow(2, (m - 69) / 12);
}
