"use client";

/**
 * /render/<sample>: a compiled timeline played end to end as a pop-up scene,
 * with narration captions, a tab per teller and the transport.
 *
 * Loads the sample's three JSON files from web/public/samples (screenplay,
 * timeline and the MemoryResponse with its sets, props and people), builds
 * the paper and cutouts in idle time, then mounts the stage. Media
 * references resolve through the API for real memories; the samples have
 * none, so their narration plays as silent timing.
 *
 * Keys: Space play / pause, R restart, ← → step half a second.
 */

import { useFrame } from "@react-three/fiber";
import { motion } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, bareMediaId } from "@/lib/api";
import { useTimelinePlayer } from "@/components/stage/playback";
import {
  CaptionStrip,
  PlayerControls,
  TellerTabs,
  TimelineStage,
  loadSampleScene,
  preloadScene,
  sceneTellers,
  type SceneData,
} from "@/components/stage/play";

export interface TimelineViewProps {
  /** Sample name under web/public/samples ("hymn_books") */
  sample: string;
  /** Frozen scene time (?t), or null to play. */
  time: number | null;
  /** Starting teller (?narrator), or null for the default. */
  narrator: string | null;
  /** Start playing on load (?autoplay, default on). */
  autoplay: boolean;
  /** Show the transport and tabs (?ui=0 hides them for frame captures). */
  ui: boolean;
}

/** Marks the document once a few frames have actually been drawn (for capture scripts). */
function ReadyProbe() {
  const frames = useRef(0);
  useFrame(() => {
    frames.current += 1;
    if (frames.current === 3) document.documentElement.dataset.stageReady = "1";
  });
  return null;
}

export function TimelineView({ sample, ...rest }: TimelineViewProps) {
  const [scene, setScene] = useState<SceneData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    loadSampleScene(sample, ctl.signal)
      .then(async (s) => {
        await preloadScene(s);
        if (!ctl.signal.aborted) setScene(s);
      })
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) setError(e instanceof Error ? e.message : String(e));
      });
    return () => ctl.abort();
  }, [sample]);

  if (error) {
    return (
      <div className="grid h-full place-items-center font-hand text-2xl text-[#e9dcbd]/80">
        This moment could not be opened.
      </div>
    );
  }
  if (!scene) return null;
  return <Player scene={scene} {...rest} />;
}

const IDLE_MS = 2600;

function Player({ scene, time, narrator, autoplay, ui }: Omit<TimelineViewProps, "sample"> & { scene: SceneData }) {
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

  const overlay = useMemo(() => <ReadyProbe />, []);

  return (
    <motion.div
      className="absolute inset-0"
      initial={{ opacity: time === null ? 0 : 1 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
    >
      <TimelineStage className="absolute inset-0" controller={controller} scene={scene} overlay={overlay} />
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
          className="absolute right-[clamp(16px,4vw,48px)] top-[clamp(12px,3vh,30px)]"
        />
      )}

      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col items-center gap-4 pb-[clamp(14px,3.4vh,34px)]">
        <CaptionStrip controller={controller} tellers={tellers} className="w-full" />
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
