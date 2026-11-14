"use client";

// The "Add a tradition" form, laid out like the add-a-memory form: its name as
// a quiet prominent line, the story of it as a large borderless writing area,
// then compact detail rows: what kind of tradition it is, how often it happens
// (with an optional "when, in your words" and, for a yearly or seasonal one,
// the month), who takes part, photos and the year it started.
// Presentational: the page owns the API calls and passes them in as onSubmit
// (the checked draft and the photo files); a rejected ApiError comes back here
// as field errors (photos too big or the wrong type land on the photos field)
// or a form-level message. What was typed is never cleared by an error. On
// success the form stays busy: the page is about to leave.
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { isApiError } from "@/lib/api";
import { Button, FOCUS_RING_WITHIN } from "./Button";
import {
  FIELD_ERROR_CLASS,
  FIELD_ROW_CLASS,
  FIELD_ROW_LABEL_CLASS,
  Field,
  SelectField,
  TextAreaField,
  TextField,
  fieldControlClass,
} from "./Field";
import { FormError, focusFirstError } from "./FormError";
import { PersonPicker } from "./PersonPicker";
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

// The add-a-memory page's writing look. `!`: these beat the kit control's own
// sans text style whatever the CSS order. The name is a quiet, prominent serif
// line; the story a large, comfortable serif area under it.
const TITLE_CLASS = "font-serif! text-[1.375rem]! leading-snug! font-semibold sm:text-[1.5rem]!";
const STORY_CLASS = "font-serif! text-[1.1875rem]! leading-[1.65]! sm:text-[1.3125rem]! pb-3!";

export function AddTraditionForm(props: {
  people: Array<{ id: string; name: string; born?: number | null; color?: string | null }>;
  /** The author's person in this family, ticked to start with; null when they are not in the tree. */
  selfPersonId: string | null;
  disabled?: boolean;
  submitLabel?: string;
  /** Save it. Resolve once saved (the page then leaves); reject with the ApiError to show it here. */
  onSubmit: (draft: TraditionDraft, photos: File[]) => Promise<void>;
}) {
  const { people, selfPersonId, disabled = false, submitLabel = "Save this tradition", onSubmit } = props;

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

        {/* The details, quiet and compact under the story: one row each, the
            labels in a narrow column so the answers line up. */}
        <div className="mt-10 space-y-5 sm:space-y-3">
          <DetailRow label="What kind of tradition is it?">
            <ChoiceChips
              legend="What kind of tradition is it?"
              name="category"
              options={CATEGORY_OPTIONS}
              value={draft.category}
              onChange={(v) => update("category", v)}
              error={errors.category}
              disabled={locked}
            />
          </DetailRow>

          <DetailRow label="How often does it happen?">
            <ChoiceChips
              legend="How often does it happen?"
              name="kind"
              options={WHEN_OPTIONS}
              value={draft.kind}
              onChange={(v) => {
                setDraft((d) => ({ ...d, kind: v }));
                clearError("kind", "month");
              }}
              error={errors.kind}
              disabled={locked}
            />
            <div className="mt-4 grid gap-5 sm:grid-cols-[minmax(0,1fr)_minmax(0,11rem)] sm:gap-6">
              <TextField
                label="When, in your words"
                optionalLabel
                placeholder={labelPlaceholder(draft.kind)}
                name="label"
                autoComplete="off"
                value={draft.label}
                onChange={(e) => update("label", e.target.value)}
                error={errors.label}
                disabled={locked}
              />
              {showMonth ? (
                <SelectField
                  label="Which month"
                  optionalLabel
                  name="month"
                  value={draft.month}
                  onChange={(e) => update("month", e.target.value)}
                  error={errors.month}
                  disabled={locked}
                >
                  <option value="">No month</option>
                  {MONTH_NAMES.map((m, i) => (
                    <option key={m} value={String(i + 1)}>
                      {m}
                    </option>
                  ))}
                </SelectField>
              ) : null}
            </div>
          </DetailRow>

          <PersonPicker
            layout="row"
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

          <PhotoUpload
            layout="row"
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

          <YearInput
            layout="row"
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
        </div>
      </fieldset>

      <div className="mt-12 space-y-4">
        <FormError message={formError} />
        <Button type="submit" busy={pending} busyLabel="Saving…" disabled={disabled} className="w-full sm:w-auto">
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}

/**
 * A details row whose control is a group (or more than one control): the
 * label drawn in the kit's left column, hidden from assistive tech because
 * each group names itself with its own legend (a rendered <legend> cannot sit
 * in a grid cell). The same shape as PersonPicker's row layout.
 */
function DetailRow(props: { label: ReactNode; children: ReactNode }) {
  return (
    <div className={FIELD_ROW_CLASS}>
      <span aria-hidden="true" className={FIELD_ROW_LABEL_CLASS}>
        {props.label}
      </span>
      <div className="mt-2 min-w-0 sm:mt-0">{props.children}</div>
    </div>
  );
}

/**
 * One choice from a short list, as a row of chips: a fieldset of real
 * (visually hidden) radio buttons, so arrow keys move between them. Its
 * legend is for assistive tech (DetailRow draws the visible label). Drawn
 * like PersonPicker's chips, with the same stitched tick for the picked one.
 */
function ChoiceChips<T extends string>(props: {
  legend: ReactNode;
  name: string;
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T | null;
  onChange(value: T): void;
  error?: string | null;
  disabled?: boolean;
}) {
  const { legend, name, options, value, onChange, error, disabled } = props;
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
      <ul className="flex list-none flex-wrap gap-2 p-0">
        {options.map((o, i) => {
          const checked = value === o.value;
          const inputId = `${uid}-${o.value}`;
          return (
            <li key={o.value}>
              <label
                htmlFor={inputId}
                className={[
                  "relative inline-flex min-h-11 select-none items-center gap-2 rounded-full border px-3.5 text-sm transition-colors duration-150 ease-paper motion-reduce:transition-none",
                  FOCUS_RING_WITHIN,
                  "has-[:disabled]:cursor-not-allowed",
                  // Flat, as PersonPicker's: picked is a filled chip with a tick, not picked a hairline.
                  checked
                    ? "border-ink-soft/40 bg-paper-deep text-ink"
                    : error
                      ? "border-[#9c3b2a]/60 bg-transparent text-ink-soft hover:text-ink"
                      : "border-rule bg-transparent text-ink-soft hover:border-ink-faint hover:text-ink",
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
                  // The first chip takes focus after a failed send.
                  aria-invalid={error && i === 0 ? true : undefined}
                  onChange={() => onChange(o.value)}
                />
                <span>{o.label}</span>
                <TickMark checked={checked} />
              </label>
            </li>
          );
        })}
      </ul>
      {error ? (
        <p id={errorId} className={`mt-1.5 ${FIELD_ERROR_CLASS}`}>
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

/** A small stitched tick, so "picked" never relies on colour alone. */
function TickMark({ checked }: { checked: boolean }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 12 12"
      className={`h-3 w-3 shrink-0 text-accent-deep transition-opacity ${checked ? "opacity-100" : "opacity-0"}`}
    >
      <path
        d="M2 6.5 4.8 9 10 3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
