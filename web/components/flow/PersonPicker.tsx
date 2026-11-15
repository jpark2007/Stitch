"use client";

// Pick people from the family (who was there for this memory). A plain
// fieldset of real checkboxes, drawn as paper chips with each person's
// thread colour. Colours are the global look's theme utilities; text styles
// and the focus ring are the kit's shared ones. A big family (more than
// PICKER_SEARCH_FROM people) also gets a "Find someone" field; whoever is
// picked stays in view whatever it holds. Enter in that field never submits
// the surrounding form (see pickerSearchKey).
import { useId, useState, type ReactNode } from "react";
import { FOCUS_RING_WITHIN } from "./Button";
import {
  FIELD_ERROR_CLASS,
  FIELD_HINT_CLASS,
  FIELD_LABEL_CLASS,
  FIELD_ROW_CLASS,
  FIELD_ROW_LABEL_CLASS,
  fieldControlClass,
} from "./Field";
import { PICKER_SEARCH_FROM, filterPeople, pickerSearchKey } from "./memoryForm";

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
  /**
   * "row": the legend shown in a left column beside the chips (the
   * add-a-memory details), rather than above them. Default "stacked".
   */
  layout?: "stacked" | "row" | "bare";
  /** Shown after the chips, in the same column (a quiet "add someone" link). */
  footer?: ReactNode;
  /**
   * "plain": names set as type in an even grid (the tradition form's
   * specimen card): the picked ones in full ink with an accent underline, the
   * rest muted, no chip outlines. Default "chips". With layout "bare" the
   * legend is for assistive tech only and the caller draws the visible label.
   */
  appearance?: "chips" | "plain";
}) {
  const {
    legend,
    people,
    value,
    onChange,
    hint,
    error,
    disabled,
    layout = "stacked",
    footer,
    appearance = "chips",
  } = props;
  const bare = layout === "bare";
  // Bare lays out like a row's value column: the caller draws the label.
  const row = layout === "row" || bare;
  const plain = appearance === "plain";
  const lockedIds = props.lockedIds ?? [];
  const uid = useId();
  const hintId = `${uid}-hint`;
  const errorId = `${uid}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ");
  const duplicates = sharedNames(people);
  const searchable = people.length > PICKER_SEARCH_FROM;
  const [query, setQuery] = useState("");
  const shown = searchable ? filterPeople(people, query, [...lockedIds, ...value]) : people;
  const noMatch = searchable && query.trim() !== "" && filterPeople(people, query).length === 0;
  const searchId = `${uid}-search`;
  const resultsId = `${uid}-results`;

  function toggle(id: string, checked: boolean) {
    if (lockedIds.includes(id)) return;
    const rest = value.filter((v) => v !== id);
    const next = checked ? [...rest, id] : rest;
    // Locked people always stay in the answer, even if the caller forgot them.
    for (const locked of lockedIds) if (!next.includes(locked)) next.unshift(locked);
    onChange(next);
  }

  const body = (
    <>
      {hint ? (
        <p id={hintId} className={`${row ? "" : "mt-0.5 "}${FIELD_HINT_CLASS}`}>
          {hint}
        </p>
      ) : null}

      {searchable ? (
        <div className={row && !hint ? undefined : "mt-2"}>
          <label htmlFor={searchId} className="sr-only">
            Find someone
          </label>
          <input
            id={searchId}
            type="search"
            autoComplete="off"
            placeholder="Find someone"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              // Enter never sends the memory from here: it picks the one
              // match, or does nothing. Escape empties the search.
              const result = pickerSearchKey(e.key, query, people, [...lockedIds, ...value]);
              if (!result) return;
              e.preventDefault();
              if (result.action === "pick") {
                toggle(result.pickId, true);
                setQuery("");
              } else if (result.action === "clear") {
                setQuery("");
              }
            }}
            aria-controls={resultsId}
            className={fieldControlClass(false, "w-full sm:w-72")}
          />
          <p id={resultsId} aria-live="polite" className="sr-only">
            {query.trim() ? `${shown.length} shown` : ""}
          </p>
        </div>
      ) : null}

      {people.length === 0 ? (
        <p className={`${row && !hint && !searchable ? "flex min-h-11 items-center" : "mt-2"} text-sm text-ink-soft`}>
          No one else is in the family yet.
        </p>
      ) : (
        <ul
          className={`${row && !hint && !searchable ? "" : "mt-2 "}${
            plain
              ? // Names flow like a line of text, evenly spaced, the first flush
                // with the column edge; a line breaks only between whole names,
                // and a name only at its spaces (a word too long for a whole
                // line breaks as a last resort).
                "-mx-1 flex list-none flex-wrap gap-x-1.5 p-0"
              : "flex list-none flex-wrap gap-2 p-0"
          }`}
        >
          {shown.map((person) => {
            const locked = lockedIds.includes(person.id);
            const checked = locked || value.includes(person.id);
            const inputId = `${uid}-${person.id}`;
            const showYear =
              duplicates.has(person.name.trim().toLowerCase()) && Number.isFinite(person.born);
            if (plain) {
              return (
                <li key={person.id} className="max-w-full">
                  <label
                    htmlFor={inputId}
                    className={[
                      "group relative inline-flex max-w-full min-h-11 select-none items-center gap-2 rounded-paper px-1 font-serif text-[0.9375rem] leading-snug transition-colors duration-150 ease-paper motion-reduce:transition-none",
                      FOCUS_RING_WITHIN,
                      "has-[:disabled]:cursor-not-allowed",
                      checked ? "text-ink" : "text-ink-soft hover:text-ink",
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
                    {/* Their thread colour: a filled dot when picked, a hollow
                        ring when not (shape, not colour alone). */}
                    <span
                      aria-hidden="true"
                      className={`box-border h-2.5 w-2.5 shrink-0 rounded-full border-[1.5px] border-ink-soft transition-[background-color,opacity] duration-150 ease-paper motion-reduce:transition-none ${
                        checked ? "bg-ink-soft" : "bg-transparent opacity-70 group-hover:opacity-100"
                      }`}
                      style={
                        person.color
                          ? { borderColor: person.color, backgroundColor: checked ? person.color : "transparent" }
                          : undefined
                      }
                    />
                    <ChosenText
                      checked={checked}
                      text={`${person.name}${showYear ? ` (${person.born})` : ""}${locked ? " (you)" : ""}`}
                    >
                      {person.name}
                      {showYear ? <span className="font-normal text-ink-soft"> ({person.born})</span> : null}
                      {locked ? <span className="font-normal text-ink-soft"> (you)</span> : null}
                    </ChosenText>
                  </label>
                </li>
              );
            }
            return (
              <li key={person.id}>
                <label
                  htmlFor={inputId}
                  className={[
                    "relative inline-flex min-h-11 select-none items-center gap-2 rounded-full border px-3.5 text-sm transition-colors duration-150 ease-paper motion-reduce:transition-none",
                    FOCUS_RING_WITHIN,
                    "has-[:disabled]:cursor-not-allowed",
                    // Flat: picked is a filled chip with a tick, not picked a hairline.
                    checked
                      ? "border-ink-soft/40 bg-paper-deep text-ink"
                      : "border-rule bg-transparent text-ink-soft hover:border-ink-faint hover:text-ink",
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
                      <span className="text-ink-soft"> ({person.born})</span>
                    ) : null}
                    {locked ? (
                      <span className="text-ink-soft"> (you)</span>
                    ) : null}
                  </span>
                  <CheckMark checked={checked} />
                </label>
              </li>
            );
          })}
          {noMatch ? (
            <li className="flex min-h-11 items-center px-1 text-sm text-ink-soft italic">
              No one called &ldquo;{query.trim()}&rdquo;.
            </li>
          ) : null}
        </ul>
      )}

      {error ? (
        <p id={errorId} className={`mt-1.5 ${FIELD_ERROR_CLASS}`}>
          {error}
        </p>
      ) : null}
      {footer}
    </>
  );

  if (bare) {
    return (
      <fieldset disabled={disabled} aria-describedby={describedBy || undefined} className="min-w-0">
        <legend className="sr-only">{legend}</legend>
        {body}
      </fieldset>
    );
  }

  if (row) {
    // A rendered <legend> cannot sit in a grid cell, so the legend names the
    // group for assistive tech and a twin label (hidden from it) is drawn in
    // the left column.
    return (
      <fieldset disabled={disabled} aria-describedby={describedBy || undefined} className="min-w-0">
        <legend className="sr-only">{legend}</legend>
        <div className={FIELD_ROW_CLASS}>
          <span aria-hidden="true" className={FIELD_ROW_LABEL_CLASS}>
            {legend}
          </span>
          <div className="mt-2 min-w-0 sm:mt-0">{body}</div>
        </div>
      </fieldset>
    );
  }

  return (
    <fieldset
      disabled={disabled}
      aria-describedby={describedBy || undefined}
      className="min-w-0"
    >
      <legend className={`p-0 ${FIELD_LABEL_CLASS}`}>{legend}</legend>
      {body}
    </fieldset>
  );
}

/**
 * A chosen word or name in the plain appearance: semibold, with a 2px accent
 * underline; unchosen it is plain. A hidden semibold twin in the same grid
 * cell reserves the wider width, so choosing never shifts the words beside
 * it. It wraps only at spaces; a single word longer than a whole line breaks
 * as a last resort. Hover belongs to the caller (it only darkens the ink).
 *
 * `text` is the same words as plain text: the twin draws them as generated
 * content, so the label's own text (what finds a control by its label) says
 * them once.
 */
export function ChosenText({ checked, text, children }: { checked: boolean; text: string; children: ReactNode }) {
  return (
    <span className="inline-grid max-w-full [overflow-wrap:anywhere]">
      <span
        className={`col-start-1 row-start-1 underline-offset-[0.38em] ${
          checked ? "font-semibold underline decoration-accent-deep decoration-2" : ""
        }`}
      >
        {children}
      </span>
      <span
        aria-hidden="true"
        data-text={text}
        className="invisible col-start-1 row-start-1 font-semibold after:content-[attr(data-text)]"
      />
    </span>
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
