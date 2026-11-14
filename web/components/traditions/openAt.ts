/**
 * ?tradition=<id>: open the Book of Family Traditions at that tradition's
 * spread. Adding a tradition (/traditions/new) lands here with it, so the
 * new page is the one the book opens on.
 *
 *   traditionHref("fam-1", "trad-9")              // "/traditions?family=fam-1&tradition=trad-9"
 *   openAtTradition(query.initial, spreads, id)   // the view the book starts in
 *
 * The rule is the book routes' ?memory= (components/book/memoryParam.ts): a
 * spread whose id is the tradition's (BookSpread.id is the tradition id;
 * the closing spread never matches) opens there, as ?spread=<n>&state=open
 * would, and wins over any ?spread or ?state. One the book does not have, or
 * none, leaves the view as it was. ?memory= is read the same way, so the
 * book routes' own link works here too.
 *
 * Pure, no imports (openAt.test.ts).
 */

const enc = encodeURIComponent;

/** The book of a family's traditions, opened at one of them. */
export function traditionHref(familyId: string, traditionId: string): string {
  return `/traditions?family=${enc(familyId)}&tradition=${enc(traditionId)}`;
}

type SearchLike = Record<string, string | string[] | undefined>;

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/** The tradition to open at: ?tradition=, else ?memory=; null for none. */
export function traditionParam(search: SearchLike): string | null {
  return one(search.tradition)?.trim() || one(search.memory)?.trim() || null;
}

/** The 1-based spread of tradition `id` in `spreads`, or null when there is none. */
export function traditionSpreadAt(spreads: readonly { id: string; kind?: "moment" | "end" }[], id: string | null | undefined): number | null {
  const want = id?.trim();
  if (!want) return null;
  const i = spreads.findIndex((s) => s.kind !== "end" && s.id === want);
  return i < 0 ? null : i + 1;
}

/** The view the book starts in: open at the tradition's spread when the book has it, else `initial` itself. */
export function openAtTradition<T extends { state?: "closed" | "open"; spread?: number }>(
  initial: T,
  spreads: readonly { id: string; kind?: "moment" | "end" }[],
  id: string | null | undefined,
): T {
  const n = traditionSpreadAt(spreads, id);
  return n === null ? initial : { ...initial, state: "open", spread: n };
}
