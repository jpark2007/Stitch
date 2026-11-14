"use client";

// Pick people from the family (who was there for this memory). A plain
// fieldset of real checkboxes, drawn as paper chips with each person's
// thread colour. Colours are the global look's theme utilities; text styles
// and the focus ring are the kit's shared ones.
import { useId, type ReactNode } from "react";
import { FOCUS_RING_WITHIN } from "./Button";
import { FIELD_ERROR_CLASS, FIELD_HINT_CLASS, FIELD_LABEL_CLASS } from "./Field";

interface PickablePerson {
  id: string;
  name: string;
  born?: number | null;
  /** Thread colour: Person.character.color or PersonSummary.color. */
  color?: string | null;
}

/** Born year shown only when two people share a name, to tell them apart. */
function sharedNames(people: PickablePerson[]): Set<string> {
  const counts = new Map<string, number>();
  for (const p of people) {
    const key = p.name.trim().toLowerCase();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return new Set([...counts].filter(([, n]) => n > 1).map(([k]) => k));
}

export function PersonPicker(props: {
  legend: ReactNode;
  people: PickablePerson[];
  value: string[];
  onChange(ids: string[]): void;
  /** Always picked and can't be unpicked (the author). Shown with "(you)". */
  lockedIds?: string[];
  hint?: ReactNode;
  error?: string | null;
  disabled?: boolean;
}) {
  const { legend, people, value, onChange, hint, error, disabled } = props;
  const lockedIds = props.lockedIds ?? [];
  const uid = useId();
  const hintId = `${uid}-hint`;
  const errorId = `${uid}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ");
  const duplicates = sharedNames(people);

  function toggle(id: string, checked: boolean) {
    if (lockedIds.includes(id)) return;
    const rest = value.filter((v) => v !== id);
    const next = checked ? [...rest, id] : rest;
    // Locked people always stay in the answer, even if the caller forgot them.
    for (const locked of lockedIds) if (!next.includes(locked)) next.unshift(locked);
    onChange(next);
  }

  return (
    <fieldset
      disabled={disabled}
      aria-describedby={describedBy || undefined}
      className="min-w-0"
    >
      <legend className={`p-0 ${FIELD_LABEL_CLASS}`}>{legend}</legend>
      {hint ? (
        <p id={hintId} className={`mt-0.5 ${FIELD_HINT_CLASS}`}>
          {hint}
        </p>
      ) : null}

      {people.length === 0 ? (
        <p className="mt-2 text-sm text-ink-soft italic">
          No one else is in the family yet.
        </p>
      ) : (
        <ul className="mt-2 flex list-none flex-wrap gap-2 p-0">
          {people.map((person) => {
            const locked = lockedIds.includes(person.id);
            const checked = locked || value.includes(person.id);
            const inputId = `${uid}-${person.id}`;
            const showYear =
              duplicates.has(person.name.trim().toLowerCase()) && Number.isFinite(person.born);
            return (
              <li key={person.id}>
                <label
                  htmlFor={inputId}
                  className={[
                    "relative inline-flex min-h-11 select-none items-center gap-2 rounded-full border px-3.5 text-sm transition-colors duration-150 ease-paper motion-reduce:transition-none",
                    FOCUS_RING_WITHIN,
                    "has-[:disabled]:cursor-not-allowed",
                    checked
                      ? "border-ink-soft bg-paper-deep text-ink shadow-inset"
                      : "border-rule bg-paper-light text-ink-soft shadow-[0_1px_1px_rgb(74_47_29/0.08)] hover:border-accent",
                    disabled ? "opacity-60" : locked ? "" : "cursor-pointer",
                  ].join(" ")}
                >
                  <input
                    id={inputId}
                    type="checkbox"
                    className="sr-only"
                    checked={checked}
                    disabled={locked || disabled}
                    onChange={(e) => toggle(person.id, e.target.checked)}
                  />
                  <span
                    aria-hidden="true"
                    className="h-3 w-3 shrink-0 rounded-full bg-ink-faint ring-1 ring-rule"
                    style={person.color ? { backgroundColor: person.color } : undefined}
                  />
                  <span>
                    {person.name}
                    {showYear ? (
                      <span className="text-ink-faint"> ({person.born})</span>
                    ) : null}
                    {locked ? (
                      <span className="text-ink-faint"> (you)</span>
                    ) : null}
                  </span>
                  <CheckMark checked={checked} />
                </label>
              </li>
            );
          })}
        </ul>
      )}

      {error ? (
        <p id={errorId} className={`mt-1.5 ${FIELD_ERROR_CLASS}`}>
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

/** A small stitched tick, so "picked" never relies on colour alone. */
function CheckMark({ checked }: { checked: boolean }) {
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
