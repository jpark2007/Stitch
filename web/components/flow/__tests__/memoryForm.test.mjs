// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in memoryForm.ts. memoryForm.ts imports
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
  MEMORY_FIELDS,
  MEMORY_YEAR_MIN,
  QUESTION_MAX,
  SERVER_QUESTION_MAX,
  SUBMIT_ERRORS,
  SUGGESTED_QUESTIONS,
  TEXT_MAX,
  draftFromQuestion,
  emptyDraft,
  filterPeople,
  pickerSearchKey,
  questionAreaFor,
  submitErrorMessage,
  toSubmitFields,
  validateDraft,
} = await import("../memoryForm.ts");

const SELF = "p_rose";
const thisYear = new Date().getFullYear();

function draft(over = {}) {
  return { ...emptyDraft(SELF), text: "We met at the dance hall.", year: "1987", ...over };
}

test("SUGGESTED_QUESTIONS: four warm, distinct prompts", () => {
  assert.equal(SUGGESTED_QUESTIONS.length, 4);
  assert.equal(new Set(SUGGESTED_QUESTIONS).size, 4);
  for (const q of SUGGESTED_QUESTIONS) {
    assert.ok(q.endsWith("?"), q);
    assert.ok(q.length <= QUESTION_MAX, q);
  }
  assert.deepEqual([...MEMORY_FIELDS].sort(), ["participantIds", "photos", "question", "text", "year"]);
});

test("emptyDraft: blank fields, self is the only participant", () => {
  assert.deepEqual(emptyDraft(SELF), {
    question: "",
    text: "",
    year: "",
    participantIds: [SELF],
    memoryId: null,
  });
});

test("emptyDraft: self first and deduped, others kept in order", () => {
  const d = emptyDraft(SELF, {
    question: "How did you two meet?",
    year: "1962",
    participantIds: ["p_luis", SELF, "p_ana", "p_luis", "", SELF],
    memoryId: "m_1",
  });
  assert.deepEqual(d.participantIds, [SELF, "p_luis", "p_ana"]);
  assert.equal(d.question, "How did you two meet?");
  assert.equal(d.year, "1962");
  assert.equal(d.text, "");
  assert.equal(d.memoryId, "m_1");
});

test("draftFromQuestion: without a moment it sets only question and memoryId", () => {
  assert.deepEqual(draftFromQuestion({ text: "What was the wedding like?" }), {
    question: "What was the wedding like?",
    memoryId: null,
  });
  assert.deepEqual(draftFromQuestion({ text: "Where?", memoryId: null }, null), {
    question: "Where?",
    memoryId: null,
  });
});

test("draftFromQuestion: with a moment it adds the year as a string and its people", () => {
  const out = draftFromQuestion(
    { text: "What do you remember of the wedding?", memoryId: "m_wedding" },
    { year: 1962, participantIds: ["p_luis", "p_rosa", "p_luis"] },
  );
  assert.deepEqual(out, {
    question: "What do you remember of the wedding?",
    memoryId: "m_wedding",
    year: "1962",
    participantIds: ["p_luis", "p_rosa"],
  });
  // A moment without people leaves participants alone.
  const noPeople = draftFromQuestion({ text: "Q", memoryId: "m_2" }, { year: 2001 });
  assert.equal(noPeople.year, "2001");
  assert.equal("participantIds" in noPeople, false);
  // And it feeds emptyDraft with self put first.
  assert.deepEqual(emptyDraft(SELF, out).participantIds, [SELF, "p_luis", "p_rosa"]);
});

test("validateDraft: a good draft has no errors", () => {
  assert.deepEqual(validateDraft(draft(), SELF), {});
  assert.deepEqual(validateDraft(draft({ question: "  How did you two meet?  " }), SELF), {});
});

test("validateDraft: empty or blank text", () => {
  assert.equal(validateDraft(draft({ text: "" }), SELF).text, "Tell the story in your own words.");
  assert.equal(validateDraft(draft({ text: "  \n\t " }), SELF).text, "Tell the story in your own words.");
});

test("validateDraft: overlong text, counted in characters after trimming", () => {
  assert.equal(validateDraft(draft({ text: "a".repeat(TEXT_MAX) }), SELF).text, undefined);
  assert.equal(validateDraft(draft({ text: `  ${"a".repeat(TEXT_MAX)}  ` }), SELF).text, undefined);
  assert.equal(
    validateDraft(draft({ text: "a".repeat(TEXT_MAX + 1) }), SELF).text,
    "Keep your story to 10,000 characters or fewer.",
  );
  // 10,000 emoji are 10,000 characters, not 20,000 UTF-16 units.
  assert.equal(validateDraft(draft({ text: "🧵".repeat(TEXT_MAX) }), SELF).text, undefined);
});

test("validateDraft: question is optional but capped", () => {
  assert.equal(validateDraft(draft({ question: "" }), SELF).question, undefined);
  assert.equal(validateDraft(draft({ question: "q".repeat(QUESTION_MAX) }), SELF).question, undefined);
  assert.equal(
    validateDraft(draft({ question: "q".repeat(QUESTION_MAX + 1) }), SELF).question,
    "Keep the question to 300 characters or fewer.",
  );
});

test("validateDraft: year is required and must be sensible", () => {
  assert.equal(validateDraft(draft({ year: "" }), SELF).year, "Enter a year.");
  assert.equal(validateDraft(draft({ year: "   " }), SELF).year, "Enter a year.");
  assert.equal(validateDraft(draft({ year: "nineteen" }), SELF).year, "Enter the year as a number, like 1987.");
  assert.equal(validateDraft(draft({ year: "1700" }), SELF).year, "Pick a year from 1800 on.");
  assert.equal(validateDraft(draft({ year: String(thisYear + 1) }), SELF).year, "That year hasn't happened yet.");
  assert.equal(validateDraft(draft({ year: String(thisYear) }), SELF).year, undefined);
});

test("the year floor and the server question cap mirror api/routes/memories.py", () => {
  assert.equal(MEMORY_YEAR_MIN, 1800);
  assert.equal(SERVER_QUESTION_MAX, 1000);
});

test("validateDraft: the year floor is the API's 1800, the ceiling this year", () => {
  assert.equal(validateDraft(draft({ year: "1820" }), SELF).year, undefined);
  assert.equal(validateDraft(draft({ year: "1800" }), SELF).year, undefined);
  assert.equal(validateDraft(draft({ year: "1799" }), SELF).year, "Pick a year from 1800 on.");
  assert.equal(validateDraft(draft({ year: String(thisYear + 1) }), SELF).year, "That year hasn't happened yet.");
});

test("validateDraft: a year the person cannot edit is only checked for being a whole number", () => {
  const ro = { yearEditable: false };
  for (const year of ["1820", "1750", String(thisYear + 1), " 2019 "]) {
    assert.equal(validateDraft(draft({ year }), SELF, ro).year, undefined, year);
  }
  assert.equal(validateDraft(draft({ year: "abc" }), SELF, ro).year, "Enter the year as a number, like 1987.");
  assert.equal(validateDraft(draft({ year: "" }), SELF, ro).year, "Enter a year.");
  assert.equal(validateDraft(draft({ year: "1987.5" }), SELF, ro).year, "Use a whole year, like 1987.");
  // Explicit true behaves like the default.
  assert.equal(
    validateDraft(draft({ year: "1799" }), SELF, { yearEditable: true }).year,
    "Pick a year from 1800 on.",
  );
});

test("validateDraft: a question the person cannot edit is not capped", () => {
  const long = "q".repeat(QUESTION_MAX + 21);
  assert.equal(validateDraft(draft({ question: long }), SELF, { questionEditable: false }).question, undefined);
  assert.equal(
    validateDraft(draft({ question: long }), SELF).question,
    "Keep the question to 300 characters or fewer.",
  );
  assert.equal(
    validateDraft(draft({ question: long }), SELF, { questionEditable: true }).question,
    "Keep the question to 300 characters or fewer.",
  );
  // The other checks still run.
  assert.equal(
    validateDraft(draft({ question: long, text: "" }), SELF, { questionEditable: false }).text,
    "Tell the story in your own words.",
  );
});

test("validateDraft: participants must include self for a new moment", () => {
  const errors = validateDraft(draft({ participantIds: ["p_luis"] }), SELF);
  assert.equal(errors.participantIds, "You were there too: keep yourself in the list.");
  assert.equal(validateDraft(draft({ participantIds: [] }), SELF).participantIds, errors.participantIds);
  // A second telling keeps the moment's people, so the list is not checked.
  assert.equal(validateDraft(draft({ participantIds: [], memoryId: "m_1" }), SELF).participantIds, undefined);
});

test("validateDraft: reports every problem at once", () => {
  const errors = validateDraft(
    { question: "q".repeat(QUESTION_MAX + 1), text: "", year: "", participantIds: [], memoryId: null },
    SELF,
  );
  assert.deepEqual(Object.keys(errors).sort(), ["participantIds", "question", "text", "year"]);
});

test("toSubmitFields: trims, numbers the year, sends participants for a new moment", () => {
  const fields = toSubmitFields(
    draft({
      question: "  How did you two meet?  ",
      text: "\n  We met at the dance hall.  \n",
      year: " 1987 ",
      participantIds: [SELF, "p_luis", "p_luis", "p_ana", SELF],
    }),
  );
  assert.deepEqual(fields, {
    text: "We met at the dance hall.",
    year: 1987,
    question: "How did you two meet?",
    participantIds: [SELF, "p_luis", "p_ana"],
  });
  assert.equal(typeof fields.year, "number");
});

test("toSubmitFields: a blank question is omitted", () => {
  for (const question of ["", "   ", "\n"]) {
    const fields = toSubmitFields(draft({ question }));
    assert.equal("question" in fields, false, JSON.stringify(question));
  }
});

test("toSubmitFields: a question over the API's cap is clamped to it, ending in an ellipsis", () => {
  const fields = toSubmitFields(draft({ question: "🧵".repeat(1500) }));
  const chars = Array.from(fields.question);
  assert.equal(chars.length, SERVER_QUESTION_MAX);
  assert.equal(chars.at(-1), "…");
  assert.equal(chars.slice(0, -1).join(""), "🧵".repeat(SERVER_QUESTION_MAX - 1));

  const plain = toSubmitFields(draft({ question: "q".repeat(1500) }));
  assert.equal(plain.question, `${"q".repeat(999)}…`);
});

test("toSubmitFields: a question at or under the API's cap is sent unchanged", () => {
  for (const n of [999, SERVER_QUESTION_MAX]) {
    const question = "q".repeat(n);
    assert.equal(toSubmitFields(draft({ question })).question, question, String(n));
  }
  const astral = "🧵".repeat(SERVER_QUESTION_MAX);
  assert.equal(toSubmitFields(draft({ question: astral })).question, astral);
});

test("toSubmitFields: memoryId set means a second telling, so participantIds are omitted", () => {
  const fields = toSubmitFields(draft({ memoryId: "m_wedding", participantIds: [SELF, "p_luis"] }));
  assert.deepEqual(fields, { text: "We met at the dance hall.", year: 1987, memoryId: "m_wedding" });
  assert.equal("participantIds" in fields, false);
});

test("toSubmitFields: no memoryId key for a new moment", () => {
  const fields = toSubmitFields(draft());
  assert.equal("memoryId" in fields, false);
  assert.deepEqual(fields.participantIds, [SELF]);
  assert.equal("memoryId" in toSubmitFields(draft({ memoryId: "  " })), false);
});

test("questionAreaFor: a second telling shows no family questions, starters or question field", () => {
  for (const prefilledQuestion of [false, true]) {
    for (const openQuestions of [0, 1, 3]) {
      assert.deepEqual(questionAreaFor({ mode: "second", openQuestions, prefilledQuestion }), {
        showFamilyQuestions: false,
        showStarters: false,
        showQuestionField: false,
      });
    }
  }
});

test("questionAreaFor: a new memory with open questions shows them and hides the starters", () => {
  assert.deepEqual(questionAreaFor({ mode: "new", openQuestions: 2, prefilledQuestion: false }), {
    showFamilyQuestions: true,
    showStarters: false,
    showQuestionField: true,
  });
  assert.equal(questionAreaFor({ mode: "new", openQuestions: 1, prefilledQuestion: false }).showStarters, false);
});

test("questionAreaFor: a new memory with no open questions allows the starters in the question field", () => {
  assert.deepEqual(questionAreaFor({ mode: "new", openQuestions: 0, prefilledQuestion: false }), {
    showFamilyQuestions: false,
    showStarters: true,
    showQuestionField: true,
  });
});

test("questionAreaFor: a picked question steps the typed field and starters aside", () => {
  assert.deepEqual(questionAreaFor({ mode: "new", openQuestions: 2, prefilledQuestion: true }), {
    showFamilyQuestions: true,
    showStarters: false,
    showQuestionField: false,
  });
  assert.deepEqual(questionAreaFor({ mode: "new", openQuestions: 0, prefilledQuestion: true }), {
    showFamilyQuestions: false,
    showStarters: false,
    showQuestionField: false,
  });
});

test("questionAreaFor: a bad count reads as no questions", () => {
  for (const openQuestions of [NaN, -1, Infinity]) {
    const area = questionAreaFor({ mode: "new", openQuestions, prefilledQuestion: false });
    assert.equal(area.showFamilyQuestions, false, String(openQuestions));
    assert.equal(area.showStarters, true, String(openQuestions));
  }
});

// ---- submitErrorMessage -------------------------------------------------------------

test("submitErrorMessage: no connection, signed out, forbidden, busy and server errors each say what happened", () => {
  assert.equal(submitErrorMessage({ status: 0, detail: "We can't reach Stitch" }), SUBMIT_ERRORS.network);
  assert.equal(submitErrorMessage({ status: 401, detail: "Not signed in" }), SUBMIT_ERRORS.signedOut);
  assert.equal(submitErrorMessage({ status: 403, detail: "Forbidden" }), SUBMIT_ERRORS.forbidden);
  assert.equal(submitErrorMessage({ status: 429, detail: "Slow down" }), SUBMIT_ERRORS.busy);
  for (const status of [500, 502, 503]) {
    assert.equal(submitErrorMessage({ status, detail: "Internal Server Error" }), SUBMIT_ERRORS.server);
  }
});

test("submitErrorMessage: every message says the story is still here", () => {
  for (const msg of Object.values(SUBMIT_ERRORS)) assert.match(msg, /still here/);
});

test("submitErrorMessage: a 404 on a second telling means its moment has gone", () => {
  assert.equal(submitErrorMessage({ status: 404, detail: "Not found" }, true), SUBMIT_ERRORS.momentGone);
  assert.equal(submitErrorMessage({ status: 404, detail: "Not found" }, false), "Not found");
});

test("submitErrorMessage: other statuses show the server's own sentence, else the server message", () => {
  assert.equal(submitErrorMessage({ status: 409, detail: "That run is already going." }), "That run is already going.");
  assert.equal(submitErrorMessage({ status: 409, detail: "  " }), SUBMIT_ERRORS.server);
  assert.equal(submitErrorMessage(null), SUBMIT_ERRORS.network);
  assert.equal(submitErrorMessage({ status: Number.NaN }), SUBMIT_ERRORS.network);
});

// ---- filterPeople -------------------------------------------------------------------

const family = [
  { id: "rosa", name: "Rosa" },
  { id: "jose", name: "José Mao" },
  { id: "ana", name: "Ana  Mao" },
  { id: "sam", name: "Sam" },
  { id: "lily", name: "Lily" },
];

test("filterPeople: a blank search keeps everyone, as a copy", () => {
  const all = filterPeople(family, "   ");
  assert.deepEqual(all, family);
  assert.notEqual(all, family);
});

test("filterPeople: matches anywhere in the name, ignoring case, accents and extra spaces", () => {
  assert.deepEqual(filterPeople(family, "jose").map((p) => p.id), ["jose"]);
  assert.deepEqual(filterPeople(family, "MAO").map((p) => p.id), ["jose", "ana"]);
  assert.deepEqual(filterPeople(family, "ana mao").map((p) => p.id), ["ana"]);
  assert.deepEqual(filterPeople(family, "il").map((p) => p.id), ["lily"]);
});

test("filterPeople: the picked stay in view, in family order", () => {
  assert.deepEqual(filterPeople(family, "lily", ["sam"]).map((p) => p.id), ["sam", "lily"]);
  assert.deepEqual(filterPeople(family, "zzz", ["rosa"]).map((p) => p.id), ["rosa"]);
});

// ---- pickerSearchKey ------------------------------------------------------------------

test("pickerSearchKey: Enter never falls through to the form", () => {
  // Blank search, several matches, no match: Enter is swallowed, nothing changes.
  assert.deepEqual(pickerSearchKey("Enter", "", family), { action: "stay" });
  assert.deepEqual(pickerSearchKey("Enter", "   ", family), { action: "stay" });
  assert.deepEqual(pickerSearchKey("Enter", "mao", family), { action: "stay" });
  assert.deepEqual(pickerSearchKey("Enter", "zzz", family), { action: "stay" });
});

test("pickerSearchKey: Enter with exactly one match picks that person", () => {
  assert.deepEqual(pickerSearchKey("Enter", "Sof", [...family, { id: "sofia", name: "Sofía" }]), {
    action: "pick",
    pickId: "sofia",
  });
  assert.deepEqual(pickerSearchKey("Enter", "jose", family), { action: "pick", pickId: "jose" });
  // Already picked (or locked, passed in pickedIds): never unpicks, just stays.
  assert.deepEqual(pickerSearchKey("Enter", "jose", family, ["jose"]), { action: "stay" });
});

test("pickerSearchKey: Escape clears a search, other keys are left alone", () => {
  assert.deepEqual(pickerSearchKey("Escape", "ro", family), { action: "clear" });
  assert.equal(pickerSearchKey("Escape", "", family), null);
  assert.equal(pickerSearchKey("a", "ro", family), null);
  assert.equal(pickerSearchKey("Tab", "ro", family), null);
});
