"use client";

/**
 * The book's table of contents (DESIGN.md "The book": "Page navigation dots
 * or a small table of contents"), small and sparse:
 *
 *   <ContentsCard>  with the closed book on the table, a little printed card
 *                   lying beside it: each moment's year (handwriting) and
 *                   title (the storybook serif). It fades as the book opens.
 *   <ContentsTab>   once the book is open, a quiet "Contents" tab by the
 *                   mute button that unfolds the same list.
 *
 * Choosing an entry goes to its spread (BookControls.goTo, which opens the
 * book first when it is closed). Plain HTML, so the titles are real text
 * for readers, screen readers and tests. The closing spread is not listed.
 */

import { motion } from "framer-motion";
import { useEffect, useId, useRef, useState, type WheelEvent } from "react";
import type { BookView } from "./controller";
import type { BookSpread } from "./types";

export interface ContentsEntry {
  /** 1-based spread number, as BookControls.goTo takes it. */
  spread: number;
  id: string;
  title: string;
  /** Empty for a moment with no year. */
  year: string;
}

/** The moments of a book as contents entries (the closing spread left out). */
export function contentsOf(spreads: readonly BookSpread[]): ContentsEntry[] {
  const out: ContentsEntry[] = [];
  spreads.forEach((s, i) => {
    if (s.kind === "end") return;
    out.push({ spread: i + 1, id: s.id, title: s.title, year: Number.isFinite(s.year) && s.year > 0 ? String(s.year) : "" });
  });
  return out;
}

interface ContentsProps {
  spreads: readonly BookSpread[];
  view: BookView;
  onPick: (spread: number) => void;
}

const EASE = [0.22, 1, 0.36, 1] as const;
/**
 * A card's opacity at rest. Just under 1 on purpose: a list that scrolls
 * (a phone's long contents) over the book's canvas otherwise leaves a dark
 * ghost of itself on the canvas below it in Chromium's compositor (seen in
 * headless captures); a translucent card is composited whole and does not.
 */
const SHOWN = 0.999;

/** The book turns pages on the wheel; a list that scrolls keeps its own. */
const keepWheel = (e: WheelEvent) => e.stopPropagation();

function Entries({
  entries,
  current,
  onPick,
  dense = false,
}: {
  entries: ContentsEntry[];
  current: number | null;
  onPick: (spread: number) => void;
  dense?: boolean;
}) {
  return (
    <ol className={dense ? "flex flex-col" : "flex flex-col gap-0.5"}>
      {entries.map((e) => {
        const here = e.spread === current;
        return (
          <li key={e.id}>
            <button
              type="button"
              onClick={() => onPick(e.spread)}
              aria-current={here ? "page" : undefined}
              className={`group grid w-full cursor-pointer grid-cols-[3.1rem_1fr] items-baseline gap-x-2 rounded-[3px] px-1.5 text-left transition-colors duration-200 hover:bg-[#efe3cc] focus-visible:bg-[#efe3cc] ${dense ? "py-[0.2rem]" : "py-1"}`}
            >
              <span className="font-hand text-[1.3rem] leading-none text-accent">{e.year}</span>
              <span
                className={`font-display text-[0.98rem] leading-snug ${here ? "text-ink" : "text-ink-soft group-hover:text-ink"}`}
                style={{ fontVariationSettings: '"SOFT" 100' }}
              >
                {e.title}
                {here && (
                  <span aria-hidden className="ml-1.5 inline-block align-middle text-[0.7rem] text-accent">
                    &#9679;
                  </span>
                )}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Beside the closed book: on a wide screen a card on the table to its left;
 * on a phone (or a squarer screen, where the book fills the width) a strip
 * across the top, under the header, that scrolls when the book is long.
 */
export function ContentsCard({ spreads, view, onPick }: ContentsProps) {
  const entries = contentsOf(spreads);
  const shown = !view.opened && entries.length > 0;
  if (entries.length === 0) return null;
  return (
    <nav aria-label="Contents" aria-hidden={!shown} inert={!shown} className="contents-card pointer-events-none absolute z-10 flex">
      <style>{CARD_CSS}</style>
      <motion.div
        onWheel={keepWheel}
        className="max-h-[inherit] w-full touch-pan-y overflow-y-auto overscroll-contain rounded-[3px] bg-[#f7f0e1] px-3 pb-3 pt-2.5 text-ink shadow-[0_1px_1px_rgb(0_0_0/0.25),0_14px_32px_-10px_rgb(0_0_0/0.75)]"
        // Laid on the table a beat after the book appears (Book3D shows it once its art is drawn).
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: shown ? SHOWN : 0, y: shown ? 0 : 6 }}
        transition={{ duration: shown ? 0.8 : 0.45, delay: shown ? 0.3 : 0, ease: EASE }}
        style={{ pointerEvents: shown ? "auto" : "none" }}
      >
        <p className="mb-1.5 px-1.5 font-sans text-[0.66rem] font-bold uppercase tracking-[0.2em] text-ink-faint">Contents</p>
        <Entries entries={entries} current={null} onPick={onPick} dense={entries.length > 6} />
      </motion.div>
    </nav>
  );
}

// Placement by the shape of the screen, as the book's camera frames it:
// a wide screen leaves room on the table beside the closed book; a phone or
// a squarer screen does not, so the card becomes a strip above it.
// The strip stops above the closed book's top edge, which sits lower on a
// tall phone (about 26% down) than on a squarer screen (about 22%).
const CARD_CSS = `
.contents-card { left: 1rem; right: 1rem; top: 4.25rem; max-height: max(4.5rem, calc(26dvh - 4.75rem)); }
.contents-card > div { padding-bottom: 1.4rem; mask-image: linear-gradient(to bottom, #000 calc(100% - 1.4rem), transparent); }
@media (min-aspect-ratio: 3/5) { .contents-card { max-height: max(4.5rem, calc(21.5dvh - 4.75rem)); } }
@media (min-width: 640px) { .contents-card { left: 50%; right: auto; width: min(26rem, calc(100vw - 2rem)); transform: translateX(-50%); } }
@media (min-width: 1000px) and (min-aspect-ratio: 5/4) {
  .contents-card { left: max(1rem, calc(50% - 41rem)); top: 50%; width: clamp(14rem, 22vw - 2rem, 17.5rem); max-height: min(30rem, 70dvh); transform: translateY(-50%) rotate(-1.2deg); }
  .contents-card > div { padding-bottom: 0.75rem; mask-image: none; }
}
`;

/** After the book has opened: a quiet "Contents" tab that unfolds the same list. */
export function ContentsTab({ spreads, view, onPick }: ContentsProps) {
  const entries = contentsOf(spreads);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const listId = useId();
  const shown = view.ready && entries.length > 1;

  // Click outside, or Escape, folds it away.
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", away, true);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("pointerdown", away, true);
      window.removeEventListener("keydown", key);
    };
  }, [open]);

  if (!shown) return null;
  return (
    <div ref={box} className="pointer-events-auto relative">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((o) => !o)}
        className="flex h-11 cursor-pointer items-center gap-2 rounded-full px-3 font-display text-[0.95rem] text-[#f3e6cf]/80 transition-colors hover:text-[#f3e6cf]"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
          <path d="M5.5 4h8M5.5 8h8M5.5 12h8" />
          <path d="M2.5 4h.01M2.5 8h.01M2.5 12h.01" strokeWidth="2" />
        </svg>
        Contents
      </button>
      <motion.nav
        id={listId}
        aria-label="Contents"
        aria-hidden={!open}
        inert={!open}
        onWheel={keepWheel}
        className="absolute right-0 top-full z-20 mt-2 max-h-[min(26rem,calc(100dvh-6rem))] w-[min(19rem,calc(100vw-2rem))] origin-top-right touch-pan-y overflow-y-auto overscroll-contain rounded-[3px] bg-[#f7f0e1] px-2.5 py-2.5 text-ink shadow-[0_1px_1px_rgb(0_0_0/0.25),0_14px_32px_-10px_rgb(0_0_0/0.75)]"
        initial={false}
        animate={{ opacity: open ? SHOWN : 0, y: open ? 0 : -6, scale: open ? 1 : 0.98 }}
        transition={{ duration: 0.3, ease: EASE }}
        style={{ pointerEvents: open ? "auto" : "none" }}
      >
        <Entries
          entries={entries}
          current={view.spread}
          onPick={(n) => {
            setOpen(false);
            onPick(n);
          }}
        />
      </motion.nav>
    </div>
  );
}
