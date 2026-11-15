"use client";

// Answer the agent's follow-up question (AnswerRequest kind="answer").
// Drawn as a note from the studio taped onto the feed: the question in
// handwriting, then a plain labelled answer field and the flow kit's Button.
// What is typed is kept in sessionStorage as a draft (answerDraft), so a
// reload brings it back; it is cleared once the answer is accepted.
import { useId, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/flow/Button";
import { Tape } from "@/components/paper/Tape";
import type { QuestionEvent } from "@/lib/types";
import {
  clearAnswerDraft,
  readAnswerDraft,
  saveAnswerDraft,
  sessionDraftStore,
  type AnswerDraftTarget,
  type DraftedAnswer,
} from "./answerDraft";

const GENERIC_ERROR = "That didn't send. Check your connection and try again.";

export default function FollowUpCard(props: {
  question: QuestionEvent;
  /** useAgentRun's answerQuestion; its `draft` tag names where the draft is kept. */
  onAnswer: DraftedAnswer;
  /** Where to keep the unsent text; defaults to `onAnswer.draft`. Without either, nothing is kept. */
  draft?: AnswerDraftTarget;
  disabled?: boolean;
}) {
  const { question, onAnswer, disabled = false } = props;
  const draft = props.draft ?? onAnswer.draft ?? null;
  const uid = useId();
  const fieldId = `${uid}-answer`;
  const hintId = `${uid}-hint`;
  const fieldErrorId = `${uid}-field-error`;
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // The card mounts once per pause, after the stream showed it (never on the
  // server), so the draft is read straight into the first state.
  const [text, setText] = useState(() =>
    draft ? readAnswerDraft(sessionDraftStore(), draft.runId, draft.pauseKey) : "",
  );
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // The question text this card last answered, so a new question re-opens the form.
  const [sentFor, setSentFor] = useState<string | null>(null);
  const sent = sentFor === question.text;
  const locked = disabled || sending || sent;

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (locked) return;
    const answer = text.trim();
    if (!answer) {
      setFieldError("Write a few words before sending.");
      textareaRef.current?.focus();
      return;
    }
    setFieldError(null);
    setSubmitError(null);
    setSending(true);
    try {
      await onAnswer(answer);
      setSentFor(question.text);
      if (draft) clearAnswerDraft(sessionDraftStore(), draft.runId);
    } catch (err) {
      setSubmitError(err instanceof Error && err.message ? err.message : GENERIC_ERROR);
    } finally {
      setSending(false);
    }
  }

  const describedBy = [hintId, fieldError ? fieldErrorId : null].filter(Boolean).join(" ");

  return (
    <section
      aria-labelledby={`${uid}-title`}
      // On the book desk (/add) the question is written on the table: no card or tape of its own.
      className="relative rounded-paper border border-rule bg-paper px-5 pt-7 pb-6 text-ink shadow-paper sm:px-7 in-[[data-desk-writing]]:rounded-none! in-[[data-desk-writing]]:border-0! in-[[data-desk-writing]]:bg-transparent! in-[[data-desk-writing]]:p-0! in-[[data-desk-writing]]:shadow-none!"
    >
      <Tape
        width={92}
        height={22}
        rotate={-3}
        seed={5}
        className="absolute -top-3 left-1/2 -translate-x-1/2 in-[[data-desk-writing]]:hidden!"
      />

      <h2 id={`${uid}-title`} className="font-sans text-xs font-bold tracking-[0.16em] text-ink-soft uppercase">
        One quick question
      </h2>

      <figure className="m-0 mt-2">
        <blockquote className="font-hand text-[1.75rem] leading-[1.15] break-words text-ink">
          <p>&ldquo;{question.text}&rdquo;</p>
        </blockquote>
        <figcaption className="mt-1.5 text-sm text-ink-soft">
          The storyteller needs this to stage your memory.
        </figcaption>
      </figure>

      <form noValidate onSubmit={handleSubmit} className="mt-5" aria-busy={sending}>
        <label htmlFor={fieldId} className="block text-[0.9375rem] leading-snug font-semibold text-ink">
          Your answer
        </label>
        <p id={hintId} className="mt-0.5 text-sm leading-snug text-ink-soft">
          A sentence or two is plenty.
        </p>
        <textarea
          ref={textareaRef}
          id={fieldId}
          name="answer"
          rows={4}
          value={text}
          disabled={locked}
          required
          aria-invalid={fieldError ? true : undefined}
          aria-describedby={describedBy}
          onChange={(e) => {
            setText(e.target.value);
            if (draft) saveAnswerDraft(sessionDraftStore(), draft.runId, draft.pauseKey, e.target.value);
            if (fieldError && e.target.value.trim()) setFieldError(null);
          }}
          className={`mt-2 block min-h-11 w-full resize-y rounded-paper border bg-paper-light px-3 py-2.5 text-base leading-relaxed text-ink shadow-inset transition-[border-color] duration-150 ease-paper placeholder:text-ink-faint focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-60 ${
            fieldError ? "border-accent-deep" : "border-ink-faint hover:border-ink-soft"
          }`}
        />
        {fieldError ? (
          <p id={fieldErrorId} className="mt-1.5 text-sm leading-snug font-medium text-accent-deep">
            {fieldError}
          </p>
        ) : null}

        {submitError ? (
          <p
            role="alert"
            className="mt-4 rounded-card border border-accent/30 border-l-[3px] border-l-accent bg-accent/8 px-3.5 py-2.5 text-sm leading-snug text-accent-deep"
          >
            {submitError}
          </p>
        ) : null}

        <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2">
          <Button type="submit" disabled={disabled || sent} busy={sending} busyLabel="Sending…">
            {sent ? "Sent" : "Send answer"}
          </Button>
          {sent ? (
            <p role="status" className="font-hand text-xl leading-tight text-ink-soft">
              Thanks. Picking the story back up.
            </p>
          ) : null}
        </div>
      </form>
    </section>
  );
}
