"use client";

// The log in and sign up screens. Both read `?next=` (where to go once signed
// in), so they render inside a <Suspense> boundary in their page.tsx; the
// boundary's fallback is <AuthScreenFallback>, the same page without the form.
//
// Nothing here asks the server who is signed in on mount: a signed-out visit
// makes no requests until the form is sent.
//
// If sign up succeeds but the me() after it fails, the account exists, so the
// person is sent to log in rather than shown an error (a second Create account
// would only get a 409). The log in page then shows a calm notice with their
// email filled in, handed over through sessionStorage (see authPages.ts).
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useLayoutEffect, useState } from "react";

import {
  afterSignupFailure,
  authLede,
  readSignupHandoff,
  SIGNUP_READY_NOTICE,
  type AuthKind,
} from "@/components/flow/authPages";
import { demoCredentials, getApi } from "@/components/flow/client";
import { FlowPage } from "@/components/flow/FlowPage";
import { LoginForm } from "@/components/flow/LoginForm";
import { PaperCard } from "@/components/flow/PaperCard";
import { destinationAfterAuth, hrefs, safeNext } from "@/components/flow/session";
import { SignupForm } from "@/components/flow/SignupForm";
import type { LoginRequest, MeResponse, SignupRequest } from "@/lib/types";

const AUTH_TITLES: Record<AuthKind, string> = {
  login: "Welcome back",
  signup: "Make your account",
};

export function AuthScreen(props: { kind: AuthKind }) {
  const { kind } = props;
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  // The email handed over by a sign up whose me() failed (log in only).
  const [handoff, setHandoff] = useState<string | null>(null);

  // Before paint, so the form first shows with the email already in it. This
  // runs again each time a hidden /login becomes visible; the handoff is read
  // once and removed, so a later run (or strict mode's second run) finds
  // nothing and keeps what is shown. Reading it during render instead would
  // consume it in a render React may throw away, and a lazy useState would
  // miss a /login that was already mounted.
  useLayoutEffect(() => {
    if (kind !== "login") return;
    const email = readSignupHandoff();
    // Syncing from sessionStorage, outside React, once per arrival.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (email) setHandoff(email);
  }, [kind]);

  // Sign in, ask who we are, then go where they were headed (or to starting a
  // family, or home). An ApiError rejection is left for the form to show.
  const login = useCallback(
    async (body: LoginRequest) => {
      const api = getApi();
      await api.login(body);
      const me = await api.me();
      router.replace(destinationAfterAuth(me, next));
    },
    [router, next],
  );

  // A rejected signup() is the form's to show. Once it resolves the account
  // exists: a failed me() sends the person to log in instead.
  const signup = useCallback(
    async (body: SignupRequest) => {
      const api = getApi();
      await api.signup(body);
      let me: MeResponse;
      try {
        me = await api.me();
      } catch {
        router.replace(afterSignupFailure(body.email, next));
        return;
      }
      router.replace(destinationAfterAuth(me, next));
    },
    [router, next],
  );

  return (
    <FlowPage title={AUTH_TITLES[kind]} lede={authLede(kind, next)}>
      {kind === "login" ? (
        <>
          {handoff ? <ReadyNotice /> : null}
          {/* Keyed on the handoff so a /login already mounted (hidden) takes the email up. */}
          <LoginForm
            key={handoff ?? ""}
            onSubmit={login}
            signupHref={hrefs.signup(next)}
            demo={demoCredentials()}
            initialEmail={handoff ?? ""}
          />
        </>
      ) : (
        <SignupForm onSubmit={signup} loginHref={hrefs.login(next)} />
      )}
    </FlowPage>
  );
}

/** One calm line over the log in form after sign up handed over. */
function ReadyNotice() {
  return (
    <p
      role="status"
      className="mb-6 rounded-paper border border-rule border-l-[3px] border-l-sage bg-paper-light px-4 py-3 text-[0.9375rem] leading-snug text-ink shadow-paper"
    >
      {SIGNUP_READY_NOTICE}
    </p>
  );
}

/**
 * What the page shows before the search params are known (the prerendered
 * HTML): the title and the plain lede over a blank sheet roughly the form's
 * height, so nothing jumps when the form arrives.
 */
export function AuthScreenFallback(props: { kind: AuthKind }) {
  const { kind } = props;
  return (
    <FlowPage title={AUTH_TITLES[kind]} lede={authLede(kind, null)}>
      <div role="status" aria-busy="true">
        <PaperCard className={kind === "login" ? "min-h-[17rem]" : "min-h-[24rem]"}>
          <span className="sr-only">Loading the form…</span>
        </PaperCard>
      </div>
    </FlowPage>
  );
}
