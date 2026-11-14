"use client";

// Approve or reject merging two people (AnswerRequest kind="merge").
import { useId, useState } from "react";
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

  const base =
    "inline-flex items-center justify-center gap-2 rounded-full px-5 py-2.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#a8492f] focus-visible:ring-offset-2 focus-visible:ring-offset-[#fffdf8] disabled:cursor-not-allowed disabled:opacity-60";

  function label(choice: Choice, text: string) {
    if (pending === choice) {
      return (
        <>
          <span
            aria-hidden="true"
            className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current/30 border-t-current motion-reduce:animate-none"
          />
          Saving…
        </>
      );
    }
    return text;
  }

  return (
    <section
      aria-labelledby={`${uid}-title`}
      aria-busy={pending !== null}
      className="rounded-[4px] border border-[#e4d8c4] bg-[#fffdf8] p-6 text-[#3a2e24] shadow-[0_1px_0_#efe6d6,0_10px_28px_-16px_rgba(58,46,36,0.35)] sm:p-7"
    >
      <h2 id={`${uid}-title`} className="font-serif text-lg tracking-tight">
        Is this someone we know?
      </h2>
      <p className="mt-3 leading-relaxed text-[#5a4a3c]">{merge.text}</p>
      <p className="mt-4 font-serif text-xl leading-snug italic">
        Is {merge.candidateName} the same person already in your tree?
      </p>

      {error ? (
        <p
          role="alert"
          className="mt-4 rounded-[3px] border border-[#e8c3b9] bg-[#fbecea] px-3 py-2 text-sm text-[#7d2c1f]"
        >
          {error}
        </p>
      ) : null}

      <div className="mt-5 flex flex-wrap gap-3">
        <button
          type="button"
          disabled={locked}
          onClick={() => decide("yes")}
          className={`${base} bg-[#a8492f] text-[#fffaf2] hover:bg-[#8f3c26]`}
        >
          {label("yes", "Yes, same person")}
        </button>
        <button
          type="button"
          disabled={locked}
          onClick={() => decide("no")}
          className={`${base} border border-[#cdbda5] bg-transparent text-[#3a2e24] hover:bg-[#f5ede0]`}
        >
          {label("no", "No, someone else")}
        </button>
      </div>

      {decided ? (
        <p role="status" className="mt-4 text-sm text-[#5f7a55]">
          {decided === "yes"
            ? `Got it. We'll keep ${merge.candidateName} as one person.`
            : `Got it. We'll add them as someone new.`}
        </p>
      ) : null}
    </section>
  );
}
