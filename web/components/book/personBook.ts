/**
 * A person's book: the moments they are in, one spread each, in order by
 * year, then a closing spread (DESIGN.md "The book"; ARCHITECTURE.md
 * section 1 "Book structure").
 *
 *   const book = personBook(await api.getBook(fid, id), { photoUrl, familyName });
 *   book.spreads   one per moment (momentSpread, from that moment's own slice
 *                  of the bundle, so its tellings, page text, pull tabs and
 *                  photos match the single-moment page), then "The end";
 *                  empty when the person has no moments yet
 *   book.slices    spread id -> that moment's MemoryResponse, to play it
 *
 * The demo family's books (?demo=1) come from components/tree/demoFamily.ts,
 * with the hand-made words and photos of demoSpreads where they exist.
 *
 * Pure data, so node runs it directly (personBook.test.ts).
 */

import type { BookResponse, Memory, MemoryResponse, Person, Prop, SetDef } from "@/lib/types";
import { demoFamily, demoMoments, demoMomentsFor, getDemoPerson, type DemoMoment } from "@/components/tree/demoFamily";
import { demoSpreads } from "./demoSpreads";
import { joinNames, momentCover, momentEnd, momentSpread, type MomentSpreadOptions } from "./moment";
import type { BookCover, BookSpread, BookTeller } from "./types";

/** A person's thread when their character has no colour. */
const FALLBACK_COLOR = "#b9573d";

/** A year a moment can be dated by: a whole, positive number. */
export function isDated(year: number | null | undefined): year is number {
  return typeof year === "number" && Number.isFinite(year) && year > 0;
}

/**
 * Moments in book order: by year (undated ones last), ties by title, then
 * id. The API already orders by year; this does not rely on it.
 */
export function orderMoments<T extends { id: string; title: string; year?: number | null }>(moments: readonly T[]): T[] {
  return [...moments].sort((a, b) => {
    const ad = isDated(a.year);
    const bd = isDated(b.year);
    if (ad !== bd) return ad ? -1 : 1;
    if (ad && bd && a.year !== b.year) return (a.year as number) - (b.year as number);
    const t = (a.title ?? "").localeCompare(b.title ?? "");
    if (t !== 0) return t;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

/** "1988" for one year, "1972 – 2019" for several, undefined for none. */
export function yearRange(years: readonly (number | null | undefined)[]): string | undefined {
  const dated = years.filter(isDated);
  if (dated.length === 0) return undefined;
  const lo = Math.min(...dated);
  const hi = Math.max(...dated);
  return lo === hi ? String(lo) : `${lo} – ${hi}`;
}

/** "David’s book" (a name ending in s keeps the ’s, as the demo cover does). */
export function bookTitle(name: string): string {
  return `${name.trim() || "Someone"}’s book`;
}

/* ------------------------------------------------------------------------ */
/* One moment's slice of a book                                             */
/* ------------------------------------------------------------------------ */

const refId = (value: unknown, kind: string): string | null => {
  const prefix = `${kind}:`;
  if (typeof value !== "string" || !value.startsWith(prefix)) return null;
  return value.slice(prefix.length).split(".", 1)[0] || null;
};
const beforeDot = (value: string | null | undefined) => (value ? value.split(".", 1)[0] : null);

/**
 * The people, props and sets one memory's scene names: the same walk the
 * API makes to bundle a moment (api/routes/tree.py _scene_bundle).
 */
export function sceneRefs(memory: Memory): { people: string[]; props: string[]; sets: string[] } {
  const people: (string | null | undefined)[] = [
    ...(memory.participantIds ?? []),
    ...(memory.tellings ?? []).map((t) => t.authorId),
  ];
  const props: (string | null | undefined)[] = [...(memory.propIds ?? [])];
  const sets: (string | null | undefined)[] = [...(memory.setIds ?? [])];
  const sp = memory.screenplay;
  if (sp) {
    people.push(...(sp.cast ?? []), ...Object.keys(sp.narrations ?? {}));
    props.push(...(sp.props ?? []));
    for (const shot of sp.shots ?? []) {
      sets.push(shot.set);
      people.push(...Object.keys(shot.start ?? {}));
      for (const start of Object.values(shot.start ?? {})) props.push(start.holding, beforeDot(start.in));
      props.push(...Object.keys(shot.propStart ?? {}));
      for (const ps of Object.values(shot.propStart ?? {})) props.push(beforeDot(ps.on));
      for (const a of shot.actions ?? []) {
        people.push(a.who);
        props.push(a.what);
      }
    }
    for (const i of sp.interactions ?? []) {
      props.push(i.drives?.prop);
      for (const target of i.drives?.targets ?? []) {
        props.push(refId(target, "prop"));
        people.push(refId(target, "puppet"));
      }
    }
    for (const e of sp.explanations ?? []) {
      props.push(refId(e.target, "prop"));
      people.push(refId(e.target, "puppet"));
    }
  }
  const tl = memory.timeline;
  if (tl) {
    people.push(...Object.keys(tl.narrations ?? {}), tl.defaultNarrator);
    sets.push(...(tl.shots ?? []).map((s) => s.set));
    for (const track of tl.tracks ?? []) {
      props.push(refId(track.target, "prop"));
      people.push(refId(track.target, "puppet"));
      sets.push(refId(track.target, "set"));
      if (track.channel === "parent") {
        for (const [, value] of track.keys ?? []) {
          props.push(refId(value, "prop"));
          people.push(refId(value, "puppet"));
        }
      }
    }
  }
  return { people: dedupe(people), props: dedupe(props), sets: dedupe(sets) };
}

function dedupe(ids: readonly (string | null | undefined)[]): string[] {
  return [...new Set(ids.filter((id): id is string => typeof id === "string" && id !== ""))];
}

function pick<T extends { id: string }>(all: readonly T[] | undefined, ids: readonly string[]): T[] {
  const byId = new Map((all ?? []).map((x) => [x.id, x]));
  return ids.map((id) => byId.get(id)).filter((x): x is T => x !== undefined);
}

/**
 * One memory as GET /memories/{id} would send it, cut from a book's bundle:
 * the memory, and the people, props (signature items included) and sets its
 * scene names.
 */
export function memorySlice(book: Pick<BookResponse, "props" | "sets" | "people">, memory: Memory): MemoryResponse {
  const refs = sceneRefs(memory);
  const people: Person[] = pick(book.people, refs.people);
  const propIds = dedupe([...refs.props, ...people.map((p) => p.character?.itemPropId)]);
  const props: Prop[] = pick(book.props, propIds);
  const sets: SetDef[] = pick(book.sets, refs.sets);
  return { memory, props, sets, people };
}

/* ------------------------------------------------------------------------ */
/* The book                                                                 */
/* ------------------------------------------------------------------------ */

export interface PersonBook {
  /** The person's name. */
  name: string;
  cover: BookCover;
  /** The moments' spreads in book order, then the closing spread; empty when there are no moments. */
  spreads: BookSpread[];
  /** Spread id -> the moment's own MemoryResponse (real books only). */
  slices: Readonly<Record<string, MemoryResponse>>;
}

export interface PersonBookOptions {
  /** A telling photo's media id to an image URL (api.mediaUrl). */
  photoUrl?: MomentSpreadOptions["photoUrl"];
  /** Printed small above the title, e.g. "The Maos". */
  familyName?: string | null;
}

/** The person as a teller, for the closing page's puppet. */
function selfTeller(person: Pick<Person, "id" | "name"> & { character?: { color?: string | null } | null }): BookTeller {
  return { id: person.id, name: person.name, color: person.character?.color || FALLBACK_COLOR };
}

/** The cover of a person's book: "David’s book", the years it spans. */
export function personCover(name: string, years: readonly (number | null | undefined)[], kicker?: string | null): BookCover {
  const subtitle = yearRange(years);
  return {
    title: bookTitle(name),
    ...(kicker ? { kicker } : {}),
    ...(subtitle ? { subtitle } : {}),
  };
}

/** The closing spread of a person's book: "The end", their puppet, and that their story goes on. */
export function personEnd(person: BookTeller, spreads: readonly BookSpread[]): BookSpread {
  const last = [...spreads].reverse().find((s) => isDated(s.year));
  return {
    id: `${person.id}:end`,
    kind: "end",
    title: "The end",
    year: last?.year ?? 0,
    text: `${person.name}’s story goes on`,
    tellings: [person],
    photos: [],
  };
}

/** A person's book from GET /families/{fid}/people/{id}/book. */
export function personBook(book: BookResponse, options: PersonBookOptions = {}): PersonBook {
  const person = book.person;
  const memories = orderMoments(book.memories ?? []);
  const slices: Record<string, MemoryResponse> = {};
  const moments = memories.map((m) => {
    const slice = memorySlice(book, m);
    slices[m.id] = slice;
    return momentSpread(slice, { photoUrl: options.photoUrl });
  });
  return {
    name: person.name,
    cover: personCover(person.name, memories.map((m) => m.year), options.familyName),
    spreads: moments.length ? [...moments, personEnd(selfTeller(person), moments)] : [],
    slices,
  };
}

/* ------------------------------------------------------------------------ */
/* The demo family                                                          */
/* ------------------------------------------------------------------------ */

const demoSpreadById = new Map(demoSpreads.map((s) => [s.id, s]));

const demoName = (id: string) => getDemoPerson(id)?.name ?? id;

/** A short storybook line for a demo moment with no hand-made page: its year, and who remembers it. */
export function demoLine(m: Pick<DemoMoment, "year" | "tellers">): string {
  const names = (m.tellers ?? []).map(demoName);
  if (names.length === 0) return `It was ${m.year}, and it went just like this.`;
  return `It was ${m.year}. This is how ${joinNames(names)} ${names.length > 1 ? "remember" : "remembers"} it.`;
}

/**
 * A demo moment's spread: the hand-made page (words and photos) where there
 * is one, else a short storybook line. `played` is the spread of the scene
 * that plays on it (the reference sample on the church steps): its teller
 * labels and stage go on this page, as on /memories/demo.
 */
export function demoMomentSpread(m: DemoMoment, played?: Pick<BookSpread, "tellings" | "stage"> | null): BookSpread {
  const made = demoSpreadById.get(m.id);
  const base: BookSpread = made
    ? { ...made }
    : {
        id: m.id,
        title: m.title,
        year: m.year,
        text: demoLine(m),
        tellers: (m.tellers ?? []).map(demoName),
        photos: [],
      };
  return played ? { ...base, tellings: played.tellings, stage: played.stage } : base;
}

/** The demo moment that plays the reference sample (the church steps, 1987). */
export const DEMO_PLAYING_MOMENT = "m-steps-1987";

/**
 * One demo moment opened as its own book (/memories/<id>?demo=1): its
 * spread, then "The end"; null for an id the demo family does not have.
 */
export function demoMomentBook(
  id: string,
  played?: Pick<BookSpread, "tellings" | "stage"> | null,
): { cover: BookCover; spreads: BookSpread[]; spread: BookSpread } | null {
  const m = demoMoments.find((d) => d.id === id);
  if (!m) return null;
  const spread = demoMomentSpread(m, m.id === DEMO_PLAYING_MOMENT ? played : null);
  return { cover: momentCover(spread), spreads: [spread, momentEnd(spread)], spread };
}

/**
 * A demo person's book, or null for someone not in the demo family.
 * `played` is the spread of the sample scene, for DEMO_PLAYING_MOMENT.
 */
export function demoPersonBook(personId: string, played?: Pick<BookSpread, "tellings" | "stage"> | null): Omit<PersonBook, "slices"> | null {
  const person = getDemoPerson(personId);
  if (!person) return null;
  const moments = orderMoments(demoMomentsFor(person.id)).map((m) =>
    demoMomentSpread(m, m.id === DEMO_PLAYING_MOMENT ? played : null),
  );
  return {
    name: person.name,
    cover: personCover(person.name, moments.map((s) => s.year), demoFamily.name),
    spreads: moments.length ? [...moments, personEnd(selfTeller(person), moments)] : [],
  };
}
