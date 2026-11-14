// Typed client for the FastAPI backend: every route in ARCHITECTURE.md
// section 6, plus GET /families/{fid}/threads.
//
// Request and response types come from ./types (generated from
// api/schemas.py). `api` here always talks to the real server. Pages call
// `getApi()` from ./client instead, which returns `api`, or, when
// `isMockMode()` (NEXT_PUBLIC_API_MOCK=1), a client wired to the in-memory
// mock backend. Tests call `createApiClient` with an injected fetch.
//
// This file has no runtime imports and uses only erasable TypeScript (no
// enums, namespaces, parameter properties or decorators), so `node --test`
// can import it directly. Tests: components/flow/__tests__/apiClient.test.mjs.

import type {
  AnswerRequest,
  AskRequest,
  AskResponse,
  BookResponse,
  CreateFamilyRequest,
  CreateFamilyResponse,
  ErrorResponse,
  Family,
  InvitePreview,
  InviteResponse,
  JoinRequest,
  JoinResponse,
  LoginRequest,
  MeResponse,
  MemoryResponse,
  NewPerson,
  Person,
  PersonPatch,
  Prop,
  PropPatch,
  PropsResponse,
  QuestionsResponse,
  RunEvents,
  SetsResponse,
  SignupRequest,
  SubmitMemoryFields,
  SubmitMemoryResponse,
  ThreadsResponse,
  TreeResponse,
} from "./types";

/** API origin, from NEXT_PUBLIC_API_URL (inlined by Next at build time). */
export const API_BASE_URL: string = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
).replace(/\/+$/, "");

/** True when NEXT_PUBLIC_API_MOCK=1: pages should talk to the in-memory mock. */
export function isMockMode(): boolean {
  return process.env.NEXT_PUBLIC_API_MOCK === "1";
}

// ---------------------------------------------------------------------------
// Errors

/** Shown when the request never reached the server (offline, DNS, CORS). */
export const NETWORK_ERROR_MESSAGE =
  "We can't reach Stitch right now. Check your connection and try again.";

const EMPTY_RESPONSE_MESSAGE = "The server sent an empty response.";
const UNREADABLE_RESPONSE_MESSAGE = "The server sent a response we couldn't read.";

const STATUS_MESSAGES: Record<number, string> = {
  400: "Something in that form isn't quite right.",
  401: "Please log in to continue.",
  403: "You don't have permission to do that.",
  404: "We couldn't find that.",
  409: "That conflicts with something that already exists.",
  410: "That link is no longer available.",
  413: "That file is too large.",
  422: "Please check the highlighted fields.",
  429: "That's a lot of requests. Wait a moment and try again.",
};

function defaultMessage(status: number): string {
  if (status === 0) return NETWORK_ERROR_MESSAGE;
  const known = STATUS_MESSAGES[status];
  if (known) return known;
  if (status >= 500) return "Something went wrong on our side. Please try again.";
  return "Something went wrong. Please try again.";
}

/**
 * A failed API call. `status` is the HTTP status, or 0 when the request never
 * reached the server (or the connection dropped). `detail` is a sentence safe
 * to show to the user. `fieldErrors` maps a request field name as sent (for
 * example `password` or `participantIds`) to its message, from FastAPI 422
 * responses.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly fieldErrors: Record<string, string>;

  constructor(status: number, detail?: string, fieldErrors?: Record<string, string>) {
    const message = detail && detail.trim() ? detail.trim() : defaultMessage(status);
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = message;
    this.fieldErrors = fieldErrors ?? {};
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Pydantic prefixes custom validator messages with "Value error, ". */
function cleanValidationMessage(msg: string): string {
  return msg.replace(/^(Value|Assertion) error,\s*/i, "").trim();
}

/** The field a FastAPI `loc` points at: its last segment that is not a list index. */
function fieldFromLoc(loc: unknown): string | undefined {
  if (!Array.isArray(loc)) return undefined;
  for (let i = loc.length - 1; i >= 0; i--) {
    const part = loc[i];
    if (typeof part === "number") continue;
    if (typeof part === "string" && /^\d+$/.test(part)) continue;
    if (typeof part !== "string" || !part) return undefined;
    // ["body"] alone means the whole body, not a field.
    if (part === "body" && i === 0) return undefined;
    return part;
  }
  return undefined;
}

/** Build an ApiError from a parsed error body (`{detail: string}` or a FastAPI 422 list). */
export function apiErrorFromBody(status: number, body: unknown): ApiError {
  if (isRecord(body)) {
    const detail = body.detail;
    if (typeof detail === "string") {
      return new ApiError(status, (body as unknown as ErrorResponse).detail);
    }
    if (Array.isArray(detail)) {
      const fieldErrors: Record<string, string> = {};
      const loose: string[] = [];
      for (const item of detail) {
        if (!isRecord(item)) continue;
        const msg = typeof item.msg === "string" ? cleanValidationMessage(item.msg) : "";
        if (!msg) continue;
        const field = fieldFromLoc(item.loc);
        if (field) {
          if (!(field in fieldErrors)) fieldErrors[field] = msg;
        } else {
          loose.push(msg);
        }
      }
      return new ApiError(status, loose.length ? loose.join(" ") : undefined, fieldErrors);
    }
  }
  return new ApiError(status);
}

async function apiErrorFromResponse(res: Response): Promise<ApiError> {
  let text: string;
  try {
    text = await res.text();
  } catch {
    return new ApiError(res.status);
  }
  if (!text) return new ApiError(res.status);
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return new ApiError(res.status);
  }
  return apiErrorFromBody(res.status, body);
}

function isAbortError(e: unknown): boolean {
  // DOMException in browsers and Node; a plain Error named AbortError elsewhere.
  return typeof e === "object" && e !== null && (e as { name?: unknown }).name === "AbortError";
}

// ---------------------------------------------------------------------------
// Server-sent events

export type RunEventName = keyof RunEvents;
export type RunEventMessage = {
  [K in keyof RunEvents]: { event: K; data: RunEvents[K] };
}[keyof RunEvents];

// A Record keyed by every RunEvents name, so adding an event to the contract
// is a type error here until it is listed.
const RUN_EVENT_NAMES: Record<RunEventName, true> = {
  plan: true,
  subagent_start: true,
  subagent_end: true,
  question: true,
  props_found: true,
  merge: true,
  compile_error: true,
  frames: true,
  scene_ready: true,
  done: true,
  error: true,
};

function isRunEventName(name: string): name is RunEventName {
  return Object.prototype.hasOwnProperty.call(RUN_EVENT_NAMES, name);
}

type SseEvent = { event: string; data: string; id?: string };

/**
 * Parse a text/event-stream body into events, following the WHATWG SSE
 * parsing rules: CR, LF and CRLF line endings (a CRLF split across chunks
 * counts once); multi-byte UTF-8 split across chunks; multi-line `data`
 * joined with "\n"; `:` comments; the event name defaults to "message";
 * events with empty data are dropped; a partial event at the end of the
 * stream is discarded. `id` is the last event id seen. Aborting the signal
 * ends the iteration without throwing. Breaking out of the loop early
 * cancels the body.
 */
export async function* parseSse(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  if (signal?.aborted) {
    body.cancel().catch(() => undefined);
    return;
  }
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let eventName = "";
  let dataLines: string[] = [];
  let lastId: string | undefined;
  let finished = false;

  const onAbort = () => {
    reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener("abort", onAbort, { once: true });

  const processLine = (line: string): SseEvent | null => {
    if (line === "") {
      const data = dataLines.join("\n");
      const name = eventName || "message";
      const had = dataLines.length > 0;
      eventName = "";
      dataLines = [];
      if (!had || data === "") return null;
      return lastId !== undefined ? { event: name, data, id: lastId } : { event: name, data };
    }
    if (line.charCodeAt(0) === 58 /* ":" */) return null;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.charCodeAt(0) === 32 /* " " */) value = value.slice(1);
    if (field === "event") eventName = value;
    else if (field === "data") dataLines.push(value);
    else if (field === "id") {
      if (!value.includes("\0")) lastId = value;
    }
    // `retry` and unknown fields are ignored.
    return null;
  };

  try {
    for (;;) {
      if (signal?.aborted) return;
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (e) {
        if (signal?.aborted || isAbortError(e)) return;
        throw e;
      }
      if (signal?.aborted) return;
      const done = chunk.done;
      buffer += done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });

      // Pull complete lines off the buffer.
      let start = 0;
      for (;;) {
        let end = -1;
        for (let i = start; i < buffer.length; i++) {
          const c = buffer.charCodeAt(i);
          if (c === 10 || c === 13) {
            end = i;
            break;
          }
        }
        if (end === -1) break;
        let next = end + 1;
        if (buffer.charCodeAt(end) === 13) {
          // A CR at the very end may be the first half of a CRLF.
          if (end + 1 >= buffer.length && !done) break;
          if (buffer.charCodeAt(end + 1) === 10) next = end + 2;
        }
        const ev = processLine(buffer.slice(start, end));
        start = next;
        if (ev) {
          yield ev;
          if (signal?.aborted) return;
        }
      }
      buffer = buffer.slice(start);

      // Whatever is left (a partial line or an undispatched event) is dropped.
      if (done) {
        finished = true;
        return;
      }
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    // Stopped early (break, abort or error): close the connection.
    if (!finished) reader.cancel().catch(() => undefined);
    try {
      reader.releaseLock();
    } catch {
      // Already released.
    }
  }
}

// ---------------------------------------------------------------------------
// The client

export interface StitchApi {
  // Accounts and families
  signup(body: SignupRequest): Promise<void>;
  login(body: LoginRequest): Promise<void>;
  logout(): Promise<void>;
  /** Rejects with ApiError status 401 when signed out. */
  me(): Promise<MeResponse>;
  createFamily(body: CreateFamilyRequest): Promise<CreateFamilyResponse>;
  /** The family with its members; `invites` is filled for admins only. */
  getFamily(fid: string): Promise<Family>;
  createInvite(fid: string): Promise<InviteResponse>;
  revokeInvite(fid: string, code: string): Promise<void>;
  previewInvite(code: string): Promise<InvitePreview>;
  joinInvite(code: string, body: JoinRequest): Promise<JoinResponse>;
  addPerson(fid: string, body: NewPerson): Promise<Person>;
  updatePerson(fid: string, personId: string, body: PersonPatch): Promise<Person>;
  removeMember(fid: string, userId: string): Promise<void>;

  // Family content
  getTree(fid: string): Promise<TreeResponse>;
  getBook(fid: string, personId: string): Promise<BookResponse>;
  getMemory(fid: string, memoryId: string): Promise<MemoryResponse>;
  getThreads(fid: string): Promise<ThreadsResponse>;
  /** URL for an <img>/<audio> src. Accepts ids with or without a "media/" prefix. */
  mediaUrl(fid: string, mediaId: string): string;
  getProps(fid: string): Promise<PropsResponse>;
  getSets(fid: string): Promise<SetsResponse>;
  updateProp(fid: string, propId: string, body: PropPatch): Promise<Prop>;
  /**
   * Multipart, one field named `file`: a photo for this family (for example a
   * prop's replacement photo). Resolves to the bare media id. The server
   * answers 415 for anything but JPEG, PNG, WebP, GIF or HEIC, 413 when the
   * file is too large and 400 when it is empty.
   */
  uploadMedia(fid: string, file: Blob): Promise<{ id: string }>;
  /** Multipart: the answer, the year, optional question/title/memoryId, participants and photos. */
  submitMemory(
    fid: string,
    fields: SubmitMemoryFields,
    photos?: Blob[],
  ): Promise<SubmitMemoryResponse>;
  /**
   * The run's event stream. Unknown event names and unparseable payloads are
   * skipped. Rejects with ApiError status 0 if the connection drops; aborting
   * the signal ends the iteration without throwing.
   */
  streamRun(
    fid: string,
    runId: string,
    opts?: { signal?: AbortSignal },
  ): AsyncIterable<RunEventMessage>;
  /** Answer a follow-up, confirm or edit props, or approve a merge. */
  answerRun(fid: string, runId: string, body: AnswerRequest): Promise<void>;
  getQuestions(fid: string): Promise<QuestionsResponse>;
  ask(fid: string, body: AskRequest): Promise<AskResponse>;
}

/**
 * URL-encode one path segment. Empty, "." and ".." segments are refused: the
 * URL parser would collapse them (even percent-encoded) and send the request
 * to a different route.
 */
function segment(value: string): string {
  const s = String(value);
  if (s === "" || s === "." || s === "..") throw new ApiError(404);
  return encodeURIComponent(s);
}

/** Template tag that URL-encodes every interpolated path segment. */
function path(strings: TemplateStringsArray, ...segments: string[]): string {
  let out = strings[0];
  for (let i = 0; i < segments.length; i++) out += segment(segments[i]) + strings[i + 1];
  return out;
}

/** Strip the "media/" prefix used in stored art references. */
export function bareMediaId(mediaId: string): string {
  return mediaId.replace(/^media\//, "");
}

const PHOTO_EXTENSIONS: Record<string, string> = {
  "image/jpeg": ".jpg",
  "image/png": ".png",
  "image/webp": ".webp",
  "image/gif": ".gif",
  "image/heic": ".heic",
  "image/heif": ".heif",
};

function isFile(b: Blob): b is File {
  return typeof File !== "undefined" && b instanceof File;
}

/** The multipart filename for a photo: its own name, else `<stem><ext from its type>`. */
function photoFilename(photo: Blob, stem: string): string {
  return isFile(photo) && photo.name ? photo.name : `${stem}${PHOTO_EXTENSIONS[photo.type] ?? ""}`;
}

type RequestBody = { json: unknown } | { form: FormData } | undefined;

export function createApiClient(opts?: {
  baseUrl?: string;
  fetch?: typeof fetch;
  mediaUrl?: (fid: string, mediaId: string) => string;
}): StitchApi {
  const base = (opts?.baseUrl ?? API_BASE_URL).replace(/\/+$/, "");
  // Look fetch up at call time and call it unbound (window.fetch throws
  // "Illegal invocation" when called as a method of another object).
  const doFetch = (input: string, init: RequestInit): Promise<Response> => {
    const f = opts?.fetch ?? globalThis.fetch;
    return f(input, init);
  };

  async function send(
    method: string,
    p: string,
    body?: RequestBody,
    extra?: { headers?: Record<string, string>; signal?: AbortSignal },
  ): Promise<Response> {
    const headers: Record<string, string> = { ...(extra?.headers ?? {}) };
    let payload: BodyInit | undefined;
    if (body && "json" in body) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body.json);
    } else if (body && "form" in body) {
      // No Content-Type: fetch adds multipart/form-data with its boundary.
      payload = body.form;
    }
    const init: RequestInit = { method, credentials: "include", headers };
    if (payload !== undefined) init.body = payload;
    if (extra?.signal) init.signal = extra.signal;
    let res: Response;
    try {
      res = await doFetch(base + p, init);
    } catch (e) {
      if (isAbortError(e)) throw e;
      throw new ApiError(0, NETWORK_ERROR_MESSAGE);
    }
    if (!res.ok) throw await apiErrorFromResponse(res);
    return res;
  }

  /** A route that answers with a JSON body. An empty or unreadable body is an error. */
  async function request<T>(method: string, p: string, body?: RequestBody): Promise<T> {
    const res = await send(method, p, body);
    let text: string;
    try {
      text = await res.text();
    } catch (e) {
      if (isAbortError(e)) throw e;
      throw new ApiError(0, NETWORK_ERROR_MESSAGE);
    }
    if (!text.trim()) throw new ApiError(res.status, EMPTY_RESPONSE_MESSAGE);
    try {
      return JSON.parse(text) as T;
    } catch {
      throw new ApiError(res.status, UNREADABLE_RESPONSE_MESSAGE);
    }
  }

  /** A route whose response body is ignored: 200 with any body, or 204. */
  async function requestVoid(method: string, p: string, body?: RequestBody): Promise<void> {
    const res = await send(method, p, body);
    // Release the connection without reading a body nobody needs.
    if (res.body) res.body.cancel().catch(() => undefined);
  }

  async function* streamRun(
    fid: string,
    runId: string,
    o?: { signal?: AbortSignal },
  ): AsyncGenerator<RunEventMessage> {
    const signal = o?.signal;
    const p = path`/families/${fid}/runs/${runId}/stream`;
    if (signal?.aborted) return;
    let res: Response;
    try {
      res = await send("GET", p, undefined, {
        headers: { Accept: "text/event-stream" },
        signal,
      });
    } catch (e) {
      if (signal?.aborted || isAbortError(e)) return;
      throw e;
    }
    if (!res.body) throw new ApiError(0, NETWORK_ERROR_MESSAGE);
    const events = parseSse(res.body, signal);
    try {
      for (;;) {
        let step: IteratorResult<SseEvent>;
        try {
          step = await events.next();
        } catch (e) {
          if (signal?.aborted || isAbortError(e)) return;
          // The connection dropped mid-stream; callers can reconnect.
          throw new ApiError(0, NETWORK_ERROR_MESSAGE);
        }
        if (step.done) return;
        const raw = step.value;
        if (!isRunEventName(raw.event)) {
          console.warn(`[stitch] skipping unknown run event "${raw.event}"`);
          continue;
        }
        let data: unknown;
        try {
          data = JSON.parse(raw.data);
        } catch {
          console.warn(`[stitch] skipping run event "${raw.event}" with unreadable data`);
          continue;
        }
        yield { event: raw.event, data } as RunEventMessage;
      }
    } finally {
      // Closes the body when the caller stops early.
      await events.return(undefined);
    }
  }

  return {
    signup: async (body) => requestVoid("POST", "/auth/signup", { json: body }),
    login: async (body) => requestVoid("POST", "/auth/login", { json: body }),
    logout: async () => requestVoid("POST", "/auth/logout"),
    me: async () => request<MeResponse>("GET", "/me"),
    createFamily: async (body) =>
      request<CreateFamilyResponse>("POST", "/families", { json: body }),
    getFamily: async (fid) => request<Family>("GET", path`/families/${fid}`),
    createInvite: async (fid) =>
      request<InviteResponse>("POST", path`/families/${fid}/invites`),
    revokeInvite: async (fid, code) =>
      requestVoid("DELETE", path`/families/${fid}/invites/${code}`),
    previewInvite: async (code) => request<InvitePreview>("GET", path`/invites/${code}`),
    joinInvite: async (code, body) =>
      request<JoinResponse>("POST", path`/invites/${code}/join`, { json: body }),
    addPerson: async (fid, body) =>
      request<Person>("POST", path`/families/${fid}/people`, { json: body }),
    updatePerson: async (fid, personId, body) =>
      request<Person>("PATCH", path`/families/${fid}/people/${personId}`, { json: body }),
    removeMember: async (fid, userId) =>
      requestVoid("DELETE", path`/families/${fid}/members/${userId}`),

    getTree: async (fid) => request<TreeResponse>("GET", path`/families/${fid}/tree`),
    getBook: async (fid, personId) =>
      request<BookResponse>("GET", path`/families/${fid}/people/${personId}/book`),
    getMemory: async (fid, memoryId) =>
      request<MemoryResponse>("GET", path`/families/${fid}/memories/${memoryId}`),
    getThreads: async (fid) =>
      request<ThreadsResponse>("GET", path`/families/${fid}/threads`),
    mediaUrl: (fid, mediaId) => {
      if (opts?.mediaUrl) return opts.mediaUrl(fid, mediaId);
      // Never throws: a bad id gives a URL that 404s, not a crashed render.
      const enc = (s: string) => encodeURIComponent(String(s));
      return `${base}/families/${enc(fid)}/media/${enc(bareMediaId(mediaId))}`;
    },
    getProps: async (fid) => request<PropsResponse>("GET", path`/families/${fid}/props`),
    getSets: async (fid) => request<SetsResponse>("GET", path`/families/${fid}/sets`),
    updateProp: async (fid, propId, body) =>
      request<Prop>("PATCH", path`/families/${fid}/props/${propId}`, { json: body }),
    uploadMedia: async (fid, file) => {
      const p = path`/families/${fid}/media`;
      const form = new FormData();
      form.append("file", file, photoFilename(file, "photo"));
      return request<{ id: string }>("POST", p, { form });
    },
    submitMemory: async (fid, fields, photos) => {
      const p = path`/families/${fid}/memories`;
      const form = new FormData();
      form.append("text", fields.text);
      form.append("year", String(fields.year));
      if (fields.question != null) form.append("question", fields.question);
      if (fields.title != null) form.append("title", fields.title);
      if (fields.memoryId != null) form.append("memoryId", fields.memoryId);
      for (const id of fields.participantIds ?? []) form.append("participantIds", id);
      (photos ?? []).forEach((photo, i) => {
        form.append("photos", photo, photoFilename(photo, `photo-${i + 1}`));
      });
      return request<SubmitMemoryResponse>("POST", p, { form });
    },
    streamRun: (fid, runId, o) => streamRun(fid, runId, o),
    answerRun: async (fid, runId, body) =>
      requestVoid("POST", path`/families/${fid}/runs/${runId}/answer`, { json: body }),
    getQuestions: async (fid) =>
      request<QuestionsResponse>("GET", path`/families/${fid}/questions`),
    ask: async (fid, body) =>
      request<AskResponse>("POST", path`/families/${fid}/ask`, { json: body }),
  };
}

/** The client pages use. */
export const api: StitchApi = createApiClient();
