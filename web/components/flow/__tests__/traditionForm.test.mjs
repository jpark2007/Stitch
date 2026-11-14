// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in traditionForm.ts. It imports
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
  CATEGORY_OPTIONS,
  DESCRIPTION_MAX,
  LABEL_MAX,
  MONTH_NAMES,
  PEOPLE_MAX,
  PHOTOS_MAX,
  START_YEAR_MIN,
  TITLE_MAX,
  TRADITION_FIELDS,
  TRADITION_SUBMIT_ERRORS,
  WHEN_OPTIONS,
  emptyTraditionDraft,
  labelPlaceholder,
  monthMakesSense,
  toCreateTraditionRequest,
  traditionServerErrors,
  traditionSubmitErrorMessage,
  validateTraditionDraft,
} = await import("../traditionForm.ts");
const mock = await import("../mock/mockBackend.ts");
const { ApiError } = await import("../../../lib/api.ts");

const SELF = "p_rosa";
const thisYear = new Date().getFullYear();

function draft(over = {}) {
  return { ...emptyTraditionDraft(SELF), title: "Tamales on Christmas Eve", category: "food", kind: "yearly", ...over };
}

test("choices: the eight categories and five kinds of the contract, each with a friendly label", () => {
  assert.deepEqual(
    CATEGORY_OPTIONS.map((o) => o.value),
    [...mock.TRADITION_CATEGORIES],
  );
  assert.deepEqual(
    CATEGORY_OPTIONS.map((o) => o.label),
    ["Food", "Holiday", "Trip", "Music", "Game", "Faith", "Craft", "Something else"],
  );
  assert.deepEqual(
    WHEN_OPTIONS.map((o) => o.value),
    [...mock.TRADITION_WHEN_KINDS],
  );
  assert.deepEqual(
    WHEN_OPTIONS.map((o) => o.label),
    ["Every year", "Every season", "Every month", "Every week", "Now and then"],
  );
  assert.equal(MONTH_NAMES.length, 12);
  assert.equal(MONTH_NAMES[0], "January");
  assert.equal(MONTH_NAMES[11], "December");
  for (const o of WHEN_OPTIONS) assert.ok(labelPlaceholder(o.value).length > 0);
  assert.ok(labelPlaceholder(null).length > 0);
});

test("bounds mirror the contract (the mock mirrors api/schemas.py)", () => {
  assert.equal(TITLE_MAX, mock.MAX_TRADITION_TITLE_CHARS);
  assert.equal(DESCRIPTION_MAX, mock.MAX_TRADITION_DESCRIPTION_CHARS);
  assert.equal(LABEL_MAX, mock.MAX_TRADITION_LABEL_CHARS);
  assert.equal(PEOPLE_MAX, mock.MAX_TRADITION_PEOPLE);
  assert.equal(PHOTOS_MAX, mock.MAX_TRADITION_PHOTOS);
  assert.equal(START_YEAR_MIN, mock.MIN_TRADITION_YEAR);
  assert.deepEqual(
    [...TRADITION_FIELDS].sort(),
    ["category", "description", "kind", "label", "month", "participantIds", "photos", "startedYear", "title"],
  );
});

test("emptyTraditionDraft: blank, with the author ticked when they are in the tree", () => {
  assert.deepEqual(emptyTraditionDraft(SELF), {
    title: "",
    description: "",
    category: null,
    kind: null,
    label: "",
    month: "",
    participantIds: [SELF],
    startedYear: "",
  });
  assert.deepEqual(emptyTraditionDraft(null).participantIds, []);
  assert.deepEqual(emptyTraditionDraft().participantIds, []);
});

test("monthMakesSense: only for yearly and seasonal", () => {
  assert.equal(monthMakesSense("yearly"), true);
  assert.equal(monthMakesSense("seasonal"), true);
  for (const k of ["monthly", "weekly", "occasional", null]) assert.equal(monthMakesSense(k), false, String(k));
});

test("validate: a good draft has no errors", () => {
  assert.deepEqual(validateTraditionDraft(draft()), {});
  assert.deepEqual(validateTraditionDraft(draft({ startedYear: "1962", month: "12", label: "Christmas Eve" }), 3), {});
  assert.deepEqual(validateTraditionDraft(draft({ startedYear: String(thisYear) })), {});
  assert.deepEqual(validateTraditionDraft(draft({ startedYear: String(START_YEAR_MIN) })), {});
});

test("validate: title, category and kind are needed", () => {
  const e = validateTraditionDraft(emptyTraditionDraft(SELF));
  assert.equal(e.title, "Give the tradition a name.");
  assert.equal(e.category, "Pick what kind of tradition it is.");
  assert.equal(e.kind, "Pick how often it happens.");
  assert.deepEqual(Object.keys(e).sort(), ["category", "kind", "title"]);
  assert.equal(validateTraditionDraft(draft({ title: "   \n " })).title, "Give the tradition a name.");
});

test("validate: lengths are counted in code points after trimming", () => {
  assert.equal(validateTraditionDraft(draft({ title: `  ${"a".repeat(TITLE_MAX)}  ` })).title, undefined);
  assert.match(validateTraditionDraft(draft({ title: "a".repeat(TITLE_MAX + 1) })).title, /200 characters/);
  // 200 emoji are 400 UTF-16 units but 200 characters: fine.
  assert.equal(validateTraditionDraft(draft({ title: "🎄".repeat(TITLE_MAX) })).title, undefined);
  assert.equal(validateTraditionDraft(draft({ description: "a".repeat(DESCRIPTION_MAX) })).description, undefined);
  assert.match(validateTraditionDraft(draft({ description: "a".repeat(DESCRIPTION_MAX + 1) })).description, /5,000/);
  assert.equal(validateTraditionDraft(draft({ label: "a".repeat(LABEL_MAX) })).label, undefined);
  assert.match(validateTraditionDraft(draft({ label: "a".repeat(LABEL_MAX + 1) })).label, /100 characters/);
});

test("validate: the year it started is optional, 1800 to this year", () => {
  assert.equal(validateTraditionDraft(draft({ startedYear: "" })).startedYear, undefined);
  assert.equal(validateTraditionDraft(draft({ startedYear: "1799" })).startedYear, "Pick a year from 1800 on.");
  assert.equal(
    validateTraditionDraft(draft({ startedYear: String(thisYear + 1) })).startedYear,
    "That year hasn't happened yet.",
  );
  assert.equal(validateTraditionDraft(draft({ startedYear: "19x2" })).startedYear, "Enter the year as a number, like 1987.");
});

test("validate: month only checked when it is shown; people and photos capped", () => {
  assert.equal(validateTraditionDraft(draft({ month: "13" })).month, "Pick a month from the list.");
  assert.equal(validateTraditionDraft(draft({ kind: "weekly", month: "13" })).month, undefined);
  const many = Array.from({ length: PEOPLE_MAX + 1 }, (_, i) => `p${i}`);
  assert.equal(validateTraditionDraft(draft({ participantIds: many })).participantIds, "Pick up to 100 people.");
  assert.equal(validateTraditionDraft(draft({ participantIds: [...many.slice(0, PEOPLE_MAX), "p0"] })).participantIds, undefined);
  assert.equal(validateTraditionDraft(draft(), PHOTOS_MAX).photos, undefined);
  assert.equal(validateTraditionDraft(draft(), PHOTOS_MAX + 1).photos, "A tradition can hold up to 10 photos.");
});

test("toCreateTraditionRequest: trims, leaves out empty optional fields", () => {
  assert.deepEqual(toCreateTraditionRequest(draft({ title: "  Tamales  ", participantIds: [] })), {
    title: "Tamales",
    category: "food",
    when: { kind: "yearly" },
  });
  assert.deepEqual(
    toCreateTraditionRequest(
      draft({
        description: "  Everyone rolls.  ",
        label: " Christmas Eve ",
        month: "12",
        participantIds: [SELF, "p_ana", SELF, ""],
        startedYear: " 1962 ",
      }),
      ["media_1", "media_2", "media_1"],
    ),
    {
      title: "Tamales on Christmas Eve",
      category: "food",
      when: { kind: "yearly", label: "Christmas Eve", month: 12 },
      description: "Everyone rolls.",
      participantIds: [SELF, "p_ana"],
      photoIds: ["media_1", "media_2"],
      startedYear: 1962,
    },
  );
});

test("toCreateTraditionRequest: the month is sent only when it makes sense", () => {
  assert.deepEqual(toCreateTraditionRequest(draft({ kind: "weekly", month: "3", label: "Sunday" })).when, {
    kind: "weekly",
    label: "Sunday",
  });
  assert.deepEqual(toCreateTraditionRequest(draft({ kind: "seasonal", month: "7" })).when, { kind: "seasonal", month: 7 });
  assert.deepEqual(toCreateTraditionRequest(draft({ month: "" })).when, { kind: "yearly" });
  assert.throws(() => toCreateTraditionRequest(draft({ category: null })));
});

test("a valid draft's request passes the contract's checks (mock backend)", async () => {
  const { createApiClient } = await import("../../../lib/api.ts");
  const { buildDemoSeed } = await import("../mock/demoSeed.ts");
  const fs = await import("node:fs");
  const sample = JSON.parse(
    fs.readFileSync(new URL("../../../public/samples/hymn_books.memory.json", import.meta.url), "utf8"),
  );
  const backend = mock.createMockBackend({ seed: () => buildDemoSeed(sample, new Date()), stepDelayMs: 0 });
  const api = createApiClient({ baseUrl: "http://localhost:8000", fetch: backend.fetch });
  await api.login({ email: "ana@example.com", password: "stitch-demo" });
  const me = await api.me();
  const fam = me.families[0];
  const d = draft({
    participantIds: fam.personId ? [fam.personId] : [],
    label: "Christmas Eve",
    month: "12",
    startedYear: String(thisYear),
    description: "x".repeat(DESCRIPTION_MAX),
    title: "t".repeat(TITLE_MAX),
  });
  assert.deepEqual(validateTraditionDraft(d), {});
  const saved = await api.createTradition(fam.id, toCreateTraditionRequest(d));
  assert.equal(saved.title, d.title);
  assert.deepEqual(saved.when, { kind: "yearly", label: "Christmas Eve", month: 12 });
  assert.equal(saved.startedYear, thisYear);
});

test("traditionServerErrors: 422 field errors land on the form's fields", () => {
  const { fields, rest } = traditionServerErrors({
    title: "String should have at least 1 character",
    label: "String should have at most 100 characters",
    month: "Input should be less than or equal to 12",
    photoIds: "List should have at most 10 items after validation, not 11",
    startedYear: "Started year must be between 1800 and this year",
    participantIds: "Input should be a valid string",
    somethingElse: "Extra inputs are not permitted",
  });
  assert.deepEqual(fields, {
    title: "String should have at least 1 character",
    label: "String should have at most 100 characters",
    month: "Input should be less than or equal to 12",
    photos: "List should have at most 10 items after validation, not 11",
    startedYear: "Started year must be between 1800 and this year",
    participantIds: "Input should be a valid string",
  });
  assert.equal(rest, "Extra inputs are not permitted");
  assert.deepEqual(traditionServerErrors({ when: "Input should be a valid dictionary" }).fields, {
    kind: "Input should be a valid dictionary",
  });
  assert.deepEqual(traditionServerErrors({ kind: "Input should be 'yearly'" }).fields, { kind: "Input should be 'yearly'" });
  assert.deepEqual(traditionServerErrors(undefined), { fields: {}, rest: null });
});

test("traditionServerErrors: what the real client makes of a mock 422", async () => {
  const body = JSON.stringify({
    detail: [
      { loc: ["body", "when", "label"], msg: "String should have at most 100 characters", type: "string_too_long" },
      { loc: ["body", "startedYear"], msg: "Value error, Started year must be between 1800 and this year", type: "value_error" },
      { loc: ["body", "photoIds"], msg: "List should have at most 10 items after validation, not 11", type: "too_long" },
    ],
  });
  const { createApiClient } = await import("../../../lib/api.ts");
  const api = createApiClient({
    baseUrl: "http://x",
    fetch: async () => new Response(body, { status: 422, headers: { "content-type": "application/json" } }),
  });
  let err;
  try {
    await api.createTradition("f", { title: "x", category: "food", when: { kind: "yearly" } });
  } catch (e) {
    err = e;
  }
  assert.ok(err instanceof ApiError);
  const { fields, rest } = traditionServerErrors(err.fieldErrors);
  assert.deepEqual(Object.keys(fields).sort(), ["label", "photos", "startedYear"]);
  assert.equal(fields.startedYear, "Started year must be between 1800 and this year");
  assert.equal(rest, null);
});

test("traditionSubmitErrorMessage: never blames the person, always says the draft is safe", () => {
  assert.equal(traditionSubmitErrorMessage({ status: 0 }), TRADITION_SUBMIT_ERRORS.network);
  assert.equal(traditionSubmitErrorMessage(null), TRADITION_SUBMIT_ERRORS.network);
  assert.equal(traditionSubmitErrorMessage({ status: 401 }), TRADITION_SUBMIT_ERRORS.signedOut);
  assert.equal(traditionSubmitErrorMessage({ status: 403 }), TRADITION_SUBMIT_ERRORS.forbidden);
  assert.equal(traditionSubmitErrorMessage({ status: 429 }), TRADITION_SUBMIT_ERRORS.busy);
  assert.equal(traditionSubmitErrorMessage({ status: 503, detail: "boom" }), TRADITION_SUBMIT_ERRORS.server);
  assert.equal(traditionSubmitErrorMessage({ status: 404, detail: "Family not found" }), "Family not found");
  assert.equal(traditionSubmitErrorMessage({ status: 400, detail: "  " }), TRADITION_SUBMIT_ERRORS.server);
  for (const msg of Object.values(TRADITION_SUBMIT_ERRORS)) assert.match(msg, /still here/);
});
