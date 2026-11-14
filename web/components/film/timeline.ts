// The film player's time math, pure: which shot is on screen at time t and
// how far into it, the crossfade between two shots, the slow pan/zoom of a
// shot, the beat (and its title card) at t, the subtitle phrase at t, seeking,
// and a silent clock for a film with no narration audio. FilmPlayer only
// applies what these return.
//
// Type-only imports and erasable TypeScript, so `node --test` loads this file
// directly (tests: components/film/__tests__/timeline.test.mjs).
import type { Film, FilmBeat, FilmShot, FilmWord } from "@/lib/types";

/** How long two shots overlap at a cut (the next one fades in over the last part of this one). */
export const CROSSFADE_SECONDS = 0.9;
/** How long a beat's title card stays up after the beat starts. */
export const TITLE_CARD_SECONDS = 3.2;
/** What the arrow keys seek by. */
export const SEEK_STEP_SECONDS = 5;
/** Most words in one subtitle line. */
export const SUBTITLE_MAX_WORDS = 7;
/** A pause between two words longer than this starts a new subtitle line. */
export const SUBTITLE_GAP_SECONDS = 1.1;
/** How long a subtitle line stays up after its last word is spoken. */
export const SUBTITLE_LINGER_SECONDS = 1.4;
/** How far a zoom goes in (or starts from, zooming out). */
export const ZOOM_SCALE = 1.16;
/** The zoom a pan starts and ends at, so there is room to travel. */
export const PAN_SCALE_FROM = 1.12;
export const PAN_SCALE_TO = 1.15;

type Timed = Pick<FilmShot, "start" | "duration">;
type Motion = NonNullable<FilmShot["motion"]>;

function finite(n: number, fallback = 0): number {
  return Number.isFinite(n) ? n : fallback;
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

/** The film's length in seconds: its duration, or the end of its last shot if that is later. */
export function filmLength(film: Pick<Film, "duration" | "shots">): number {
  let end = Math.max(0, finite(film.duration));
  for (const s of film.shots) end = Math.max(end, finite(s.start) + Math.max(0, finite(s.duration)));
  return end;
}

/** `t` kept inside the film: 0 for NaN, never below 0 or past `length`. */
export function clampTime(t: number, length: number): number {
  return clamp(finite(t), 0, Math.max(0, finite(length)));
}

/** Where a relative seek (the arrow keys) lands. */
export function seekBy(t: number, delta: number, length: number): number {
  return clampTime(finite(t) + finite(delta), length);
}

/** Index of the last item whose `key` is at or before `t`; -1 when none is. Items sorted by key. */
function lastAtOrBefore<T>(items: readonly T[], t: number, key: (x: T) => number): number {
  let lo = 0;
  let hi = items.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (key(items[mid]) <= t) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/** The shot on screen at `t` (the first one before it starts); -1 for a film with no shots. */
export function shotIndexAt(shots: readonly Timed[], t: number): number {
  if (shots.length === 0) return -1;
  return Math.max(0, lastAtOrBefore(shots, finite(t), (s) => s.start));
}

/** How far through `shot` time `t` is, from 0 to 1. */
export function shotProgress(shot: Timed, t: number): number {
  if (!(shot.duration > 0)) return 1;
  return clamp((finite(t) - shot.start) / shot.duration, 0, 1);
}

/** The crossfade at the cut from shot `i` to shot `i + 1`: never more than half of either shot. */
function fadeInto(shots: readonly Timed[], i: number, fade: number): number {
  const next = shots[i + 1];
  if (!next) return 0;
  return Math.max(0, Math.min(fade, shots[i].duration / 2, next.duration / 2));
}

/** One shot's layer at a time: how opaque it is and how far through its motion. */
export interface ShotLayer {
  index: number;
  opacity: number;
  /** 0 to 1 over the whole time the shot is visible, its fade-in included. */
  progress: number;
}

/**
 * The layers on screen at `t`, bottom first: the current shot, fully opaque,
 * and over the last `fade` seconds before the next cut, the next shot fading
 * in on top of it. A shot's motion starts as it begins to fade in, so it never
 * jumps when it takes over.
 */
export function layersAt(shots: readonly Timed[], t: number, fade: number = CROSSFADE_SECONDS): ShotLayer[] {
  const i = shotIndexAt(shots, t);
  if (i < 0) return [];
  const time = finite(t);
  const out: ShotLayer[] = [{ index: i, opacity: 1, progress: motionProgress(shots, i, time, fade) }];
  const f = fadeInto(shots, i, fade);
  const next = shots[i + 1];
  if (next && f > 0 && time > next.start - f) {
    out.push({
      index: i + 1,
      opacity: clamp((time - (next.start - f)) / f, 0, 1),
      progress: motionProgress(shots, i + 1, time, fade),
    });
  }
  return out;
}

/** Shot `i`'s motion progress at `t`, over its fade-in plus its own duration. */
export function motionProgress(shots: readonly Timed[], i: number, t: number, fade: number = CROSSFADE_SECONDS): number {
  const shot = shots[i];
  if (!shot) return 0;
  const lead = i > 0 ? fadeInto(shots, i - 1, fade) : 0;
  const span = shot.duration + lead;
  if (!(span > 0)) return 1;
  return clamp((finite(t) - (shot.start - lead)) / span, 0, 1);
}

/** A layer's pan and zoom: scale about the frame's centre, then a shift in fractions of the frame. */
export interface FrameTransform {
  scale: number;
  x: number;
  y: number;
}

/** Gentle in-out: mostly linear (a slow drift should not stall), softened at both ends. */
export function ease(p: number): number {
  const q = clamp(finite(p), 0, 1);
  return 0.55 * q + 0.45 * (0.5 - Math.cos(Math.PI * q) / 2);
}

/** The largest shift a layer zoomed to `scale` can take before an edge shows. */
function room(scale: number): number {
  return Math.max(0, (scale - 1) / 2);
}

/** The shift that brings fraction `f` of the photo as close to the centre as the edges allow. */
function toward(f: number, scale: number): number {
  const r = room(scale);
  // `+ 0` turns -0 (at scale 1) into 0.
  return clamp((0.5 - clamp(finite(f, 0.5), 0, 1)) * scale, -r, r) + 0;
}

/**
 * The pan/zoom of a shot at motion progress `p`. "in" zooms from the whole
 * photo toward its focus, "out" the reverse; "left" drifts the photo left
 * (the view travels right across it) and "right" the other way, both held
 * level with the focus. Never shows an edge: every shift stays within what
 * the zoom leaves. Reduced motion: the photo holds still.
 */
export function kenBurns(
  motion: Motion | null | undefined,
  focus: readonly [number, number] | null | undefined,
  p: number,
  reduced = false,
): FrameTransform {
  if (reduced) return { scale: 1, x: 0, y: 0 };
  const e = ease(p);
  const u = focus ? focus[0] : 0.5;
  const v = focus ? focus[1] : 0.5;
  const m = motion ?? "in";
  if (m === "left" || m === "right") {
    const scale = PAN_SCALE_FROM + (PAN_SCALE_TO - PAN_SCALE_FROM) * e;
    const r = room(scale) * 0.9;
    const x = m === "left" ? r - 2 * r * e : -r + 2 * r * e;
    return { scale, x, y: toward(v, scale) };
  }
  const scale = m === "out" ? ZOOM_SCALE - (ZOOM_SCALE - 1) * e : 1 + (ZOOM_SCALE - 1) * e;
  return { scale, x: toward(u, scale), y: toward(v, scale) };
}

/** A FrameTransform as a CSS transform (GPU-only: translate3d and scale). */
export function transformCss(t: FrameTransform): string {
  const pct = (n: number) => `${Math.round(n * 100000) / 1000}%`;
  return `translate3d(${pct(t.x)}, ${pct(t.y)}, 0) scale(${Math.round(t.scale * 10000) / 10000})`;
}

/**
 * How a photo sits in the frame: "cover" fills it when the shapes are close
 * (a little cropping is fine); "contain" shows all of a photo whose shape is
 * far off (a portrait in a landscape frame), over a blurred copy of itself.
 */
export function fitFor(width: number, height: number, frameAspect: number): "cover" | "contain" {
  if (!(width > 0) || !(height > 0) || !(frameAspect > 0)) return "cover";
  const ratio = width / height / frameAspect;
  return ratio >= 0.8 && ratio <= 1.4 ? "cover" : "contain";
}

/** The beat playing at `t`: the last one started; -1 before the first. */
export function beatIndexAt(beats: readonly Pick<FilmBeat, "start">[], t: number): number {
  return lastAtOrBefore(beats, finite(t), (b) => b.start);
}

/** The beat whose title card is up at `t` (for `hold` seconds after it starts), or null. */
export function titleCardAt(
  beats: readonly Pick<FilmBeat, "start">[],
  t: number,
  hold: number = TITLE_CARD_SECONDS,
): number | null {
  const i = beatIndexAt(beats, t);
  if (i < 0) return null;
  return finite(t) - beats[i].start < hold ? i : null;
}

/** Where each beat starts, as a fraction of the film, for ticks on the scrub bar. The first at 0 is left out. */
export function beatTicks(beats: readonly Pick<FilmBeat, "start">[], length: number): number[] {
  if (!(length > 0)) return [];
  return beats.map((b) => b.start / length).filter((f) => f > 0.005 && f < 1);
}

/** A subtitle line: words `from` (inclusive) to `to` (exclusive). */
export interface Phrase {
  from: number;
  to: number;
}

const SENTENCE_END = /[.!?…]["”’)\]]*$/;
const CLAUSE_END = /[,;:—–]["”’)\]]*$/;

/**
 * The narration cut into subtitle lines of a few words: a line ends at the
 * end of a sentence, at a comma (once it has three words), at `maxWords`, or
 * before a pause longer than `gap` seconds.
 */
export function phrases(
  words: readonly FilmWord[],
  maxWords: number = SUBTITLE_MAX_WORDS,
  gap: number = SUBTITLE_GAP_SECONDS,
): Phrase[] {
  const out: Phrase[] = [];
  let from = 0;
  for (let i = 0; i < words.length; i++) {
    const len = i - from + 1;
    const w = words[i].word;
    const next = words[i + 1];
    const end =
      !next ||
      SENTENCE_END.test(w) ||
      (len >= 3 && CLAUSE_END.test(w)) ||
      len >= Math.max(1, maxWords) ||
      next.t - words[i].t > gap;
    if (end) {
      out.push({ from, to: i + 1 });
      from = i + 1;
    }
  }
  return out;
}

/** The subtitle at a time: its phrase, its words, and which one is being spoken. */
export interface Subtitle {
  phrase: number;
  words: string[];
  /** Index into `words` of the word being spoken (the ones before it are said). */
  current: number;
}

/**
 * The subtitle line up at `t`: the phrase holding the last word spoken, until
 * `linger` seconds after its last word. Null before the first word and in the
 * quiet between beats.
 */
export function subtitleAt(
  words: readonly FilmWord[],
  lines: readonly Phrase[],
  t: number,
  linger: number = SUBTITLE_LINGER_SECONDS,
): Subtitle | null {
  const time = finite(t);
  const i = lastAtOrBefore(words, time, (w) => w.t);
  if (i < 0) return null;
  const p = lastAtOrBefore(lines, i, (l) => l.from);
  if (p < 0) return null;
  const line = lines[p];
  if (i >= line.to) return null;
  if (i === line.to - 1 && time - words[i].t > linger) return null;
  return {
    phrase: p,
    words: words.slice(line.from, line.to).map((w) => w.word),
    current: i - line.from,
  };
}

/** "0:42", "1:05", "12:00": whole seconds, rounded down. */
export function formatTime(seconds: number): string {
  const s = Math.max(0, Math.floor(finite(seconds)));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** "1 min 5 sec", "45 sec": a film's length for people, in whole seconds (as formatTime counts them). */
export function spokenLength(seconds: number): string {
  const s = Math.max(0, Math.floor(finite(seconds)));
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m === 0) return `${r} sec`;
  return r === 0 ? `${m} min` : `${m} min ${r} sec`;
}

/** A playable film: ready, with at least one shot. */
export function isPlayable(film: Pick<Film, "status" | "shots">): boolean {
  return film.status === "ready" && film.shots.length > 0;
}

/** A clock the player can drive: the narration audio, or a silent one. Times in seconds. */
export interface FilmClock {
  time(): number;
  playing(): boolean;
  /** Starts (from 0 again once the end was reached). May reject (a blocked or broken audio). */
  play(): Promise<void>;
  pause(): void;
  seek(t: number): void;
}

/**
 * The clock for a film with no narration audio: film time from a monotonic
 * millisecond clock (performance.now in the browser), stopping at `length`.
 */
export function createSilentClock(length: number, nowMs: () => number): FilmClock {
  const end = Math.max(0, finite(length));
  let base = 0;
  let startedAt: number | null = null;
  const time = () => (startedAt === null ? base : Math.min(end, base + (nowMs() - startedAt) / 1000));
  return {
    time,
    playing: () => startedAt !== null && time() < end,
    play() {
      if (startedAt === null) {
        if (base >= end) base = 0;
        startedAt = nowMs();
      }
      return Promise.resolve();
    },
    pause() {
      base = time();
      startedAt = null;
    },
    seek(t: number) {
      base = clampTime(t, end);
      if (startedAt !== null) startedAt = nowMs();
    },
  };
}
