// Pure logic behind the member list on family settings: join each membership
// to the person it claimed (members carry no name), decide who an admin may
// remove or change the role of, and order the list. Admins remove members and
// make members admins or admins members; nobody removes themselves here, an
// admin may step down only while another admin remains, and the family never
// loses its last admin (ARCHITECTURE.md section 4; PATCH and DELETE
// /families/{fid}/members/{userId}).
//
// Erasable TypeScript with type-only imports, so `node --test` can load this
// file directly (see __tests__/settingsParts.test.mjs).
import type { Member, Person } from "../../lib/types";

export interface MemberRow {
  userId: string;
  role: "admin" | "member";
  name: string;
  personId: string | null;
  /** The person's thread colour (Person.character.color), if any. */
  color: string | null;
  joinedAt: string | null;
  isYou: boolean;
  removable: boolean;
  /** The viewer may change this member's role (then `roleAction` is set). */
  canChangeRole: boolean;
  /**
   * What the role control would do: "promote" makes a member an admin,
   * "demote" makes an admin a member (on the viewer's own row, stepping
   * down). Null when the viewer can't change this member's role.
   */
  roleAction: RoleAction | null;
}

export type RoleAction = "promote" | "demote";

/** The role a role action sets. */
export function roleAfter(action: RoleAction): MemberRow["role"] {
  return action === "promote" ? "admin" : "member";
}

/** Who a sentence names: "Ana", or `fallback` when we don't know their name. */
function whoIn(row: Pick<MemberRow, "name" | "personId">, fallback: string): string {
  return row.personId ? row.name : fallback;
}

/**
 * The words for a role change on one row: the quiet button (`labelBefore`,
 * then `labelHidden` for screen readers only, then `labelAfter`, so it reads
 * "Make admin" and is named "Make Ana an admin"), the inline confirm (`question`,
 * `detail`, `confirm`, `busy`) and the live announcement once it worked.
 */
export interface RoleChangeCopy {
  labelBefore: string;
  labelHidden: string;
  labelAfter: string;
  question: string;
  detail: string;
  confirm: string;
  busy: string;
  done: string;
}

export function roleChangeCopy(row: Pick<MemberRow, "name" | "personId" | "isYou">, action: RoleAction): RoleChangeCopy {
  if (row.isYou) {
    return {
      labelBefore: "Step down as admin",
      labelHidden: "",
      labelAfter: "",
      question: "Step down as admin?",
      detail:
        "You’ll stay in the family, but you won’t be able to invite people or manage members. Another admin can make you an admin again.",
      confirm: "Step down",
      busy: "Stepping down…",
      done: "You stepped down. You’re now a member of this family.",
    };
  }
  const who = whoIn(row, "this family member");
  const Who = whoIn(row, "The family member");
  if (action === "promote") {
    return {
      labelBefore: "Make",
      labelHidden: ` ${who} an`,
      labelAfter: " admin",
      question: `Make ${who} an admin?`,
      detail: "Admins can invite people, remove members and change who is an admin.",
      confirm: "Make admin",
      busy: "Saving…",
      done: `${Who} is now an admin.`,
    };
  }
  return {
    labelBefore: "Make",
    labelHidden: ` ${who} a`,
    labelAfter: " member",
    question: `Make ${who} a member?`,
    detail: "They’ll stay in the family, but won’t be able to invite people or manage members.",
    confirm: "Make member",
    busy: "Saving…",
    done: `${Who} is now a member.`,
  };
}

function statusOf(err: unknown): number | undefined {
  const status = typeof err === "object" && err !== null ? (err as { status?: unknown }).status : undefined;
  return typeof status === "number" ? status : undefined;
}

function ownMessage(err: unknown): string {
  if (err instanceof Error && err.message.trim()) return err.message;
  return "Something went wrong. Please try again.";
}

/** A 403 from an admin route: the viewer was an admin when the page loaded, and isn't now. */
export function notAdminMessage(what: string): string {
  return `Only admins can ${what}, and you’re not an admin of this family any more.`;
}

function lastAdminMessage(row: Pick<MemberRow, "name" | "personId">): string {
  return `${whoIn(row, "This family member")} is the only admin left, and a family always needs one. Make someone else an admin first.`;
}

/**
 * The message for a failed role change, in plain words. A 409 is the API's
 * LAST_ADMIN ("A family needs at least one admin"), a 404 a member who has
 * left, and a 403 a viewer who is no longer an admin; anything else (a 5xx,
 * no connection) keeps the error's own message, which the API client already
 * words for people.
 */
export function roleChangeError(err: unknown, row: Pick<MemberRow, "name" | "personId" | "isYou">): string {
  const status = statusOf(err);
  if (status === 409) {
    return row.isYou
      ? "You’re the only admin, so you can’t step down yet. Make someone else an admin first."
      : lastAdminMessage(row);
  }
  if (status === 404 && !row.isYou) {
    return `${whoIn(row, "This family member")} isn’t in the family any more.`;
  }
  if (status === 403) return notAdminMessage("change who is an admin");
  return ownMessage(err);
}

/**
 * What removing a member came to. A 404 means they had already left, which
 * is what the admin wanted: `gone` is true and `message` says so calmly. A
 * 409 is the last admin, a 403 a viewer who is no longer an admin; anything
 * else keeps the error's own message.
 */
export function removeMemberOutcome(
  err: unknown,
  row: Pick<MemberRow, "name" | "personId">,
): { gone: boolean; message: string } {
  const status = statusOf(err);
  if (status === 404) return { gone: true, message: `${whoIn(row, "The family member")} had already left the family.` };
  if (status === 409) return { gone: false, message: lastAdminMessage(row) };
  if (status === 403) return { gone: false, message: notAdminMessage("remove people") };
  return { gone: false, message: ownMessage(err) };
}

/**
 * True for a refusal because the viewer is no longer an admin (a 403). The
 * settings page then fetches the family, drops every admin control and says
 * so once at the top (settingsPage.ts NO_LONGER_ADMIN), so the row needs no
 * message of its own saying the same thing again.
 */
export function isLostAdmin(err: unknown): boolean {
  return statusOf(err) === 403;
}

/**
 * A heading above a row's failure message when the message alone doesn't say
 * what failed: a 5xx, no connection or anything unexpected ("Something went
 * wrong on our side…" says nothing about Ana). Null for a 403, 404 or 409,
 * whose own words (roleChangeError, removeMemberOutcome) already say it.
 */
export function memberFailureTitle(
  err: unknown,
  row: Pick<MemberRow, "name" | "personId" | "isYou">,
  action: RoleAction | "remove",
): string | null {
  const status = statusOf(err);
  if (status === 403 || status === 404 || status === 409) return null;
  const who = whoIn(row, "this family member");
  if (action === "remove") return `We couldn’t remove ${who}`;
  if (action === "promote") return `We couldn’t make ${who} an admin`;
  return row.isYou ? "You’re still an admin" : `We couldn’t make ${who} a member`;
}

/** Shown when no person in the tree is claimed by this member's account. */
export const UNKNOWN_MEMBER_NAME = "A family member";

type PersonLike = Pick<Person, "id" | "userId" | "name" | "character">;

export function memberRows(
  members: Member[],
  people: PersonLike[],
  viewerUserId: string,
  viewerIsAdmin: boolean,
): MemberRow[] {
  const byUser = new Map<string, PersonLike>();
  for (const p of people ?? []) {
    if (p && p.userId && !byUser.has(p.userId)) byUser.set(p.userId, p);
  }
  const list = (members ?? []).filter((m) => m && m.userId);
  const adminCount = list.filter((m) => m.role === "admin").length;

  const rows = list.map((m): MemberRow => {
    const person = byUser.get(m.userId);
    const name = person?.name?.trim() || UNKNOWN_MEMBER_NAME;
    const isYou = m.userId === viewerUserId;
    const lastAdmin = m.role === "admin" && adminCount <= 1;
    // Promote any member but yourself; demote any admin but the last one (on
    // your own row that is stepping down, so only while another admin remains).
    let roleAction: RoleAction | null = null;
    if (viewerIsAdmin) {
      if (m.role === "member" && !isYou) roleAction = "promote";
      else if (m.role === "admin" && !lastAdmin) roleAction = "demote";
    }
    return {
      userId: m.userId,
      role: m.role,
      name,
      personId: person?.id ?? null,
      color: person?.character?.color || null,
      joinedAt: m.joinedAt ?? null,
      isYou,
      removable: viewerIsAdmin && !isYou && !lastAdmin,
      canChangeRole: roleAction !== null,
      roleAction,
    };
  });

  return rows.sort((a, b) => {
    if (a.isYou !== b.isYou) return a.isYou ? -1 : 1;
    if (a.role !== b.role) return a.role === "admin" ? -1 : 1;
    return a.name.localeCompare(b.name) || a.userId.localeCompare(b.userId);
  });
}
