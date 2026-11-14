"use client";

// The log in and sign up screens. Both read `?next=` (where to go once signed
// in), so they render inside a <Suspense> boundary in their page.tsx; the
// boundary's fallback is <AuthScreenFallback>, the same page without the form.
//
// Nothing here asks the server who is signed in on mount: a signed-out visit
// makes no requests until the form is sent.
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

import { authLede, type AuthKind } from "@/components/flow/authPages";
import { demoCredentials, getApi } from "@/components/flow/client";
import { FlowPage } from "@/components/flow/FlowPage";
import { LoginForm } from "@/components/flow/LoginForm";
import { PaperCard } from "@/components/flow/PaperCard";
import { destinationAfterAuth, hrefs, safeNext } from "@/components/flow/session";
import { SignupForm } from "@/components/flow/SignupForm";
import type { LoginRequest, SignupRequest } from "@/lib/types";

const AUTH_TITLES: Record<AuthKind, string> = {
  login: "Welcome back",
  signup: "Make your account",
};

export function AuthScreen(props: { kind: AuthKind }) {
  const { kind } = props;
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));

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

  const signup = useCallback(
    async (body: SignupRequest) => {
      const api = getApi();
      await api.signup(body);
      const me = await api.me();
      router.replace(destinationAfterAuth(me, next));
    },
    [router, next],
  );

  return (
    <FlowPage title={AUTH_TITLES[kind]} lede={authLede(kind, next)}>
      {kind === "login" ? (
        <LoginForm onSubmit={login} signupHref={hrefs.signup(next)} demo={demoCredentials()} />
      ) : (
        <SignupForm onSubmit={signup} loginHref={hrefs.login(next)} />
      )}
    </FlowPage>
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
