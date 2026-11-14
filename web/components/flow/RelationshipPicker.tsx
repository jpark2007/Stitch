"use client";

// Say how someone fits into the family: "child of Rosa and Luis, married to
// Ana". Two plain checkbox groups (parents, spouses) with a live summary in
// words. Self-contained; colours are Ethan's CSS variables with fallbacks.
import { useId, useState, type ReactNode } from "react";
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

const FALLBACK_DOT = "#8f8173";
const PAPER_SHADOW =
  "var(--shadow-paper, 0 1px 1px rgb(74 47 29 / 0.08), 0 2px 6px rgb(74 47 29 / 0.08))";

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
      <legend className="p-0 text-sm font-medium text-[var(--ink,#2f2620)]">{legend}</legend>
      {hint ? (
        <p id={hintId} className="mt-1 text-sm leading-relaxed text-[var(--ink-faint,#8f8173)]">
          {hint}
        </p>
      ) : null}

      {people.length === 0 ? (
        <p className="mt-3 text-sm text-[var(--ink-faint,#8f8173)] italic">
          No one else is in the family yet, so there&rsquo;s nobody to pick. You can add relatives
          later.
        </p>
      ) : (
        <div
          className="mt-3 grid gap-4 rounded-[var(--radius-card,6px)] border border-[var(--rule,#2f262024)] bg-[var(--paper-light,#fbf8f1)] p-4"
          style={{ boxShadow: PAPER_SHADOW }}
        >
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
              className="-mt-2 text-sm text-[var(--accent-deep,#8f3f2a)]"
              role="status"
            >
              {note}
            </p>
          ) : null}
          <div aria-hidden="true" className="border-t border-dashed border-[var(--rule,#2f262024)]" />
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
        className="mt-3 font-serif text-base text-[var(--ink-soft,#5c4e43)] italic"
      >
        {describeRelationships(value, names)}
      </p>

      {error ? (
        <p id={errorId} className="mt-1 text-sm text-[#9c3b2a]">
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
      <legend className="p-0 text-xs font-medium tracking-wide text-[var(--ink-soft,#5c4e43)] uppercase">
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
                  "relative inline-flex min-h-11 cursor-pointer select-none items-center gap-2 rounded-full border px-3.5 text-sm transition-colors",
                  "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[var(--accent,#b9573d)] has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-[var(--paper-light,#fbf8f1)]",
                  "has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60",
                  checked
                    ? "border-[var(--ink-soft,#5c4e43)] bg-[var(--paper-deep,#ebe0cb)] text-[var(--ink,#2f2620)] shadow-[inset_0_1px_2px_rgb(74_47_29/0.12)]"
                    : "border-[var(--rule,#2f262024)] bg-[var(--paper,#f5eee1)] text-[var(--ink-soft,#5c4e43)] hover:border-[var(--accent,#b9573d)]",
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
                  className="h-3 w-3 shrink-0 rounded-full ring-1 ring-[var(--rule,#2f262024)]"
                  style={{ backgroundColor: person.color || FALLBACK_DOT }}
                />
                <span>{p.names[person.id] ?? person.name}</span>
                <svg
                  aria-hidden="true"
                  viewBox="0 0 12 12"
                  className={`h-3 w-3 shrink-0 transition-opacity ${checked ? "opacity-100" : "opacity-0"}`}
                >
                  <path
                    d="M2 6.5 4.8 9 10 3"
                    fill="none"
                    stroke="var(--accent-deep, #8f3f2a)"
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
