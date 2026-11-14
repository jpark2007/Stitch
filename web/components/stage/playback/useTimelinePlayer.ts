/**
 * React glue for the timeline player: one PlaybackController per timeline,
 * driven by requestAnimationFrame, with an <audio> element for narration.
 *
 *   const { controller, frame, status } = useTimelinePlayer(timeline, { narrator, resolveAudio });
 *
 * - `frame` is the controller's live frame. It is mutated in place every
 *   animation frame and does NOT re-render React. Read it inside r3f's
 *   useFrame (or controller.onFrame) and write the numbers into three objects.
 * - `status` is React state: playing, narrator, caption and word index, audio
 *   state. It changes only on discrete events, so caption UI re-renders a few
 *   times a second at most.
 * - usePlayerFrame(controller) re-renders a component on every sampled frame,
 *   for 2D debug views; the 3D stage should not need it.
 */

import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import type { Timeline } from "@/lib/types";

import { PlaybackController, type ControllerOptions, type PlayerStatus } from "./controller";
import { PUPPET_BONES } from "./rig";
import { compileTimeline } from "./sampler";
import type { PlaybackFrame } from "./types";

export interface UseTimelinePlayerOptions {
  /**
   * Teller (author person id). Changing it after mount switches the telling:
   * audio and captions swap and the scene replays from 0.
   */
  narrator?: string | null;
  loop?: boolean;
  /** Playback rate, 0.1 .. 4 */
  rate?: number;
  /** Start playing on mount. Browsers may block the audio until a click; the scene plays regardless. */
  autoplay?: boolean;
  /**
   * Freeze at this time (seconds): the player seeks there and never advances
   * on its own. For screenshots and the checker's frame captures.
   */
  freezeAt?: number | null;
  /** Create an <audio> element for narration (default true) */
  audio?: boolean;
  /** Media id -> playable URL. Without it the player is silent. */
  resolveAudio?: ControllerOptions["resolveAudio"];
  /** Rig bones every puppet frame carries; default shared/rig/puppet.json */
  bones?: readonly string[];
}

export interface TimelinePlayer {
  controller: PlaybackController;
  /** Live, mutable frame (see the note at the top of this file) */
  frame: PlaybackFrame;
  status: PlayerStatus;
}

export function useTimelinePlayer(timeline: Timeline, options: UseTimelinePlayerOptions = {}): TimelinePlayer {
  const {
    narrator = null,
    loop = false,
    rate = 1,
    autoplay = false,
    freezeAt = null,
    audio = true,
    resolveAudio,
    bones = PUPPET_BONES,
  } = options;

  // The controller is pure until its effects run (no DOM, no timers), so it is
  // safe to build during render. Initial-only options are read here; the rest
  // are synced by the effects below.
  const controller = useMemo(
    () =>
      new PlaybackController(compileTimeline(timeline, { bones }), {
        narrator,
        loop,
        rate,
        resolveAudio,
        time: freezeAt ?? 0,
      }),
    // Only a new timeline (or rig) makes a new controller.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [timeline, bones],
  );

  // Narration audio element, owned by this hook.
  useEffect(() => {
    if (!audio || typeof Audio === "undefined") return;
    const el = new Audio();
    el.preload = "auto";
    controller.attachAudio(el);
    return () => controller.attachAudio(null);
  }, [controller, audio]);

  // The animation loop.
  useEffect(() => {
    let raf = 0;
    const loopFrame = (now: number) => {
      controller.tick(now);
      raf = requestAnimationFrame(loopFrame);
    };
    raf = requestAnimationFrame(loopFrame);
    return () => {
      cancelAnimationFrame(raf);
      controller.pause();
    };
  }, [controller]);

  useEffect(() => controller.setLoop(loop), [controller, loop]);
  useEffect(() => controller.setRate(rate), [controller, rate]);
  useEffect(() => controller.setNarrator(narrator), [controller, narrator]);

  useEffect(() => {
    if (freezeAt !== null) {
      controller.pause();
      controller.seek(freezeAt);
    } else if (autoplay) {
      controller.play();
    }
  }, [controller, freezeAt, autoplay]);

  const status = useSyncExternalStore(controller.subscribe, controller.getStatus, controller.getStatus);
  return { controller, frame: controller.frame, status };
}

/**
 * Re-renders the calling component every time the controller samples a new
 * frame, and returns that frame. For debug views and other 2D readouts.
 */
export function usePlayerFrame(controller: PlaybackController): PlaybackFrame {
  const subscribe = useCallback((onChange: () => void) => controller.onFrame(onChange), [controller]);
  const version = useCallback(() => controller.frameVersion, [controller]);
  useSyncExternalStore(subscribe, version, version);
  return controller.frame;
}
