"use client";

// /join/[code] in the browser. Loads the invite preview and who is signed in
// side by side, then shows one view, chosen by joinView (components/flow/
// joinFlow.ts): the invitation with sign-up links when signed out, "which one
// is you?" when signed in, the relationships step after adding yourself, and
// a welcome at the end. Being signed out is a normal state here, so this page
// does not use SessionGate.
import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Button, ButtonLink } from "@/components/flow/Button";
import { getApi } from "@/components/flow/client";
import { FormError } from "@/components/flow/FormError";
import { InviteWelcome } from "@/components/flow/InviteWelcome";
import { JoinForm } from "@/components/flow/JoinForm";
import { JoinRelationships } from "@/components/flow/JoinRelationships";
import {
  addedSelf,
  familyPhrase,
  joinView,
  relationshipPeople,
  stepAfterJoin,
  type JoinInputs,
  type JoinStep,
  type JoinView,
  type RelationshipPerson,
} from "@/components/flow/joinFlow";
import { PaperCard } from "@/components/flow/PaperCard";
import { hrefs, rememberFamily } from "@/components/flow/session";
import { signedInLabel } from "@/components/flow/settingsPage";
import { isApiError } from "@/lib/api";
import type { InvitePreview, JoinRequest, JoinResponse, MeResponse } from "@/lib/types";

import { JoinPlaceholder, JoinShell } from "./JoinShell";

const LOAD_ERROR = "Something went wrong while opening this invitation. Please try again.";

type MeState =
  | { status: "loading" }
  | { status: "signed-in"; me: MeResponse }
  | { status: "signed-out" }
  | { status: "error"; error: unknown };

/** A sentence safe to show: the API's own message, or a friendly fallback. */
function loadErrorMessage(err: unknown): string {
  return isApiError(err) && err.detail ? err.detail : LOAD_ERROR;
}

export function JoinScreen(props: { code: string }) {
  const { code } = props;

  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [meState, setMeState] = useState<MeState>({ status: "loading" });
  const [joined, setJoined] = useState<JoinResponse | null>(null);
  const [step, setStep] = useState<JoinStep>("choose");
  const [others, setOthers] = useState<RelationshipPerson[]>([]);
  // Bumped by "Try again" to load both again.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    // Ignore answers once this load is replaced or unmounted (strict mode mounts twice).
    let live = true;
    const api = getApi();
    api.previewInvite(code).then(
      (p) => {
        if (live) setPreview(p);
      },
      (e: unknown) => {
        if (live) setPreviewError(e ?? new Error("preview failed"));
      },
    );
    api.me().then(
      (me) => {
        if (live) setMeState({ status: "signed-in", me });
      },
      (e: unknown) => {
        if (!live) return;
        setMeState(isApiError(e) && e.status === 401 ? { status: "signed-out" } : { status: "error", error: e });
      },
    );
    return () => {
      live = false;
    };
  }, [code, attempt]);

  const me = meState.status === "signed-in" ? meState.me : null;
  const inputs: JoinInputs = { preview, previewError, me, meStatus: meState.status, joined, step };
  const view = joinView(inputs);

  // Someone already in this family opens it next time.
  const familyId = preview?.familyId ?? null;
  useEffect(() => {
    if (view === "already-member" && familyId) rememberFamily(familyId);
  }, [view, familyId]);

  const retry = useCallback(() => {
    setPreview(null);
    setPreviewError(null);
    setMeState({ status: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  /** POST the join; ApiErrors reject so JoinForm shows them, and nothing here changes. */
  const handleJoin = useCallback(
    async (body: JoinRequest) => {
      const api = getApi();
      const res = await api.joinInvite(code, body);
      rememberFamily(res.family.id);
      let people: RelationshipPerson[] = [];
      if (addedSelf(body)) {
        try {
          people = relationshipPeople(await api.getTree(res.family.id), res.person.id);
        } catch {
          // Joined all the same. Relationships can be set later from the person's page.
          people = [];
        }
      }
      setOthers(people);
      setJoined(res);
      setStep(stepAfterJoin(body, people));
    },
    [code],
  );

  const handleSaveRelationships = useCallback(
    async (r: { parentIds: string[]; spouseIds: string[] }) => {
      if (!joined) return;
      await getApi().updatePerson(joined.family.id, joined.person.id, {
        parentIds: r.parentIds,
        spouseIds: r.spouseIds,
      });
      setStep("done");
    },
    [joined],
  );

  const skipRelationships = useCallback(() => setStep("done"), []);

  return (
    <ViewFocus view={view}>
      {renderView(view, {
        code,
        preview,
        previewError,
        me,
        meError: meState.status === "error" ? meState.error : null,
        joined,
        others,
        onRetry: retry,
        onJoin: handleJoin,
        onSaveRelationships: handleSaveRelationships,
        onSkipRelationships: skipRelationships,
      })}
    </ViewFocus>
  );
}

interface ViewProps {
  code: string;
  preview: InvitePreview | null;
  previewError: unknown;
  me: MeResponse | null;
  meError: unknown;
  joined: JoinResponse | null;
  others: RelationshipPerson[];
  onRetry: () => void;
  onJoin: (body: JoinRequest) => Promise<void>;
  onSaveRelationships: (r: { parentIds: string[]; spouseIds: string[] }) => Promise<void>;
  onSkipRelationships: () => void;
}

function renderView(view: JoinView, p: ViewProps): ReactNode {
  const backHere = hrefs.join(p.code);
  switch (view) {
    case "loading":
      return <JoinPlaceholder />;

    case "gone":
      return (
        <JoinShell>
          <PaperCard className="text-center">
            <p className="kicker">An invitation</p>
            <h1 data-view-heading="" tabIndex={-1} className="focus:outline-none mt-3 text-[1.875rem] break-words text-ink sm:text-[2.25rem]">
              This invite link no longer works
            </h1>
            <p className="mx-auto mt-4 max-w-[24rem] text-[1.0625rem] leading-relaxed text-ink-soft">
              Ask whoever sent it for a new one. Invite links last two weeks, and a family&rsquo;s admin
              can turn one off.
            </p>
            <hr aria-hidden="true" className="stitch-rule mx-auto mt-7 w-24" />
            <div className="mt-7">
              <ButtonLink href={hrefs.home()} variant="secondary">
                Go to Stitch
              </ButtonLink>
            </div>
          </PaperCard>
        </JoinShell>
      );

    case "error":
      return (
        <JoinShell>
          <PaperCard className="flex flex-col gap-5">
            <h1 data-view-heading="" tabIndex={-1} className="focus:outline-none text-[1.75rem] break-words text-ink sm:text-[2rem]">
              We couldn&rsquo;t open this invitation
            </h1>
            <FormError message={loadErrorMessage(p.meError ?? p.previewError)} />
            <div>
              <Button onClick={p.onRetry}>
                Try again
              </Button>
            </div>
          </PaperCard>
        </JoinShell>
      );

    case "signed-out":
      return (
        <JoinShell width="wide">
          <InviteWelcome
            familyName={p.preview?.familyName ?? ""}
            signedIn={false}
            loginHref={hrefs.login(backHere)}
            signupHref={hrefs.signup(backHere)}
          />
        </JoinShell>
      );

    case "already-member": {
      const fid = p.preview?.familyId ?? "";
      return (
        <JoinShell>
          <PaperCard className="text-center">
            <p className="kicker">An invitation</p>
            <h1 data-view-heading="" tabIndex={-1} className="focus:outline-none mt-3 text-[1.875rem] break-words text-ink sm:text-[2.25rem]">
              You&rsquo;re already in {familyPhrase(p.preview?.familyName ?? "")}
            </h1>
            <p className="mx-auto mt-4 max-w-[24rem] text-[1.0625rem] leading-relaxed text-ink-soft">
              This link is for a family you belong to, so there&rsquo;s nothing to join. Their book is
              waiting for you.
            </p>
            <hr aria-hidden="true" className="stitch-rule mx-auto mt-7 w-24" />
            <div className="mt-7 flex flex-col items-stretch justify-center gap-3 sm:flex-row sm:items-center">
              <ButtonLink href={hrefs.home()}>Open the family tree</ButtonLink>
              {fid ? (
                <ButtonLink href={hrefs.settings(fid)} variant="secondary">
                  Family settings
                </ButtonLink>
              ) : null}
            </div>
          </PaperCard>
        </JoinShell>
      );
    }

    case "choose":
      return (
        <JoinShell width="wide">
          <InviteWelcome
            familyName={p.preview?.familyName ?? ""}
            signedIn
            loginHref={hrefs.login(backHere)}
            signupHref={hrefs.signup(backHere)}
          >
            <JoinForm
              unclaimed={p.preview?.unclaimedPeople ?? []}
              suggestedName={p.me?.user.name}
              onJoin={p.onJoin}
            />
            {p.me ? (
              <p className="mt-5 text-center text-[0.875rem] leading-snug break-words text-ink-soft">
                Signed in as {signedInLabel(p.me.user)}.{" "}
                <Link href={hrefs.login(backHere)} className="font-semibold">
                  Not you?
                </Link>
              </p>
            ) : null}
          </InviteWelcome>
        </JoinShell>
      );

    case "relationships": {
      if (!p.joined) return <JoinPlaceholder />;
      return (
        <JoinShell width="wide">
          <header className="mb-6 text-center">
            <p className="kicker">You&rsquo;re in</p>
            <h1 data-view-heading="" tabIndex={-1} className="focus:outline-none mt-2 text-[1.5rem] break-words text-ink-soft sm:text-[1.75rem]">
              You joined {familyPhrase(p.joined.family.name)}
            </h1>
          </header>
          <JoinRelationships
            selfId={p.joined.person.id}
            selfName={p.joined.person.name}
            people={p.others}
            onSave={p.onSaveRelationships}
            onSkip={p.onSkipRelationships}
          />
        </JoinShell>
      );
    }

    case "done": {
      if (!p.joined) return <JoinPlaceholder />;
      const fid = p.joined.family.id;
      return (
        <JoinShell>
          <PaperCard className="text-center">
            <p className="kicker">You&rsquo;re in</p>
            <h1 data-view-heading="" tabIndex={-1} className="focus:outline-none mt-3 text-[2rem] break-words text-ink sm:text-[2.5rem]">
              Welcome to {familyPhrase(p.joined.family.name)}
            </h1>
            <p className="mx-auto mt-4 max-w-[24rem] text-[1.0625rem] leading-relaxed text-ink-soft">
              You&rsquo;re on the tree as <span className="font-semibold text-ink">{p.joined.person.name}</span>.
              Every memory you add becomes a page in the family&rsquo;s book.
            </p>
            <p className="caption mt-5" aria-hidden="true">
              start with any story, big or small
            </p>
            <hr aria-hidden="true" className="stitch-rule mx-auto mt-5 w-24" />
            <div className="mt-7 flex flex-col items-stretch justify-center gap-2 sm:flex-row sm:items-center sm:gap-4">
              <ButtonLink href={hrefs.add({ familyId: fid })}>Add your first memory</ButtonLink>
              <ButtonLink href={hrefs.home()} variant="quiet">
                See the family tree
              </ButtonLink>
            </div>
          </PaperCard>
        </JoinShell>
      );
    }
  }
}

/**
 * Moves focus to the new view's heading when the view changes after the
 * first one (a join or a saved step replaces the whole page), so keyboard and
 * screen reader users land on what just appeared.
 */
function ViewFocus(props: { view: JoinView; children: ReactNode }) {
  const { view, children } = props;
  const ref = useRef<HTMLDivElement>(null);
  const first = useRef<JoinView | null>(null);

  useEffect(() => {
    if (view === "loading") return;
    if (first.current === null) {
      first.current = view;
      return;
    }
    const target =
      ref.current?.querySelector<HTMLElement>("[data-view-heading]") ??
      ref.current?.querySelector<HTMLElement>("h1, h2");
    if (!target) return;
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
    target.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [view]);

  return <div ref={ref}>{children}</div>;
}
