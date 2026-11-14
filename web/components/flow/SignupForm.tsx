"use client";

// Sign up: your name, email and a password. Presentational: the page owns the
// API call and passes it in as onSubmit, then navigates away when it resolves.
// A rejected ApiError comes back as field messages and its detail in the
// form-level alert; anything else gets a plain message. What was typed is
// never cleared by an error.
//
// With cacheComponents on, a page left behind stays mounted, hidden by React
// Activity, with its state and DOM. So the password is cleared whenever this
// form is hidden (a layout effect cleanup, which runs as Activity hides it and
// before anything paints, so the visible form never flashes empty), and a sent
// form is swapped for signupDraftAfterSuccess, which empties name and email
// too. A half-typed name and email survive a detour to /login and back; a
// password never does, and nothing survives a successful sign up.
import Link from "next/link";
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import type { SignupRequest } from "@/lib/types";
import { isApiError } from "@/lib/api";
import {
  PASSWORD_HINT,
  SIGNUP_FIELDS,
  serverErrorState,
  signupDraftAfterSuccess,
  toSignupRequest,
  validateSignup,
  withoutError,
  type SignupDraft,
  type SignupField,
} from "./accountForms";
import { Button, FOCUS_RING } from "./Button";
import { TextField } from "./Field";
import { FormError, focusFirstError } from "./FormError";
import { PaperCard } from "./PaperCard";
import type { FieldErrors } from "./validation";

const GENERIC_ERROR = "We couldn't make your account just now. Check your connection and try again.";

// The kit's dashed focus ring, like every other control on the page.
const LINK =
  "rounded-[var(--radius-paper,2px)] font-semibold text-accent-deep underline decoration-1 underline-offset-[0.2em] " +
  `hover:decoration-2 ${FOCUS_RING}`;

export function SignupForm(props: {
  onSubmit: (body: SignupRequest) => Promise<void>;
  loginHref: string;
}) {
  const { onSubmit, loginHref } = props;

  const [draft, setDraft] = useState<SignupDraft>({ name: "", email: "", password: "" });
  const [errors, setErrors] = useState<FieldErrors<SignupField>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  // Bumped after a failed submit; the effect moves focus once errors render.
  const [focusTick, setFocusTick] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  // True from sending until onSubmit rejects: the page is navigating away.
  // Set before the call so a navigation that hides this form first still
  // finds it set.
  const sentRef = useRef(false);

  useEffect(() => {
    if (focusTick > 0) focusFirstError(formRef.current);
  }, [focusTick]);

  // Runs when Activity hides this form (and on unmount, where it is a no-op).
  useLayoutEffect(() => {
    return () => {
      const sent = sentRef.current;
      sentRef.current = false;
      setDraft((d) => (sent ? signupDraftAfterSuccess() : { ...d, password: "" }));
    };
  }, []);

  function update(key: SignupField, value: string) {
    setDraft((d) => ({ ...d, [key]: value }));
    setErrors((e) => withoutError(e, key));
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (pending) return;

    const local = validateSignup(draft);
    if (Object.keys(local).length > 0) {
      setErrors(local);
      setFormError(null);
      setFocusTick((n) => n + 1);
      return;
    }

    setErrors({});
    setFormError(null);
    setPending(true);
    sentRef.current = true;
    try {
      await onSubmit(toSignupRequest(draft));
    } catch (err) {
      sentRef.current = false;
      if (isApiError(err)) {
        const next = serverErrorState(err, SIGNUP_FIELDS);
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
    <div className="space-y-6">
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
            label="Your name"
            hint="The name your family knows you by."
            name="name"
            autoComplete="name"
            required
            value={draft.name}
            onChange={(e) => update("name", e.target.value)}
            error={errors.name}
          />
          <TextField
            label="Email"
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            autoCapitalize="none"
            spellCheck={false}
            required
            value={draft.email}
            onChange={(e) => update("email", e.target.value)}
            error={errors.email}
          />
          <TextField
            label="Password"
            hint={PASSWORD_HINT}
            type="password"
            name="password"
            autoComplete="new-password"
            required
            value={draft.password}
            onChange={(e) => update("password", e.target.value)}
            error={errors.password}
          />
        </fieldset>

        <div className="space-y-4">
          <FormError message={formError} />
          <Button type="submit" busy={pending} busyLabel="Creating your account…" className="w-full">
            Create account
          </Button>
        </div>
      </PaperCard>

      <p className="text-center text-[0.9375rem] text-ink-soft">
        Already have an account?{" "}
        <Link href={loginHref} className={LINK}>
          Log in
        </Link>
      </p>
    </div>
  );
}
