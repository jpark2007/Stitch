// The demo control page's prepared stories: tellings typed ahead of time so
// that, on stage, the add page can be opened with one already filled in and
// replay mode (demo/replay) matches it word for word. A per-operator
// convenience kept in this browser's localStorage, with JSON import/export to
// move it between laptops. Also the draft handed to the add page.
//
// No runtime imports and only erasable TypeScript, so `node --test` loads this
// file directly. Tests: components/demo/__tests__/stories.test.mjs.

/** localStorage key of the library. */
export const STORIES_KEY = "stitch.demo.stories";

/** localStorage key of the draft the add page reads with `?demoDraft=1` (components/flow/addPage.ts). */
export const DRAFT_KEY = "stitch.demo.draft";

export const MAX_TITLE = 200;
export const MAX_TEXT = 20_000;
export const MAX_QUESTION = 300;
export const MAX_STORIES = 200;
export const MIN_YEAR = 1800;

export interface PreparedStory {
  id: string;
  title: string;
  /** The year the story happened, or null when not set. */
  year: number | null;
  /** The telling, exactly as it should be typed. */
  text: string;
  /** The question it answers ("" for none). */
  question: string;
}

/** A story as typed into the editor: every field a string. */
export interface StoryInput {
  title: string;
  year: string;
  text: string;
  question: string;
}

export type StoryErrors = Partial<Record<keyof StoryInput, string>>;

export type StoryAction =
  | { type: "add"; story: PreparedStory }
  | { type: "update"; story: PreparedStory }
  | { type: "remove"; id: string }
  | { type: "import"; stories: PreparedStory[] }
  | { type: "replace"; stories: PreparedStory[] };

/** The library after one action. Ids stay unique; an import skips stories already in it (same text). */
export function storiesReducer(state: readonly PreparedStory[], action: StoryAction): PreparedStory[] {
  switch (action.type) {
    case "add":
      if (state.some((s) => s.id === action.story.id)) return [...state];
      return [...state, action.story].slice(0, MAX_STORIES);
    case "update":
      return state.map((s) => (s.id === action.story.id ? action.story : s));
    case "remove":
      return state.filter((s) => s.id !== action.id);
    case "import": {
      const out = [...state];
      const texts = new Set(state.map((s) => normalText(s.text)));
      const ids = new Set(state.map((s) => s.id));
      for (const s of action.stories) {
        const key = normalText(s.text);
        if (texts.has(key)) continue;
        texts.add(key);
        let id = s.id;
        for (let n = 2; ids.has(id); n++) id = `${s.id}-${n}`;
        ids.add(id);
        out.push({ ...s, id });
      }
      return out.slice(0, MAX_STORIES);
    }
    case "replace":
      return [...action.stories];
  }
}

function normalText(s: string): string {
  return s.trim().replace(/\s+/g, " ");
}

/** Field errors for a story being edited; empty when it can be saved. */
export function storyErrors(input: StoryInput, thisYear: number = new Date().getFullYear()): StoryErrors {
  const errors: StoryErrors = {};
  const title = input.title.trim();
  if (!title) errors.title = "Give it a title.";
  else if (title.length > MAX_TITLE) errors.title = `Keep the title to ${MAX_TITLE} characters.`;
  if (!input.text.trim()) errors.text = "Paste the telling, word for word.";
  else if (input.text.length > MAX_TEXT) errors.text = `Keep the telling to ${MAX_TEXT.toLocaleString("en-US")} characters.`;
  const year = input.year.trim();
  if (year && !(/^\d{4}$/.test(year) && Number(year) >= MIN_YEAR && Number(year) <= thisYear)) {
    errors.year = `A year from ${MIN_YEAR} to ${thisYear}, or leave it empty.`;
  }
  if (input.question.trim().length > MAX_QUESTION) errors.question = `Keep the question to ${MAX_QUESTION} characters.`;
  return errors;
}

/** The story to save from valid input (title and question trimmed; the telling kept exactly). */
export function storyFromInput(input: StoryInput, id: string): PreparedStory {
  const year = input.year.trim();
  return { id, title: input.title.trim(), year: year ? Number(year) : null, text: input.text, question: input.question.trim() };
}

export function inputFromStory(s: PreparedStory | null): StoryInput {
  return s
    ? { title: s.title, year: s.year === null ? "" : String(s.year), text: s.text, question: s.question }
    : { title: "", year: "", text: "", question: "" };
}

/** A fresh id: `story-<time>-<random>`. */
export function newStoryId(now: number = Date.now(), rand: () => number = Math.random): string {
  return `story-${now.toString(36)}-${Math.floor(rand() * 1e8).toString(36)}`;
}

/** One stored or imported story, or null when it is not one. Missing ids are filled from `fallbackId`. */
export function parseStory(value: unknown, fallbackId: string, thisYear: number = new Date().getFullYear()): PreparedStory | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const o = value as Record<string, unknown>;
  if (typeof o.text !== "string" || !o.text.trim() || o.text.length > MAX_TEXT) return null;
  const title = typeof o.title === "string" && o.title.trim() ? o.title.trim().slice(0, MAX_TITLE) : firstWords(o.text);
  let year: number | null = null;
  const y = typeof o.year === "string" && /^\d{4}$/.test(o.year.trim()) ? Number(o.year.trim()) : o.year;
  if (typeof y === "number" && Number.isInteger(y) && y >= MIN_YEAR && y <= thisYear) year = y;
  const question = typeof o.question === "string" ? o.question.trim().slice(0, MAX_QUESTION) : "";
  const id = typeof o.id === "string" && /^[\w-]{1,80}$/.test(o.id) ? o.id : fallbackId;
  return { id, title, year, text: o.text, question };
}

function firstWords(text: string): string {
  const words = text.trim().split(/\s+/).slice(0, 6).join(" ");
  return words.length > 60 ? `${words.slice(0, 59)}…` : words;
}

export type ImportResult = { ok: true; stories: PreparedStory[]; skipped: number } | { ok: false; error: string };

/**
 * Stories from pasted JSON: a list of stories, `{stories: [...]}` (what
 * exportStories writes) or one story. Entries that are not stories are
 * skipped and counted; nothing usable is an error.
 */
export function importStories(text: string, thisYear: number = new Date().getFullYear()): ImportResult {
  if (!text.trim()) return { ok: false, error: "Paste the JSON of one or more stories first." };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, error: "That isn’t valid JSON. Paste exactly what Export gave you." };
  }
  let list: unknown[];
  if (Array.isArray(parsed)) list = parsed;
  else if (typeof parsed === "object" && parsed !== null && Array.isArray((parsed as { stories?: unknown }).stories)) {
    list = (parsed as { stories: unknown[] }).stories;
  } else if (typeof parsed === "object" && parsed !== null && "text" in parsed) list = [parsed];
  else return { ok: false, error: "Expected a list of stories, each with at least a “text”." };
  const stories: PreparedStory[] = [];
  list.slice(0, MAX_STORIES).forEach((item, i) => {
    const s = parseStory(item, `imported-${i + 1}`, thisYear);
    if (s) stories.push(s);
  });
  const skipped = list.length - stories.length;
  if (stories.length === 0) return { ok: false, error: "None of those entries has a telling (“text”) to keep." };
  return { ok: true, stories, skipped };
}

/** The library as JSON for Export (importStories reads it back). */
export function exportStories(stories: readonly PreparedStory[]): string {
  return JSON.stringify({ stories }, null, 2);
}

/** The library kept in `storage`, or [] when there is none or it is unreadable. */
export function loadStories(storage: Pick<Storage, "getItem"> | null): PreparedStory[] {
  if (!storage) return [];
  try {
    const raw = storage.getItem(STORIES_KEY);
    if (!raw) return [];
    const result = importStories(raw);
    return result.ok ? dedupeIds(result.stories) : [];
  } catch {
    return [];
  }
}

function dedupeIds(stories: PreparedStory[]): PreparedStory[] {
  return storiesReducer([], { type: "import", stories });
}

/** Keep the library; false when storage refused it. */
export function saveStories(storage: Pick<Storage, "setItem"> | null, stories: readonly PreparedStory[]): boolean {
  if (!storage) return false;
  try {
    storage.setItem(STORIES_KEY, exportStories(stories));
    return true;
  } catch {
    return false;
  }
}

/** The draft the add page reads (`?demoDraft=1`): {text, year, title, question}. */
export function draftJson(s: PreparedStory): string {
  return JSON.stringify({ text: s.text, year: s.year === null ? "" : String(s.year), title: s.title, question: s.question });
}

/** Write the draft for the add page; false when storage refused it. */
export function writeDraft(storage: Pick<Storage, "setItem"> | null, s: PreparedStory): boolean {
  if (!storage) return false;
  try {
    storage.setItem(DRAFT_KEY, draftJson(s));
    return true;
  } catch {
    return false;
  }
}
