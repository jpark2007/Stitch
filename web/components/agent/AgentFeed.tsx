"use client";

// Live agent plan and sub-agent steps (ARCHITECTURE.md section 2).
// Renders a RunState built by runReducer; the page owns the stream and the
// pending-input cards, which it passes in as children. Which lines show and
// what they say is decided in feedView.ts; this file only lays them out.
import { useId, useState, type ReactNode } from "react";
import { AnimatePresence, MotionConfig, motion } from "framer-motion";

import { Button, ButtonLink } from "@/components/flow/Button";
import { Photo } from "@/components/paper/Photo";
import type { PlanItem } from "@/lib/types";
import {
  answeredLine,
  compileNote,
  errorRecovery,
  finishedHeading,
  finishedNote,
  openLabel,
  readyLine,
  workingHeading,
  hiddenEntryCount,
  pauseLine,
  phaseLine,
  startOverNote,
  stepDetail,
  toggleLabel,
  visibleEntries,
  watchLabel,
  type FeedSubject,
} from "./feedView";
import type { FeedEntry, FeedStep, RunPhase, RunState } from "./runState";

// The feed is one sheet of light paper (the flow kit's PaperCard look). Colours,
// radii and shadows come from the global tokens in app/globals.css.

/** The paper ease (--ease-paper) as framer-motion wants it. */
const PAPER_EASE = [0.22, 0.8, 0.24, 1] as const;

// Done marks are sage. Plain sage sits just under 3:1 on paper-light, so the
// mark is sage stitched with a fifth of ink (about 4:1) to stay visible.
const DONE_TEXT = "text-[color-mix(in_oklab,var(--sage)_80%,var(--ink))]";
const DONE_FILL = "bg-[color-mix(in_oklab,var(--sage)_80%,var(--ink))]";

// A stitched rule between parts of the sheet, like thread running through it.
const STITCH_TOP = "border-t-[1.5px] border-dashed border-rule";

const FOCUS_RING = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

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

/** Frame time for alt text: at most one decimal place ("3.3", "4"). */
function formatSeconds(t: number): string {
  return Number.isFinite(t) ? String(Math.round(t * 10) / 10) : "?";
}

export default function AgentFeed(props: {
  state: RunState;
  mediaUrl: (mediaId: string) => string;
  sceneHref?: string;
  /**
   * The finished scene playing on the stage: one quiet link beside the way in,
   * shown only once the run is done (and only with sceneHref).
   */
  watchHref?: string;
  /** Reconnects; offered only when the connection gave up (see errorRecovery). */
  onRetry?: () => void;
  /**
   * The way on after the run's own error, shown in the error box in place of
   * Retry (which would only replay the same error): "Tell it again", say.
   */
  startOver?: ReactNode;
  /** What the run adds, for the finished heading (default "memory"). */
  subject?: FeedSubject;
  children?: ReactNode;
}) {
  const { state, mediaUrl, sceneHref, watchHref, onRetry, startOver, subject = "memory", children } = props;
  const headingId = useId();
  const feedId = useId();
  const [showAll, setShowAll] = useState(false);

  const stepsByKey = new Map<string, FeedStep>(state.steps.map((s) => [s.key, s]));
  const visibleLog = visibleEntries(state, showAll);
  const hiddenCount = hiddenEntryCount(state);

  const finished = state.phase === "done";
  const recovery = errorRecovery(state);
  const hasStartOver = startOver != null && startOver !== false;
  const sceneReady = state.phase === "scene_ready" || (finished && state.memoryId !== null);
  const line = phaseLine(state);
  // A stitched rule sets the log off from what is above it, but only when it
  // has lines to show: a lone "Show all steps" sits right under the plan.
  const ruled = finished ? hiddenCount > 0 : state.todos.length > 0 && visibleLog.length > 0;
  const logClass = ruled ? `mt-6 pt-5 ${STITCH_TOP}` : visibleLog.length > 0 || state.log.length === 0 ? "mt-6" : "mt-3";

  const openButton = sceneHref ? (
    <ButtonLink href={sceneHref} className="gap-1.5">
      {openLabel(subject)} <span aria-hidden="true">&rarr;</span>
    </ButtonLink>
  ) : null;

  const log = (
    <div
      id={feedId}
      role="log"
      aria-live="polite"
      aria-relevant="additions text"
      aria-label="What the studio is doing"
      className={logClass}
    >
      {state.phase === "connecting" && state.log.length === 0 && (
        <p className="font-hand text-xl leading-snug text-ink-soft">Threading the needle. This takes a moment.</p>
      )}

      {hiddenCount > 0 && (
        <button
          type="button"
          aria-expanded={showAll}
          aria-controls={feedId}
          onClick={() => setShowAll((v) => !v)}
          className={`mb-1 -ml-1 inline-flex min-h-11 items-center rounded-paper px-1 text-sm font-semibold text-accent-deep underline-offset-[0.2em] transition-colors duration-150 ease-paper hover:underline ${FOCUS_RING}`}
        >
          {toggleLabel(state, showAll, hiddenCount)}
        </button>
      )}

      {visibleLog.length > 0 && (
        <ol className="space-y-3.5">
          <AnimatePresence initial={false}>
            {visibleLog.map((entry) => (
              <motion.li
                key={entry.seq}
                layout="position"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.32, ease: PAPER_EASE }}
              >
                <Entry
                  entry={entry}
                  step={entry.kind === "step" ? stepsByKey.get(entry.stepKey) : undefined}
                  state={state}
                  mediaUrl={mediaUrl}
                />
              </motion.li>
            ))}
          </AnimatePresence>
        </ol>
      )}
    </div>
  );

  return (
    <MotionConfig reducedMotion="user">
      <section
        aria-labelledby={headingId}
        className="relative w-full max-w-xl rounded-paper border border-rule bg-paper-light px-5 py-7 text-ink shadow-lift sm:px-8 sm:py-8"
      >
        <header>
          <h2 id={headingId} className="font-display text-[1.625rem] leading-tight sm:text-[1.875rem]">
            {finished ? finishedHeading(subject) : workingHeading(subject)}
          </h2>
          {line !== null && (
            <p className="mt-2 flex items-center gap-2 text-sm text-ink-soft">
              <PhaseMark phase={state.phase} reconnecting={state.reconnecting === true} />
              <span>{line}</span>
            </p>
          )}
        </header>

        {finished ? (
          // One heading, one sentence, the way in; the steps fold away below.
          <>
            <p className="mt-3 text-[1.0625rem] leading-relaxed text-pretty text-ink-soft">{finishedNote(state, subject)}</p>
            {sceneReady && openButton && (
              // One primary way in; watching the scene is the quiet second.
              // Side by side where there is room; on a phone the link sits
              // under the button, its text flush with the button's edge.
              <div className="mt-5 flex flex-col items-start gap-1 sm:flex-row sm:items-center sm:gap-3">
                {openButton}
                {watchHref && (
                  <ButtonLink variant="quiet" href={watchHref} className="-ml-3 gap-1.5 sm:ml-0">
                    <PlayIcon className="size-3 shrink-0" />
                    {watchLabel()}
                  </ButtonLink>
                )}
              </div>
            )}
            {log}
          </>
        ) : (
          <>
            {state.todos.length > 0 && (
              <PlanList todos={state.todos} still={state.phase === "waiting" || state.phase === "error"} />
            )}

            {log}

            {children != null && children !== false && (
              <motion.div
                className={`mt-7 pt-7 ${STITCH_TOP}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, ease: PAPER_EASE }}
              >
                {children}
              </motion.div>
            )}

            {sceneReady && (
              <div className={`mt-7 pt-6 ${STITCH_TOP}`}>
                <p className="flex items-start gap-2.5 font-display text-xl leading-snug">
                  <CheckIcon className={`mt-1.5 size-4 shrink-0 ${DONE_TEXT}`} />
                  <span>{readyLine(subject)}</span>
                </p>
                <p className="mt-1 pl-[1.625rem] text-sm text-ink-soft">You can open it now.</p>
                {openButton && <div className="mt-4 pl-[1.625rem]">{openButton}</div>}
              </div>
            )}

            {state.phase === "error" && (
              <div
                role="alert"
                className="mt-7 rounded-card border border-accent/30 border-l-[3px] border-l-accent bg-accent/8 px-4 py-3.5 text-accent-deep"
              >
                <p className="font-semibold leading-snug">{state.error ?? "The studio stopped unexpectedly."}</p>
                {recovery === "start_over" ? (
                  <>
                    <p className="mt-1.5 text-[0.9375rem] leading-snug text-pretty text-ink-soft">
                      {startOverNote(hasStartOver)}
                    </p>
                    {hasStartOver && <div className="mt-3">{startOver}</div>}
                  </>
                ) : (
                  onRetry && (
                    <Button variant="secondary" onClick={onRetry} className="mt-3">
                      Retry
                    </Button>
                  )
                )}
              </div>
            )}
          </>
        )}
      </section>
    </MotionConfig>
  );
}

function PhaseMark({ phase, reconnecting }: { phase: RunPhase; reconnecting: boolean }) {
  // phaseLine says "Reconnecting…" then, whatever the phase: the mark keeps turning.
  if (reconnecting && phase !== "done" && phase !== "error") return <StitchSpinner />;
  if (phase === "done" || phase === "scene_ready") {
    return <CheckIcon className={`size-3.5 shrink-0 ${DONE_TEXT}`} />;
  }
  if (phase === "error" || phase === "waiting") {
    return <span aria-hidden="true" className="inline-block size-2 shrink-0 rounded-full bg-accent" />;
  }
  return <StitchSpinner />;
}

/** A small dashed ring that slowly turns, like a running stitch. Still under reduced motion. */
function StitchSpinner({ still = false }: { still?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block size-3.5 shrink-0 rounded-full border-[1.5px] border-dashed border-accent bg-paper-light ${still ? "" : "motion-safe:animate-[spin_3.5s_linear_infinite]"}`}
    />
  );
}

/** A small rounded play mark for the watch link. */
function PlayIcon({ className }: { className?: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 12 12" fill="currentColor" className={className}>
      <path d="M3 1.9c0-.6.66-.97 1.17-.65l6.1 3.9c.47.3.47 1 0 1.3l-6.1 3.9A.77.77 0 0 1 3 9.7z" />
    </svg>
  );
}

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" className={className}>
      <path d="M3 8.5l3.2 3L13 4.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * The plan: the run's one progress list, its marks strung on a dashed thread.
 * Marks are not form controls: a small hollow dot to do, the stitch spinner in
 * progress, a plain check when done.
 */
function PlanList({ todos, still }: { todos: PlanItem[]; still: boolean }) {
  const headingId = useId();
  return (
    <div className="mt-6">
      <h3 id={headingId} className="font-sans text-xs font-bold tracking-[0.16em] text-ink-soft uppercase">
        The plan
      </h3>
      <ul
        aria-labelledby={headingId}
        className="relative mt-3 space-y-2 before:absolute before:top-2.5 before:bottom-2.5 before:left-[7px] before:border-l-[1.5px] before:border-dashed before:border-rule before:content-['']"
      >
        {todos.map((todo, i) => {
          const status = todo.status ?? "pending";
          return (
            <li key={`${i}-${todo.text}`} className="relative flex items-start gap-3 text-[0.9375rem] leading-snug">
              <span className="flex h-[1.3rem] w-4 shrink-0 items-center justify-center">
                {status === "completed" ? (
                  <span className="flex size-4 items-center justify-center rounded-full bg-paper-light">
                    <CheckIcon className={`size-3.5 ${DONE_TEXT}`} />
                  </span>
                ) : status === "in_progress" ? (
                  <StitchSpinner still={still} />
                ) : (
                  <span aria-hidden="true" className="size-2 rounded-full border-[1.5px] border-ink-faint bg-paper-light" />
                )}
              </span>
              <span className={status === "completed" ? "text-ink-soft" : status === "in_progress" ? "font-semibold text-ink" : "text-ink"}>
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
  state,
  mediaUrl,
}: {
  entry: FeedEntry;
  step: FeedStep | undefined;
  state: RunState;
  mediaUrl: (mediaId: string) => string;
}) {
  const { phase } = state;
  switch (entry.kind) {
    case "step": {
      if (!step) return null;
      // A finished run closes every step; a paused or failed run stops the spinners.
      const done = step.status === "done" || phase === "done";
      const moving = !done && phase !== "waiting" && phase !== "error";
      const detail = stepDetail(state, step);
      return (
        <div className="flex items-start gap-3">
          <span className="flex h-6 w-4 shrink-0 items-center justify-center">
            {done ? (
              <CheckIcon className={`size-3.5 ${DONE_TEXT}`} />
            ) : moving ? (
              <StitchSpinner />
            ) : (
              <span aria-hidden="true" className="size-3.5 rounded-full border-[1.5px] border-dashed border-ink-faint" />
            )}
          </span>
          <div className="min-w-0">
            <p className={done ? "text-ink" : "font-semibold text-ink"}>
              {stepLabel(step.name)}
              <span className="sr-only">{done ? " (done)" : moving ? " (working)" : " (paused)"}</span>
            </p>
            {detail && <p className="mt-0.5 text-sm leading-snug text-ink-soft">{detail}</p>}
          </div>
        </div>
      );
    }

    case "compile_error":
      return <Note>{compileNote(entry.event)}</Note>;

    case "frames":
      return (
        <figure className="m-0 pl-7">
          <ul className="flex flex-wrap gap-3 pt-3 sm:gap-4" aria-label="Frames the checker looked at">
            {entry.event.frames.map((f, i) => (
              <li key={`${i}-${f.mediaId}`}>
                {/* Media comes from the API with the session cookie, so Photo uses a plain <img>. */}
                <Photo
                  src={mediaUrl(f.mediaId)}
                  alt={`Checker frame at ${formatSeconds(f.t)}s`}
                  width={88}
                  aspect={14 / 9}
                  rotate={i % 2 === 0 ? -1.5 : 1.2}
                />
              </li>
            ))}
          </ul>
          {entry.event.critique && (
            <figcaption className="mt-2.5 font-hand text-xl leading-snug text-ink-soft">
              {entry.event.critique}
            </figcaption>
          )}
        </figure>
      );

    case "asked":
      // Shown only while the question is unanswered and its card is not up
      // (the connection dropped mid-pause, say).
      return (
        <Note>
          <span className="text-ink-soft">The studio asked: </span>
          <span className="font-hand text-xl leading-tight text-ink">“{entry.text}”</span>
        </Note>
      );

    case "answered": {
      const { prompt, text } = answeredLine(entry);
      return (
        <Note>
          {prompt && (
            <span className="block text-ink-soft">
              {entry.answer === "answer" ? (
                <>
                  The studio asked: <span className="font-hand text-xl leading-tight text-ink">“{prompt}”</span>
                </>
              ) : (
                prompt
              )}
            </span>
          )}
          <span className={prompt ? "mt-0.5 block" : undefined}>{text}</span>
        </Note>
      );
    }

    case "props":
    case "merge":
      return <Note>{pauseLine(entry)}</Note>;

    case "scene_ready":
      return <Note tone="good">The scene is stitched together.</Note>;

    case "error":
      return <Note tone="bad">{entry.message}</Note>;

    default:
      return null;
  }
}

function Note({ children, tone = "plain" }: { children: ReactNode; tone?: "plain" | "good" | "bad" }) {
  const dot = tone === "good" ? DONE_FILL : tone === "bad" ? "bg-accent" : "bg-ink-faint";
  return (
    <div className="flex items-start gap-3 text-[0.9375rem] leading-snug">
      <span className="flex h-[1.3rem] w-4 shrink-0 items-center justify-center">
        <span aria-hidden="true" className={`size-1.5 rounded-full ${dot}`} />
      </span>
      <div className={`min-w-0 ${tone === "bad" ? "font-medium text-accent-deep" : "text-ink"}`}>{children}</div>
    </div>
  );
}
