// Pure logic behind the member list on family settings: join each membership
// to the person it claimed (members carry no name), decide who an admin may
// remove, and order the list. Admins remove members; nobody removes
// themselves here, and the family never loses its last admin
// (ARCHITECTURE.md section 4).
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
    return {
      userId: m.userId,
      role: m.role,
      name,
      personId: person?.id ?? null,
      color: person?.character?.color || null,
      joinedAt: m.joinedAt ?? null,
      isYou,
      removable: viewerIsAdmin && !isYou && !lastAdmin,
    };
  });

  return rows.sort((a, b) => {
    if (a.isYou !== b.isYou) return a.isYou ? -1 : 1;
    if (a.role !== b.role) return a.role === "admin" ? -1 : 1;
    return a.name.localeCompare(b.name) || a.userId.localeCompare(b.userId);
  });
}
