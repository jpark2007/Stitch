// The decisions behind the /join/[code] page: which view to show for what has
// loaded so far, which errors mean the link is gone, whether a join added a
// new person (and so needs the relationships step), and who can be picked as
// a parent or spouse afterwards. The page holds the state and asks these.
//
// Erasable TypeScript only. The one runtime import is @/lib/api (itself free
// of runtime imports); __tests__/joinFlow.test.mjs maps "@/" for node --test.
// Tests: components/flow/__tests__/joinFlow.test.mjs.

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

/** The link no longer leads anywhere: ApiError 404 (unknown, revoked) or 410 (expired). */
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
