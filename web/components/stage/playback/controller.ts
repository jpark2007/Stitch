/**
 * The player's clock: play, pause, seek, rate, loop, the current teller, held
 * pull tabs and sliders, and a narration <audio> element kept in step with
 * scene time. Framework-free; useTimelinePlayer drives it from
 * requestAnimationFrame, and tests drive it with a fake clock.
 *
 *   const player = new PlaybackController(compiled, { audio: new Audio(), resolveAudio });
 *   player.play();                    // from a click, so the browser lets audio start
 *   requestAnimationFrame(function loop(now) { player.tick(now); draw(player.frame); requestAnimationFrame(loop); });
 *
 * The internal clock is the master. Audio follows it: it starts at the
 * matching offset (audio time = timeline time), pauses with it, and is
 * re-seeked if it drifts by more than `driftTolerance`. Missing audio (no
 * element, a teller with no recording yet, a blocked autoplay or a load
 * error) never stops the scene; it just plays silently.
 *
 * Pulling a scrub tab past an effect it owns (CompiledInteraction.effects:
 * confetti thrown in the hug the tab replays) replays that effect from its
 * start, on its own clock that runs from the moment the pull reached it, at
 * the scene's rate, for its duration and the particles' flight, whether the
 * tab is still held or not. Pulling again (or back and forward again within
 * one pull) throws it again. The scene's own playback is untouched: once it
 * plays into an effect, or is seeked, restarted or loops, that effect is on
 * scene time again, exactly as without the tab.
 *
 * Node runs this file directly for the unit tests, so it may only import types.
 */

import type { CompiledTimeline } from "./sampler";
import type { CompiledInteraction, InteractionInput, NarratorTrack, PlaybackFrame, SampleOptions } from "./types";

/** The subset of HTMLAudioElement the controller uses. */
export interface AudioLike {
  src: string;
  currentTime: number;
  playbackRate: number;
  readonly paused: boolean;
  /** Seconds; NaN until metadata has loaded */
  readonly duration: number;
  play(): Promise<void> | void;
  pause(): void;
  load(): void;
  removeAttribute?(name: string): void;
  addEventListener?(type: "error" | "canplay", listener: () => void): void;
  removeEventListener?(type: "error" | "canplay", listener: () => void): void;
}

export type AudioState =
  /** No element, or this teller has no recording */
  | "none"
  /** Source set; playing or ready to */
  | "ready"
  /** The browser refused to start audio without a user gesture; call play() from one */
  | "blocked"
  /** The source failed to load or play */
  | "error";

/** Discrete player state, for React. A new object only when something in it changes. */
export interface PlayerStatus {
  readonly playing: boolean;
  /** Reached the end without looping */
  readonly ended: boolean;
  readonly rate: number;
  readonly loop: boolean;
  /** Current teller (author person id), or null when the timeline has no narration */
  readonly narrator: string | null;
  readonly audio: AudioState;
  /** A scrub drive is being held, so scene time is paused */
  readonly scrubbing: boolean;
  /** Mirrors frame.narration / frame.shotIndex, so captions can re-render only when they change */
  readonly captionIndex: number;
  readonly wordIndex: number;
  readonly shotIndex: number;
}

export interface ControllerOptions {
  /** Starting teller; default the timeline's default narrator */
  narrator?: string | null;
  loop?: boolean;
  /** Playback rate, 0.1 .. 4 (default 1) */
  rate?: number;
  /** Start at this time (seconds) */
  time?: number;
  /** The narration element; may be attached later with attachAudio */
  audio?: AudioLike | null;
  /** Media id -> URL for an audio element. Return null when it cannot be played. */
  resolveAudio?: (mediaId: string) => string | null;
  /** Milliseconds clock; default performance.now */
  now?: () => number;
  /** Seconds audio may drift from scene time before it is re-seeked (default 0.2) */
  driftTolerance?: number;
}

export interface ReleaseOptions {
  /**
   * For a scrub: "return" (default) puts its targets back at scene time;
   * "seek" moves the whole scene to the time the tab left them at.
   */
  settle?: "return" | "seek";
}

/** Seconds a replayed effect's clock runs past its duration, so particles in flight land or fade. */
const REPLAY_TAIL = 8;
/** Duration of an effect with none, as the stage draws it (EffectBindings). */
const REPLAY_DEFAULT_DUR = 3;
/** Within one pull, how far (s) the tab must go back before an effect it passed can be thrown again. */
const REARM = 0.2;
/** Slack (s) on reaching an effect's time, for rounding in scrub travel. */
const REACH_EPS = 1e-6;

const MIN_RATE = 0.1;
const MAX_RATE = 4;

function clampRate(r: number): number {
  return Number.isFinite(r) ? Math.min(MAX_RATE, Math.max(MIN_RATE, r)) : 1;
}

export class PlaybackController {
  readonly compiled: CompiledTimeline;
  /** The current frame. Mutated in place on every sample; read it, do not keep parts of it. */
  readonly frame: PlaybackFrame;

  private t = 0;
  private playing = false;
  private ended = false;
  private rate: number;
  private loop: boolean;
  private narratorId: string | null;
  private readonly now: () => number;
  private readonly resolveAudio: ((mediaId: string) => string | null) | null;
  private readonly driftTolerance: number;

  // Clock anchor: scene time anchorT at wall time anchorWall (ms).
  private anchorT = 0;
  private anchorWall = 0;

  // Held drives. Objects are reused; `held` is what sample() reads.
  private readonly held: InteractionInput[] = [];
  private readonly sampleOptions: SampleOptions;
  private scrubHolds = 0;
  private resumeAfterScrub = false;

  // Effects replayed by a pull tab, per Timeline.effects index: the effect's
  // own clock (NaN: scene time; what sample() reads), the clock at its anchor,
  // the wall time (ms) of that anchor, and where the clock stops.
  private readonly effectClock: Float64Array;
  private readonly replayFrom: Float64Array;
  private readonly replayWall: Float64Array;
  private readonly replayEnd: Float64Array;
  /** Per held scrub tab with effects: 1 where ci.effects[j] can still be thrown by this pull. */
  private readonly armed = new Map<string, Uint8Array>();

  // Audio
  private audio: AudioLike | null = null;
  private audioUrl: string | null = null;
  private audioState: AudioState = "none";
  private audioPending = false;
  private readonly onAudioError = () => {
    this.audioState = "error";
    this.audioPending = false;
    this.emitStatus();
  };

  // Sampling and listeners
  private dirty = true;
  private sampledT = Number.NaN;
  private version = 0;
  private status: PlayerStatus;
  private readonly statusListeners = new Set<() => void>();
  // An array, not a Set: iterating it every frame must not allocate an iterator.
  private frameListeners: ((frame: PlaybackFrame) => void)[] = [];

  constructor(compiled: CompiledTimeline, options: ControllerOptions = {}) {
    this.compiled = compiled;
    this.frame = compiled.createFrame();
    this.rate = clampRate(options.rate ?? 1);
    this.loop = options.loop ?? false;
    this.now = options.now ?? (() => performance.now());
    this.resolveAudio = options.resolveAudio ?? null;
    this.driftTolerance = options.driftTolerance ?? 0.2;
    this.narratorId = compiled.narrator(options.narrator)?.id ?? null;
    const effects = compiled.timeline.effects?.length ?? 0;
    this.effectClock = new Float64Array(effects).fill(Number.NaN);
    this.replayFrom = new Float64Array(effects);
    this.replayWall = new Float64Array(effects);
    this.replayEnd = new Float64Array(effects);
    this.sampleOptions = { narrator: this.narratorId, interactions: this.held, effectClock: this.effectClock };
    this.t = this.clampTime(options.time ?? 0);
    this.status = this.buildStatus();
    if (options.audio) this.attachAudio(options.audio);
    this.sampleNow();
  }

  // ------------------------------------------------------------ reading

  /** Scene time in seconds */
  get time(): number {
    return this.t;
  }

  get duration(): number {
    return this.compiled.duration;
  }

  /** Increments every time `frame` is re-sampled. */
  get frameVersion(): number {
    return this.version;
  }

  get narrator(): NarratorTrack | null {
    return this.compiled.narrator(this.narratorId);
  }

  getStatus = (): PlayerStatus => this.status;

  /** Discrete status changes (play, pause, narrator, caption, ...). Returns an unsubscribe. */
  subscribe = (listener: () => void): (() => void) => {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  };

  /** Called after every re-sample with the updated frame. Returns an unsubscribe. */
  onFrame(listener: (frame: PlaybackFrame) => void): () => void {
    this.frameListeners = [...this.frameListeners, listener];
    return () => {
      this.frameListeners = this.frameListeners.filter((l) => l !== listener);
    };
  }

  // ------------------------------------------------------------ transport

  /** Starts playback. Call from a user gesture so the browser lets audio start. */
  play(): void {
    if (this.compiled.duration <= 0) return;
    if (this.t >= this.compiled.duration) {
      this.t = 0;
      this.clearReplays();
    }
    if (this.audioState === "blocked") this.audioState = "ready";
    this.ended = false;
    if (this.scrubHolds > 0) {
      // Resume once the tab is let go.
      this.resumeAfterScrub = true;
      this.emitStatus();
      return;
    }
    this.playing = true;
    this.anchor();
    this.dirty = true;
    this.syncAudio();
    this.emitStatus();
  }

  pause(): void {
    this.resumeAfterScrub = false;
    if (!this.playing) return;
    this.advance();
    this.playing = false;
    this.syncAudio();
    this.emitStatus();
  }

  toggle(): void {
    if (this.playing || this.resumeAfterScrub) this.pause();
    else this.play();
  }

  /** Jumps to scene time t (clamped to 0 .. duration). Keeps playing if it was. */
  seek(t: number): void {
    this.t = this.clampTime(t);
    this.ended = false;
    this.clearReplays();
    this.anchor();
    this.dirty = true;
    if (this.audio && this.audioUrl) this.seekAudio();
    this.syncAudio();
    this.sampleNow();
    this.emitStatus();
  }

  setRate(rate: number): void {
    const r = clampRate(rate);
    if (r === this.rate) return;
    this.advance();
    this.rebaseReplays();
    this.rate = r;
    this.anchor();
    if (this.audio) this.audio.playbackRate = r;
    this.emitStatus();
  }

  setLoop(loop: boolean): void {
    if (loop === this.loop) return;
    this.loop = loop;
    this.emitStatus();
  }

  /**
   * Switches teller: swaps the narration audio and captions and replays the
   * scene from 0. The action is shared, so only the voice and words change.
   * Choosing the current teller does nothing.
   */
  setNarrator(id: string | null): void {
    const next = this.compiled.narrator(id)?.id ?? null;
    if (next === this.narratorId) return;
    this.narratorId = next;
    this.sampleOptions.narrator = next;
    this.loadNarratorAudio();
    this.seek(0);
    this.play();
  }

  // ------------------------------------------------------------ interactions

  /**
   * Holds a pull tab or slider at `value` (0..1 of its travel). A scrub drive
   * plays its targets at the mapped time while the rest of the scene holds:
   * scene time pauses for as long as it is held, and resumes on release if it
   * was playing. prop_state and prop_part drives override their prop while
   * held and do not stop the clock.
   */
  holdInteraction(id: string, value: number): void {
    const ci = this.compiled.interaction(id);
    if (!ci) return;
    let entry = this.held.find((h) => h.id === id);
    if (!entry) {
      entry = { id, value };
      this.held.push(entry);
      if (ci.type === "scrub") {
        if (this.scrubHolds === 0) {
          this.resumeAfterScrub = this.playing;
          if (this.playing) {
            this.advance();
            this.playing = false;
            this.syncAudio();
          }
        }
        this.scrubHolds++;
      }
    }
    entry.value = value;
    if (ci.effects.length > 0) this.pullEffects(ci, value);
    this.dirty = true;
    this.sampleNow();
    this.emitStatus();
  }

  /** Lets go of a held drive. See ReleaseOptions for where a scrub's targets go. */
  releaseInteraction(id: string, options: ReleaseOptions = {}): void {
    const i = this.held.findIndex((h) => h.id === id);
    if (i < 0) return;
    const [entry] = this.held.splice(i, 1);
    this.armed.delete(id);
    const ci = this.compiled.interaction(id);
    if (ci?.type === "scrub") {
      this.scrubHolds--;
      if (options.settle === "seek") {
        this.t = this.clampTime(this.compiled.scrubTime(ci, entry.value));
        this.anchor();
        if (this.audio && this.audioUrl) this.seekAudio();
      }
      if (this.scrubHolds === 0 && this.resumeAfterScrub) {
        this.resumeAfterScrub = false;
        this.playing = true;
        this.anchor();
        this.syncAudio();
      }
    }
    this.dirty = true;
    this.sampleNow();
    this.emitStatus();
  }

  /** The value a drive is held at, or null when it is not held. */
  heldValue(id: string): number | null {
    return this.held.find((h) => h.id === id)?.value ?? null;
  }

  /** Throws each effect the tab's travel has reached this pull; re-arms one the tab went back before. */
  private pullEffects(ci: CompiledInteraction, value: number): void {
    let armed = this.armed.get(ci.id);
    if (!armed) {
      armed = new Uint8Array(ci.effects.length).fill(1);
      this.armed.set(ci.id, armed);
    }
    const st = this.compiled.scrubTime(ci, value);
    const effects = this.compiled.timeline.effects ?? [];
    for (let j = 0; j < ci.effects.length; j++) {
      const k = ci.effects[j];
      const e = effects[k];
      if (!e) continue;
      if (armed[j] === 1) {
        if (st >= e.t - REACH_EPS) {
          armed[j] = 0;
          this.startReplay(k, e.t, e.dur);
        }
      } else if (st < e.t - REARM) {
        armed[j] = 1;
      }
    }
  }

  /** Starts effect k's own clock at its start time, now. */
  private startReplay(k: number, start: number, dur: number | null | undefined): void {
    this.effectClock[k] = start;
    this.replayFrom[k] = start;
    this.replayWall[k] = this.now();
    this.replayEnd[k] = start + Math.max(0, dur ?? REPLAY_DEFAULT_DUR) + REPLAY_TAIL;
    this.dirty = true;
  }

  /** Moves every running replay's clock to wall time `nowMs`. */
  private advanceReplays(nowMs?: number): void {
    const clock = this.effectClock;
    let wall = Number.NaN;
    for (let k = 0; k < clock.length; k++) {
      const c = clock[k];
      if (c !== c || c >= this.replayEnd[k]) continue;
      if (wall !== wall) wall = nowMs ?? this.now();
      const elapsed = wall > this.replayWall[k] ? wall - this.replayWall[k] : 0;
      const next = Math.min(this.replayEnd[k], this.replayFrom[k] + (elapsed / 1000) * this.rate);
      if (next !== c) {
        clock[k] = next;
        this.dirty = true;
      }
    }
  }

  /** Re-anchors running replays at now, before the rate changes. */
  private rebaseReplays(): void {
    this.advanceReplays();
    const wall = this.now();
    for (let k = 0; k < this.effectClock.length; k++) {
      const c = this.effectClock[k];
      if (c !== c) continue;
      this.replayFrom[k] = c;
      this.replayWall[k] = wall;
    }
  }

  /** Puts every effect back on scene time. */
  private clearReplays(): void {
    const clock = this.effectClock;
    for (let k = 0; k < clock.length; k++) {
      if (clock[k] === clock[k]) {
        clock[k] = Number.NaN;
        this.dirty = true;
      }
    }
  }

  /** The scene played from `from` to `to`: an effect it played into is on scene time again. */
  private sceneReached(from: number, to: number): void {
    const clock = this.effectClock;
    if (clock.length === 0 || !(to > from)) return;
    const effects = this.compiled.timeline.effects ?? [];
    for (let k = 0; k < clock.length; k++) {
      if (clock[k] !== clock[k]) continue;
      const t = effects[k]?.t;
      if (t !== undefined && from < t && to >= t) {
        clock[k] = Number.NaN;
        this.dirty = true;
      }
    }
  }

  // ------------------------------------------------------------ audio

  /** Uses this element for narration (null detaches). The controller owns its src while attached. */
  attachAudio(audio: AudioLike | null): void {
    if (audio === this.audio) return;
    this.detachAudio();
    this.audio = audio;
    if (!audio) {
      this.emitStatus();
      return;
    }
    audio.addEventListener?.("error", this.onAudioError);
    audio.playbackRate = this.rate;
    this.loadNarratorAudio();
    this.syncAudio();
    this.emitStatus();
  }

  private detachAudio(): void {
    const a = this.audio;
    if (!a) return;
    a.removeEventListener?.("error", this.onAudioError);
    if (!a.paused) a.pause();
    this.clearAudioSrc(a);
    this.audio = null;
    this.audioUrl = null;
    this.audioState = "none";
    this.audioPending = false;
  }

  private clearAudioSrc(a: AudioLike): void {
    if (a.removeAttribute) {
      a.removeAttribute("src");
      a.load();
    } else a.src = "";
  }

  private loadNarratorAudio(): void {
    const a = this.audio;
    if (!a) return;
    const media = this.narrator?.audio ?? null;
    let url: string | null = null;
    if (media) {
      try {
        url = this.resolveAudio ? this.resolveAudio(media) : null;
      } catch {
        url = null;
      }
    }
    if (!a.paused) a.pause();
    this.audioPending = false;
    this.audioUrl = url;
    if (url) {
      a.src = url;
      a.load();
      this.audioState = "ready";
    } else {
      this.clearAudioSrc(a);
      this.audioState = "none";
    }
  }

  private seekAudio(): void {
    const a = this.audio!;
    try {
      a.currentTime = this.t;
    } catch {
      // Not seekable yet (no metadata); syncAudio sets it again on play.
    }
  }

  /** Starts, stops or re-seeks the audio so it matches the clock. Cheap; runs every tick. */
  private syncAudio(): void {
    const a = this.audio;
    if (!a || !this.audioUrl || this.audioState === "error") return;
    const len = Number.isFinite(a.duration) ? a.duration : Number.POSITIVE_INFINITY;
    if (!this.playing || this.t >= len) {
      if (!a.paused) a.pause();
      return;
    }
    if (a.paused) {
      if (this.audioPending || this.audioState === "blocked") return;
      this.seekAudio();
      a.playbackRate = this.rate;
      this.startAudio(a);
      return;
    }
    if (Math.abs(a.currentTime - this.t) > this.driftTolerance) this.seekAudio();
  }

  private startAudio(a: AudioLike): void {
    let result: Promise<void> | void;
    try {
      result = a.play();
    } catch {
      this.onAudioError();
      return;
    }
    if (!result || typeof result.then !== "function") return;
    this.audioPending = true;
    const url = this.audioUrl;
    result.then(
      () => {
        if (this.audioUrl !== url) return;
        this.audioPending = false;
        // Paused or seeked while starting: let the next tick reconcile.
        if (!this.playing && !a.paused) a.pause();
      },
      (err: unknown) => {
        if (this.audioUrl !== url) return;
        this.audioPending = false;
        const name = (err as { name?: string } | null)?.name;
        if (name === "AbortError") return; // interrupted by pause() or a new src
        this.audioState = name === "NotAllowedError" ? "blocked" : "error";
        this.emitStatus();
      },
    );
  }

  // ------------------------------------------------------------ the loop

  /**
   * Advances the clock to wall time `nowMs` (default now()) and re-samples
   * the frame if anything changed. Returns true when the frame changed.
   * Call once per animation frame.
   */
  tick(nowMs?: number): boolean {
    if (this.playing) {
      this.advance(nowMs);
      const d = this.compiled.duration;
      if (this.t >= d) {
        if (this.loop && d > 0) {
          this.t = this.t % d;
          this.clearReplays();
          this.anchor(nowMs);
          if (this.audio && this.audioUrl) this.seekAudio();
        } else {
          this.t = d;
          this.playing = false;
          this.ended = true;
        }
      }
      this.syncAudio();
    }
    this.advanceReplays(nowMs);
    const changed = this.sampleNow();
    if (changed || this.playing) this.emitStatus();
    return changed;
  }

  /** Stops playback and audio and drops every listener. */
  dispose(): void {
    this.playing = false;
    this.detachAudio();
    this.statusListeners.clear();
    this.frameListeners = [];
  }

  // ------------------------------------------------------------ internals

  private clampTime(t: number): number {
    if (!Number.isFinite(t)) return 0;
    return Math.min(Math.max(t, 0), Math.max(this.compiled.duration, 0));
  }

  private anchor(nowMs?: number): void {
    this.anchorT = this.t;
    this.anchorWall = nowMs ?? this.now();
  }

  private advance(nowMs?: number): void {
    if (!this.playing) return;
    const wall = nowMs ?? this.now();
    // A rAF timestamp can predate a play() made later in the same frame; never run backwards.
    const elapsed = wall > this.anchorWall ? wall - this.anchorWall : 0;
    const from = this.t;
    this.t = this.anchorT + (elapsed / 1000) * this.rate;
    this.sceneReached(from, this.t);
  }

  private sampleNow(): boolean {
    if (!this.dirty && this.t === this.sampledT) return false;
    this.compiled.sample(this.t, this.frame, this.sampleOptions);
    this.sampledT = this.t;
    this.dirty = false;
    this.version++;
    const listeners = this.frameListeners;
    for (let i = 0; i < listeners.length; i++) listeners[i](this.frame);
    return true;
  }

  private buildStatus(): PlayerStatus {
    return {
      playing: this.playing || this.resumeAfterScrub,
      ended: this.ended,
      rate: this.rate,
      loop: this.loop,
      narrator: this.narratorId,
      audio: this.audioState,
      scrubbing: this.scrubHolds > 0,
      captionIndex: this.frame.narration.captionIndex,
      wordIndex: this.frame.narration.wordIndex,
      shotIndex: this.frame.shotIndex,
    };
  }

  private emitStatus(): void {
    const s = this.status;
    const f = this.frame;
    if (
      s.playing === (this.playing || this.resumeAfterScrub) &&
      s.ended === this.ended &&
      s.rate === this.rate &&
      s.loop === this.loop &&
      s.narrator === this.narratorId &&
      s.audio === this.audioState &&
      s.scrubbing === this.scrubHolds > 0 &&
      s.captionIndex === f.narration.captionIndex &&
      s.wordIndex === f.narration.wordIndex &&
      s.shotIndex === f.shotIndex
    ) {
      return;
    }
    this.status = this.buildStatus();
    for (const l of this.statusListeners) l();
  }
}
