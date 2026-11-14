// Pure logic for the crafting book on /add (CraftingBook.tsx): what the
// teller's book shows while they write and while the studio makes their page.
//
// - The draft page: the story page the left side of the open book prints as
//   they write (title, year, the opening lines, the people, the photos).
// - The crafting scene: a run's state (runState.ts) read as a pop-up being
//   made on the right page: storyboard frames, props, the set, the cast, the
//   ink, the checker's frames. Only what the run has said so far: nothing
//   stands before its event, and nothing is finished before the run is.
// - Captions: one plain line per moment of the run, from the run's own words
//   where they are plain enough.
// - Entrances: when each new piece starts to move in, so events that arrive
//   in a burst (a reload replays the whole run) never queue minutes of
//   animation, and a fresh event is never shown faster than it arrived.
// - The prop tap: a RunSource wrapper that keeps the props the prop master
//   found (and the family's edits to them), which the run state forgets once
//   the review closes.
//
// No runtime imports, and only erasable TypeScript, so `node --test` can load
// this file directly.
import type { FeedStep, RunEventMessage, RunState } from "@/components/agent/runState";
import type { RunSource } from "@/components/agent/useAgentRun";
import type { AnswerRequest, PlanItem, PropChip, PropEdit } from "@/lib/types";

// ---------------------------------------------------------------------------
// Text helpers

/** Sentences of a story, trimmed, in order ("Hi. Bye!" -> ["Hi.", "Bye!"]). Line breaks end a sentence too. */
export function sentences(text: string): string[] {
  const out: string[] = [];
  for (const para of text.split(/\n+/)) {
    const parts = para.match(/[^.!?…]+(?:[.!?…]+["'”’)\]]*|$)/g) ?? [];
    for (const p of parts) {
      const s = p.trim();
      if (s) out.push(s);
    }
  }
  return out;
}

/** `text` cut to at most `max` characters at a word boundary, with "…" when cut. */
export function clip(text: string, max: number): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  const space = cut.lastIndexOf(" ");
  const head = (space > max * 0.5 ? cut.slice(0, space) : cut).replace(/[\s,;:.!?…-]+$/, "");
  return `${head}…`;
}

/** The most of a story the printed page holds (it has a drop cap, a title, photos). */
export const PAGE_TEXT_MAX = 360;
/** What the page shows while the story is being written: its opening. */
export const OPENING_MAX = 200;

/**
 * The opening lines of a story for the page: whole sentences while they fit
 * in `max` (at least the first, clipped), joined with single spaces.
 */
export function openingLines(text: string, max = OPENING_MAX, count = 2): string {
  const all = sentences(text);
  if (all.length === 0) return "";
  let out = "";
  for (const s of all.slice(0, count)) {
    const next = out ? `${out} ${s}` : s;
    if (next.length > max) break;
    out = next;
  }
  return out || clip(all[0], max);
}

/**
 * The story as inked by the narrator so far: the first `n` sentences (at
 * least the opening), within the page's room. `n` of Infinity is all of it.
 */
export function inkedLines(text: string, n: number): string {
  const all = sentences(text);
  if (all.length === 0) return "";
  let out = "";
  const want = Math.max(1, Math.min(all.length, n));
  for (const s of all.slice(0, want)) {
    const next = out ? `${out} ${s}` : s;
    if (next.length > PAGE_TEXT_MAX) return out ? `${out.replace(/[.!?…]+$/, "")}…` : clip(s, PAGE_TEXT_MAX);
    out = next;
  }
  return out;
}

/** How many sentences the narrator inks in for `text` (what fits the page). */
export function inkSteps(text: string): number {
  const all = sentences(text);
  let len = 0;
  let n = 0;
  for (const s of all) {
    len += (n ? 1 : 0) + s.length;
    if (len > PAGE_TEXT_MAX) return Math.max(1, n);
    n++;
  }
  return n;
}

/** A printed page's year: a whole year from 1000 to 2999 typed in the form, else null. */
export function pageYear(year: string | number | null | undefined): number | null {
  const n = typeof year === "number" ? year : Number(String(year ?? "").trim());
  return Number.isInteger(n) && n >= 1000 && n <= 2999 ? n : null;
}

/** The longest question that still reads as a title on the page. */
const TITLE_MAX = 64;

/**
 * The draft page's title: the moment's (a second telling), else the question
 * being answered when it is short enough to head a page, else "A new memory".
 */
export function pageTitle(o: { question?: string | null; secondTellingOf?: string | null }): string {
  const moment = o.secondTellingOf?.trim();
  if (moment) return moment;
  const q = o.question?.replace(/\s+/g, " ").trim();
  if (q && q.length <= TITLE_MAX) return q;
  return "A new memory";
}

/** "Ana", "Ana and David", "Ana, David and Rosa", "Ana, David and 2 others". */
export function namesLine(names: readonly string[], max = 3): string {
  const n = names.map((s) => s.trim()).filter(Boolean);
  if (n.length === 0) return "";
  if (n.length === 1) return n[0];
  if (n.length <= max) return `${n.slice(0, -1).join(", ")} and ${n[n.length - 1]}`;
  const rest = n.length - (max - 1);
  return `${n.slice(0, max - 1).join(", ")} and ${rest} others`;
}

/** "Ana’s book" (a name ending in s keeps the ’s). The first word of a full name. */
export function bookOf(name: string | null | undefined): string {
  const first = (name ?? "").trim().split(/\s+/)[0];
  return `${first || "Your"}${first ? "’s" : ""} book`;
}

const WORD_NUMBERS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
};

/** The number of shots a storyboarder's summary names ("Three shots: …" -> 3), or null. */
export function shotCount(summary: string | null | undefined): number | null {
  const m = /\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+shots?\b/i.exec(summary ?? "");
  if (!m) return null;
  const word = m[1].toLowerCase();
  const n = /^\d+$/.test(word) ? Number(word) : WORD_NUMBERS[word];
  return Number.isFinite(n) && n > 0 ? n : null;
}

// Verbs a short task may start with, and how they read as under way.
const GERUNDS: Record<string, string> = {
  add: "Adding", arrange: "Arranging", ask: "Asking", block: "Blocking", break: "Breaking",
  build: "Building", check: "Checking", choose: "Choosing", compile: "Compiling", cut: "Cutting",
  direct: "Directing", draw: "Drawing", find: "Finding", fix: "Fixing", give: "Giving", look: "Looking",
  make: "Making", move: "Moving", narrate: "Narrating", paint: "Painting", pick: "Picking",
  place: "Placing", plan: "Planning", put: "Putting", read: "Reading", record: "Recording",
  render: "Rendering", rig: "Rigging", set: "Setting", sketch: "Sketching", stage: "Staging",
  tidy: "Tidying", time: "Timing", write: "Writing",
};

/** The longest task that reads as a caption. */
const TASK_MAX = 56;

/**
 * A sub-agent's task as a line under way: "Find the props in the telling" ->
 * "Finding the props in the telling…". Only a short, plain, one-line task
 * that starts with a known verb; null otherwise (the caller says it its own
 * way). A second clause ("… and find what is missing") is left out.
 */
export function taskLine(task: string | null | undefined): string | null {
  const t = (task ?? "").trim();
  if (!t || t.includes("\n") || /[:;"“”{}[\]<>`]/.test(t)) return null;
  const first = t.split(/\s+(?:and|or)\s+/i)[0].replace(/[.!…]+$/, "").trim();
  if (first.length > TASK_MAX) return null;
  const m = /^([A-Za-z]+)\b(.*)$/.exec(first);
  if (!m || !m[2].trim()) return null;
  const verb = GERUNDS[m[1].toLowerCase()];
  return verb ? `${verb}${m[2]}…` : null;
}

/** A sub-agent's summary when it is plain enough to show as it is (one short sentence, no codes). */
export function plainSummary(summary: string | null | undefined, max = 72): string | null {
  const s = (summary ?? "").replace(/\s+/g, " ").trim();
  if (!s || s.length > max) return null;
  if (/[{}[\]<>`=_]|\bt=|\berror|\bcompile|\bshot s\d|\d+(\.\d+)?s\b/i.test(s)) return null;
  return s;
}

// ---------------------------------------------------------------------------
// Props as paper cut-outs

/** One prop on the crafting page. */
export interface CraftProp {
  id: string;
  name: string;
  size: PropChip["size"];
  /** The paper it is cut from. */
  color: string;
  /** Left out in the family's review: it folds back down. */
  removed: boolean;
}

/** Paper colours for the props, warm and muted like the set's card stock. */
export const PROP_PAPERS = ["#b9573d", "#d98d6b", "#e6b45c", "#8ba278", "#4e6a69", "#a8573f", "#7c5b40", "#c4a37a"] as const;

/** A stable paper colour for a prop, from its name. */
export function propColor(name: string): string {
  let h = 2166136261;
  for (const ch of name.toLowerCase()) {
    h ^= ch.charCodeAt(0);
    h = Math.imul(h, 16777619);
  }
  return PROP_PAPERS[(h >>> 0) % PROP_PAPERS.length];
}

/** The props a props_found event names, as cut-outs (the newest list wins). */
export function propsFromChips(chips: readonly PropChip[], before: readonly CraftProp[] = []): CraftProp[] {
  const kept = new Map(before.map((p) => [p.id, p]));
  return chips.map((c) => {
    const had = kept.get(c.id);
    const name = (c.name ?? "").trim() || "a prop";
    return { id: c.id, name: had?.name ?? name, size: c.size, color: had?.color ?? propColor(name), removed: had?.removed ?? false };
  });
}

/** The family's review applied: renamed props take their new name, removed ones fold away. */
export function applyPropEdits(props: readonly CraftProp[], edits: readonly PropEdit[]): CraftProp[] {
  const byId = new Map(edits.map((e) => [e.id, e]));
  return props.map((p) => {
    const e = byId.get(p.id);
    if (!e) return p;
    if (e.action === "remove") return { ...p, removed: true };
    if (e.action === "rename" && e.name?.trim()) return { ...p, name: e.name.trim(), removed: false };
    return { ...p, removed: false };
  });
}

/**
 * The run source, watched: `onProps` hears the props of every props_found
 * event (a reconnect replays them, so it is told again), `onEdits` the
 * family's prop review as it is sent. Everything else passes through as is.
 */
export function tapProps(
  source: RunSource,
  onProps: (chips: PropChip[]) => void,
  onEdits: (edits: PropEdit[]) => void,
): RunSource {
  return {
    async *stream(signal: AbortSignal): AsyncGenerator<RunEventMessage> {
      for await (const message of source.stream(signal)) {
        if (message.event === "props_found") onProps(message.data.props ?? []);
        yield message;
      }
    },
    async answer(body: AnswerRequest): Promise<void> {
      await source.answer(body);
      if (body.kind === "props" && body.props) onEdits(body.props);
    },
  };
}

// ---------------------------------------------------------------------------
// The crafting scene

/** Where the studio is, as the book shows it. */
export type CraftStage =
  | "opening"
  | "reading"
  | "storyboard"
  | "props"
  | "set"
  | "staging"
  | "narration"
  | "checking"
  | "linking"
  | "finishing"
  | "ready"
  | "done"
  | "stopped";

type WorkStage = Extract<CraftStage, "reading" | "storyboard" | "props" | "set" | "staging" | "narration" | "checking" | "linking">;

const STEP_STAGE: Record<string, WorkStage> = {
  interviewer: "reading",
  storyboarder: "storyboard",
  prop_master: "props",
  set_designer: "set",
  director: "staging",
  narrator: "narration",
  checker: "checking",
  linker: "linking",
};

/** The working stages in the order a run reaches them. */
const WORK_ORDER: readonly WorkStage[] = ["reading", "storyboard", "props", "set", "staging", "narration", "checking", "linking"];

/** A plan item's stage, from its words (the scripted studio's plan has no sub-agent for every step). */
export function planStage(text: string): WorkStage | null {
  const t = text.toLowerCase();
  if (/narrat|voice|record/.test(t)) return "narration";
  if (/storyboard|shot list|\bshots?\b/.test(t)) return "storyboard";
  if (/\bprops?\b/.test(t)) return "props";
  if (/\bset\b|scenery|place|backdrop/.test(t)) return "set";
  if (/stage|direct|block|screenplay/.test(t)) return "staging";
  if (/check|frames?\b|look over/.test(t)) return "checking";
  if (/books|ask the others|their version|\blink/.test(t)) return "linking";
  if (/read|telling|follow/.test(t)) return "reading";
  return null;
}

/** Most storyboard frames laid on the page. */
export const MAX_FRAMES = 4;
/** Frames laid when the storyboarder does not say how many shots. */
const DEFAULT_FRAMES = 3;
/** Backdrop layers in the set. */
export const SET_LAYERS = 3;

export interface CraftScene {
  stage: CraftStage;
  /** What the run is paused on, while it waits for the teller. */
  waiting: "answer" | "props" | "merge" | null;
  /** Storyboard frames on the page. */
  frames: number;
  /** The storyboarder is at work (its pencil sketches). */
  sketching: boolean;
  /** Props found so far (none before props_found). */
  props: CraftProp[];
  /** Backdrop layers standing (0..SET_LAYERS). */
  layers: number;
  /** The cast stands on the page (the director has begun). */
  cast: boolean;
  /** Fixes so far (compile errors, and the checker sending the scene back): each nudges the pieces. */
  fixes: number;
  /** Narration: 0 not begun, 1 under way, 2 written. */
  ink: 0 | 1 | 2;
  /** Frames the checker has looked at. */
  checked: number;
  /** The linker has begun (the page goes into everyone's books). */
  linked: boolean;
  memoryId: string | null;
  /** One plain line: what is happening now. */
  caption: string;
  /** A quieter second line, for a pause or a stop; null otherwise. */
  note: string | null;
}

/** What the scene reads from a run. */
export type CraftRun = Pick<RunState, "phase" | "steps" | "log" | "todos" | "pending" | "memoryId" | "error" | "errorFrom"> &
  Partial<Pick<RunState, "reconnecting">>;

export interface CraftOptions {
  /** The cast's names, for the staging line ("Putting Ana and David on their marks…"). */
  cast?: readonly string[];
  /** The teller's first name, for the narration line. */
  teller?: string | null;
  /** A second telling: only the reading and the narration happen. */
  second?: boolean;
}

const STAGE_LINES: Record<WorkStage, string> = {
  reading: "Reading your story…",
  storyboard: "Sketching the storyboard…",
  props: "Looking for the things in your story…",
  set: "Building the scenery…",
  staging: "Putting everyone on their marks…",
  narration: "Inking the words…",
  checking: "Checking the pages…",
  linking: "Putting it in everyone’s books…",
};

const WAIT_NOTES: Record<"answer" | "props" | "merge", string> = {
  answer: "Waiting for your answer.",
  props: "Waiting for you to look over the props.",
  merge: "Waiting for you to say who that is.",
};

/** "the hymn books", "the hymn books and the ring", "the hymn books, the ring and 2 more". */
function propList(names: readonly string[]): string {
  const the = names.map((n) => (/^(the|a|an|my|our|his|her|their)\s/i.test(n) ? n : `the ${n}`));
  if (the.length <= 2) return the.join(" and ");
  return `${the.slice(0, 2).join(", ")} and ${the.length - 2} more`;
}

function lastOf<T>(list: readonly T[], test: (t: T) => boolean): T | undefined {
  for (let i = list.length - 1; i >= 0; i--) if (test(list[i])) return list[i];
  return undefined;
}

function stageOf(step: FeedStep): WorkStage | null {
  return STEP_STAGE[step.name] ?? null;
}

/**
 * The crafting scene for a run: everything the run has reached, by its
 * sub-agents (authoritative) and its plan (for steps without a sub-agent).
 * `props` are the cut-outs from the prop tap.
 */
export function craftScene(run: CraftRun, props: readonly CraftProp[] = [], o: CraftOptions = {}): CraftScene {
  const steps = run.steps;
  const started = (stage: WorkStage) => steps.some((s) => stageOf(s) === stage);
  const running = (stage: WorkStage) => steps.some((s) => stageOf(s) === stage && s.status === "running");
  const ended = (stage: WorkStage) => steps.some((s) => stageOf(s) === stage && s.status === "done");
  const plan = (stage: WorkStage, status: PlanItem["status"]) =>
    run.todos.some((t) => (t.status ?? "pending") === status && planStage(t.text) === stage);

  const board = lastOf(steps, (s) => s.name === "storyboarder");
  const frames = board
    ? board.status === "done"
      ? Math.min(MAX_FRAMES, shotCount(board.summary) ?? DEFAULT_FRAMES)
      : 1
    : 0;

  const layers = ended("set") ? SET_LAYERS : running("set") ? 1 : 0;

  // Fixes: every compile error, and each time the checker sends the scene
  // back (a director pass that starts after a checker pass).
  let fixes = run.log.filter((e) => e.kind === "compile_error").length;
  for (let i = 1; i < steps.length; i++) {
    if (steps[i].name === "director" && steps.slice(0, i).some((s) => s.name === "checker")) fixes++;
  }

  const inkDone = ended("narration") || plan("narration", "completed");
  const inkBegun = started("narration") || plan("narration", "in_progress");
  const ink: 0 | 1 | 2 = inkDone ? 2 : inkBegun ? 1 : 0;

  const checked = run.log.reduce((n, e) => (e.kind === "frames" ? n + e.event.frames.length : n), 0);
  const shownProps = started("props") || props.length > 0 ? props.slice() : [];

  // What it is on now: the latest step still running; between steps, the
  // further on of the plan's item in progress and the latest step that ended
  // (a plan can lag behind its steps, and run ahead of them).
  const current = lastOf(steps, (s) => s.status === "running" && stageOf(s) !== null);
  const planNow = run.todos.find((t) => t.status === "in_progress");
  const latest = lastOf(steps, (s) => stageOf(s) !== null);
  const between = [planNow ? planStage(planNow.text) : null, latest ? stageOf(latest) : null]
    .filter((s): s is WorkStage => s !== null)
    .sort((a, b) => WORK_ORDER.indexOf(b) - WORK_ORDER.indexOf(a))[0];
  let stage: CraftStage = current ? (stageOf(current) as WorkStage) : (between ?? "opening");
  // Every planned step done, the scene not yet handed over: the last touches.
  if (!current && run.todos.length > 0 && run.todos.every((t) => t.status === "completed")) stage = "finishing";

  const waiting = run.phase === "waiting" && run.pending ? run.pending.kind : null;
  let caption = captionFor(stage, { run, current, props: shownProps, o, checked });
  let note: string | null = waiting ? WAIT_NOTES[waiting] : null;

  if (run.phase === "scene_ready") {
    stage = "ready";
    caption = "The pop-up stands. A last look over the page…";
  } else if (run.phase === "done") {
    stage = "done";
    caption = run.memoryId ? "Your page is made." : "All done.";
    note = null;
  } else if (run.phase === "error") {
    stage = "stopped";
    caption = "The page stopped part-way.";
    note =
      run.errorFrom === "connection"
        ? "We lost touch with the studio. Try again beside the book."
        : "The studio stopped before it was finished. Your story is kept.";
  } else if (run.phase === "connecting" && steps.length === 0) {
    stage = "opening";
    caption = "Opening the book at a new page…";
  } else if (run.reconnecting) {
    note = "Finding the thread again…";
  }

  return {
    stage,
    waiting,
    frames,
    sketching: running("storyboard"),
    props: shownProps,
    layers,
    cast: started("staging"),
    fixes,
    ink,
    checked,
    linked: started("linking"),
    memoryId: run.memoryId,
    caption,
    note,
  };
}

function captionFor(
  stage: CraftStage,
  c: { run: CraftRun; current: FeedStep | undefined; props: readonly CraftProp[]; o: CraftOptions; checked: number },
): string {
  const { run, current, props, o } = c;
  const step = current ?? lastOf(run.steps, (s) => STEP_STAGE[s.name] === stage);
  const live = step?.status === "running";
  switch (stage) {
    case "opening":
      return "Opening the book at a new page…";
    case "reading":
      return o.second ? "Reading your version…" : STAGE_LINES.reading;
    case "storyboard":
      if (!live) return plainSummary(step?.summary) ?? "The storyboard is sketched.";
      return STAGE_LINES.storyboard;
    case "props": {
      const kept = props.filter((p) => !p.removed);
      if (kept.length > 0) return `Cutting out ${propList(kept.map((p) => p.name))}…`;
      if (props.length > 0) return "Folding the props away…";
      return (live && taskLine(step?.task)) || STAGE_LINES.props;
    }
    case "set":
      if (!live) return plainSummary(step?.summary) ?? "The scenery stands.";
      return taskLine(step?.task) ?? STAGE_LINES.set;
    case "staging": {
      const fix = live && run.steps.filter((s) => s.name === "director").length > 1;
      const task = live ? taskLine(step?.task) : null;
      if (fix) return task ?? "Moving a few things into place…";
      const cast = namesLine(o.cast ?? []);
      if (cast) return `Putting ${cast} on ${o.cast && o.cast.length === 1 ? "their mark" : "their marks"}…`;
      return task ?? STAGE_LINES.staging;
    }
    case "narration":
      return o.teller ? `Inking ${o.teller}’s words…` : STAGE_LINES.narration;
    case "checking": {
      const frames = lastOf(run.log, (e) => e.kind === "frames");
      const critique = frames && frames.kind === "frames" ? plainSummary(frames.event.critique, 90) : null;
      return critique ?? STAGE_LINES.checking;
    }
    case "linking":
      return STAGE_LINES.linking;
    case "finishing":
      return "Adding the last touches…";
    default:
      return STAGE_LINES.reading;
  }
}

// ---------------------------------------------------------------------------
// Entrances: pacing pieces as they arrive

export interface EntranceOptions {
  /** Milliseconds between two pieces that arrive together. */
  gap: number;
  /** At most this many new pieces move in one after another; earlier ones are simply there. */
  burst: number;
  /** Every new piece is simply there (a resumed run, reduced motion). */
  instant?: boolean;
}

export const ENTRANCE: EntranceOptions = { gap: 420, burst: 3 };

/** When a piece's entrance starts, ms; SETTLED for a piece that is simply there. */
export const SETTLED = -1e12;

/**
 * Entrance times for the pieces `keys`, in order, at time `now`. Pieces that
 * already have one keep it. New pieces start one after another, `gap` apart,
 * no sooner than now and after the latest piece still moving in. Only the
 * last `burst` of a batch move: the rest are there already, so a burst (a
 * replay, a fast scripted run) never builds a queue that runs behind.
 * Pieces no longer named are dropped.
 */
export function scheduleEntrances(
  prev: ReadonlyMap<string, number>,
  keys: readonly string[],
  now: number,
  o: EntranceOptions = ENTRANCE,
): Map<string, number> {
  const out = new Map<string, number>();
  const fresh: string[] = [];
  for (const k of keys) {
    if (out.has(k)) continue;
    const had = prev.get(k);
    if (had !== undefined) out.set(k, had);
    else fresh.push(k);
  }
  let last = -Infinity;
  for (const at of out.values()) if (at > last) last = at;
  const moving = o.instant ? 0 : Math.max(0, o.burst);
  fresh.forEach((k, i) => {
    if (i < fresh.length - moving) {
      out.set(k, SETTLED);
      return;
    }
    const at = Math.max(now, last + o.gap);
    out.set(k, at);
    last = at;
  });
  return out;
}

/** 0..1, how far a piece's entrance has got at `now` (1 for a settled piece). */
export function entranceProgress(at: number | undefined, now: number, duration: number): number {
  if (at === undefined) return 0;
  if (at <= SETTLED) return 1;
  if (duration <= 0) return now >= at ? 1 : 0;
  return Math.min(1, Math.max(0, (now - at) / duration));
}

/** The pieces a scene stands up, as keys in the order they arrive (for scheduleEntrances). */
export function sceneKeys(scene: CraftScene, castCount: number): string[] {
  const keys: string[] = [];
  for (let i = 0; i < scene.frames; i++) keys.push(`frame:${i}`);
  for (const p of scene.props) keys.push(`prop:${p.id}`);
  for (let i = 0; i < scene.layers; i++) keys.push(`layer:${i}`);
  if (scene.cast) for (let i = 0; i < castCount; i++) keys.push(`cast:${i}`);
  for (let i = 0; i < Math.min(scene.checked, MAX_CHECKS); i++) keys.push(`check:${i}`);
  return keys;
}

/** Most checked frames laid beside the stage. */
export const MAX_CHECKS = 4;

/**
 * The scene to show now: the new one, except while a reconnect replays the
 * run from nothing (it would wipe the page and stand it all up again).
 */
export function holdScene(shown: CraftScene | null, next: CraftScene, run: Pick<RunState, "phase" | "steps">): CraftScene {
  if (shown && run.phase === "connecting" && run.steps.length === 0) return shown;
  return next;
}

// ---------------------------------------------------------------------------
// The people on the page

/** One of the cast: a puppet on the stage and an avatar on the draft page. */
export interface CastMember {
  id: string;
  name: string;
  color: string;
  item?: string | null;
  build?: "small" | "short" | "average" | "tall" | null;
}

/** A person's thread colour when they have none. */
export const NO_COLOR = "#b9573d";

/**
 * The cast of a telling: the teller first, then everyone ticked, each once,
 * at most `max`. People unknown to `people` are left out.
 */
export function castOf(
  people: readonly { id: string; name: string; color?: string | null; item?: string | null; build?: CastMember["build"] }[],
  tellerId: string | null,
  participantIds: readonly string[],
  max = 4,
): CastMember[] {
  const byId = new Map(people.map((p) => [p.id, p]));
  const ids = [...(tellerId ? [tellerId] : []), ...participantIds];
  const out: CastMember[] = [];
  for (const id of ids) {
    if (out.length >= max) break;
    if (out.some((c) => c.id === id)) continue;
    const p = byId.get(id);
    if (!p) continue;
    out.push({ id, name: p.name, color: p.color || NO_COLOR, item: p.item ?? null, build: p.build ?? null });
  }
  return out;
}

/** sessionStorage key for a run's cast, kept so a reload stands the same puppets up. */
export function castKey(runId: string): string {
  return `stitch.craftCast.${runId}`;
}

// ---------------------------------------------------------------------------
// A small value the page writes and the book reads, so a keystroke or a run
// event re-renders the book alone, not the page around it.

export interface Watch<T> {
  get(): T;
  set(value: T): void;
  subscribe(listener: () => void): () => void;
}

export function createWatch<T>(initial: T): Watch<T> {
  let value = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    set(next) {
      if (Object.is(next, value)) return;
      value = next;
      for (const l of [...listeners]) l();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

/** The cast kept for a run, or [] when there is none (or it does not read). */
export function readCast(raw: string | null): CastMember[] {
  if (!raw) return [];
  try {
    const v: unknown = JSON.parse(raw);
    if (!Array.isArray(v)) return [];
    return v
      .filter(
        (c): c is CastMember =>
          typeof c === "object" && c !== null && typeof c.id === "string" && typeof c.name === "string" && typeof c.color === "string",
      )
      .slice(0, 4)
      .map((c) => ({ id: c.id, name: c.name, color: c.color, item: c.item ?? null, build: c.build ?? null }));
  } catch {
    return [];
  }
}
