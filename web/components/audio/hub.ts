/**
 * The one AudioContext the music shares, made only inside a reader's gesture
 * (the same rule as the book's paper sounds, sound.ts: constructing a
 * context can block the main thread, so it must never land mid-animation),
 * and suspended whenever no engine is playing so a quiet book costs nothing.
 *
 *   requestMusicContext(cb)   cb(ctx) now, or at the reader's next gesture
 *   wakeMusic(owner)          resume the context for this owner
 *   sleepMusic(owner)         suspend it once no owner is awake
 *
 * The book's own sounds keep their context (sound.ts, read-only here); the
 * first gesture unlocks both.
 */

import { unlockAudio } from "@/components/book/sound";

let ctx: AudioContext | null = null;
const waiting = new Set<(c: AudioContext) => void>();
const awake = new Set<object>();
let armed = false;

type UA = { isActive?: boolean; hasBeenActive?: boolean };
const userActivation = (): UA | undefined =>
  typeof navigator === "undefined" ? undefined : (navigator as Navigator & { userActivation?: UA }).userActivation;

export function musicContext(): AudioContext | null {
  return ctx;
}

function create(): AudioContext | null {
  if (ctx) return ctx;
  if (typeof window === "undefined") return null;
  const AC = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AC) return null;
  const c = new AC({ latencyHint: "playback" });
  ctx = c;
  disarm();
  const list = [...waiting];
  waiting.clear();
  for (const cb of list) cb(c);
  // Nobody wanted to play after all: sleep rather than run silence.
  if (!awake.size) void c.suspend().catch(() => {});
  return c;
}

function onGesture() {
  unlockAudio();
  if (waiting.size) create();
}

function arm() {
  if (armed || typeof window === "undefined") return;
  armed = true;
  window.addEventListener("pointerdown", onGesture, true);
  window.addEventListener("keydown", onGesture, true);
  window.addEventListener("touchend", onGesture, true);
}

function disarm() {
  if (!armed) return;
  armed = false;
  window.removeEventListener("pointerdown", onGesture, true);
  window.removeEventListener("keydown", onGesture, true);
  window.removeEventListener("touchend", onGesture, true);
}

/**
 * Call `cb` with the shared context: at once if it exists or we are inside a
 * gesture (a mute-button click, say), otherwise at the reader's next pointer
 * or key press. Returns a cancel function.
 */
export function requestMusicContext(cb: (c: AudioContext) => void): () => void {
  if (ctx) {
    cb(ctx);
    return () => {};
  }
  waiting.add(cb);
  const ua = userActivation();
  if (ua?.isActive) create();
  else arm();
  return () => {
    waiting.delete(cb);
    if (!waiting.size) disarm();
  };
}

export function wakeMusic(owner: object) {
  awake.add(owner);
  if (ctx && ctx.state === "suspended") void ctx.resume().catch(() => {});
}

export function sleepMusic(owner: object) {
  awake.delete(owner);
  if (ctx && !awake.size && ctx.state === "running") void ctx.suspend().catch(() => {});
}
