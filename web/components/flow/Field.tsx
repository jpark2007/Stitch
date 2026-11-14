"use client";

// Labelled form fields for the flow pages. Field is the shell (a real
// <label htmlFor>, hint, error, all wired with aria-describedby); TextField,
// TextAreaField and SelectField wrap the native controls in it. Errors are
// plain text linked to the control (no role): the form-level FormError is the
// one alert, and focusFirstError moves focus to the first invalid control,
// whose description then reads the message.
import {
  useId,
  useState,
  type InputHTMLAttributes,
  type ReactNode,
  type Ref,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { FOCUS_RING } from "./Button";

// One text style per role for every field in the kit (Field, YearInput,
// PhotoUpload, PersonPicker, RelationshipPicker, AddMemoryForm), so labels,
// legends, hints and errors read the same everywhere. Spacing rhythm: hint
// mt-0.5 under the label, the control mt-2 under that, the error mt-1.5 below.
// Client components only: this module is "use client".

/** A field's <label>, a fieldset's <legend>, or the label of a custom control. */
export const FIELD_LABEL_CLASS = "block text-[0.9375rem] leading-snug font-semibold text-ink";

/** The quiet "(optional)" after a label. */
export const FIELD_OPTIONAL_CLASS = "ml-1.5 font-normal text-ink-soft";

/** A hint under a label. Add mt-0.5. */
export const FIELD_HINT_CLASS = "text-sm leading-snug text-ink-soft";

/**
 * A field's error message. Add mt-1.5. The error red (#9c3b2a, 6.2:1 on
 * paper-light) has no token in globals.css yet.
 */
export const FIELD_ERROR_CLASS = "text-sm leading-snug font-medium break-words text-[#9c3b2a]";

/** What Field hands its control: spread it onto the input. */
type ControlProps = {
  id: string;
  "aria-describedby"?: string;
  "aria-invalid"?: true;
  required?: boolean;
};

type FieldText = {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  /** Show a quiet "(optional)" after the label. */
  optionalLabel?: boolean;
};

function joinIds(...ids: (string | undefined | null | false)[]): string | undefined {
  const s = ids.filter(Boolean).join(" ");
  return s || undefined;
}

export function Field(
  props: FieldText & {
    required?: boolean;
    id?: string;
    children: (control: ControlProps) => ReactNode;
  },
) {
  const { label, hint, error, required, optionalLabel = false, id: givenId, children } = props;
  const uid = useId();
  const id = givenId ?? `${uid}control`;
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const hasHint = hint != null && hint !== false && hint !== "";
  const hasError = typeof error === "string" && error.trim() !== "";

  const control: ControlProps = { id };
  // The error first, so it is what a screen reader reads right after the label.
  const describedBy = joinIds(hasError && errorId, hasHint && hintId);
  if (describedBy) control["aria-describedby"] = describedBy;
  if (hasError) control["aria-invalid"] = true;
  if (required) control.required = true;

  return (
    <div className="min-w-0">
      <label htmlFor={id} className={FIELD_LABEL_CLASS}>
        {label}
        {optionalLabel ? <span className={FIELD_OPTIONAL_CLASS}>(optional)</span> : null}
      </label>
      {hasHint ? (
        <p id={hintId} className={`mt-0.5 ${FIELD_HINT_CLASS}`}>
          {hint}
        </p>
      ) : null}
      <div className="mt-2">{children(control)}</div>
      {hasError ? (
        <p id={errorId} className={`mt-1.5 ${FIELD_ERROR_CLASS}`}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

// A recessed paper well: paper-deep inset, ink text, the kit focus ring.
// The resting border is ink-faint rather than the hairline rule so the field
// edge keeps 3:1 contrast against the card (WCAG 1.4.11).
const CONTROL_BASE =
  "block min-w-0 min-h-11 rounded-paper border px-3 py-2.5 " +
  "font-sans text-base leading-normal text-ink " +
  "bg-paper-deep/45 shadow-inset placeholder:text-ink-faint " +
  "transition-[background-color,border-color] duration-150 ease-paper motion-reduce:transition-none " +
  "hover:border-ink-soft focus:bg-paper-light " +
  FOCUS_RING +
  " disabled:cursor-not-allowed disabled:opacity-60";

/**
 * The class list of a kit text control (the recessed well), for a native
 * input a page lays out itself. Full width unless `extra` sets a width.
 */
export function fieldControlClass(invalid: boolean, extra?: string): string {
  const border = invalid ? "border-[#9c3b2a] hover:border-[#9c3b2a]" : "border-ink-faint";
  const width = extra && /(^|\s)w-/.test(extra) ? "" : " w-full";
  return `${CONTROL_BASE}${width} ${border}${extra ? ` ${extra}` : ""}`;
}

const controlClass = fieldControlClass;

/** A labelled <input>. `className` goes on the input itself. */
export function TextField(
  props: FieldText & { ref?: Ref<HTMLInputElement> } & InputHTMLAttributes<HTMLInputElement>,
) {
  const {
    label,
    hint,
    error,
    optionalLabel,
    id,
    required,
    className,
    "aria-describedby": extraDescribedBy,
    ...rest
  } = props;
  return (
    <Field label={label} hint={hint} error={error} optionalLabel={optionalLabel} id={id} required={required}>
      {(c) => (
        <input
          {...rest}
          {...c}
          aria-describedby={joinIds(c["aria-describedby"], extraDescribedBy)}
          className={controlClass(c["aria-invalid"] === true, className)}
        />
      )}
    </Field>
  );
}

const countFormat = new Intl.NumberFormat("en-US");

/**
 * A labelled <textarea>, at least five rows tall, that grows with its text
 * (CSS field-sizing, where supported) up to a cap and can still be dragged
 * taller. With `maxLength` it shows "120 of 2,000 characters" under the hint.
 * `className` goes on the textarea itself.
 */
export function TextAreaField(
  props: FieldText & { ref?: Ref<HTMLTextAreaElement> } & TextareaHTMLAttributes<HTMLTextAreaElement>,
) {
  const {
    label,
    hint,
    error,
    optionalLabel,
    id,
    required,
    className,
    "aria-describedby": extraDescribedBy,
    maxLength,
    rows,
    style,
    value,
    defaultValue,
    onChange,
    ...rest
  } = props;
  const minRows = Math.max(5, rows ?? 5);
  const [typed, setTyped] = useState(() => (defaultValue == null ? 0 : String(defaultValue).length));
  const count = value == null ? typed : String(value).length;

  const counter =
    maxLength != null && maxLength > 0 ? (
      <span className="tabular-nums">
        {countFormat.format(count)} of {countFormat.format(maxLength)} characters
      </span>
    ) : null;
  const fullHint =
    counter && hint != null && hint !== false && hint !== "" ? (
      <span className="flex flex-wrap justify-between gap-x-4 gap-y-0.5">
        <span>{hint}</span> {counter}
      </span>
    ) : (
      (counter ?? hint)
    );

  return (
    <Field label={label} hint={fullHint} error={error} optionalLabel={optionalLabel} id={id} required={required}>
      {(c) => (
        <textarea
          {...rest}
          {...c}
          rows={minRows}
          maxLength={maxLength}
          value={value}
          defaultValue={defaultValue}
          onChange={(e) => {
            setTyped(e.target.value.length);
            onChange?.(e);
          }}
          aria-describedby={joinIds(c["aria-describedby"], extraDescribedBy)}
          style={{ minHeight: `calc(${minRows}lh + 1.25rem + 2px)`, ...style }}
          className={controlClass(
            c["aria-invalid"] === true,
            `field-sizing-content max-h-[28rem] resize-y leading-relaxed${className ? ` ${className}` : ""}`,
          )}
        />
      )}
    </Field>
  );
}

/** A labelled native <select> with a drawn chevron. Pass <option>s as children. */
export function SelectField(
  props: FieldText & { children: ReactNode; ref?: Ref<HTMLSelectElement> } & SelectHTMLAttributes<HTMLSelectElement>,
) {
  const {
    label,
    hint,
    error,
    optionalLabel,
    id,
    required,
    className,
    children,
    "aria-describedby": extraDescribedBy,
    ...rest
  } = props;
  return (
    <Field label={label} hint={hint} error={error} optionalLabel={optionalLabel} id={id} required={required}>
      {(c) => (
        <div className="relative">
          <select
            {...rest}
            {...c}
            aria-describedby={joinIds(c["aria-describedby"], extraDescribedBy)}
            className={controlClass(
              c["aria-invalid"] === true,
              `cursor-pointer appearance-none pr-10${className ? ` ${className}` : ""}`,
            )}
          >
            {children}
          </select>
          <svg
            aria-hidden="true"
            viewBox="0 0 16 16"
            className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-ink-soft"
          >
            <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      )}
    </Field>
  );
}
