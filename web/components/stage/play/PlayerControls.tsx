"use client";

/**
 * Transport for a timeline player, in the book's paper language:
 *
 *   <TellerTabs>      a paper tab per teller in Timeline.narrations; choosing
 *                     one replays the same scene with that person's voice and
 *                     captions (DESIGN.md "Moments and tellings")
 *   <PlayerControls>  play / pause, restart, a stitched-thread scrubber with
 *                     the time, and mute when there is narration audio. It
 *                     fills the width its host gives it (the thread takes
 *                     what the buttons leave), so a host sets the gutter and
 *                     a max width; phones show only the elapsed time.
 *
 * The scrubber and the clock follow the player through controller.onFrame
 * and write straight to the DOM, so playback never re-renders React; the
 * buttons re-render only on discrete status changes.
 */

import { useEffect, useRef, useSyncExternalStore, type PointerEvent as ReactPointerEvent } from "react";
import type { PlaybackController } from "../playback";
import type { Teller } from "./CaptionStrip";

function usePlayerStatus(controller: PlaybackController) {
  return useSyncExternalStore(controller.subscribe, controller.getStatus, controller.getStatus);
}

const fmt = (t: number) => {
  const s = Math.max(0, t);
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}:${r < 10 ? "0" : ""}${r.toFixed(1)}`;
};

/* ------------------------------------------------------------------------ */
/* Teller tabs                                                               */
/* ------------------------------------------------------------------------ */

export interface TellerTabsProps {
  controller: PlaybackController;
  tellers: readonly Teller[];
  className?: string;
  /** Called after the teller changes (e.g. to keep ?narrator in the URL). */
  onChange?: (id: string) => void;
}

export function TellerTabs({ controller, tellers, className = "", onChange }: TellerTabsProps) {
  const status = usePlayerStatus(controller);
  if (tellers.length < 2) return null;
  return (
    <nav className={`flex items-end gap-1.5 ${className}`} aria-label="Tellings">
      {tellers.map((t, i) => {
        const active = t.id === status.narrator;
        return (
          <button
            key={t.id}
            type="button"
            aria-pressed={active}
            onClick={() => {
              controller.setNarrator(t.id);
              onChange?.(t.id);
            }}
            className={[
              "group relative rounded-t-[10px] px-3.5 font-serif text-[0.9rem] leading-none italic sm:px-4 sm:text-[0.95rem]",
              "transition-all duration-300 [transition-timing-function:var(--ease-paper)]",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#f3d488]",
              active
                ? "bg-[#f7f0e1] pb-3 pt-2.5 text-ink shadow-[0_-2px_10px_rgba(20,10,4,0.35)]"
                : "bg-[#d9c6a4]/80 pb-2 pt-2 text-ink-soft hover:bg-[#e6d6b8] hover:pb-2.5",
            ].join(" ")}
            style={{ rotate: `${(i % 2 ? 1 : -1) * (active ? 0.4 : 1.1)}deg` }}
          >
            {/* DESIGN.md's wording, the same on every tab; the thread says which is playing. */}
            Hear <span className="not-italic">{t.name}</span>&rsquo;s version
            {/* The teller's thread, stitched along the tab's hem. */}
            <span
              aria-hidden
              className="absolute inset-x-3 bottom-1 h-[2px] rounded-full transition-opacity duration-300"
              style={{
                background: `repeating-linear-gradient(90deg, ${t.color} 0 6px, transparent 6px 10px)`,
                opacity: active ? 0.95 : 0.4,
              }}
            />
          </button>
        );
      })}
    </nav>
  );
}

/* ------------------------------------------------------------------------ */
/* Sound hint                                                                */
/* ------------------------------------------------------------------------ */

/**
 * Browsers will not start narration audio without a click. When the scene
 * autoplayed silently for that reason, offer the click: one small paper note
 * that resumes the voice in step with the scene.
 */
export function SoundHint({ controller, className = "" }: { controller: PlaybackController; className?: string }) {
  const status = usePlayerStatus(controller);
  if (status.audio !== "blocked") return null;
  return (
    <button
      type="button"
      onClick={() => controller.play()}
      className={`rounded-full bg-[#f7f0e1]/95 px-4 py-1.5 font-hand text-lg text-ink shadow-[0_6px_18px_-6px_rgba(20,10,4,0.6)] transition-transform hover:-translate-y-px ${className}`}
    >
      Tap to hear the story
    </button>
  );
}

/* ------------------------------------------------------------------------ */
/* Transport                                                                 */
/* ------------------------------------------------------------------------ */

export interface PlayerControlsProps {
  controller: PlaybackController;
  className?: string;
  /** Narration muted. The mute button shows only when some telling has audio. */
  muted?: boolean;
  onMutedChange?: (muted: boolean) => void;
}

function Icon({ name }: { name: "play" | "pause" | "restart" | "sound" | "muted" }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  switch (name) {
    case "play":
      return (
        <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden>
          <path d="M8.2 5.6c0-.8.9-1.3 1.6-.9l8.6 5.6c.6.4.6 1.3 0 1.7l-8.6 5.6c-.7.4-1.6 0-1.6-.9z" fill="currentColor" />
        </svg>
      );
    case "pause":
      return (
        <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden>
          <rect x="6.5" y="5" width="3.8" height="14" rx="1.3" fill="currentColor" />
          <rect x="13.7" y="5" width="3.8" height="14" rx="1.3" fill="currentColor" />
        </svg>
      );
    case "restart":
      return (
        <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" aria-hidden {...common}>
          <path d="M4.5 12a7.5 7.5 0 1 0 2.3-5.4" />
          <path d="M4.2 4.5v3.6h3.6" />
        </svg>
      );
    case "sound":
      return (
        <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" aria-hidden {...common}>
          <path d="M4.5 9.5h3l4-3.5v12l-4-3.5h-3z" />
          <path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" />
        </svg>
      );
    case "muted":
      return (
        <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" aria-hidden {...common}>
          <path d="M4.5 9.5h3l4-3.5v12l-4-3.5h-3z" />
          <path d="M15.5 9.5l5 5M20.5 9.5l-5 5" />
        </svg>
      );
  }
}

const roundBtn =
  "grid shrink-0 place-items-center rounded-full text-ink transition-transform duration-200 [transition-timing-function:var(--ease-paper)] hover:-translate-y-px active:translate-y-px focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#f3d488]";

export function PlayerControls({ controller, className = "", muted = false, onMutedChange }: PlayerControlsProps) {
  const status = usePlayerStatus(controller);
  const fill = useRef<HTMLDivElement>(null);
  const knob = useRef<HTMLDivElement>(null);
  const clock = useRef<HTMLSpanElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; resume: boolean } | null>(null);
  const duration = controller.duration;
  const hasAudio = controller.compiled.narrators.some((n) => n.audio);

  // Follow the player without re-rendering: write the scrubber and clock directly.
  useEffect(() => {
    let lastText = "";
    let lastAria = "";
    const draw = () => {
      const k = duration > 0 ? Math.min(1, Math.max(0, controller.time / duration)) : 0;
      if (fill.current) fill.current.style.transform = `scaleX(${k})`;
      if (knob.current) knob.current.style.left = `${k * 100}%`;
      const text = fmt(controller.time);
      if (clock.current && text !== lastText) {
        clock.current.textContent = text;
        lastText = text;
      }
      if (track.current && text !== lastAria) {
        // Same text React renders (a number to one decimal), so the two never disagree.
        track.current.setAttribute("aria-valuenow", String(Number(controller.time.toFixed(1))));
        track.current.setAttribute("aria-valuetext", fmt(controller.time));
        lastAria = text;
      }
    };
    draw();
    return controller.onFrame(draw);
  }, [controller, duration]);

  const seekFrom = (e: ReactPointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const k = Math.min(1, Math.max(0, (e.clientX - r.left) / Math.max(1, r.width)));
    controller.seek(k * duration);
  };

  return (
    <div
      className={`flex min-w-0 items-center gap-2 rounded-full bg-[#f7f0e1]/95 py-2 pl-2 pr-3.5 sm:gap-3 sm:pr-5 shadow-[0_8px_24px_-6px_rgba(20,10,4,0.55),0_1px_0_rgba(255,255,255,0.6)_inset] ${className}`}
    >
      <button
        type="button"
        aria-label={status.playing ? "Pause" : "Play"}
        onClick={() => controller.toggle()}
        className={`${roundBtn} h-10 w-10 bg-[#b9573d] text-[#fbf8f1] shadow-[0_2px_0_#8f3f2a]`}
      >
        <Icon name={status.playing ? "pause" : "play"} />
      </button>
      <button
        type="button"
        aria-label="Play from the start"
        onClick={() => {
          controller.seek(0);
          controller.play();
        }}
        className={`${roundBtn} h-8 w-8 text-ink-soft hover:text-ink`}
      >
        <Icon name="restart" />
      </button>
      <div
        ref={track}
        role="slider"
        tabIndex={0}
        aria-label="Scene time"
        aria-valuemin={0}
        aria-valuemax={Number(duration.toFixed(1))}
        // Current at every render; between renders the frame listener above keeps it.
        aria-valuenow={Number(controller.time.toFixed(1))}
        aria-valuetext={fmt(controller.time)}
        className="relative h-8 min-w-0 flex-1 cursor-pointer touch-none select-none"
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          const resume = controller.getStatus().playing;
          if (resume) controller.pause();
          drag.current = { id: e.pointerId, resume };
          seekFrom(e);
        }}
        onPointerMove={(e) => {
          if (drag.current?.id === e.pointerId) seekFrom(e);
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          if (!d || d.id !== e.pointerId) return;
          drag.current = null;
          if (d.resume) controller.play();
        }}
        onPointerCancel={() => {
          const d = drag.current;
          drag.current = null;
          if (d?.resume) controller.play();
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
            e.preventDefault();
            controller.seek(controller.time + (e.key === "ArrowLeft" ? -0.5 : 0.5));
          }
        }}
      >
        {/* Stitched thread: dashes on the paper, and the sewn part in terracotta. */}
        <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 border-t-2 border-dashed border-[#c9b89a]" />
        <div
          ref={fill}
          className="absolute inset-x-0 top-1/2 h-[3px] origin-left -translate-y-1/2 rounded-full bg-[#b9573d]"
          style={{ transform: "scaleX(0)" }}
        />
        <div
          ref={knob}
          className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[#f7f0e1] bg-[#8f3f2a] shadow-[0_1px_3px_rgba(40,20,8,0.45)]"
          style={{ left: "0%" }}
        />
      </div>
      {/* Elapsed always; the total from sm up (phones keep the room for the thread). */}
      <span className="shrink-0 text-right font-sans text-xs tabular-nums text-ink-soft">
        <span ref={clock} className="inline-block min-w-[3.4em]" />
        <span className="hidden sm:inline"> / {fmt(duration)}</span>
      </span>
      {hasAudio && (
        <button
          type="button"
          aria-label={muted ? "Unmute narration" : "Mute narration"}
          aria-pressed={muted}
          onClick={() => onMutedChange?.(!muted)}
          className={`${roundBtn} h-8 w-8 text-ink-soft hover:text-ink`}
        >
          <Icon name={muted ? "muted" : "sound"} />
        </button>
      )}
    </div>
  );
}
