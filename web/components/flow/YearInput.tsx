"use client";

// A four-digit year field. A text input with a numeric keyboard (no spinner
// arrows); the value stays a string and the page decides what is valid.
import { useId, type ReactNode } from "react";

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
      <label htmlFor={inputId} className="block text-sm font-medium text-[var(--ink,#2f2620)]">
        {label}
        {required ? (
          <span className="text-[var(--ink-faint,#8f8173)]">
            {" "}
            <span aria-hidden="true">*</span>
            <span className="sr-only">(required)</span>
          </span>
        ) : null}
      </label>
      {hint ? (
        <p id={hintId} className="mt-1 text-sm leading-relaxed text-[var(--ink-faint,#8f8173)]">
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
        className={[
          "mt-1.5 block h-11 w-[7.5rem] rounded-[var(--radius-paper,2px)] border px-3 text-base tabular-nums tracking-[0.12em]",
          "text-[var(--ink,#2f2620)] placeholder:tracking-normal placeholder:text-[var(--ink-faint,#8f8173)]",
          "shadow-[inset_0_1px_2px_rgb(74_47_29/0.1)] transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent,#b9573d)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--paper,#f5eee1)]",
          error
            ? "border-[#9c3b2a] bg-[#f7e4dc]"
            : "border-[var(--rule,#2f262024)] bg-[var(--paper-light,#fbf8f1)] hover:border-[var(--ink-faint,#8f8173)]",
        ].join(" ")}
      />
      {error ? (
        <p id={errorId} className="mt-1.5 text-sm text-[#9c3b2a]">
          {error}
        </p>
      ) : null}
    </div>
  );
}
