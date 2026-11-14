/**
 * The Book of Family Traditions as a book (/traditions): one spread per
 * tradition, bound as Book3D binds a person's moments (components/book).
 *
 *   const book = traditionBook(traditions, people, { familyName, photoUrl, snapshot });
 *   <Book3D spreads={book.spreads} cover={book.cover} renderStage={...} />
 *
 * The story page of a tradition prints, top to bottom: when it happens, in
 * hand ("Every Christmas Eve", whenLine), its title, its story, and taped in
 * below a snapshot of the people who keep it (their paper puppets and names,
 * captioned "since 1962" when it is known), beside its first photo. The
 * right page is the category's paper kit (TraditionStage.tsx).
 *
 * Pure, with type-only imports, so `node --test` loads it directly
 * (traditionBook.test.ts). The snapshot is drawn in the browser
 * (snapshot.ts) and passed in.
 */

import type { BookCover, BookPhoto, BookSpread } from "@/components/book/types";
import type { CharacterSheet, Person, Tradition, TraditionWhen } from "@/lib/types";

/* ------------------------------------------------------------------------ */
/* When                                                                     */
/* ------------------------------------------------------------------------ */

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

/**
 * The longest "when" line the story page prints: it is set on one line in
 * the page's big hand, so a longer label falls back to a line made from the
 * kind and month.
 */
export const WHEN_MAX = 26;

/** Words a label can open with that already read as a whole phrase ("when everyone is home"). */
const PHRASE_START =
  /^(every|each|when|whenever|on|at|in|after|before|during|once|twice|most|some|any|all|the|our|first|last|now|until|around)\b/i;

const capitalize = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/** The month (1-12) as a name, or null when it is not one. */
export function monthName(month: number | null | undefined): string | null {
  return typeof month === "number" && Number.isInteger(month) && month >= 1 && month <= 12 ? MONTHS[month - 1] : null;
}

/** The season a month falls in (northern hemisphere, as the family's calendar). */
function seasonOf(month: number): "spring" | "summer" | "autumn" | "winter" {
  if (month >= 3 && month <= 5) return "spring";
  if (month >= 6 && month <= 8) return "summer";
  if (month >= 9 && month <= 11) return "autumn";
  return "winter";
}

/** The line made from the kind and month alone. */
function plainWhen(when: Pick<TraditionWhen, "kind" | "month">): string {
  const month = monthName(when.month);
  switch (when.kind) {
    case "yearly":
      return month ? `Every ${month}` : "Every year";
    case "seasonal":
      return month ? `Every ${seasonOf(when.month as number)}` : "Every season";
    case "monthly":
      return "Every month";
    case "weekly":
      return "Every week";
    default:
      return "Now and then";
  }
}

/**
 * When a tradition happens, as a person would say it: "Every Christmas Eve",
 * "Every Sunday", "Every July", "When everyone is home". The label leads
 * when there is one ("Christmas Eve" on a yearly tradition is "Every
 * Christmas Eve"; "every Sunday" is kept as written); without one, or when
 * it is too long for the page, the kind and month say it ("Every December",
 * "Every week", "Now and then").
 */
export function whenLine(when: TraditionWhen | null | undefined): string {
  if (!when) return plainWhen({ kind: "occasional" });
  const label = (when.label ?? "").replace(/\s+/g, " ").trim().replace(/[.!]+$/, "");
  if (label) {
    let line: string;
    if (PHRASE_START.test(label)) line = capitalize(label);
    else if (when.kind === "occasional") line = capitalize(label);
    else line = `Every ${label}`;
    if (line.length <= WHEN_MAX) return line;
  }
  return plainWhen(when);
}

/** "since 1962", or null when the year it began is not known. */
export function sinceLine(startedYear: number | null | undefined): string | null {
  return typeof startedYear === "number" && Number.isInteger(startedYear) && startedYear > 0 ? `since ${startedYear}` : null;
}

/** "Rosa", "Rosa & Luis", "Rosa, Luis & David". */
export function namesLine(names: readonly string[]): string {
  const clean = names.map((n) => n.trim()).filter(Boolean);
  if (clean.length <= 1) return clean[0] ?? "";
  return `${clean.slice(0, -1).join(", ")} & ${clean[clean.length - 1]}`;
}

/* ------------------------------------------------------------------------ */
/* The book's title                                                         */
/* ------------------------------------------------------------------------ */

/**
 * The family as it is named in "The ___ Book of Traditions": "The
 * Alvarez–Moore family" is "Alvarez–Moore", "The Riveras" is "Rivera",
 * "The Joneses" is "Jones", "Maos" is "Maos" (only a "The …s" name is read
 * as a plural).
 */
export function familyCore(name: string | null | undefined): string {
  let s = (name ?? "").replace(/\s+/g, " ").trim();
  const hadThe = /^the\s+/i.test(s);
  s = s.replace(/^the\s+/i, "");
  const hadFamily = /\s+(family|household|clan)$/i.test(s);
  s = s.replace(/\s+(family|household|clan)$/i, "").trim();
  if (hadThe && !hadFamily && !/\s/.test(s)) {
    if (/(s|x|z|ch|sh)es$/i.test(s)) s = s.slice(0, -2);
    else if (/[^s]s$/i.test(s)) s = s.slice(0, -1);
  }
  return s;
}

/** "The Alvarez–Moore Book of Traditions", or "The Book of Family Traditions" without a name. */
export function traditionsTitle(familyName: string | null | undefined): string {
  const core = familyCore(familyName);
  return core ? `The ${core} Book of Traditions` : "The Book of Family Traditions";
}

/**
 * The cover: "The Alvarez–Moore" small above "Book of Traditions" (the title
 * page sets its title on one line, so the family name rides above it), and
 * the oldest tradition's year under it.
 */
export function traditionsCover(familyName: string | null | undefined, traditions: readonly Pick<Tradition, "startedYear">[]): BookCover {
  const core = familyCore(familyName);
  const years = traditions.map((t) => t.startedYear).filter((y): y is number => sinceLine(y) !== null);
  const since = years.length ? sinceLine(Math.min(...years)) : null;
  return {
    title: core ? "Book of Traditions" : "Book of Family Traditions",
    ...(core ? { kicker: `The ${core}` } : {}),
    ...(since ? { subtitle: since } : {}),
  };
}

/* ------------------------------------------------------------------------ */
/* The spreads                                                              */
/* ------------------------------------------------------------------------ */

/** Someone who keeps a tradition, as the book draws them (puppet colour, item, build). */
export interface TraditionPerson {
  id: string;
  name: string;
  color: string;
  item?: string | null;
  build?: CharacterSheet["build"];
}

const FALLBACK_COLOR = "#b9573d";

/** A family's people as the book draws them, by id. */
export function peopleById(people: readonly Pick<Person, "id" | "name" | "character">[]): Map<string, TraditionPerson> {
  const out = new Map<string, TraditionPerson>();
  for (const p of people) {
    if (!p || typeof p.id !== "string") continue;
    const c = p.character;
    out.set(p.id, {
      id: p.id,
      name: (p.name ?? "").trim() || "Someone",
      color: typeof c?.color === "string" && /^#[0-9a-f]{3,8}$/i.test(c.color) ? c.color : FALLBACK_COLOR,
      item: c?.item ?? null,
      build: c?.build,
    });
  }
  return out;
}

/** The people who keep a tradition, in its order, known ones only, each once. */
export function keepersOf(tradition: Pick<Tradition, "participantIds">, people: ReadonlyMap<string, TraditionPerson>): TraditionPerson[] {
  const seen = new Set<string>();
  const out: TraditionPerson[] = [];
  for (const id of tradition.participantIds ?? []) {
    const p = people.get(id);
    if (!p || seen.has(id)) continue;
    seen.add(id);
    out.push(p);
  }
  return out;
}

/** Book order: the oldest tradition first (the year it began), those without a year last, then by title. */
export function orderTraditions<T extends Pick<Tradition, "id" | "title" | "startedYear">>(traditions: readonly T[]): T[] {
  const dated = (y: number | null | undefined) => sinceLine(y) !== null;
  return [...traditions].sort((a, b) => {
    const ad = dated(a.startedYear);
    const bd = dated(b.startedYear);
    if (ad !== bd) return ad ? -1 : 1;
    if (ad && bd && a.startedYear !== b.startedYear) return (a.startedYear as number) - (b.startedYear as number);
    return a.title.localeCompare(b.title) || a.id.localeCompare(b.id);
  });
}

/**
 * The page's top line goes where a moment prints its year. BookSpread.year
 * is a number, and the story page prints it with String() (pageArt.ts),
 * while Contents and the dots read anything but a positive number as
 * undated; so the words are handed through as they are. Requested of the
 * book's owner: a `dateLine?: string` of its own on BookSpread.
 */
export function printedLine(line: string): number {
  return line as unknown as number;
}

/** The words of a spread: the story, or a line made from the tradition when it has none. */
export function storyText(tradition: Pick<Tradition, "description" | "title" | "when">, keepers: readonly TraditionPerson[]): string {
  const story = (tradition.description ?? "").trim();
  if (story) return story;
  const when = whenLine(tradition.when);
  const who = namesLine(keepers.map((k) => k.name));
  return who ? `${when}, ${who} keep it going.` : `${when}, without fail.`;
}

export interface TraditionBookOptions {
  /** The family's name, for the cover. */
  familyName?: string | null;
  /** A photo's media id to an image URL (null when it could not be fetched). */
  photoUrl?: (mediaId: string) => string | null;
  /** The snapshot of the people who keep a tradition, as an image URL (snapshot.ts); null for none. */
  snapshot?: (tradition: Tradition, keepers: readonly TraditionPerson[]) => string | null;
}

/** One tradition as a spread. */
export function traditionSpread(tradition: Tradition, keepers: readonly TraditionPerson[], options: TraditionBookOptions = {}): BookSpread {
  const since = sinceLine(tradition.startedYear);
  const real: BookPhoto[] = [];
  for (const id of tradition.photoIds ?? []) {
    const src = options.photoUrl?.(id) ?? null;
    if (src) real.push({ id, src });
  }
  const shot = keepers.length && options.snapshot ? options.snapshot(tradition, keepers) : null;
  // The page holds two prints: with a snapshot of the people, one photo beside it.
  const photos: BookPhoto[] = shot ? [...real.slice(0, 1), { id: `${tradition.id}:keepers`, src: shot }] : real.slice(0, 2);
  if (since && photos.length) {
    const last = photos[photos.length - 1];
    photos[photos.length - 1] = { ...last, caption: since };
  }
  return {
    id: tradition.id,
    title: tradition.title.trim() || "A family tradition",
    year: printedLine(whenLine(tradition.when)),
    text: storyText(tradition, keepers),
    photos,
    // The kit stands on the right page: printed plain, no "the pop-up rises here".
    stage: {},
  };
}

/** The closing spread: "To be continued", and a few of the family's puppets side by side. */
export function traditionsEnd(keepers: readonly TraditionPerson[]): BookSpread {
  return {
    id: "traditions:end",
    kind: "end",
    title: "To be continued",
    year: 0,
    text: "Every family keeps a few. The next one goes here.",
    tellings: keepers.slice(0, 5).map((k) => ({ id: k.id, name: k.name, color: k.color })),
    photos: [],
  };
}

export interface TraditionBook {
  cover: BookCover;
  /** Window title: "The Rivera Book of Traditions". */
  title: string;
  /** The traditions in book order, then the closing spread; empty when there are none. */
  spreads: BookSpread[];
  /**
   * Spread id -> its tradition, the people who keep it, and its place among
   * the book's traditions of the same category (0 for the first), for the
   * right page's kit.
   */
  byId: ReadonlyMap<string, { tradition: Tradition; keepers: TraditionPerson[]; variant: number }>;
}

/** The whole book for a family's traditions and people. */
export function traditionBook(
  traditions: readonly Tradition[],
  people: readonly Pick<Person, "id" | "name" | "character">[],
  options: TraditionBookOptions = {},
): TraditionBook {
  const known = peopleById(people);
  const ordered = orderTraditions(traditions.filter((t) => t && typeof t.id === "string"));
  const byId = new Map<string, { tradition: Tradition; keepers: TraditionPerson[]; variant: number }>();
  const everyone = new Map<string, TraditionPerson>();
  const seen = new Map<string, number>();
  const spreads = ordered.map((t) => {
    const keepers = keepersOf(t, known);
    for (const k of keepers) everyone.set(k.id, k);
    const variant = seen.get(t.category) ?? 0;
    seen.set(t.category, variant + 1);
    byId.set(t.id, { tradition: t, keepers, variant });
    return traditionSpread(t, keepers, options);
  });
  return {
    cover: traditionsCover(options.familyName, ordered),
    title: traditionsTitle(options.familyName),
    spreads: spreads.length ? [...spreads, traditionsEnd([...everyone.values()])] : [],
    byId,
  };
}
