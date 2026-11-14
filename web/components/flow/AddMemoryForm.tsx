"use client";

// The add-a-memory form: the story first, then an optional question (folded
// away under "Answering a question?"), the year, optional photos and who was
// there. Presentational: the page owns the API call and
// passes it in as onSubmit; a rejected ApiError comes back here as field
// errors (photos too big or the wrong type land on the photos field) or a
// form-level message. What was typed is never cleared by an error.
//
// With `secondTellingOf` it adds this person's version of an existing moment
// (the draft's memoryId): the year is the moment's and its people are already
// set, so the year is shown as text and the participant picker is hidden.
// `questionArea` (from questionAreaFor) says whether the typed question field
// and the generic starters show; `askedQuestion` is a question shown read-only
// in the second-telling banner. `onDraftChange` hears every edit, so the page
// can carry what was typed into a form it remounts (a picked question, another
// family).
import Link from "next/link";
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { SubmitMemoryFields } from "@/lib/types";
import { isApiError } from "@/lib/api";
import { Button, FOCUS_RING } from "./Button";
import { FIELD_LABEL_CLASS, OptionalMark, TextAreaField, TextField } from "./Field";
import { FormError, focusFirstError } from "./FormError";
import {
  MEMORY_FIELDS,
  SUGGESTED_QUESTIONS,
  emptyDraft,
  questionAreaFor,
  submitErrorMessage,
  toSubmitFields,
  validateDraft,
  type MemoryDraft,
  type MemoryField,
  type QuestionArea,
} from "./memoryForm";
import { PaperCard } from "./PaperCard";
import { PersonPicker } from "./PersonPicker";
import { PhotoUpload } from "./PhotoUpload";
import { mergeServerErrors, type FieldErrors } from "./validation";
import { YearInput } from "./YearInput";

const GENERIC_ERROR = "That didn't save. Your story is still here: try sending it again.";
const PHOTOS_TOO_BIG = "Those photos are too big to send together. Try fewer or smaller ones.";
const PHOTOS_WRONG_TYPE = "One of those photos isn't a kind we can use. Try a JPEG, PNG, WebP, HEIC or GIF.";
const YEAR_MIN = 1850;

/** A visible asterisk; the control's `required` tells assistive tech. */
function RequiredMark() {
  return (
    <span aria-hidden="true" className="ml-1 font-normal text-ink-faint">
      *
    </span>
  );
}

export function AddMemoryForm(props: {
  people: Array<{ id: string; name: string; born?: number | null; color?: string | null }>;
  selfPersonId: string;
  initial?: Partial<MemoryDraft>;
  /** Photos the form starts with (a failed run's, brought back to tell it again). */
  initialPhotos?: File[];
  secondTellingOf?: { title: string; year: number } | null;
  /** The question a second telling answers, shown read-only in its banner. */
  askedQuestion?: string | null;
  /** This person has told the moment before: sending replaces their telling. */
  alreadyTold?: boolean;
  /** Which question controls show. Defaults to questionAreaFor with no family questions. */
  questionArea?: Pick<QuestionArea, "showQuestionField" | "showStarters">;
  disabled?: boolean;
  submitLabel?: string;
  onSubmit: (fields: SubmitMemoryFields, photos: File[]) => Promise<void>;
  /** Told after every change to the draft or the photos. */
  onDraftChange?: (draft: MemoryDraft, photos: File[]) => void;
  /**
   * Where "Someone missing? Add them to the family" goes (the add-relative
   * page, which comes back here). Shown under "Who was there?" for a new
   * memory only: a second telling's people are its moment's.
   */
  addPersonHref?: string | null;
  /** Told just before that link is followed, so the page can keep the draft. */
  onAddPerson?: () => void;
  /**
   * Someone to tick under "Who was there?" when this changes to their id
   * (just added on the add-relative page), leaving the rest of the draft as
   * it is. The value the form mounts with is not applied: the page puts that
   * one in `initial`.
   */
  tickPersonId?: string | null;
}) {
  const {
    people,
    selfPersonId,
    initial,
    initialPhotos,
    secondTellingOf = null,
    askedQuestion = null,
    alreadyTold = false,
    questionArea,
    disabled = false,
    submitLabel = "Save this memory",
    onSubmit,
    onDraftChange,
    addPersonHref = null,
    onAddPerson,
    tickPersonId = null,
  } = props;

  const [draft, setDraft] = useState<MemoryDraft>(() => emptyDraft(selfPersonId, initial));
  const [photos, setPhotos] = useState<File[]>(() => initialPhotos ?? []);
  const [errors, setErrors] = useState<FieldErrors<MemoryField>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  // Counts sends, so the photo field drops notes about an earlier pick.
  const [attempt, setAttempt] = useState(0);
  // Bumped after a failed submit; the effect moves focus once errors render.
  const [focusTick, setFocusTick] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const questionRef = useRef<HTMLInputElement>(null);
  const uid = useId();
  const suggestionsId = `${uid}-suggestions`;
  // The typed question stays folded away until asked for (or already filled).
  const [questionOpen, setQuestionOpen] = useState(() => (initial?.question ?? "").trim() !== "");
  const focusQuestionOnOpen = useRef(false);

  // Start over when the page hands in a different starting point (compared by
  // value, so a fresh object with the same contents changes nothing).
  const initKey = JSON.stringify([selfPersonId, initial ?? null]);
  const [seenKey, setSeenKey] = useState(initKey);
  if (seenKey !== initKey) {
    setSeenKey(initKey);
    setDraft(emptyDraft(selfPersonId, initial));
    setPhotos(initialPhotos ?? []);
    setErrors({});
    setFormError(null);
    setQuestionOpen((initial?.question ?? "").trim() !== "");
  }

  // Someone just added to the family: tick them, keep everything else.
  const [seenTick, setSeenTick] = useState(tickPersonId);
  if (seenTick !== tickPersonId) {
    setSeenTick(tickPersonId);
    if (tickPersonId && !secondTellingOf) {
      const id = tickPersonId;
      setDraft((d) => (d.participantIds.includes(id) ? d : { ...d, participantIds: [...d.participantIds, id] }));
    }
  }

  useEffect(() => {
    if (focusTick > 0) focusFirstError(formRef.current);
  }, [focusTick]);

  useEffect(() => {
    onDraftChange?.(draft, photos);
  }, [draft, photos, onDraftChange]);

  useEffect(() => {
    if (questionOpen && focusQuestionOnOpen.current) {
      focusQuestionOnOpen.current = false;
      questionRef.current?.focus();
    }
  }, [questionOpen]);

  const locked = disabled || pending;
  const prefilledQuestion = (initial?.question ?? "").trim() !== "";
  const area =
    questionArea ??
    questionAreaFor({ mode: secondTellingOf ? "second" : "new", openQuestions: 0, prefilledQuestion });
  const showQuestionField = area.showQuestionField;
  // An error on the question (or anything typed in it) keeps it unfolded.
  const questionShown =
    showQuestionField && (questionOpen || draft.question.trim() !== "" || Boolean(errors.question));

  function update<K extends keyof MemoryDraft>(key: K, value: MemoryDraft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
    // A field's message goes away as soon as it is edited.
    if (key in errors) {
      setErrors((e) => {
        const next = { ...e };
        delete next[key as MemoryField];
        return next;
      });
    }
  }

  /** Errors for fields this mode hides can only be read at the form level. */
  function splitHidden(fields: FieldErrors<MemoryField>, rest: string | null) {
    const hidden: MemoryField[] = [];
    if (!showQuestionField) hidden.push("question");
    if (secondTellingOf) hidden.push("year", "participantIds");
    if (hidden.length === 0) return { fields, rest };
    const shown = { ...fields };
    const extra: string[] = rest ? [rest] : [];
    for (const key of hidden) {
      const msg = shown[key];
      if (msg) {
        delete shown[key];
        if (!extra.includes(msg)) extra.push(msg);
      }
    }
    return { fields: shown, rest: extra.length ? extra.join(" ") : null };
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (locked) return;
    setAttempt((n) => n + 1);

    const current: MemoryDraft = secondTellingOf
      ? { ...draft, year: String(secondTellingOf.year) }
      : draft;
    const local = splitHidden(validateDraft(current, selfPersonId), null);
    if (Object.keys(local.fields).length > 0 || local.rest) {
      setErrors(local.fields);
      setFormError(local.rest);
      setFocusTick((n) => n + 1);
      return;
    }

    setErrors({});
    setFormError(null);
    setPending(true);
    try {
      await onSubmit(toSubmitFields(current), photos);
    } catch (err) {
      if (isApiError(err)) {
        if (err.status === 413 || err.status === 415) {
          setErrors({ photos: err.status === 413 ? PHOTOS_TOO_BIG : PHOTOS_WRONG_TYPE });
          setFormError(null);
        } else if (err.status !== 400 && err.status !== 422) {
          // Not about a field: say what happened, and that nothing typed is lost.
          setErrors({});
          setFormError(submitErrorMessage(err, Boolean(secondTellingOf)));
        } else {
          const merged = mergeServerErrors<MemoryField>({}, err.fieldErrors, MEMORY_FIELDS);
          const { fields, rest } = splitHidden(merged.fields, merged.rest);
          const anyField = Object.keys(fields).length > 0;
          setErrors(fields);
          setFormError(rest ?? (anyField ? null : err.detail));
        }
      } else {
        setErrors({});
        setFormError(GENERIC_ERROR);
      }
      setFocusTick((n) => n + 1);
    } finally {
      setPending(false);
    }
  }

  return (
    <PaperCard
      as="form"
      ref={formRef}
      noValidate
      onSubmit={handleSubmit}
      aria-busy={pending || undefined}
      className="space-y-8"
    >
      {secondTellingOf ? (
        <div className="border-l-[3px] border-accent bg-paper px-4 py-3">
          <p className="text-[1.0625rem] leading-snug font-semibold text-pretty text-ink">
            You&rsquo;re adding your version of &lsquo;{secondTellingOf.title}&rsquo; ({secondTellingOf.year})
          </p>
          <p className="mt-1 text-sm leading-snug text-ink-soft">
            The same scene will play again, told in your words.
            {alreadyTold ? " You’ve told this one before: this replaces your earlier telling." : null}
          </p>
          {askedQuestion ? (
            <p className="mt-3 border-t border-dashed border-rule pt-2.5 text-sm leading-snug text-ink-soft">
              <span className="block">You were asked</span>
              <span className="mt-0.5 block font-hand text-[1.375rem] leading-[1.15] text-pretty break-words text-ink">
                {askedQuestion}
              </span>
            </p>
          ) : null}
        </div>
      ) : null}

      {/* mx-0, not m-0: the sheet's space-y-8 gives the fieldset its bottom
          margin, and m-0 would cancel it, leaving the dashed rule over the
          send button pressed against the last field. */}
      <fieldset disabled={locked} className="mx-0 min-w-0 space-y-8 border-0 p-0">
        <div>
          {/* A second telling needs only the story, so no key to the asterisk. */}
          {secondTellingOf ? null : (
            <p className="mb-5 text-sm text-ink-soft">
              <span aria-hidden="true">* </span>
              Needed: your story and the year.
            </p>
          )}
          <TextAreaField
            label={
              <>
                Your story
                <RequiredMark />
              </>
            }
            hint="Small details help: who said what, the weather, a smell, a song."
            required
            name="text"
            rows={7}
            value={draft.text}
            onChange={(e) => update("text", e.target.value)}
            error={errors.text}
            disabled={locked}
          />

          {!showQuestionField ? null : questionShown ? (
            <div className="mt-6">
              <TextField
                ref={questionRef}
                label="What question are you answering?"
                optionalLabel
                name="question"
                autoComplete="off"
                value={draft.question}
                onChange={(e) => update("question", e.target.value)}
                error={errors.question}
                disabled={locked}
              />
              {area.showStarters ? (
                <div className="mt-3">
                  <p id={suggestionsId} className="text-sm text-ink-soft">
                    Or start from one of these
                  </p>
                  <ul
                    aria-labelledby={suggestionsId}
                    className="mt-2 flex list-none flex-wrap gap-2 p-0"
                  >
                    {SUGGESTED_QUESTIONS.slice(0, 4).map((q) => (
                      <li key={q}>
                        <SuggestionChip
                          picked={draft.question.trim() === q}
                          disabled={locked}
                          onClick={() => update("question", q)}
                        >
                          {q}
                        </SuggestionChip>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : (
            <Button
              variant="quiet"
              disabled={locked}
              onClick={() => {
                focusQuestionOnOpen.current = true;
                setQuestionOpen(true);
              }}
              className="mt-2 -ml-3"
            >
              <span aria-hidden="true" className="font-normal">
                +
              </span>
              Answering a question?
            </Button>
          )}
        </div>

        {secondTellingOf ? (
          <div>
            <p className={FIELD_LABEL_CLASS}>Year</p>
            <p className="mt-2 text-base text-ink">
              <span className="tabular-nums">{secondTellingOf.year}</span>
              <span className="text-ink-soft">, the same as the moment</span>
            </p>
          </div>
        ) : (
          <YearInput
            label="When was this?"
            hint="The year it happened. A good guess is fine."
            required
            name="year"
            // No max: reading the clock while rendering breaks prerendering.
            // validateYear checks "not in the future" on submit.
            min={YEAR_MIN}
            value={draft.year}
            onChange={(v) => update("year", v)}
            error={errors.year}
          />
        )}

        <PhotoUpload
          label={
            <>
              Photos
              <OptionalMark />
            </>
          }
          hint="Old snapshots help us draw the scene."
          files={photos}
          onChange={(files) => {
            setPhotos(files);
            if (errors.photos) {
              setErrors((e) => {
                const next = { ...e };
                delete next.photos;
                return next;
              });
            }
          }}
          error={errors.photos}
          disabled={locked}
          attempt={attempt}
        />

        {secondTellingOf ? null : (
          <div>
            <PersonPicker
              legend="Who was there?"
              hint="You're already in. Add anyone else who was part of it."
              people={people}
              value={draft.participantIds}
              onChange={(ids) => update("participantIds", ids)}
              lockedIds={[selfPersonId]}
              error={errors.participantIds}
              disabled={locked}
            />
            {addPersonHref ? (
              <p className="mt-3 text-sm leading-snug text-ink-soft">
                Someone missing?{" "}
                <Link
                  href={addPersonHref}
                  onClick={onAddPerson}
                  className={`rounded-paper font-medium text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2 ${FOCUS_RING}`}
                >
                  Add them to the family
                </Link>
              </p>
            ) : null}
          </div>
        )}
      </fieldset>

      <div className="space-y-4 border-t border-dashed border-rule pt-6">
        <FormError message={formError} />
        <Button
          type="submit"
          busy={pending}
          busyLabel="Saving…"
          disabled={disabled}
          className="w-full sm:w-auto"
        >
          {submitLabel}
        </Button>
      </div>
    </PaperCard>
  );
}

/** A small, quiet suggestion: fills the question field when pressed. */
function SuggestionChip(props: {
  picked: boolean;
  disabled?: boolean;
  onClick(): void;
  children: ReactNode;
}) {
  const { picked, disabled, onClick, children } = props;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={[
        "inline-flex min-h-9 items-center rounded-full border px-3 py-1.5 text-left text-sm leading-tight",
        "transition-colors duration-150 ease-paper motion-reduce:transition-none",
        FOCUS_RING,
        "disabled:cursor-not-allowed disabled:opacity-60",
        picked
          ? "border-ink-soft bg-paper-deep text-ink"
          : "border-rule bg-paper-light text-ink-soft not-disabled:hover:border-accent not-disabled:hover:text-ink",
      ].join(" ")}
    >
      {children}
    </button>
  );
}
