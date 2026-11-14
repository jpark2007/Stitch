"use client";

/**
 * <CaptionStrip>: the current teller's caption, handwritten on a torn strip
 * of paper taped over the scene, like a note tucked into a storybook.
 *
 * It follows the player's discrete status (caption index, word index,
 * narrator), so it re-renders a few times a second at most, never per frame.
 * Words already spoken are in full ink; the rest wait in pencil, so the line
 * reads along with the narration (word timings from the telling, silent
 * timing when there is no audio). A caption shows until the next one or the
 * end of its shot (CONTRACTS.md Clarifications 6); switching teller swaps
 * the words over the same scene.
 *
 * DOM only: mount it over the canvas (the render route) or anywhere a host
 * wants the words (the book can print them on its story page instead).
 */

import { AnimatePresence, motion } from "framer-motion";
import { useId, useMemo, useSyncExternalStore } from "react";
import { Tape } from "@/components/paper/Tape";
import type { PlaybackController } from "../playback";

export interface Teller {
  id: string;
  name: string;
  /** Thread colour (character.color) */
  color: string;
}

export interface CaptionStripProps {
  controller: PlaybackController;
  /** Who is who, for the small "David's telling" line. */
  tellers?: readonly Teller[];
  className?: string;
}

/** A deterministic torn-paper outline in a 1000 × 100 box (stretched to fit). */
function tornPath(seed: number): string {
  let s = (seed * 2654435761) % 4294967296;
  const rnd = () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296;
  };
  const top: string[] = [];
  const bottom: string[] = [];
  const steps = 46;
  for (let i = 0; i <= steps; i++) {
    const x = 8 + (984 * i) / steps;
    top.push(`${x.toFixed(1)},${(3 + rnd() * 6 + (i % 3 === 0 ? rnd() * 3 : 0)).toFixed(1)}`);
    bottom.push(`${x.toFixed(1)},${(97 - rnd() * 6 - (i % 4 === 0 ? rnd() * 3 : 0)).toFixed(1)}`);
  }
  // Ends: a few ragged teeth each, as if torn off the roll.
  const left = [`${(2 + rnd() * 6).toFixed(1)},70`, `${(4 + rnd() * 6).toFixed(1)},40`];
  const right = [`${(992 + rnd() * 6).toFixed(1)},30`, `${(990 + rnd() * 6).toFixed(1)},62`];
  return `M${top.join(" L")} L${right.join(" L")} L${bottom.reverse().join(" L")} L${left.join(" L")} Z`;
}

/** Caption tokens with how many are spoken, proportional when words and tokens differ. */
function spokenTokens(text: string, words: number, wordIndex: number): { tokens: string[]; spoken: number } {
  const tokens = text.split(/\s+/).filter(Boolean);
  if (words <= 0) return { tokens, spoken: tokens.length };
  const k = Math.max(0, Math.min(1, (wordIndex + 1) / words));
  return { tokens, spoken: words === tokens.length ? wordIndex + 1 : Math.round(k * tokens.length) };
}

export function CaptionStrip({ controller, tellers = [], className = "" }: CaptionStripProps) {
  const status = useSyncExternalStore(controller.subscribe, controller.getStatus, controller.getStatus);
  const track = controller.compiled.narrator(status.narrator);
  const index = status.captionIndex;
  const caption = track && index >= 0 ? track.captions[index] : null;
  const line = track && index >= 0 ? track.captionLines[index] : null;
  const teller = tellers.find((t) => t.id === status.narrator) ?? null;
  const key = `${status.narrator}:${index}`;
  const seed = index + 1 + (status.narrator?.length ?? 0) * 7;
  const torn = useMemo(() => tornPath(seed), [seed]);
  const grainId = `caption-grain-${useId().replace(/:/g, "")}`;
  const { tokens, spoken } = caption
    ? spokenTokens(caption.text, line?.words?.length ?? 0, status.wordIndex)
    : { tokens: [], spoken: 0 };

  return (
    <div className={`pointer-events-none flex justify-center ${className}`} aria-live="polite">
      <AnimatePresence mode="wait">
        {caption && (
          <motion.figure
            key={key}
            className="relative mx-4 max-w-[min(760px,92vw)] px-10 pb-5 pt-4 sm:px-14"
            initial={{ opacity: 0, y: 10, rotate: -1.6 }}
            animate={{ opacity: 1, y: 0, rotate: index % 2 ? 0.5 : -0.7 }}
            exit={{ opacity: 0, y: -6, rotate: index % 2 ? 1 : -1.2, transition: { duration: 0.2, ease: [0.4, 0, 1, 1] } }}
            transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
          >
            <svg
              aria-hidden
              className="absolute inset-0 h-full w-full drop-shadow-[0_6px_14px_rgba(40,22,10,0.35)]"
              viewBox="0 0 1000 100"
              preserveAspectRatio="none"
            >
              <defs>
                <filter id={grainId} x="0" y="0" width="100%" height="100%">
                  <feTurbulence type="fractalNoise" baseFrequency="0.9 0.6" numOctaves="2" seed={seed} result="n" />
                  <feColorMatrix
                    in="n"
                    type="matrix"
                    values="0 0 0 0 0.42  0 0 0 0 0.30  0 0 0 0 0.20  0 0 0 0.07 0"
                    result="g"
                  />
                  <feComposite in="g" in2="SourceGraphic" operator="in" result="gi" />
                  <feMerge>
                    <feMergeNode in="SourceGraphic" />
                    <feMergeNode in="gi" />
                  </feMerge>
                </filter>
              </defs>
              <path d={torn} fill="#f7f0e1" filter={`url(#${grainId})`} />
              {/* The torn fibres: a paler rim just inside the edge. */}
              <path d={torn} fill="none" stroke="#fffaf0" strokeWidth="2.2" vectorEffect="non-scaling-stroke" opacity="0.9" />
            </svg>
            <Tape
              className="absolute -top-3 left-1/2 -translate-x-1/2"
              width={92}
              height={22}
              rotate={index % 2 ? 3 : -4}
              seed={seed}
              color={teller ? `color-mix(in srgb, ${teller.color} 38%, #e9dcbd)` : undefined}
            />
            {teller && (
              <figcaption className="relative mb-1 flex items-center justify-center gap-2 font-sans text-[11px] font-semibold uppercase tracking-[0.22em] text-ink-faint">
                <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: teller.color }} />
                {teller.name}&rsquo;s telling
              </figcaption>
            )}
            <blockquote className="relative text-balance text-center font-hand text-[clamp(1.45rem,2.3vw,2.05rem)] leading-[1.15] text-ink">
              {tokens.map((t, i) => (
                <span
                  key={i}
                  className="transition-[color,opacity] duration-300 ease-out"
                  style={{ opacity: i < spoken ? 1 : 0.32 }}
                >
                  {t}
                  {i < tokens.length - 1 ? " " : ""}
                </span>
              ))}
            </blockquote>
          </motion.figure>
        )}
      </AnimatePresence>
    </div>
  );
}
