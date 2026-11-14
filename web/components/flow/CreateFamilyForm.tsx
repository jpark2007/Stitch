"use client";

// Start a family: its name, your name (you become its first person) and,
// optionally, the year you were born. Presentational: the page owns the API
// call and passes it in as onSubmit, then navigates away when it resolves. A
// rejected ApiError comes back as field messages and its detail in the
// form-level alert; anything else gets a plain message. What was typed is
// never cleared by an error.
//
// Nothing is reset here after a successful submit. With cacheComponents on,
// the page it navigated from stays mounted (hidden) with this form's state,
// so the page clears it: NewFamilyScreen remounts this form with a new `key`
// when it is shown again after a family was made (see freshForm.ts).
import { useEffect, useRef, useState, type FormEvent } from "react";
import type { CreateFamilyRequest } from "@/lib/types";
import { isApiError } from "@/lib/api";
import {
  FAMILY_FIELDS,
  familyServerErrors,
  serverErrorState,
  toCreateFamilyRequest,
  validateFamily,
  withoutError,
  type FamilyDraft,
  type FamilyField,
} from "./accountForms";
import { Button } from "./Button";
import { OptionalMark, TextField } from "./Field";
import { FormError, focusFirstError } from "./FormError";
import { PaperCard } from "./PaperCard";
import type { FieldErrors } from "./validation";
import { YearInput } from "./YearInput";

const GENERIC_ERROR = "We couldn't start your family just now. Check your connection and try again.";
const YEAR_MIN = 1850;

export function CreateFamilyForm(props: {
  defaultYourName?: string;
  onSubmit: (body: CreateFamilyRequest) => Promise<void>;
}) {
  const { defaultYourName = "", onSubmit } = props;

  const [draft, setDraft] = useState<FamilyDraft>({
    familyName: "",
    yourName: defaultYourName,
    born: "",
  });
  const [errors, setErrors] = useState<FieldErrors<FamilyField>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  // Bumped after a failed submit; the effect moves focus once errors render.
  const [focusTick, setFocusTick] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);

  // The page may learn your name after the first render (from /me). Take it
  // then, unless you have already changed the field yourself.
  const [seenDefault, setSeenDefault] = useState(defaultYourName);
  if (seenDefault !== defaultYourName) {
    setSeenDefault(defaultYourName);
    if (draft.yourName === seenDefault) setDraft((d) => ({ ...d, yourName: defaultYourName }));
  }

  useEffect(() => {
    if (focusTick > 0) focusFirstError(formRef.current);
  }, [focusTick]);

  function update(key: FamilyField, value: string) {
    setDraft((d) => ({ ...d, [key]: value }));
    setErrors((e) => withoutError(e, key));
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (pending) return;

    const local = validateFamily(draft);
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
      await onSubmit(toCreateFamilyRequest(draft));
    } catch (err) {
      if (isApiError(err)) {
        const next = serverErrorState(
          { detail: err.detail, fieldErrors: familyServerErrors(err.fieldErrors) },
          FAMILY_FIELDS,
        );
        setErrors(next.fields);
        setFormError(next.formError);
      } else {
        setErrors({});
        setFormError(GENERIC_ERROR);
      }
      setFocusTick((n) => n + 1);
    } finally {
      setPending(false);
    }
  }

  return (
    <PaperCard
      as="form"
      ref={formRef}
      noValidate
      onSubmit={handleSubmit}
      aria-busy={pending || undefined}
      className="space-y-6"
    >
      <fieldset disabled={pending} className="mx-0 min-w-0 space-y-5 border-0 p-0">
        <TextField
          label="Family name"
          hint="What you call yourselves, like “The Maos”."
          name="familyName"
          autoComplete="off"
          required
          value={draft.familyName}
          onChange={(e) => update("familyName", e.target.value)}
          error={errors.familyName}
        />
        <TextField
          label="Your name"
          name="yourName"
          autoComplete="name"
          required
          value={draft.yourName}
          onChange={(e) => update("yourName", e.target.value)}
          error={errors.yourName}
        />
        <YearInput
          label={
            <>
              The year you were born
              <OptionalMark />
            </>
          }
          hint="It places you on the family timeline."
          name="born"
          // No max: reading the clock while rendering breaks prerendering.
          // validateYear checks "not in the future" on submit.
          min={YEAR_MIN}
          value={draft.born}
          onChange={(v) => update("born", v)}
          error={errors.born}
        />
      </fieldset>

      <div className="space-y-4">
        <FormError message={formError} />
        <Button type="submit" busy={pending} busyLabel="Starting your family…" className="w-full">
          Start the family
        </Button>
      </div>
    </PaperCard>
  );
}
