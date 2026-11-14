"use client";

// What /add shows once a memory is sent: the telling that went in, then the
// live agent run making its page. When the run pauses on the family, the
// matching card (follow-up question, prop review or merge) sits inside the
// feed; answers go back through useAgentRun to the run's answer route.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import AgentFeed from "@/components/agent/AgentFeed";
import FollowUpCard from "@/components/agent/FollowUpCard";
import MergeCard from "@/components/agent/MergeCard";
import PropChips from "@/components/agent/PropChips";
import { useAgentRun, type RunSource, type UseAgentRunOptions } from "@/components/agent/useAgentRun";
import type { PendingInput, RunState } from "@/components/agent/runState";

import { draftPauseKey } from "@/components/agent/answerDraft";
import { errorRecovery } from "@/components/agent/feedView";

import { Button, ButtonLink } from "./Button";
import {
  addAnotherHrefFor,
  openRunProgress,
  pendingCard,
  pendingKey,
  sceneHrefFor,
  tellAgainFor,
  watchHrefFor,
  type OpenRunProgress,
} from "./memoryRunState";
import { hrefs } from "./session";
import { TellingNote } from "./TellingNote";

export interface MemoryRunTelling {
  question?: string | null;
  text: string;
  year: number;
  photoCount: number;
  /** Title of the moment this is a second telling of. */
  secondTellingOf?: string | null;
}

const HEADINGS = "h1, h2, h3, h4, h5, h6";

/**
 * No focus ring on a heading this panel focused by script (tabindex="-1",
 * which no keyboard can reach): after a reload Chromium treats that focus as
 * keyboard focus and would ring the card's title. Controls keep their ring.
 */
export const SCRIPT_FOCUS_QUIET = "[&_:is(h1,h2,h3,h4,h5,h6)[tabindex='-1']]:outline-none";

/**
 * Focus `root`'s first heading (made focusable for script only), or `root`
 * itself when it has none. Never scrolls; the caller decides that.
 */
function focusHeading(root: HTMLElement | null) {
  if (!root) return;
  const target = root.querySelector<HTMLElement>(HEADINGS) ?? root;
  if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
  target.focus({ preventScroll: true });
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function MemoryRun(props: {
  source: RunSource | null;
  options?: UseAgentRunOptions;
  /**
   * Names the run (family and run id), so the answers given in this tab
   * survive a reload and an answered card does not come back (see
   * UseAgentRunOptions.runKey).
   */
  runKey?: string;
  telling?: MemoryRunTelling | null;
  mediaUrl: (mediaId: string) => string;
  /** The moment's book page, the finished run's way in. */
  sceneHref?: (memoryId: string) => string;
  /**
   * The moment's scene playing on the stage, a quiet link beside the way in
   * once the run is done. Leave it out where the stage cannot load the moment
   * (mock mode).
   */
  watchHref?: (memoryId: string) => string;
  onUploadPhoto?: (file: File) => Promise<string>;
  addAnotherHref?: string;
  /**
   * Brings the form back with what was sent, after the run ended with its own
   * error (a reconnect would only replay that error).
   */
  onTellAgain?: () => void;
  /**
   * Told each time the run starts or stops waiting on the person, and once
   * when it is over (see openRunProgress), so the page can keep its record of
   * open runs current.
   */
  onProgress?: (progress: OpenRunProgress) => void;
  /** Told the run's state each time it changes (the crafting book beside the page draws from it). */
  onState?: (state: RunState) => void;
}) {
  const {
    source,
    options,
    runKey,
    telling,
    mediaUrl,
    sceneHref,
    watchHref,
    onUploadPhoto,
    addAnotherHref,
    onTellAgain,
    onProgress,
    onState,
  } = props;
  const runOptions = useMemo(() => (runKey ? { ...options, runKey } : options), [options, runKey]);
  const { state, answerQuestion, reviewProps, decideMerge, retry } = useAgentRun(source, runOptions);

  // In the commit that shows it, before it is painted: a page that says the
  // run is stitched in has already let go of its open-run record, so leaving
  // at once (a link, a reload) can never find the run still open.
  const progress = openRunProgress(state);
  useLayoutEffect(() => {
    if (progress !== null) onProgress?.(progress);
  }, [progress, onProgress]);
  // Then whatever draws from the run's state (the crafting book).
  useEffect(() => {
    onState?.(state);
  }, [state, onState]);

  // The card's key is read once per pending input (the reducer keeps the same
  // object for the whole pause), so nothing logged mid-pause can remount the
  // card and throw away what the family has typed.
  const [held, setHeld] = useState<{ pending: PendingInput | null; key: string }>(() => ({
    pending: state.pending,
    key: pendingKey(state),
  }));
  let cardKey = held.key;
  if (held.pending !== state.pending) {
    cardKey = pendingKey(state);
    setHeld({ pending: state.pending, key: cardKey });
  }

  const card = pendingCard(state);
  const shownKey = card ? cardKey : null;

  const feedRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const lastShownKey = useRef<string | null>(null);

  // A new card takes focus and scrolls into view, which is how the pause is
  // announced: while the card is up the feed hides its own line for it, and
  // the phase line under the heading names the kind of pause. When the card
  // goes away (answered here, or found already answered after a reload),
  // focus would fall to <body>: hand it to the feed's heading instead,
  // without moving the page.
  useEffect(() => {
    const previous = lastShownKey.current;
    lastShownKey.current = shownKey;
    if (shownKey === previous) return;
    if (shownKey) {
      const el = cardRef.current;
      if (!el) return;
      el.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
      focusHeading(el);
    } else if (previous && (document.activeElement === null || document.activeElement === document.body)) {
      focusHeading(feedRef.current);
    }
  }, [shownKey]);

  if (!source) return null;

  const pending = state.phase === "waiting" ? state.pending : null;
  let cardNode = null;
  if (card && pending) {
    cardNode = (
      <div key={cardKey} ref={cardRef} className="scroll-mt-6">
        {pending.kind === "answer" ? (
          <FollowUpCard
            question={pending.question}
            onAnswer={answerQuestion}
            draft={
              runKey && state.pauseSeq !== null
                ? { runId: runKey, pauseKey: draftPauseKey("answer", state.pauseSeq) }
                : undefined
            }
          />
        ) : pending.kind === "props" ? (
          <PropChips props={pending.props} mediaUrl={mediaUrl} onSubmit={reviewProps} onUploadPhoto={onUploadPhoto} />
        ) : (
          <MergeCard merge={pending.merge} onDecide={decideMerge} />
        )}
      </div>
    );
  }

  const another = addAnotherHrefFor(state, addAnotherHref);
  // After the run's own error: send the story again, or go back to the tree,
  // so the failed state is never a dead end.
  const tellAgain = tellAgainFor(state, onTellAgain);
  const startOver =
    errorRecovery(state) === "start_over" ? (
      <div className="flex flex-wrap gap-3">
        {tellAgain ? <Button onClick={tellAgain}>Tell it again</Button> : null}
        <ButtonLink variant={tellAgain ? "quiet" : "secondary"} href={hrefs.home()}>
          Family tree
        </ButtonLink>
      </div>
    ) : undefined;

  return (
    <div className={`flex w-full min-w-0 flex-col gap-6 ${SCRIPT_FOCUS_QUIET}`}>
      {telling ? <TellingNote {...telling} /> : null}

      {/* The feed is a full sheet in this column, the same width as the note
          above it. `*:` lifts the cap on whatever element AgentFeed renders at
          its top, so a restyle that changes that tag keeps the column even. */}
      <div ref={feedRef} className="min-w-0 *:max-w-none">
        <AgentFeed
          state={state}
          mediaUrl={mediaUrl}
          onRetry={retry}
          startOver={startOver}
          sceneHref={sceneHrefFor(state, sceneHref)}
          watchHref={watchHrefFor(state, watchHref)}
          // After a reload the telling is unknown (null): call it a memory.
          subject={telling?.secondTellingOf ? "telling" : "memory"}
        >
          {cardNode}
        </AgentFeed>
      </div>

      {another ? (
        // The two ways on once the page is made, besides opening it: tell
        // another, or go back to the tree where the new page now hangs.
        // Side by side with a dot between them where there is room; stacked
        // (no dangling dot) on a phone.
        <nav aria-label="What next" className="flex flex-col items-center gap-x-2 sm:flex-row sm:justify-center">
          <ButtonLink variant="quiet" href={another}>
            Add another memory
          </ButtonLink>
          <span aria-hidden="true" className="hidden text-ink-faint sm:inline">
            &middot;
          </span>
          <ButtonLink variant="quiet" href={hrefs.home()}>
            Back to the family tree
          </ButtonLink>
        </nav>
      ) : null}
    </div>
  );
}
