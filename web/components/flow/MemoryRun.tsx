"use client";

// What /add shows once a memory is sent: the telling that went in, then the
// live agent run making its page. When the run pauses on the family, the
// matching card (follow-up question, prop review or merge) sits inside the
// feed; answers go back through useAgentRun to the run's answer route.
import { useEffect, useRef, useState } from "react";

import AgentFeed from "@/components/agent/AgentFeed";
import FollowUpCard from "@/components/agent/FollowUpCard";
import MergeCard from "@/components/agent/MergeCard";
import PropChips from "@/components/agent/PropChips";
import { useAgentRun, type RunSource, type UseAgentRunOptions } from "@/components/agent/useAgentRun";
import type { PendingInput } from "@/components/agent/runState";

import { ButtonLink } from "./Button";
import { addAnotherHrefFor, pendingCard, pendingKey, sceneHrefFor } from "./memoryRun";
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
  telling: MemoryRunTelling;
  mediaUrl: (mediaId: string) => string;
  sceneHref?: (memoryId: string) => string;
  onUploadPhoto?: (file: File) => Promise<string>;
  addAnotherHref?: string;
}) {
  const { source, options, telling, mediaUrl, sceneHref, onUploadPhoto, addAnotherHref } = props;
  const { state, answerQuestion, reviewProps, decideMerge, retry } = useAgentRun(source, options);

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

  // A new card takes focus and scrolls into view. The feed's own log already
  // announces the pause ("The studio asked: ..."), so no extra live region.
  // When an answered card goes away, focus would fall to <body>: hand it to
  // the feed's heading instead, without moving the page.
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
          <FollowUpCard question={pending.question} onAnswer={answerQuestion} />
        ) : pending.kind === "props" ? (
          <PropChips props={pending.props} mediaUrl={mediaUrl} onSubmit={reviewProps} onUploadPhoto={onUploadPhoto} />
        ) : (
          <MergeCard merge={pending.merge} onDecide={decideMerge} />
        )}
      </div>
    );
  }

  const another = addAnotherHrefFor(state, addAnotherHref);

  return (
    <div className="flex w-full min-w-0 flex-col gap-6">
      <TellingNote {...telling} />

      {/* The feed is a full sheet in this column, the same width as the note above it. */}
      <div ref={feedRef} className="min-w-0 [&>section]:max-w-none">
        <AgentFeed
          state={state}
          mediaUrl={mediaUrl}
          onRetry={retry}
          sceneHref={sceneHrefFor(state, sceneHref)}
        >
          {cardNode}
        </AgentFeed>
      </div>

      {another ? (
        <div className="flex justify-center">
          <ButtonLink variant="quiet" href={another}>
            Add another memory
          </ButtonLink>
        </div>
      ) : null}
    </div>
  );
}
