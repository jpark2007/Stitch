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

const { authLede, isJoinPath } = await import("../authPages.ts");

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
