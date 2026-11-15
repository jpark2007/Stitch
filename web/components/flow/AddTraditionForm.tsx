"use client";

// The "Add a tradition" form, set like a specimen card: its name and its story
// large in the serif on the paper, then a ruled table of details, one entry
// per row on the same rhythm: a short tracked label in a narrow column (KIND,
// HOW OFTEN, WHO, PHOTOS, SINCE) and the answer beside it, chosen as words
// rather than chips. Each control keeps its full accessible name ("What kind
// of tradition is it?"); the short label is presentation.
// Presentational: the page owns the API calls and passes them in as onSubmit
// (the checked draft and the photo files); a rejected ApiError comes back here
// as field errors (photos too big or the wrong type land on the photos field)
// or a form-level message. What was typed is never cleared by an error. On
// success the form stays busy: the page is about to leave.
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { isApiError } from "@/lib/api";
import { Button, FOCUS_RING_WITHIN } from "./Button";
import { FIELD_ERROR_CLASS, Field, TextAreaField, fieldControlClass } from "./Field";
import { FormError, focusFirstError } from "./FormError";
import { ChosenText, PersonPicker } from "./PersonPicker";
import { PhotoUpload } from "./PhotoUpload";
import {
  CATEGORY_OPTIONS,
  MONTH_NAMES,
  PHOTOS_MAX,
  START_YEAR_MIN,
  WHEN_OPTIONS,
  emptyTraditionDraft,
  labelPlaceholder,
  monthMakesSense,
  traditionServerErrors,
  traditionSubmitErrorMessage,
  validateTraditionDraft,
  type TraditionDraft,
  type TraditionField,
} from "./traditionForm";
import type { FieldErrors } from "./validation";
import { YearInput } from "./YearInput";

const GENERIC_ERROR = "That didn't save. Everything you wrote is still here: try saving it again.";
const PHOTOS_TOO_BIG = "Those photos are too big to send. Try fewer or smaller ones.";
const PHOTOS_WRONG_TYPE = "One of those photos isn't a kind we can use. Try a JPEG, PNG, WebP, HEIC or GIF.";

/** The invitation in the empty name line; short enough to fit a phone. */
const TITLE_PLACEHOLDER = "What do you call it?";
/** The invitation in the empty story: it is also the page's only instruction. */
const STORY_PLACEHOLDER =
  "Tell how it goes: who started it, what everyone brings, the one thing nobody may skip…";

// The card's two heroes. `!`: these beat the kit control's own sans text style
// whatever the CSS order. The name is the card's headline, on its own ruled
// line; the story a large, comfortable serif area whose line is also the top
// rule of the details below.
// Its empty invitation is set like the other examples on the card: italic,
// at regular weight, so it never reads as a typed name.
const TITLE_CLASS =
  "font-display! text-[1.625rem]! leading-[1.25]! font-[560]! tracking-[-0.01em] pb-2! sm:text-[1.875rem]! " +
  "placeholder:font-normal placeholder:italic";
const STORY_CLASS = "font-serif! text-[1.1875rem]! leading-[1.65]! sm:text-[1.25rem]! pb-4!";

// The details table. One grid for every row (from md up): a 6.5rem label
// column and the value column, 1.5rem apart, each row's label sitting on the
// baseline of the first line of its answer. Rhythm unit 4px: every row has
// 12px above and below its 44px lines, and a hairline between rows.
// In a narrow column that is a size container (the book desk, beside its
// book) the rows stack as on a phone; elsewhere the container rules never apply.
const GRID = "md:grid md:grid-cols-[6.5rem_minmax(0,1fr)] md:gap-x-6 @max-[34rem]:block";
const ROW = `${GRID} md:items-baseline border-t border-rule py-3 first:border-t-0`;
/** A row's short label: small tracked capitals, like the "Stitch" mark, in ink-soft (not faint) for contrast. */
const LABEL_CLASS =
  "block pt-2 pb-1.5 font-sans text-[0.72rem] leading-4 font-bold tracking-[0.16em] uppercase md:pt-0 md:pb-0 @max-[34rem]:pt-2 @max-[34rem]:pb-1.5";
/** Values are set in the reading serif. */
const VALUE_TEXT = "font-serif text-[0.9375rem] leading-snug";
const ERROR_RED = "text-[#9c3b2a]";

export function AddTraditionForm(props: {
  people: Array<{ id: string; name: string; born?: number | null; color?: string | null }>;
  /** The author's person in this family, ticked to start with; null when they are not in the tree. */
  selfPersonId: string | null;
  disabled?: boolean;
  submitLabel?: string;
  /** Save it. Resolve once saved (the page then leaves); reject with the ApiError to show it here. */
  onSubmit: (draft: TraditionDraft, photos: File[]) => Promise<void>;
  /**
   * What the form holds now, told after every change (and once to start
   * with), for the book beside it. The photos array is the same one until
   * the photos change.
   */
  onPreview?: (draft: TraditionDraft, photos: File[]) => void;
}) {
  const { people, selfPersonId, disabled = false, submitLabel = "Save this tradition", onSubmit, onPreview } = props;

  const [draft, setDraft] = useState<TraditionDraft>(() => emptyTraditionDraft(selfPersonId));
  const [photos, setPhotos] = useState<File[]>([]);
  const [errors, setErrors] = useState<FieldErrors<TraditionField>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  // Counts sends, so the photo field drops notes about an earlier pick.
  const [attempt, setAttempt] = useState(0);
  // Bumped after a failed submit; the effect moves focus once errors render.
  const [focusTick, setFocusTick] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (focusTick > 0) focusFirstError(formRef.current);
  }, [focusTick]);

  useEffect(() => {
    onPreview?.(draft, photos);
  }, [onPreview, draft, photos]);

  const locked = disabled || pending;
  const showMonth = monthMakesSense(draft.kind);

  function clearError(...keys: TraditionField[]) {
    if (!keys.some((k) => k in errors)) return;
    setErrors((e) => {
      const next = { ...e };
      for (const k of keys) delete next[k];
      return next;
    });
  }

  function update<K extends keyof TraditionDraft>(key: K, value: TraditionDraft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
    // A field's message goes away as soon as it is edited.
    clearError(key as TraditionField);
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (locked) return;
    setAttempt((n) => n + 1);

    const local = validateTraditionDraft(draft, photos.length);
    if (Object.keys(local).length > 0) {
      setErrors(local);
      setFormError(null);
      setFocusTick((n) => n + 1);
      return;
    }

    setErrors({});
    setFormError(null);
    setPending(true);
    try {
      await onSubmit(draft, photos);
      // Saved: stay busy while the page moves on.
    } catch (err) {
      if (isApiError(err)) {
        if (err.status === 413 || err.status === 415) {
          setErrors({ photos: err.status === 413 ? PHOTOS_TOO_BIG : PHOTOS_WRONG_TYPE });
          setFormError(null);
        } else if (err.status !== 400 && err.status !== 422) {
          // Not about a field: say what happened, and that nothing typed is lost.
          setErrors({});
          setFormError(traditionSubmitErrorMessage(err));
        } else {
          const { fields, rest } = traditionServerErrors(err.fieldErrors);
          // A month error on a hidden month can only be read at the form level.
          if (!showMonth && fields.month) {
            const msg = fields.month;
            delete fields.month;
            const anyField = Object.keys(fields).length > 0;
            setErrors(fields);
            setFormError([rest, msg].filter(Boolean).join(" ") || (anyField ? null : err.detail));
          } else {
            const anyField = Object.keys(fields).length > 0;
            setErrors(fields);
            setFormError(rest ?? (anyField ? null : err.detail));
          }
        }
      } else {
        setErrors({});
        setFormError(GENERIC_ERROR);
      }
      setPending(false);
      setFocusTick((n) => n + 1);
    }
  }

  return (
    <form
      ref={formRef}
      noValidate
      onSubmit={handleSubmit}
      aria-busy={pending || undefined}
      className="min-w-0"
    >
      <fieldset disabled={locked} className="m-0 min-w-0 border-0 p-0">
        {/* The name, then the story: one quiet prominent line and a large,
            borderless writing area, as on the add-a-memory page. Their labels
            are for assistive tech; nothing is starred: what is missing is
            said beside the field when someone saves without it. */}
        <Field label="What do you call it?" labelHidden required error={errors.title}>
          {(c) => (
            <input
              {...c}
              type="text"
              name="title"
              autoComplete="off"
              placeholder={TITLE_PLACEHOLDER}
              value={draft.title}
              onChange={(e) => update("title", e.target.value)}
              disabled={locked}
              className={fieldControlClass(c["aria-invalid"] === true, TITLE_CLASS)}
            />
          )}
        </Field>

        <div className="mt-6">
          <TextAreaField
            label="The story of it"
            optionalLabel
            labelHidden
            placeholder={STORY_PLACEHOLDER}
            name="description"
            rows={5}
            value={draft.description}
            onChange={(e) => update("description", e.target.value)}
            error={errors.description}
            disabled={locked}
            className={STORY_CLASS}
          />
        </div>

        {/* The details: a ruled table under the story, whose line is its
            top rule. Each visible label is presentation (hidden from
            assistive tech); every control names itself in full. */}
        <div>
          <SpecRow label="Kind" error={!!errors.category}>
            <ChoiceLine
              legend="What kind of tradition is it?"
              name="category"
              options={CATEGORY_OPTIONS}
              layout={KIND_LAYOUT}
              value={draft.category}
              onChange={(v) => update("category", v)}
              error={errors.category}
              disabled={locked}
            />
          </SpecRow>

          <SpecRow label="How often" error={!!(errors.kind || errors.label || errors.month)}>
            <ChoiceLine
              legend="How often does it happen?"
              name="kind"
              options={WHEN_OPTIONS}
              layout={WHEN_LAYOUT}
              value={draft.kind}
              onChange={(v) => {
                setDraft((d) => ({ ...d, kind: v }));
                clearError("kind", "month");
              }}
              error={errors.kind}
              disabled={locked}
            />
            {/* Then the sentence's blanks: the words for it, and its month
                when a month makes sense. */}
            <div className="mt-1 flex flex-wrap items-start gap-x-8 gap-y-2">
              <Blank
                label="When, in your words (optional)"
                caption="in your words"
                error={errors.label}
                className="w-full sm:w-[16rem]"
              >
                {(c) => (
                  <input
                    {...c}
                    type="text"
                    name="label"
                    autoComplete="off"
                    placeholder={labelPlaceholder(draft.kind)}
                    value={draft.label}
                    onChange={(e) => update("label", e.target.value)}
                    disabled={locked}
                    className={fieldControlClass(!!c["aria-invalid"], `w-full ${BLANK_TEXT}`)}
                  />
                )}
              </Blank>
              {showMonth ? (
                <Blank label="Which month (optional)" caption="month" error={errors.month} className="w-[10rem]">
                  {(c) => (
                    <div className="relative">
                      <select
                        {...c}
                        name="month"
                        value={draft.month}
                        onChange={(e) => update("month", e.target.value)}
                        disabled={locked}
                        className={fieldControlClass(
                          !!c["aria-invalid"],
                          `w-full cursor-pointer appearance-none pr-7 ${BLANK_TEXT} ${draft.month ? "" : "text-ink-soft!"}`,
                        )}
                      >
                        <option value="">No month</option>
                        {MONTH_NAMES.map((m, i) => (
                          <option key={m} value={String(i + 1)}>
                            {m}
                          </option>
                        ))}
                      </select>
                      <svg
                        aria-hidden="true"
                        viewBox="0 0 16 16"
                        className="pointer-events-none absolute top-1/2 right-0.5 size-4 -translate-y-1/2 text-ink-soft"
                      >
                        <path
                          d="M4 6l4 4 4-4"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.6"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    </div>
                  )}
                </Blank>
              ) : null}
            </div>
          </SpecRow>

          <SpecRow label="Who" error={!!errors.participantIds}>
            <PersonPicker
              layout="bare"
              appearance="plain"
              legend={
                <>
                  Who takes part?
                  <span className="sr-only"> (optional)</span>
                </>
              }
              people={people}
              value={draft.participantIds}
              onChange={(ids) => update("participantIds", ids)}
              error={errors.participantIds}
              disabled={locked}
            />
          </SpecRow>

          <SpecRow label="Photos" error={!!errors.photos}>
            <PhotoUpload
              layout="bare"
              appearance="plain"
              label={
                <>
                  Photos
                  <span className="sr-only"> (optional)</span>
                </>
              }
              max={PHOTOS_MAX}
              holder="tradition"
              files={photos}
              onChange={(files) => {
                setPhotos(files);
                clearError("photos");
              }}
              error={errors.photos}
              disabled={locked}
              attempt={attempt}
            />
          </SpecRow>

          <SpecRow label="Since" error={!!errors.startedYear}>
            <YearInput
              layout="bare"
              inputClassName={BLANK_TEXT}
              label={
                <>
                  The year it started
                  <span className="sr-only"> (optional)</span>
                </>
              }
              name="startedYear"
              // No max: reading the clock while rendering breaks prerendering.
              // validateYear checks "not in the future" on submit.
              min={START_YEAR_MIN}
              value={draft.startedYear}
              onChange={(v) => update("startedYear", v)}
              error={errors.startedYear}
            />
          </SpecRow>
        </div>
      </fieldset>

      {/* The one action, in the value column like every answer above it. */}
      <div className={`mt-10 ${GRID}`}>
        <div className="space-y-4 md:col-start-2">
          <FormError message={formError} />
          <Button
            type="submit"
            busy={pending}
            busyLabel="Saving…"
            disabled={disabled}
            className="w-full rounded-paper! px-6! shadow-none! sm:w-auto"
          >
            {submitLabel}
          </Button>
        </div>
      </div>
    </form>
  );
}

/**
 * Typed answers on the card are set in the serif, like the chosen words; an
 * example in an empty blank is italic, so it never reads as an answer.
 */
const BLANK_TEXT = "font-serif! text-[0.9375rem]! placeholder:italic";

/*
 * How a choice line lies in its column (container widths, not the window's):
 * one line from the width its words take on one line (measured in the serif
 * at 15px, chosen word's weight included, less the 8px the list hangs out
 * past the column); below that, even rows in content-sized columns; in a
 * very narrow column, two (or one) a row. Each step is reached only where
 * its own columns fit.
 */
/** The eight kinds (one line 502px; 4 + 4, 4px apart, 292px): one line of 8, else 4 + 4, else 2 a row. */
const KIND_LAYOUT =
  "grid gap-x-1 grid-cols-[repeat(2,max-content)] @min-[18rem]:grid-cols-[repeat(4,max-content)] " +
  "@min-[31rem]:flex @min-[31rem]:flex-nowrap @min-[31rem]:gap-x-1.5";
/** The five frequencies (one line 512px; 3 + 2, 4px apart, 308px): one line of 5, else 3 + 2, else one a row. */
const WHEN_LAYOUT =
  "grid gap-x-1 grid-cols-[max-content] @min-[18.75rem]:grid-cols-[repeat(3,max-content)] " +
  "@min-[32rem]:flex @min-[32rem]:flex-nowrap @min-[32rem]:gap-x-1.5";

/**
 * One entry of the details table: the short label (presentation only: each
 * control inside names itself in full) and the answer in the value column.
 * The label turns the error red when something in its row needs fixing; the
 * message itself sits beside the control.
 */
function SpecRow(props: { label: string; error?: boolean; children: ReactNode }) {
  return (
    <div className={ROW}>
      <span aria-hidden="true" className={`${LABEL_CLASS} ${props.error ? ERROR_RED : "text-ink-soft"}`}>
        {props.label}
      </span>
      <div className="min-w-0">{props.children}</div>
    </div>
  );
}

/**
 * A blank in the "how often" sentence: an underlined line with a short
 * caption under it ("in your words"). The real label, in full, is for
 * assistive tech; the caption is what the eye reads. Errors are wired as in
 * the kit's Field (described by, invalid), so focusFirstError finds them.
 */
function Blank(props: {
  label: string;
  caption: string;
  error?: string | null;
  className?: string;
  children: (control: { id: string; "aria-describedby"?: string; "aria-invalid"?: true }) => ReactNode;
}) {
  const { label, caption, error, className, children } = props;
  const uid = useId();
  const id = `${uid}control`;
  const errorId = `${id}-error`;
  const control: { id: string; "aria-describedby"?: string; "aria-invalid"?: true } = { id };
  if (error) {
    control["aria-describedby"] = errorId;
    control["aria-invalid"] = true;
  }
  return (
    <div className={`min-w-0 ${className ?? ""}`}>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      {children(control)}
      {/* Medium weight: at 12px the regular strokes render too thin for 4.5:1 on the texture. */}
      <p aria-hidden="true" className="mt-1 font-sans text-xs leading-4 font-medium text-ink-soft">
        {caption}
      </p>
      {error ? (
        <p id={errorId} className={`mt-1.5 ${FIELD_ERROR_CLASS}`}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * One choice from a short list, set as words: a fieldset of real (visually
 * hidden) radio buttons, so arrow keys move between them and the kit's
 * dashed ring shows focus. The chosen word is semibold, in full ink, with an
 * accent underline (weight and shape, not colour alone); the rest are muted
 * and only darken on hover. Its legend is for assistive tech (SpecRow
 * draws the label).
 *
 * Laid out by the width of its own column (a container query, so it holds in
 * any column the form is put in, not by the window): one line where the
 * words fit; where they do not, even rows in columns (`layout`: the eight
 * kinds as 4 + 4, the five frequencies as 3 + 2), never a word left alone
 * on a line of its own; in a very narrow column, fewer columns still.
 */
function ChoiceLine<T extends string>(props: {
  legend: ReactNode;
  name: string;
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T | null;
  onChange(value: T): void;
  error?: string | null;
  disabled?: boolean;
  /** How the words lie in the column (KIND_LAYOUT, WHEN_LAYOUT). */
  layout: string;
}) {
  const { legend, name, options, value, onChange, error, disabled, layout } = props;
  const uid = useId();
  const errorId = `${uid}-error`;

  return (
    <fieldset
      disabled={disabled}
      aria-describedby={error ? errorId : undefined}
      aria-required="true"
      className="min-w-0"
    >
      <legend className="sr-only">{legend}</legend>
      {/* The container its layout reads. -mx-1 pulls each word's padding
          out, so the words sit flush with the column. */}
      <div className="@container min-w-0">
        <ul className={`-mx-1 list-none p-0 ${layout}`}>
          {options.map((o, i) => {
            const checked = value === o.value;
            const inputId = `${uid}-${o.value}`;
            return (
              <li key={o.value} className="min-w-0">
                <label
                  htmlFor={inputId}
                  className={[
                    `group relative inline-flex min-h-11 select-none items-center rounded-paper px-1 whitespace-nowrap ${VALUE_TEXT}`,
                    "transition-colors duration-150 ease-paper motion-reduce:transition-none",
                    FOCUS_RING_WITHIN,
                    "has-[:disabled]:cursor-not-allowed",
                    checked ? "text-ink" : "text-ink-soft hover:text-ink",
                    disabled ? "opacity-60" : "cursor-pointer",
                  ].join(" ")}
                >
                  <input
                    id={inputId}
                    type="radio"
                    name={name}
                    value={o.value}
                    className="sr-only"
                    checked={checked}
                    disabled={disabled}
                    required
                    // The first word takes focus after a failed send.
                    aria-invalid={error && i === 0 ? true : undefined}
                    onChange={() => onChange(o.value)}
                  />
                  <ChosenText checked={checked} text={o.label}>
                    {o.label}
                  </ChosenText>
                </label>
              </li>
            );
          })}
        </ul>
      </div>
      {error ? (
        <p id={errorId} className={`mt-0.5 mb-1 ${FIELD_ERROR_CLASS}`}>
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}
