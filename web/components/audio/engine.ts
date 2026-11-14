/**
 * Gentle generated music per era, live.
 *
 *   const music = createEraMusic();
 *   music.setEra("1960s");     // crossfades ~2.6 s from whatever was playing
 *   music.prefetch("1970s");   // the next spread's era: ready before it is asked for
 *   music.setDucked(true);     // -10 dB under narration, smoothly
 *   music.setActive(false);    // the book closed: ebb away, then sleep
 *   music.dispose();
 *
 * Lazy: until the reader's first pointer or key press an engine is a mute
 * subscription and two event listeners, nothing more. That gesture makes the
 * shared AudioContext and this engine's graph (hub.ts), before the gesture's
 * own animation starts; the context then sleeps (suspended) whenever there is
 * nothing to hear (muted, closed, no era), so a quiet page spends no audio CPU.
 *
 * It obeys the book's mute (sound.ts): muting fades the music out in 0.3 s
 * and suspends the context; unmuting resumes it and fades back in. One
 * button silences music, narration and paper sounds together.
 *
 * Every call is cheap and returns at once: the work (building a voice,
 * scheduling notes, prefetching) happens on the engine's lookahead timer
 * (setTimeout every 100 ms, notes made 0.4 s ahead at exact AudioContext
 * times), never in requestAnimationFrame and never inside the caller's
 * effect, and only while the music is awake or warming an era up.
 */

import { isMuted, subscribeMuted } from "@/components/book/sound";
import { MusicCore, type MeterReading } from "./core";
import type { EraId } from "./eras";
import { stepTransport, tickTransport, wantsSound, type Phase, type TransportAction } from "./fades";
import { attachMusic, sleepMusic, wakeMusic } from "./hub";

/** Scheduler period. The lookahead (core.ts) covers four of these. */
const TICK_MS = 100;
/** Main-thread milliseconds a pass may spend warming a prefetched era. */
const PREFETCH_BUDGET_MS = 1.5;

export interface EraMusicStats {
  /** Scheduler passes run, and their main-thread cost. */
  ticks: number;
  busyMs: number;
  /** Worst pass after the first. */
  maxTickMs: number;
  /** The first pass (cold: the JIT compiles the engine). */
  firstTickMs: number;
  notes: number;
  voices: number;
  phase: Phase["kind"];
  context: AudioContextState | "none";
  /** A prefetched era is still being made ready. */
  warming: boolean;
}

export interface EraMusic {
  /** Crossfade to an era; null fades the music out. */
  setEra(id: EraId | null): void;
  /** Make an era ready to start instantly (the next spread's); no sound. */
  prefetch(id: EraId | null): void;
  /** Duck under narration (-10 dB) or come back up. */
  setDucked(on: boolean): void;
  /** The book is open (music may play) or closed (it ebbs away). */
  setActive(on: boolean): void;
  dispose(): void;
  /** Output level for a meter; null before audio has started. */
  meter(): MeterReading | null;
  stats(): EraMusicStats;
}

export interface EraMusicOptions {
  era?: EraId | null;
  active?: boolean;
  ducked?: boolean;
}

export function createEraMusic(opts: EraMusicOptions = {}): EraMusic {
  let era: EraId | null = opts.era ?? null;
  let ducked = opts.ducked ?? false;
  let active = opts.active ?? true;
  let muted = isMuted();
  let ctx: AudioContext | null = null;
  let core: MusicCore | null = null;
  let phase: Phase = { kind: "asleep" };
  let timer: ReturnType<typeof setTimeout> | null = null;
  let disposed = false;
  /** The last change's reason, for the transport's fade length. */
  let why: "mute" | "active" | "era" | null = null;
  /** Eras asked to prefetch before the core existed, or since the last pass. */
  const prefetches: EraId[] = [];
  const owner = {};
  const st = { ticks: 0, busyMs: 0, maxTickMs: 0, firstTickMs: 0, notes: 0 };

  const input = () => ({ muted, active, sounding: era !== null || (core?.sounding ?? false) });

  function run(actions: TransportAction[], now: number) {
    for (const a of actions) {
      if (a.kind === "resume") wakeMusic(owner);
      else if (a.kind === "level") core?.setLevel(a.to, a.dur, now);
      else {
        // Silent now: a voice that was fading out need not wait for the wake.
        core?.retireEnding(now);
        sleepMusic(owner);
      }
    }
  }

  /** One pass: apply what changed, schedule ahead, advance the transport, warm up. */
  function tick() {
    timer = null;
    if (disposed || !core || !ctx) return;
    const t0 = performance.now();
    const now = ctx.currentTime;
    if (why) {
      core.setEra(era, now);
      core.setDucked(ducked, now);
      const r = stepTransport(phase, input(), now, why);
      phase = r.phase;
      run(r.actions, now);
      why = null;
      // changed() may have woken the context for a change that was undone
      // before this pass: nothing to hear, so let it sleep again.
      if (phase.kind === "asleep") sleepMusic(owner);
    }
    if (phase.kind !== "asleep") {
      st.notes += core.tick(now);
      if (!core.sounding && phase.kind === "playing") {
        const r = stepTransport(phase, input(), now);
        phase = r.phase;
        run(r.actions, now);
      }
      const r = tickTransport(phase, now);
      phase = r.phase;
      run(r.actions, now);
    }
    // Prefetching: one era's score per pass, its strings in small slices.
    const next = prefetches.shift();
    if (next) core.prefetch(next);
    else core.warm(PREFETCH_BUDGET_MS);
    const ms = performance.now() - t0;
    // The very first pass compiles everything (JIT); keep it apart from the steady worst.
    if (st.ticks === 0) st.firstTickMs = ms;
    else if (ms > st.maxTickMs) st.maxTickMs = ms;
    st.ticks++;
    st.busyMs += ms;
    if (phase.kind !== "asleep" || prefetches.length || core.warmingUp) timer = setTimeout(tick, TICK_MS);
  }

  /** Something changed: handle it on the next pass (never inside the caller). */
  function changed(reason: "mute" | "active" | "era") {
    if (disposed) return;
    // A mute decides the fade length over an era change in the same pass.
    if (why !== "mute") why = reason;
    if (!core) return;
    // Wake the context now, while the caller may still be inside the reader's
    // gesture (iOS resumes a suspended context only then). The level is still
    // 0 until the pass fades it in, so waking early is silent.
    if (wantsSound(input())) wakeMusic(owner);
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(tick, 0);
  }

  const detach = attachMusic((c) => {
    if (disposed) return;
    ctx = c;
    core = new MusicCore(c, c.destination);
    why ??= "era";
    changed(why);
  });

  const unsubscribe = subscribeMuted((m) => {
    muted = m;
    changed("mute");
  });

  return {
    setEra(id) {
      if (id === era) return;
      era = id;
      changed("era");
    },
    prefetch(id) {
      if (disposed || !id || id === era || prefetches.includes(id)) return;
      prefetches.push(id);
      if (core && timer === null) timer = setTimeout(tick, TICK_MS);
    },
    setDucked(on) {
      if (on === ducked) return;
      ducked = on;
      changed("era");
    },
    setActive(on) {
      if (on === active) return;
      active = on;
      changed("active");
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      unsubscribe();
      detach();
      if (timer !== null) clearTimeout(timer);
      timer = null;
      if (core && ctx) {
        core.dispose(ctx.currentTime, 0.15);
        // Sleep once the short fade has played, so suspending cannot click.
        if (phase.kind === "asleep") sleepMusic(owner);
        else setTimeout(() => sleepMusic(owner), 220);
      }
      core = null;
    },
    meter() {
      return core ? core.meter() : null;
    },
    stats() {
      return {
        ...st,
        voices: core?.voiceCount ?? 0,
        phase: phase.kind,
        context: ctx ? ctx.state : "none",
        warming: core !== null && (prefetches.length > 0 || core.warmingUp),
      };
    },
  };
}
