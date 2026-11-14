"use client";

// Log in: email and password on a sheet of paper. Presentational: the page
// owns the API call and passes it in as onSubmit, then navigates away when it
// resolves. A rejected ApiError comes back as field messages and its detail in
// the form-level alert; anything else gets a plain message. What was typed is
// never cleared by an error.
//
// With cacheComponents on, a page left behind stays mounted, hidden by React
// Activity, with its state and DOM. So the password is cleared whenever this
// form is hidden (a layout effect cleanup, which runs as Activity hides it and
// before anything paints, so the visible form never flashes empty), and a sent
// form is swapped for loginDraftAfterSuccess. A hidden /login never holds a
// password.
//
// With `demo`, a handwritten note gives the demo account and a button that
// fills both fields, so a visitor can look around without signing up.
import Link from "next/link";
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import type { LoginRequest } from "@/lib/types";
import { isApiError } from "@/lib/api";
import {
  LOGIN_FIELDS,
  loginDraftAfterSuccess,
  serverErrorState,
  toLoginRequest,
  validateLogin,
  withoutError,
  type LoginDraft,
  type LoginField,
} from "./accountForms";
import { Button, FOCUS_RING } from "./Button";
import { TextField } from "./Field";
import { FormError, focusFirstError } from "./FormError";
import { PaperCard } from "./PaperCard";
import type { FieldErrors } from "./validation";

const GENERIC_ERROR = "We couldn't log you in just now. Check your connection and try again.";

// The kit's dashed focus ring, like every other control on the page.
const LINK =
  "rounded-[var(--radius-paper,2px)] font-semibold text-accent-deep underline decoration-1 underline-offset-[0.2em] " +
  `hover:decoration-2 ${FOCUS_RING}`;

export function LoginForm(props: {
  onSubmit: (body: LoginRequest) => Promise<void>;
  signupHref: string;
  demo?: { email: string; password: string } | null;
  initialEmail?: string;
}) {
  const { onSubmit, signupHref, demo = null, initialEmail = "" } = props;

  const [draft, setDraft] = useState<LoginDraft>({ email: initialEmail, password: "" });
  const [errors, setErrors] = useState<FieldErrors<LoginField>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  // Bumped after a failed submit; the effect moves focus once errors render.
  const [focusTick, setFocusTick] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const submitRef = useRef<HTMLButtonElement>(null);
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
      setDraft((d) => (sent ? loginDraftAfterSuccess(d) : { ...d, password: "" }));
    };
  }, []);

  function update(key: LoginField, value: string) {
    setDraft((d) => ({ ...d, [key]: value }));
    setErrors((e) => withoutError(e, key));
  }

  function fillDemo() {
    if (!demo || pending) return;
    setDraft({ email: demo.email, password: demo.password });
    setErrors({});
    setFormError(null);
    // Filled in: the next press of Enter or Space logs in.
    submitRef.current?.focus();
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (pending) return;

    const local = validateLogin(draft);
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
      await onSubmit(toLoginRequest(draft));
    } catch (err) {
      sentRef.current = false;
      if (isApiError(err)) {
        const next = serverErrorState(err, LOGIN_FIELDS);
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
        {demo ? <DemoNote demo={demo} disabled={pending} onUse={fillDemo} /> : null}

        <fieldset disabled={pending} className="mx-0 min-w-0 space-y-5 border-0 p-0">
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
            type="password"
            name="password"
            autoComplete="current-password"
            required
            value={draft.password}
            onChange={(e) => update("password", e.target.value)}
            error={errors.password}
          />
        </fieldset>

        <div className="space-y-4">
          <FormError message={formError} />
          <Button
            ref={submitRef}
            type="submit"
            busy={pending}
            busyLabel="Logging in…"
            className="w-full"
          >
            Log in
          </Button>
        </div>
      </PaperCard>

      <p className="text-center text-[0.9375rem] text-ink-soft">
        New to Stitch?{" "}
        <Link href={signupHref} className={LINK}>
          Create an account
        </Link>
      </p>
    </div>
  );
}

/**
 * A slip of paper tucked into the form: the demo credentials in a hand, and a
 * button that fills them in. The credentials themselves are in the plain face
 * so every character reads clearly.
 */
function DemoNote(props: {
  demo: { email: string; password: string };
  disabled: boolean;
  onUse(): void;
}) {
  const { demo, disabled, onUse } = props;
  return (
    <aside
      aria-label="Demo account"
      className="-rotate-[0.6deg] rounded-[var(--radius-paper,2px)] border border-dashed border-ink-faint/60 bg-paper px-4 py-3 shadow-paper motion-reduce:rotate-0"
    >
      <p className="font-hand text-[1.375rem] leading-tight text-ink">
        Just looking around? Use the demo family.
      </p>
      <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-sm text-ink-soft">
        <dt>Email</dt>
        <dd className="m-0 font-medium break-all text-ink">{demo.email}</dd>
        <dt>Password</dt>
        <dd className="m-0 font-medium break-all text-ink">{demo.password}</dd>
      </dl>
      <Button
        variant="secondary"
        disabled={disabled}
        onClick={onUse}
        className="mt-3"
      >
        Use the demo account
      </Button>
    </aside>
  );
}
