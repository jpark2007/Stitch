"use client";

// Questions family members asked this person, as small handwritten notes on
// top of the add-a-memory page. Picking one fills the form (the page turns it
// into a draft with draftFromQuestion). Three show at once; the rest wait
// behind "Show N more" so the page never feels like an inbox.
import { useId, useState } from "react";
import type { Question } from "@/lib/types";
import { Button } from "./Button";

const VISIBLE = 3;
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
        className="font-display text-[1.375rem] leading-tight tracking-[-0.01em] text-ink"
      >
        Someone asked you
      </h2>
      <p className="mt-1 text-[0.9375rem] text-ink-soft">
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
                "relative flex flex-col gap-3 rounded-paper border bg-paper-light px-4 pt-4 pb-3.5 shadow-paper sm:flex-row sm:items-center sm:justify-between sm:gap-5 sm:px-5",
                "transition-colors duration-150 ease-paper motion-reduce:transition-none",
                picked ? "border-accent" : "border-rule",
              ].join(" ")}
            >
              {/* A strip of tape holding the note to the page. */}
              <span
                aria-hidden="true"
                className="absolute -top-2 left-6 h-3.5 w-11 -rotate-3 bg-[#e9dcbd]/80 mix-blend-multiply"
              />
              <p
                className="min-w-0 font-hand text-[1.5rem] leading-[1.2] text-pretty break-words text-ink"
              >
                {q.text}
              </p>
              <Button
                variant="secondary"
                aria-pressed={picked}
                onClick={() => onPick(q)}
                className="shrink-0 self-start aria-pressed:border-accent-deep aria-pressed:bg-paper-deep sm:self-auto"
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
    <svg aria-hidden="true" viewBox="0 0 12 12" className="size-3 shrink-0 text-accent-deep">
      <path
        d="M2 6.5 4.8 9 10 3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
