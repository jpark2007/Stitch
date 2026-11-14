// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in accountForms.ts. accountForms.ts
// imports "./validation" without an extension (as the Next build wants), so
// this file first adds a resolve hook that maps extensionless relative imports
// made from .ts files to the .ts file next to them, then loads it dynamically.
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
  FAMILY_FIELDS,
  LOGIN_FIELDS,
  PASSWORD_HINT,
  SIGNUP_FIELDS,
  adoptTypedValues,
  familyServerErrors,
  loginDraftAfterSuccess,
  roleLabel,
  serverErrorState,
  shouldShowSwitcher,
  signupDraftAfterSuccess,
  toCreateFamilyRequest,
  toLoginRequest,
  toSignupRequest,
  validateFamily,
  validateLogin,
  validateSignup,
  withoutError,
} = await import("../accountForms.ts");
const { validatePassword } = await import("../validation.ts");

const thisYear = new Date().getFullYear();

// ---- validateLogin --------------------------------------------------------

test("validateLogin: empty draft asks for both", () => {
  assert.deepEqual(validateLogin({ email: "", password: "" }), {
    email: "Enter your email.",
    password: "Enter your password.",
  });
});

test("validateLogin: whitespace-only email counts as empty", () => {
  assert.equal(validateLogin({ email: "   ", password: "x" }).email, "Enter your email.");
});

test("validateLogin: bad email", () => {
  for (const email of ["rosa", "rosa@", "rosa@maos", "ro sa@maos.com", "@maos.com"]) {
    assert.equal(
      validateLogin({ email, password: "secret" }).email,
      "That doesn't look like an email address.",
      email,
    );
  }
});

test("validateLogin: any non-empty password is fine (no signup length rule)", () => {
  for (const password of ["x", "abc", " ", "1234567"]) {
    assert.deepEqual(validateLogin({ email: "rosa@maos.family", password }), {}, JSON.stringify(password));
  }
});

test("validateLogin: a valid draft has no errors", () => {
  assert.deepEqual(validateLogin({ email: "  rosa@maos.family ", password: "correct horse" }), {});
});

// ---- validateSignup -------------------------------------------------------

test("validateSignup: empty draft asks for all three", () => {
  assert.deepEqual(validateSignup({ name: "", email: "", password: "" }), {
    name: "Enter your name.",
    email: "Enter your email.",
    password: "Enter a password.",
  });
});

test("validateSignup: bad email", () => {
  const e = validateSignup({ name: "Rosa", email: "rosa.maos.family", password: "longenough" });
  assert.deepEqual(e, { email: "That doesn't look like an email address." });
});

test("validateSignup: short password", () => {
  const e = validateSignup({ name: "Rosa", email: "rosa@maos.family", password: "1234567" });
  assert.deepEqual(e, { password: "That’s too short. Use at least 8 characters." });
});

test("validateSignup: eight characters, spaces counted, is enough", () => {
  assert.deepEqual(validateSignup({ name: "Rosa", email: "rosa@maos.family", password: "12345678" }), {});
  assert.deepEqual(validateSignup({ name: "Rosa", email: "rosa@maos.family", password: "  abcd  " }), {});
});

test("validateSignup: blank and overlong names", () => {
  assert.equal(
    validateSignup({ name: "   ", email: "rosa@maos.family", password: "12345678" }).name,
    "Enter your name.",
  );
  assert.match(
    validateSignup({ name: "a".repeat(81), email: "rosa@maos.family", password: "12345678" }).name,
    /80 characters or fewer/,
  );
});

test("PASSWORD_HINT: the signup rule, the same minimum validatePassword checks", () => {
  assert.equal(PASSWORD_HINT, "At least 8 characters.");
  // Worded apart from the error, so a short password never shows one line twice.
  assert.notEqual(PASSWORD_HINT, validatePassword("short"));
  assert.ok(validatePassword("short").includes("at least 8 characters"));
  assert.equal(validatePassword("12345678"), null);
  assert.notEqual(validatePassword("1234567"), null);
});

// ---- validateFamily -------------------------------------------------------

const fam = (over = {}) => ({ familyName: "The Maos", yourName: "Rosa", born: "", ...over });

test("validateFamily: empty draft asks for both names, born stays optional", () => {
  assert.deepEqual(validateFamily({ familyName: "", yourName: "", born: "" }), {
    familyName: "Give your family a name.",
    yourName: "Enter your name.",
  });
});

test("validateFamily: blank born (empty or spaces) is fine", () => {
  assert.deepEqual(validateFamily(fam({ born: "" })), {});
  assert.deepEqual(validateFamily(fam({ born: "   " })), {});
});

test("validateFamily: a valid born year is fine", () => {
  assert.deepEqual(validateFamily(fam({ born: "1950" })), {});
  assert.deepEqual(validateFamily(fam({ born: "1850" })), {});
  assert.deepEqual(validateFamily(fam({ born: String(thisYear) })), {});
});

test("validateFamily: bad born years", () => {
  assert.equal(validateFamily(fam({ born: "19x0" })).born, "Enter the year as a number, like 1987.");
  assert.equal(validateFamily(fam({ born: "1849" })).born, "Pick a year from 1850 on.");
  assert.equal(validateFamily(fam({ born: String(thisYear + 1) })).born, "That year hasn't happened yet.");
  assert.equal(validateFamily(fam({ born: "1950.5" })).born, "Use a whole year, like 1987.");
});

test("validateFamily: overlong family name", () => {
  assert.match(validateFamily(fam({ familyName: "M".repeat(81) })).familyName, /80 characters or fewer/);
});

// ---- request mappers ------------------------------------------------------

test("toLoginRequest: trims the email, leaves the password untouched", () => {
  assert.deepEqual(toLoginRequest({ email: "  rosa@maos.family\n", password: "  pass word  " }), {
    email: "rosa@maos.family",
    password: "  pass word  ",
  });
});

test("toSignupRequest: trims name and email, leaves the password untouched", () => {
  assert.deepEqual(
    toSignupRequest({ name: "  Rosa Mao ", email: " rosa@maos.family ", password: " secret12 " }),
    { name: "Rosa Mao", email: "rosa@maos.family", password: " secret12 " },
  );
});

test("toCreateFamilyRequest: trims names, blank born is null", () => {
  assert.deepEqual(toCreateFamilyRequest({ familyName: "  The Maos ", yourName: " Rosa ", born: "" }), {
    name: "The Maos",
    person: { name: "Rosa", born: null },
  });
  assert.deepEqual(toCreateFamilyRequest(fam({ born: "  " })).person.born, null);
});

test("toCreateFamilyRequest: born '1950' becomes the number 1950", () => {
  const body = toCreateFamilyRequest(fam({ born: "1950" }));
  assert.equal(body.person.born, 1950);
  assert.equal(typeof body.person.born, "number");
  assert.equal(toCreateFamilyRequest(fam({ born: " 1987 " })).person.born, 1987);
});

// ---- server errors --------------------------------------------------------

test("field lists are in form order", () => {
  assert.deepEqual([...LOGIN_FIELDS], ["email", "password"]);
  assert.deepEqual([...SIGNUP_FIELDS], ["name", "email", "password"]);
  assert.deepEqual([...FAMILY_FIELDS], ["familyName", "yourName", "born"]);
});

test("serverErrorState: a 401 detail alone goes to the form-level message", () => {
  const s = serverErrorState({ detail: "That email and password don't match", fieldErrors: {} }, LOGIN_FIELDS);
  assert.deepEqual(s, { fields: {}, formError: "That email and password don't match" });
});

test("serverErrorState: field errors land on fields; detail still shown", () => {
  const s = serverErrorState(
    {
      detail: "Please check the highlighted fields.",
      fieldErrors: { email: "That email is already in use.", password: "Too common." },
    },
    SIGNUP_FIELDS,
  );
  assert.deepEqual(s.fields, { email: "That email is already in use.", password: "Too common." });
  assert.equal(s.formError, "Please check the highlighted fields.");
});

test("serverErrorState: unknown keys join the detail, without repeats", () => {
  const s = serverErrorState(
    { detail: "That didn't work.", fieldErrors: { captcha: "Try again.", other: "Try again." } },
    LOGIN_FIELDS,
  );
  assert.deepEqual(s.fields, {});
  assert.equal(s.formError, "That didn't work. Try again.");
});

test("serverErrorState: 'check the highlighted fields' is dropped when nothing is highlighted", () => {
  // A 422 about a field this form does not have: its own message, not a pointer at nothing.
  const s = serverErrorState(
    { detail: "Please check the highlighted fields.", fieldErrors: { email: "Enter a valid email" } },
    FAMILY_FIELDS,
  );
  assert.deepEqual(s.fields, {});
  assert.equal(s.formError, "Enter a valid email");
});

test("serverErrorState: a 422 with nothing usable still says something plain", () => {
  const none = serverErrorState({ detail: "Please check the highlighted fields.", fieldErrors: {} }, LOGIN_FIELDS);
  assert.deepEqual(none, { fields: {}, formError: "Something in that form isn't quite right. Check it and try again." });
  // When a field is highlighted, the pointer is right and stays.
  const some = serverErrorState(
    { detail: "Please check the highlighted fields.", fieldErrors: { password: "Too short." } },
    LOGIN_FIELDS,
  );
  assert.deepEqual(some, { fields: { password: "Too short." }, formError: "Please check the highlighted fields." });
});

test("serverErrorState: blank detail and nothing left over gives null", () => {
  const s = serverErrorState({ detail: "  ", fieldErrors: { email: "Taken." } }, SIGNUP_FIELDS);
  assert.deepEqual(s, { fields: { email: "Taken." }, formError: null });
});

test("familyServerErrors: maps API names to the form's fields", () => {
  assert.deepEqual(
    familyServerErrors({ name: "Too long.", "person.name": "Needed.", born: "Bad year." }),
    { familyName: "Too long.", yourName: "Needed.", born: "Bad year." },
  );
  assert.deepEqual(familyServerErrors({ familyName: "A", yourName: "B", "person.born": "C" }), {
    familyName: "A",
    yourName: "B",
    born: "C",
  });
});

test("familyServerErrors: unknown and prototype-looking keys pass through as-is", () => {
  assert.deepEqual(familyServerErrors({ died: "No.", constructor: "Odd." }), { died: "No.", constructor: "Odd." });
});

test("familyServerErrors + serverErrorState: end to end", () => {
  const s = serverErrorState(
    { detail: "Please check the highlighted fields.", fieldErrors: familyServerErrors({ name: "Too long." }) },
    FAMILY_FIELDS,
  );
  assert.deepEqual(s.fields, { familyName: "Too long." });
});

test("withoutError: drops one key, same object when absent", () => {
  const e = { email: "Bad.", password: "Short." };
  assert.deepEqual(withoutError(e, "email"), { password: "Short." });
  assert.deepEqual(e, { email: "Bad.", password: "Short." }, "input not mutated");
  const none = { password: "Short." };
  assert.equal(withoutError(none, "email"), none);
});

// ---- family switcher ------------------------------------------------------

const f = (id, role = "member") => ({ id, name: `Family ${id}`, role });

test("shouldShowSwitcher: hidden for none or one family", () => {
  assert.equal(shouldShowSwitcher([]), false);
  assert.equal(shouldShowSwitcher([f("a")]), false);
  assert.equal(shouldShowSwitcher(undefined), false);
  assert.equal(shouldShowSwitcher(null), false);
});

test("shouldShowSwitcher: shown for two or more", () => {
  assert.equal(shouldShowSwitcher([f("a"), f("b")]), true);
  assert.equal(shouldShowSwitcher([f("a"), f("b"), f("c")]), true);
});

test("roleLabel: admin and member", () => {
  assert.equal(roleLabel("admin"), "Admin");
  assert.equal(roleLabel("member"), "Member");
});

// ---- after a successful submit -------------------------------------------

test("loginDraftAfterSuccess: the password is gone, the email is kept", () => {
  const draft = { email: "a@b.c", password: "secret" };
  const after = loginDraftAfterSuccess(draft);
  assert.equal(after.password, "");
  assert.equal(after.email, "a@b.c");
  assert.deepEqual(Object.keys(after).sort(), ["email", "password"]);
  assert.deepEqual(draft, { email: "a@b.c", password: "secret" }, "input not mutated");
});

test("loginDraftAfterSuccess: no password survives, whatever was typed", () => {
  for (const password of ["x", "  spaced  ", "correct horse battery staple"]) {
    assert.equal(loginDraftAfterSuccess({ email: "", password }).password, "");
  }
});

test("signupDraftAfterSuccess: empty name, email and password", () => {
  assert.deepEqual(signupDraftAfterSuccess(), { name: "", email: "", password: "" });
});

test("signupDraftAfterSuccess: a fresh object each time", () => {
  const a = signupDraftAfterSuccess();
  a.password = "leaked";
  assert.equal(signupDraftAfterSuccess().password, "");
});

// The browser sweep's case: /login is server-rendered in next dev, and an
// email and password typed before hydration were in the DOM but not in state.
test("adoptTypedValues: takes what was typed before hydration", () => {
  const draft = { email: "", password: "" };
  const next = adoptTypedValues(draft, { email: "ana@demo.stitch", password: "stitch-demo" });
  assert.deepEqual(next, { email: "ana@demo.stitch", password: "stitch-demo" });
  assert.deepEqual(draft, { email: "", password: "" }, "the draft is not mutated");
});

test("adoptTypedValues: nothing typed returns the same draft (no re-render)", () => {
  const draft = { name: "Ana", email: "", password: "" };
  assert.equal(adoptTypedValues(draft, { name: "Ana", email: "", password: "" }), draft);
  assert.equal(adoptTypedValues(draft, {}), draft);
});

test("adoptTypedValues: only the fields that differ change", () => {
  const draft = { familyName: "", yourName: "Ana Mao", born: "" };
  const next = adoptTypedValues(draft, { familyName: "The Maos", yourName: "Ana Mao", born: "" });
  assert.deepEqual(next, { familyName: "The Maos", yourName: "Ana Mao", born: "" });
});

test("adoptTypedValues: a cleared field is adopted as cleared", () => {
  // The server rendered your name; you deleted it before the page woke up.
  const next = adoptTypedValues({ familyName: "", yourName: "Ana Mao", born: "" }, { yourName: "" });
  assert.equal(next.yourName, "");
});

test("adoptTypedValues: unknown keys, non-strings and inherited names are ignored", () => {
  const draft = { email: "", password: "" };
  const next = adoptTypedValues(draft, {
    remember: "on",
    email: 42,
    password: null,
    toString: "x",
    __proto__: "y",
  });
  assert.equal(next, draft);
  assert.equal(Object.getPrototypeOf(next), Object.prototype);
});

test("adoptTypedValues: spaces in a password are kept exactly", () => {
  const next = adoptTypedValues({ email: "", password: "" }, { password: "  pass phrase  " });
  assert.equal(next.password, "  pass phrase  ");
});
