"use client";

// Approve or reject merging two people (AnswerRequest kind="merge").
// A calm two-choice prompt written straight onto the feed's sheet: no extra
// box, one question, the flow kit's primary and secondary Buttons.
import { useId, useState } from "react";
import { Button } from "@/components/flow/Button";
import type { MergeEvent } from "@/lib/types";

const GENERIC_ERROR = "That didn't go through. Check your connection and try again.";

type Choice = "yes" | "no";

export default function MergeCard(props: {
  merge: MergeEvent;
  onDecide: (approve: boolean) => Promise<void>;
}) {
  const { merge, onDecide } = props;
  const uid = useId();
  const [pending, setPending] = useState<Choice | null>(null);
  const [decided, setDecided] = useState<Choice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const locked = pending !== null || decided !== null;

  async function decide(choice: Choice) {
    if (locked) return;
    setError(null);
    setPending(choice);
    try {
      await onDecide(choice === "yes");
      setDecided(choice);
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : GENERIC_ERROR);
    } finally {
      setPending(null);
    }
  }

  return (
    <section aria-labelledby={`${uid}-title`} aria-busy={pending !== null} className="text-ink">
      <h2 id={`${uid}-title`} className="font-display text-xl leading-snug sm:text-[1.375rem]">
        Is this someone we know?
      </h2>
      <p className="mt-2 leading-relaxed text-ink-soft">{merge.text}</p>
      <p className="mt-4 font-display text-lg leading-snug">
        Is {merge.candidateName} the same person already in your tree?
      </p>

      {error ? (
        <p
          role="alert"
          className="mt-4 rounded-card border border-accent/30 border-l-[3px] border-l-accent bg-accent/8 px-3.5 py-2.5 text-sm leading-snug text-accent-deep"
        >
          {error}
        </p>
      ) : null}

      <div className="mt-5 flex flex-wrap gap-3">
        <Button
          variant="primary"
          disabled={locked}
          busy={pending === "yes"}
          busyLabel="Saving…"
          onClick={() => decide("yes")}
        >
          Yes, same person
        </Button>
        <Button
          variant="secondary"
          disabled={locked}
          busy={pending === "no"}
          busyLabel="Saving…"
          onClick={() => decide("no")}
        >
          No, someone else
        </Button>
      </div>

      {decided ? (
        <p role="status" className="mt-4 text-sm leading-snug text-ink-soft">
          {decided === "yes"
            ? `Got it. We'll keep ${merge.candidateName} as one person.`
            : `Got it. We'll add them as someone new.`}
        </p>
      ) : null}
    </section>
  );
}
