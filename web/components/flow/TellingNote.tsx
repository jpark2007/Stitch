"use client";

// A small read-only slip of what was just sent, shown above the AgentFeed
// while the run stages it: the question (in handwriting), the story, the year
// and how many photos went with it. A long story is clamped to a few lines;
// the full text stays in the DOM, so screen readers always read all of it,
// and "Show all" opens it for sighted readers.
import { useEffect, useId, useRef, useState } from "react";

const HAND = "font-[family-name:var(--font-hand,var(--font-caveat,'Bradley_Hand'),cursive)]";

function photoLabel(n: number): string | null {
  if (!Number.isFinite(n) || n <= 0) return null;
  return n === 1 ? "1 photo" : `${n} photos`;
}

export function TellingNote(props: {
  question?: string | null;
  text: string;
  year: number;
  photoCount: number;
  /** Title of the moment this is a second telling of. */
  secondTellingOf?: string | null;
}) {
  const { question, text, year, photoCount, secondTellingOf } = props;
  const uid = useId();
  const headingId = `${uid}-heading`;
  const textId = `${uid}-text`;
  const textRef = useRef<HTMLParagraphElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);

  // Only offer the toggle when the clamp actually hides something. The
  // observer reports once on observe and again whenever the width changes.
  useEffect(() => {
    const el = textRef.current;
    if (!el || expanded || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setOverflows(el.scrollHeight > el.clientHeight + 1));
    ro.observe(el);
    return () => ro.disconnect();
  }, [text, expanded]);

  const q = question?.trim();
  const photos = photoLabel(photoCount);
  const meta = [String(year), photos].filter(Boolean).join(" · ");

  return (
    <section
      aria-labelledby={headingId}
      className="relative min-w-0 rounded-[var(--radius-paper,2px)] border border-[var(--rule,#2f262024)] bg-[var(--paper-light,#fbf8f1)] px-5 pt-4 pb-4 text-[var(--ink,#2f2620)] shadow-[var(--shadow-paper,0_1px_1px_rgb(74_47_29/0.08),0_2px_6px_rgb(74_47_29/0.08))] sm:px-6"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2
          id={headingId}
          className="text-xs font-semibold tracking-[0.08em] text-[var(--ink-soft,#5c4e43)] uppercase"
        >
          {secondTellingOf ? (
            <>
              Your version of &lsquo;<span className="normal-case">{secondTellingOf}</span>&rsquo;
            </>
          ) : (
            "Your telling"
          )}
        </h2>
        <p className="text-sm text-[var(--ink-faint,#8f8173)] tabular-nums">{meta}</p>
      </div>

      {q ? (
        <p className={`mt-2 text-[1.375rem] leading-[1.2] text-pretty break-words ${HAND}`}>{q}</p>
      ) : null}

      <p
        ref={textRef}
        id={textId}
        className={[
          "mt-2 text-[0.9375rem] leading-relaxed break-words whitespace-pre-line text-[var(--ink-soft,#5c4e43)]",
          expanded ? "" : "line-clamp-4",
        ].join(" ")}
      >
        {text.trim()}
      </p>

      {overflows || expanded ? (
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={textId}
          onClick={() => setExpanded((v) => !v)}
          className="-ml-1 mt-1 inline-flex min-h-11 items-center rounded-[var(--radius-paper,2px)] px-1 text-sm font-semibold text-[var(--accent-deep,#8f3f2a)] underline-offset-[0.2em] hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent,#b9573d)]"
        >
          {expanded ? "Show less" : "Show all"}
        </button>
      ) : null}
    </section>
  );
}
