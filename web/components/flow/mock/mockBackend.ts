// In-memory mock of the FastAPI backend (ARCHITECTURE.md sections 4 and 6),
// exposed as a fake `fetch` so mock mode (NEXT_PUBLIC_API_MOCK=1) runs the real
// API client: JSON bodies, error responses, multipart and the SSE run stream.
//
// No runtime imports and only erasable TypeScript, so `node --test` can load
// this file directly. Composition (seed loading, browser storage) lives in
// ./index.ts.
import type {
  AnswerRequest,
  AskQuote,
  AskResponse,
  BookResponse,
  CharacterSheet,
  CompileError,
  CreateFamilyResponse,
  Family,
  FamilySummary,
  FrameShot,
  InvitePreview,
  InviteResponse,
  JoinResponse,
  MeResponse,
  Memory,
  MemoryResponse,
  MomentSummary,
  NarrationLine,
  Person,
  PersonSummary,
  PlanItem,
  Prop,
  PropChip,
  PropEdit,
  PropsResponse,
  Question,
  QuestionsResponse,
  RunEvents,
  Screenplay,
  SetDef,
  SetsResponse,
  SubmitMemoryResponse,
  Telling,
  ThreadsResponse,
  Timeline,
  TreeResponse,
  User,
} from "@/lib/types";
import type { MockSeed } from "./demoSeed";

export interface MockBackend {
  fetch: typeof fetch;
  mediaUrl(fid: string, mediaId: string): string;
  reset(): void;
  /**
   * Make matching requests fail or wait, for browser sweeps and tests of
   * error and loading states (mock mode never touches the network, so
   * Playwright's page.route can't reach it). Returns a function that removes
   * the fault.
   */
  inject(fault: MockFault): () => void;
  /** Remove every injected fault. */
  clearFaults(): void;
}

/**
 * One injected fault. `path` matches the request path (a string matches
 * exactly or as a "{param}" pattern; a RegExp is tested). With `status` the
 * request answers `{detail}` with that status; with `network` it rejects
 * like a failed fetch; with only `delayMs` it waits, then goes through.
 * `times` limits how many requests it hits (default: all of them).
 */
export interface MockFault {
  method?: string;
  path: string | RegExp;
  status?: number;
  detail?: string;
  network?: boolean;
  delayMs?: number;
  times?: number;
}

export interface MockBackendOptions {
  seed: () => Promise<MockSeed> | MockSeed;
  /** Pause before each scripted run event. Default 600; tests pass 0. */
  stepDelayMs?: number;
  /** Where state persists between page loads (window.localStorage in the browser). */
  storage?: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null;
  now?: () => Date;
}

/** localStorage key for the persisted mock state. */
export const MOCK_STORAGE_KEY = "stitch.mock.v1";

const INVITE_DAYS = 14;
const DAY_MS = 24 * 60 * 60 * 1000;
/** api/routes/memories.py MIN_YEAR; the latest year is next year (the server's clock + 1). */
const MIN_MEMORY_YEAR = 1800;
const SHORT_TELLING_WORDS = 25;
/** api/routes/memories.py MAX_PHOTOS and MAX_PARTICIPANTS. */
const MAX_PHOTOS = 10;
const MAX_PARTICIPANTS = 100;
/** Largest photo POST /families/{fid}/media takes (api/db/media.py MAX_BYTES). */
export const MAX_MEDIA_BYTES = 20 * 1024 * 1024;
/** What POST /families/{fid}/media and photos take (api/routes ALLOWED_*_TYPES); anything else is a 415. */
const UPLOAD_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"];

/** api/seed/fake_runs.py FOLLOWUP_QUESTION, so both modes ask the same thing. */
export const FOLLOW_UP_QUESTION = "Who else was there that day?";
/** api/routes/memories.py NOT_WAITING: the run is not paused on a question or review. */
export const NOT_WAITING = "This run is not waiting for an answer";
/** api/routes/memories.py ANSWER_WRONG_KIND: paused, but on another kind of answer. */
export const ANSWER_WRONG_KIND = "That answer does not match what this run is waiting for";

// ---------------------------------------------------------------------------
// State

type Role = "admin" | "member";
type PauseKind = AnswerRequest["kind"];
type Loc = Array<string | number>;
type Json = Record<string, unknown>;

interface MockUser {
  id: string;
  email: string;
  name: string;
  password: string;
  createdAt: string;
}

interface MockMember {
  userId: string;
  role: Role;
  joinedAt?: string | null;
}

interface MockInvite {
  code: string;
  createdBy: string;
  expiresAt: string;
  revoked?: boolean;
}

interface MockFamily {
  id: string;
  name: string;
  members: MockMember[];
  invites: MockInvite[];
}

interface MockState {
  v: 1;
  users: MockUser[];
  /** The logged-in user's id: the mock's session cookie */
  session: string | null;
  families: MockFamily[];
  people: Person[];
  memories: Memory[];
  questions: Question[];
  props: Prop[];
  sets: SetDef[];
}

/** The scene a new moment starts from: the seed's first memory with a screenplay. */
interface SceneTemplate {
  familyId: string;
  memoryId: string;
  screenplay: Screenplay;
  timeline: Timeline | null;
  propIds: string[];
  setIds: string[];
  props: Prop[];
  sets: SetDef[];
}

interface MediaEntry {
  familyId: string;
  blob: Blob;
  url: string | null;
}

type RunEventName = keyof RunEvents;

interface MockRun {
  id: string;
  familyId: string;
  userId: string;
  authorId: string;
  text: string;
  year: number;
  question: string | null;
  title: string | null;
  memoryId: string | null;
  participantIds: string[];
  photoIds: string[];
  events: Array<{ event: RunEventName; data: unknown }>;
  listeners: Set<() => void>;
  waiting: PauseKind | null;
  resume: (() => void) | null;
  finished: boolean;
  chips: PropChip[];
  chipPhotos: Record<string, string>;
  mergePersonId: string | null;
  mergeApproved: boolean;
}

// ---------------------------------------------------------------------------
// Errors and responses

class HttpError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    super(typeof body === "object" && body && "detail" in body ? String((body as Json).detail) : "error");
    this.status = status;
    this.body = body;
  }
}

/** Thrown inside a run script when the run was reset away. */
class RunStopped extends Error {}

interface FieldError {
  loc: Loc;
  msg: string;
  type: string;
}

function fail(status: number, detail: string): never {
  throw new HttpError(status, { detail });
}

function failFields(errors: FieldError[]): never {
  throw new HttpError(422, { detail: errors });
}

function check(errors: FieldError[]): void {
  if (errors.length) failFields(errors);
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function noContent(): Response {
  return new Response(null, { status: 204 });
}

// The real API's error details, word for word (api/auth.py, api/routes/*.py), so
// a page shows the same message in mock mode as against the API.
const NOT_SIGNED_IN = "Not signed in";
/** auth.family_member: a family the caller is not in is a 404, like one that doesn't exist. */
const NO_FAMILY = "Not found";
const ADMIN_ONLY = "Admins only";
const NOT_A_MEMBER = "Member not found";
/** 409 for removing or demoting a family's only admin (the API's LAST_ADMIN). */
const LAST_ADMIN = "A family needs at least one admin";
const INVITE_NOT_FOUND = "Invite not found or expired";
const PERSON_NOT_FOUND = "Person not found";
const MEMORY_NOT_FOUND = "Memory not found";
const RUN_NOT_FOUND = "Run not found";
const NOT_RUN_OWNER = "Only the person who started this run can answer";
const NO_PERSON = "Add or claim your person in this family first";
const NO_SCENE = "This moment has no scene yet, so another telling cannot be added to it";
const INVALID_LOGIN = "Invalid email or password";
const EMAIL_TAKEN = "An account with this email already exists";
const BLANK_PERSON_NAME = "A person needs a name";
const JOIN_EXACTLY_ONE = "Send exactly one of personId (claim) or person (add yourself)";
const ALREADY_HAS_PERSON = "this user already has a person in this family";

// ---------------------------------------------------------------------------
// Small helpers

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function isRecord(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function unique<T>(items: T[]): T[] {
  return Array.from(new Set(items));
}

const ID_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789";

function randomToken(length: number): string {
  const bytes = new Uint8Array(length);
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c && typeof c.getRandomValues === "function") {
    c.getRandomValues(bytes);
  } else {
    for (let i = 0; i < length; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  let out = "";
  for (let i = 0; i < length; i++) out += ID_ALPHABET[bytes[i] % ID_ALPHABET.length];
  return out;
}

function newId(prefix: string): string {
  return `${prefix}_${randomToken(10)}`;
}

function eraOf(year: number): string {
  if (year < 1940) return "pre1940";
  if (year >= 2020) return "2020s";
  return `${Math.floor(year / 10) * 10}s`;
}

function wordCount(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, Math.max(0, ms)));
}

function escapeXml(text: string): string {
  return text.replace(/[<>&"']/g, (ch) =>
    ch === "<" ? "&lt;" : ch === ">" ? "&gt;" : ch === "&" ? "&amp;" : ch === '"' ? "&quot;" : "&#39;",
  );
}

/** A small paper-coloured card, so mock mode never requests a missing image. */
function placeholderSvg(label: string): string {
  return (
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240" viewBox="0 0 320 240">' +
    '<rect width="320" height="240" fill="#fbf6ec"/>' +
    '<rect x="10" y="10" width="300" height="220" rx="6" fill="#f3ead8" stroke="#e6d9c0" stroke-width="2" stroke-dasharray="7 5"/>' +
    `<text x="160" y="128" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="20" fill="#7a6857">${escapeXml(label)}</text>` +
    "</svg>"
  );
}

/** Media ids may come as "media/<id>" (the stored art form) or bare. */
function bareId(mediaId: string): string {
  return mediaId.replace(/^media\//, "");
}

function svgDataUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/** Frame media ids end in their time ("mockframe_<run>_4.5s"); the placeholder shows it. */
function mediaLabel(mediaId: string): string {
  const frame = /^mockframe_.+_(\d+(?:\.\d+)?)s$/.exec(mediaId);
  return frame ? `Frame at ${frame[1]}s` : "Photo";
}

const PALETTE = [
  "#3f6fb5",
  "#c0504d",
  "#7b5ea7",
  "#5f7f52",
  "#d08a2e",
  "#2e8b8b",
  "#a0522d",
  "#b5476b",
  "#4f6d7a",
  "#8a7a2f",
  "#6b4f9e",
  "#3d8b5f",
];

const SIGNATURE_ITEMS = [
  "blue flat cap",
  "red scarf",
  "round glasses",
  "knitted cardigan",
  "straw hat",
  "pearl earrings",
  "green backpack",
  "yellow raincoat",
  "wristwatch",
  "bow tie",
  "sun hat",
  "wool mittens",
];

const BUILDS = ["small", "short", "average", "tall"] as const;

/** Things a new story might mention, for the prop master's new chip. */
const STORY_THINGS: Array<{ word: RegExp; name: string; size: PropChip["size"] }> = [
  { word: /\bbik(e|es|ed)\b|\bbicycles?\b/i, name: "bicycle", size: "carried" },
  { word: /\bcars?\b|\bsedan\b/i, name: "family car", size: "vehicle" },
  { word: /\btrucks?\b/i, name: "pickup truck", size: "vehicle" },
  { word: /\bboats?\b/i, name: "rowing boat", size: "vehicle" },
  { word: /\bcakes?\b/i, name: "birthday cake", size: "hand" },
  { word: /\bguitars?\b/i, name: "guitar", size: "carried" },
  { word: /\bpiano\b/i, name: "upright piano", size: "furniture" },
  { word: /\bletters?\b/i, name: "letter", size: "tiny" },
  { word: /\bumbrellas?\b/i, name: "umbrella", size: "carried" },
  { word: /\bsuitcases?\b/i, name: "suitcase", size: "carried" },
  { word: /\bcameras?\b/i, name: "camera", size: "hand" },
  { word: /\bradio\b/i, name: "radio", size: "hand" },
  { word: /\bkites?\b/i, name: "kite", size: "carried" },
  { word: /\bhats?\b|\bcaps?\b/i, name: "hat", size: "tiny" },
  { word: /\brings?\b/i, name: "ring", size: "tiny" },
  { word: /\btables?\b/i, name: "kitchen table", size: "furniture" },
  { word: /\bdoors?\b/i, name: "front door", size: "furniture" },
];

const PLAN_STEPS = [
  "Listen to the story",
  "Sketch the storyboard",
  "Find the props",
  "Build the scenery",
  "Direct the scene",
  "Record the narration",
  "Check the pages",
  "Add it to everyone's books",
];

function planAt(index: number): RunEvents["plan"] {
  const todos: PlanItem[] = PLAN_STEPS.map((text, i) => ({
    text,
    status: i < index ? "completed" : i === index ? "in_progress" : "pending",
  }));
  return { todos };
}

const STOP_WORDS = new Set(
  (
    "a an and are as at be but by did do does for from had has have he her his how i in is it its me my " +
    "of on or our she so that the their them then there they this to was we were what when where which who " +
    "why with you your about tell story remember"
  ).split(" "),
);

// ---------------------------------------------------------------------------
// Validation helpers (FastAPI-style 422 bodies)

function requiredString(
  body: Json,
  key: string,
  loc: Loc,
  errors: FieldError[],
  opts: { min?: number; blankMsg?: string } = {},
): string {
  const value = body[key];
  if (value === undefined || value === null) {
    errors.push({ loc: [...loc, key], msg: "Field required", type: "missing" });
    return "";
  }
  if (typeof value !== "string") {
    errors.push({ loc: [...loc, key], msg: "Input should be a valid string", type: "string_type" });
    return "";
  }
  const min = opts.min ?? 0;
  if (value.length < min) {
    errors.push({
      loc: [...loc, key],
      msg: `String should have at least ${min} character${min === 1 ? "" : "s"}`,
      type: "string_too_short",
    });
    return value;
  }
  if (opts.blankMsg && !value.trim()) {
    errors.push({ loc: [...loc, key], msg: opts.blankMsg, type: "value_error" });
  }
  return value;
}

function optionalString(body: Json, key: string, loc: Loc, errors: FieldError[]): string | null {
  const value = body[key];
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") {
    errors.push({ loc: [...loc, key], msg: "Input should be a valid string", type: "string_type" });
    return null;
  }
  return value;
}

/**
 * An `int | None` field as pydantic reads it (lax mode): an integer, or a
 * string of digits. No range: the API takes any year for a person and checks
 * only that the years fit together (died after born, born after a parent).
 */
function optionalYear(body: Json, key: string, loc: Loc, errors: FieldError[]): number | null {
  const value = body[key];
  if (value === undefined || value === null) return null;
  if (typeof value === "number" && Number.isInteger(value)) return value;
  if (typeof value === "string" && /^\s*[-+]?\d+\s*$/.test(value)) return parseInt(value, 10);
  if (typeof value === "string") {
    errors.push({
      loc: [...loc, key],
      msg: "Input should be a valid integer, unable to parse string as an integer",
      type: "int_parsing",
    });
  } else if (typeof value === "number") {
    errors.push({
      loc: [...loc, key],
      msg: "Input should be a valid integer, got a number with a fractional part",
      type: "int_from_float",
    });
  } else {
    errors.push({ loc: [...loc, key], msg: "Input should be a valid integer", type: "int_type" });
  }
  return null;
}

/** A `list[str]` field: kept as sent (the API dedupes spouses itself and rejects a duplicate parent). */
function optionalIdList(body: Json, key: string, loc: Loc, errors: FieldError[]): string[] | null {
  const value = body[key];
  if (value === undefined || value === null) return null;
  if (!Array.isArray(value)) {
    errors.push({ loc: [...loc, key], msg: "Input should be a valid list", type: "list_type" });
    return null;
  }
  const bad = value.findIndex((v) => typeof v !== "string");
  if (bad !== -1) {
    errors.push({ loc: [...loc, key, bad], msg: "Input should be a valid string", type: "string_type" });
    return null;
  }
  return [...(value as string[])];
}

function optionalCharacter(
  body: Json,
  loc: Loc,
  errors: FieldError[],
): CharacterSheet | null | undefined {
  const value = body.character;
  if (value === undefined) return undefined;
  if (value === null) return null;
  if (!isRecord(value)) {
    errors.push({ loc: [...loc, "character"], msg: "Input should be a valid dictionary", type: "dict_type" });
    return null;
  }
  const inner = [...loc, "character"];
  const before = errors.length;
  const color = requiredString(value, "color", inner, errors, { min: 1 });
  const item = optionalString(value, "item", inner, errors);
  const itemPropId = optionalString(value, "itemPropId", inner, errors);
  const notes = optionalString(value, "notes", inner, errors);
  const build = value.build;
  if (build !== undefined && !(BUILDS as readonly unknown[]).includes(build)) {
    errors.push({
      loc: [...inner, "build"],
      msg: "Input should be 'small', 'short', 'average' or 'tall'",
      type: "literal_error",
    });
  }
  if (errors.length > before) return null;
  return fullSheet({ color, item, build: build as CharacterSheet["build"], itemPropId, notes });
}

/** A character sheet as the API sends it: every field present, `build` "average" by default. */
function fullSheet(sheet: CharacterSheet): CharacterSheet {
  return {
    color: sheet.color,
    item: sheet.item ?? null,
    build: sheet.build ?? "average",
    itemPropId: sheet.itemPropId ?? null,
    notes: sheet.notes ?? null,
  };
}

// ---------------------------------------------------------------------------
// Request reading

interface MockRequest {
  method: string;
  path: string;
  body: unknown;
  signal: AbortSignal | null;
}

async function readRequest(input: RequestInfo | URL, init?: RequestInit): Promise<MockRequest> {
  const isRequest = typeof Request !== "undefined" && input instanceof Request;
  const href = isRequest ? (input as Request).url : input instanceof URL ? input.href : String(input);
  const url = new URL(href, "http://mock.local");
  const method = (init?.method ?? (isRequest ? (input as Request).method : "GET")).toUpperCase();
  const signal = init?.signal ?? (isRequest ? (input as Request).signal : null);
  let body: unknown = init?.body;
  if (body === undefined && isRequest && method !== "GET" && method !== "HEAD") {
    const req = input as Request;
    const type = req.headers.get("content-type") ?? "";
    body = type.includes("multipart/form-data") ? await req.formData() : await req.text();
  }
  let path = url.pathname.replace(/\/+$/, "");
  if (!path) path = "/";
  return { method, path, body, signal: signal ?? null };
}

function jsonBody(body: unknown): Json {
  let text: string | null = null;
  if (typeof body === "string") text = body;
  else if (body instanceof ArrayBuffer) text = new TextDecoder().decode(body);
  else if (ArrayBuffer.isView(body)) text = new TextDecoder().decode(body as ArrayBufferView);
  if (body === undefined || body === null || text === "") {
    failFields([{ loc: ["body"], msg: "Field required", type: "missing" }]);
  }
  if (text === null) {
    failFields([{ loc: ["body"], msg: "Input should be a valid dictionary", type: "model_attributes_type" }]);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    failFields([{ loc: ["body", 0], msg: "JSON decode error", type: "json_invalid" }]);
  }
  if (!isRecord(parsed)) {
    failFields([{ loc: ["body"], msg: "Input should be a valid dictionary", type: "model_attributes_type" }]);
  }
  return parsed;
}

function abortError(signal: AbortSignal): unknown {
  if (signal.reason !== undefined) return signal.reason;
  return new DOMException("This operation was aborted", "AbortError");
}

// ---------------------------------------------------------------------------
// Narration for another telling

/** The telling's sentences, one narration line each (api/seed/fake_runs.py split_sentences). */
export function splitSentences(text: string): string[] {
  return text
    .split(/(?<=[.!?…])\s+|\n+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

const WORD_GAP = 0.25;

/**
 * Give `authorId` their own narration track over the moment's shared action,
 * so the book can play their version (Timeline.narrations, Screenplay.narrations):
 * one line per sentence, spread evenly over the scene, words WORD_GAP apart.
 * The default narrator's own retelling also becomes the default captions.
 * Does nothing for a moment without a scene.
 */
export function addNarration(memory: Memory, authorId: string, text: string): void {
  const timeline = memory.timeline;
  const screenplay = memory.screenplay;
  if (!timeline || !screenplay) return;
  const sentences = splitSentences(text);
  if (!sentences.length) return;
  const duration = timeline.duration > 0 ? timeline.duration : 10;
  const round = (n: number) => Math.round(n * 100) / 100;
  const lines: NarrationLine[] = sentences.map((sentence, i) => {
    const t = round((duration * i) / sentences.length + 0.5);
    const words = sentence.split(/\s+/).filter(Boolean);
    return {
      t,
      text: sentence,
      source: `telling:${authorId}:s${i + 1}`,
      words: words.map((word, w) => ({ t: round(t + w * WORD_GAP), word: word.replace(/^[^\p{L}\p{N}']+|[^\p{L}\p{N}']+$/gu, "") || word })),
    };
  });
  const captions = lines.map((l) => ({ t: l.t, text: l.text }));
  timeline.narrations = { ...(timeline.narrations ?? {}), [authorId]: { authorId, audio: null, captions, lines } };
  if (timeline.defaultNarrator === authorId) timeline.captions = captions;

  // The screenplay keeps the same lines per shot, in shot time.
  const shots = timeline.shots?.length ? timeline.shots : null;
  const byShot: Record<string, NarrationLine[]> = {};
  for (const line of lines) {
    const shot = shots ? ([...shots].reverse().find((s) => s.start <= line.t) ?? shots[0]) : null;
    const shotId = shot?.id ?? screenplay.shots[0]?.id;
    if (!shotId) continue;
    const start = shot ? shot.actionStart : 0;
    (byShot[shotId] ??= []).push({ t: round(Math.max(0, line.t - start)), text: line.text, source: line.source });
  }
  screenplay.narrations = { ...(screenplay.narrations ?? {}), [authorId]: byShot };
}

// ---------------------------------------------------------------------------
// Scene cloning

/** Rewrite person ids throughout a screenplay or timeline (strings and object keys). */
function remapIds(value: unknown, map: Record<string, string>): unknown {
  if (typeof value === "string") {
    if (Object.prototype.hasOwnProperty.call(map, value)) return map[value];
    const puppet = /^puppet:([^.:]+)(.*)$/.exec(value);
    if (puppet && Object.prototype.hasOwnProperty.call(map, puppet[1])) {
      return `puppet:${map[puppet[1]]}${puppet[2]}`;
    }
    const telling = /^telling:([^:]+)(:.*)?$/.exec(value);
    if (telling && Object.prototype.hasOwnProperty.call(map, telling[1])) {
      return `telling:${map[telling[1]]}${telling[2] ?? ""}`;
    }
    return value;
  }
  if (Array.isArray(value)) return value.map((v) => remapIds(v, map));
  if (isRecord(value)) {
    const out: Json = {};
    for (const [k, v] of Object.entries(value)) {
      const key = Object.prototype.hasOwnProperty.call(map, k) ? map[k] : k;
      out[key] = remapIds(v, map);
    }
    return out;
  }
  return value;
}

/**
 * Map the template's cast onto this moment's participants (author first),
 * one to one. Cast members left over keep their own id when it is free,
 * otherwise take a template id nobody was mapped to.
 */
function castMap(cast: string[], participants: string[]): Record<string, string> {
  const map: Record<string, string> = {};
  const used = new Set<string>();
  cast.forEach((id, i) => {
    if (i < participants.length) {
      map[id] = participants[i];
      used.add(participants[i]);
    }
  });
  const free = cast.filter((id) => !used.has(id));
  for (let i = participants.length; i < cast.length; i++) {
    const id = cast[i];
    const target = free.includes(id) ? id : free.find((f) => !used.has(f));
    if (target) {
      map[id] = target;
      used.add(target);
    }
  }
  for (const [k, v] of Object.entries(map)) if (k === v) delete map[k];
  return map;
}

// ---------------------------------------------------------------------------
// The backend

export function createMockBackend(opts: MockBackendOptions): MockBackend {
  const stepDelayMs = opts.stepDelayMs ?? 600;
  const storage = opts.storage ?? null;
  const now = opts.now ?? (() => new Date());

  let state: MockState | null = null;
  let template: SceneTemplate | null = null;
  let loading: Promise<void> | null = null;
  /** Bumped by reset(), so work started before a reset can tell it is stale. */
  let generation = 0;
  const runs = new Map<string, MockRun>();
  const media = new Map<string, MediaEntry>();

  // ----- persistence

  function save(): void {
    if (!storage || !state) return;
    try {
      storage.setItem(MOCK_STORAGE_KEY, JSON.stringify(state));
    } catch {
      // Storage full or blocked: mock mode keeps working from memory.
    }
  }

  function restore(): MockState | null {
    if (!storage) return null;
    let raw: string | null = null;
    try {
      raw = storage.getItem(MOCK_STORAGE_KEY);
    } catch {
      return null;
    }
    if (!raw) return null;
    try {
      const parsed: unknown = JSON.parse(raw);
      if (!isRecord(parsed) || parsed.v !== 1) return null;
      const lists = ["users", "families", "people", "memories", "questions", "props", "sets"];
      if (!lists.every((k) => Array.isArray(parsed[k]))) return null;
      if (parsed.session !== null && typeof parsed.session !== "string") return null;
      const users = parsed.users as unknown[];
      if (!users.every((u) => isRecord(u) && typeof u.id === "string" && typeof u.email === "string")) return null;
      return parsed as unknown as MockState;
    } catch {
      return null;
    }
  }

  function stateFromSeed(seed: MockSeed): MockState {
    const at = now().toISOString();
    return {
      v: 1,
      users: seed.users.map((u) => ({ ...u, createdAt: at })),
      session: null,
      families: seed.families.map((f) => ({
        id: f.id,
        name: f.name,
        members: f.members.map((m) => ({ ...m, joinedAt: at })),
        invites: f.invites.map((i) => ({ revoked: false, ...i })),
      })),
      people: clone(seed.people).map((p) => (p.character ? { ...p, character: fullSheet(p.character) } : p)),
      memories: clone(seed.memories),
      questions: clone(seed.questions),
      props: clone(seed.props ?? []),
      sets: clone(seed.sets ?? []),
    };
  }

  function templateFromSeed(seed: MockSeed): SceneTemplate | null {
    const source = seed.memories.find((m) => m.screenplay);
    if (!source || !source.screenplay) return null;
    const propIds = source.propIds ?? [];
    const setIds = source.setIds ?? [];
    const props = seed.props ?? [];
    const sets = seed.sets ?? [];
    return {
      familyId: source.familyId,
      memoryId: source.id,
      screenplay: clone(source.screenplay),
      timeline: source.timeline ? clone(source.timeline) : null,
      propIds: [...propIds],
      setIds: [...setIds],
      props: clone(props.filter((p) => propIds.includes(p.id))),
      sets: clone(sets.filter((s) => setIds.includes(s.id))),
    };
  }

  async function ensureState(): Promise<MockState> {
    if (state) return state;
    if (!loading) {
      const gen = generation;
      const pending = (async () => {
        const seed = await opts.seed();
        if (gen !== generation) return;
        template = templateFromSeed(seed);
        state = restore() ?? stateFromSeed(seed);
      })();
      loading = pending;
      // A failed seed load is retried on the next request.
      pending.catch(() => {
        if (loading === pending) loading = null;
      });
    }
    await loading;
    if (!state) return ensureState();
    return state;
  }

  // ----- access control

  function currentUser(s: MockState): MockUser | null {
    if (!s.session) return null;
    return s.users.find((u) => u.id === s.session) ?? null;
  }

  function requireUser(s: MockState): MockUser {
    const user = currentUser(s);
    if (!user) fail(401, NOT_SIGNED_IN);
    return user;
  }

  interface Membership {
    user: MockUser;
    family: MockFamily;
    role: Role;
    person: Person | null;
  }

  function requireMember(s: MockState, fid: string): Membership {
    const user = requireUser(s);
    const family = s.families.find((f) => f.id === fid);
    const member = family?.members.find((m) => m.userId === user.id);
    if (!family || !member) fail(404, NO_FAMILY);
    return { user, family, role: member.role, person: personOf(s, fid, user.id) };
  }

  function requireAdmin(s: MockState, fid: string): Membership {
    const m = requireMember(s, fid);
    if (m.role !== "admin") fail(403, ADMIN_ONLY);
    return m;
  }

  function requireAuthor(s: MockState, m: Membership): Person {
    if (!m.person) fail(409, NO_PERSON);
    return m.person;
  }

  // ----- lookups and views

  function personOf(s: MockState, fid: string, userId: string): Person | null {
    return s.people.find((p) => p.familyId === fid && p.userId === userId) ?? null;
  }

  function familyPeople(s: MockState, fid: string): Person[] {
    return s.people.filter((p) => p.familyId === fid);
  }

  function findPerson(s: MockState, fid: string, id: string): Person | null {
    return s.people.find((p) => p.familyId === fid && p.id === id) ?? null;
  }

  function userView(u: MockUser): User {
    return { id: u.id, email: u.email, name: u.name, createdAt: u.createdAt };
  }

  function summaryOf(s: MockState, family: MockFamily, userId: string): FamilySummary {
    const member = family.members.find((m) => m.userId === userId);
    return {
      id: family.id,
      name: family.name,
      role: member?.role ?? "member",
      personId: personOf(s, family.id, userId)?.id ?? null,
    };
  }

  function meOf(s: MockState, user: MockUser): MeResponse {
    const families = s.families
      .filter((f) => f.members.some((m) => m.userId === user.id))
      .map((f) => summaryOf(s, f, user.id));
    return { user: userView(user), families };
  }

  function personSummary(p: Person): PersonSummary {
    return { id: p.id, name: p.name, born: p.born ?? null, color: p.character?.color ?? null };
  }

  function byYear(a: Memory, b: Memory): number {
    return a.year - b.year || a.title.localeCompare(b.title) || a.id.localeCompare(b.id);
  }

  function inviteValid(invite: MockInvite | undefined): invite is MockInvite {
    if (!invite || invite.revoked) return false;
    const expires = Date.parse(invite.expiresAt);
    return Number.isFinite(expires) && expires > now().getTime();
  }

  function findInvite(s: MockState, code: string): { family: MockFamily; invite: MockInvite } | null {
    for (const family of s.families) {
      const invite = family.invites.find((i) => i.code === code);
      if (invite) return inviteValid(invite) ? { family, invite } : null;
    }
    return null;
  }

  function memoryBundle(s: MockState, fid: string, memories: Memory[], extraPeople: string[] = []) {
    const propIds = new Set(memories.flatMap((m) => m.propIds ?? []));
    const setIds = new Set(memories.flatMap((m) => m.setIds ?? []));
    const peopleIds = new Set(extraPeople);
    for (const m of memories) {
      for (const id of m.participantIds ?? []) peopleIds.add(id);
      for (const t of m.tellings ?? []) peopleIds.add(t.authorId);
    }
    return {
      props: s.props.filter((p) => p.familyId === fid && propIds.has(p.id)),
      sets: s.sets.filter((x) => x.familyId === fid && setIds.has(x.id)),
      people: familyPeople(s, fid).filter((p) => peopleIds.has(p.id)),
    };
  }

  function pickCharacter(s: MockState, fid: string): CharacterSheet {
    const people = familyPeople(s, fid);
    const colors = new Set(people.map((p) => p.character?.color?.toLowerCase()));
    const items = new Set(people.map((p) => p.character?.item));
    const color = PALETTE.find((c) => !colors.has(c.toLowerCase())) ?? PALETTE[people.length % PALETTE.length];
    const item =
      SIGNATURE_ITEMS.find((i) => !items.has(i)) ?? SIGNATURE_ITEMS[people.length % SIGNATURE_ITEMS.length];
    return fullSheet({ color, item, build: "average" });
  }

  // ----- people
  //
  // Two layers, as in the API: readPerson is the request model (schemas.NewPerson
  // or PersonPatch, a 422 list for a wrong type or a missing name), and
  // checkNewPerson / checkPatch are api/db/people.py's InvalidRelation checks,
  // in its order, each a 400 with its exact message.

  interface PersonFields {
    name?: string;
    born?: number | null;
    died?: number | null;
    parentIds?: string[];
    spouseIds?: string[];
    character?: CharacterSheet | null;
  }

  /** Read a NewPerson (partial = PersonPatch) body: types only, names trimmed. */
  function readPerson(raw: Json, loc: Loc, errors: FieldError[], opts2: { partial: boolean }): PersonFields {
    const out: PersonFields = {};
    if (!opts2.partial || (raw.name !== undefined && raw.name !== null)) {
      out.name = requiredString(raw, "name", loc, errors).trim();
    }
    if (raw.born !== undefined) out.born = optionalYear(raw, "born", loc, errors);
    if (raw.died !== undefined) out.died = optionalYear(raw, "died", loc, errors);
    for (const key of ["parentIds", "spouseIds"] as const) {
      if (raw[key] === undefined) continue;
      if (raw[key] === null) {
        // NewPerson's lists can't be null; a patch's null means "leave as is".
        if (!opts2.partial) errors.push({ loc: [...loc, key], msg: "Input should be a valid list", type: "list_type" });
        continue;
      }
      const ids = optionalIdList(raw, key, loc, errors);
      if (ids !== null) out[key] = key === "spouseIds" ? unique(ids) : ids;
    }
    const character = optionalCharacter(raw, loc, errors);
    if (character) out.character = character;
    return out;
  }

  /** The route's name check (api/routes/families.py _clean_new_person): 400 for a blank name. */
  function requireName(fields: PersonFields): void {
    if (fields.name !== undefined && !fields.name) fail(400, BLANK_PERSON_NAME);
  }

  /** Everyone below `id` through parentIds (api/db/people.py _descendant_ids). */
  function descendantIds(people: Person[], id: string): Set<string> {
    const children = new Map<string, string[]>();
    for (const p of people) {
      for (const parent of p.parentIds ?? []) children.set(parent, [...(children.get(parent) ?? []), p.id]);
    }
    const seen = new Set<string>();
    const queue = [id];
    while (queue.length) {
      for (const child of children.get(queue.shift() as string) ?? []) {
        if (!seen.has(child)) {
          seen.add(child);
          queue.push(child);
        }
      }
    }
    return seen;
  }

  /** Everyone above `id` through parentIds, given each person's parents (_ancestor_ids). */
  function ancestorIds(parentsOf: (id: string) => string[], id: string): Set<string> {
    const seen = new Set<string>();
    const queue = [id];
    while (queue.length) {
      for (const parent of parentsOf(queue.shift() as string)) {
        if (!seen.has(parent)) {
          seen.add(parent);
          queue.push(parent);
        }
      }
    }
    seen.delete(id);
    return seen;
  }

  /** api/db/people.py _validate_relations. */
  function validateRelations(
    s: MockState,
    fid: string,
    parentIds: string[] | undefined,
    spouseIds: string[] | undefined,
    selfId: string | null,
  ): void {
    if (parentIds) {
      if (parentIds.length > 2) fail(400, "a person has at most 2 parents");
      if (new Set(parentIds).size !== parentIds.length) fail(400, "duplicate parent id");
    }
    const named = [...(parentIds ?? []), ...(spouseIds ?? [])];
    if (selfId !== null && named.includes(selfId)) fail(400, "a person cannot be their own parent or spouse");
    if (parentIds && spouseIds && parentIds.some((id) => spouseIds.includes(id))) {
      fail(400, "a person cannot be both a parent and a spouse");
    }
    const missing = unique(named.filter((id) => !findPerson(s, fid, id))).sort();
    if (missing.length) {
      fail(400, `unknown person id(s) in this family: [${missing.map((id) => `'${id}'`).join(", ")}]`);
    }
  }

  /** check_lifespan, then _check_born_after_parents (unknown years pass). */
  function checkYears(s: MockState, fid: string, born: number | null, died: number | null, parentIds: string[]): void {
    if (born !== null && died !== null && died < born) fail(400, "a person cannot die before they were born");
    if (born === null) return;
    const parents = parentIds
      .map((id) => findPerson(s, fid, id))
      .filter((p): p is Person => p !== null && typeof p.born === "number")
      .sort((a, b) => (a.born as number) - (b.born as number) || a.name.localeCompare(b.name));
    for (const parent of parents) {
      if (born < (parent.born as number)) {
        fail(400, `a person cannot be born before their parent (${parent.name}, born ${parent.born})`);
      }
    }
  }

  /** api/db/people.py create_person's checks, in its order. */
  function checkNewPerson(s: MockState, fid: string, fields: PersonFields): void {
    const parentIds = fields.parentIds ?? [];
    const spouseIds = fields.spouseIds ?? [];
    validateRelations(s, fid, parentIds, spouseIds, null);
    if (parentIds.length && spouseIds.length) {
      // A spouse may not be a parent or anyone further up.
      const parentsOf = (id: string) => findPerson(s, fid, id)?.parentIds ?? [];
      const above = new Set(parentIds);
      for (const parent of parentIds) for (const id of ancestorIds(parentsOf, parent)) above.add(id);
      if (spouseIds.some((id) => above.has(id))) {
        fail(400, "a person cannot be married to their own ancestor or descendant");
      }
    }
    checkYears(s, fid, fields.born ?? null, fields.died ?? null, parentIds);
  }

  /** api/db/people.py update_person's checks against the resulting relations and years. */
  function checkPatch(s: MockState, fid: string, person: Person, fields: PersonFields): void {
    const parentsAfter = fields.parentIds ?? person.parentIds ?? [];
    if (fields.parentIds !== undefined || fields.spouseIds !== undefined) {
      validateRelations(s, fid, fields.parentIds, fields.spouseIds, person.id);
      const spousesAfter = fields.spouseIds ?? person.spouseIds ?? [];
      if (parentsAfter.some((id) => spousesAfter.includes(id))) {
        fail(400, "a person cannot be both a parent and a spouse");
      }
      const people = familyPeople(s, fid);
      const below = descendantIds(people, person.id);
      if (fields.parentIds && fields.parentIds.some((id) => below.has(id))) {
        fail(400, "a person cannot be their own ancestor");
      }
      const parentsOf = (id: string) =>
        id === person.id ? parentsAfter : (findPerson(s, fid, id)?.parentIds ?? []);
      const line = new Set([...below, ...ancestorIds(parentsOf, person.id)]);
      if (spousesAfter.some((id) => line.has(id))) {
        fail(400, "a person cannot be married to their own ancestor or descendant");
      }
    }
    const bornAfter = fields.born !== undefined ? fields.born : (person.born ?? null);
    if (fields.born !== undefined || fields.died !== undefined) {
      const diedAfter = fields.died !== undefined ? fields.died : (person.died ?? null);
      if (bornAfter !== null && diedAfter !== null && diedAfter < bornAfter) {
        fail(400, "a person cannot die before they were born");
      }
    }
    if (fields.born !== undefined || fields.parentIds !== undefined) checkYears(s, fid, bornAfter, null, parentsAfter);
    if (fields.born !== undefined && bornAfter !== null) {
      const children = familyPeople(s, fid)
        .filter((p) => (p.parentIds ?? []).includes(person.id) && typeof p.born === "number")
        .sort((a, b) => (a.born as number) - (b.born as number) || a.name.localeCompare(b.name));
      for (const child of children) {
        if ((child.born as number) < bornAfter) {
          fail(400, `a person cannot be born after their child (${child.name}, born ${child.born})`);
        }
      }
    }
  }

  /** Keep spouse links two-way after a person's spouse list changes. */
  function syncSpouses(s: MockState, person: Person, before: string[]): void {
    const after = person.spouseIds ?? [];
    for (const p of familyPeople(s, person.familyId)) {
      if (p.id === person.id) continue;
      const list = p.spouseIds ?? [];
      if (after.includes(p.id) && !list.includes(person.id)) p.spouseIds = [...list, person.id];
      if (!after.includes(p.id) && before.includes(p.id)) p.spouseIds = list.filter((id) => id !== person.id);
    }
  }

  function createPerson(s: MockState, fid: string, fields: PersonFields, userId: string | null, addedBy: string): Person {
    const person: Person = {
      id: newId("person"),
      familyId: fid,
      userId,
      name: fields.name ?? "",
      born: fields.born ?? null,
      died: fields.died ?? null,
      parentIds: fields.parentIds ?? [],
      spouseIds: fields.spouseIds ?? [],
      character: fields.character ?? pickCharacter(s, fid),
      addedBy,
    };
    s.people.push(person);
    syncSpouses(s, person, []);
    return person;
  }

  // ----- runs

  function emit<K extends RunEventName>(run: MockRun, event: K, data: RunEvents[K]): void {
    run.events.push({ event, data: clone(data) });
    if (event === "done" || event === "error") run.finished = true;
    for (const listener of Array.from(run.listeners)) listener();
  }

  function formatEvent(index: number, e: { event: string; data: unknown }): string {
    return `id: ${index + 1}\nevent: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`;
  }

  function streamResponse(run: MockRun, signal: AbortSignal | null): Response {
    const encoder = new TextEncoder();
    let cursor = 0;
    let closed = false;
    let listener: (() => void) | null = null;
    let onAbort: (() => void) | null = null;

    function stop(): void {
      if (closed) return;
      closed = true;
      if (listener) run.listeners.delete(listener);
      if (signal && onAbort) signal.removeEventListener("abort", onAbort);
    }

    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        listener = () => {
          if (closed) return;
          while (cursor < run.events.length) {
            controller.enqueue(encoder.encode(formatEvent(cursor, run.events[cursor])));
            cursor += 1;
          }
          if (run.finished) {
            stop();
            controller.close();
          }
        };
        onAbort = () => {
          if (closed) return;
          stop();
          try {
            controller.close();
          } catch {
            // Already closed or errored.
          }
        };
        run.listeners.add(listener);
        if (signal) signal.addEventListener("abort", onAbort, { once: true });
        listener();
      },
      cancel() {
        stop();
      },
    });

    return new Response(stream, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
      },
    });
  }

  function buildChips(s: MockState, run: MockRun, authorName: string): PropChip[] {
    const chips: PropChip[] = [];
    const sources = template ? template.props : [];
    for (const p of sources.slice(0, 2)) {
      const own = s.props.find((x) => x.familyId === run.familyId && x.id === p.id);
      const from = own ?? p;
      chips.push({
        id: p.id,
        name: from.name,
        description: from.description ?? null,
        size: from.size,
        art: null,
        reused: Boolean(own),
      });
    }
    const thing = STORY_THINGS.find((t) => t.word.test(run.text)) ?? {
      name: "keepsake box",
      size: "hand" as const,
    };
    chips.push({
      id: `${thing.name.replace(/[^a-z0-9]+/gi, "_").toLowerCase()}_${randomToken(6)}`,
      name: thing.name,
      description: `The ${thing.name} from ${authorName}'s story`,
      size: thing.size,
      art: null,
      reused: false,
    });
    return chips;
  }

  function mergeCandidate(s: MockState, run: MockRun): Person | null {
    const people = familyPeople(s, run.familyId).filter((p) => p.id !== run.authorId);
    return people.find((p) => p.id === "luis") ?? people[0] ?? null;
  }

  /** Make sure the family owns copies of the template's props and sets. */
  function ensureTemplateAssets(s: MockState, fid: string): void {
    if (!template) return;
    for (const p of template.props) {
      if (!s.props.some((x) => x.familyId === fid && x.id === p.id)) s.props.push({ ...clone(p), familyId: fid });
    }
    for (const set of template.sets) {
      if (!s.sets.some((x) => x.familyId === fid && x.id === set.id)) s.sets.push({ ...clone(set), familyId: fid });
    }
  }

  function cloneScene(memoryId: string, participants: string[]) {
    if (!template) return { screenplay: null, timeline: null, propIds: [] as string[], setIds: [] as string[] };
    const map = castMap(template.screenplay.cast, participants);
    const screenplay = remapIds(clone(template.screenplay), map) as Screenplay;
    screenplay.memoryId = memoryId;
    const timeline = template.timeline ? (remapIds(clone(template.timeline), map) as Timeline) : null;
    return { screenplay, timeline, propIds: [...template.propIds], setIds: [...template.setIds] };
  }

  /** The run reached scene_ready: write the moment (or the new telling) into the family. */
  function commitRun(s: MockState, run: MockRun): Memory {
    const family = s.families.find((f) => f.id === run.familyId);
    if (!family) throw new Error("This family is no longer here.");
    const at = now().toISOString();
    const telling: Telling = {
      authorId: run.authorId,
      question: run.question,
      text: run.text,
      photoIds: [...run.photoIds],
      createdAt: at,
    };
    const extra = run.mergeApproved && run.mergePersonId ? [run.mergePersonId] : [];
    const participants = unique([run.authorId, ...run.participantIds, ...extra]).filter((id) =>
      Boolean(findPerson(s, run.familyId, id)),
    );

    // Props the family kept in review.
    ensureTemplateAssets(s, run.familyId);
    const kept: string[] = [];
    for (const chip of run.chips) {
      const photoId = run.chipPhotos[chip.id];
      const existing = s.props.find((p) => p.familyId === run.familyId && p.id === chip.id);
      if (existing) {
        existing.name = chip.name;
        if (photoId) {
          existing.art = { ...existing.art, body: photoId };
          existing.source = "photo";
        }
      } else {
        s.props.push({
          id: chip.id,
          familyId: run.familyId,
          name: chip.name,
          description: chip.description ?? null,
          source: photoId ? "photo" : "generated",
          size: chip.size,
          art: { body: photoId ?? null },
          color: "#b0533a",
          aspect: 1,
          anchor: [0.5, 1],
          grip: [0.5, 0.5],
          sockets: {},
          parts: {},
          states: {},
        });
      }
      kept.push(chip.id);
    }

    let memory: Memory;
    if (run.memoryId) {
      const found = s.memories.find((m) => m.familyId === run.familyId && m.id === run.memoryId);
      if (!found) throw new Error("That moment is no longer in the family.");
      memory = found;
      // A teller's new telling replaces their old one in place (api/db/memories.py add_telling).
      const tellings = memory.tellings ?? [];
      memory.tellings = tellings.some((t) => t.authorId === run.authorId)
        ? tellings.map((t) => (t.authorId === run.authorId ? telling : t))
        : [...tellings, telling];
      memory.participantIds = unique([...(memory.participantIds ?? []), ...participants]);
      memory.version = (memory.version ?? 1) + 1;
      if (!memory.screenplay) {
        const scene = cloneScene(memory.id, memory.participantIds);
        memory.screenplay = scene.screenplay;
        memory.timeline = scene.timeline;
        memory.setIds = unique([...(memory.setIds ?? []), ...scene.setIds]);
        memory.propIds = unique([...(memory.propIds ?? []), ...scene.propIds]);
      }
      memory.propIds = unique([...(memory.propIds ?? []), ...kept]);
      addNarration(memory, run.authorId, run.text);
    } else {
      const id = newId("memory");
      const scene = cloneScene(id, participants);
      memory = {
        id,
        familyId: run.familyId,
        title: run.title?.trim() || run.question?.trim() || "A new memory",
        year: run.year,
        era: eraOf(run.year),
        participantIds: participants,
        tellings: [telling],
        screenplay: scene.screenplay,
        timeline: scene.timeline,
        // The cloned scene needs its own props even if a chip for one was removed.
        propIds: unique([...scene.propIds, ...kept]),
        setIds: scene.setIds,
        version: 1,
      };
      s.memories.push(memory);
    }

    // The caller's open questions this telling answers.
    const asked = run.question?.trim();
    for (const q of s.questions) {
      if (q.familyId !== run.familyId || q.personId !== run.authorId || q.status !== "open") continue;
      if ((run.memoryId && q.memoryId === run.memoryId) || (asked && !q.memoryId && q.text === asked)) {
        q.status = "answered";
      }
    }
    return memory;
  }

  /** The linker: ask everyone else in a new moment for their side of it. */
  function linkQuestions(s: MockState, run: MockRun, memory: Memory, authorName: string): number {
    if (run.memoryId) return 0;
    let added = 0;
    for (const pid of memory.participantIds ?? []) {
      if (pid === run.authorId) continue;
      const exists = s.questions.some(
        (q) => q.familyId === run.familyId && q.personId === pid && q.memoryId === memory.id && q.status === "open",
      );
      if (exists) continue;
      s.questions.push({
        id: newId("question"),
        familyId: run.familyId,
        personId: pid,
        text: `${authorName} told the story of "${memory.title}". What do you remember?`,
        memoryId: memory.id,
        status: "open",
      });
      added += 1;
    }
    return added;
  }

  async function runScript(run: MockRun, gen: number): Promise<void> {
    const alive = () => gen === generation && runs.get(run.id) === run;
    const live = (): MockState => {
      if (!alive() || !state) throw new RunStopped();
      return state;
    };
    const step = async <K extends RunEventName>(event: K, data: RunEvents[K]) => {
      await sleep(stepDelayMs);
      live();
      emit(run, event, data);
    };
    /** Emit an event that needs the teller, already waiting when a client sees it. */
    const pauseOn = async <K extends RunEventName>(kind: PauseKind, event: K, data: RunEvents[K]) => {
      await sleep(stepDelayMs);
      live();
      const answered = new Promise<void>((resolve) => {
        run.resume = resolve;
      });
      run.waiting = kind;
      emit(run, event, data);
      await answered;
      run.waiting = null;
      run.resume = null;
      live();
    };

    try {
      const author = findPerson(live(), run.familyId, run.authorId);
      const authorName = author?.name ?? "Someone";
      const second = Boolean(run.memoryId);

      if (second) {
        // As the scripted studio's second telling (api/seed/fake_runs.py): no
        // follow-up and no prop review; the narrator writes this teller's lines
        // over the moment's scene, then the telling is added.
        const todos = ["Read the telling", "Write the narration", "Add it to the moment"];
        const plan = (at: number): RunEvents["plan"] => ({
          todos: todos.map((text, i) => ({
            text,
            status: i < at ? "completed" : i === at ? "in_progress" : "pending",
          })),
        });
        await step("plan", plan(0));
        await step("subagent_start", { name: "interviewer", task: "Read the new telling of this moment" });
        await step("subagent_end", { name: "interviewer", summary: "The telling is ready to narrate." });
        await step("plan", plan(1));
        await step("subagent_start", { name: "narrator", task: "Write the new teller's narration over the scene" });
        const lines = splitSentences(run.text).length;
        await step("subagent_end", {
          name: "narrator",
          summary: `${lines} line${lines === 1 ? "" : "s"}, compiled without errors.`,
        });
        await step("plan", plan(2));
        await sleep(stepDelayMs);
        const memory = commitRun(live(), run);
        save();
        emit(run, "scene_ready", { memoryId: memory.id, version: memory.version ?? 1 });
        await step("plan", plan(todos.length));
        await step("done", { memoryId: memory.id });
        return;
      }

      await step("plan", planAt(0));
      await step("subagent_start", { name: "interviewer", task: "Read the telling and look for what's missing" });
      const short = wordCount(run.text) < SHORT_TELLING_WORDS;
      await step("subagent_end", {
        name: "interviewer",
        summary: short ? "The story is short, so one question for you." : "The story has what the scene needs.",
      });
      if (short) {
        await pauseOn("answer", "question", { text: FOLLOW_UP_QUESTION });
      }
      if (/\buncle\b/i.test(run.text)) {
        const candidate = mergeCandidate(live(), run);
        if (candidate) {
          run.mergePersonId = candidate.id;
          const born = candidate.born ? `, born ${candidate.born}` : "";
          await pauseOn("merge", "merge", {
            text: `Your story mentions an uncle. ${candidate.name}${born}, is already in the family tree.`,
            personId: candidate.id,
            candidateName: `Uncle ${candidate.name}`,
          });
        }
      }

      await step("plan", planAt(1));
      await step("subagent_start", { name: "storyboarder", task: "Break the story into shots" });
      await step("subagent_end", {
        name: "storyboarder",
        summary: second ? "Lined your telling up with the scene that's already there." : "Three shots: arriving, the moment, and after.",
      });

      await step("plan", planAt(2));
      await step("subagent_start", { name: "prop_master", task: "Find the things in the story" });
      run.chips = buildChips(live(), run, authorName);
      await pauseOn("props", "props_found", { props: run.chips });
      await step("subagent_end", {
        name: "prop_master",
        summary: `Kept ${run.chips.length} ${run.chips.length === 1 ? "prop" : "props"}.`,
      });

      await step("plan", planAt(3));
      await step("subagent_start", { name: "set_designer", task: "Find or build the place" });
      const setName = template?.sets[0]?.name;
      await step("subagent_end", {
        name: "set_designer",
        summary: setName ? `Reused ${setName}.` : "Built a new place.",
      });

      await step("plan", planAt(4));
      await step("subagent_start", { name: "director", task: "Block the scene" });
      await step("subagent_end", { name: "director", summary: "First pass at the blocking." });
      const propId = run.chips[0]?.id ?? template?.propIds[0] ?? "keepsake";
      const error: CompileError = {
        code: "not_holding",
        message: `shot s2, t=4.5: ${run.authorId} cannot hand_to "${propId}"; they are not holding it`,
        shot: "s2",
        t: 4.5,
        who: run.authorId,
        do: "hand_to",
      };
      await step("compile_error", { errors: [error], round: 1 });
      await step("subagent_start", { name: "director", task: "Fix the hand-off" });
      await step("subagent_end", { name: "director", summary: "Picked it up first, then handed it over. It compiles." });

      await step("plan", planAt(5));
      await step("subagent_start", { name: "narrator", task: `Time ${authorName}'s words to the action` });
      await step("subagent_end", { name: "narrator", summary: "Narration recorded and cued." });

      await step("plan", planAt(6));
      await step("subagent_start", { name: "checker", task: "Look at the scene at key moments" });
      const duration = template?.timeline?.duration ?? 9;
      const frames: FrameShot[] = [0.2, 0.5, 0.8].map((f) => {
        const t = Math.round(duration * f * 10) / 10;
        return { t, mediaId: `mockframe_${run.id}_${t}s` };
      });
      await step("frames", { frames, critique: "Everyone is on their marks and the hand-off reads clearly." });
      await step("subagent_end", { name: "checker", summary: "The pages look right." });

      await sleep(stepDelayMs);
      const s = live();
      const memory = commitRun(s, run);
      save();
      emit(run, "scene_ready", { memoryId: memory.id, version: memory.version ?? 1 });

      await step("plan", planAt(7));
      await step("subagent_start", { name: "linker", task: "Put it in everyone's books" });
      const asked = linkQuestions(live(), run, memory, authorName);
      save();
      await step("subagent_end", {
        name: "linker",
        summary: asked
          ? `Added it to ${memory.participantIds?.length ?? 1} books and asked ${asked} ${asked === 1 ? "person" : "people"} for their side.`
          : `Added it to ${memory.participantIds?.length ?? 1} ${(memory.participantIds?.length ?? 1) === 1 ? "book" : "books"}.`,
      });
      await step("plan", planAt(PLAN_STEPS.length));
      await step("done", { memoryId: memory.id });
    } catch (err) {
      if (err instanceof RunStopped || !alive()) return;
      emit(run, "error", {
        message: err instanceof Error && err.message ? err.message : "The studio stopped unexpectedly.",
      });
    }
  }

  /** A family as GET /families/{fid} shows it to a viewer with `role`: invites for admins only. */
  function familyView(family: MockFamily, role: Role): Family {
    return {
      id: family.id,
      name: family.name,
      members: family.members.map((m) => ({ userId: m.userId, role: m.role, joinedAt: m.joinedAt ?? null })),
      invites: role === "admin" ? family.invites.map((i) => ({ revoked: false, ...i })) : [],
    };
  }

  // ----- route handlers

  type Handler = (s: MockState, req: MockRequest, params: string[]) => Promise<Response> | Response;

  const routes: Array<{ method: string; pattern: RegExp; handler: Handler }> = [];
  function route(method: string, pattern: string, handler: Handler): void {
    const source = pattern.replace(/\{[a-zA-Z]+\}/g, "([^/]+)");
    routes.push({ method, pattern: new RegExp(`^${source}$`), handler });
  }

  // Accounts

  // api/routes/auth.py: the request model first (a 422 list), then the email
  // (stripped and lowercased; it needs an "@") and the name, each a 422 with
  // one sentence, then a 409 for an email already in use.
  route("POST", "/auth/signup", (s, req) => {
    const body = jsonBody(req.body);
    const errors: FieldError[] = [];
    const rawEmail = requiredString(body, "email", ["body"], errors);
    const password = requiredString(body, "password", ["body"], errors, { min: 8 });
    const name = requiredString(body, "name", ["body"], errors);
    check(errors);
    const email = rawEmail.trim().toLowerCase();
    if (!email || !email.includes("@")) fail(422, "Enter a valid email");
    if (!name.trim()) fail(422, "Enter your name");
    if (s.users.some((u) => u.email.toLowerCase() === email)) fail(409, EMAIL_TAKEN);
    const user: MockUser = {
      id: newId("user"),
      email,
      name: name.trim(),
      password,
      createdAt: now().toISOString(),
    };
    s.users.push(user);
    s.session = user.id;
    save();
    return json(201, meOf(s, user));
  });

  route("POST", "/auth/login", (s, req) => {
    const body = jsonBody(req.body);
    const errors: FieldError[] = [];
    const email = requiredString(body, "email", ["body"], errors).trim().toLowerCase();
    const password = requiredString(body, "password", ["body"], errors);
    check(errors);
    const user = s.users.find((u) => u.email.toLowerCase() === email);
    if (!user || user.password !== password) fail(401, INVALID_LOGIN);
    s.session = user.id;
    save();
    return json(200, meOf(s, user));
  });

  route("POST", "/auth/logout", (s) => {
    s.session = null;
    save();
    return noContent();
  });

  route("GET", "/me", (s) => json(200, meOf(s, requireUser(s))));

  // Families

  // api/routes/families.py create_family: every check before anything is written.
  route("POST", "/families", (s, req) => {
    const user = requireUser(s);
    const body = jsonBody(req.body);
    const errors: FieldError[] = [];
    const name = requiredString(body, "name", ["body"], errors);
    const fid = newId("family");
    let fields: PersonFields = {};
    if (body.person === undefined || body.person === null) {
      errors.push({ loc: ["body", "person"], msg: "Field required", type: "missing" });
    } else if (!isRecord(body.person)) {
      errors.push({ loc: ["body", "person"], msg: "Input should be a valid dictionary", type: "dict_type" });
    } else {
      fields = readPerson(body.person, ["body", "person"], errors, { partial: false });
    }
    check(errors);
    if (!name.trim()) fail(400, "A family needs a name");
    if (fields.parentIds?.length || fields.spouseIds?.length) {
      fail(400, "The first person in a new family cannot have parents or spouses yet");
    }
    requireName(fields);
    checkYears(s, fid, fields.born ?? null, fields.died ?? null, []);
    const family: MockFamily = {
      id: fid,
      name: name.trim(),
      members: [{ userId: user.id, role: "admin", joinedAt: now().toISOString() }],
      invites: [],
    };
    s.families.push(family);
    const person = createPerson(s, fid, fields, user.id, user.id);
    save();
    const out: CreateFamilyResponse = { family: summaryOf(s, family, user.id), person };
    return json(201, out);
  });

  // Not in section 6: the family settings page needs members and invites, and
  // `Family` is already a contract type. Invites are shown to admins only.
  route("GET", "/families/{fid}", (s, _req, [fid]) => {
    const { family, role } = requireMember(s, fid);
    return json(200, familyView(family, role));
  });

  route("POST", "/families/{fid}/invites", (s, _req, [fid]) => {
    const { user, family } = requireAdmin(s, fid);
    const code = randomToken(12);
    const expiresAt = new Date(now().getTime() + INVITE_DAYS * DAY_MS).toISOString();
    family.invites.push({ code, createdBy: user.id, expiresAt, revoked: false });
    save();
    const out: InviteResponse = { code, path: `/join/${code}`, expiresAt };
    return json(201, out);
  });

  // Revoking a code this family has, even one already revoked or expired, is a 204.
  route("DELETE", "/families/{fid}/invites/{code}", (s, _req, [fid, code]) => {
    const { family } = requireAdmin(s, fid);
    const invite = family.invites.find((i) => i.code === code);
    if (!invite) fail(404, INVITE_NOT_FOUND);
    invite.revoked = true;
    save();
    return noContent();
  });

  route("GET", "/invites/{code}", (s, _req, [code]) => {
    const found = findInvite(s, code);
    if (!found) fail(404, INVITE_NOT_FOUND);
    const out: InvitePreview = {
      familyId: found.family.id,
      familyName: found.family.name,
      unclaimedPeople: familyPeople(s, found.family.id)
        .filter((p) => !p.userId)
        .map(personSummary),
    };
    return json(200, out);
  });

  // api/routes/families.py join_family: the body (a 422 list, then exactly one
  // of personId or person, a 422 sentence), the invite (404), then the claim or
  // the new person. Someone already in the family may use a link again: claiming
  // their own person is a no-op, anything else is a 409. A failed join leaves no
  // membership behind.
  route("POST", "/invites/{code}/join", (s, req, [code]) => {
    const user = requireUser(s);
    const body = jsonBody(req.body);
    const errors: FieldError[] = [];
    let fields: PersonFields | null = null;
    let personId: string | null = null;
    if (body.personId !== undefined && body.personId !== null) {
      if (typeof body.personId !== "string") {
        errors.push({ loc: ["body", "personId"], msg: "Input should be a valid string", type: "string_type" });
      } else {
        personId = body.personId;
      }
    }
    if (body.person !== undefined && body.person !== null) {
      if (!isRecord(body.person)) {
        errors.push({ loc: ["body", "person"], msg: "Input should be a valid dictionary", type: "model_attributes_type" });
      } else {
        fields = readPerson(body.person, ["body", "person"], errors, { partial: false });
      }
    }
    check(errors);
    if ((personId === null) === (fields === null)) fail(422, JOIN_EXACTLY_ONE);
    const found = findInvite(s, code);
    if (!found) fail(404, INVITE_NOT_FOUND);
    const { family } = found;
    const mine = personOf(s, family.id, user.id);

    let person: Person;
    if (personId !== null) {
      const target = findPerson(s, family.id, personId);
      if (!target) fail(404, PERSON_NOT_FOUND);
      if (target.userId && target.userId !== user.id) fail(409, "Someone has already claimed this person");
      if (mine && mine.id !== target.id) fail(409, ALREADY_HAS_PERSON);
      target.userId = user.id;
      person = target;
    } else {
      const add = fields as PersonFields;
      requireName(add);
      checkNewPerson(s, family.id, add);
      if (mine) fail(409, "You already have a person in this family");
      person = createPerson(s, family.id, add, user.id, user.id);
    }
    if (!family.members.some((m) => m.userId === user.id)) {
      family.members.push({ userId: user.id, role: "member", joinedAt: now().toISOString() });
    }
    save();
    const out: JoinResponse = { family: summaryOf(s, family, user.id), person };
    return json(200, out);
  });

  route("POST", "/families/{fid}/people", (s, req, [fid]) => {
    const { user } = requireMember(s, fid);
    const body = jsonBody(req.body);
    const errors: FieldError[] = [];
    const fields = readPerson(body, ["body"], errors, { partial: false });
    check(errors);
    requireName(fields);
    checkNewPerson(s, fid, fields);
    const person = createPerson(s, fid, fields, null, user.id);
    save();
    return json(201, person);
  });

  route("PATCH", "/families/{fid}/people/{id}", (s, req, [fid, id]) => {
    requireMember(s, fid);
    const body = jsonBody(req.body);
    const errors: FieldError[] = [];
    const fields = readPerson(body, ["body"], errors, { partial: true });
    check(errors);
    requireName(fields);
    const person = findPerson(s, fid, id);
    if (!person) fail(404, PERSON_NOT_FOUND);
    checkPatch(s, fid, person, fields);
    const spousesBefore = [...(person.spouseIds ?? [])];
    if (fields.name !== undefined) person.name = fields.name;
    if (fields.born !== undefined) person.born = fields.born;
    if (fields.died !== undefined) person.died = fields.died;
    if (fields.parentIds !== undefined) person.parentIds = fields.parentIds;
    if (fields.spouseIds !== undefined) {
      person.spouseIds = fields.spouseIds;
      syncSpouses(s, person, spousesBefore);
    }
    if (fields.character) person.character = fields.character;
    save();
    return json(200, person);
  });

  route("DELETE", "/families/{fid}/members/{userId}", (s, _req, [fid, userId]) => {
    const { family } = requireAdmin(s, fid);
    const member = family.members.find((m) => m.userId === userId);
    if (!member) fail(404, NOT_A_MEMBER);
    if (member.role === "admin" && family.members.filter((m) => m.role === "admin").length <= 1) {
      fail(409, LAST_ADMIN);
    }
    family.members = family.members.filter((m) => m.userId !== userId);
    for (const p of familyPeople(s, fid)) if (p.userId === userId) p.userId = null;
    save();
    return noContent();
  });

  // Admin: make a member an admin or a member (api/routes/families.py
  // set_member_role). The same role is a no-op; the last admin can't be
  // demoted; an admin who steps down gets the member's view back.
  route("PATCH", "/families/{fid}/members/{userId}", (s, req, [fid, userId]) => {
    const { user, family } = requireAdmin(s, fid);
    const body = jsonBody(req.body);
    const role = body.role;
    if (role !== "admin" && role !== "member") {
      failFields([
        role === undefined
          ? { loc: ["body", "role"], msg: "Field required", type: "missing" }
          : { loc: ["body", "role"], msg: "Input should be 'admin' or 'member'", type: "literal_error" },
      ]);
    }
    const member = family.members.find((m) => m.userId === userId);
    if (!member) fail(404, NOT_A_MEMBER);
    if (member.role !== role) {
      if (role === "member" && !family.members.some((m) => m.role === "admin" && m.userId !== userId)) {
        fail(409, LAST_ADMIN);
      }
      member.role = role;
      save();
    }
    const viewer = family.members.find((m) => m.userId === user.id);
    return json(200, familyView(family, viewer?.role ?? "member"));
  });

  // Family content

  route("GET", "/families/{fid}/tree", (s, _req, [fid]) => {
    const { family } = requireMember(s, fid);
    const out: TreeResponse = { familyId: family.id, familyName: family.name, people: familyPeople(s, fid) };
    return json(200, out);
  });

  route("GET", "/families/{fid}/people/{id}/book", (s, _req, [fid, id]) => {
    requireMember(s, fid);
    const person = findPerson(s, fid, id);
    if (!person) fail(404, "Not found");
    const memories = s.memories
      .filter(
        (m) =>
          m.familyId === fid &&
          ((m.participantIds ?? []).includes(id) || (m.tellings ?? []).some((t) => t.authorId === id)),
      )
      .sort(byYear);
    const out: BookResponse = { person, memories, ...memoryBundle(s, fid, memories, [id]) };
    return json(200, out);
  });

  route("GET", "/families/{fid}/memories/{id}", (s, _req, [fid, id]) => {
    requireMember(s, fid);
    const memory = s.memories.find((m) => m.familyId === fid && m.id === id);
    if (!memory) fail(404, "Not found");
    const out: MemoryResponse = { memory, ...memoryBundle(s, fid, [memory]) };
    return json(200, out);
  });

  route("GET", "/families/{fid}/threads", (s, _req, [fid]) => {
    requireMember(s, fid);
    const moments: MomentSummary[] = s.memories
      .filter((m) => m.familyId === fid)
      .sort(byYear)
      .map((m) => ({
        id: m.id,
        title: m.title,
        year: m.year,
        participantIds: m.participantIds ?? [],
        era: m.era ?? eraOf(m.year),
        tellingAuthorIds: unique((m.tellings ?? []).map((t) => t.authorId)),
      }));
    const out: ThreadsResponse = { people: familyPeople(s, fid).map(personSummary), moments };
    return json(200, out);
  });

  route("GET", "/families/{fid}/media/{id}", (s, _req, [fid, raw]) => {
    requireMember(s, fid);
    const id = bareId(raw);
    const entry = media.get(id);
    if (entry && entry.familyId === fid) {
      return new Response(entry.blob, {
        status: 200,
        headers: { "Content-Type": entry.blob.type || "application/octet-stream" },
      });
    }
    return new Response(placeholderSvg(mediaLabel(id)), {
      status: 200,
      headers: { "Content-Type": "image/svg+xml" },
    });
  });

  route("GET", "/families/{fid}/props", (s, _req, [fid]) => {
    requireMember(s, fid);
    const out: PropsResponse = { props: s.props.filter((p) => p.familyId === fid) };
    return json(200, out);
  });

  route("GET", "/families/{fid}/sets", (s, _req, [fid]) => {
    requireMember(s, fid);
    const out: SetsResponse = { sets: s.sets.filter((x) => x.familyId === fid) };
    return json(200, out);
  });

  route("PATCH", "/families/{fid}/props/{id}", (s, req, [fid, id]) => {
    requireMember(s, fid);
    const body = jsonBody(req.body);
    const errors: FieldError[] = [];
    const rawName = optionalString(body, "name", ["body"], errors);
    const rawPhotoId = optionalString(body, "photoId", ["body"], errors);
    check(errors);
    if (rawName === null && rawPhotoId === null) fail(422, "Nothing to change");
    const name = rawName === null ? null : rawName.trim();
    if (name !== null && !name) fail(422, "Name must not be blank");
    if (name !== null && name.length > 200) fail(422, "Name must be at most 200 characters");
    const prop = s.props.find((p) => p.familyId === fid && p.id === id);
    if (!prop) fail(404, "Not found");
    const photoId = rawPhotoId ? bareId(rawPhotoId) : null;
    // As api/routes/props.py patch_prop: 400 "Unknown photo" for a photo that is not
    // this family's, and the art keeps the "media/<id>" form whichever form was sent.
    if (photoId && media.get(photoId)?.familyId !== fid) fail(400, "Unknown photo");
    if (name !== null) prop.name = name.trim();
    if (photoId) {
      prop.art = { ...prop.art, body: `media/${photoId}` };
      prop.source = "photo";
    }
    save();
    return json(200, prop);
  });

  /** Keep an uploaded photo; mediaUrl and GET /families/{fid}/media/{id} serve it. */
  function storeMedia(fid: string, blob: Blob): string {
    const id = newId("media");
    let url: string | null = null;
    try {
      if (typeof URL !== "undefined" && typeof URL.createObjectURL === "function") url = URL.createObjectURL(blob);
    } catch {
      url = null;
    }
    media.set(id, { familyId: fid, blob, url });
    return id;
  }

  // Mirrors api/routes/props.py upload_media: one multipart field `file`.
  route("POST", "/families/{fid}/media", (s, req, [fid]) => {
    requireMember(s, fid);
    const file =
      typeof FormData !== "undefined" && req.body instanceof FormData ? req.body.get("file") : null;
    if (file === null || typeof file === "string") {
      failFields([{ loc: ["body", "file"], msg: "Field required", type: "missing" }]);
    }
    const type = (file.type || "").split(";", 1)[0].trim().toLowerCase();
    if (!UPLOAD_IMAGE_TYPES.includes(type)) fail(415, "Upload a JPEG, PNG, WebP, GIF or HEIC/HEIF image");
    if (file.size > MAX_MEDIA_BYTES) fail(413, "Photos must be 20 MB or smaller");
    if (file.size === 0) fail(400, "The file is empty");
    return json(201, { id: storeMedia(fid, file) });
  });

  // api/routes/memories.py submit_memory, step by step: the form (a 422 list),
  // the fields (422 sentences), the author (409), the moment (404), the
  // participants (400), the photos (422, 415, 413), then the studio: a second
  // telling needs a moment with a scene (409). 202 with the run id.
  route("POST", "/families/{fid}/memories", (s, req, [fid]) => {
    const m = requireMember(s, fid);
    const form = typeof FormData !== "undefined" && req.body instanceof FormData ? req.body : new FormData();
    const errors: FieldError[] = [];
    // FastAPI reads an empty form value as a missing one.
    const field = (key: string): string | null => {
      const v = form.get(key);
      return typeof v === "string" && v !== "" ? v : null;
    };

    const text = field("text");
    if (text === null) errors.push({ loc: ["body", "text"], msg: "Field required", type: "missing" });
    const yearRaw = field("year");
    let year = 0;
    if (yearRaw === null) {
      errors.push({ loc: ["body", "year"], msg: "Field required", type: "missing" });
    } else if (!/^\s*[-+]?\d+\s*$/.test(yearRaw)) {
      errors.push({
        loc: ["body", "year"],
        msg: "Input should be a valid integer, unable to parse string as an integer",
        type: "int_parsing",
      });
    } else {
      year = parseInt(yearRaw, 10);
    }
    check(errors);

    const blankToNull = (v: string | null) => (v !== null && v.trim() ? v : null);
    if (!(text as string).trim()) fail(422, "Write the memory before submitting it");
    const question = blankToNull(field("question"));
    const title = blankToNull(field("title"));
    if ((text as string).length > 20_000) fail(422, "The memory must be at most 20,000 characters");
    if (question && question.length > 1_000) fail(422, "The question must be at most 1,000 characters");
    if (title && title.length > 200) fail(422, "The title must be at most 200 characters");
    const participantIds = unique(form.getAll("participantIds").filter((v): v is string => typeof v === "string"));
    if (participantIds.length > MAX_PARTICIPANTS) {
      fail(422, `A memory can have at most ${MAX_PARTICIPANTS} people in it`);
    }
    const maxYear = now().getUTCFullYear() + 1;
    if (year < MIN_MEMORY_YEAR || year > maxYear) fail(422, `Year must be between ${MIN_MEMORY_YEAR} and ${maxYear}`);

    const author = requireAuthor(s, m);
    const memoryId = blankToNull(field("memoryId"));
    const moment = memoryId ? s.memories.find((x) => x.familyId === fid && x.id === memoryId) : null;
    if (memoryId && !moment) fail(404, MEMORY_NOT_FOUND);
    if (participantIds.some((id) => !findPerson(s, fid, id))) {
      fail(400, "Every participant must be a person in this family");
    }

    const photos: File[] = [];
    for (const part of form.getAll("photos")) {
      if (typeof part === "string") {
        if (part) fail(422, "Photos must be uploaded as files");
        continue;
      }
      if (!part.name && part.size === 0) continue;
      photos.push(part as File);
    }
    if (photos.length > MAX_PHOTOS) fail(422, `Attach at most ${MAX_PHOTOS} photos`);
    for (const photo of photos) {
      const type = (photo.type || "").split(";", 1)[0].trim().toLowerCase();
      if (!UPLOAD_IMAGE_TYPES.includes(type)) fail(415, "Photos must be JPEG, PNG, WebP, GIF or HEIC images");
    }
    for (const photo of photos) {
      if (photo.size > MAX_MEDIA_BYTES) fail(413, "A photo is too large");
      if (photo.size === 0) fail(422, "A photo is empty");
    }
    // The studio adds a telling only to a moment that has a scene (seed/fake_runs.py MemoryNotFound).
    if (moment && (!moment.screenplay || !moment.timeline)) fail(409, NO_SCENE);

    const photoIds = photos.map((photo) => storeMedia(fid, photo));

    const run: MockRun = {
      id: newId("run"),
      familyId: fid,
      userId: m.user.id,
      authorId: author.id,
      text: (text as string).trim(),
      year,
      question: question?.trim() || null,
      title: title?.trim() || null,
      memoryId,
      participantIds: unique([author.id, ...participantIds]),
      photoIds,
      events: [],
      listeners: new Set(),
      waiting: null,
      resume: null,
      finished: false,
      chips: [],
      chipPhotos: {},
      mergePersonId: null,
      mergeApproved: false,
    };
    runs.set(run.id, run);
    void runScript(run, generation);
    const out: SubmitMemoryResponse = { runId: run.id, memoryId };
    return json(202, out);
  });

  /** A run of this family (api/routes/memories.py: any member may follow it; 404 otherwise). */
  function familyRun(fid: string, runId: string): MockRun {
    const run = runs.get(runId);
    if (!run || run.familyId !== fid) fail(404, RUN_NOT_FOUND);
    return run;
  }

  route("GET", "/families/{fid}/runs/{id}/stream", (s, req, [fid, id]) => {
    requireMember(s, fid);
    return streamResponse(familyRun(fid, id), req.signal);
  });

  // api/routes/memories.py answer_run_route: the body (a 422 list, then
  // _check_answer's sentences), then the run (404), its owner (403), whether it
  // is waiting (409 NOT_WAITING) and for this kind (409 ANSWER_WRONG_KIND). As
  // in the scripted studio, edits for a prop not in the review are ignored.
  route("POST", "/families/{fid}/runs/{id}/answer", (s, req, [fid, id]) => {
    const { user } = requireMember(s, fid);
    const body = jsonBody(req.body);
    const errors: FieldError[] = [];
    const kind = body.kind;
    if (kind !== "answer" && kind !== "props" && kind !== "merge") {
      errors.push({
        loc: ["body", "kind"],
        msg: kind === undefined ? "Field required" : "Input should be 'answer', 'props' or 'merge'",
        type: kind === undefined ? "missing" : "literal_error",
      });
    }
    const text = optionalString(body, "text", ["body"], errors);
    if (body.approve !== undefined && body.approve !== null && typeof body.approve !== "boolean") {
      errors.push({ loc: ["body", "approve"], msg: "Input should be a valid boolean", type: "bool_type" });
    }
    let edits: PropEdit[] | null = null;
    if (body.props !== undefined && body.props !== null) {
      if (!Array.isArray(body.props)) {
        errors.push({ loc: ["body", "props"], msg: "Input should be a valid list", type: "list_type" });
      } else {
        edits = [];
        body.props.forEach((raw: unknown, i: number) => {
          const loc: Loc = ["body", "props", i];
          if (!isRecord(raw)) {
            errors.push({ loc, msg: "Input should be a valid dictionary", type: "model_attributes_type" });
            return;
          }
          const before = errors.length;
          requiredString(raw, "id", loc, errors);
          if (!["keep", "rename", "remove", "replace_photo"].includes(raw.action as string)) {
            errors.push({
              loc: [...loc, "action"],
              msg:
                raw.action === undefined
                  ? "Field required"
                  : "Input should be 'keep', 'rename', 'remove' or 'replace_photo'",
              type: raw.action === undefined ? "missing" : "literal_error",
            });
          }
          optionalString(raw, "name", loc, errors);
          optionalString(raw, "photoId", loc, errors);
          if (errors.length === before) edits?.push(raw as unknown as PropEdit);
        });
      }
    }
    check(errors);
    if (kind === "answer" && !text?.trim()) fail(422, "An answer needs text");
    if (kind === "props" && edits === null) fail(422, "A props review needs the props list");
    if (kind === "merge" && typeof body.approve !== "boolean") fail(422, "A merge answer needs approve");
    if (text && text.length > 20_000) fail(422, "The answer must be at most 20,000 characters");
    if (edits && edits.length > 100) fail(422, "A props review can list at most 100 props");

    const run = familyRun(fid, id);
    if (run.userId !== user.id) fail(403, NOT_RUN_OWNER);
    if (run.finished || !run.waiting || !run.resume) fail(409, NOT_WAITING);
    if (run.waiting !== kind) fail(409, ANSWER_WRONG_KIND);

    if (kind === "answer") {
      run.text = `${run.text}\n\n${(text as string).trim()}`;
    } else if (kind === "props") {
      const chipIds = new Set(run.chips.map((c) => c.id));
      const valid = (edits ?? []).filter((edit) => {
        if (!chipIds.has(edit.id)) return false;
        if (edit.action === "rename") return typeof edit.name === "string" && edit.name.trim() !== "";
        if (edit.action === "replace_photo") {
          return typeof edit.photoId === "string" && media.get(bareId(edit.photoId))?.familyId === fid;
        }
        return true;
      });
      for (const edit of valid) {
        if (edit.action === "remove") {
          run.chips = run.chips.filter((c) => c.id !== edit.id);
        } else if (edit.action === "rename") {
          run.chips = run.chips.map((c) => (c.id === edit.id ? { ...c, name: (edit.name ?? c.name).trim() } : c));
        } else if (edit.action === "replace_photo" && edit.photoId) {
          run.chipPhotos[edit.id] = `media/${bareId(edit.photoId)}`; // stored art form, as the real API
        }
      }
    } else {
      run.mergeApproved = body.approve === true;
    }

    const resume = run.resume;
    run.resume = null;
    run.waiting = null;
    resume();
    return noContent();
  });

  route("GET", "/families/{fid}/questions", (s, _req, [fid]) => {
    const { person } = requireMember(s, fid);
    // Newest first, as api/db/memories.py open_questions (the list is in the order asked).
    const questions = person
      ? s.questions
          .filter((q) => q.familyId === fid && q.personId === person.id && (q.status ?? "open") === "open")
          .reverse()
      : [];
    const out: QuestionsResponse = { questions };
    return json(200, out);
  });

  route("POST", "/families/{fid}/ask", (s, req, [fid]) => {
    requireMember(s, fid);
    const body = jsonBody(req.body);
    const errors: FieldError[] = [];
    const query = requiredString(body, "query", ["body"], errors, { min: 1, blankMsg: "Ask a question first." });
    check(errors);
    return json(200, ask(s, fid, query));
  });

  function ask(s: MockState, fid: string, query: string): AskResponse {
    const terms = unique(
      query
        .toLowerCase()
        .split(/[^a-z0-9']+/)
        .map((w) => w.replace(/'s$/, "").replace(/'/g, ""))
        .filter((w) => w.length >= 3 && !STOP_WORDS.has(w)),
    );
    const stem = (w: string) => w.replace(/(ing|ed|es|s)$/, "");
    const stems = terms.map(stem).filter((w) => w.length >= 3);
    const hits = (text: string): number => {
      const words = text.toLowerCase().split(/[^a-z0-9]+/).map(stem);
      return stems.filter((t) => words.includes(t)).length;
    };
    const scored: Array<{ score: number; quote: AskQuote }> = [];
    for (const memory of s.memories.filter((m) => m.familyId === fid)) {
      for (const telling of memory.tellings ?? []) {
        const sentences = telling.text.split(/(?<=[.!?])\s+/).filter(Boolean);
        let best = "";
        let bestScore = 0;
        for (const sentence of sentences) {
          const n = hits(sentence);
          if (n > bestScore) {
            best = sentence;
            bestScore = n;
          }
        }
        const score = hits(`${memory.title} ${telling.text}`) + bestScore;
        if (score > 0) {
          scored.push({
            score,
            quote: {
              memoryId: memory.id,
              authorId: telling.authorId,
              text: (best || telling.text).trim(),
              title: memory.title,
              year: memory.year,
            },
          });
        }
      }
    }
    scored.sort((a, b) => b.score - a.score || (a.quote.year ?? 0) - (b.quote.year ?? 0));
    const quotes = scored.slice(0, 3).map((x) => x.quote);
    if (!quotes.length) {
      return { answer: "Nobody in the family has told that story yet.", quotes: [] };
    }
    const top = quotes[0];
    const name = findPerson(s, fid, top.authorId)?.name ?? "Someone";
    const others = quotes.length - 1;
    const more = others ? `, and ${others} other ${others === 1 ? "telling mentions" : "tellings mention"} it too` : "";
    return { answer: `${name} remembers it in "${top.title}" (${top.year})${more}.`, quotes };
  }

  // ----- fetch

  // ----- injected faults

  interface ActiveFault {
    fault: MockFault;
    matches: (path: string) => boolean;
    left: number;
  }
  const faults: ActiveFault[] = [];

  function faultMatcher(path: string | RegExp): (p: string) => boolean {
    if (path instanceof RegExp) return (p) => path.test(p);
    const source = path
      .replace(/\/+$/, "")
      .split(/(\{[a-zA-Z]+\})/)
      .map((part) => (/^\{[a-zA-Z]+\}$/.test(part) ? "[^/]+" : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))
      .join("");
    const re = new RegExp(`^${source}$`);
    return (p) => re.test(p);
  }

  /** The first live fault for this request, used up by one. */
  function takeFault(method: string, path: string): MockFault | null {
    const hit = faults.find(
      (f) => f.left > 0 && (!f.fault.method || f.fault.method.toUpperCase() === method) && f.matches(path),
    );
    if (!hit) return null;
    hit.left -= 1;
    if (hit.left <= 0) faults.splice(faults.indexOf(hit), 1);
    return hit.fault;
  }

  async function handle(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const req = await readRequest(input, init);
    if (req.signal?.aborted) throw abortError(req.signal);
    const fault = takeFault(req.method, req.path);
    if (fault) {
      if (fault.delayMs) await sleep(fault.delayMs);
      if (req.signal?.aborted) throw abortError(req.signal);
      if (fault.network) throw new TypeError("Failed to fetch");
      if (fault.status) {
        return fault.status >= 500 && fault.detail === undefined
          ? new Response("Internal Server Error", { status: fault.status, headers: { "Content-Type": "text/plain" } })
          : json(fault.status, { detail: fault.detail ?? "Injected failure" });
      }
    }
    let s: MockState;
    try {
      s = await ensureState();
    } catch {
      return json(503, { detail: "The demo family couldn't load." });
    }
    const matches = routes
      .map((r) => ({ r, m: r.pattern.exec(req.path) }))
      .filter((x): x is { r: (typeof routes)[number]; m: RegExpExecArray } => x.m !== null);
    if (!matches.length) return json(404, { detail: "Not Found" });
    const hit = matches.find((x) => x.r.method === req.method);
    if (!hit) return json(405, { detail: "Method Not Allowed" });
    let params: string[];
    try {
      params = hit.m.slice(1).map((p) => decodeURIComponent(p));
    } catch {
      return json(404, { detail: "Not Found" });
    }
    try {
      return await hit.r.handler(s, req, params);
    } catch (err) {
      if (err instanceof HttpError) return json(err.status, err.body);
      console.error("[mock backend]", err);
      return json(500, { detail: "Something went wrong in the mock backend." });
    }
  }

  return {
    fetch: (input: RequestInfo | URL, init?: RequestInit) => handle(input, init),

    inject(fault: MockFault): () => void {
      const active: ActiveFault = {
        fault,
        matches: faultMatcher(fault.path),
        left: fault.times && fault.times > 0 ? fault.times : Infinity,
      };
      faults.push(active);
      return () => {
        const at = faults.indexOf(active);
        if (at !== -1) faults.splice(at, 1);
      };
    },

    clearFaults(): void {
      faults.length = 0;
    },

    mediaUrl(fid: string, raw: string): string {
      const mediaId = bareId(raw);
      const entry = media.get(mediaId);
      if (entry && entry.familyId === fid && entry.url) return entry.url;
      return svgDataUrl(placeholderSvg(mediaLabel(mediaId)));
    },

    reset(): void {
      generation += 1;
      for (const run of runs.values()) {
        run.finished = true;
        run.resume = null;
        for (const listener of Array.from(run.listeners)) listener();
      }
      runs.clear();
      for (const entry of media.values()) {
        if (entry.url && typeof URL !== "undefined" && typeof URL.revokeObjectURL === "function") {
          try {
            URL.revokeObjectURL(entry.url);
          } catch {
            // Ignore: the URL is gone either way.
          }
        }
      }
      media.clear();
      state = null;
      template = null;
      loading = null;
      if (storage) {
        try {
          storage.removeItem(MOCK_STORAGE_KEY);
        } catch {
          // Blocked storage: nothing to clear.
        }
      }
    },
  };
}
