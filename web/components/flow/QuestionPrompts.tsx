"use client";

// Questions family members asked this person, as small handwritten notes on
// top of the add-a-memory page. Picking one fills the form (the page turns it
// into a draft with draftFromQuestion). Three show at once; the rest wait
// behind "Show N more" so the page never feels like an inbox.
import { useId, useState } from "react";
import type { Question } from "@/lib/types";
import { Button } from "./Button";

const VISIBLE = 3;
const HAND = "font-[family-name:var(--font-hand,var(--font-caveat,'Bradley_Hand'),cursive)]";
// A hand-placed tilt per note, small enough that the row still reads tidy.
const TILTS = [-0.6, 0.45, -0.3, 0.55, -0.45];

export function QuestionPrompts(props: {
  questions: Question[];
  pickedId?: string | null;
  onPick: (q: Question) => void;
}) {
  const { questions, pickedId = null, onPick } = props;
  const uid = useId();
  const headingId = `${uid}-heading`;
  const listId = `${uid}-list`;
  const [expanded, setExpanded] = useState(false);

  if (questions.length === 0) return null;

  const pickedIndex = pickedId ? questions.findIndex((q) => q.id === pickedId) : -1;
  // A picked question never hides behind "Show more".
  const open = expanded || pickedIndex >= VISIBLE;
  const shown = open ? questions : questions.slice(0, VISIBLE);
  const hidden = questions.length - VISIBLE;

  return (
    <section aria-labelledby={headingId} className="min-w-0">
      <h2
        id={headingId}
        className="font-serif text-[1.375rem] leading-tight tracking-[-0.01em] text-[var(--ink,#2f2620)]"
      >
        Someone asked you
      </h2>
      <p className="mt-1 text-[0.9375rem] text-[var(--ink-soft,#5c4e43)]">
        Pick one to answer, or tell any story you like below.
      </p>

      <ul id={listId} className="mt-4 list-none space-y-3 p-0">
        {shown.map((q, i) => {
          const picked = q.id === pickedId;
          return (
            <li
              key={q.id}
              style={{ transform: `rotate(${TILTS[i % TILTS.length]}deg)` }}
              className={[
                "relative flex flex-col gap-3 rounded-[var(--radius-paper,2px)] border px-4 pt-4 pb-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-5 sm:px-5",
                "shadow-[var(--shadow-paper,0_1px_1px_rgb(74_47_29/0.08),0_2px_6px_rgb(74_47_29/0.08))]",
                "transition-colors duration-150 motion-reduce:transition-none",
                picked
                  ? "border-[var(--accent,#b9573d)] bg-[var(--paper-light,#fbf8f1)]"
                  : "border-[var(--rule,#2f262024)] bg-[var(--paper-light,#fbf8f1)]",
              ].join(" ")}
            >
              {/* A strip of tape holding the note to the page. */}
              <span
                aria-hidden="true"
                className="absolute -top-2 left-6 h-3.5 w-11 -rotate-3 bg-[#e9dcbd]/80 mix-blend-multiply"
              />
              <p
                className={`min-w-0 text-[1.5rem] leading-[1.2] text-pretty break-words text-[var(--ink,#2f2620)] ${HAND}`}
              >
                {q.text}
              </p>
              <Button
                variant="secondary"
                aria-pressed={picked}
                onClick={() => onPick(q)}
                className="shrink-0 self-start aria-pressed:border-[var(--accent-deep,#8f3f2a)] aria-pressed:bg-[var(--paper-deep,#ebe0cb)] sm:self-auto"
              >
                {picked ? <PickedMark /> : null}
                Answer this
              </Button>
            </li>
          );
        })}
      </ul>

      {hidden > 0 && pickedIndex < VISIBLE ? (
        <Button
          variant="quiet"
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setExpanded((v) => !v)}
          className="mt-3 -ml-3"
        >
          {open ? "Show fewer" : `Show ${hidden} more`}
        </Button>
      ) : null}
    </section>
  );
}

function PickedMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 12 12" className="size-3 shrink-0">
      <path
        d="M2 6.5 4.8 9 10 3"
        fill="none"
        stroke="var(--accent-deep, #8f3f2a)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
