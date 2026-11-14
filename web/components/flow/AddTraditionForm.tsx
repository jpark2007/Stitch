"use client";

// The "Add a tradition" form: its name, what kind of tradition it is, how
// often it happens (with an optional "when, in your words" and, for a yearly
// or seasonal one, the month), the story of it, who takes part, photos and the
// year it started. Built from the same kit as the add-a-memory form.
// Presentational: the page owns the API calls and passes them in as onSubmit
// (the checked draft and the photo files); a rejected ApiError comes back here
// as field errors (photos too big or the wrong type land on the photos field)
// or a form-level message. What was typed is never cleared by an error. On
// success the form stays busy: the page is about to leave.
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { isApiError } from "@/lib/api";
import { Button, FOCUS_RING_WITHIN } from "./Button";
import { FIELD_ERROR_CLASS, FIELD_HINT_CLASS, FIELD_LABEL_CLASS, OptionalMark, SelectField, TextAreaField, TextField } from "./Field";
import { FormError, focusFirstError } from "./FormError";
import { PaperCard } from "./PaperCard";
import { PersonPicker } from "./PersonPicker";
import { PhotoUpload } from "./PhotoUpload";
import {
  CATEGORY_OPTIONS,
  MONTH_NAMES,
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

/** A visible asterisk; the control's `required` tells assistive tech. */
function RequiredMark() {
  return (
    <span aria-hidden="true" className="ml-1 font-normal text-ink-faint">
      *
    </span>
  );
}

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
    <PaperCard
      as="form"
      ref={formRef}
      noValidate
      onSubmit={handleSubmit}
      aria-busy={pending || undefined}
      className="space-y-8"
    >
      {/* mx-0, not m-0: the sheet's space-y-8 gives the fieldset its bottom margin. */}
      <fieldset disabled={locked} className="mx-0 min-w-0 space-y-8 border-0 p-0">
        <div>
          <p className="mb-5 text-sm text-ink-soft">
            <span aria-hidden="true">* </span>
            Needed: its name, what kind it is and how often it happens.
          </p>
          <TextField
            label={
              <>
                What do you call it?
                <RequiredMark />
              </>
            }
            hint="The name the family would know it by."
            placeholder="Tamales on Christmas Eve"
            required
            name="title"
            autoComplete="off"
            value={draft.title}
            onChange={(e) => update("title", e.target.value)}
            error={errors.title}
            disabled={locked}
          />
        </div>

        <ChoiceChips
          legend={
            <>
              What kind of tradition is it?
              <RequiredMark />
            </>
          }
          name="category"
          options={CATEGORY_OPTIONS}
          value={draft.category}
          onChange={(v) => update("category", v)}
          error={errors.category}
          disabled={locked}
        />

        <div className="space-y-6">
          <ChoiceChips
            legend={
              <>
                How often does it happen?
                <RequiredMark />
              </>
            }
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
          <div className="grid gap-6 sm:grid-cols-[minmax(0,1fr)_minmax(0,13rem)]">
            <TextField
              label="When, in your words"
              optionalLabel
              hint={`Like “Christmas Eve” or “every Sunday”.`}
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
                hint="If it falls in one."
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
        </div>

        <TextAreaField
          label="The story of it"
          optionalLabel
          hint="How it goes, who started it, what nobody may skip."
          name="description"
          rows={6}
          value={draft.description}
          onChange={(e) => update("description", e.target.value)}
          error={errors.description}
          disabled={locked}
        />

        <PersonPicker
          legend={
            <>
              Who takes part?
              <OptionalMark />
            </>
          }
          hint="Everyone who keeps it going."
          people={people}
          value={draft.participantIds}
          onChange={(ids) => update("participantIds", ids)}
          error={errors.participantIds}
          disabled={locked}
        />

        <PhotoUpload
          label={
            <>
              Photos
              <OptionalMark />
            </>
          }
          hint="Snapshots from any year it happened."
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
          label={
            <>
              The year it started
              <OptionalMark />
            </>
          }
          hint="A good guess is fine."
          name="startedYear"
          // No max: reading the clock while rendering breaks prerendering.
          // validateYear checks "not in the future" on submit.
          min={START_YEAR_MIN}
          value={draft.startedYear}
          onChange={(v) => update("startedYear", v)}
          error={errors.startedYear}
        />
      </fieldset>

      <div className="space-y-4 border-t border-dashed border-rule pt-6">
        <FormError message={formError} />
        <Button type="submit" busy={pending} busyLabel="Saving…" disabled={disabled} className="w-full sm:w-auto">
          {submitLabel}
        </Button>
      </div>
    </PaperCard>
  );
}

/**
 * One choice from a short list, as a row of paper chips: a fieldset of real
 * (visually hidden) radio buttons, so arrow keys move between them. Drawn
 * like PersonPicker's chips, with the same stitched tick for the picked one.
 */
function ChoiceChips<T extends string>(props: {
  legend: ReactNode;
  name: string;
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T | null;
  onChange(value: T): void;
  hint?: ReactNode;
  error?: string | null;
  disabled?: boolean;
}) {
  const { legend, name, options, value, onChange, hint, error, disabled } = props;
  const uid = useId();
  const hintId = `${uid}-hint`;
  const errorId = `${uid}-error`;
  const describedBy = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(" ");

  return (
    <fieldset
      disabled={disabled}
      aria-describedby={describedBy || undefined}
      aria-required="true"
      className="min-w-0"
    >
      <legend className={`p-0 ${FIELD_LABEL_CLASS}`}>{legend}</legend>
      {hint ? (
        <p id={hintId} className={`mt-0.5 ${FIELD_HINT_CLASS}`}>
          {hint}
        </p>
      ) : null}
      <ul className="mt-2 flex list-none flex-wrap gap-2 p-0">
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
                  checked
                    ? "border-ink-soft bg-paper-deep text-ink shadow-inset"
                    : error
                      ? "border-[#9c3b2a]/60 bg-paper-light text-ink-soft hover:border-accent"
                      : "border-rule bg-paper-light text-ink-soft shadow-[0_1px_1px_rgb(74_47_29/0.08)] hover:border-accent",
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
