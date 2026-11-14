"use client";

// Say how someone fits into the family: "child of Rosa and Luis, married to
// Ana". Two plain checkbox groups (parents, spouses) with a live summary in
// words. Colours are the global look's theme utilities; text styles and the
// focus ring are the kit's shared ones.
import { useId, useState, type ReactNode } from "react";
import { FOCUS_RING_WITHIN } from "./Button";
import { FIELD_ERROR_CLASS, FIELD_HINT_CLASS, FIELD_LABEL_CLASS } from "./Field";
import {
  MAX_PARENTS,
  describeRelationships,
  toggleRelation,
  type Relationships,
} from "./relationships";

interface PickablePerson {
  id: string;
  name: string;
  born?: number | null;
  /** Thread colour: Person.character.color or PersonSummary.color. */
  color?: string | null;
}

type Kind = "parent" | "spouse";

/** Names, with the born year added only where two people share a name. */
function displayNames(people: PickablePerson[]): Record<string, string> {
  const counts = new Map<string, number>();
  for (const p of people) {
    const key = p.name.trim().toLowerCase();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const out: Record<string, string> = {};
  for (const p of people) {
    const shared = (counts.get(p.name.trim().toLowerCase()) ?? 0) > 1;
    out[p.id] = shared && Number.isFinite(p.born) ? `${p.name} (${p.born})` : p.name;
  }
  return out;
}

export function RelationshipPicker(props: {
  legend: ReactNode;
  people: PickablePerson[];
  value: Relationships;
  onChange(r: Relationships): void;
  /** The person being described; left out of both lists. */
  selfId?: string;
  hint?: ReactNode;
  error?: string | null;
  disabled?: boolean;
}) {
  const { legend, value, onChange, selfId, hint, error, disabled } = props;
  const uid = useId();
  const hintId = `${uid}-hint`;
  const errorId = `${uid}-error`;
  const summaryId = `${uid}-summary`;
  const noteId = `${uid}-note`;
  const [note, setNote] = useState<string | null>(null);

  const people = props.people.filter((p) => p.id !== selfId);
  const names = displayNames(props.people);

  const describedBy = [hint ? hintId : null, summaryId, error ? errorId : null]
    .filter(Boolean)
    .join(" ");

  function toggle(kind: Kind, id: string) {
    const next = toggleRelation(value, kind, id);
    if (next === value) {
      setNote(
        `Someone can have up to ${MAX_PARENTS} parents. Unpick one first to choose ${names[id] ?? "someone else"}.`,
      );
      return;
    }
    setNote(null);
    onChange(next);
  }

  return (
    <fieldset
      disabled={disabled}
      aria-describedby={describedBy}
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
          No one else is in the family yet, so there&rsquo;s nobody to pick. You can add relatives
          later.
        </p>
      ) : (
        <div className="mt-2 grid gap-4 rounded-card border border-rule bg-paper-light p-4 shadow-paper">
          <Group
            uid={`${uid}-parent`}
            title={`Parents (up to ${MAX_PARENTS})`}
            kind="parent"
            people={people}
            names={names}
            picked={value.parentIds}
            onToggle={toggle}
            noteId={note ? noteId : undefined}
          />
          {note ? (
            <p
              id={noteId}
              className="-mt-2 text-sm text-accent-deep"
              role="status"
            >
              {note}
            </p>
          ) : null}
          <div aria-hidden="true" className="border-t border-dashed border-rule" />
          <Group
            uid={`${uid}-spouse`}
            title="Married to"
            kind="spouse"
            people={people}
            names={names}
            picked={value.spouseIds}
            onToggle={toggle}
          />
        </div>
      )}

      <p
        id={summaryId}
        aria-live="polite"
        className="mt-3 font-display text-base text-ink-soft italic"
      >
        {describeRelationships(value, names)}
      </p>

      {error ? (
        <p id={errorId} className={`mt-1.5 ${FIELD_ERROR_CLASS}`}>
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}

function Group(p: {
  uid: string;
  title: string;
  kind: Kind;
  people: PickablePerson[];
  names: Record<string, string>;
  picked: string[];
  onToggle(kind: Kind, id: string): void;
  noteId?: string;
}) {
  return (
    <fieldset className="min-w-0" aria-describedby={p.noteId}>
      {/* A sub-group heading inside the field, deliberately smaller than the
          field's own legend above. */}
      <legend className="p-0 text-xs font-medium tracking-wide text-ink-soft uppercase">
        {p.title}
      </legend>
      <ul className="mt-2 flex list-none flex-wrap gap-2 p-0">
        {p.people.map((person) => {
          const checked = p.picked.includes(person.id);
          const inputId = `${p.uid}-${person.id}`;
          return (
            <li key={person.id}>
              <label
                htmlFor={inputId}
                className={[
                  "relative inline-flex min-h-11 cursor-pointer select-none items-center gap-2 rounded-full border px-3.5 text-sm transition-colors duration-150 ease-paper motion-reduce:transition-none",
                  FOCUS_RING_WITHIN,
                  "has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60",
                  checked
                    ? "border-ink-soft bg-paper-deep text-ink shadow-inset"
                    : "border-rule bg-paper text-ink-soft hover:border-accent",
                ].join(" ")}
              >
                <input
                  id={inputId}
                  type="checkbox"
                  className="sr-only"
                  checked={checked}
                  onChange={() => p.onToggle(p.kind, person.id)}
                />
                <span
                  aria-hidden="true"
                  className="h-3 w-3 shrink-0 rounded-full bg-ink-faint ring-1 ring-rule"
                  style={person.color ? { backgroundColor: person.color } : undefined}
                />
                <span>{p.names[person.id] ?? person.name}</span>
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
              </label>
            </li>
          );
        })}
      </ul>
    </fieldset>
  );
}
