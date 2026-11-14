"use client";

// A four-digit year field. A text input with a numeric keyboard (no spinner
// arrows); the value stays a string and the page decides what is valid.
import { useId, type ReactNode } from "react";
import { FIELD_ERROR_CLASS, FIELD_HINT_CLASS, FIELD_LABEL_CLASS, fieldControlClass } from "./Field";

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
}) {
  const { label, value, onChange, hint, error, required, min, max, name } = props;
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

  return (
    <div>
      <label htmlFor={inputId} className={FIELD_LABEL_CLASS}>
        {label}
        {required ? (
          <span className="ml-1 font-normal text-ink-faint">
            <span aria-hidden="true">*</span>
            <span className="sr-only">(required)</span>
          </span>
        ) : null}
      </label>
      {hint ? (
        <p id={hintId} className={`mt-0.5 ${FIELD_HINT_CLASS}`}>
          {hint}
        </p>
      ) : null}
      {range ? (
        <span id={rangeId} className="sr-only">
          {range}
        </span>
      ) : null}
      <input
        id={inputId}
        name={name}
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        maxLength={4}
        autoComplete="off"
        placeholder="YYYY"
        required={required}
        value={value}
        // Keep only digits: a stray letter or space is dropped as it is typed.
        onChange={(e) => onChange(e.target.value.replace(/\D+/g, "").slice(0, 4))}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        // The same recessed well as TextField, just four digits wide.
        className={fieldControlClass(
          !!error,
          "mt-2 w-[7.5rem] tabular-nums tracking-[0.12em] placeholder:tracking-normal",
        )}
      />
      {error ? (
        <p id={errorId} className={`mt-1.5 ${FIELD_ERROR_CLASS}`}>
          {error}
        </p>
      ) : null}
    </div>
  );
}
