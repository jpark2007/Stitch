// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// (Node 22+ treats each argument as a file or glob and does not walk a directory.)
// Node strips the erasable TypeScript in lib/api.ts; no build step needed.
// Every test injects a fake fetch: nothing touches the network.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  API_BASE_URL,
  ApiError,
  NETWORK_ERROR_MESSAGE,
  apiErrorFromBody,
  bareMediaId,
  createApiClient,
  isApiError,
  isMockMode,
  parseSse,
  api,
} from "../../../lib/api.ts";
import * as apiModule from "../../../lib/api.ts";

const BASE = "http://api.test";

// ---------------------------------------------------------------------------
// Helpers

/** A fake fetch that records every call and answers with `respond(url, init)`. */
function recordingFetch(respond = () => jsonResponse({})) {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, init });
    return respond(url, init);
  };
  return { fetch, calls };
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** A ReadableStream that yields the given chunks (strings or bytes) and closes. */
function streamOf(chunks, { error } = {}) {
  const enc = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i < chunks.length) {
        const c = chunks[i++];
        controller.enqueue(typeof c === "string" ? enc.encode(c) : c);
      } else if (error) {
        controller.error(error);
      } else {
        controller.close();
      }
    },
  });
}

async function collect(iterable) {
  const out = [];
  for await (const x of iterable) out.push(x);
  return out;
}

async function rejectsWith(promise, check) {
  try {
    await promise;
  } catch (e) {
    check(e);
    return;
  }
  assert.fail("expected a rejection");
}

/** Captures console.warn calls during `fn`. */
async function withWarnings(fn) {
  const warnings = [];
  const original = console.warn;
  console.warn = (...args) => warnings.push(args.join(" "));
  try {
    const result = await fn();
    return { result, warnings };
  } finally {
    console.warn = original;
  }
}

function headersOf(init) {
  return new Headers(init.headers ?? {});
}

// ---------------------------------------------------------------------------
// Module surface

describe("module surface", () => {
  test("exports exactly the documented runtime names", () => {
    assert.deepEqual(Object.keys(apiModule).sort(), [
      "API_BASE_URL",
      "ApiError",
      "NETWORK_ERROR_MESSAGE",
      "api",
      "apiErrorFromBody",
      "bareMediaId",
      "createApiClient",
      "isApiError",
      "isMockMode",
      "parseSse",
    ]);
  });

  test("the client has exactly the StitchApi members", () => {
    const client = createApiClient({ baseUrl: BASE, fetch: recordingFetch().fetch });
    assert.deepEqual(Object.keys(client).sort(), [
      "addPerson",
      "answerRun",
      "ask",
      "createFamily",
      "createInvite",
      "getBook",
      "getFamily",
      "getMemory",
      "getProps",
      "getQuestions",
      "getSets",
      "getThreads",
      "getTree",
      "joinInvite",
      "login",
      "logout",
      "me",
      "mediaUrl",
      "previewInvite",
      "removeMember",
      "revokeInvite",
      "signup",
      "streamRun",
      "submitMemory",
      "updatePerson",
      "updateProp",
      "uploadMedia",
    ]);
    assert.deepEqual(Object.keys(api).sort(), Object.keys(client).sort());
  });

  test("API_BASE_URL defaults to localhost:8000 with no trailing slash", () => {
    if (process.env.NEXT_PUBLIC_API_URL) {
      assert.equal(API_BASE_URL, process.env.NEXT_PUBLIC_API_URL.replace(/\/+$/, ""));
    } else {
      assert.equal(API_BASE_URL, "http://localhost:8000");
    }
    assert.ok(!API_BASE_URL.endsWith("/"));
  });

  test("isMockMode reads NEXT_PUBLIC_API_MOCK at call time", () => {
    const before = process.env.NEXT_PUBLIC_API_MOCK;
    try {
      process.env.NEXT_PUBLIC_API_MOCK = "1";
      assert.equal(isMockMode(), true);
      process.env.NEXT_PUBLIC_API_MOCK = "0";
      assert.equal(isMockMode(), false);
      delete process.env.NEXT_PUBLIC_API_MOCK;
      assert.equal(isMockMode(), false);
    } finally {
      if (before === undefined) delete process.env.NEXT_PUBLIC_API_MOCK;
      else process.env.NEXT_PUBLIC_API_MOCK = before;
    }
  });
});

// ---------------------------------------------------------------------------
// Routes

const F = "fam 1";
const FE = "fam%201";

const signup = { email: "a@b.c", password: "secret12", name: "Ana" };
const login = { email: "a@b.c", password: "secret12" };
const newPerson = { name: "Rosa", born: 1931, parentIds: [], spouseIds: [] };
const answer = { kind: "answer", text: "It was blue." };

/** [method name, args, HTTP method, path, JSON body (undefined = none), response] */
const ROUTES = [
  ["signup", [signup], "POST", "/auth/signup", signup, null],
  ["login", [login], "POST", "/auth/login", login, null],
  ["logout", [], "POST", "/auth/logout", undefined, null],
  ["me", [], "GET", "/me", undefined, { user: { id: "u1" }, families: [] }],
  ["createFamily", [{ name: "The Maos", person: newPerson }], "POST", "/families",
    { name: "The Maos", person: newPerson }, { family: { id: "f1" }, person: { id: "p1" } }],
  ["getFamily", [F], "GET", `/families/${FE}`, undefined,
    { id: "fam 1", name: "The Maos", members: [{ userId: "u1", role: "admin" }], invites: [] }],
  ["createInvite", [F], "POST", `/families/${FE}/invites`, undefined, { code: "abc", url: "/join/abc" }],
  ["revokeInvite", [F, "abc"], "DELETE", `/families/${FE}/invites/abc`, undefined, null],
  ["previewInvite", ["abc"], "GET", "/invites/abc", undefined,
    { familyId: "f1", familyName: "The Maos", unclaimedPeople: [] }],
  ["joinInvite", ["abc", { personId: "p2" }], "POST", "/invites/abc/join", { personId: "p2" },
    { family: { id: "f1" }, person: { id: "p2" } }],
  ["addPerson", [F, newPerson], "POST", `/families/${FE}/people`, newPerson, { id: "p3" }],
  ["updatePerson", [F, "p3", { name: "Rosa M." }], "PATCH", `/families/${FE}/people/p3`,
    { name: "Rosa M." }, { id: "p3" }],
  ["removeMember", [F, "u9"], "DELETE", `/families/${FE}/members/u9`, undefined, null],
  ["getTree", [F], "GET", `/families/${FE}/tree`, undefined, { people: [] }],
  ["getBook", [F, "p1"], "GET", `/families/${FE}/people/p1/book`, undefined, { memories: [] }],
  ["getMemory", [F, "m1"], "GET", `/families/${FE}/memories/m1`, undefined, { memory: { id: "m1" } }],
  ["getThreads", [F], "GET", `/families/${FE}/threads`, undefined, { moments: [] }],
  ["getProps", [F], "GET", `/families/${FE}/props`, undefined, { props: [] }],
  ["getSets", [F], "GET", `/families/${FE}/sets`, undefined, { sets: [] }],
  ["updateProp", [F, "pr1", { name: "Hymn book" }], "PATCH", `/families/${FE}/props/pr1`,
    { name: "Hymn book" }, { id: "pr1" }],
  ["submitMemory", [F, { text: "We sang.", year: 1962 }], "POST", `/families/${FE}/memories`,
    undefined, { runId: "r1", memoryId: null }],
  ["uploadMedia", [F, new Blob(["png"], { type: "image/png" })], "POST", `/families/${FE}/media`,
    undefined, { id: "abc123" }],
  ["answerRun", [F, "r1", answer], "POST", `/families/${FE}/runs/r1/answer`, answer, null],
  ["getQuestions", [F], "GET", `/families/${FE}/questions`, undefined, { questions: [] }],
  ["ask", [F, { query: "who sang?" }], "POST", `/families/${FE}/ask`, { query: "who sang?" },
    { answer: "Rosa", quotes: [] }],
];

describe("routes", () => {
  test("the table covers every request method on the client", () => {
    const client = createApiClient({ baseUrl: BASE, fetch: recordingFetch().fetch });
    const covered = new Set([...ROUTES.map((r) => r[0]), "streamRun", "mediaUrl"]);
    assert.deepEqual([...covered].sort(), Object.keys(client).sort());
  });

  for (const [name, args, method, path, json, response] of ROUTES) {
    test(`${name} -> ${method} ${path}`, async () => {
      const { fetch, calls } = recordingFetch(() =>
        response === null ? new Response(null, { status: 204 }) : jsonResponse(response),
      );
      const client = createApiClient({ baseUrl: BASE, fetch });
      const result = await client[name](...args);

      assert.equal(calls.length, 1);
      const { url, init } = calls[0];
      assert.equal(url, BASE + path);
      assert.equal(init.method, method);
      assert.equal(init.credentials, "include");
      const headers = headersOf(init);
      if (json !== undefined) {
        assert.equal(headers.get("content-type"), "application/json");
        assert.deepEqual(JSON.parse(init.body), json);
      } else {
        assert.equal(headers.get("content-type"), null);
        if (name !== "submitMemory" && name !== "uploadMedia") assert.equal(init.body, undefined);
      }
      if (response === null) assert.equal(result, undefined);
      else assert.deepEqual(result, response);
    });
  }

  test("void routes accept 200 with a body as well as 204", async () => {
    const { fetch } = recordingFetch(() => jsonResponse({ ok: true }));
    const client = createApiClient({ baseUrl: BASE, fetch });
    assert.equal(await client.logout(), undefined);
    assert.equal(await client.signup(signup), undefined);
    assert.equal(await client.login(login), undefined);
    assert.equal(await client.revokeInvite("f", "c"), undefined);
    assert.equal(await client.removeMember("f", "u"), undefined);
    assert.equal(await client.answerRun("f", "r", answer), undefined);
    const plain = recordingFetch(() => new Response("ok", { status: 200 }));
    await createApiClient({ baseUrl: BASE, fetch: plain.fetch }).logout();
  });

  test("path segments with '/', '?', '#' and spaces are encoded", async () => {
    const { fetch, calls } = recordingFetch(() => jsonResponse({ id: "x" }));
    const client = createApiClient({ baseUrl: BASE, fetch });
    await client.getBook("a/b", "c?d e#f");
    await client.previewInvite("x/../y");
    await client.revokeInvite("f&g", "h%i");
    assert.deepEqual(
      calls.map((c) => c.url),
      [
        `${BASE}/families/a%2Fb/people/c%3Fd%20e%23f/book`,
        `${BASE}/invites/x%2F..%2Fy`,
        `${BASE}/families/f%26g/invites/h%25i`,
      ],
    );
  });

  test("empty, '.' and '..' segments are refused without a request", async () => {
    const { fetch, calls } = recordingFetch();
    const client = createApiClient({ baseUrl: BASE, fetch });
    for (const bad of ["", ".", ".."]) {
      await rejectsWith(client.previewInvite(bad), (e) => {
        assert.ok(isApiError(e));
        assert.equal(e.status, 404);
      });
      await rejectsWith(collect(client.streamRun(bad, "r")), (e) => assert.equal(e.status, 404));
    }
    assert.equal(calls.length, 0);
  });

  test("base URL trailing slashes are stripped", async () => {
    const { fetch, calls } = recordingFetch(() => jsonResponse({ user: {}, families: [] }));
    await createApiClient({ baseUrl: "http://x.test///", fetch }).me();
    assert.equal(calls[0].url, "http://x.test/me");
  });

  test("fetch is looked up at call time and called unbound", async () => {
    const original = globalThis.fetch;
    const seen = [];
    // Created before the global is swapped: the swap must still be seen.
    const client = createApiClient({ baseUrl: BASE });
    globalThis.fetch = function (url) {
      seen.push({ url, self: this });
      return Promise.resolve(jsonResponse({ user: {}, families: [] }));
    };
    try {
      await client.me();
      assert.equal(seen.length, 1);
      assert.equal(seen[0].self, undefined);
    } finally {
      globalThis.fetch = original;
    }
  });
});

// ---------------------------------------------------------------------------
// submitMemory

describe("submitMemory", () => {
  test("builds multipart form data with repeated participants and named photos", async () => {
    const { fetch, calls } = recordingFetch(() => jsonResponse({ runId: "r1", memoryId: "m1" }));
    const client = createApiClient({ baseUrl: BASE, fetch });
    const file = new File(["jpeg bytes"], "kitchen.jpg", { type: "image/jpeg" });
    const blob = new Blob(["png bytes"], { type: "image/png" });
    const res = await client.submitMemory(
      "f1",
      {
        text: "We sang from the blue hymn books.",
        year: 1962,
        question: "What did Sundays sound like?",
        title: "Sunday hymns",
        memoryId: "m0",
        participantIds: ["p1", "p2", "p3"],
      },
      [file, blob],
    );
    assert.deepEqual(res, { runId: "r1", memoryId: "m1" });

    const { init } = calls[0];
    assert.ok(init.body instanceof FormData);
    const headers = headersOf(init);
    assert.equal(headers.get("content-type"), null, "fetch must set the multipart boundary");
    assert.equal(init.credentials, "include");

    const form = init.body;
    assert.equal(form.get("text"), "We sang from the blue hymn books.");
    assert.equal(form.get("year"), "1962");
    assert.equal(typeof form.get("year"), "string");
    assert.equal(form.get("question"), "What did Sundays sound like?");
    assert.equal(form.get("title"), "Sunday hymns");
    assert.equal(form.get("memoryId"), "m0");
    assert.deepEqual(form.getAll("participantIds"), ["p1", "p2", "p3"]);
    const photos = form.getAll("photos");
    assert.equal(photos.length, 2);
    assert.equal(photos[0].name, "kitchen.jpg");
    assert.equal(await photos[0].text(), "jpeg bytes");
    assert.equal(photos[1].name, "photo-2.png");
    assert.equal(await photos[1].text(), "png bytes");
    assert.deepEqual(
      [...new Set([...form.keys()])].sort(),
      ["memoryId", "participantIds", "photos", "question", "text", "title", "year"],
    );
  });

  test("omits null and missing optional fields", async () => {
    const { fetch, calls } = recordingFetch(() => jsonResponse({ runId: "r2" }));
    const client = createApiClient({ baseUrl: BASE, fetch });
    await client.submitMemory("f1", {
      text: "Just the answer.",
      year: 1990,
      question: null,
      title: null,
      memoryId: null,
    });
    const form = calls[0].init.body;
    assert.deepEqual([...form.keys()].sort(), ["text", "year"]);
    assert.equal(headersOf(calls[0].init).get("content-type"), null);
  });
});

// ---------------------------------------------------------------------------
// getFamily and uploadMedia

describe("getFamily", () => {
  test("encodes the family id and sends cookies", async () => {
    const family = { id: "a/b", name: "The Maos", members: [], invites: [] };
    const { fetch, calls } = recordingFetch(() => jsonResponse(family));
    const client = createApiClient({ baseUrl: BASE, fetch });
    assert.deepEqual(await client.getFamily("a/b"), family);
    assert.equal(calls[0].url, `${BASE}/families/a%2Fb`);
    assert.equal(calls[0].init.method, "GET");
    assert.equal(calls[0].init.credentials, "include");
  });

  test("a non-member's 404 is an ApiError with the server detail", async () => {
    const { fetch } = recordingFetch(() => jsonResponse({ detail: "We couldn't find that family." }, 404));
    await rejectsWith(createApiClient({ baseUrl: BASE, fetch }).getFamily("f"), (e) => {
      assert.ok(isApiError(e));
      assert.equal(e.status, 404);
      assert.equal(e.detail, "We couldn't find that family.");
    });
  });

  test("empty and '..' family ids are refused without a request", async () => {
    const { fetch, calls } = recordingFetch();
    const client = createApiClient({ baseUrl: BASE, fetch });
    for (const bad of ["", ".."]) {
      await rejectsWith(client.getFamily(bad), (e) => assert.equal(e.status, 404));
    }
    assert.equal(calls.length, 0);
  });
});

describe("uploadMedia", () => {
  test("posts one multipart field named 'file' with the File's own name", async () => {
    const { fetch, calls } = recordingFetch(() => jsonResponse({ id: "m1" }, 201));
    const client = createApiClient({ baseUrl: BASE, fetch });
    const file = new File(["jpeg bytes"], "hymn book.jpg", { type: "image/jpeg" });
    assert.deepEqual(await client.uploadMedia("fam 1", file), { id: "m1" });

    const { url, init } = calls[0];
    assert.equal(url, `${BASE}/families/fam%201/media`);
    assert.equal(init.method, "POST");
    assert.equal(init.credentials, "include");
    assert.equal(headersOf(init).get("content-type"), null, "fetch must set the multipart boundary");
    assert.ok(init.body instanceof FormData);
    assert.deepEqual([...init.body.keys()], ["file"]);
    const sent = init.body.get("file");
    assert.equal(sent.name, "hymn book.jpg");
    assert.equal(sent.type, "image/jpeg");
    assert.equal(await sent.text(), "jpeg bytes");
  });

  test("a bare Blob is named photo<ext from its type>", async () => {
    const { fetch, calls } = recordingFetch(() => jsonResponse({ id: "m2" }, 201));
    const client = createApiClient({ baseUrl: BASE, fetch });
    await client.uploadMedia("f", new Blob(["png"], { type: "image/png" }));
    await client.uploadMedia("f", new Blob(["heic"], { type: "image/heic" }));
    await client.uploadMedia("f", new Blob(["?"], { type: "application/octet-stream" }));
    assert.deepEqual(
      calls.map((c) => c.init.body.get("file").name),
      ["photo.png", "photo.heic", "photo"],
    );
  });

  test("413 and 415 become ApiErrors carrying the server detail", async () => {
    for (const [status, detail] of [
      [413, "Photos must be 20 MB or smaller"],
      [415, "Upload a JPEG, PNG, WebP, GIF or HEIC image"],
      [400, "The file is empty"],
    ]) {
      const { fetch } = recordingFetch(() => jsonResponse({ detail }, status));
      const client = createApiClient({ baseUrl: BASE, fetch });
      await rejectsWith(client.uploadMedia("f", new Blob(["x"], { type: "image/png" })), (e) => {
        assert.ok(isApiError(e));
        assert.equal(e.status, status);
        assert.equal(e.detail, detail);
      });
    }
  });

  test("a 413 without a body falls back to the friendly size message", async () => {
    const { fetch } = recordingFetch(() => new Response(null, { status: 413 }));
    await rejectsWith(
      createApiClient({ baseUrl: BASE, fetch }).uploadMedia("f", new Blob(["x"], { type: "image/png" })),
      (e) => {
        assert.equal(e.status, 413);
        assert.equal(e.detail, "That file is too large.");
      },
    );
  });
});

// ---------------------------------------------------------------------------
// Errors

describe("errors", () => {
  test("{detail: string} becomes the user-facing detail", async () => {
    const { fetch } = recordingFetch(() => jsonResponse({ detail: "That email is already in use." }, 409));
    const client = createApiClient({ baseUrl: BASE, fetch });
    await rejectsWith(client.signup(signup), (e) => {
      assert.ok(e instanceof ApiError);
      assert.ok(e instanceof Error);
      assert.ok(isApiError(e));
      assert.equal(e.name, "ApiError");
      assert.equal(e.status, 409);
      assert.equal(e.detail, "That email is already in use.");
      assert.equal(e.message, e.detail);
      assert.deepEqual(e.fieldErrors, {});
    });
  });

  test("me() rejects with 401 when signed out", async () => {
    const { fetch } = recordingFetch(() => jsonResponse({ detail: "Not signed in." }, 401));
    await rejectsWith(createApiClient({ baseUrl: BASE, fetch }).me(), (e) => {
      assert.equal(e.status, 401);
    });
  });

  test("a FastAPI 422 list becomes fieldErrors", async () => {
    const body = {
      detail: [
        { loc: ["body", "password"], msg: "Value error, Use at least 8 characters.", type: "value_error" },
        { loc: ["body", "password"], msg: "a second password error is ignored", type: "x" },
        { loc: ["body", "participantIds", 1], msg: "Input should be a valid string", type: "string_type" },
        { loc: ["body", "person", "parentIds", 0], msg: "Assertion error, Unknown parent.", type: "x" },
        { loc: ["body"], msg: "Field required", type: "missing" },
      ],
    };
    const { fetch } = recordingFetch(() => jsonResponse(body, 422));
    await rejectsWith(createApiClient({ baseUrl: BASE, fetch }).signup(signup), (e) => {
      assert.equal(e.status, 422);
      assert.deepEqual(e.fieldErrors, {
        password: "Use at least 8 characters.",
        participantIds: "Input should be a valid string",
        parentIds: "Unknown parent.",
      });
      assert.equal(e.detail, "Field required");
    });
  });

  test("a 422 with only field errors gets the friendly default detail", () => {
    const e = apiErrorFromBody(422, { detail: [{ loc: ["body", "year"], msg: "Value error, Pick a year." }] });
    assert.equal(e.detail, "Please check the highlighted fields.");
    assert.deepEqual(e.fieldErrors, { year: "Pick a year." });
  });

  test("non-JSON and empty error bodies fall back to per-status messages", async () => {
    const cases = [
      [new Response("<html>Bad Gateway</html>", { status: 502 }), 502, "Something went wrong on our side. Please try again."],
      [new Response("Internal Server Error", { status: 500 }), 500, "Something went wrong on our side. Please try again."],
      [new Response(null, { status: 404 }), 404, "We couldn't find that."],
      [new Response("[]", { status: 403 }), 403, "You don't have permission to do that."],
      [new Response("{}", { status: 418 }), 418, "Something went wrong. Please try again."],
    ];
    for (const [response, status, detail] of cases) {
      const client = createApiClient({ baseUrl: BASE, fetch: async () => response });
      await rejectsWith(client.getTree("f"), (e) => {
        assert.equal(e.status, status);
        assert.equal(e.detail, detail);
      });
    }
  });

  test("a network failure becomes ApiError status 0", async () => {
    const client = createApiClient({
      baseUrl: BASE,
      fetch: async () => {
        throw new TypeError("Failed to fetch");
      },
    });
    await rejectsWith(client.getTree("f"), (e) => {
      assert.ok(isApiError(e));
      assert.equal(e.status, 0);
      assert.equal(e.detail, NETWORK_ERROR_MESSAGE);
    });
  });

  test("an AbortError from fetch is rethrown as is", async () => {
    const abort = new DOMException("The operation was aborted.", "AbortError");
    const client = createApiClient({
      baseUrl: BASE,
      fetch: async () => {
        throw abort;
      },
    });
    await rejectsWith(client.me(), (e) => assert.equal(e, abort));
  });

  test("an empty body on a JSON route throws instead of resolving undefined", async () => {
    for (const make of [
      () => new Response(null, { status: 204 }),
      () => new Response("", { status: 200 }),
      () => new Response("  \n", { status: 200 }),
    ]) {
      const client = createApiClient({ baseUrl: BASE, fetch: async () => make() });
      await rejectsWith(client.me(), (e) => {
        assert.ok(isApiError(e));
        assert.equal(e.detail, "The server sent an empty response.");
      });
      await rejectsWith(client.submitMemory("f", { text: "t", year: 2000 }), (e) => {
        assert.equal(e.detail, "The server sent an empty response.");
      });
    }
  });

  test("an unreadable JSON success body throws", async () => {
    const client = createApiClient({ baseUrl: BASE, fetch: async () => new Response("<html>", { status: 200 }) });
    await rejectsWith(client.getTree("f"), (e) => {
      assert.equal(e.status, 200);
      assert.equal(e.detail, "The server sent a response we couldn't read.");
    });
  });

  test("ApiError defaults its detail from the status", () => {
    assert.equal(new ApiError(0).detail, NETWORK_ERROR_MESSAGE);
    assert.equal(new ApiError(401).detail, "Please log in to continue.");
    assert.equal(new ApiError(404, "   ").detail, "We couldn't find that.");
    assert.equal(new ApiError(503).detail, "Something went wrong on our side. Please try again.");
    assert.equal(apiErrorFromBody(400, "nope").detail, "Something in that form isn't quite right.");
    assert.equal(isApiError(new Error("x")), false);
  });
});

// ---------------------------------------------------------------------------
// parseSse

describe("parseSse", () => {
  test("parses named events, ids and the default 'message' event", async () => {
    const events = await collect(
      parseSse(streamOf(["event: plan\nid: 7\ndata: {\"a\":1}\n\n", "data: hello\n\n"])),
    );
    assert.deepEqual(events, [
      { event: "plan", data: '{"a":1}', id: "7" },
      { event: "message", data: "hello", id: "7" },
    ]);
  });

  test("handles CR, LF and CRLF, including a CRLF split across chunks", async () => {
    const events = await collect(
      parseSse(streamOf(["event: a\r", "\ndata: 1\r", "\n\r", "\nevent: b\rdata: 2\r\r", "data: 3\n\n"])),
    );
    assert.deepEqual(events, [
      { event: "a", data: "1" },
      { event: "b", data: "2" },
      { event: "message", data: "3" },
    ]);
  });

  test("handles a multi-byte character split across chunks", async () => {
    const bytes = new TextEncoder().encode("data: Abuela’s kitchen 🍲\n\n");
    // Split inside the three-byte ’ and inside the four-byte emoji.
    const quote = bytes.indexOf(0xe2);
    const emoji = bytes.indexOf(0xf0);
    const events = await collect(
      parseSse(
        streamOf([
          bytes.slice(0, quote + 1),
          bytes.slice(quote + 1, emoji + 2),
          bytes.slice(emoji + 2),
        ]),
      ),
    );
    assert.deepEqual(events, [{ event: "message", data: "Abuela’s kitchen 🍲" }]);
  });

  test("joins multi-line data with \\n and keeps only one leading space", async () => {
    const events = await collect(parseSse(streamOf(["data: line one\ndata:line two\ndata:  indented\n\n"])));
    assert.deepEqual(events, [{ event: "message", data: "line one\nline two\n indented" }]);
  });

  test("ignores comments, retry and unknown fields, and drops empty-data events", async () => {
    const events = await collect(
      parseSse(
        streamOf([
          ": keep-alive\n\n",
          "retry: 3000\nfoo: bar\nevent: plan\n\n", // no data: dropped
          "event: plan\ndata:\n\n", // empty data: dropped
          ":comment\nevent: done\ndata: {}\n\n",
        ]),
      ),
    );
    assert.deepEqual(events, [{ event: "done", data: "{}" }]);
  });

  test("an event name does not leak into the next event", async () => {
    const events = await collect(parseSse(streamOf(["event: plan\ndata: 1\n\ndata: 2\n\n"])));
    assert.deepEqual(events.map((e) => e.event), ["plan", "message"]);
  });

  test("drops a trailing partial event", async () => {
    const events = await collect(parseSse(streamOf(["data: whole\n\n", "event: plan\ndata: partial\n"])));
    assert.deepEqual(events, [{ event: "message", data: "whole" }]);
    const noNewline = await collect(parseSse(streamOf(["data: whole\n\ndata: half"])));
    assert.deepEqual(noNewline, [{ event: "message", data: "whole" }]);
  });

  test("aborting mid-stream ends the iteration without throwing", async () => {
    const controller = new AbortController();
    let cancelled = false;
    let pulls = 0;
    const body = new ReadableStream({
      pull(c) {
        pulls++;
        if (pulls === 1) c.enqueue(new TextEncoder().encode("data: first\n\n"));
        // Afterwards never enqueue: the read stays pending until cancelled.
        return new Promise(() => {});
      },
      cancel() {
        cancelled = true;
      },
    });
    const seen = [];
    for await (const ev of parseSse(body, controller.signal)) {
      seen.push(ev.data);
      setTimeout(() => controller.abort(), 5);
    }
    assert.deepEqual(seen, ["first"]);
    assert.equal(cancelled, true);
  });

  test("an already-aborted signal yields nothing", async () => {
    const controller = new AbortController();
    controller.abort();
    assert.deepEqual(await collect(parseSse(streamOf(["data: x\n\n"]), controller.signal)), []);
  });

  test("breaking out early cancels the body", async () => {
    let cancelled = false;
    const body = new ReadableStream({
      start(c) {
        c.enqueue(new TextEncoder().encode("data: 1\n\ndata: 2\n\n"));
      },
      cancel() {
        cancelled = true;
      },
    });
    for await (const ev of parseSse(body)) {
      assert.equal(ev.data, "1");
      break;
    }
    assert.equal(cancelled, true);
  });

  test("a stream error propagates when not aborted", async () => {
    await rejectsWith(collect(parseSse(streamOf(["data: 1\n\n"], { error: new TypeError("reset") }))), (e) => {
      assert.equal(e.message, "reset");
    });
  });
});

// ---------------------------------------------------------------------------
// streamRun

function sseFetch(chunks, opts) {
  return recordingFetch(
    () =>
      new Response(streamOf(chunks, opts), {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
  );
}

describe("streamRun", () => {
  test("requests the stream and yields typed {event, data} messages", async () => {
    const { fetch, calls } = sseFetch([
      'event: plan\ndata: {"todos":[{"text":"Read the memory","status":"in_progress"}]}\n\n',
      ": ping\n\n",
      'event: question\ndata: {"text":"What color was the hymn book?"}\n\n',
      'event: done\ndata: {"memoryId":"m1"}\n\n',
    ]);
    const client = createApiClient({ baseUrl: BASE, fetch });
    const messages = await collect(client.streamRun("f 1", "r/1"));

    assert.equal(calls[0].url, `${BASE}/families/f%201/runs/r%2F1/stream`);
    assert.equal(calls[0].init.method, "GET");
    assert.equal(calls[0].init.credentials, "include");
    assert.equal(headersOf(calls[0].init).get("accept"), "text/event-stream");
    assert.deepEqual(messages, [
      { event: "plan", data: { todos: [{ text: "Read the memory", status: "in_progress" }] } },
      { event: "question", data: { text: "What color was the hymn book?" } },
      { event: "done", data: { memoryId: "m1" } },
    ]);
  });

  test("skips unknown events and unreadable JSON, warning once per skip", async () => {
    const { fetch } = sseFetch([
      'event: mystery\ndata: {"x":1}\n\n',
      "data: {}\n\n", // default "message" is not a run event
      "event: plan\ndata: {not json\n\n",
      'event: error\ndata: {"message":"The studio is busy."}\n\n',
    ]);
    const client = createApiClient({ baseUrl: BASE, fetch });
    const { result, warnings } = await withWarnings(() => collect(client.streamRun("f", "r")));
    assert.deepEqual(result, [{ event: "error", data: { message: "The studio is busy." } }]);
    assert.equal(warnings.length, 3);
    assert.match(warnings[0], /mystery/);
    assert.match(warnings[2], /plan/);
  });

  test("a non-2xx response throws the parsed ApiError", async () => {
    const { fetch } = recordingFetch(() => jsonResponse({ detail: "Only the teller can watch this run." }, 403));
    const client = createApiClient({ baseUrl: BASE, fetch });
    await rejectsWith(collect(client.streamRun("f", "r")), (e) => {
      assert.ok(isApiError(e));
      assert.equal(e.status, 403);
      assert.equal(e.detail, "Only the teller can watch this run.");
    });
  });

  test("throws ApiError(0) when the connection drops mid-stream", async () => {
    const { fetch } = sseFetch(['event: plan\ndata: {"todos":[]}\n\n'], { error: new TypeError("terminated") });
    const client = createApiClient({ baseUrl: BASE, fetch });
    const seen = [];
    await rejectsWith(
      (async () => {
        for await (const m of client.streamRun("f", "r")) seen.push(m.event);
      })(),
      (e) => {
        assert.ok(isApiError(e));
        assert.equal(e.status, 0);
        assert.equal(e.detail, NETWORK_ERROR_MESSAGE);
      },
    );
    assert.deepEqual(seen, ["plan"]);
  });

  test("a network failure before the stream opens throws ApiError(0)", async () => {
    const client = createApiClient({
      baseUrl: BASE,
      fetch: async () => {
        throw new TypeError("Failed to fetch");
      },
    });
    await rejectsWith(collect(client.streamRun("f", "r")), (e) => assert.equal(e.status, 0));
  });

  test("returns quietly when aborted mid-stream", async () => {
    const controller = new AbortController();
    let cancelled = false;
    let pulls = 0;
    const fetch = async (url, init) => {
      assert.equal(init.signal, controller.signal);
      const body = new ReadableStream({
        pull(c) {
          pulls++;
          if (pulls === 1) c.enqueue(new TextEncoder().encode('event: plan\ndata: {"todos":[]}\n\n'));
          return new Promise(() => {});
        },
        cancel() {
          cancelled = true;
        },
      });
      return new Response(body, { status: 200 });
    };
    const client = createApiClient({ baseUrl: BASE, fetch });
    const seen = [];
    for await (const m of client.streamRun("f", "r", { signal: controller.signal })) {
      seen.push(m.event);
      setTimeout(() => controller.abort(), 5);
    }
    assert.deepEqual(seen, ["plan"]);
    assert.equal(cancelled, true);
  });

  test("returns quietly when fetch itself is aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    const { fetch, calls } = recordingFetch();
    const client = createApiClient({ baseUrl: BASE, fetch });
    assert.deepEqual(await collect(client.streamRun("f", "r", { signal: controller.signal })), []);
    assert.equal(calls.length, 0);

    const live = new AbortController();
    const aborting = createApiClient({
      baseUrl: BASE,
      fetch: async () => {
        live.abort();
        throw new DOMException("aborted", "AbortError");
      },
    });
    assert.deepEqual(await collect(aborting.streamRun("f", "r", { signal: live.signal })), []);
  });

  test("breaking out of the loop closes the connection", async () => {
    let cancelled = false;
    const fetch = async () =>
      new Response(
        new ReadableStream({
          start(c) {
            c.enqueue(new TextEncoder().encode('event: plan\ndata: {"todos":[]}\n\n'));
          },
          cancel() {
            cancelled = true;
          },
        }),
        { status: 200 },
      );
    const client = createApiClient({ baseUrl: BASE, fetch });
    for await (const m of client.streamRun("f", "r")) {
      assert.equal(m.event, "plan");
      break;
    }
    assert.equal(cancelled, true);
  });
});

// ---------------------------------------------------------------------------
// mediaUrl

describe("mediaUrl", () => {
  test("default strips a leading 'media/' and encodes segments", () => {
    const client = createApiClient({ baseUrl: `${BASE}/`, fetch: recordingFetch().fetch });
    assert.equal(client.mediaUrl("f1", "media/abc123"), `${BASE}/families/f1/media/abc123`);
    assert.equal(client.mediaUrl("f1", "abc123"), `${BASE}/families/f1/media/abc123`);
    assert.equal(client.mediaUrl("f 1", "media/a/b"), `${BASE}/families/f%201/media/a%2Fb`);
  });

  test("bareMediaId only strips the prefix", () => {
    assert.equal(bareMediaId("media/x"), "x");
    assert.equal(bareMediaId("x"), "x");
    assert.equal(bareMediaId("social/media/x"), "social/media/x");
  });

  test("an override is used when given", () => {
    const seen = [];
    const client = createApiClient({
      baseUrl: BASE,
      fetch: recordingFetch().fetch,
      mediaUrl: (fid, id) => {
        seen.push([fid, id]);
        return `/samples/${id}.png`;
      },
    });
    assert.equal(client.mediaUrl("f1", "media/hymn"), "/samples/media/hymn.png");
    assert.deepEqual(seen, [["f1", "media/hymn"]]);
  });

  test("the default client points at API_BASE_URL", () => {
    assert.equal(api.mediaUrl("f", "m"), `${API_BASE_URL}/families/f/media/m`);
  });
});
