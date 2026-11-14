/**
 * A moment (a `memories` document) as one spread of the book, and the cover
 * and closing spread of a book that holds it. DESIGN.md "Moments and
 * tellings" and "3. The book".
 *
 *   tellers     one taped label per telling the timeline can play, default
 *               narrator first, each with their own words
 *   page text   the shots' page text, read in order, as one short page; else
 *               the screenplay's; else empty, and the page prints the playing
 *               teller's own words (BookSpread.text)
 *   pull tabs   the timeline's stage-page interactions, placed in the page
 *
 * Pure data with type-only imports, so node runs it directly in the tests
 * (moment.test.ts).
 */

import type { Interaction, MemoryResponse, Person, Telling, Timeline } from "@/lib/types";
import type { BookCover, BookPhoto, BookPullTab, BookSpread, BookTeller } from "./types";

/** A teller's thread when their person has no character colour. */
const FALLBACK_COLOR = "#b9573d";
/** Where a tab with no usable position goes on its page (the foot, toward the fore-edge). */
const DEFAULT_AT = [0.85, 0.82] as const;
/** At most this many photos are taped to a story page. */
const MAX_PHOTOS = 2;

type Shots = readonly { pageText?: string | null }[] | null | undefined;

/**
 * The tellers of a moment, default narrator first: the timeline's narrations
 * when it has any (those are what can be heard), else the tellings' authors.
 * Each carries their telling's words, for a page with no text of its own.
 */
export function momentTellers(
  timeline: Pick<Timeline, "narrations" | "defaultNarrator"> | null | undefined,
  people: readonly Person[],
  tellings: readonly Telling[] = [],
): BookTeller[] {
  const heard = Object.keys(timeline?.narrations ?? {});
  const ids = heard.length ? heard : [...new Set(tellings.map((t) => t.authorId))];
  const first = timeline?.defaultNarrator && ids.includes(timeline.defaultNarrator) ? timeline.defaultNarrator : ids[0];
  const ordered = first ? [first, ...ids.filter((id) => id !== first)] : ids;
  return ordered.map((id) => {
    const person = people.find((p) => p.id === id);
    const text = tellings.find((t) => t.authorId === id)?.text.trim();
    return { id, name: person?.name ?? id, color: person?.character?.color ?? FALLBACK_COLOR, ...(text ? { text } : {}) };
  });
}

/** The page text of a run of shots, in order, as one short page ("" for none). */
function shotsText(shots: Shots): string {
  return (shots ?? [])
    .map((s) => s.pageText?.trim())
    .filter((t): t is string => !!t)
    .join(" ");
}

/**
 * The storybook words printed on the story page: the timeline's shots' page
 * text, else the screenplay's (an older compile left it there). Empty when
 * neither has any: the page then prints the playing teller's own words.
 */
export function momentPageText(timelineShots: Shots, screenplayShots?: Shots): string {
  return shotsText(timelineShots) || shotsText(screenplayShots);
}

const clamp01 = (v: number | undefined, fallback: number) =>
  typeof v === "number" && Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : fallback;

/**
 * The pull tabs (and sliders, drawn as tabs) on the stage page, from a
 * timeline's interactions. A missing or broken position falls back to the
 * foot of the page; story-page interactions are left out (only the stage
 * page has slits cut for them).
 */
export function momentPullTabs(interactions: readonly Interaction[] | null | undefined): BookPullTab[] {
  return (interactions ?? [])
    .filter((i) => (i.page ?? "stage") === "stage")
    .map((i) => ({
      id: i.id,
      label: i.label,
      at: [clamp01(i.at?.[0], DEFAULT_AT[0]), clamp01(i.at?.[1], DEFAULT_AT[1])] as const,
    }));
}

/** "David & Ana", "Rosa, Ana & David". */
export function joinNames(names: readonly string[]): string {
  if (names.length < 2) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} & ${names[names.length - 1]}`;
}

/**
 * The teller a shared link asks for (?narrator=), by person id or by name,
 * either in any case; null when there is no such teller.
 */
export function pickTeller(tellers: readonly Pick<BookTeller, "id" | "name">[], want: string | null | undefined): string | null {
  const w = want?.trim().toLowerCase();
  if (!w) return null;
  return tellers.find((t) => t.id.toLowerCase() === w)?.id ?? tellers.find((t) => t.name.toLowerCase() === w)?.id ?? null;
}

export interface MomentSpreadOptions {
  /** The timeline that plays, when it is not the memory's own (a sample's separate file). */
  timeline?: Timeline | null;
  /** The screenplay's shots, for page text an older compile left there. */
  screenplayShots?: Shots;
  /** A telling photo's media id to an image URL; without it photos are left out. */
  photoUrl?: (mediaId: string) => string | null;
}

/**
 * A moment's spread: the story page's words, a label per telling, the
 * tellings' photos, and (with a timeline to play) the stage and its pull tabs.
 */
export function momentSpread(res: MemoryResponse, options: MomentSpreadOptions = {}): BookSpread {
  const m = res.memory;
  const timeline = options.timeline ?? m.timeline ?? null;
  const tellings = m.tellings ?? [];
  const tellers = momentTellers(timeline, res.people ?? [], tellings);

  const photos: BookPhoto[] = [];
  if (options.photoUrl) {
    for (const id of new Set(tellings.flatMap((t) => t.photoIds ?? []))) {
      if (photos.length >= MAX_PHOTOS) break;
      const src = options.photoUrl(id);
      // No aspect: each is printed in its own shape once it loads (photoArt.withPrintAspect).
      if (src) photos.push({ id, src });
    }
  }

  return {
    id: m.id,
    title: m.title,
    year: m.year,
    text: momentPageText(timeline?.shots, options.screenplayShots ?? m.screenplay?.shots),
    tellers: tellers.map((t) => t.name),
    tellings: tellers,
    photos,
    stage: timeline ? { pullTabs: momentPullTabs(timeline.interactions) } : undefined,
  };
}

/** The cover of a book that holds one moment: "A moment from 1988". */
export function momentCover(spread: Pick<BookSpread, "title" | "year" | "tellers">): BookCover {
  const told = joinNames(spread.tellers ?? []);
  return {
    kicker: `A moment from ${spread.year}`,
    title: spread.title,
    subtitle: told ? `as told by ${told}` : undefined,
  };
}

/** The closing spread to turn to (and back from): "The end", the tellers side by side. */
export function momentEnd(spread: BookSpread): BookSpread {
  const names = spread.tellers ?? [];
  const told = joinNames(names);
  return {
    id: `${spread.id}:end`,
    kind: "end",
    title: "The end",
    year: spread.year,
    text: told ? `as ${told} ${names.length > 1 ? "remember" : "remembers"} it` : "",
    tellings: spread.tellings,
    photos: [],
  };
}
