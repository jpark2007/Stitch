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
 *   3. Nothing: the host's fallback (the empty stage).
 * Paper, puppet kit and cutouts are built in idle time before mounting.
 * Media references resolve through the API (samples have none, so their
 * narration plays as silent timing).
 *
 * Ready: once every piece of art is in and a few frames are drawn, the page
 * sets `window.__STITCH_READY__ = true` and `<html data-stage-ready="1">`.
 *
 * Keys: Space play / pause, R restart, ← → step half a second.
 */

import { motion } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, bareMediaId } from "@/lib/api";
import type { MemoryResponse } from "@/lib/types";
import { useTimelinePlayer } from "@/components/stage/playback";
import {
  CaptionStrip,
  PlayerControls,
  SAMPLE_SCENES,
  SoundHint,
  TellerTabs,
  TimelineStage,
  loadSampleScene,
  preloadScene,
  sceneFromMemory,
  sceneTellers,
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
  /** Frozen scene time (?t), or null to play. */
  time: number | null;
  /** Starting teller (?narrator), or null for the default. */
  narrator: string | null;
  /** Start playing on load (?autoplay, default on). */
  autoplay: boolean;
  /** Show the transport and tabs (?ui=0 hides them for frame captures). */
  ui: boolean;
  /** Shown when there is no scene for this id. */
  fallback?: ReactNode;
}

type Source = { kind: "scene"; scene: SceneData; checker: boolean } | { kind: "missing" } | { kind: "error" };

function markReady() {
  document.documentElement.dataset.stageReady = "1";
  window.__STITCH_READY__ = true;
}

export function TimelineView({ memoryId, fallback = null, ...rest }: TimelineViewProps) {
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
    const injected = window.__STITCH_RENDER__;
    const fromChecker = injected ? sceneFromMemory(injected) : null;
    if (fromChecker) ready(fromChecker, true).catch(() => done({ kind: "error" }));
    else if (Object.hasOwn(SAMPLE_SCENES, memoryId))
      loadSampleScene(memoryId, ctl.signal)
        .then((s) => ready(s, false))
        .catch(() => done({ kind: "error" }));
    else done({ kind: "missing" });
    return () => ctl.abort();
  }, [memoryId]);

  if (!source) return null;
  if (source.kind === "missing") return <>{fallback}</>;
  if (source.kind === "error") {
    return (
      <div className="grid h-full place-items-center font-hand text-2xl text-[#e9dcbd]/80">
        This moment could not be opened.
      </div>
    );
  }
  const { scene, checker } = source;
  // The checker's frames: the bare stage, no chrome, no captions over it.
  return checker ? <Player scene={scene} {...rest} ui={false} captions={false} /> : <Player scene={scene} {...rest} />;
}

const IDLE_MS = 2600;

function Player({
  scene,
  time,
  narrator,
  autoplay,
  ui,
  captions = true,
}: Omit<TimelineViewProps, "memoryId" | "fallback"> & { scene: SceneData; captions?: boolean }) {
  const familyId = scene.familyId;
  const resolveAudio = useCallback(
    (mediaId: string) => (familyId ? api.mediaUrl(familyId, bareMediaId(mediaId)) : null),
    [familyId],
  );
  const tellers = useMemo(() => sceneTellers(scene), [scene]);
  const start = narrator && tellers.some((t) => t.id === narrator) ? narrator : null;
  const { controller } = useTimelinePlayer(scene.timeline, {
    narrator: start,
    autoplay: time === null && autoplay,
    freezeAt: time,
    // The view owns the narration element, so it can mute it.
    audio: false,
    resolveAudio,
  });

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

  return (
    <motion.div
      className="absolute inset-0"
      initial={{ opacity: time === null ? 0 : 1 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
    >
      {/* Lifted so the caption strip sits under the action, not over it. */}
      <TimelineStage
        className="absolute inset-0"
        controller={controller}
        scene={scene}
        onReady={markReady}
        lift={captions ? 0.065 : 0}
      />
      {/* Soft lens vignette: the lamp's pool, falling off into the room. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{ background: "radial-gradient(120% 95% at 50% 42%, transparent 56%, rgb(20 12 6 / 0.55))" }}
      />

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

      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col items-center gap-4 pb-[clamp(14px,3.4vh,34px)]">
        {ui && <SoundHint controller={controller} className="pointer-events-auto" />}
        {captions && <CaptionStrip controller={controller} tellers={tellers} className="w-full" />}
        {ui && (
          <div
            className={`pointer-events-auto transition-all duration-500 [transition-timing-function:var(--ease-paper)] ${
              idle ? "translate-y-3 opacity-0" : "translate-y-0 opacity-100"
            }`}
          >
            <PlayerControls controller={controller} muted={muted} onMutedChange={setMuted} />
          </div>
        )}
      </div>
    </motion.div>
  );
}
