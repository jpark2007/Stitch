// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in authPages.ts. authPages.ts imports
// "./session" without an extension (as the Next build wants), so this file
// first adds a resolve hook that maps extensionless relative imports made from
// .ts files to the .ts file next to them, then loads it dynamically.
import { describe, test } from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";

registerHooks({
  resolve(specifier, context, nextResolve) {
    const fromTs = typeof context.parentURL === "string" && context.parentURL.endsWith(".ts");
    if (fromTs && /^\.\.?\//.test(specifier) && !/\.[cm]?[jt]sx?$/.test(specifier)) {
      return nextResolve(`${specifier}.ts`, context);
    }
    return nextResolve(specifier, context);
  },
});

const {
  SIGNUP_HANDOFF_KEY,
  SIGNUP_READY_NOTICE,
  afterSignupFailure,
  authLede,
  isJoinPath,
  readSignupHandoff,
} = await import("../authPages.ts");

const LOGIN_JOIN = "Log in to join your family.";
const SIGNUP_JOIN = "Make an account to join your family.";

describe("authLede", () => {
  test("login and signup each have a warm default that is not about joining", () => {
    const login = authLede("login", null);
    const signup = authLede("signup", null);
    assert.ok(login.length > 0);
    assert.ok(signup.length > 0);
    assert.notEqual(login, signup);
    assert.notEqual(login, LOGIN_JOIN);
    assert.notEqual(signup, SIGNUP_JOIN);
    assert.doesNotMatch(login, /join your family/i);
    assert.doesNotMatch(signup, /join your family/i);
  });

  test("a join next gives the joining lede", () => {
    assert.equal(authLede("login", "/join/abc"), LOGIN_JOIN);
    assert.equal(authLede("signup", "/join/abc"), SIGNUP_JOIN);
    assert.equal(authLede("login", "/join/riveras-demo?from=email"), LOGIN_JOIN);
    assert.equal(authLede("signup", "/join/abc/"), SIGNUP_JOIN);
  });

  test("any other safe next keeps the default", () => {
    for (const next of ["/", "/families/new", "/add?family=f1", "/families/f1/settings"]) {
      assert.equal(authLede("login", next), authLede("login", null), next);
      assert.equal(authLede("signup", next), authLede("signup", null), next);
    }
  });

  test("missing or empty next keeps the default", () => {
    assert.equal(authLede("login", ""), authLede("login", null));
    assert.equal(authLede("signup", ""), authLede("signup", null));
  });

  test("an unsafe next falls back to the default", () => {
    for (const next of [
      "//evil.example",
      "//evil.example/join/abc",
      "https://evil.example/join/abc",
      "/\\evil.example/join/abc",
      "/login",
      "/login?next=/join/abc",
      "/signup",
      "join/abc",
      "/.//evil.example/join/abc",
      "/%2e//evil.example/join/abc",
    ]) {
      assert.equal(authLede("login", next), authLede("login", null), next);
      assert.equal(authLede("signup", next), authLede("signup", null), next);
    }
  });
});

describe("isJoinPath", () => {
  test("true for an invite page with a code", () => {
    for (const next of [
      "/join/abc",
      "/join/abc/",
      "/join/riveras-demo",
      "/join/abc?x=1",
      "/join/abc#top",
      "/join/a%20b",
    ]) {
      assert.equal(isJoinPath(next), true, next);
    }
  });

  test("false without a code, or for a longer path under it", () => {
    for (const next of ["/join", "/join/", "/join//abc", "/join?code=abc", "/join/abc/extra"]) {
      assert.equal(isJoinPath(next), false, next);
    }
  });

  test("false for paths that only look like join", () => {
    for (const next of ["/joined/abc", "/families/join/abc", "/JOIN-us", "/add?next=/join/abc"]) {
      assert.equal(isJoinPath(next), false, next);
    }
  });

  test("false for unsafe or missing values", () => {
    for (const next of [
      null,
      undefined,
      "",
      "//evil.example/join/abc",
      "https://evil.example/join/abc",
      "/join/abc\n",
      "join/abc",
    ]) {
      assert.equal(isJoinPath(next), false, String(next));
    }
  });
});

// ---- sign up handoff -------------------------------------------------------

/** A Map-backed stand-in for sessionStorage. */
function memoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => void map.set(k, String(v)),
    removeItem: (k) => void map.delete(k),
  };
}

const boom = () => {
  throw new Error("SecurityError: storage is blocked");
};

describe("readSignupHandoff", () => {
  test("gives the stored email once, then null", () => {
    const storage = memoryStorage({ [SIGNUP_HANDOFF_KEY]: "rosa@maos.family" });
    assert.equal(readSignupHandoff(storage), "rosa@maos.family");
    assert.equal(storage.map.has(SIGNUP_HANDOFF_KEY), false, "removed after reading");
    assert.equal(readSignupHandoff(storage), null);
  });

  test("null for empty storage or a blank value", () => {
    assert.equal(readSignupHandoff(memoryStorage()), null);
    assert.equal(readSignupHandoff(memoryStorage({ [SIGNUP_HANDOFF_KEY]: "   " })), null);
    assert.equal(readSignupHandoff(memoryStorage({ [SIGNUP_HANDOFF_KEY]: "" })), null);
  });

  test("null, without throwing, when storage is null", () => {
    assert.equal(readSignupHandoff(null), null);
  });

  test("null, without throwing, when getItem throws", () => {
    const storage = { ...memoryStorage({ [SIGNUP_HANDOFF_KEY]: "a@b.c" }), getItem: boom };
    assert.equal(readSignupHandoff(storage), null);
  });

  test("null, without throwing, when removeItem throws (never given out twice)", () => {
    const storage = { ...memoryStorage({ [SIGNUP_HANDOFF_KEY]: "a@b.c" }), removeItem: boom };
    assert.equal(readSignupHandoff(storage), null);
    assert.equal(readSignupHandoff(storage), null);
  });

  test("null when every method throws", () => {
    assert.equal(readSignupHandoff({ getItem: boom, setItem: boom, removeItem: boom }), null);
  });

  test("ignores other keys", () => {
    const storage = memoryStorage({ "stitch.familyId": "f1" });
    assert.equal(readSignupHandoff(storage), null);
    assert.equal(storage.map.get("stitch.familyId"), "f1");
  });

  test("with no storage given and none in this runtime, null", () => {
    // node has no sessionStorage global unless --experimental-webstorage.
    if (typeof globalThis.sessionStorage === "undefined") {
      assert.equal(readSignupHandoff(), null);
    }
  });
});

describe("afterSignupFailure", () => {
  test("sends to log in and leaves the email for it", () => {
    const storage = memoryStorage();
    assert.equal(afterSignupFailure("rosa@maos.family", null, storage), "/login");
    assert.equal(readSignupHandoff(storage), "rosa@maos.family");
  });

  test("keeps a safe next and drops an unsafe one", () => {
    const storage = memoryStorage();
    assert.equal(afterSignupFailure("a@b.c", "/join/abc", storage), "/login?next=%2Fjoin%2Fabc");
    assert.equal(afterSignupFailure("a@b.c", "//evil.example", storage), "/login");
    assert.equal(afterSignupFailure("a@b.c", "/signup", storage), "/login");
  });

  test("drops protocol-relative, absolute and dot-segment escapes", () => {
    for (const next of [
      "//evil.example/x",
      "///evil.example",
      "https://evil.example/",
      "http://localhost:3000/join/abc",
      "javascript:alert(1)",
      "/\\evil.example",
      "/.//evil.example/x",
      "/%2e//evil.example",
      "/x/..//evil.example",
    ]) {
      assert.equal(afterSignupFailure("a@b.c", next, memoryStorage()), "/login", next);
    }
  });

  test("the email never appears in the URL", () => {
    const email = "rosa+family@maos.family";
    for (const next of [null, "/", "/join/abc", "/families/new"]) {
      const href = afterSignupFailure(email, next, memoryStorage());
      assert.doesNotMatch(href, /rosa|maos|%40|@/, href);
    }
  });

  test("trims the email; a blank one is not stored", () => {
    const storage = memoryStorage();
    afterSignupFailure("  a@b.c ", null, storage);
    assert.equal(storage.map.get(SIGNUP_HANDOFF_KEY), "a@b.c");
    const empty = memoryStorage();
    afterSignupFailure("   ", null, empty);
    assert.equal(empty.map.size, 0);
  });

  test("still returns the log in page when storage throws or is missing", () => {
    assert.equal(afterSignupFailure("a@b.c", null, { getItem: boom, setItem: boom, removeItem: boom }), "/login");
    assert.equal(afterSignupFailure("a@b.c", "/join/x", null), "/login?next=%2Fjoin%2Fx");
  });

  test("the notice is the one calm line", () => {
    assert.equal(SIGNUP_READY_NOTICE, "Your account is ready. Log in to carry on.");
  });
});
