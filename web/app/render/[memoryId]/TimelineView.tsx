"use client";

/**
 * /render/<memoryId>: a compiled timeline played end to end as a pop-up
 * scene, with narration captions, a tab per teller and the transport.
 *
 * Where the scene comes from, first match wins:
 *   1. `window.__STITCH_RENDER__`: a MemoryResponse the checker injected
 *      before any script ran (api/agent/tools/frames.py; the memory may be an
 *      unsaved draft, so nothing is fetched). Shown with no chrome and no
 *      captions, paused at ?t, filling the viewport.
 *   2. A local sample (web/public/samples/<id>.{screenplay,timeline,memory}.json).
 *   3. With ?family=<familyId>: the saved moment from the API (getMemory),
 *      its compiled timeline with its sets, props and people; a moment with
 *      no compiled timeline says so.
 *   4. Nothing: the host's fallback (the empty stage).
 * Paper, puppet kit and cutouts are built in idle time before mounting.
 * Media references resolve through the API (samples have none, so their
 * narration plays as silent timing).
 *
 * Ready: once every piece of art is in and a few frames are drawn, the page
 * sets `window.__STITCH_READY__ = true` and `<html data-stage-ready="1">`,
 * fades the view in and only then autoplays, so the set's first rise is seen.
 *
 * Keys: Space play / pause, R restart, ← → step half a second.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { api, bareMediaId } from "@/lib/api";
import type { MemoryResponse } from "@/lib/types";
import { useTimelinePlayer, type PlaybackController } from "@/components/stage/playback";
import {
  CaptionStrip,
  FpsProbe,
  PlayerControls,
  SAMPLE_SCENES,
  SoundHint,
  TellerTabs,
  TimelineStage,
  loadSampleScene,
  preloadScene,
  sceneFromMemory,
  sceneTellers,
  shotPageText,
  type SceneData,
} from "@/components/stage/play";

declare global {
  interface Window {
    /** The scene to draw, injected by the checker's frame renderer. */
    __STITCH_RENDER__?: MemoryResponse;
    /** Set once the frame is fully drawn. */
    __STITCH_READY__?: boolean;
  }
}

export interface TimelineViewProps {
  memoryId: string;
  /** Family of a saved moment to load from the API (?family), or null. */
  family?: string | null;
  /** Frozen scene time (?t), or null to play. */
  time: number | null;
  /** Starting teller (?narrator), or null for the default. */
  narrator: string | null;
  /** Start playing on load (?autoplay, default on). */
  autoplay: boolean;
  /** Show the transport and tabs (?ui=0 hides them for frame captures). */
  ui: boolean;
  /** Print the measured frame rate in a corner (?fps). It is always in `<html data-fps>`. */
  fps?: boolean;
  /** Shown when there is no scene for this id. */
  fallback?: ReactNode;
}

type Source =
  | { kind: "scene"; scene: SceneData; checker: boolean }
  | { kind: "missing" }
  | { kind: "error"; detail: string | null };

/** Said when a saved moment has not been compiled into a scene yet. */
const NOT_COMPILED = "This moment has no compiled scene yet.";

function markReady() {
  document.documentElement.dataset.stageReady = "1";
  window.__STITCH_READY__ = true;
}

/** A saved moment's scene from the API (?family), or throws NOT_COMPILED. */
async function loadSavedScene(familyId: string, memoryId: string): Promise<SceneData> {
  const scene = sceneFromMemory(await api.getMemory(familyId, memoryId));
  if (!scene) throw new Error(NOT_COMPILED);
  return scene;
}

export function TimelineView({ memoryId, family = null, fallback = null, ...rest }: TimelineViewProps) {
  const [source, setSource] = useState<Source | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    const done = (s: Source) => {
      if (!ctl.signal.aborted) setSource(s);
    };
    const ready = async (scene: SceneData, checker: boolean) => {
      await preloadScene(scene);
      done({ kind: "scene", scene, checker });
    };
    const failed = (e: unknown) => {
      if (ctl.signal.aborted) return;
      console.warn(`[render] ${memoryId}:`, e);
      done({ kind: "error", detail: e instanceof Error && e.message === NOT_COMPILED ? NOT_COMPILED : null });
    };
    const injected = window.__STITCH_RENDER__;
    const fromChecker = injected ? sceneFromMemory(injected) : null;
    if (fromChecker) ready(fromChecker, true).catch(failed);
    else if (Object.hasOwn(SAMPLE_SCENES, memoryId))
      loadSampleScene(memoryId, ctl.signal)
        .then((s) => ready(s, false))
        .catch(failed);
    else if (family && memoryId !== "demo")
      loadSavedScene(family, memoryId)
        .then((s) => ready(s, false))
        .catch(failed);
    else done({ kind: "missing" });
    return () => ctl.abort();
  }, [memoryId, family]);

  if (!source) return null;
  if (source.kind === "missing") return <>{fallback}</>;
  if (source.kind === "error") {
    return (
      <div className="grid h-full place-items-center p-8 text-center">
        <p className="font-hand text-2xl text-[#e9dcbd]/80">
          This moment could not be opened.
          {source.detail && (
            <span className="mt-2 block font-serif text-base italic text-[#e9dcbd]/55">{source.detail}</span>
          )}
        </p>
      </div>
    );
  }
  const { scene, checker } = source;
  // The checker's frames: the bare stage, no chrome, no captions over it.
  return checker ? <Player scene={scene} {...rest} ui={false} captions={false} /> : <Player scene={scene} {...rest} />;
}

const IDLE_MS = 2600;
const FPS_READOUT = "stage-fps";

/**
 * The shot's storybook line under the title while the set rises, before the
 * teller's first caption; it fades as the narration takes over (and comes
 * back when a new telling replays the scene).
 */
function ShotLine({ controller, scene }: { controller: PlaybackController; scene: SceneData }) {
  const status = useSyncExternalStore(controller.subscribe, controller.getStatus, controller.getStatus);
  const text = status.captionIndex < 0 ? shotPageText(scene, status.shotIndex) : null;
  return (
    <AnimatePresence>
      {text && (
        <motion.p
          key={`${status.shotIndex}:${status.narrator}`}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, transition: { duration: 0.45 } }}
          transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
          // Phones put the teller tabs under the title: no room for it there.
          className="mt-1.5 hidden max-w-[min(30rem,80vw)] font-serif md:block text-[0.98rem] italic leading-snug text-[#f4ecdd]/80 [text-shadow:0_1px_10px_rgb(20_10_4/0.7)]"
        >
          {text}
        </motion.p>
      )}
    </AnimatePresence>
  );
}

function Player({
  scene,
  time,
  narrator,
  autoplay,
  ui,
  fps = false,
  captions = true,
}: Omit<TimelineViewProps, "memoryId" | "family" | "fallback"> & { scene: SceneData; captions?: boolean }) {
  // Stable, so the memoised stage never re-renders for it.
  const overlay = useMemo(() => <FpsProbe target={fps ? FPS_READOUT : undefined} />, [fps]);
  const familyId = scene.familyId;
  const resolveAudio = useCallback(
    (mediaId: string) => (familyId ? api.mediaUrl(familyId, bareMediaId(mediaId)) : null),
    [familyId],
  );
  const tellers = useMemo(() => sceneTellers(scene), [scene]);
  const start = narrator && tellers.some((t) => t.id === narrator) ? narrator : null;
  const { controller } = useTimelinePlayer(scene.timeline, {
    narrator: start,
    // Autoplay waits for the stage to be drawn (onStageReady below), so the
    // set's first rise is seen rather than spent loading.
    autoplay: false,
    freezeAt: time,
    // The view owns the narration element, so it can mute it.
    audio: false,
    resolveAudio,
  });

  // Drawn: tell capture scripts, fade the view in, and start the telling.
  const [ready, setReady] = useState(false);
  const onStageReady = useCallback(() => {
    markReady();
    setReady(true);
    if (autoplay && time === null) controller.play();
  }, [autoplay, time, controller]);

  const [muted, setMuted] = useState(false);
  const audioEl = useRef<HTMLAudioElement | null>(null);
  useEffect(() => {
    if (typeof Audio === "undefined") return;
    const el = new Audio();
    el.preload = "auto";
    audioEl.current = el;
    controller.attachAudio(el);
    return () => {
      controller.attachAudio(null);
      audioEl.current = null;
    };
  }, [controller]);
  useEffect(() => {
    if (audioEl.current) audioEl.current.muted = muted;
  }, [muted]);

  // Keys: space, R, arrows (not while typing or on a focused control).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(el.tagName))) return;
      if (e.key === " ") {
        e.preventDefault();
        controller.toggle();
      } else if (e.key === "r" || e.key === "R") {
        controller.seek(0);
        controller.play();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        controller.seek(controller.time + (e.key === "ArrowLeft" ? -0.5 : 0.5));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [controller]);

  // The chrome steps back while the scene plays and nobody is touching it.
  const [idle, setIdle] = useState(false);
  useEffect(() => {
    let timer = 0;
    const wake = () => {
      setIdle(false);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setIdle(controller.getStatus().playing), IDLE_MS);
    };
    const onStatus = () => {
      if (!controller.getStatus().playing) wake();
    };
    wake();
    window.addEventListener("pointermove", wake);
    window.addEventListener("pointerdown", wake);
    window.addEventListener("keydown", wake);
    const off = controller.subscribe(onStatus);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("pointermove", wake);
      window.removeEventListener("pointerdown", wake);
      window.removeEventListener("keydown", wake);
      off();
    };
  }, [controller]);

  const keepNarrator = useCallback((id: string) => {
    const url = new URL(window.location.href);
    url.searchParams.set("narrator", id);
    window.history.replaceState(null, "", url);
  }, []);

  // The caption band: a real strip under the picture, never over it. The
  // canvas stops at its top edge, so the camera tracks' framing (fov, aim)
  // plays exactly, in the room left above. Fixed height, so captions and
  // controls coming and going never resize the canvas mid-shot.
  const band = captions || ui;

  return (
    <motion.div
      className={`absolute inset-0 ${band ? "[--band:212px] sm:[--band:clamp(168px,21vh,196px)]" : "[--band:0px]"}`}
      initial={{ opacity: time === null ? 0 : 1 }}
      // Fades in once drawn; a frozen frame (?t, the checker) shows at once.
      animate={{ opacity: ready || time !== null ? 1 : 0 }}
      transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
    >
      <div className="absolute inset-x-0 top-0 bottom-[var(--band)]">
        <TimelineStage
          className="absolute inset-0"
          controller={controller}
          scene={scene}
          onReady={onStageReady}
          overlay={overlay}
        />
        {/* Soft lens vignette: the lamp's pool, falling off into the room, and
            a faint shade at the head of the frame where the title sits when a
            close shot fills the screen. Over the band, the picture's lower
            edge settles into the room rather than ending on a cut. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            background: [
              "radial-gradient(120% 95% at 50% 42%, transparent 56%, rgb(20 12 6 / 0.55))",
              ui ? "linear-gradient(180deg, rgb(20 12 6 / 0.34) 0%, transparent 15%)" : null,
              band ? "linear-gradient(0deg, rgb(36 24 15 / 0.9) 0, rgb(36 24 15 / 0) 22px)" : null,
            ]
              .filter(Boolean)
              .join(", "),
          }}
        />
      </div>

      {ui && (
        <header
          className={`pointer-events-none absolute left-[clamp(16px,4vw,48px)] top-[clamp(14px,3.2vh,32px)] transition-opacity duration-700 ${idle ? "opacity-60" : "opacity-100"}`}
        >
          {scene.year !== null && (
            <p className="font-sans text-[11px] font-semibold uppercase tracking-[0.28em] text-[#e9dcbd]/70">{scene.year}</p>
          )}
          <h1 className="font-display text-[clamp(1.35rem,2.1vw,1.9rem)] leading-tight text-[#f7f0e1] [text-shadow:0_2px_12px_rgb(20_10_4/0.6)]">
            {scene.title}
          </h1>
          {captions && <ShotLine controller={controller} scene={scene} />}
        </header>
      )}

      {ui && (
        <TellerTabs
          controller={controller}
          tellers={tellers}
          onChange={keepNarrator}
          // Phones: under the title. Wider: the top-right corner, like bookmark tabs.
          className="absolute left-4 top-[88px] md:left-auto md:right-[clamp(16px,4vw,48px)] md:top-[clamp(12px,3vh,30px)]"
        />
      )}

      {ui && (
        <div className="pointer-events-none absolute inset-x-0 bottom-[calc(var(--band)+14px)] flex justify-center">
          <SoundHint controller={controller} className="pointer-events-auto" />
        </div>
      )}

      {band && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex h-[var(--band)] flex-col items-center justify-end pb-[clamp(10px,2vh,18px)]">
          {captions && (
            <div className="flex w-full flex-1 flex-col justify-center">
              <CaptionStrip controller={controller} tellers={tellers} className="w-full" />
            </div>
          )}
          {ui && (
            // Collapses while the scene plays untouched, so the caption
            // settles into the middle of the band instead of over an empty slot.
            <div
              className={`grid w-full transition-[grid-template-rows,opacity] duration-500 [transition-timing-function:var(--ease-paper)] ${
                idle ? "grid-rows-[0fr] opacity-0" : "grid-rows-[1fr] opacity-100"
              }`}
            >
              <div className="min-h-0">
                <div className="flex justify-center px-4 pt-2.5">
                  <PlayerControls
                    controller={controller}
                    muted={muted}
                    onMutedChange={setMuted}
                    className={`w-full max-w-[640px] ${idle ? "pointer-events-none" : "pointer-events-auto"}`}
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      )}
      {fps && (
        <span
          id={FPS_READOUT}
          className="pointer-events-none absolute bottom-3 right-4 font-sans text-xs tabular-nums text-[#f4ecdd]/60"
        />
      )}
    </motion.div>
  );
}
