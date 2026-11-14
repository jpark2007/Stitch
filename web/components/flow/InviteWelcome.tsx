"use client";

// The top of /join/[code]: a warm welcome to the family's book. Signed out,
// it offers sign up (primary) or log in; signed in, it shows its children
// (the JoinForm). Presentational: the page builds the hrefs (with a return
// path back to the invite) and decides who is signed in.
import { useId, type ReactNode } from "react";

import { ButtonLink } from "./Button";
import { inviteHeading } from "./join";

export function InviteWelcome(props: {
  familyName: string;
  signedIn: boolean;
  loginHref: string;
  signupHref: string;
  children?: ReactNode;
}) {
  const { familyName, signedIn, loginHref, signupHref, children } = props;
  const headingId = `${useId()}heading`;

  return (
    <section aria-labelledby={headingId} className="mx-auto w-full max-w-[36rem] min-w-0">
      <header className="text-center">
        <p className="kicker">An invitation</p>
        <h1
          id={headingId}
          className="mt-3 text-[clamp(2rem,7vw,2.75rem)] text-balance break-words text-ink"
        >
          {inviteHeading(familyName)}
        </h1>
        <p className="mx-auto mt-4 max-w-[30rem] text-[1.0625rem] leading-relaxed text-ink-soft">
          Stitch turns the stories your family tells into a pop-up book you can page through
          together, one memory at a time.
        </p>
        <hr aria-hidden="true" className="stitch-rule mx-auto mt-7 w-24" />
      </header>

      {signedIn ? (
        <div className="mt-8">{children}</div>
      ) : (
        <div className="mt-8 text-center">
          <p className="text-[0.9375rem] leading-snug text-ink-soft">
            Make an account to join them. It only takes a minute.
          </p>
          <div className="mt-5 flex flex-col items-stretch justify-center gap-3 sm:flex-row sm:items-center">
            <ButtonLink href={signupHref}>Create an account</ButtonLink>
            <ButtonLink href={loginHref} variant="secondary">
              I already have one
            </ButtonLink>
          </div>
        </div>
      )}
    </section>
  );
}
