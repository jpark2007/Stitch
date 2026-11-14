/**
 * A book's players: one PlaybackController per spread with a scene, and
 * which telling each spread shows as playing (Book3D's `tellings`).
 *
 *   const players = new ScenePlayers({ order, timelines, create, start });
 *   players.setOpen(spreadId)     the book is on this spread: its player and
 *                                 its neighbours' are made, far ones are let
 *                                 go, every other one is paused
 *   players.player(spreadId)      the spread's player (made on first ask;
 *                                 pure, so a render may ask)
 *   players.retain(spreadId)      a mounted stage holds its player: it is not
 *                                 let go while the stage stands
 *   players.tell(spreadId, id)    a teller's label was chosen on that spread
 *   players.activate()            narration elements and mute, while the
 *                                 page is shown; returns the deactivate
 *
 * Each spread keeps its own telling: switching tellers on one spread never
 * touches another, and a spread whose player was let go starts again with
 * the telling last chosen there.
 *
 * As a TellingsSource the book subscribes to it directly, so the players'
 * status updates (each caption, play, pause) never re-render React:
 * listeners hear only a change of teller on some spread, and the book
 * reprints just that story page. A spread whose scene has ended shows no
 * telling as playing (every label offers its telling again). A spread with
 * no player yet shows the telling it will start with, so making its player
 * reprints nothing.
 *
 * Type-only imports (the player is made by `create`), so node runs it
 * (moment.test.ts).
 */

import type { AudioLike, PlaybackController } from "@/components/stage/playback";
import type { Timeline } from "@/lib/types";
import type { TellingsSource } from "../Book3D";

/** A narration element: what the controller drives, plus mute. */
export interface NarrationAudio extends AudioLike {
  muted: boolean;
}

export interface ScenePlayersOptions {
  /** Every spread id, in book order (spreads without a scene included). */
  order: readonly string[];
  /** Spread id -> the timeline that plays there. */
  timelines: Readonly<Record<string, Timeline>>;
  /** Make a spread's player, starting with `narrator` (null: the default telling). */
  create: (spreadId: string, narrator: string | null) => PlaybackController;
  /** The telling a spread starts with (a shared link's ?narrator=). */
  start?: Readonly<Record<string, string | null>>;
  /** A narration element (browser only); without it the players are silent. */
  createAudio?: () => NarrationAudio | null;
  /** The book's mute (components/book/sound.ts). */
  muted?: { get(): boolean; subscribe(listener: (muted: boolean) => void): () => void };
}

interface Live {
  controller: PlaybackController;
  audio: NarrationAudio | null;
  off: () => void;
}

/** The ids a timeline's player can tell, default first (as the player orders them). */
export function timelineTellers(timeline: Pick<Timeline, "narrations" | "defaultNarrator" | "captions" | "audio">): string[] {
  const ids = Object.keys(timeline.narrations ?? {});
  if (ids.length === 0) {
    const told = (timeline.captions?.length ?? 0) > 0 || !!timeline.audio;
    return told ? [timeline.defaultNarrator ?? "narrator"] : [];
  }
  const def = timeline.defaultNarrator;
  return def && ids.includes(def) ? [def, ...ids.filter((id) => id !== def)] : ids;
}

const NONE: Readonly<Record<string, string>> = Object.freeze({});

export class ScenePlayers implements TellingsSource {
  private readonly order: readonly string[];
  private readonly timelines: Readonly<Record<string, Timeline>>;
  private readonly tellers = new Map<string, string[]>();
  private readonly create: ScenePlayersOptions["create"];
  private readonly createAudio: ScenePlayersOptions["createAudio"];
  private readonly muted: ScenePlayersOptions["muted"];
  /** The telling each spread starts with next time its player is made. */
  private readonly chosen = new Map<string, string>();
  private readonly live = new Map<string, Live>();
  private readonly held = new Map<string, number>();
  private keep = new Set<string>();
  private open: string | null = null;
  private active = false;
  private offMuted: (() => void) | null = null;
  private value: Readonly<Record<string, string>> = NONE;
  private valueCount = 0;
  private readonly listeners = new Set<() => void>();

  constructor(options: ScenePlayersOptions) {
    this.order = options.order;
    this.timelines = options.timelines;
    this.create = options.create;
    this.createAudio = options.createAudio;
    this.muted = options.muted;
    for (const id of this.order) {
      const tl = this.timelines[id];
      if (tl) this.tellers.set(id, timelineTellers(tl));
    }
    for (const [id, want] of Object.entries(options.start ?? {})) {
      if (want && this.tellers.get(id)?.includes(want)) this.chosen.set(id, want);
    }
    this.value = this.compute() ?? NONE;
    this.valueCount = Object.keys(this.value).length;
  }

  /** The spread the book is on, or null. */
  get openId(): string | null {
    return this.open;
  }

  /** Spread ids with a player now (for tests and probes). */
  get liveIds(): string[] {
    return this.order.filter((id) => this.live.has(id));
  }

  /** The spread's player, made on first ask; null for a spread with no scene. */
  player(id: string): PlaybackController | null {
    const have = this.live.get(id);
    if (have) return have.controller;
    if (!this.timelines[id]) return null;
    const controller = this.create(id, this.chosen.get(id) ?? null);
    const entry: Live = { controller, audio: null, off: () => {} };
    entry.off = controller.subscribe(() => this.changed());
    this.live.set(id, entry);
    if (this.active) this.attachAudio(entry);
    return controller;
  }

  /** The book is on spread `id`: keep it and its neighbours' players, let go of the rest, pause all but it. */
  setOpen(id: string | null): void {
    this.open = id;
    const i = id === null ? -1 : this.order.indexOf(id);
    const keep = new Set<string>();
    if (i >= 0) {
      for (let j = Math.max(0, i - 1); j <= Math.min(this.order.length - 1, i + 1); j++) {
        if (this.timelines[this.order[j]]) keep.add(this.order[j]);
      }
    }
    this.keep = keep;
    for (const k of keep) this.player(k);
    for (const [k, entry] of [...this.live]) {
      if (k === id) continue;
      entry.controller.pause();
      if (!keep.has(k) && !this.held.get(k)) this.letGo(k);
    }
    this.changed();
  }

  /** A mounted stage holds spread `id`'s player. Returns the release. */
  retain(id: string): () => void {
    this.held.set(id, (this.held.get(id) ?? 0) + 1);
    let done = false;
    return () => {
      if (done) return;
      done = true;
      const n = (this.held.get(id) ?? 1) - 1;
      if (n > 0) this.held.set(id, n);
      else {
        this.held.delete(id);
        if (!this.keep.has(id)) {
          this.letGo(id);
          this.changed();
        }
      }
    };
  }

  /** A teller's label was chosen on spread `id`: hear theirs (or the same one again, from the start). */
  tell(id: string, tellerId: string): void {
    const c = this.player(id);
    if (!c) return;
    if (this.tellers.get(id)?.includes(tellerId)) this.chosen.set(id, tellerId);
    if (c.getStatus().narrator === tellerId) {
      c.seek(0);
      c.play();
    } else c.setNarrator(tellerId);
    this.changed();
  }

  /** The open spread's scene plays but the browser held its voice back: a touch lets it in. */
  resumeBlocked(): void {
    const c = this.open ? this.live.get(this.open)?.controller : null;
    if (!c) return;
    const s = c.getStatus();
    if (s.audio === "blocked" && s.playing) c.play();
  }

  /** Narration and mute while the page is shown. Returns the deactivate: everything pauses and lets go of its audio. */
  activate(): () => void {
    if (this.active) return () => {};
    this.active = true;
    for (const entry of this.live.values()) this.attachAudio(entry);
    this.offMuted =
      this.muted?.subscribe((m) => {
        for (const entry of this.live.values()) if (entry.audio) entry.audio.muted = m;
      }) ?? null;
    return () => {
      this.active = false;
      this.offMuted?.();
      this.offMuted = null;
      for (const entry of this.live.values()) {
        entry.controller.pause();
        if (entry.audio) {
          entry.controller.attachAudio(null);
          entry.audio = null;
        }
      }
    };
  }

  /** Let go of every player. */
  dispose(): void {
    for (const id of [...this.live.keys()]) this.letGo(id);
    this.offMuted?.();
    this.offMuted = null;
    this.listeners.clear();
  }

  // ---------------------------------------------------------- TellingsSource

  get = (): Readonly<Record<string, string>> => this.value;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  // ---------------------------------------------------------------- internals

  private attachAudio(entry: Live): void {
    if (entry.audio || !this.createAudio) return;
    const el = this.createAudio();
    if (!el) return;
    el.muted = this.muted?.get() ?? false;
    entry.audio = el;
    entry.controller.attachAudio(el);
  }

  private letGo(id: string): void {
    const entry = this.live.get(id);
    if (!entry) return;
    this.live.delete(id);
    entry.off();
    // Pauses, detaches the narration element (dropping its source) and its listeners.
    entry.controller.dispose();
    entry.audio = null;
  }

  /** The telling spread `id` shows as playing now, or null. */
  private showing(id: string): string | null {
    const entry = this.live.get(id);
    if (entry) {
      const s = entry.controller.getStatus();
      return s.ended ? null : s.narrator;
    }
    return this.chosen.get(id) ?? this.tellers.get(id)?.[0] ?? null;
  }

  /** The new value when it differs from the current one, else null. Allocates only on a change. */
  private compute(): Readonly<Record<string, string>> | null {
    let same = true;
    let count = 0;
    for (const id of this.order) {
      const v = this.showing(id);
      if (v !== null) count++;
      if ((this.value[id] ?? null) !== v) same = false;
    }
    if (same && count === this.valueCount) return null;
    const next: Record<string, string> = {};
    for (const id of this.order) {
      const v = this.showing(id);
      if (v !== null) next[id] = v;
    }
    return next;
  }

  private changed(): void {
    const next = this.compute();
    if (!next) return;
    this.value = next;
    this.valueCount = Object.keys(next).length;
    for (const l of [...this.listeners]) l();
  }
}
