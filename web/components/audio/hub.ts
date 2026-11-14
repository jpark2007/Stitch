/**
 * The one AudioContext the music shares, made only inside a reader's gesture
 * (the same rule as the book's paper sounds, sound.ts: constructing a
 * context, and the engine's reverb with it, is main-thread work that must
 * never land mid-animation, so it happens in the pointerdown/keydown that
 * starts things, before that gesture's page turn begins), and suspended
 * whenever no engine is playing, so a quiet book costs nothing.
 *
 *   attachMusic(cb)        cb(ctx) at the reader's next gesture (now, if made)
 *   createMusicContext()   make it now: only from inside a gesture (unmute)
 *   wakeMusic(owner)       resume the context for this owner
 *   sleepMusic(owner)      suspend it once no owner is awake
 *
 * While the book is muted a gesture makes nothing (like sound.ts); the
 * unmute click does. The book's own sounds keep their context (sound.ts,
 * read-only here); the first gesture unlocks both.
 */

import { isMuted, unlockAudio } from "@/components/book/sound";

let ctx: AudioContext | null = null;
const waiting = new Set<(c: AudioContext) => void>();
const awake = new Set<object>();
let armed = false;

export function musicContext(): AudioContext | null {
  return ctx;
}

/** Make the shared context and hand it to everyone waiting. Call inside a gesture. */
export function createMusicContext(): AudioContext | null {
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
  // Nobody wants to play yet: sleep rather than run silence.
  if (!awake.size) void c.suspend().catch(() => {});
  return c;
}

function onGesture() {
  if (isMuted()) return;
  unlockAudio();
  if (waiting.size) createMusicContext();
}

function arm() {
  if (armed || typeof window === "undefined") return;
  armed = true;
  window.addEventListener("pointerdown", onGesture, true);
  window.addEventListener("keydown", onGesture, true);
}

function disarm() {
  if (!armed) return;
  armed = false;
  window.removeEventListener("pointerdown", onGesture, true);
  window.removeEventListener("keydown", onGesture, true);
}

/**
 * Call `cb` with the shared context at the reader's next pointer or key
 * press (at once if it already exists). Returns a cancel function; the
 * gesture listeners go away with the last waiter.
 */
export function attachMusic(cb: (c: AudioContext) => void): () => void {
  if (ctx) {
    cb(ctx);
    return () => {};
  }
  waiting.add(cb);
  arm();
  return () => {
    waiting.delete(cb);
    if (!waiting.size) disarm();
  };
}

// Always ask, even when the state already looks right: a suspend() or
// resume() still in flight would otherwise win, and they settle in order.
export function wakeMusic(owner: object) {
  awake.add(owner);
  if (ctx && ctx.state !== "closed") void ctx.resume().catch(() => {});
}

export function sleepMusic(owner: object) {
  awake.delete(owner);
  if (ctx && !awake.size && ctx.state !== "closed") void ctx.suspend().catch(() => {});
}

/** True while some engine holds the context awake (tests, the lab). */
export function musicAwake() {
  return awake.size > 0;
}
