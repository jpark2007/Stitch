"use client";

// "Which one is you?" on /join/[code]: claim one of the family's unclaimed
// people, or add yourself as someone new. Presentational: the page owns the
// API call and passes it in as onJoin. A rejected ApiError comes back here as
// field errors (personId, name, born) or a form-level message, such as a 410
// for a link that has expired. What was typed is never cleared by an error.
//
// The choices are one native radio group (arrow keys move between them).
// Picking "I'm not on the tree yet" reveals the name and year-born fields;
// with nobody left to claim, only those fields show.
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";

import { isApiError } from "@/lib/api";
import type { JoinRequest, PersonSummary } from "@/lib/types";
import { Button } from "./Button";
import { FIELD_ERROR_CLASS, OptionalMark, TextField } from "./Field";
import { FormError, focusFirstError } from "./FormError";
import {
  JOIN_FIELDS,
  initialJoinChoice,
  toJoinRequest,
  validateJoinChoice,
  type JoinChoice,
  type JoinField,
} from "./join";
import { friendlyError } from "./joinFlow";
import { PaperCard } from "./PaperCard";
import { sortPeopleForPicking } from "./relationships";
import { mergeServerErrors, type FieldErrors } from "./validation";
import { YearInput } from "./YearInput";

const GENERIC_ERROR = "That didn't go through. Check your connection and try again.";
const ADD = "__add__";
const FALLBACK_DOT = "#8f8173";
const YEAR_MIN = 1850;

/** A visible asterisk; the control's `required` tells assistive tech. */
function RequiredMark() {
  return (
    <span aria-hidden="true" className="ml-1 font-normal text-ink-faint">
      *
    </span>
  );
}

export function JoinForm(props: {
  unclaimed: PersonSummary[];
  suggestedName?: string;
  disabled?: boolean;
  onJoin: (body: JoinRequest) => Promise<void>;
}) {
  const { unclaimed, suggestedName, disabled = false, onJoin } = props;

  const [start] = useState(() => initialJoinChoice(unclaimed, suggestedName));
  const [mode, setMode] = useState<JoinChoice["mode"]>(start.mode);
  const [personId, setPersonId] = useState("");
  // Kept while switching back and forth, so nothing typed is lost.
  const [name, setName] = useState(start.mode === "add" ? start.name : (suggestedName ?? ""));
  const [born, setBorn] = useState("");
  const [errors, setErrors] = useState<FieldErrors<JoinField>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  // Bumped after a failed submit; the effect moves focus once errors render.
  const [focusTick, setFocusTick] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);

  const uid = useId();
  const groupName = `${uid}who`;
  const hintId = `${uid}who-hint`;
  const choiceErrorId = `${uid}who-error`;

  useEffect(() => {
    if (focusTick === 0) return;
    const form = formRef.current;
    // Radios can't carry aria-invalid, so a choice error (always the first
    // field) is found by its message and focus goes to the group: the picked
    // radio, or the first one. The group's description reads the message.
    if (form && document.getElementById(choiceErrorId)) {
      const radios = Array.from(form.querySelectorAll<HTMLInputElement>('input[type="radio"]'));
      const target = radios.find((r) => r.checked) ?? radios[0];
      if (target) {
        target.focus();
        return;
      }
    }
    focusFirstError(form);
  }, [focusTick, choiceErrorId]);

  const locked = disabled || pending;
  const anyoneToClaim = unclaimed.length > 0;
  // With nobody to claim, adding yourself is the only way in.
  const effectiveMode = anyoneToClaim ? mode : "add";
  // A pick that left the list (claimed by someone else, then the list was
  // reloaded) counts as no pick, so nothing shows as chosen that can't be.
  const livePersonId = unclaimed.some((p) => p.id === personId) ? personId : "";
  const choice: JoinChoice =
    effectiveMode === "claim" ? { mode: "claim", personId: livePersonId } : { mode: "add", name, born };
  const people = sortPeopleForPicking(unclaimed);
  const picked = effectiveMode === "claim" ? unclaimed.find((p) => p.id === livePersonId) : undefined;

  function clearError(key: JoinField) {
    if (!(key in errors)) return;
    setErrors((e) => {
      const next = { ...e };
      delete next[key];
      return next;
    });
  }

  function pick(value: string) {
    if (value === ADD) {
      setMode("add");
    } else {
      setMode("claim");
      setPersonId(value);
    }
    clearError("personId");
  }

  /** Messages for fields this choice hides can only be read at the form level. */
  function splitHidden(fields: FieldErrors<JoinField>, rest: string | null) {
    const visible: readonly JoinField[] = effectiveMode === "claim" ? ["personId"] : ["name", "born"];
    const shown: FieldErrors<JoinField> = {};
    const extra: string[] = rest ? [rest] : [];
    for (const key of JOIN_FIELDS) {
      const msg = fields[key];
      if (!msg) continue;
      if (visible.includes(key)) shown[key] = msg;
      else if (!extra.includes(msg)) extra.push(msg);
    }
    return { fields: shown, rest: extra.length ? extra.join(" ") : null };
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (locked) return;

    const local = validateJoinChoice(choice, unclaimed);
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
      await onJoin(toJoinRequest(choice));
    } catch (err) {
      if (isApiError(err)) {
        const merged = mergeServerErrors<JoinField>({}, err.fieldErrors, JOIN_FIELDS);
        const { fields, rest } = splitHidden(merged.fields, merged.rest);
        const anyField = Object.keys(fields).length > 0;
        setErrors(fields);
        setFormError(rest ?? (anyField ? null : friendlyError(err, GENERIC_ERROR)));
      } else {
        setErrors({});
        setFormError(GENERIC_ERROR);
      }
      setFocusTick((n) => n + 1);
    } finally {
      setPending(false);
    }
  }

  const addFields = (
    <div className="grid gap-6 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
      <TextField
        label={
          <>
            Your name
            <RequiredMark />
          </>
        }
        hint="The name your family calls you."
        name="name"
        autoComplete="name"
        required
        value={name}
        onChange={(e) => {
          setName(e.target.value);
          clearError("name");
        }}
        error={errors.name}
        disabled={locked}
      />
      <YearInput
        label={
          <>
            Year you were born
            <OptionalMark />
          </>
        }
        name="born"
        // No max: reading the clock while rendering breaks prerendering.
        // validateYear checks "not in the future" on submit.
        min={YEAR_MIN}
        value={born}
        onChange={(v) => {
          setBorn(v);
          clearError("born");
        }}
        error={errors.born}
      />
    </div>
  );

  return (
    <PaperCard
      as="form"
      ref={formRef}
      noValidate
      onSubmit={handleSubmit}
      aria-busy={pending || undefined}
      className="space-y-7"
    >
      <fieldset disabled={locked} className="mx-0 min-w-0 space-y-7 border-0 p-0">
        {anyoneToClaim ? (
          <fieldset
            className="mx-0 min-w-0 border-0 p-0"
            aria-describedby={[hintId, errors.personId ? choiceErrorId : null].filter(Boolean).join(" ")}
          >
            <legend className="p-0 font-display text-[1.5rem] leading-tight text-ink">
              Which one is you?
            </legend>
            <p id={hintId} className="mt-1.5 text-[0.9375rem] leading-snug text-ink-soft">
              Someone may have added you already. Pick your name, or add yourself.
            </p>

            {/* A choice error marks the group once, with the same red rule down its
                left edge that FormError has, rather than a red border on every card.
                The rule sits in the card's padding, so nothing moves when it shows. */}
            <div className="relative mt-4">
              {errors.personId ? (
                <span
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-y-0 -left-3.5 w-[3px] rounded-full bg-[#9c3b2a] sm:-left-4"
                />
              ) : null}
              <ul className="grid list-none gap-2.5 p-0 sm:grid-cols-2">
                {people.map((p) => (
                  <li key={p.id} className="min-w-0">
                    <ChoiceCard
                      name={groupName}
                      value={p.id}
                      checked={effectiveMode === "claim" && livePersonId === p.id}
                      onPick={pick}
                    >
                      <span
                        aria-hidden="true"
                        className="size-3 shrink-0 rounded-full ring-1 ring-rule"
                        style={{ backgroundColor: p.color || FALLBACK_DOT }}
                      />
                      <span className="min-w-0 flex-1 break-words font-semibold text-ink">{p.name}</span>
                      {Number.isFinite(p.born) ? (
                        <span className="shrink-0 text-sm text-ink-faint tabular-nums">born {p.born}</span>
                      ) : null}
                    </ChoiceCard>
                  </li>
                ))}
                <li className="min-w-0 sm:col-span-2">
                  <ChoiceCard
                    name={groupName}
                    value={ADD}
                    checked={effectiveMode === "add"}
                    onPick={pick}
                    dashed
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block font-semibold text-ink">I&rsquo;m not on the tree yet</span>
                      <span className="block text-sm leading-snug text-ink-soft">
                        Add yourself as someone new.
                      </span>
                    </span>
                  </ChoiceCard>
                </li>
              </ul>

              {errors.personId ? (
                <p id={choiceErrorId} className={`mt-2 ${FIELD_ERROR_CLASS}`}>
                  {errors.personId}
                </p>
              ) : null}
            </div>
          </fieldset>
        ) : (
          <div>
            <h2 className="font-display text-[1.5rem] leading-tight text-ink">Add yourself to the tree</h2>
            <p className="mt-1.5 text-[0.9375rem] leading-snug text-ink-soft">
              Everyone on the tree so far has already been claimed, so you&rsquo;ll join as someone new.
            </p>
          </div>
        )}

        {effectiveMode === "add" ? (
          anyoneToClaim ? (
            <div className="border-l-[3px] border-accent/50 pl-4 sm:pl-5">{addFields}</div>
          ) : (
            addFields
          )
        ) : null}
      </fieldset>

      <div className="space-y-4 border-t border-dashed border-rule pt-6">
        <FormError message={formError} />
        <Button
          type="submit"
          busy={pending}
          busyLabel="Joining…"
          disabled={disabled}
          className="w-full sm:w-auto"
        >
          {picked ? `Join as ${picked.name}` : "Join the family"}
        </Button>
      </div>
    </PaperCard>
  );
}

/** One radio choice, drawn as a small paper card; the native radio stays visible. */
function ChoiceCard(props: {
  name: string;
  value: string;
  checked: boolean;
  dashed?: boolean;
  onPick(value: string): void;
  children: ReactNode;
}) {
  const { name, value, checked, dashed = false, onPick, children } = props;
  return (
    <label
      className={[
        "flex h-full min-h-14 cursor-pointer items-center gap-3 rounded-card border px-4 py-3",
        "transition-[background-color,border-color,box-shadow] duration-150 motion-reduce:transition-none",
        "has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60",
        dashed ? "border-dashed" : "",
        checked
          ? "border-accent-deep bg-paper shadow-[inset_0_0_0_1px_var(--accent-deep)]"
          : "border-ink-faint/60 bg-paper-light shadow-paper hover:border-ink-soft",
      ].join(" ")}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={checked}
        onChange={() => onPick(value)}
        className="size-[1.125rem] shrink-0 cursor-pointer accent-accent-deep disabled:cursor-not-allowed"
      />
      {children}
    </label>
  );
}
