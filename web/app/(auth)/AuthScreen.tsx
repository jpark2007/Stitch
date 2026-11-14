"use client";

// The log in and sign up screens. Both read `?next=` (where to go once signed
// in), so they render inside a <Suspense> boundary in their page.tsx; the
// boundary's fallback is <AuthScreenFallback>, the same page without the form.
//
// A signed-in visitor is sent on (to ?next=, starting a family, or home). The
// session cookie is httpOnly, so the page asks /me on arrival, but only when
// this browser holds the signed-in hint (session.ts): a signed-out visit makes
// no requests until the form is sent. While that check runs the page shows the
// same blank sheet as the fallback, so the form never flashes on the way out.
//
// If sign up succeeds but the me() after it fails, the account exists, so the
// person is sent to log in rather than shown an error (a second Create account
// would only get a 409). The log in page then shows a calm notice with their
// email filled in, handed over through sessionStorage (see authPages.ts).
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useLayoutEffect, useState } from "react";

import { isApiError } from "@/lib/api";

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
import {
  destinationAfterAuth,
  forgetSignedIn,
  hrefs,
  recallSignedIn,
  rememberSignedIn,
  safeNext,
} from "@/components/flow/session";
import { SignupForm } from "@/components/flow/SignupForm";
import type { LoginRequest, MeResponse, SignupRequest } from "@/lib/types";

const AUTH_TITLES: Record<AuthKind, string> = {
  login: "Welcome back",
  signup: "Make your account",
};

/** How long the signed-in check may hold the form back before showing it anyway. */
const SIGNED_IN_CHECK_MS = 4000;

export function AuthScreen(props: { kind: AuthKind }) {
  const { kind } = props;
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  // The email handed over by a sign up whose me() failed (log in only).
  const [handoff, setHandoff] = useState<string | null>(null);
  // True while asking /me whether this visitor is already signed in.
  const [checking, setChecking] = useState(false);

  // Already signed in? Send them on. Before paint, so a signed-in visitor sees
  // the blank sheet rather than the form. Runs again each time a hidden page
  // is shown (cacheComponents), which is when a signed-in person comes back.
  useLayoutEffect(() => {
    if (!recallSignedIn()) return;
    let live = true;
    // Holding the form back while the server is asked, once per arrival.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setChecking(true);
    // A slow server never keeps the form away for long.
    const giveUp = window.setTimeout(() => {
      if (live) setChecking(false);
    }, SIGNED_IN_CHECK_MS);
    getApi()
      .me()
      .then(
        (me) => {
          // Keep the blank sheet up until the next page arrives.
          if (live) router.replace(destinationAfterAuth(me, next));
        },
        (e: unknown) => {
          if (isApiError(e) && e.status === 401) forgetSignedIn();
          if (live) setChecking(false);
        },
      )
      .finally(() => window.clearTimeout(giveUp));
    return () => {
      live = false;
      window.clearTimeout(giveUp);
      setChecking(false);
    };
  }, [router, next]);

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
      rememberSignedIn();
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
      rememberSignedIn();
      router.replace(destinationAfterAuth(me, next));
    },
    [router, next],
  );

  // While checking, the form stays mounted but hidden behind the blank sheet:
  // in next dev the server renders the form, and what someone typed into it
  // before the check began (the forms adopt it on mount) must still be there
  // when the server says "not signed in" and the form comes back.
  return (
    <FlowPage title={AUTH_TITLES[kind]} lede={authLede(kind, next)}>
      {checking ? <BlankSheet kind={kind} /> : null}
      <div hidden={checking}>
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
      </div>
    </FlowPage>
  );
}

/** A blank sheet roughly the form's height, while the form is not ready to show. */
function BlankSheet(props: { kind: AuthKind }) {
  return (
    <div role="status" aria-busy="true">
      <PaperCard className={props.kind === "login" ? "min-h-[17rem]" : "min-h-[24rem]"}>
        <span className="sr-only">Loading the form…</span>
      </PaperCard>
    </div>
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
export function AuthScreenFallback(props: { kind: AuthKind; next?: string | null }) {
  const { kind, next = null } = props;
  return (
    <FlowPage title={AUTH_TITLES[kind]} lede={authLede(kind, next)}>
      <BlankSheet kind={kind} />
    </FlowPage>
  );
}
