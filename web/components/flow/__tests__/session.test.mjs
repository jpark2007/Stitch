// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in session.ts. client.ts imports through
// the "@/" alias and extensionless paths, so a small resolve hook (registered
// below, for this test process only) maps those to the .ts files.
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";

import {
  FAMILY_KEY,
  SIGNED_IN_KEY,
  destinationAfterAuth,
  forgetFamily,
  forgetSignedIn,
  hrefs,
  recallSignedIn,
  rememberSignedIn,
  pickFamily,
  recallFamily,
  rememberFamily,
  safeNext,
} from "../session.ts";

// ---- safeNext ---------------------------------------------------------------

describe("safeNext", () => {
  test("keeps same-origin paths as given", () => {
    for (const ok of [
      "/",
      "/add?family=x",
      "/add?family=x&memoryId=m%201",
      "/families/f1/settings",
      "/join/abc#top",
      "/people/p1?next=/login",
      "/loginish",
      "/memories/login",
    ]) {
      assert.equal(safeNext(ok), ok, ok);
    }
  });

  test("refuses other origins, schemes and non-paths", () => {
    for (const bad of [
      "https://evil",
      "http://localhost:3000/add",
      "//evil",
      "//evil/add",
      "/\\evil",
      "/\\/evil",
      "\\\\evil",
      "/a\\b",
      "javascript:x",
      "javascript:alert(1)",
      "data:text/html,hi",
      "add",
      "?next=/x",
      "#x",
      " /add",
      "",
      null,
      undefined,
    ]) {
      assert.equal(safeNext(bad), null, String(bad));
    }
  });

  test("refuses control characters (browsers drop tabs and newlines)", () => {
    for (const bad of ["/\t/evil", "/\n/evil", "/add\r", "/add\u0000", "/x\u007f", "/x\u0085"]) {
      assert.equal(safeNext(bad), null, JSON.stringify(bad));
    }
  });

  test("refuses the login and signup pages themselves", () => {
    for (const bad of [
      "/login",
      "/login/",
      "/login?next=/x",
      "/login#x",
      "/signup",
      "/signup?next=%2Fadd",
      "/LOGIN",
      "/./login",
      "/add/../signup",
    ]) {
      assert.equal(safeNext(bad), null, bad);
    }
  });

  test("refuses absurdly long values", () => {
    assert.equal(safeNext(`/${"a".repeat(5000)}`), null);
  });
});

// ---- hrefs ------------------------------------------------------------------

describe("hrefs", () => {
  test("every entry is a function", () => {
    for (const [name, fn] of Object.entries(hrefs)) assert.equal(typeof fn, "function", name);
    assert.deepEqual(Object.keys(hrefs).sort(), [
      "add",
      "home",
      "join",
      "login",
      "memory",
      "newFamily",
      "person",
      "render",
      "settings",
      "signup",
    ]);
  });

  test("fixed pages", () => {
    assert.equal(hrefs.home(), "/");
    assert.equal(hrefs.newFamily(), "/families/new");
  });

  test("login and signup carry a safe next, encoded", () => {
    assert.equal(hrefs.login(), "/login");
    assert.equal(hrefs.login(null), "/login");
    assert.equal(hrefs.login("/add?family=x&memoryId=y"), "/login?next=%2Fadd%3Ffamily%3Dx%26memoryId%3Dy");
    assert.equal(hrefs.signup("/join/abc"), "/signup?next=%2Fjoin%2Fabc");
    assert.equal(hrefs.signup(), "/signup");
    // An unsafe next is dropped, never passed along.
    assert.equal(hrefs.login("https://evil"), "/login");
    assert.equal(hrefs.login("//evil"), "/login");
    assert.equal(hrefs.signup("/login?next=/x"), "/signup");
  });

  test("ids are encoded as one path segment", () => {
    assert.equal(hrefs.settings("fam 1"), "/families/fam%201/settings");
    assert.equal(hrefs.settings("a/b?c#d"), "/families/a%2Fb%3Fc%23d/settings");
    assert.equal(hrefs.join("k3J9x"), "/join/k3J9x");
    assert.equal(hrefs.join("../x"), "/join/..%2Fx");
    assert.equal(hrefs.memory("m/1"), "/memories/m%2F1");
    assert.equal(hrefs.person("p&q é"), "/people/p%26q%20%C3%A9");
    assert.equal(hrefs.render("m/1?t=4#x"), "/render/m%2F1%3Ft%3D4%23x");
  });

  test("add takes an optional family and memory", () => {
    assert.equal(hrefs.add(), "/add");
    assert.equal(hrefs.add({}), "/add");
    assert.equal(hrefs.add({ familyId: null, memoryId: null }), "/add");
    assert.equal(hrefs.add({ familyId: "f1" }), "/add?family=f1");
    assert.equal(hrefs.add({ memoryId: "m1" }), "/add?memoryId=m1");
    assert.equal(hrefs.add({ familyId: "f 1", memoryId: "m&1" }), "/add?family=f%201&memoryId=m%261");
  });

  test("memory and person take an optional family as ?family=", () => {
    assert.equal(hrefs.memory("m1"), "/memories/m1");
    assert.equal(hrefs.memory("m1", "f 1"), "/memories/m1?family=f%201");
    assert.equal(hrefs.memory("m1", null), "/memories/m1");
    assert.equal(hrefs.memory("m1", undefined), "/memories/m1");
    assert.equal(hrefs.memory("m1", ""), "/memories/m1");
    assert.equal(hrefs.memory("m/1", "a&b=c"), "/memories/m%2F1?family=a%26b%3Dc");
    assert.equal(hrefs.person("p1"), "/people/p1");
    assert.equal(hrefs.person("p1", "f 1"), "/people/p1?family=f%201");
    assert.equal(hrefs.person("p1", null), "/people/p1");
    assert.equal(hrefs.person("p1", ""), "/people/p1");
    assert.equal(hrefs.person("p&q é", "fam#1"), "/people/p%26q%20%C3%A9?family=fam%231");
    // Still usable as a one-argument callback.
    assert.deepEqual(["a", "b"].map((id) => hrefs.memory(id)), ["/memories/a", "/memories/b"]);
  });

  test("render plays a saved moment, with the same ?family= rule as memory", () => {
    assert.equal(hrefs.render("m1"), "/render/m1");
    assert.equal(hrefs.render("m1", "f 1"), "/render/m1?family=f%201");
    assert.equal(hrefs.render("m1", null), "/render/m1");
    assert.equal(hrefs.render("m1", undefined), "/render/m1");
    assert.equal(hrefs.render("m1", ""), "/render/m1");
    assert.equal(hrefs.render("m é", "a&b=c"), "/render/m%20%C3%A9?family=a%26b%3Dc");
    // The stage and the book page take the same id and family.
    for (const [id, fid] of [["m1", "f1"], ["m/1", "f 1"], ["m", ""], ["m", null]]) {
      assert.equal(
        hrefs.render(id, fid).replace(/^\/render\//, "/memories/"),
        hrefs.memory(id, fid),
        `${id} ${fid}`,
      );
    }
    assert.deepEqual(["a", "b"].map((id) => hrefs.render(id, "f")), ["/render/a?family=f", "/render/b?family=f"]);
  });

  test("the hrefs it builds pass safeNext", () => {
    for (const h of [
      hrefs.add({ familyId: "f", memoryId: "m" }),
      hrefs.settings("a/b"),
      hrefs.join("c"),
      hrefs.memory("m", "f 1"),
      hrefs.person("p", "f"),
      hrefs.render("m", "f 1"),
    ]) {
      assert.equal(safeNext(h), h);
    }
  });
});

// ---- destinationAfterAuth ---------------------------------------------------

const user = { id: "u1", email: "a@example.com", name: "Ana" };
const fam = (id, role = "member") => ({ id, name: `Family ${id}`, role, personId: `p_${id}` });

describe("destinationAfterAuth", () => {
  test("a safe next wins", () => {
    assert.equal(destinationAfterAuth({ user, families: [] }, "/join/abc"), "/join/abc");
    assert.equal(destinationAfterAuth({ user, families: [fam("f1")] }, "/add?family=f1"), "/add?family=f1");
  });

  test("without a next: start a family when there is none", () => {
    assert.equal(destinationAfterAuth({ user, families: [] }, null), "/families/new");
  });

  test("without a next: home when there is a family", () => {
    assert.equal(destinationAfterAuth({ user, families: [fam("f1")] }, null), "/");
  });

  test("an unsafe next is ignored", () => {
    assert.equal(destinationAfterAuth({ user, families: [fam("f1")] }, "https://evil"), "/");
    assert.equal(destinationAfterAuth({ user, families: [] }, "/login?next=/x"), "/families/new");
  });
});

// ---- pickFamily -------------------------------------------------------------

describe("pickFamily", () => {
  const families = [fam("f1", "admin"), fam("f2")];

  test("the preferred family when the user belongs to it", () => {
    assert.equal(pickFamily(families, "f2"), families[1]);
  });

  test("the first family when the preferred one is unknown or missing", () => {
    assert.equal(pickFamily(families, "nope"), families[0]);
    assert.equal(pickFamily(families, null), families[0]);
    assert.equal(pickFamily(families, undefined), families[0]);
    assert.equal(pickFamily(families, ""), families[0]);
  });

  test("null for no families", () => {
    assert.equal(pickFamily([], "f1"), null);
    assert.equal(pickFamily([], null), null);
  });
});

// ---- remember / recall / forget ---------------------------------------------

function fakeStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => void data.set(k, String(v)),
    removeItem: (k) => void data.delete(k),
  };
}

function throwingStorage() {
  const boom = () => {
    throw new Error("SecurityError: storage is disabled");
  };
  return { getItem: boom, setItem: boom, removeItem: boom };
}

describe("family storage", () => {
  test("remember, recall and forget round-trip under FAMILY_KEY", () => {
    assert.equal(FAMILY_KEY, "stitch.familyId");
    const s = fakeStorage();
    assert.equal(recallFamily(s), null);
    rememberFamily("f1", s);
    assert.equal(s.data.get(FAMILY_KEY), "f1");
    assert.equal(recallFamily(s), "f1");
    rememberFamily("f2", s);
    assert.equal(recallFamily(s), "f2");
    forgetFamily(s);
    assert.equal(s.data.has(FAMILY_KEY), false);
    assert.equal(recallFamily(s), null);
  });

  test("an empty stored value or empty id counts as nothing", () => {
    const s = fakeStorage({ [FAMILY_KEY]: "" });
    assert.equal(recallFamily(s), null);
    rememberFamily("", s);
    assert.equal(s.data.get(FAMILY_KEY), "");
  });

  test("storage whose methods throw never throws out", () => {
    const s = throwingStorage();
    assert.doesNotThrow(() => rememberFamily("f1", s));
    assert.equal(recallFamily(s), null);
    assert.doesNotThrow(() => forgetFamily(s));
  });

  test("null storage is a no-op", () => {
    assert.doesNotThrow(() => rememberFamily("f1", null));
    assert.equal(recallFamily(null), null);
    assert.doesNotThrow(() => forgetFamily(null));
  });

  test("defaults to globalThis.localStorage, and copes when it is missing or throws", () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
    try {
      const s = fakeStorage();
      Object.defineProperty(globalThis, "localStorage", { value: s, configurable: true, writable: true });
      rememberFamily("f9");
      assert.equal(s.data.get(FAMILY_KEY), "f9");
      assert.equal(recallFamily(), "f9");
      forgetFamily();
      assert.equal(recallFamily(), null);

      Object.defineProperty(globalThis, "localStorage", {
        configurable: true,
        get() {
          throw new Error("SecurityError: access denied");
        },
      });
      assert.doesNotThrow(() => rememberFamily("f1"));
      assert.equal(recallFamily(), null);
      assert.doesNotThrow(() => forgetFamily());

      Object.defineProperty(globalThis, "localStorage", { value: undefined, configurable: true, writable: true });
      assert.doesNotThrow(() => rememberFamily("f1"));
      assert.equal(recallFamily(), null);
    } finally {
      if (original) Object.defineProperty(globalThis, "localStorage", original);
      else delete globalThis.localStorage;
    }
  });
});

describe("signed-in hint", () => {
  test("remember, recall and forget round-trip under SIGNED_IN_KEY", () => {
    assert.equal(SIGNED_IN_KEY, "stitch.signedIn");
    const s = fakeStorage();
    assert.equal(recallSignedIn(s), false);
    rememberSignedIn(s);
    assert.equal(s.data.get(SIGNED_IN_KEY), "1");
    assert.equal(recallSignedIn(s), true);
    forgetSignedIn(s);
    assert.equal(s.data.has(SIGNED_IN_KEY), false);
    assert.equal(recallSignedIn(s), false);
  });

  test("only the exact value counts", () => {
    for (const v of ["", "0", "true", "yes"]) assert.equal(recallSignedIn(fakeStorage({ [SIGNED_IN_KEY]: v })), false, v);
  });

  test("it is separate from the remembered family", () => {
    const s = fakeStorage();
    rememberFamily("f1", s);
    rememberSignedIn(s);
    forgetSignedIn(s);
    assert.equal(recallFamily(s), "f1");
    rememberSignedIn(s);
    forgetFamily(s);
    assert.equal(recallSignedIn(s), true);
  });

  test("throwing, null and missing storage never throw and read as signed out", () => {
    for (const s of [throwingStorage(), null]) {
      assert.doesNotThrow(() => rememberSignedIn(s));
      assert.equal(recallSignedIn(s), false);
      assert.doesNotThrow(() => forgetSignedIn(s));
    }
    const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
    try {
      const s = fakeStorage();
      Object.defineProperty(globalThis, "localStorage", { value: s, configurable: true, writable: true });
      rememberSignedIn();
      assert.equal(recallSignedIn(), true);
      forgetSignedIn();
      assert.equal(recallSignedIn(), false);
      Object.defineProperty(globalThis, "localStorage", { value: undefined, configurable: true, writable: true });
      assert.doesNotThrow(() => rememberSignedIn());
      assert.equal(recallSignedIn(), false);
    } finally {
      if (original) Object.defineProperty(globalThis, "localStorage", original);
      else delete globalThis.localStorage;
    }
  });
});

// ---- client.ts --------------------------------------------------------------

// Resolve "@/x" to web/x and extensionless imports to .ts or /index.ts, as the
// bundler does, so client.ts and the mock it pulls in load under node --test.
const HOOKS = `
const exts = [".ts", ".tsx", "/index.ts"];
let root;
export function initialize(data) { root = data.root; }
export async function resolve(specifier, context, next) {
  const spec = specifier.startsWith("@/") ? new URL(specifier.slice(2), root).href : specifier;
  try {
    return await next(spec, context);
  } catch (e) {
    if (e?.code !== "ERR_MODULE_NOT_FOUND" && e?.code !== "ERR_UNSUPPORTED_DIR_IMPORT") throw e;
    if (!(spec.startsWith(".") || spec.startsWith("file:"))) throw e;
    for (const ext of exts) {
      try {
        return await next(spec + ext, context);
      } catch {}
    }
    throw e;
  }
}
`;
register(`data:text/javascript,${encodeURIComponent(HOOKS)}`, {
  parentURL: import.meta.url,
  data: { root: new URL("../../../", import.meta.url).href },
});

/** A fresh copy of client.ts (its own memo), loaded with NEXT_PUBLIC_API_MOCK set as given. */
async function loadClient(mock, tag) {
  if (mock) process.env.NEXT_PUBLIC_API_MOCK = "1";
  else delete process.env.NEXT_PUBLIC_API_MOCK;
  return import(`../client.ts?${tag}`);
}

describe("client.ts", () => {
  test("real mode: getApi is the api singleton, every time; no demo credentials", async () => {
    const saved = process.env.NEXT_PUBLIC_API_MOCK;
    try {
      const client = await loadClient(false, "real");
      const { api } = await import("../../../lib/api.ts");
      const first = client.getApi();
      assert.equal(first, api);
      assert.equal(client.getApi(), first);
      assert.equal(client.demoCredentials(), null);
    } finally {
      if (saved === undefined) delete process.env.NEXT_PUBLIC_API_MOCK;
      else process.env.NEXT_PUBLIC_API_MOCK = saved;
    }
  });

  test("mock mode: getApi is one mock-wired client per page load; demo credentials", async () => {
    const saved = process.env.NEXT_PUBLIC_API_MOCK;
    try {
      const client = await loadClient(true, "mock");
      const { api } = await import("../../../lib/api.ts");
      const first = client.getApi();
      assert.notEqual(first, api);
      assert.equal(client.getApi(), first, "memoized");
      assert.equal(typeof first.me, "function");
      // Wired to the mock backend's mediaUrl: an unknown id gets an inline placeholder.
      assert.ok(first.mediaUrl("sample_family", "media/nothing").startsWith("data:image/svg+xml"));
      assert.deepEqual(client.demoCredentials(), { email: "david@example.com", password: "stitch-demo" });
    } finally {
      if (saved === undefined) delete process.env.NEXT_PUBLIC_API_MOCK;
      else process.env.NEXT_PUBLIC_API_MOCK = saved;
    }
  });

  test("RUN_OPTIONS replays on reconnect", async () => {
    const client = await loadClient(false, "opts");
    assert.deepEqual(client.RUN_OPTIONS, { replayOnReconnect: true });
  });

  test("makeRunSource binds the family and run to streamRun and answerRun", async () => {
    const client = await loadClient(false, "source");
    const calls = [];
    const events = [{ event: "done", data: { memoryId: "m1" } }];
    const fake = {
      streamRun: (fid, runId, opts) => {
        calls.push(["streamRun", fid, runId, opts]);
        return (async function* () {
          yield* events;
        })();
      },
      answerRun: async (fid, runId, body) => {
        calls.push(["answerRun", fid, runId, body]);
      },
    };
    const source = client.makeRunSource(fake, "f1", "r1");
    const signal = new AbortController().signal;
    const seen = [];
    for await (const e of source.stream(signal)) seen.push(e);
    assert.deepEqual(seen, events);
    await source.answer({ kind: "answer", text: "At the lake." });
    assert.equal(calls[0][0], "streamRun");
    assert.deepEqual(calls[0].slice(1, 3), ["f1", "r1"]);
    assert.equal(calls[0][3].signal, signal);
    assert.deepEqual(calls[1], ["answerRun", "f1", "r1", { kind: "answer", text: "At the lake." }]);
  });
});
