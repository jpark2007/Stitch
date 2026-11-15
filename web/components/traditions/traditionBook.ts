/**
 * The Book of Family Traditions as a book (/traditions): one spread per
 * tradition, bound as Book3D binds a person's moments (components/book).
 *
 *   const book = traditionBook(traditions, people, { familyName, photoUrl, snapshot });
 *   <Book3D spreads={book.spreads} cover={book.cover} renderStage={...} />
 *
 * The story page of a tradition prints, top to bottom: when it happens, in
 * hand ("Christmas Eve, every year", dateLineOf), its title, its story, and taped in
 * below a snapshot of the people who keep it (their paper puppets and names,
 * captioned "since 1962" when it is known; with no print to caption, the
 * story closes on "Kept since 1962."), beside its first photo. The
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
 * A family's "when" words, tidied and nothing more: trimmed, spaces
 * collapsed, trailing punctuation (". ! ? , ; :") dropped. The case is left
 * as written; callers capitalise the first letter where it opens a line.
 */
export function cleanLabel(label: string | null | undefined): string {
  return (label ?? "").replace(/\s+/g, " ").trim().replace(/[\s.!?,;:]+$/, "");
}

const WEEKDAY = "(monday|tuesday|wednesday|thursday|friday|saturday|sunday)";
const MONTH = `(${MONTHS.join("|")})`;
/** "every Sunday", "Each sundays": every or each and one weekday, nothing else. */
const EVERY_WEEKDAY = new RegExp(`^(every|each) ${WEEKDAY}s?$`, "i");
/** "every July": every or each and one month, nothing else. */
const EVERY_MONTH = new RegExp(`^(every|each) ${MONTH}$`, "i");
/** "every other Friday", "each first snow". */
const STARTS_EVERY = /^(every|each)\b/i;
/** "Sundays", "Sunday nights": a plural weekday leads. */
const PLURAL_WEEKDAY = new RegExp(`^${WEEKDAY}s\\b`, "i");
/** "Sunday nights", "Saturday mornings": a weekday's plural times, already how often. */
const WEEKDAY_TIMES = new RegExp(`^${WEEKDAY} \\p{L}+s$`, "iu");
/** Words that already say how often ("weekly game night", "twice a year", "cada domingo"). */
const FREQUENCY =
  /(^|[^\p{L}])(every|each|weekly|monthly|yearly|annual|annually|daily|once|twice|always|whenever|when|cada|todos|todas)(?![\p{L}])/iu;
/** A season's name: a seasonal tradition's bare label ("summer"). */
const SEASON = /^(spring|summer|autumn|fall|winter)$/i;

/** The family's words, read: which of the forms below they take. */
type LabelForm = "everyWeekday" | "everyMonth" | "asWritten" | "pluralWeekday" | "occasion";

function labelForm(label: string): LabelForm {
  if (EVERY_WEEKDAY.test(label)) return "everyWeekday";
  if (EVERY_MONTH.test(label)) return "everyMonth";
  if (STARTS_EVERY.test(label)) return "asWritten";
  if (PLURAL_WEEKDAY.test(label)) return "pluralWeekday";
  // No words to say "every" of ("🎄"): as written too.
  if (FREQUENCY.test(label) || PHRASE_START.test(label) || WEEKDAY_TIMES.test(label) || !/\p{L}/u.test(label)) return "asWritten";
  return "occasion";
}

/** "Sunday" or "sundays" as "Sundays". */
function pluralWeekday(word: string): string {
  const day = word.toLowerCase().replace(/s$/, "");
  return `${capitalize(day)}s`;
}

/**
 * When a tradition happens, as the opening of a sentence: "Every Christmas
 * Eve" (a bare occasion), "Every Sunday" and "Every other Friday" (as
 * written), "On Sundays", "When everyone is home". storyText says
 * "Every Christmas Eve, Rosa keeps it going." with it. Without a label, or
 * one too long for the page (WHEN_MAX), the kind and month say it ("Every
 * December", "Every week", "Now and then").
 */
export function whenLine(when: TraditionWhen | null | undefined): string {
  if (!when) return plainWhen({ kind: "occasional" });
  const label = cleanLabel(when.label);
  if (label && label.length <= WHEN_MAX) {
    const form = labelForm(label);
    if (form === "pluralWeekday") {
      const [day, ...rest] = label.split(" ");
      return `On ${[pluralWeekday(day), ...rest].join(" ")}`;
    }
    if (form === "occasion" && when.kind !== "occasional") return `Every ${label}`;
    return capitalize(label);
  }
  return plainWhen(when);
}

/** How often, said after a bare occasion: "Christmas Eve, every year". */
const OFTEN: Record<TraditionWhen["kind"], string> = {
  yearly: "every year",
  // A seasonal tradition comes round once a year ("Summer at the lake, every year").
  seasonal: "every year",
  monthly: "every month",
  weekly: "every week",
  occasional: "",
};

/** How often, alone: "Once a year". */
const PLAIN_OFTEN: Record<TraditionWhen["kind"], string> = {
  yearly: "Once a year",
  seasonal: "Once a season",
  monthly: "Once a month",
  weekly: "Once a week",
  occasional: "Now and then",
};

/**
 * When a tradition happens, for the spread's dateLine (printed over the
 * title, in Contents and under the navigation dots, which cut it to a few
 * letters). The family's words are rewritten as little as possible:
 *
 *   "every Sunday" (weekly)        -> "Sundays"
 *   "every July" (yearly)          -> "July, every year"
 *   "Christmas Eve" (bare, yearly) -> "Christmas Eve, every year"
 *   "every other Friday", "Sundays", "weekly game night", "When everyone
 *   is home", any occasional label -> as written
 *
 * Without a label, or one longer than WHEN_MAX, the kind and month say it
 * ("July, every year", "Winter, every year", "Once a week", "Now and then").
 */
export function dateLineOf(when: TraditionWhen | null | undefined): string {
  const kind = when?.kind && when.kind in OFTEN ? when.kind : "occasional";
  const label = cleanLabel(when?.label);
  if (label && label.length <= WHEN_MAX) {
    const written = capitalize(label);
    switch (labelForm(label)) {
      case "everyWeekday":
        return kind === "weekly" ? pluralWeekday(label.split(" ")[1]) : written;
      case "everyMonth":
        return kind === "yearly" ? `${capitalize(label.split(" ")[1].toLowerCase())}, every year` : written;
      case "occasion":
        if (kind === "occasional") return written;
        if (kind === "seasonal" && SEASON.test(label)) return `${capitalize(label.toLowerCase())}, every year`;
        return `${written}, ${OFTEN[kind]}`;
      default:
        return written;
    }
  }
  const month = monthName(when?.month);
  if (month && (kind === "yearly" || kind === "seasonal")) {
    const name = kind === "yearly" ? month : capitalize(seasonOf(when?.month as number));
    return `${name}, every year`;
  }
  return PLAIN_OFTEN[kind];
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
 * The words of a spread: the story, or a line made from the tradition when
 * it has none ("Every month, Pat keeps it going.", "Every Christmas Eve,
 * Rosa & Luis keep it going.", "Every Christmas Eve, without fail.").
 */
export function storyText(tradition: Pick<Tradition, "description" | "title" | "when">, keepers: readonly TraditionPerson[]): string {
  const story = (tradition.description ?? "").trim();
  if (story) return story;
  const when = whenLine(tradition.when);
  const names = keepers.map((k) => k.name.trim()).filter(Boolean);
  if (!names.length) return `${when}, without fail.`;
  return `${when}, ${namesLine(names)} ${names.length === 1 ? "keeps" : "keep"} it going.`;
}

/**
 * The most of a story the left page holds (pageArt.ts sets it at 31px over a
 * 770px measure, about 50 characters a line): with prints taped in below,
 * about nine lines, so the prints keep their size; alone, the page's height.
 * A title over one line takes about a line and a half of story with each
 * extra line (it is set at 64px, about 22 characters a line), up to the
 * three lines the book prints (pageArt cuts a longer one with "…").
 */
export const STORY_MAX_WITH_PHOTOS = 440;
export const STORY_MAX_ALONE = 750;
const STORY_MIN = 160;
/** The most lines of title the story page prints (pageArt.ts TITLE_MAX_LINES). */
const TITLE_LINES = 3;

/** How much of a story fits on the page beside its title, with or without prints. */
export function storyBudget(title: string, withPhotos: boolean): number {
  const extraTitleLines = Math.min(TITLE_LINES - 1, Math.max(0, Math.ceil(title.trim().length / 22) - 1));
  const max = withPhotos ? STORY_MAX_WITH_PHOTOS : STORY_MAX_ALONE;
  return Math.max(STORY_MIN, max - extraTitleLines * 75);
}

/**
 * A story cut to at most `max` characters, the ellipsis included: at the
 * last sentence that ends in the back part of it, else at the last word, so
 * the page never prints half a word. A story that fits comes back as is.
 */
export function fitStory(text: string, max: number): string {
  if (text.trim().length <= max) return text.trim();
  const s = text.replace(/\s+/g, " ").trim();
  // Room for the ellipsis (and its space after a whole sentence).
  const head = s.slice(0, Math.max(1, max - 2));
  // A whole sentence, when one ends in the last 40% of the room.
  const sentence = /[.!?]["'”’)]?(?= )/g;
  let cut = -1;
  for (let m = sentence.exec(head); m; m = sentence.exec(head)) cut = m.index + m[0].length;
  if (cut >= max * 0.6) return `${head.slice(0, cut)} …`;
  const space = head.lastIndexOf(" ");
  const words = (space > 0 ? head.slice(0, space) : head).replace(/[\s,;:–—-]+$/, "");
  return `${words}…`;
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
  const title = tradition.title.trim() || "A family tradition";
  const story = storyText(tradition, keepers);
  if (since && photos.length) {
    const last = photos[photos.length - 1];
    photos[photos.length - 1] = { ...last, caption: since };
  }
  // With no print to caption, the year closes the story instead ("Kept since 2001.").
  const closing = since && !photos.length ? ` Kept ${since}.` : "";
  const budget = storyBudget(title, photos.length > 0) - closing.length;
  return {
    id: tradition.id,
    title,
    // The book is not re-ordered by year (traditionBook orders it), and with
    // a dateLine the year is printed nowhere: the year it began, else 0
    // ("undated", as the book reads it).
    year: since ? (tradition.startedYear as number) : 0,
    // "Christmas Eve, every year": over the title, in Contents and the dots.
    dateLine: dateLineOf(tradition.when),
    // Cut to what the page holds: the printer has no overflow of its own.
    text: fitStory(story, budget) + closing,
    photos,
    // The kit stands on the right page: printed plain, no "the pop-up rises here".
    stage: {},
  };
}

/* ------------------------------------------------------------------------ */
/* A tradition being written (/traditions/new)                              */
/* ------------------------------------------------------------------------ */

/** The spread id of a tradition not saved yet. */
export const DRAFT_TRADITION_ID = "traditions:draft";

/**
 * What a tradition being written has so far: a Tradition whose kind and
 * category may not be picked yet, and whose year may not be one.
 */
export interface TraditionDraftPage {
  title: string;
  description: string;
  category: Tradition["category"] | null;
  when: { kind: TraditionWhen["kind"] | null; label?: string | null; month?: number | null };
  participantIds: readonly string[];
  /** Ids the options' photoUrl knows the draft's photos by. */
  photoIds: readonly string[];
  startedYear: number | null;
}

/**
 * BookSpread.year is a number printed as text where no dateLine is given;
 * a page with nothing to date leaves the line blank (not "0"). This empty
 * value prints as nothing and reads as undated (spreadDate); nothing in the
 * book does arithmetic on it. (The same as /add's draft page.)
 */
const NO_YEAR = "" as unknown as number;

/** The draft as the Tradition it will be saved as (its blanks filled the way the saved one never has them). */
export function draftTradition(draft: TraditionDraftPage, id: string = DRAFT_TRADITION_ID): Tradition {
  const when: TraditionWhen = { kind: draft.when.kind ?? "occasional" };
  if (draft.when.label) when.label = draft.when.label;
  if (draft.when.month) when.month = draft.when.month;
  return {
    id,
    familyId: "",
    title: draft.title,
    description: draft.description,
    category: draft.category ?? "other",
    when,
    participantIds: [...draft.participantIds],
    photoIds: [...draft.photoIds],
    startedYear: draft.startedYear,
    createdBy: "",
    createdAt: "",
    updatedAt: "",
  };
}

/**
 * The story page of a tradition being written, printed by the book's own
 * builder (traditionSpread) so it is the page the book will show once it is
 * saved, with only what is not given yet left blank instead of filled in:
 *
 * - no name yet: no title (not "A family tradition");
 * - no "how often" yet: no line over the title (not "Now and then"), and no
 *   story made up from it ("Every year, Rosa keeps it going.") until there
 *   is one; a story typed is printed as it will be;
 * - no kind yet: the right page keeps its pencilled "the pop-up rises
 *   here" marks; once there is one, it is printed plain for the kit.
 */
export function draftSpread(draft: TraditionDraftPage, keepers: readonly TraditionPerson[], options: TraditionBookOptions = {}): BookSpread {
  const tradition = draftTradition(draft);
  const spread = traditionSpread(tradition, keepers, options);
  const named = draft.title.trim() !== "";
  const timed = draft.when.kind !== null;
  const told = draft.description.trim() !== "";
  const { dateLine, stage, ...rest } = spread;
  return {
    ...rest,
    title: named ? spread.title : "",
    year: timed ? spread.year : NO_YEAR,
    ...(timed ? { dateLine } : {}),
    text: told || timed ? spread.text : "",
    ...(draft.category ? { stage } : {}),
  };
}

/**
 * Which of its category's kits a tradition gets in the book (traditionBook's
 * `variant`): its place, in book order, among the family's traditions of the
 * same category.
 */
export function variantIn(traditions: readonly Tradition[], tradition: Tradition): number {
  const all = orderTraditions([...traditions.filter((t) => t.id !== tradition.id), tradition]);
  let n = 0;
  for (const t of all) {
    if (t.id === tradition.id) return n;
    if (t.category === tradition.category) n++;
  }
  return 0;
}

/**
 * The kit variant the book beside the form stands for a tradition being
 * written: the draft's place among the family's traditions, and the same
 * once it is saved. A saved one's real id can sort it on either side of an
 * exact twin (same title, year and kind) that the draft id did not, which
 * would swap the kit while the page shows it saved; the draft's is held.
 * The saved one's own only when there is no draft to go by.
 */
export function draftVariant(traditions: readonly Tradition[], draft: TraditionDraftPage | null, saved: Tradition | null = null): number {
  if (draft?.category) return variantIn(traditions, draftTradition(draft));
  return saved ? variantIn(traditions, saved) : 0;
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
