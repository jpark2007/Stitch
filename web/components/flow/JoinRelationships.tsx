"use client";

// The step after someone joins by adding themselves: "Where do you fit?"
// Parents and spouses are picked from everyone already on the tree (the
// invite preview only lists unclaimed people, so this happens after joining,
// and the page saves it with api.updatePerson). Presentational: the page
// passes the save call and what Skip does. A rejected ApiError shows as a
// form-level message; the picks are kept.
import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import { isApiError } from "@/lib/api";
import { Button } from "./Button";
import { FormError, focusFirstError } from "./FormError";
import { PaperCard } from "./PaperCard";
import { RelationshipPicker } from "./RelationshipPicker";
import { sortPeopleForPicking, validateRelationships, type Relationships } from "./relationships";

const GENERIC_ERROR = "That didn't save. Check your connection and try again.";

type PickablePerson = { id: string; name: string; born?: number | null; color?: string | null };

/** Server messages, field ones first, each once; null when there are none. */
function serverMessage(fieldErrors: Record<string, string>, detail: string): string {
  const out: string[] = [];
  for (const msg of Object.values(fieldErrors)) {
    if (typeof msg === "string" && msg.trim() && !out.includes(msg.trim())) out.push(msg.trim());
  }
  return out.length ? out.join(" ") : detail;
}

export function JoinRelationships(props: {
  selfId: string;
  selfName: string;
  people: PickablePerson[];
  onSave: (r: { parentIds: string[]; spouseIds: string[] }) => Promise<void>;
  onSkip: () => void;
}) {
  const { selfId, selfName, onSave, onSkip } = props;

  const [value, setValue] = useState<Relationships>({ parentIds: [], spouseIds: [] });
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [focusTick, setFocusTick] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const headingId = `${useId()}heading`;

  useEffect(() => {
    if (focusTick > 0) focusFirstError(formRef.current);
  }, [focusTick]);

  const others = sortPeopleForPicking(props.people.filter((p) => p.id !== selfId));
  const nobodyToPick = others.length === 0;
  const firstName = selfName.trim();

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (pending) return;
    if (nobodyToPick) {
      onSkip();
      return;
    }

    const problem = validateRelationships(value, { selfId });
    if (problem) {
      setFormError(problem);
      setFocusTick((n) => n + 1);
      return;
    }

    setFormError(null);
    setPending(true);
    try {
      await onSave({ parentIds: [...value.parentIds], spouseIds: [...value.spouseIds] });
    } catch (err) {
      setFormError(isApiError(err) ? serverMessage(err.fieldErrors, err.detail) : GENERIC_ERROR);
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
      aria-labelledby={headingId}
      className="space-y-7"
    >
      <div>
        <h2 id={headingId} className="text-[1.5rem] leading-tight break-words text-ink">
          {firstName ? <>Where do you fit, {firstName}?</> : "Where do you fit?"}
        </h2>
        <p className="mt-1.5 text-[0.9375rem] leading-snug text-ink-soft">
          Pick your parents and anyone you&rsquo;re married to, so you land in the right place on the
          tree. You can change this later.
        </p>
      </div>

      <RelationshipPicker
        legend="Your family"
        people={others}
        selfId={selfId}
        value={value}
        onChange={(next) => {
          setValue(next);
          if (formError) setFormError(null);
        }}
        disabled={pending}
      />

      <div className="space-y-4 border-t border-dashed border-rule pt-6">
        <FormError message={formError} />
        <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-center">
          <Button type="submit" busy={pending} busyLabel="Saving…" className="w-full sm:w-auto">
            {nobodyToPick ? "Continue" : "Save and continue"}
          </Button>
          {nobodyToPick ? null : (
            <Button variant="quiet" onClick={onSkip} disabled={pending} className="self-center">
              Skip for now
            </Button>
          )}
        </div>
      </div>
    </PaperCard>
  );
}
