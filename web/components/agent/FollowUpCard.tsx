"use client";

// Answer the agent's follow-up question (AnswerRequest kind="answer").
import { useId, useRef, useState, type FormEvent } from "react";
import type { QuestionEvent } from "@/lib/types";

const GENERIC_ERROR = "That didn't send. Check your connection and try again.";

export default function FollowUpCard(props: {
  question: QuestionEvent;
  onAnswer: (text: string) => Promise<void>;
  disabled?: boolean;
}) {
  const { question, onAnswer, disabled = false } = props;
  const uid = useId();
  const fieldId = `${uid}-answer`;
  const hintId = `${uid}-hint`;
  const fieldErrorId = `${uid}-field-error`;
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const [text, setText] = useState("");
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
      className="rounded-[4px] border border-[#e4d8c4] bg-[#fffdf8] p-6 text-[#3a2e24] shadow-[0_1px_0_#efe6d6,0_10px_28px_-16px_rgba(58,46,36,0.35)] sm:p-7"
    >
      <h2 id={`${uid}-title`} className="font-serif text-lg tracking-tight">
        One quick question
      </h2>

      <figure className="mt-4 border-l-2 border-[#d9a58f] pl-4">
        <blockquote className="font-serif text-xl leading-snug italic text-[#3a2e24]">
          <p>&ldquo;{question.text}&rdquo;</p>
        </blockquote>
        <figcaption className="mt-2 text-sm text-[#7b6b5a]">
          The storyteller needs this to stage your memory.
        </figcaption>
      </figure>

      <form noValidate onSubmit={handleSubmit} className="mt-6" aria-busy={sending}>
        <label htmlFor={fieldId} className="block text-sm font-medium text-[#3a2e24]">
          Your answer
        </label>
        <p id={hintId} className="mt-0.5 text-sm text-[#7b6b5a]">
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
            if (fieldError && e.target.value.trim()) setFieldError(null);
          }}
          className={`mt-2 block w-full resize-y rounded-[3px] border bg-[#fffaf2] px-3 py-2.5 text-base leading-relaxed text-[#3a2e24] placeholder:text-[#a89886] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#a8492f] focus-visible:ring-offset-2 focus-visible:ring-offset-[#fffdf8] disabled:cursor-not-allowed disabled:opacity-70 ${
            fieldError ? "border-[#a23b2a]" : "border-[#d8cab3]"
          }`}
        />
        {fieldError ? (
          <p id={fieldErrorId} className="mt-1.5 text-sm text-[#a23b2a]">
            {fieldError}
          </p>
        ) : null}

        {submitError ? (
          <p
            role="alert"
            className="mt-4 rounded-[3px] border border-[#e8c3b9] bg-[#fbecea] px-3 py-2 text-sm text-[#7d2c1f]"
          >
            {submitError}
          </p>
        ) : null}

        <div className="mt-5 flex items-center gap-4">
          <button
            type="submit"
            disabled={locked}
            className="inline-flex items-center gap-2 rounded-full bg-[#a8492f] px-5 py-2.5 text-sm font-medium text-[#fffaf2] transition-colors hover:bg-[#8f3c26] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#a8492f] focus-visible:ring-offset-2 focus-visible:ring-offset-[#fffdf8] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {sending ? (
              <>
                <span
                  aria-hidden="true"
                  className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[#fffaf2]/40 border-t-[#fffaf2] motion-reduce:animate-none"
                />
                Sending…
              </>
            ) : sent ? (
              "Sent"
            ) : (
              "Send answer"
            )}
          </button>
          {sent ? (
            <p role="status" className="text-sm text-[#5f7a55]">
              Thanks. Picking the story back up.
            </p>
          ) : null}
        </div>
      </form>
    </section>
  );
}
