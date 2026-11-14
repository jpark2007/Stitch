/**
 * Gentle generated music per era, live.
 *
 *   const music = createEraMusic();
 *   music.setEra("1960s");     // crossfades ~2.6 s from whatever was playing
 *   music.setDucked(true);     // -10 dB under narration, smoothly
 *   music.setActive(false);    // the book closed: ebb away, then sleep
 *   music.dispose();
 *
 * Lazy: creating one costs a mute subscription and nothing else. The shared
 * AudioContext (hub.ts) is made at the reader's first gesture once there is
 * something to play, and suspended whenever nothing is (muted, closed, no
 * era), so a quiet page spends no audio CPU at all.
 *
 * It obeys the book's mute (sound.ts): muting fades the music out in 0.3 s
 * and suspends the context; unmuting resumes it and fades back in. One
 * button silences music, narration and paper sounds together.
 *
 * Scheduling runs on a lookahead timer (setTimeout every 100 ms, notes made
 * 0.4 s ahead at exact AudioContext times), never in requestAnimationFrame,
 * and only while the music is awake.
 */

import { isMuted, subscribeMuted } from "@/components/book/sound";
import { MusicCore, type MeterReading } from "./core";
import type { EraId } from "./eras";
import { stepTransport, tickTransport, wantsSound, type Phase, type TransportAction } from "./fades";
import { requestMusicContext, sleepMusic, wakeMusic } from "./hub";

/** Scheduler period. The lookahead (core.ts) covers four of these. */
const TICK_MS = 100;

export interface EraMusicStats {
  /** Scheduler passes run, and their main-thread cost. */
  ticks: number;
  busyMs: number;
  maxTickMs: number;
  notes: number;
  voices: number;
  phase: Phase["kind"];
  context: AudioContextState | "none";
}

export interface EraMusic {
  /** Crossfade to an era; null fades the music out. */
  setEra(id: EraId | null): void;
  /** Duck under narration (-10 dB) or come back up. */
  setDucked(on: boolean): void;
  /** The book is open (music may play) or closed (it ebbs away). */
  setActive(on: boolean): void;
  dispose(): void;
  /** Output level for a meter; null before audio has started. */
  meter(): MeterReading | null;
  stats(): EraMusicStats;
}

export function createEraMusic(opts: { active?: boolean } = {}): EraMusic {
  let era: EraId | null = null;
  let ducked = false;
  let active = opts.active ?? true;
  let muted = isMuted();
  let ctx: AudioContext | null = null;
  let core: MusicCore | null = null;
  let phase: Phase = { kind: "asleep" };
  let timer: ReturnType<typeof setTimeout> | null = null;
  let cancelRequest: (() => void) | null = null;
  let disposed = false;
  const owner = {};
  const st = { ticks: 0, busyMs: 0, maxTickMs: 0, notes: 0 };

  const input = () => ({ muted, active, sounding: era !== null || (core?.sounding ?? false) });

  function run(actions: TransportAction[], now: number) {
    for (const a of actions) {
      if (a.kind === "resume") wakeMusic(owner);
      else if (a.kind === "level") core?.setLevel(a.to, a.dur, now);
      else sleepMusic(owner);
    }
  }

  function startTimer() {
    if (timer === null && !disposed) timer = setTimeout(tick, 0);
  }

  function tick() {
    timer = null;
    if (disposed || !core || !ctx) return;
    const t0 = performance.now();
    const now = ctx.currentTime;
    st.notes += core.tick(now);
    if (!core.sounding && phase.kind === "playing") {
      const r = stepTransport(phase, input(), now);
      phase = r.phase;
      run(r.actions, now);
    }
    const r = tickTransport(phase, now);
    phase = r.phase;
    run(r.actions, now);
    const ms = performance.now() - t0;
    st.ticks++;
    st.busyMs += ms;
    if (ms > st.maxTickMs) st.maxTickMs = ms;
    if (phase.kind !== "asleep") timer = setTimeout(tick, TICK_MS);
  }

  function update(why: "mute" | "active" | "era") {
    if (disposed) return;
    if (!core || !ctx) {
      // Nothing is made until there is something to hear.
      if (wantsSound(input()) && !cancelRequest) {
        cancelRequest = requestMusicContext((c) => {
          cancelRequest = null;
          if (disposed) return;
          ctx = c;
          core = new MusicCore(c, c.destination);
          update(why);
        });
      }
      return;
    }
    const now = ctx.currentTime;
    core.setEra(era, now);
    core.setDucked(ducked, now);
    const r = stepTransport(phase, input(), now, why);
    phase = r.phase;
    run(r.actions, now);
    if (phase.kind !== "asleep") startTimer();
  }

  const unsubscribe = subscribeMuted((m) => {
    muted = m;
    update("mute");
  });

  return {
    setEra(id) {
      if (id === era) return;
      era = id;
      update("era");
    },
    setDucked(on) {
      if (on === ducked) return;
      ducked = on;
      update("era");
    },
    setActive(on) {
      if (on === active) return;
      active = on;
      update("active");
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      unsubscribe();
      cancelRequest?.();
      cancelRequest = null;
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
      };
    },
  };
}
