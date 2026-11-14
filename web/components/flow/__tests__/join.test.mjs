// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in join.ts. join.ts imports
// "./validation" without an extension (as the Next build wants), so this file
// first adds a resolve hook that maps extensionless relative imports made from
// .ts files to the .ts file next to them, then loads the module dynamically.
import { test } from "node:test";
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
  JOIN_FIELDS,
  initialJoinChoice,
  inviteHeading,
  needsRelationships,
  toJoinRequest,
  validateJoinChoice,
} = await import("../join.ts");

const UNCLAIMED = [
  { id: "rosa", name: "Rosa", born: 1938, color: "#7b5ea7" },
  { id: "luis", name: "Luis", born: 1936, color: "#5f7f52" },
  { id: "maya", name: "Maya", born: null, color: null },
];
const thisYear = new Date().getFullYear();

// ---- initialJoinChoice ----------------------------------------------------

test("initialJoinChoice: claim with nobody picked when anyone is unclaimed", () => {
  assert.deepEqual(initialJoinChoice(UNCLAIMED), { mode: "claim", personId: "" });
  // The suggested name waits for the add option; it does not change the start.
  assert.deepEqual(initialJoinChoice(UNCLAIMED, "Sam"), { mode: "claim", personId: "" });
});

test("initialJoinChoice: add yourself when nobody is unclaimed", () => {
  assert.deepEqual(initialJoinChoice([]), { mode: "add", name: "", born: "" });
});

test("initialJoinChoice: add uses the suggested name", () => {
  assert.deepEqual(initialJoinChoice([], "Sam Rivera"), { mode: "add", name: "Sam Rivera", born: "" });
});

// ---- validateJoinChoice ---------------------------------------------------

test("validateJoinChoice: claim with no pick", () => {
  const e = validateJoinChoice({ mode: "claim", personId: "" }, UNCLAIMED);
  assert.deepEqual(Object.keys(e), ["personId"]);
  assert.match(e.personId, /pick/i);
});

test("validateJoinChoice: claim with a pick not in the list", () => {
  const e = validateJoinChoice({ mode: "claim", personId: "dad" }, UNCLAIMED);
  assert.deepEqual(Object.keys(e), ["personId"]);
  assert.match(e.personId, /isn't available/);
  // Nobody to claim at all: any pick is out of the list.
  assert.ok(validateJoinChoice({ mode: "claim", personId: "rosa" }, []).personId);
});

test("validateJoinChoice: claim with a valid pick", () => {
  assert.deepEqual(validateJoinChoice({ mode: "claim", personId: "luis" }, UNCLAIMED), {});
});

test("validateJoinChoice: add with a blank name", () => {
  for (const name of ["", "   "]) {
    const e = validateJoinChoice({ mode: "add", name, born: "" }, UNCLAIMED);
    assert.deepEqual(Object.keys(e), ["name"]);
    assert.equal(e.name, "Enter your name.");
  }
});

test("validateJoinChoice: add with a too-long name", () => {
  const e = validateJoinChoice({ mode: "add", name: "x".repeat(81), born: "" }, []);
  assert.match(e.name, /80 characters/);
});

test("validateJoinChoice: add with a bad born year", () => {
  const cases = [
    ["19x8", /as a number/],
    ["1700", /from 1850/],
    [String(thisYear + 1), /hasn't happened/],
  ];
  for (const [born, re] of cases) {
    const e = validateJoinChoice({ mode: "add", name: "Sam", born }, UNCLAIMED);
    assert.deepEqual(Object.keys(e), ["born"], born);
    assert.match(e.born, re, born);
  }
});

test("validateJoinChoice: add with a blank name and a bad year reports both", () => {
  const e = validateJoinChoice({ mode: "add", name: "", born: "3000" }, []);
  assert.deepEqual(Object.keys(e).sort(), ["born", "name"]);
});

test("validateJoinChoice: valid add, with or without a born year", () => {
  assert.deepEqual(validateJoinChoice({ mode: "add", name: "Sam", born: "" }, UNCLAIMED), {});
  assert.deepEqual(validateJoinChoice({ mode: "add", name: " Sam ", born: "1994" }, []), {});
  assert.deepEqual(validateJoinChoice({ mode: "add", name: "Sam", born: String(thisYear) }, []), {});
});

test("validateJoinChoice: add ignores who might be unclaimed", () => {
  assert.deepEqual(validateJoinChoice({ mode: "add", name: "Rosa", born: "1938" }, UNCLAIMED), {});
});

// ---- toJoinRequest --------------------------------------------------------

test("toJoinRequest: claim sends only personId", () => {
  const body = toJoinRequest({ mode: "claim", personId: "rosa" });
  assert.deepEqual(body, { personId: "rosa" });
  assert.ok(!("person" in body));
});

test("toJoinRequest: add trims the name and sends no personId", () => {
  const body = toJoinRequest({ mode: "add", name: "  Sam Rivera ", born: "" });
  assert.deepEqual(body, { person: { name: "Sam Rivera", born: null } });
  assert.ok(!("personId" in body));
});

test("toJoinRequest: add turns the born year into a number", () => {
  assert.deepEqual(toJoinRequest({ mode: "add", name: "Sam", born: "1948" }), {
    person: { name: "Sam", born: 1948 },
  });
  assert.deepEqual(toJoinRequest({ mode: "add", name: "Sam", born: " 1994 " }), {
    person: { name: "Sam", born: 1994 },
  });
  assert.equal(toJoinRequest({ mode: "add", name: "Sam", born: "   " }).person.born, null);
});

// ---- needsRelationships ---------------------------------------------------

test("needsRelationships: true for add, false for claim", () => {
  assert.equal(needsRelationships({ mode: "add", name: "Sam", born: "" }), true);
  assert.equal(needsRelationships({ mode: "claim", personId: "rosa" }), false);
});

// ---- JOIN_FIELDS ----------------------------------------------------------

test("JOIN_FIELDS: every field, once", () => {
  assert.deepEqual([...JOIN_FIELDS], ["personId", "name", "born"]);
});

// ---- inviteHeading --------------------------------------------------------

test("inviteHeading: a surname becomes '<name> family's book'", () => {
  assert.equal(inviteHeading("Mao"), "You’re invited to the Mao family’s book");
  assert.equal(inviteHeading("  Rivera  "), "You’re invited to the Rivera family’s book");
});

test("inviteHeading: a name that already reads as a family is kept", () => {
  assert.equal(inviteHeading("The Riveras"), "You’re invited to the Riveras’ book");
  assert.equal(inviteHeading("the Maos"), "You’re invited to the Maos’ book");
  assert.equal(inviteHeading("Mao family"), "You’re invited to the Mao family’s book");
  assert.equal(inviteHeading("The Mao Family"), "You’re invited to the Mao Family’s book");
});

test("inviteHeading: a blank name still reads well", () => {
  assert.equal(inviteHeading("  "), "You’re invited to a family’s book");
});
