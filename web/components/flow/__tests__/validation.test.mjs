// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in validation.ts; no build step needed.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  hasErrors,
  mergeServerErrors,
  validateEmail,
  validateFamilyName,
  validateName,
  validatePassword,
  validateYear,
} from "../validation.ts";

const thisYear = new Date().getFullYear();

test("validateEmail: empty, invalid, valid", () => {
  assert.equal(validateEmail(""), "Enter your email.");
  assert.equal(validateEmail("   "), "Enter your email.");
  for (const bad of ["grandma", "grandma@", "@example.com", "grandma@example", "gran ma@example.com", "a@b@c.com"]) {
    assert.equal(validateEmail(bad), "That doesn't look like an email address.", bad);
  }
  assert.equal(validateEmail("grandma@example.com"), null);
  assert.equal(validateEmail("  rose.park+stitch@mail.example.org  "), null);
});

test("validatePassword: empty, too short, valid, matches the API's min_length=8", () => {
  assert.equal(validatePassword(""), "Enter a password.");
  assert.equal(validatePassword("1234567"), "That’s too short. Use at least 8 characters.");
  assert.equal(validatePassword("12345678"), null);
  // Not trimmed: spaces count, like the server.
  assert.equal(validatePassword("        "), null);
  // Counted in characters, not UTF-16 units: 4 emoji are 4 characters.
  assert.equal(validatePassword("🧵🧵🧵🧵"), "That’s too short. Use at least 8 characters.");
});

test("validateName: empty, too long, valid, custom noun", () => {
  assert.equal(validateName(""), "Enter your name.");
  assert.equal(validateName("   "), "Enter your name.");
  assert.equal(validateName("", "their name"), "Enter their name.");
  assert.equal(validateName("x".repeat(81)), "Keep your name to 80 characters or fewer.");
  assert.equal(validateName("x".repeat(80)), null);
  // Surrounding spaces do not count toward the limit.
  assert.equal(validateName(`  ${"x".repeat(80)}  `), null);
  assert.equal(validateName("Rose Park"), null);
});

test("validateFamilyName: empty, too long, valid", () => {
  assert.equal(validateFamilyName(""), "Give your family a name.");
  assert.equal(validateFamilyName("  "), "Give your family a name.");
  assert.equal(validateFamilyName("y".repeat(81)), "Keep the family name to 80 characters or fewer.");
  assert.equal(validateFamilyName("The Parks"), null);
});

test("validateYear: empty is fine unless required", () => {
  assert.equal(validateYear(""), null);
  assert.equal(validateYear("  "), null);
  assert.equal(validateYear(Number.NaN), null);
  assert.equal(validateYear("", { required: true }), "Enter a year.");
  assert.equal(validateYear(Number.NaN, { required: true }), "Enter a year.");
});

test("validateYear: not a number and non-integers", () => {
  for (const bad of ["abc", "19x7", "1e3", "nineteen", "1,987"]) {
    assert.equal(validateYear(bad), "Enter the year as a number, like 1987.", bad);
  }
  assert.equal(validateYear(Number.POSITIVE_INFINITY), "Enter the year as a number, like 1987.");
  assert.equal(validateYear("1987.5"), "Use a whole year, like 1987.");
  assert.equal(validateYear(1987.5), "Use a whole year, like 1987.");
});

test("validateYear: default bounds are 1850 through this year, inclusive", () => {
  assert.equal(validateYear("1850"), null);
  assert.equal(validateYear(1849), "Pick a year from 1850 on.");
  assert.equal(validateYear(String(thisYear)), null);
  assert.equal(validateYear(thisYear + 1), "That year hasn't happened yet.");
  assert.equal(validateYear(" 1987 "), null);
  assert.equal(validateYear(1987), null);
});

test("validateYear: custom bounds", () => {
  assert.equal(validateYear(1950, { min: 1960 }), "Pick a year from 1960 on.");
  assert.equal(validateYear(1960, { min: 1960 }), null);
  assert.equal(validateYear(2001, { max: 2000 }), "Pick a year no later than 2000.");
  assert.equal(validateYear(2000, { max: 2000 }), null);
  // A future year still gets the friendlier message under a custom max.
  assert.equal(validateYear(thisYear + 5, { max: 2000 }), "That year hasn't happened yet.");
  assert.equal(validateYear(thisYear + 1, { max: thisYear + 10 }), null);
});

test("hasErrors", () => {
  assert.equal(hasErrors({}), false);
  assert.equal(hasErrors({ email: null, password: undefined, name: "" }), false);
  assert.equal(hasErrors({ email: null, password: "Use at least 8 characters." }), true);
});

test("mergeServerErrors: known keys land on fields, unknown keys go to rest", () => {
  const fields = ["email", "password", "name"];
  const local = { name: "Enter your name." };
  const out = mergeServerErrors(local, { email: "That email is already in use.", invite: "Invite expired." }, fields);
  assert.deepEqual(out.fields, { name: "Enter your name.", email: "That email is already in use." });
  assert.equal(out.rest, "Invite expired.");
  // The input is not mutated.
  assert.deepEqual(local, { name: "Enter your name." });
});

test("mergeServerErrors: server overrides local for the same field", () => {
  const out = mergeServerErrors({ password: "local" }, { password: "server" }, ["password"]);
  assert.deepEqual(out.fields, { password: "server" });
  assert.equal(out.rest, null);
});

test("mergeServerErrors: dotted and snake_case keys match camelCase fields", () => {
  const out = mergeServerErrors(
    {},
    { "person.name": "Name is required.", participant_ids: "Pick at least one person." },
    ["name", "participantIds"],
  );
  assert.deepEqual(out.fields, { name: "Name is required.", participantIds: "Pick at least one person." });
  assert.equal(out.rest, null);
});

test("mergeServerErrors: several unknown keys are joined, duplicates and blanks dropped", () => {
  const out = mergeServerErrors({}, { a: "First.", b: "Second.", c: "First.", d: "  " }, ["email"]);
  assert.deepEqual(out.fields, {});
  assert.equal(out.rest, "First. Second.");
});

test("mergeServerErrors: nothing from the server keeps local and rest null", () => {
  const out = mergeServerErrors({ email: "Enter your email." }, {}, ["email"]);
  assert.deepEqual(out.fields, { email: "Enter your email." });
  assert.equal(out.rest, null);
});
