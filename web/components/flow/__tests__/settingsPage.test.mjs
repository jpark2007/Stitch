// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in settingsPage.ts. settingsPage.ts
// imports "../../lib/api" without an extension (as the Next build wants), so
// this file first adds a resolve hook that maps extensionless relative imports
// made from .ts files to the .ts file next to them, then loads it dynamically.
import { test, describe } from "node:test";
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
  SETTINGS_LOAD_FALLBACK,
  addRelativeHref,
  findFamily,
  inviteAskLine,
  isWelcome,
  listOfNames,
  settingsLoadError,
  signedInLabel,
} = await import("../settingsPage.ts");
// The same module settingsPage.ts resolves to, so instanceof agrees.
const { ApiError, NETWORK_ERROR_MESSAGE } = await import("../../../lib/api.ts");

// ---- findFamily -------------------------------------------------------------

describe("findFamily", () => {
  const maos = { id: "f1", name: "The Maos", role: "admin", personId: "p1" };
  const garcias = { id: "f2", name: "The Garcias", role: "member", personId: null };

  test("finds the family by id", () => {
    assert.equal(findFamily([maos, garcias], "f2"), garcias);
    assert.equal(findFamily([maos, garcias], "f1"), maos);
  });

  test("null when the user is not in that family", () => {
    assert.equal(findFamily([maos, garcias], "f3"), null);
    assert.equal(findFamily([maos], "F1"), null, "ids are compared exactly");
  });

  test("null for an empty list or an empty id", () => {
    assert.equal(findFamily([], "f1"), null);
    assert.equal(findFamily([maos], ""), null);
  });
});

// ---- isWelcome --------------------------------------------------------------

describe("isWelcome", () => {
  test("true for welcome=1, with or without the question mark", () => {
    assert.equal(isWelcome("?welcome=1"), true);
    assert.equal(isWelcome("welcome=1"), true);
    assert.equal(isWelcome("?family=x&welcome=1"), true);
  });

  test("false for welcome=0 and other values", () => {
    assert.equal(isWelcome("?welcome=0"), false);
    assert.equal(isWelcome("?welcome=true"), false);
    assert.equal(isWelcome("?welcome="), false);
    assert.equal(isWelcome("?welcome"), false);
  });

  test("false when it is missing", () => {
    assert.equal(isWelcome(""), false);
    assert.equal(isWelcome("?"), false);
    assert.equal(isWelcome("?other=1"), false);
    assert.equal(isWelcome(null), false);
  });

  test("reads URLSearchParams", () => {
    assert.equal(isWelcome(new URLSearchParams("welcome=1")), true);
    assert.equal(isWelcome(new URLSearchParams({ welcome: "0" })), false);
    assert.equal(isWelcome(new URLSearchParams()), false);
  });
});

// ---- addRelativeHref --------------------------------------------------------

describe("addRelativeHref", () => {
  test("the family's new-person page, with no next", () => {
    assert.equal(addRelativeHref("f1"), "/families/f1/people/new");
    assert.equal(addRelativeHref("6650c0ffee"), "/families/6650c0ffee/people/new");
  });

  test("encodes the family id", () => {
    assert.equal(addRelativeHref("a/b c?d#e"), "/families/a%2Fb%20c%3Fd%23e/people/new");
  });
});

// ---- listOfNames ------------------------------------------------------------

describe("listOfNames", () => {
  test("one, two and three names", () => {
    assert.equal(listOfNames(["David"]), "David");
    assert.equal(listOfNames(["David", "Rosa"]), "David and Rosa");
    assert.equal(listOfNames(["David", "Rosa", "Luis"]), "David, Rosa and Luis");
  });

  test("empty for none; blanks are dropped and names trimmed", () => {
    assert.equal(listOfNames([]), "");
    assert.equal(listOfNames(["", "  "]), "");
    assert.equal(listOfNames([" David ", "", "Rosa"]), "David and Rosa");
  });
});

// ---- inviteAskLine ----------------------------------------------------------

describe("inviteAskLine", () => {
  const row = (name, role, extra = {}) => ({ name, role, personId: `p-${name}`, isYou: false, ...extra });

  test("names the admins, never the viewer or a member", () => {
    assert.equal(
      inviteAskLine([row("Ana", "member", { isYou: true }), row("David", "admin"), row("Sam", "member")]),
      "Ask David to send them the invite link.",
    );
    assert.equal(
      inviteAskLine([row("David", "admin"), row("Rosa", "admin"), row("Ana", "member", { isYou: true })]),
      "Ask David and Rosa to send them the invite link.",
    );
  });

  test("an admin with no person in the tree has no name to give", () => {
    assert.equal(
      inviteAskLine([row("A family member", "admin", { personId: null }), row("Ana", "member", { isYou: true })]),
      "Ask an admin to send them the invite link.",
    );
  });

  test("falls back to 'an admin' when there is nobody to name", () => {
    assert.equal(inviteAskLine([]), "Ask an admin to send them the invite link.");
    assert.equal(inviteAskLine([row("Ana", "admin", { isYou: true })]), "Ask an admin to send them the invite link.");
  });
});

// ---- signedInLabel ----------------------------------------------------------

describe("signedInLabel", () => {
  test("name and email when the account has a name", () => {
    assert.equal(signedInLabel({ name: "Maya", email: "m@x.org" }), "Maya (m@x.org)");
    assert.equal(signedInLabel({ name: "  Maya Park ", email: "m@x.org" }), "Maya Park (m@x.org)", "name trimmed");
  });

  test("just the email when the name is blank, null or missing", () => {
    assert.equal(signedInLabel({ name: "  ", email: "m@x.org" }), "m@x.org");
    assert.equal(signedInLabel({ name: "", email: "m@x.org" }), "m@x.org");
    assert.equal(signedInLabel({ name: null, email: "m@x.org" }), "m@x.org");
    assert.equal(signedInLabel({ email: "m@x.org" }), "m@x.org");
  });

  test("never leaves empty brackets when the email is missing", () => {
    assert.equal(signedInLabel({ name: "Maya", email: "" }), "Maya");
  });
});

// ---- settingsLoadError ------------------------------------------------------

describe("settingsLoadError", () => {
  test("a 404 ApiError is not-found", () => {
    assert.deepEqual(settingsLoadError(new ApiError(404)), { kind: "not-found" });
    assert.deepEqual(settingsLoadError(new ApiError(404, "Family not found")), { kind: "not-found" });
  });

  test("a 500 ApiError shows its message", () => {
    assert.deepEqual(settingsLoadError(new ApiError(500, "The database is asleep.")), {
      kind: "error",
      message: "The database is asleep.",
    });
    assert.deepEqual(settingsLoadError(new ApiError(500)), {
      kind: "error",
      message: "Something went wrong on our side. Please try again.",
    });
  });

  test("status 0 (never reached the server) shows the offline message", () => {
    assert.deepEqual(settingsLoadError(new ApiError(0)), {
      kind: "error",
      message: NETWORK_ERROR_MESSAGE,
    });
  });

  test("anything that is not an ApiError gets the generic message", () => {
    for (const err of [new Error("boom"), new TypeError("x is undefined"), "nope", null, undefined, { status: 404 }]) {
      assert.deepEqual(settingsLoadError(err), { kind: "error", message: SETTINGS_LOAD_FALLBACK }, String(err));
    }
  });
});
