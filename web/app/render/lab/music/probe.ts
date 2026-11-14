"use client";

/**
 * The music lab's probe (?probe=1), for the verification scripts: installs
 * `window.__eraMusic`, loading the offline renderer only when asked for.
 *
 *   render(cues, seconds)   the real engine graph and scheduler in an
 *                           OfflineAudioContext, faster than real time -> WAV
 *   stats() / hub()         the live engine and the shared context
 *   prefetch(era)           the live engine's prefetch (the next spread's era)
 *   startCost(era)          main-thread ms to start an era, cold vs prefetched
 *   mount(on)               mount or unmount the engine's component (teardown)
 *
 * It never touches the live AudioContext beyond reading its state.
 */

import { useEffect, type RefObject } from "react";
import type { EraId, EraMusic } from "@/components/audio";
import type { OfflineCue } from "@/components/audio/offline";

export interface MusicProbe {
  render(cues: OfflineCue[], seconds: number): Promise<{ wav: string; notes: number; busyMs: number; maxTickMs: number }>;
  stats(): ReturnType<EraMusic["stats"]> | null;
  prefetch(era: EraId): void;
  /** Main-thread ms to start an era cold vs prefetched (fresh offline cores). */
  startCost(era: EraId): ReturnType<typeof import("@/components/audio/offline").measureEraStart>;
  mount(on: boolean): void;
  /** The real context's state beside the hub's own view of it: they must agree once settled. */
  hub(): { context: AudioContextState | "none"; status: "none" | "running" | "suspended"; awake: boolean };
}

declare global {
  interface Window {
    __eraMusic?: MusicProbe;
  }
}

/** Base64 of a byte array, in chunks (a WAV is too big for one apply). */
function base64(bytes: Uint8Array) {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

export function useMusicProbe(on: boolean, music: RefObject<EraMusic | null>, setMounted: (on: boolean) => void) {
  useEffect(() => {
    if (!on) return;
    let live = true;
    void Promise.all([import("@/components/audio/offline"), import("@/components/audio/hub")]).then(([offline, hub]) => {
      if (!live) return;
      window.__eraMusic = {
        async render(cues, seconds) {
          const r = await offline.renderEraMusic(cues, seconds);
          return { wav: base64(new Uint8Array(offline.encodeWav(r.buffer))), notes: r.notes, busyMs: r.busyMs, maxTickMs: r.maxTickMs };
        },
        stats: () => music.current?.stats() ?? null,
        prefetch: (era) => music.current?.prefetch(era),
        startCost: (era) => offline.measureEraStart(era),
        mount: setMounted,
        hub: () => ({ context: hub.musicContext()?.state ?? "none", status: hub.musicHubStatus(), awake: hub.musicAwake() }),
      };
    });
    return () => {
      live = false;
      delete window.__eraMusic;
    };
  }, [on, music, setMounted]);
}
