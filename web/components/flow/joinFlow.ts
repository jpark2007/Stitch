// The decisions behind the /join/[code] page: which view to show for what has
// loaded so far, which errors mean the link is gone, whether a join added a
// new person (and so needs the relationships step), and who can be picked as
// a parent or spouse afterwards. The page holds the state and asks these.
//
// Erasable TypeScript only. The runtime imports are @/lib/api and
// @/components/flow/session (both free of runtime imports);
// __tests__/joinFlow.test.mjs maps "@/" for node --test.
// Tests: components/flow/__tests__/joinFlow.test.mjs.

import { hrefs } from "@/components/flow/session";
import { isApiError } from "@/lib/api";
import type { InvitePreview, JoinRequest, JoinResponse, MeResponse, TreeResponse } from "@/lib/types";

export type JoinView =
  | "loading"
  | "gone"
  | "error"
  | "signed-out"
  | "already-member"
  | "choose"
  | "relationships"
  | "done";

export type JoinStep = "choose" | "relationships" | "done";

export interface JoinInputs {
  preview: InvitePreview | null;
  /** Why GET /invites/{code} failed, or null. */
  previewError: unknown;
  me: MeResponse | null;
  meStatus: "loading" | "signed-in" | "signed-out" | "error";
  /** The answer to POST /invites/{code}/join, once this visit has joined. */
  joined: JoinResponse | null;
  step: JoinStep;
}

/** The link no longer leads anywhere: ApiError 404 (unknown, revoked, expired) or 410. */
export function isGoneError(err: unknown): boolean {
  return isApiError(err) && (err.status === 404 || err.status === 410);
}

/**
 * The one view for this moment. In order: a gone link wins over everything
 * (signed in or not, nothing else on the page can help); any other failure is
 * the retry sheet; then wait for both loads; signed-out visitors only see the
 * invitation (who they are decides the rest); a join made on this visit moves
 * through its own steps; and only then does membership, judged by family id,
 * pick between "already in" and choosing who you are.
 */
export function joinView(i: JoinInputs): JoinView {
  if (i.previewError != null) return isGoneError(i.previewError) ? "gone" : "error";
  if (i.meStatus === "error") return "error";
  if (!i.preview || i.meStatus === "loading") return "loading";
  if (i.meStatus === "signed-out") return "signed-out";
  if (i.joined && i.step !== "choose") return i.step;
  if (!i.me) return "loading";
  const familyId = i.preview.familyId;
  if (!i.joined && i.me.families.some((f) => f.id === familyId)) return "already-member";
  return "choose";
}

/** True when the join body adds a new person rather than claiming one. */
export function addedSelf(body: JoinRequest): boolean {
  return body.person != null;
}

/**
 * Where a successful join goes next: someone who added themselves picks their
 * parents and spouses when there is anyone else on the tree to pick; a claim
 * (already placed on the tree) goes straight to the welcome.
 */
export function stepAfterJoin(body: JoinRequest, others: readonly unknown[]): Exclude<JoinStep, "choose"> {
  return addedSelf(body) && others.length > 0 ? "relationships" : "done";
}

export interface RelationshipPerson {
  id: string;
  name: string;
  born?: number | null;
  color?: string | null;
}

/** Everyone on the tree except the person who just joined, as the relationship picker takes them. */
export function relationshipPeople(
  tree: Pick<TreeResponse, "people">,
  selfId: string,
): RelationshipPerson[] {
  const out: RelationshipPerson[] = [];
  for (const p of tree.people) {
    if (p.id === selfId) continue;
    const color = p.character?.color;
    out.push({
      id: p.id,
      name: p.name,
      born: p.born ?? null,
      color: typeof color === "string" && color ? color : null,
    });
  }
  return out;
}

/**
 * Why a link is gone, as far as the answer says. The API answers 404 for an
 * unknown, revoked or expired code alike ("Invite not found or expired"), so
 * 404 is "unknown" and the card names every cause; a 410 is "expired", or
 * "revoked" when its message says the link was turned off.
 */
export type GoneReason = "unknown" | "expired" | "revoked";

export function goneReason(err: unknown): GoneReason {
  if (!isApiError(err) || err.status !== 410) return "unknown";
  return /revok|turned off|switched off|disabled/i.test(err.detail) ? "revoked" : "expired";
}

/** What the "this link no longer works" card says for each reason. */
export function goneCopy(reason: GoneReason): { title: string; body: string } {
  switch (reason) {
    case "expired":
      return {
        title: "This invite link has expired",
        body: "Invite links last two weeks. Ask whoever sent it for a fresh one and you can join from there.",
      };
    case "revoked":
      return {
        title: "This invite link was turned off",
        body: "The family’s admin switched it off. If you still mean to join, ask them for a new link.",
      };
    default:
      return {
        title: "This invite link isn’t working",
        body: "It may have been copied only in part, turned off by the family’s admin, or run past its two weeks. Ask whoever sent it for a fresh link.",
      };
  }
}

/**
 * The one way out of a dead link: your own tree when you have one, starting a
 * family when you have none, and logging in when signed out. While who is
 * signed in is still loading (or failed), plain home.
 */
export function goneWayOut(
  me: Pick<MeResponse, "families"> | null,
  meStatus: JoinInputs["meStatus"],
): { label: string; href: string } {
  if (meStatus === "signed-out") return { label: "Log in", href: hrefs.login() };
  if (me && me.families.length === 0) return { label: "Start your own family", href: hrefs.newFamily() };
  if (me) return { label: "Open your family tree", href: hrefs.home() };
  return { label: "Go to Stitch", href: hrefs.home() };
}

export const SERVER_TROUBLE =
  "Something went wrong on our side. Please try again in a moment.";

/**
 * A sentence safe to show for a failed call: the API's own message for a
 * 4xx or a lost connection (status 0, already friendly), and a calm fallback
 * for a 5xx, whose detail is a bare "Internal Server Error", or anything that
 * is not an ApiError.
 */
export function friendlyError(err: unknown, fallback: string): string {
  if (!isApiError(err)) return fallback;
  if (err.status >= 500) return SERVER_TROUBLE;
  return err.detail.trim() || fallback;
}

/**
 * What a failed POST /invites/{code}/join means for the page:
 * - "taken": a claim answered 409, someone claimed that person first; the
 *   page reloads the list and asks again.
 * - "recheck": a 404 or 410; the link (or the person) may be gone since the
 *   preview, so the page asks for the preview again to find out which.
 * - "show": anything else, for the form to show as it is.
 */
export type JoinFailure = "taken" | "recheck" | "show";

export function classifyJoinFailure(err: unknown, body: JoinRequest): JoinFailure {
  if (!isApiError(err)) return "show";
  if (err.status === 409 && body.personId != null) return "taken";
  if (err.status === 404 || err.status === 410) return "recheck";
  return "show";
}

/**
 * After a "taken" or "recheck" failure and a fresh preview: what to tell the
 * person. "taken" / "removed" when the person they picked has left the list
 * (claimed by someone else, or taken off the tree); otherwise "as-is", the
 * server's own message, for example a 409 because this account already has a
 * person in the family, where the picked person is still free.
 */
export function afterRecheck(
  body: JoinRequest,
  fresh: Pick<InvitePreview, "unclaimedPeople">,
  failure: Exclude<JoinFailure, "show">,
): "taken" | "removed" | "as-is" {
  if (body.personId == null) return "as-is";
  if (fresh.unclaimedPeople.some((p) => p.id === body.personId)) return "as-is";
  return failure === "taken" ? "taken" : "removed";
}

/** The note under the choices when the person picked was claimed first, or has gone. */
export function takenMessage(name: string | null | undefined, gone = false): string {
  const who = name?.trim() || "That person";
  return gone
    ? `${who} is no longer on the tree. Pick another name, or add yourself.`
    : `${who} was just claimed by someone else. Pick another name, or add yourself.`;
}

/** The first word of a name, for a friendly greeting: "Theo Rivera" -> "Theo". */
export function firstName(name: string | null | undefined): string {
  return (name ?? "").trim().split(/\s+/)[0] ?? "";
}

/**
 * The family as it reads mid-sentence, matching inviteHeading:
 * "The Riveras" -> "the Riveras", "Mao family" -> "the Mao family",
 * "Rivera" -> "the Rivera family".
 */
export function familyPhrase(familyName: string): string {
  const raw = familyName.trim().replace(/\s+/g, " ");
  if (!raw) return "the family";
  const name = raw.replace(/^the\s+/i, "");
  if (!name || /^the$/i.test(name)) return "the family";
  const startsWithThe = name !== raw;
  const hasFamilyWord = /\bfamily$/i.test(name);
  return `the ${startsWithThe || hasFamilyWord ? name : `${name} family`}`;
}
