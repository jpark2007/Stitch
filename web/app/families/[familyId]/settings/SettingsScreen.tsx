"use client";

// Family settings: who is in the family and their roles, the invite link
// (admins only), switching families, and signing out. ARCHITECTURE.md
// section 4 (roles, invite links, admins remove members) over the section 6
// routes GET /families/{fid}, GET /families/{fid}/tree, POST and DELETE
// /families/{fid}/invites and DELETE /families/{fid}/members/{userId}.
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { roleLabel } from "@/components/flow/accountForms";
import { Button, ButtonLink, FOCUS_RING } from "@/components/flow/Button";
import { FamilySwitcher } from "@/components/flow/FamilySwitcher";
import { FlowPage } from "@/components/flow/FlowPage";
import { FormError } from "@/components/flow/FormError";
import { InviteLinkCard } from "@/components/flow/InviteLinkCard";
import { pickActiveInvite } from "@/components/flow/invites";
import { MemberList } from "@/components/flow/MemberList";
import { memberRows, type MemberRow } from "@/components/flow/members";
import { PaperCard } from "@/components/flow/PaperCard";
import { SessionGate, useSession } from "@/components/flow/SessionGate";
import { hrefs, rememberFamily } from "@/components/flow/session";
import {
  findFamily,
  isWelcome,
  settingsLoadError,
  type SettingsLoad,
} from "@/components/flow/settingsPage";
import { isApiError } from "@/lib/api";
import type { Family, FamilySummary, TreeResponse } from "@/lib/types";

import { SettingsPlaceholder } from "./SettingsPlaceholder";

const BACK = { href: hrefs.home(), label: "Home" };

/**
 * The kit's quiet button look for "Sign out", sized to its text. Button
 * reserves room for its busy spinner even when idle, which would push this one
 * out of line with the quiet links beside it.
 */
const QUIET_ACTION =
  "inline-flex min-h-11 items-center rounded-card border border-transparent bg-transparent px-3 py-2.5 " +
  "font-sans text-base font-semibold leading-tight text-accent-deep underline-offset-[0.2em] " +
  "not-disabled:hover:underline not-disabled:hover:decoration-1 disabled:cursor-progress disabled:opacity-60 " +
  FOCUS_RING;

/** The route's client half: reads the URL, then waits for the session. */
export function SettingsScreen() {
  const params = useParams<{ familyId: string }>();
  const search = useSearchParams();
  const fid = typeof params?.familyId === "string" ? params.familyId : "";
  const welcome = isWelcome(search);

  return (
    <SessionGate>
      <FamilySettingsGate fid={fid} welcome={welcome} />
    </SessionGate>
  );
}

/** Not one of the user's families: say so calmly and ask the server nothing. */
function FamilySettingsGate(props: { fid: string; welcome: boolean }) {
  const { me } = useSession();
  const summary = findFamily(me.families, props.fid);
  if (!summary) return <FamilyNotFound />;
  // Keyed, so switching families starts each one from a clean load.
  return <FamilySettings key={summary.id} summary={summary} welcome={props.welcome} />;
}

function FamilyNotFound() {
  return (
    <FlowPage
      title="We couldn’t find that family"
      lede="It may have been removed, or this link is for a family you haven’t joined."
    >
      <div>
        <ButtonLink href={hrefs.home()} variant="secondary">
          Go home
        </ButtonLink>
      </div>
    </FlowPage>
  );
}

type Load =
  | { kind: "loading" }
  /** `now` and `origin` are read in the browser when the data lands, never during render. */
  | { kind: "ready"; family: Family; tree: TreeResponse; now: Date; origin: string }
  | SettingsLoad;

function FamilySettings(props: { summary: FamilySummary; welcome: boolean }) {
  const { summary, welcome } = props;
  const fid = summary.id;
  const { me, api, refresh } = useSession();
  const isAdmin = summary.role === "admin";

  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    let live = true;
    Promise.all([api.getFamily(fid), api.getTree(fid)]).then(
      ([family, tree]) => {
        if (!live) return;
        setLoad({ kind: "ready", family, tree, now: new Date(), origin: window.location.origin });
        rememberFamily(fid);
      },
      (err: unknown) => {
        if (!live) return;
        if (isApiError(err) && err.status === 401) {
          // Signed out since the page opened: refresh() sends us to log in.
          refresh().catch(() => setLoad(settingsLoadError(err)));
          return;
        }
        setLoad(settingsLoadError(err));
      },
    ).finally(() => {
      if (live) setRetrying(false);
    });
    return () => {
      live = false;
    };
  }, [api, fid, refresh, attempt]);

  const retry = useCallback(() => {
    setRetrying(true);
    setAttempt((n) => n + 1);
  }, []);

  /** Fetch the family again after a change. Rejects so the card shows why. */
  const reloadFamily = useCallback(async () => {
    const family = await api.getFamily(fid);
    const now = new Date();
    setLoad((prev) => (prev.kind === "ready" ? { ...prev, family, now } : prev));
  }, [api, fid]);

  const createInvite = useCallback(async () => {
    await api.createInvite(fid);
    await reloadFamily();
  }, [api, fid, reloadFamily]);

  const revokeInvite = useCallback(
    async (code: string) => {
      await api.revokeInvite(fid, code);
      await reloadFamily();
    },
    [api, fid, reloadFamily],
  );

  const removeMember = useCallback(
    async (userId: string) => {
      await api.removeMember(fid, userId);
      await reloadFamily();
    },
    [api, fid, reloadFamily],
  );

  if (load.kind === "not-found") return <FamilyNotFound />;

  const name = load.kind === "ready" ? load.family.name || summary.name : summary.name;
  const lede = welcome
    ? isAdmin
      ? "Your family is started. Send the link below to everyone you want in it."
      : "You’re in. Here’s who else is here."
    : "Who’s in the family, and how to bring everyone else in.";

  let body: ReactNode;
  if (load.kind === "error") {
    body = (
      <PaperCard className="flex flex-col gap-5">
        <FormError title="We couldn’t open this family’s settings" message={load.message} />
        <div>
          <Button onClick={retry} busy={retrying} busyLabel="Trying again…">
            Try again
          </Button>
        </div>
      </PaperCard>
    );
  } else if (load.kind === "loading") {
    body = <SettingsPlaceholder label="Gathering the family…" bare />;
  } else {
    const rows = memberRows(load.family.members ?? [], load.tree.people, me.user.id, isAdmin);
    body = (
      <div className="flex flex-col gap-8">
        {isAdmin ? (
          <InviteLinkCard
            invite={pickActiveInvite(load.family.invites, load.now)}
            origin={load.origin}
            now={load.now}
            onCreate={createInvite}
            onRevoke={revokeInvite}
          />
        ) : null}
        <MemberList rows={rows} onRemove={removeMember} />
        {isAdmin ? null : <InviteNote rows={rows} />}
      </div>
    );
  }

  return (
    <FlowPage title={name} lede={lede} back={BACK} width="wide">
      {body}
      <SettingsFooter fid={fid} role={summary.role} />
    </FlowPage>
  );
}

/** For members: the invite link is an admin's to share; name who to ask. */
function InviteNote(props: { rows: MemberRow[] }) {
  const admins = props.rows
    .filter((r) => r.role === "admin" && !r.isYou && r.personId)
    .map((r) => r.name);
  const ask = admins.length ? `Ask ${listOf(admins)} to share it with them.` : "Ask an admin to share it with them.";
  return (
    <section aria-labelledby="invite-note-heading">
      <h2 id="invite-note-heading" className="font-display text-[1.25rem] leading-tight text-ink">
        Invite link
      </h2>
      <p className="mt-2 text-[0.9375rem] leading-relaxed text-pretty text-ink-soft">
        Want someone else in the family? An admin can send them the invite link. {ask}
      </p>
    </section>
  );
}

/** "David", "David and Rosa", "David, Rosa and Luis". */
function listOf(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The quiet bottom of the page: switch family, add a memory, start another, sign out. */
function SettingsFooter(props: { fid: string; role: FamilySummary["role"] }) {
  const { fid, role } = props;
  const { me, signOut } = useSession();
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSignOut() {
    setError(null);
    setSigningOut(true);
    try {
      await signOut();
    } catch (err) {
      setError(isApiError(err) ? err.detail : "We couldn’t sign you out. Please try again.");
      setSigningOut(false);
    }
  }

  return (
    <div className="mt-10 flex flex-col gap-8 border-t border-dashed border-rule pt-8">
      <FamilySwitcher families={me.families} currentId={fid} hrefFor={hrefs.settings} />

      <div className="flex flex-col gap-3">
        <p className="text-[0.9375rem] leading-relaxed text-ink-soft">
          Signed in as <span className="font-medium text-ink">{me.user.name || me.user.email}</span>
          {me.user.name ? <span className="break-all"> ({me.user.email})</span> : null}, {roleLabel(role).toLowerCase()} of
          this family.
        </p>
        <nav aria-label="More" className="-ml-3 flex flex-wrap items-center gap-x-1 gap-y-1">
          <ButtonLink variant="quiet" href={hrefs.add({ familyId: fid })}>
            Add a memory
          </ButtonLink>
          <ButtonLink variant="quiet" href={hrefs.newFamily()}>
            Start another family
          </ButtonLink>
          <button
            type="button"
            disabled={signingOut}
            aria-busy={signingOut || undefined}
            onClick={onSignOut}
            className={QUIET_ACTION}
          >
            Sign out
          </button>
        </nav>
        {error ? <FormError message={error} /> : null}
      </div>
    </div>
  );
}
