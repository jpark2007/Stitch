/**
 * Render the music engine faster than real time into an AudioBuffer, for the
 * verification renders (levels, spectra, crossfade and mute clicks). It is the
 * live engine's own graph and scheduler (core.ts) and the same transport
 * decisions (fades.ts), stepped through OfflineAudioContext.suspend() every
 * 100 ms exactly as the live timer steps it.
 */

import { MusicCore } from "./core";
import type { EraId } from "./eras";
import { stepTransport, tickTransport, type Phase, type TransportAction } from "./fades";

export interface OfflineCue {
  /** Seconds into the render. */
  at: number;
  era?: EraId | null;
  muted?: boolean;
  ducked?: boolean;
  active?: boolean;
}

export interface OfflineResult {
  buffer: AudioBuffer;
  notes: number;
  /** Main-thread milliseconds spent scheduling (sum and worst pass). */
  busyMs: number;
  maxTickMs: number;
}

const STEP = 0.1;

export async function renderEraMusic(cues: OfflineCue[], seconds: number, sampleRate = 44100): Promise<OfflineResult> {
  const ctx = new OfflineAudioContext(2, Math.ceil(seconds * sampleRate), sampleRate);
  const core = new MusicCore(ctx, ctx.destination);
  const state = { era: null as EraId | null, muted: false, ducked: false, active: true };
  let phase: Phase = { kind: "asleep" };
  let notes = 0;
  let busyMs = 0;
  let maxTickMs = 0;
  const pending = cues.slice().sort((a, b) => a.at - b.at);

  const run = (actions: TransportAction[], now: number) => {
    for (const a of actions) if (a.kind === "level") core.setLevel(a.to, a.dur, now);
    // resume / suspend: an offline context cannot sleep; the level is already 0.
  };

  const step = (now: number) => {
    const t0 = performance.now();
    let why: "mute" | "active" | "era" | null = null;
    while (pending.length && pending[0].at <= now + 1e-6) {
      const c = pending.shift()!;
      if (c.era !== undefined) state.era = c.era;
      if (c.ducked !== undefined) state.ducked = c.ducked;
      if (c.muted !== undefined) {
        state.muted = c.muted;
        why = "mute";
      }
      if (c.active !== undefined) {
        state.active = c.active;
        why ??= "active";
      }
      why ??= "era";
    }
    if (why) {
      core.setEra(state.era, now);
      core.setDucked(state.ducked, now);
      const r = stepTransport(phase, { muted: state.muted, active: state.active, sounding: state.era !== null || core.sounding }, now, why);
      phase = r.phase;
      run(r.actions, now);
    }
    notes += core.tick(now);
    const r = tickTransport(phase, now);
    phase = r.phase;
    run(r.actions, now);
    const ms = performance.now() - t0;
    busyMs += ms;
    maxTickMs = Math.max(maxTickMs, ms);
  };

  step(0);
  const quantum = 128 / sampleRate;
  for (let t = STEP; t < seconds - STEP; t += STEP) {
    const at = Math.round(t / quantum) * quantum;
    void ctx.suspend(at).then(() => {
      step(ctx.currentTime);
      void ctx.resume();
    });
  }
  const buffer = await ctx.startRendering();
  return { buffer, notes, busyMs, maxTickMs };
}

/**
 * Main-thread cost of starting an era (the voice's build plus its first
 * scheduling passes, which build its strings), cold or after prefetch(),
 * each on a fresh core. For checking what prefetching saves.
 */
export function measureEraStart(
  era: EraId,
  passes = 12,
): { coldMs: number; prefetchedMs: number; coldWorstPassMs: number; prefetchedWorstPassMs: number } {
  const run = (prefetch: boolean) => {
    const ctx = new OfflineAudioContext(2, 44100, 44100);
    const core = new MusicCore(ctx, ctx.destination);
    if (prefetch) {
      core.prefetch(era);
      while (core.warmingUp) core.warm(50);
    }
    let worst = 0;
    let total = 0;
    for (let i = 0; i < passes; i++) {
      const t0 = performance.now();
      if (i === 0) core.setEra(era, 0);
      core.tick(i * STEP);
      const ms = performance.now() - t0;
      worst = Math.max(worst, ms);
      total += ms;
    }
    core.dispose(0, 0);
    return { total, worst };
  };
  // Once each to warm the JIT, then the measured pair.
  run(false);
  run(true);
  const cold = run(false);
  const warm = run(true);
  return { coldMs: cold.total, prefetchedMs: warm.total, coldWorstPassMs: cold.worst, prefetchedWorstPassMs: warm.worst };
}

/** 16-bit PCM WAV of a buffer (for saving renders). */
export function encodeWav(buffer: AudioBuffer): ArrayBuffer {
  const ch = buffer.numberOfChannels;
  const n = buffer.length;
  const out = new ArrayBuffer(44 + n * ch * 2);
  const v = new DataView(out);
  const str = (o: number, s: string) => {
    for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i));
  };
  str(0, "RIFF");
  v.setUint32(4, 36 + n * ch * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, ch, true);
  v.setUint32(24, buffer.sampleRate, true);
  v.setUint32(28, buffer.sampleRate * ch * 2, true);
  v.setUint16(32, ch * 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, n * ch * 2, true);
  const data = Array.from({ length: ch }, (_, c) => buffer.getChannelData(c));
  let o = 44;
  for (let i = 0; i < n; i++) {
    for (let c = 0; c < ch; c++) {
      const s = Math.max(-1, Math.min(1, data[c][i]));
      v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      o += 2;
    }
  }
  return out;
}
