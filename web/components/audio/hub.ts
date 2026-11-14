/**
 * The one AudioContext the music shares, made only inside a reader's gesture
 * (the same rule as the book's paper sounds, sound.ts: constructing a
 * context, and the engine's reverb with it, is main-thread work that must
 * never land mid-animation, so it happens in the pointerdown/keydown that
 * starts things, before that gesture's page turn begins), and suspended
 * whenever no engine is playing, so a quiet book costs nothing.
 *
 *   attachMusic(cb)        cb(ctx) once the context exists (now, if it does);
 *                          the returned detach lets go
 *   wakeMusic(owner)       this engine wants the context running
 *   sleepMusic(owner)      it does not; suspended once no engine does
 *
 * The decisions are hubState.ts's pure step; this file only carries out its
 * effects. While the book is muted a gesture makes nothing (like sound.ts);
 * the unmute click does. The book's own sounds keep their context (sound.ts,
 * read-only here); the first gesture unlocks both.
 */

import { isMuted, subscribeMuted, unlockAudio } from "@/components/book/sound";
import { INITIAL_HUB, hubStep, type HubEffect, type HubEvent, type HubState } from "./hubState";

let ctx: AudioContext | null = null;
let state: HubState = INITIAL_HUB;
const clients = new Set<(c: AudioContext) => void>();
const awake = new Set<object>();
let unsubscribeMute: (() => void) | null = null;

export function musicContext(): AudioContext | null {
  return ctx;
}

function dispatch(ev: HubEvent) {
  const r = hubStep(state, ev);
  state = r.state;
  for (const fx of r.effects) apply(fx);
}

function apply(fx: HubEffect) {
  switch (fx) {
    case "create":
      create();
      break;
    // Always ask, even when the state already looks right: a suspend() or
    // resume() still in flight would otherwise win, and they settle in order.
    case "resume":
      if (ctx && ctx.state !== "closed") void ctx.resume().catch(() => {});
      break;
    case "suspend":
      if (ctx && ctx.state !== "closed") void ctx.suspend().catch(() => {});
      break;
    case "unlockBook":
      unlockAudio();
      break;
    case "listen":
      listen(true);
      break;
    case "unlisten":
      listen(false);
      break;
  }
}

function create() {
  if (ctx || typeof window === "undefined") return;
  const AC = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AC) return;
  // "playback": the largest buffers the system offers. Music needs no low
  // latency, and fewer audio-thread wake-ups leave more for the book.
  const c = new AC({ latencyHint: "playback" });
  ctx = c;
  for (const cb of [...clients]) cb(c);
}

const onGesture = () => dispatch({ type: "gesture" });

function listen(on: boolean) {
  if (typeof window === "undefined") return;
  if (on) {
    window.addEventListener("pointerdown", onGesture, true);
    window.addEventListener("keydown", onGesture, true);
    if (isMuted() !== state.muted) dispatch({ type: "mute", on: isMuted() });
    unsubscribeMute = subscribeMuted((m) => dispatch({ type: "mute", on: m }));
  } else {
    window.removeEventListener("pointerdown", onGesture, true);
    window.removeEventListener("keydown", onGesture, true);
    unsubscribeMute?.();
    unsubscribeMute = null;
  }
}

/**
 * Call `cb` with the shared context as soon as it exists: now, if it does,
 * else inside the reader's next pointer or key press (or unmute click).
 * Returns the detach; the gesture listeners go away with the last engine.
 */
export function attachMusic(cb: (c: AudioContext) => void): () => void {
  let attached = true;
  if (ctx) cb(ctx);
  else clients.add(cb);
  dispatch({ type: "attach" });
  return () => {
    if (!attached) return;
    attached = false;
    clients.delete(cb);
    dispatch({ type: "detach" });
  };
}

export function wakeMusic(owner: object) {
  const had = awake.size > 0;
  awake.add(owner);
  if (!had) dispatch({ type: "awake", on: true });
  // Another engine already holds it awake: still ask, in case it is interrupted.
  else if (ctx && ctx.state !== "running" && ctx.state !== "closed") void ctx.resume().catch(() => {});
}

export function sleepMusic(owner: object) {
  if (!awake.delete(owner)) return;
  if (awake.size === 0) dispatch({ type: "awake", on: false });
}

/** True while some engine holds the context awake (tests, the lab). */
export function musicAwake() {
  return awake.size > 0;
}
