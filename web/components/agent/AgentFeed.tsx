"use client";

// Live agent plan and sub-agent steps (ARCHITECTURE.md section 2).
// Renders a RunState built by runReducer; the page owns the stream and the
// pending-input cards, which it passes in as children.
import { useId, useState, type ReactNode } from "react";
import Link from "next/link";
import { AnimatePresence, MotionConfig, motion } from "framer-motion";

import type { PlanItem } from "@/lib/types";
import type { FeedEntry, FeedStep, RunPhase, RunState } from "./runState";

// Local palette: warm paper, brown ink, a rust thread and a sage check.
// paper #fbf6ec, paper-deep #f3ead8, edge #e6d9c0, ink #3b2f27,
// muted #7a6857, thread #b0533a, sage #5f7f52, tape #ecdcae, error #9c3b2a / #f7e4dc

const STEP_NAMES: Record<string, string> = {
  interviewer: "Listening to your story",
  storyboarder: "Sketching the storyboard",
  prop_master: "Cutting out the props",
  set_designer: "Building the scenery",
  director: "Directing the scene",
  narrator: "Recording the narration",
  checker: "Checking the pages",
  linker: "Adding it to everyone’s books",
};

/** Friendly sub-agent name; unknown names are humanized ("sound_mixer" -> "Sound mixer"). */
function stepLabel(name: string): string {
  const known = STEP_NAMES[name];
  if (known) return known;
  const words = name
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_\-.]+/g, " ")
    .trim()
    .toLowerCase();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Working";
}

const PHASE_LINE: Record<RunPhase, string> = {
  connecting: "Opening the studio…",
  running: "The studio is working on your moment.",
  waiting: "The studio has a question for you.",
  scene_ready: "Your page is ready.",
  done: "All finished.",
  error: "Something went wrong.",
};

/** Frame time for alt text: at most one decimal place ("3.3", "4"). */
function formatSeconds(t: number): string {
  return Number.isFinite(t) ? String(Math.round(t * 10) / 10) : "?";
}

/** How many of the latest steps stay visible before the rest fold away. */
const VISIBLE_STEPS = 4;

export default function AgentFeed(props: {
  state: RunState;
  mediaUrl: (mediaId: string) => string;
  sceneHref?: string;
  onRetry?: () => void;
  children?: ReactNode;
}) {
  const { state, mediaUrl, sceneHref, onRetry, children } = props;
  const headingId = useId();
  const feedId = useId();
  const [showAll, setShowAll] = useState(false);

  const stepsByKey = new Map<string, FeedStep>(state.steps.map((s) => [s.key, s]));

  // Collapse finished steps that are older than the latest few.
  const stepEntries = state.log.filter((e) => e.kind === "step");
  const recentStepKeys = new Set(stepEntries.slice(-VISIBLE_STEPS).map((e) => e.stepKey));
  const isFolded = (e: FeedEntry) =>
    e.kind === "step" && !recentStepKeys.has(e.stepKey) && stepsByKey.get(e.stepKey)?.status === "done";
  const hiddenCount = state.log.filter(isFolded).length;
  // The error box below repeats the latest error, so it is not logged twice.
  const lastEntry = state.log.at(-1);
  const isRepeatedError = (e: FeedEntry) => state.phase === "error" && e === lastEntry && e.kind === "error";
  const visibleLog = state.log.filter((e) => !isRepeatedError(e) && (showAll || !isFolded(e)));

  const finished = state.phase === "done";
  const sceneReady = state.phase === "scene_ready" || (finished && state.memoryId !== null);

  return (
    <MotionConfig reducedMotion="user">
      <section
        aria-labelledby={headingId}
        className="relative w-full max-w-xl rounded-[18px] border border-[#e6d9c0] bg-[#fbf6ec] px-6 py-7 text-[#3b2f27] shadow-[0_1px_0_#fffdf8_inset,0_10px_28px_-14px_rgba(59,47,39,0.35)] sm:px-8"
      >
        <header className="mb-6">
          <h2 id={headingId} className="font-serif text-2xl leading-tight tracking-[-0.01em]">
            {finished ? "Your moment is stitched in" : "Making your page"}
          </h2>
          <p className="mt-1.5 flex items-center gap-2 text-sm text-[#7a6857]">
            <PhaseMark phase={state.phase} />
            <span>{PHASE_LINE[state.phase]}</span>
          </p>
        </header>

        {state.todos.length > 0 && (
          <PlanList todos={state.todos} done={finished} still={state.phase === "waiting" || state.phase === "error"} />
        )}

        <div
          id={feedId}
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
          aria-label="What the studio is doing"
          className={state.todos.length > 0 ? "mt-5 border-t border-dashed border-[#e6d9c0] pt-5" : ""}
        >
          {state.phase === "connecting" && state.log.length === 0 && (
            <p className="text-sm text-[#7a6857]">Threading the needle. This takes a moment.</p>
          )}

          {hiddenCount > 0 && (
            <button
              type="button"
              aria-expanded={showAll}
              aria-controls={feedId}
              onClick={() => setShowAll((v) => !v)}
              className="mb-3 ml-7 rounded-sm py-0.5 text-xs font-medium text-[#7a6857] underline decoration-[#e6d9c0] decoration-2 underline-offset-4 transition-colors hover:text-[#3b2f27] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#b0533a]"
            >
              {showAll ? "Hide earlier steps" : `Show all steps (${hiddenCount} earlier)`}
            </button>
          )}

          {visibleLog.length > 0 && (
            <ol className="space-y-3">
              <AnimatePresence initial={false}>
                {visibleLog.map((entry) => (
                  <motion.li
                    key={entry.seq}
                    layout="position"
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.28, ease: "easeOut" }}
                  >
                    <Entry
                      entry={entry}
                      step={entry.kind === "step" ? stepsByKey.get(entry.stepKey) : undefined}
                      mediaUrl={mediaUrl}
                      phase={state.phase}
                    />
                  </motion.li>
                ))}
              </AnimatePresence>
            </ol>
          )}
        </div>

        {children != null && children !== false && <div className="mt-6">{children}</div>}

        {sceneReady && (
          <div className="mt-6 rounded-xl border border-dashed border-[#5f7f52]/50 bg-[#f3ead8] px-5 py-4">
            <p className="font-serif text-lg">
              {finished ? "Your page is ready, and it’s in everyone’s books." : "Your page is ready"}
            </p>
            {!finished && (
              <p className="mt-0.5 text-sm text-[#7a6857]">You can open it now. The studio is finishing a few last touches.</p>
            )}
            {sceneHref && (
              <Link
                href={sceneHref}
                className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-[#3b2f27] px-4 py-2 text-sm font-medium text-[#fbf6ec] transition-colors hover:bg-[#b0533a] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#b0533a]"
              >
                Open your page <span aria-hidden="true">&rarr;</span>
              </Link>
            )}
          </div>
        )}

        {finished && !sceneReady && (
          <p className="mt-6 font-serif text-lg">All done. Thank you for sharing this one.</p>
        )}

        {state.phase === "error" && (
          <div role="alert" className="mt-6 rounded-xl border border-[#9c3b2a]/25 bg-[#f7e4dc] px-5 py-4 text-[#6e2a1e]">
            <p className="font-medium">{state.error ?? "The studio stopped unexpectedly."}</p>
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                className="mt-3 rounded-full border border-[#9c3b2a]/40 bg-[#fbf6ec] px-4 py-1.5 text-sm font-medium text-[#6e2a1e] transition-colors hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#9c3b2a]"
              >
                Retry
              </button>
            )}
          </div>
        )}
      </section>
    </MotionConfig>
  );
}

function PhaseMark({ phase }: { phase: RunPhase }) {
  if (phase === "done" || phase === "scene_ready") {
    return <CheckIcon className="h-3.5 w-3.5 text-[#5f7f52]" />;
  }
  if (phase === "error") {
    return <span aria-hidden="true" className="inline-block h-2 w-2 rounded-full bg-[#9c3b2a]" />;
  }
  if (phase === "waiting") {
    return <span aria-hidden="true" className="inline-block h-2 w-2 rounded-full bg-[#b0533a]" />;
  }
  return <StitchSpinner />;
}

/** A small dashed ring that slowly turns, like a running stitch. Still under reduced motion. */
function StitchSpinner({ still = false }: { still?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block h-3.5 w-3.5 shrink-0 rounded-full border-[1.5px] border-dashed border-[#b0533a] ${still ? "" : "motion-safe:animate-[spin_3.5s_linear_infinite]"}`}
    />
  );
}

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" className={className}>
      <path d="M3 8.5l3.2 3L13 4.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function PlanList({ todos, done, still }: { todos: PlanItem[]; done: boolean; still: boolean }) {
  const headingId = useId();
  return (
    <div>
      <h3 id={headingId} className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-[#7a6857]">
        The plan
      </h3>
      <ul aria-labelledby={headingId} className="space-y-1.5">
        {todos.map((todo, i) => {
          const status = done ? "completed" : (todo.status ?? "pending");
          return (
            <li key={`${i}-${todo.text}`} className="flex items-start gap-2.5 text-[15px] leading-snug">
              <span className="mt-[3px] flex h-4 w-4 shrink-0 items-center justify-center">
                {status === "completed" ? (
                  <span className="flex h-4 w-4 items-center justify-center rounded-[4px] bg-[#5f7f52] text-[#fbf6ec]">
                    <CheckIcon className="h-3 w-3" />
                  </span>
                ) : status === "in_progress" ? (
                  <StitchSpinner still={still} />
                ) : (
                  <span aria-hidden="true" className="h-3.5 w-3.5 rounded-[4px] border-[1.5px] border-[#cbb99b]" />
                )}
              </span>
              <span className={status === "completed" ? "text-[#7a6857]" : status === "in_progress" ? "font-medium" : "text-[#3b2f27]"}>
                {todo.text}
                <span className="sr-only">
                  {status === "completed" ? " (done)" : status === "in_progress" ? " (in progress)" : " (to do)"}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Entry({
  entry,
  step,
  mediaUrl,
  phase,
}: {
  entry: FeedEntry;
  step: FeedStep | undefined;
  mediaUrl: (mediaId: string) => string;
  phase: RunPhase;
}) {
  switch (entry.kind) {
    case "step": {
      if (!step) return null;
      // A finished run closes every step; a paused or failed run stops the spinners.
      const done = step.status === "done" || phase === "done";
      const moving = !done && phase !== "waiting" && phase !== "error";
      const detail = done ? (step.summary ?? step.task) : step.task;
      return (
        <div className="flex items-start gap-3">
          <span className="mt-1 flex h-4 w-4 shrink-0 items-center justify-center">
            {done ? (
              <CheckIcon className="h-3.5 w-3.5 text-[#5f7f52]" />
            ) : moving ? (
              <StitchSpinner />
            ) : (
              <span aria-hidden="true" className="h-3.5 w-3.5 rounded-full border-[1.5px] border-dashed border-[#cbb99b]" />
            )}
          </span>
          <div className="min-w-0">
            <p className={done ? "text-[#3b2f27]" : "font-medium"}>
              {stepLabel(step.name)}
              <span className="sr-only">{done ? " (done)" : moving ? " (working)" : " (paused)"}</span>
            </p>
            {detail && <p className="mt-0.5 text-sm leading-snug text-[#7a6857]">{detail}</p>}
          </div>
        </div>
      );
    }

    case "compile_error": {
      const n = entry.event.errors.length;
      const things = `${n} ${n === 1 ? "thing" : "things"}`;
      return (
        <Note>
          <p>
            The director is fixing {things}
            {entry.event.round != null ? `, round ${entry.event.round}` : ""}.
          </p>
          {n > 0 && (
            <details className="mt-1.5">
              <summary className="cursor-pointer text-xs text-[#7a6857] underline decoration-[#e6d9c0] underline-offset-4 hover:text-[#3b2f27]">
                What needs fixing
              </summary>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-[#7a6857]">
                {entry.event.errors.map((err, i) => (
                  <li key={`${i}-${err.code}`}>{err.message}</li>
                ))}
              </ul>
            </details>
          )}
        </Note>
      );
    }

    case "frames":
      return (
        <figure className="pl-7">
          <ul className="flex flex-wrap gap-3 pt-2 sm:gap-4" aria-label="Frames the checker looked at">
            {entry.event.frames.map((f, i) => (
              <li key={`${i}-${f.mediaId}`} className={i % 2 === 0 ? "-rotate-[1.5deg]" : "rotate-[1.2deg]"}>
                <span className="relative block bg-white p-1.5 pb-2 shadow-[0_3px_8px_-3px_rgba(59,47,39,0.35)]">
                  <span
                    aria-hidden="true"
                    className="absolute -top-2 left-1/2 h-3.5 w-10 -translate-x-1/2 rotate-[-3deg] bg-[#ecdcae]/80"
                  />
                  {/* Media comes from the API with the session cookie; next/image would need remote config. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={mediaUrl(f.mediaId)}
                    alt={`Checker frame at ${formatSeconds(f.t)}s`}
                    width={112}
                    height={72}
                    loading="lazy"
                    className="block h-14 w-[88px] bg-[#f3ead8] object-cover sm:h-[72px] sm:w-28"
                  />
                </span>
              </li>
            ))}
          </ul>
          {entry.event.critique && (
            <figcaption className="mt-2 font-serif text-sm italic leading-snug text-[#7a6857]">
              {entry.event.critique}
            </figcaption>
          )}
        </figure>
      );

    case "asked":
      return (
        <Note>
          <span className="text-[#7a6857]">The studio asked: </span>
          <span className="font-serif italic">“{entry.text}”</span>
        </Note>
      );

    case "answered":
      return (
        <Note>
          <span className="text-[#7a6857]">You answered: </span>
          {entry.summary}
        </Note>
      );

    case "props":
      return (
        <Note>
          Found {entry.count} {entry.count === 1 ? "prop" : "props"} for you to look over.
        </Note>
      );

    case "merge":
      return <Note>{entry.text}</Note>;

    case "scene_ready":
      return <Note tone="good">The scene is stitched together.</Note>;

    case "error":
      return <Note tone="bad">{entry.message}</Note>;

    default:
      return null;
  }
}

function Note({ children, tone = "plain" }: { children: ReactNode; tone?: "plain" | "good" | "bad" }) {
  const dot = tone === "good" ? "bg-[#5f7f52]" : tone === "bad" ? "bg-[#9c3b2a]" : "bg-[#cbb99b]";
  return (
    <div className="flex items-start gap-3 text-sm leading-snug">
      <span className="mt-[7px] flex w-4 shrink-0 justify-center">
        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      </span>
      <div className={`min-w-0 ${tone === "bad" ? "text-[#9c3b2a]" : ""}`}>{children}</div>
    </div>
  );
}

