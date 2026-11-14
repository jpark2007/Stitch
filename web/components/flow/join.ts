// Pure logic behind the join flow (/join/[code]): who you are on the tree
// (claim an unclaimed person, or add yourself), its checks, and the body for
// api.joinFamily. Erasable TypeScript only. The runtime imports (./validation,
// ./relationships) are extensionless for the Next build;
// __tests__/join.test.mjs adds a resolve hook so `node --test` finds them.
//
// The invite preview only lists unclaimed people, so relationships to people
// already on the tree are picked after joining (see JoinRelationships), not
// sent with the join request.
import type { JoinRequest, PersonSummary } from "@/lib/types";
import { sortPeopleForPicking } from "./relationships";
import { validateName, validateYear, type FieldErrors } from "./validation";

export type JoinChoice =
  | { mode: "claim"; personId: string }
  | { mode: "add"; name: string; born: string };

export type JoinField = "personId" | "name" | "born";

/** Every field, in form order, for mergeServerErrors. */
export const JOIN_FIELDS: readonly JoinField[] = ["personId", "name", "born"];

/**
 * Where the form starts: picking someone (nobody picked yet) when anyone is
 * unclaimed, otherwise adding yourself, with the account's name filled in.
 */
export function initialJoinChoice(unclaimed: PersonSummary[], suggestedName?: string): JoinChoice {
  if (unclaimed.length > 0) return { mode: "claim", personId: "" };
  return { mode: "add", name: suggestedName ?? "", born: "" };
}

/** Friendly messages for what is wrong before sending. */
export function validateJoinChoice(c: JoinChoice, unclaimed: PersonSummary[]): FieldErrors<JoinField> {
  const errors: FieldErrors<JoinField> = {};
  if (c.mode === "claim") {
    if (!c.personId) errors.personId = "Pick which one is you.";
    else if (!unclaimed.some((p) => p.id === c.personId)) {
      errors.personId = "That person isn't available to claim. Pick someone from the list.";
    }
    return errors;
  }
  const name = validateName(c.name);
  if (name) errors.name = name;
  const born = validateYear(c.born);
  if (born) errors.born = born;
  return errors;
}

/** The body for POST /invites/{code}/join: exactly one of personId or person. */
export function toJoinRequest(c: JoinChoice): JoinRequest {
  if (c.mode === "claim") return { personId: c.personId };
  const born = c.born.trim();
  return { person: { name: c.name.trim(), born: born ? Number(born) : null } };
}

/** Someone who adds themselves still has to say where they fit. */
export function needsRelationships(c: JoinChoice): boolean {
  return c.mode === "add";
}

/**
 * "You're invited to the Mao family's book". A name that already reads as a
 * family ("The Riveras", "Mao family") is used as it is: "the Riveras' book".
 */
export function inviteHeading(familyName: string): string {
  const raw = familyName.trim().replace(/\s+/g, " ");
  // A name that is only "The" says nothing more than a blank one (familyPhrase agrees).
  if (!raw || /^the$/i.test(raw)) return "You’re invited to a family’s book";
  const name = raw.replace(/^the\s+/i, "");
  const startsWithThe = name !== raw;
  const hasFamilyWord = /\bfamily$/i.test(name);
  const base = startsWithThe || hasFamilyWord ? name : `${name} family`;
  const possessive = /s$/i.test(base) && startsWithThe && !hasFamilyWord ? `${base}’` : `${base}’s`;
  return `You’re invited to the ${possessive} book`;
}

/** How many waiting people the signed-out invitation names before "and N more". */
export const WAITING_SHOWN = 8;

/**
 * The unclaimed people a signed-out visitor sees on the invitation, oldest
 * first, at most `limit` of them, and how many more there are.
 */
export function waitingPeople<T extends { name: string; born?: number | null }>(
  unclaimed: readonly T[],
  limit: number = WAITING_SHOWN,
): { people: T[]; more: number } {
  const named = unclaimed.filter((p) => p.name.trim().length > 0);
  const sorted = sortPeopleForPicking(named);
  const cap = Math.max(0, Math.floor(limit));
  // "and 1 more" reads worse than the one name it hides.
  if (sorted.length <= cap + 1) return { people: sorted, more: 0 };
  return { people: sorted.slice(0, cap), more: sorted.length - cap };
}
