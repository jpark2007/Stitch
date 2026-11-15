/**
 * Read-aloud: the browser's own voice (Web Speech API), free and key-less.
 *
 *   const voice = readAloud();                 // the page's one engine, or null
 *   voice.read(`${title}. ${text}`);           // the story page, aloud
 *   voice.caption(line, rate);                 // a caption of a silent telling
 *   voice.stop();                              // hush (a turn, a pause, leaving)
 *   voice.subscribe(() => voice.reading);      // "story" | "caption" | null
 *
 * One engine per page, so one voice at a time: a new read cancels what was
 * speaking. The story page wins over captions: while it is read aloud a
 * caption is passed by, and reading it cancels a caption mid-line. Muted
 * (the book's mute, sound.ts) it says nothing, and muting hushes it.
 *
 * The voice: a warm, natural English one when the browser has it (Natural,
 * Samantha, Google US English, Daniel, Karen), else any English, else the
 * browser's default. A long story is spoken a sentence at a time (queued),
 * which also keeps Chrome from cutting a long utterance off.
 *
 * No imports and erasable types only, so node runs it with a fake
 * speechSynthesis (readAloud.test.ts).
 */

/** What is being spoken: the story page, or a caption line. */
export type ReadKind = "story" | "caption";

export interface VoiceLike {
  readonly name: string;
  readonly lang: string;
  readonly default?: boolean;
}

export interface UtteranceLike {
  text: string;
  voice: VoiceLike | null;
  lang: string;
  rate: number;
  pitch: number;
  onend: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
}

export interface SynthLike {
  speak(u: UtteranceLike): void;
  cancel(): void;
  getVoices(): readonly VoiceLike[];
}

export interface MuteSource {
  get(): boolean;
  subscribe(listener: (muted: boolean) => void): () => void;
}

export interface ReadAloudOptions {
  synth: SynthLike;
  createUtterance: (text: string) => UtteranceLike;
  muted?: MuteSource;
}

/** The story's pace: a touch slower than speech. */
export const STORY_RATE = 0.95;
const MAX_CAPTION_RATE = 1.35;
/** Words a second at rate 1, for fitting a caption to its time on the page. */
const WORDS_PER_SECOND = 2.7;

/** Voices liked best, best first: matched in the name, English voices only. */
const WARM = [/natural/i, /samantha/i, /google us english/i, /daniel/i, /karen/i];

/** The voice to read in, or null for the browser's default. */
export function pickVoice(voices: readonly VoiceLike[]): VoiceLike | null {
  const english = voices.filter((v) => /^en([-_]|$)/i.test(v.lang));
  for (const re of WARM) {
    const v = english.find((x) => re.test(x.name));
    if (v) return v;
  }
  return english.find((v) => /^en[-_]us$/i.test(v.lang)) ?? english.find((v) => v.default) ?? english[0] ?? null;
}

/**
 * A caption's rate: the story's pace, quickened (to MAX_CAPTION_RATE at
 * most) when the line would not otherwise be said before the next one is
 * printed, `window` seconds on. No window (the last line): the story's pace.
 */
export function captionRate(text: string, window: number): number {
  const words = text.split(/\s+/).filter(Boolean).length;
  if (!(window > 0) || words === 0) return STORY_RATE;
  const needed = (words / WORDS_PER_SECOND + 0.25) / (window * 0.92);
  return Math.min(MAX_CAPTION_RATE, Math.max(STORY_RATE, Math.round(needed * 100) / 100));
}

/** Sentences of a story, each short enough to say in one utterance. */
export function sentences(text: string): string[] {
  return text
    .split(/(?<=[.!?…])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Whether a telling is silent, so its captions are spoken: no narration
 * recording for this teller ("none"), or one that failed to load
 * ("error"). A recording waiting on the reader's touch ("blocked") is not.
 */
export function silentTelling(audio: string): boolean {
  return audio === "none" || audio === "error";
}

export class ReadAloud {
  private readonly synth: SynthLike;
  private readonly createUtterance: (text: string) => UtteranceLike;
  private readonly muted: MuteSource | null;
  private kind: ReadKind | null = null;
  /** The utterance whose end ends this read (the last queued). */
  private last: UtteranceLike | null = null;
  private readonly listeners = new Set<() => void>();
  private offMuted: (() => void) | null = null;

  constructor(options: ReadAloudOptions) {
    this.synth = options.synth;
    this.createUtterance = options.createUtterance;
    this.muted = options.muted ?? null;
    this.offMuted = this.muted?.subscribe((m) => {
      if (m) this.stop();
    }) ?? null;
  }

  /** What is being spoken now, or null. */
  get reading(): ReadKind | null {
    return this.kind;
  }

  /** Read the story page aloud. False when muted or there is nothing to say. */
  read(text: string): boolean {
    return this.say("story", sentences(text), STORY_RATE);
  }

  /** Say one caption line, cancelling the line before it. Passed by while the story is read. */
  caption(text: string, rate = STORY_RATE): boolean {
    if (this.kind === "story") return false;
    return this.say("caption", [text.trim()].filter(Boolean), rate);
  }

  /** Hush: everything, or only a read of `kind`. */
  stop(kind?: ReadKind): void {
    if (this.kind === null || (kind && kind !== this.kind)) return;
    this.last = null;
    this.synth.cancel();
    this.set(null);
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** Hush and stop listening to mute. */
  dispose(): void {
    this.stop();
    this.offMuted?.();
    this.offMuted = null;
    this.listeners.clear();
  }

  private say(kind: ReadKind, parts: string[], rate: number): boolean {
    if (this.muted?.get() || parts.length === 0) return false;
    // A new read cancels whatever was speaking (cancel also clears the queue).
    this.last = null;
    this.synth.cancel();
    const voice = pickVoice(this.synth.getVoices());
    const queue = parts.map((text) => {
      const u = this.createUtterance(text);
      u.voice = voice;
      u.lang = voice?.lang ?? "en-US";
      u.rate = rate;
      u.pitch = 1;
      // A cancelled utterance ends too (or errors "interrupted"): only the
      // current read's last one ends the read.
      u.onend = u.onerror = () => {
        if (this.last === u) {
          this.last = null;
          this.set(null);
        }
      };
      return u;
    });
    this.last = queue[queue.length - 1];
    this.set(kind);
    for (const u of queue) this.synth.speak(u);
    return true;
  }

  private set(kind: ReadKind | null): void {
    if (kind === this.kind) return;
    this.kind = kind;
    for (const l of [...this.listeners]) l();
  }
}

/** The player's status as captions are spoken from it (PlayerStatus). */
export interface CaptionStatus {
  readonly playing: boolean;
  readonly ended: boolean;
  readonly audio: string;
  readonly narrator: string | null;
  readonly captionIndex: number;
}

/** The teller's captions (NarratorTrack), and when the telling ends. */
export interface CaptionTrack {
  readonly captions: readonly { readonly t: number; readonly text: string }[];
}

/**
 * Spoken captions for a silent telling: each line said as it is printed,
 * cutting off the line before; hushed when the telling pauses (a turn, a
 * note, the reader on the story page), not when it ends (the last line
 * finishes). A replay, or another teller, says the lines again. A telling
 * with its own recording is left to it.
 *
 *   const spoken = new CaptionVoice(voice);
 *   controller.subscribe(() => spoken.update(controller.getStatus(), controller.narrator, controller.duration));
 */
export class CaptionVoice {
  private readonly voice: ReadAloud;
  /** The line last said ("narrator:index"), or null to say the next one afresh. */
  private said: string | null = null;

  constructor(voice: ReadAloud) {
    this.voice = voice;
  }

  update(status: CaptionStatus, track: CaptionTrack | null, duration: number): void {
    if (!status.playing || !silentTelling(status.audio)) {
      this.said = null;
      if (!status.ended) this.voice.stop("caption");
      return;
    }
    const i = status.captionIndex;
    const line = track && i >= 0 ? track.captions[i] : undefined;
    if (!line) return;
    const key = `${status.narrator}:${i}`;
    if (key === this.said) return;
    this.said = key;
    const until = track!.captions[i + 1]?.t ?? duration;
    this.voice.caption(line.text, captionRate(line.text, until - line.t));
  }

  /** The stage went away: hush its line. */
  dispose(): void {
    this.said = null;
    this.voice.stop("caption");
  }
}

/* ------------------------------------------------------------------------ */
/* The browser's engine                                                      */
/* ------------------------------------------------------------------------ */

let engine: ReadAloud | null | undefined;

/**
 * The page's engine over window.speechSynthesis, made on first ask; null
 * where the browser has no speech (the read-aloud button is not shown).
 * `muted` is the book's mute, read only.
 */
export function readAloud(muted?: MuteSource): ReadAloud | null {
  if (engine !== undefined) return engine;
  if (typeof window === "undefined") return null;
  const w = window as Window & { speechSynthesis?: SpeechSynthesis };
  const Utterance = (window as Window & { SpeechSynthesisUtterance?: typeof SpeechSynthesisUtterance })
    .SpeechSynthesisUtterance;
  const synth = w.speechSynthesis;
  if (!synth || !Utterance) return (engine = null);
  // Some browsers list their voices only once asked: ask early.
  synth.getVoices();
  engine = new ReadAloud({
    synth: synth as unknown as SynthLike,
    createUtterance: (text) => new Utterance(text) as unknown as UtteranceLike,
    muted,
  });
  // Leaving the page (or reloading) never leaves the browser talking.
  window.addEventListener("pagehide", () => engine?.stop());
  return engine;
}
