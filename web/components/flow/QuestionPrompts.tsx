"use client";

// Questions family members asked this person, as quiet handwritten lines
// above the story on the add-a-memory page. Each note is one toggle button:
// pressing it answers that question (the page turns it into a draft with
// draftFromQuestion), pressing it again lets it go. A question about a moment
// says "Add your version": answering it adds this person's telling to that
// moment instead of starting a new one. A pressed note says what is under way
// ("Answering this", "Adding your version") with a check mark. Two show at once; the rest
// wait behind "More questions" so the story stays on the first screen and the
// page never feels like an inbox.
import { useId, useState } from "react";
import type { Question } from "@/lib/types";
import { Button, FOCUS_RING } from "./Button";

const VISIBLE = 2;

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
  // A picked question never hides behind "More questions".
  const open = expanded || pickedIndex >= VISIBLE;
  const shown = open ? questions : questions.slice(0, VISIBLE);
  const hidden = questions.length - VISIBLE;

  return (
    <section aria-labelledby={headingId} className="min-w-0">
      <h2 id={headingId} className="font-sans text-[0.9375rem] leading-snug font-medium text-ink-soft">
        Someone asked you
      </h2>

      {/* Each note is one quiet line on the paper, in the asker's hand; the
          left edge of its text lines up with the page (the hover wash bleeds
          out past it). */}
      <ul id={listId} className="mt-1 list-none space-y-0.5 p-0">
        {shown.map((q) => {
          const picked = q.id === pickedId;
          return (
            <li key={q.id}>
              <button
                type="button"
                aria-pressed={picked}
                onClick={() => onPick(q)}
                className={[
                  "group relative -mx-3 flex min-h-11 w-[calc(100%+1.5rem)] flex-col items-start gap-0.5 rounded-card px-3 py-1.5 text-left",
                  "sm:flex-row sm:items-center sm:justify-between sm:gap-5",
                  "transition-colors duration-150 ease-paper motion-reduce:transition-none",
                  FOCUS_RING,
                  picked ? "bg-paper-deep/70" : "hover:bg-paper-deep/40",
                ].join(" ")}
              >
                <span className="min-w-0 font-hand text-[1.25rem] leading-[1.15] text-pretty break-words text-ink sm:text-[1.375rem]">
                  {q.text}
                </span>
                <span
                  className={[
                    "inline-flex shrink-0 items-center gap-1.5 text-[0.9375rem] font-semibold text-accent-deep",
                    picked ? "" : "underline-offset-[0.2em] group-hover:underline",
                  ].join(" ")}
                >
                  {picked ? <PickedMark /> : null}
                  {promptAction(Boolean(q.memoryId), picked)}
                  {/* Heard, not seen: the note is a toggle. */}
                  {picked ? <span className="sr-only">, press again to let it go</span> : null}
                </span>
              </button>
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
          className="mt-1.5 -ml-3"
        >
          {open ? "Fewer questions" : `More questions (${hidden})`}
        </Button>
      ) : null}
    </section>
  );
}

/** A note's action label: what pressing it does, or (once pressed) what is under way. */
function promptAction(aboutMoment: boolean, picked: boolean): string {
  if (aboutMoment) return picked ? "Adding your version" : "Add your version";
  return picked ? "Answering this" : "Answer this";
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
