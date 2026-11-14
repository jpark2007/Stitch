"use client";

// A four-digit year field. A text input with a numeric keyboard (no spinner
// arrows); the value stays a string and the page decides what is valid.
import { useId, type ReactNode } from "react";
import {
  FIELD_ERROR_CLASS,
  FIELD_HINT_CLASS,
  FIELD_LABEL_CLASS,
  FIELD_ROW_CLASS,
  FIELD_ROW_LABEL_CLASS,
  fieldControlClass,
} from "./Field";

export function YearInput(props: {
  label: ReactNode;
  value: string;
  onChange(v: string): void;
  hint?: ReactNode;
  error?: string | null;
  required?: boolean;
  /** Earliest sensible year; read out to screen readers with `max`. */
  min?: number;
  /** Latest sensible year; read out to screen readers with `min`. */
  max?: number;
  id?: string;
  name?: string;
  /**
   * "row": the label in a left column beside the field (the add-a-memory
   * details), rather than above it. Default "stacked".
   */
  layout?: "stacked" | "row";
}) {
  const { label, value, onChange, hint, error, required, min, max, name, layout = "stacked" } = props;
  const uid = useId();
  const inputId = props.id ?? `${uid}-year`;
  const hintId = `${inputId}-hint`;
  const rangeId = `${inputId}-range`;
  const errorId = `${inputId}-error`;

  const range =
    min !== undefined && max !== undefined
      ? `A year from ${min} to ${max}.`
      : min !== undefined
        ? `A year from ${min} on.`
        : max !== undefined
          ? `A year up to ${max}.`
          : null;

  const describedBy = [hint ? hintId : null, range ? rangeId : null, error ? errorId : null]
    .filter(Boolean)
    .join(" ");

  const row = layout === "row";
  // No asterisk: the input's `required` tells assistive tech, and a missing
  // year is said beside the field when someone sends without it.
  const labelEl = (
    <label
      htmlFor={inputId}
      className={row ? `${FIELD_ROW_LABEL_CLASS} flex min-h-11 shrink-0 items-center` : FIELD_LABEL_CLASS}
    >
      {label}
      {required ? <span className="sr-only"> (required)</span> : null}
    </label>
  );
  const input = (
    <input
        id={inputId}
        name={name}
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        maxLength={4}
        autoComplete="off"
        placeholder={row ? "Year" : "YYYY"}
        required={required}
        value={value}
        // Keep only digits: a stray letter or space is dropped as it is typed.
        onChange={(e) => onChange(e.target.value.replace(/\D+/g, "").slice(0, 4))}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        // The same underlined line as TextField, just four digits wide.
        className={fieldControlClass(
          !!error,
          `${row ? "" : "mt-1 "}w-[5.5rem] tabular-nums tracking-[0.08em] placeholder:tracking-normal`,
        )}
      />
  );
  const hintEl = hint ? (
    <p id={hintId} className={`mt-0.5 ${FIELD_HINT_CLASS}`}>
      {hint}
    </p>
  ) : null;
  const rangeEl = range ? (
    <span id={rangeId} className="sr-only">
      {range}
    </span>
  ) : null;
  const errorEl = error ? (
    <p id={errorId} className={`mt-1.5 ${FIELD_ERROR_CLASS}`}>
      {error}
    </p>
  ) : null;

  if (row) {
    // Four digits fit beside the label even on a phone, so this row stays
    // inline at every width ("When was this? 1988").
    return (
      <div className={`flex items-start gap-x-4 ${FIELD_ROW_CLASS}`}>
        {labelEl}
        <div className="min-w-0">
          {input}
          {hintEl}
          {rangeEl}
          {errorEl}
        </div>
      </div>
    );
  }

  return (
    <div>
      {labelEl}
      {hintEl}
      {rangeEl}
      {input}
      {errorEl}
    </div>
  );
}
