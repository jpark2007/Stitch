"use client";

// Add a relative who may never sign up: their name, the years they lived,
// how they fit in (parents, spouses) and, optionally, children already in the
// tree. Presentational: the page owns the API calls and passes them in as
// onSubmit(body, childIds). When it resolves the page moves on (navigates, or
// swaps this form for its done state), so the form stays busy; when it
// rejects, an ApiError comes back as field messages or a form-level message
// and nothing typed is lost.
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";

import { isApiError } from "@/lib/api";
import type { NewPerson } from "@/lib/types";
import { Button } from "./Button";
import { FIELD_HINT_CLASS, FIELD_LABEL_CLASS, FIELD_OPTIONAL_CLASS, TextField } from "./Field";
import { FormError, focusFirstError } from "./FormError";
import { PaperCard } from "./PaperCard";
import { PersonPicker } from "./PersonPicker";
import { RelationshipPicker } from "./RelationshipPicker";
import {
  RELATIVE_FIELDS,
  RELATIVE_YEAR_MIN,
  childCandidates,
  duplicateNameHint,
  emptyRelative,
  relativeServerError,
  toNewPerson,
  validateRelative,
  type RelativeDraft,
  type RelativeField,
  type TreePersonLite,
} from "./relativeForm";
import { sortPeopleForPicking, type Relationships } from "./relationships";
import { mergeServerErrors, type FieldErrors } from "./validation";
import { YearInput } from "./YearInput";

const GENERIC_ERROR = "That didn’t save. Check your connection and try again.";

/** Fields drawn as a group of checkboxes rather than one control. */
const PICKER_FIELDS: readonly RelativeField[] = ["parentIds", "spouseIds", "childIds"];

type PickablePerson = TreePersonLite & { color?: string | null };

function without(errors: FieldErrors<RelativeField>, ...keys: RelativeField[]): FieldErrors<RelativeField> {
  if (!keys.some((k) => k in errors)) return errors;
  const next = { ...errors };
  for (const k of keys) delete next[k];
  return next;
}

/** One message from two (the relationship picker has a single error slot). */
function joinMessages(...msgs: (string | undefined)[]): string | null {
  const out = msgs.filter((m): m is string => typeof m === "string" && m.trim() !== "");
  return out.length ? [...new Set(out)].join(" ") : null;
}

/** The first field, in form order, that has a message. */
function firstField(errors: FieldErrors<RelativeField>): RelativeField | null {
  return RELATIVE_FIELDS.find((f) => errors[f]) ?? null;
}

function Optional() {
  return <span className={FIELD_OPTIONAL_CLASS}>(optional)</span>;
}

export function AddRelativeForm(props: {
  people: Array<PickablePerson>;
  disabled?: boolean;
  onSubmit: (body: NewPerson, childIds: string[]) => Promise<void>;
}) {
  const { people, disabled = false, onSubmit } = props;

  const [draft, setDraft] = useState<RelativeDraft>(emptyRelative);
  const [errors, setErrors] = useState<FieldErrors<RelativeField>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  // Set after a failed submit, with the first field in error; the effect
  // moves focus once the errors render.
  const [focusRequest, setFocusRequest] = useState<{ field: RelativeField | null } | null>(null);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const form = formRef.current;
    if (!focusRequest || !form) return;
    // The checkbox groups carry no aria-invalid, so when the first problem is
    // one of them, focus its first box: the group's description reads the
    // message. Otherwise the kit finds the first invalid control or the alert.
    const first = focusRequest.field;
    if (first && PICKER_FIELDS.includes(first)) {
      const group = form.querySelector<HTMLElement>(`[data-relative-field~="${first}"]`);
      // A picked box first (the problem is usually someone picked), else the first one.
      const box =
        group?.querySelector<HTMLInputElement>('input[type="checkbox"]:checked:not(:disabled)') ??
        group?.querySelector<HTMLInputElement>('input[type="checkbox"]:not(:disabled)');
      if (box) {
        requestAnimationFrame(() => box.focus());
        return;
      }
    }
    focusFirstError(form);
  }, [focusRequest]);

  const locked = disabled || pending;
  const sorted = sortPeopleForPicking(people);
  const kids = childCandidates(sorted, [...draft.parentIds, ...draft.spouseIds]) as PickablePerson[];

  function setText(key: "name" | "born" | "died", value: string) {
    setDraft((d) => ({ ...d, [key]: value }));
    // Fixing either year can settle a "died before born" message on Died.
    setErrors((e) => (key === "born" ? without(e, "born", "died") : without(e, key)));
  }

  // Born is checked against the parents and children picked, so changing
  // either settles a message on Born too.
  function setRelationships(r: Relationships) {
    setDraft((d) => {
      const picked = new Set([...r.parentIds, ...r.spouseIds]);
      // Someone just picked as a parent or spouse drops out of the children
      // (the list hides them, so they must not stay picked unseen).
      const childIds = d.childIds.filter((id) => !picked.has(id));
      return { ...d, parentIds: r.parentIds, spouseIds: r.spouseIds, childIds };
    });
    setErrors((e) => without(e, "parentIds", "spouseIds", "born"));
  }

  function setChildren(ids: string[]) {
    setDraft((d) => ({ ...d, childIds: ids }));
    setErrors((e) => without(e, "childIds", "born"));
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (locked) return;

    const local = validateRelative(draft, people);
    if (Object.keys(local).length > 0) {
      setErrors(local);
      setFormError(null);
      setFocusRequest({ field: firstField(local) });
      return;
    }

    setErrors({});
    setFormError(null);
    setPending(true);
    try {
      await onSubmit(toNewPerson(draft), [...draft.childIds]);
      // Success: the page takes over from here, so stay busy.
      return;
    } catch (err) {
      let fields: FieldErrors<RelativeField> = {};
      let message: string | null = null;
      if (isApiError(err)) {
        if (Object.keys(err.fieldErrors).length > 0) {
          const merged = mergeServerErrors<RelativeField>({}, err.fieldErrors, RELATIVE_FIELDS);
          fields = merged.fields;
          message = merged.rest;
        } else if (err.status === 400) {
          const mapped = relativeServerError(err.detail);
          if (mapped.field) fields = { [mapped.field]: mapped.message };
          else message = mapped.message;
        } else {
          message = err.detail || GENERIC_ERROR;
        }
      } else {
        message = GENERIC_ERROR;
      }
      setErrors(fields);
      setFormError(message);
      setFocusRequest({ field: firstField(fields) });
    }
    setPending(false);
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
      <fieldset disabled={locked} className="mx-0 min-w-0 space-y-8 border-0 p-0">
        <div className="space-y-6">
          <TextField
            label="Their name"
            // A non-breaking hyphen keeps the example in one piece on phones.
            // Someone with this name already in the tree swaps the example for a gentle check.
            hint={duplicateNameHint(draft.name, people) ?? "The name the family uses, like “Great\u2011grandma Inés”."}
            name="name"
            autoComplete="off"
            required
            value={draft.name}
            onChange={(e) => setText("name", e.target.value)}
            error={errors.name}
          />

          <YearsGroup>
            <YearInput
              label="Born"
              name="born"
              // No max: reading the clock while rendering breaks prerendering.
              // validateYear checks "not in the future" on submit.
              min={RELATIVE_YEAR_MIN}
              value={draft.born}
              onChange={(v) => setText("born", v)}
              error={errors.born}
            />
            <YearInput
              label="Died"
              name="died"
              min={RELATIVE_YEAR_MIN}
              value={draft.died}
              onChange={(v) => setText("died", v)}
              error={errors.died}
            />
          </YearsGroup>
        </div>

        <div aria-hidden="true" className="border-t border-dashed border-rule" />

        <div data-relative-field="parentIds spouseIds">
          <RelationshipPicker
            legend="How they fit in"
            hint="Their parents, and anyone they married. Skip whatever you don’t know."
            people={sorted}
            value={{ parentIds: draft.parentIds, spouseIds: draft.spouseIds }}
            onChange={setRelationships}
            error={joinMessages(errors.parentIds, errors.spouseIds)}
            disabled={locked}
          />
        </div>

        <div data-relative-field="childIds">
          {kids.length === 0 && people.length > 0 ? (
            // The kit's empty line says nobody is in the family, which is not
            // the case here: say why nobody can be picked.
            <div className="min-w-0">
              <p className={FIELD_LABEL_CLASS}>
                Their children
                <Optional />
              </p>
              <p className="mt-2 text-sm text-ink-soft italic">
                {childCandidates(people, []).length === 0
                  ? "Everyone in the tree already has two parents listed, so there’s nobody to pick here."
                  : "Everyone who could be their child is already picked above."}
              </p>
            </div>
          ) : (
            <PersonPicker
              legend={
                <>
                  Their children
                  <Optional />
                </>
              }
              hint="Only people who don’t already have two parents are listed."
              people={kids}
              value={draft.childIds}
              onChange={setChildren}
              error={errors.childIds}
              disabled={locked}
            />
          )}
        </div>
      </fieldset>

      <div className="space-y-4 border-t border-dashed border-rule pt-6">
        <FormError message={formError} />
        <Button type="submit" busy={pending} busyLabel="Adding them…" disabled={disabled} className="w-full sm:w-auto">
          Add to the tree
        </Button>
      </div>
    </PaperCard>
  );
}

/** Born and Died side by side from sm up, stacked on phones, under one legend. */
function YearsGroup(props: { children: ReactNode }) {
  const hintId = `${useId()}-hint`;
  return (
    <fieldset className="mx-0 min-w-0 border-0 p-0" aria-describedby={hintId}>
      <legend className={`p-0 ${FIELD_LABEL_CLASS}`}>
        Their years
        <Optional />
      </legend>
      <p id={hintId} className={`mt-0.5 ${FIELD_HINT_CLASS}`}>
        Leave Died empty if they’re still with us.
      </p>
      <div className="mt-3 grid gap-5 sm:grid-cols-2 sm:gap-6">{props.children}</div>
    </fieldset>
  );
}
