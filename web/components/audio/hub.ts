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
 * The decisions and the effects are hubState.ts's (createMusicHub, tested
 * with a fake context); this file only wires them to the window, the book's
 * mute and a real AudioContext. While the book is muted a gesture makes
 * nothing (like sound.ts); the unmute click does. The book's own sounds keep
 * their context (sound.ts, read-only here); the first gesture unlocks both.
 */

import { isMuted, subscribeMuted, unlockAudio } from "@/components/book/sound";
import { createMusicHub } from "./hubState";

const hub = createMusicHub<AudioContext>({
  isMuted,
  subscribeMuted,
  unlockAudio,
  makeContext() {
    if (typeof window === "undefined") return null;
    const AC = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    // "playback": the largest buffers the system offers. Music needs no low
    // latency, and fewer audio-thread wake-ups leave more for the book.
    return AC ? new AC({ latencyHint: "playback" }) : null;
  },
  listen(on, onGesture) {
    if (typeof window === "undefined") return;
    const method = on ? "addEventListener" : "removeEventListener";
    window[method]("pointerdown", onGesture, true);
    window[method]("keydown", onGesture, true);
  },
});

export const musicContext = (): AudioContext | null => hub.context();
export const attachMusic = (cb: (c: AudioContext) => void) => hub.attach(cb);
export const wakeMusic = (owner: object) => hub.wake(owner);
export const sleepMusic = (owner: object) => hub.sleep(owner);
/** True while some engine holds the context awake (tests, the lab). */
export const musicAwake = () => hub.awake();
/** The hub's own view of the context ("none" | "running" | "suspended"). */
export const musicHubStatus = () => hub.state().status;
