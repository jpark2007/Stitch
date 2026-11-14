/**
 * The quiet states of the threads screen, all on the same paper: a loading
 * skeleton (faint running stitches where the threads will be) and a sheet
 * for messages (signed out, no family yet, nothing to show, something went
 * wrong). No hooks, so the skeleton can also be the server's fallback.
 */

import Link from "next/link";
import type { ReactNode } from "react";

/** Gentle waves for the stand-in threads, in a 1000 x 100 box. */
const LANES = [
  { y: 8, tint: "var(--sage)", d: 0.0 },
  { y: 27, tint: "var(--mustard)", d: 0.35 },
  { y: 46, tint: "var(--plum)", d: 0.7 },
  { y: 65, tint: "var(--sky)", d: 1.05 },
  { y: 84, tint: "var(--accent)", d: 1.4 },
];

function wave(y: number, i: number): string {
  // One soft dip per thread, at a different place each, like threads meeting.
  const at = 180 + i * 150;
  const dip = i % 2 ? -7 : 7;
  return `M -10 ${y} L ${at - 90} ${y} C ${at - 40} ${y}, ${at - 40} ${y + dip}, ${at} ${y + dip} S ${at + 40} ${y}, ${at + 90} ${y} L 1010 ${y}`;
}

/** Faint stand-in threads and the sewn axis, laid where the real ones go. */
export function PaperThreads(props: { busy?: boolean }) {
  return (
    <div aria-hidden className="threads-paper pointer-events-none absolute inset-x-0" data-busy={props.busy ? "1" : undefined}>
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1000 100" preserveAspectRatio="none">
        {LANES.map((l, i) => (
          <path
            key={l.y}
            d={wave(l.y, i)}
            fill="none"
            stroke={l.tint}
            strokeWidth={2.4}
            strokeLinecap="round"
            strokeDasharray="7 6"
            vectorEffect="non-scaling-stroke"
            className="threads-paper-thread"
            style={{ animationDelay: `${l.d}s` }}
          />
        ))}
      </svg>
      <span className="threads-paper-axis absolute inset-x-[28px] block" />
    </div>
  );
}

/** Loading: the header's shape and the threads, softly breathing, never a blank flash. */
export function ThreadsSkeleton() {
  return (
    <main className="fixed inset-0 overflow-clip select-none" role="status" aria-busy="true" aria-live="polite">
      <PaperThreads busy />
      <header className="pointer-events-none absolute inset-x-0 top-0 px-[var(--gutter)] pt-6 sm:pt-8">
        <span className="threads-paper-bar block h-3 w-36" />
        <span className="threads-paper-bar mt-3 block h-8 w-56 sm:h-9 sm:w-72" />
      </header>
      <p className="threads-hint caption pointer-events-none" style={{ animation: "none" }}>
        gathering the threads&hellip;
      </p>
    </main>
  );
}

/**
 * A light sheet over the faint threads, for anything that is not the
 * timeline itself. `kicker` names the place, `title` says what is going on.
 */
export function ThreadsSheet(props: { kicker?: ReactNode; title: ReactNode; children: ReactNode; label?: string }) {
  return (
    <main className="fixed inset-0 overflow-y-auto">
      <PaperThreads />
      {/* The same way back as on the timeline. */}
      <p className="kicker absolute left-[var(--gutter)] top-6 sm:top-8">
        <Link href="/" className="text-ink-faint no-underline transition-colors hover:text-ink-soft">
          Family tree
        </Link>
        <span aria-hidden className="mx-2 opacity-60">
          /
        </span>
        <span className="text-ink-soft">Threads</span>
      </p>
      <div className="relative flex min-h-full items-center justify-center px-4 py-20">
        <section
          aria-label={props.label}
          className="relative w-full max-w-md rounded-paper border border-rule bg-paper-light p-6 text-ink shadow-lift sm:p-8"
        >
          {props.kicker ? <p className="kicker break-words">{props.kicker}</p> : null}
          <h1 className="mt-2 text-[1.7rem] leading-tight sm:text-[2rem]" style={{ fontVariationSettings: '"SOFT" 100, "WONK" 1' }}>
            {props.title}
          </h1>
          <div className="mt-4 text-ink-soft">{props.children}</div>
        </section>
      </div>
    </main>
  );
}
