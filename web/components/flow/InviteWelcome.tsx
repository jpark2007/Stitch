"use client";

// The top of /join/[code]: a warm welcome to the family's book. Signed out,
// it shows who is already on the tree waiting to be claimed and offers sign
// up (primary) or log in; signed in, it shows its children (the JoinForm).
// Presentational: the page builds the hrefs (with a return path back to the
// invite) and decides who is signed in.
import { useId, type ReactNode } from "react";

import type { PersonSummary } from "@/lib/types";
import { ButtonLink } from "./Button";
import { inviteHeading, waitingPeople } from "./join";

const FALLBACK_DOT = "#8f8173";

export function InviteWelcome(props: {
  familyName: string;
  signedIn: boolean;
  loginHref: string;
  signupHref: string;
  /** Unclaimed people from the preview; shown to signed-out visitors. */
  waiting?: PersonSummary[];
  children?: ReactNode;
}) {
  const { familyName, signedIn, loginHref, signupHref, waiting = [], children } = props;
  const headingId = `${useId()}heading`;
  const waitingId = `${useId()}waiting`;
  const shown = waitingPeople(waiting);

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
          {shown.people.length > 0 ? (
            <div className="mb-7">
              <p id={waitingId} className="caption text-[1.25rem] text-ink-soft">
                already on the tree, waiting for their owner
              </p>
              <ul
                aria-labelledby={waitingId}
                className="mx-auto mt-3 flex max-w-[30rem] list-none flex-wrap justify-center gap-2 p-0"
              >
                {shown.people.map((p) => (
                  <li
                    key={p.id}
                    className="inline-flex min-w-0 items-center gap-2 rounded-full border border-rule bg-paper-light px-3 py-1.5 text-[0.9375rem] text-ink shadow-paper"
                  >
                    <span
                      aria-hidden="true"
                      className="size-2.5 shrink-0 rounded-full ring-1 ring-rule"
                      style={{ backgroundColor: p.color || FALLBACK_DOT }}
                    />
                    <span className="min-w-0 break-words">{p.name}</span>
                  </li>
                ))}
                {shown.more > 0 ? (
                  <li className="inline-flex items-center px-2 py-1.5 text-[0.9375rem] text-ink-soft">
                    and {shown.more} more
                  </li>
                ) : null}
              </ul>
            </div>
          ) : null}
          <p className="mx-auto max-w-[28rem] text-[0.9375rem] leading-snug text-pretty text-ink-soft">
            {shown.people.length === 0
              ? "Make an account to join them. It only takes a minute."
              : `${shown.people.length === 1 ? "Is that you?" : "Is one of them you?"} Make an account to claim your place, or add yourself. It only takes a minute.`}
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
