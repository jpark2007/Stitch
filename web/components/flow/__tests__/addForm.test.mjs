// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// The add-a-memory form's logic end to end, the way app/add/AddForm.tsx wires
// it: the family's moments from GET /threads, a pending question picked (a
// general prompt or a moment's), what was typed carried into the new form,
// the checks, and the fields sent. memoryForm.ts imports "./validation"
// without an extension, so this file adds the same resolve hook as
// memoryForm.test.mjs before loading it.
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

const { draftFromQuestion, emptyDraft, questionAreaFor, toSubmitFields, validateDraft } = await import(
  "../memoryForm.ts"
);
const {
  carryInto,
  hasTold,
  momentsFromThreads,
  questionForMemory,
  readCarry,
  saveCarry,
  sentDraftFromSubmit,
  tellingFromSubmit,
} = await import("../addPage.ts");

// The seed's shape: Ana (mom) has a general prompt and a question about Sam's wedding.
const SELF = "mom";
const threads = {
  people: [],
  moments: [
    {
      id: "demo_sam_wedding_2019",
      title: "Sam's wedding in the orchard",
      year: 2019,
      participantIds: ["sam", "lily", "mom", "dad"],
      tellingAuthorIds: ["sam", "dad"],
    },
    {
      id: "sample_hymn_books",
      title: "How I met your mother",
      year: 1988,
      participantIds: ["dad", "mom"],
      tellingAuthorIds: ["dad", "mom"],
    },
  ],
};
const questions = [
  { id: "q_born", familyId: "demo_family", personId: SELF, text: "What do you remember about the day Lily was born?", memoryId: null },
  {
    id: "q_wedding",
    familyId: "demo_family",
    personId: SELF,
    text: "Sam says you hugged him so hard at the wedding that his flower fell off. How do you remember that day?",
    memoryId: "demo_sam_wedding_2019",
  },
];

/** What the page does on a pick: the question's draft, with what was typed carried in. */
function pick(q, typed) {
  const moments = momentsFromThreads(threads);
  const moment = q.memoryId ? (moments.get(q.memoryId) ?? null) : null;
  const base = draftFromQuestion(moment ? q : { ...q, memoryId: null }, moment);
  return { moment, draft: emptyDraft(SELF, carryInto(base, typed, moment !== null)) };
}

const typed = { text: "  I hugged him so hard the flower fell off.  ", year: "2018", participantIds: ["mom", "lily"] };

describe("picking a general prompt", () => {
  test("starts a new memory with the question; the story, year and people come along", () => {
    const { moment, draft } = pick(questions[0], typed);
    assert.equal(moment, null);
    assert.equal(draft.question, questions[0].text);
    assert.equal(draft.memoryId, null);
    assert.equal(draft.year, "2018");
    assert.deepEqual(draft.participantIds, ["mom", "lily"]);
    assert.deepEqual(validateDraft(draft, SELF), {});
    assert.deepEqual(toSubmitFields(draft), {
      text: "I hugged him so hard the flower fell off.",
      year: 2018,
      question: questions[0].text,
      participantIds: ["mom", "lily"],
    });
  });

  test("the question field steps aside once a pick fills the question", () => {
    const area = questionAreaFor({ mode: "new", openQuestions: questions.length, prefilledQuestion: true });
    assert.deepEqual(area, { showFamilyQuestions: true, showStarters: false, showQuestionField: false });
  });
});

describe("picking a moment's question (a second telling)", () => {
  test("joins the moment: its year and people, the story carried, no people sent", () => {
    const { moment, draft } = pick(questions[1], typed);
    assert.equal(moment.title, "Sam's wedding in the orchard");
    assert.equal(draft.memoryId, "demo_sam_wedding_2019");
    assert.equal(draft.year, "2019");
    assert.deepEqual(draft.participantIds, ["mom", "sam", "lily", "dad"]);
    assert.equal(draft.text, typed.text);
    const fields = toSubmitFields(draft);
    assert.deepEqual(fields, {
      text: "I hugged him so hard the flower fell off.",
      year: 2019,
      question: questions[1].text,
      memoryId: "demo_sam_wedding_2019",
    });
    // The slip above the run names the moment; the kept draft remembers it.
    assert.equal(tellingFromSubmit(fields, 0, moment.title).secondTellingOf, "Sam's wedding in the orchard");
    assert.equal(sentDraftFromSubmit(fields, 0).memoryId, "demo_sam_wedding_2019");
  });

  test("a first telling of it is not a retelling; a teller who told it before is", () => {
    const moments = momentsFromThreads(threads);
    assert.equal(hasTold(moments.get("demo_sam_wedding_2019"), SELF), false);
    assert.equal(hasTold(moments.get("sample_hymn_books"), SELF), true);
  });

  test("a question about a moment that is not in the family starts a new memory", () => {
    const stray = { ...questions[1], id: "q_gone", memoryId: "deleted_moment" };
    const { moment, draft } = pick(stray, { text: "", year: "", participantIds: null });
    assert.equal(moment, null);
    assert.equal(draft.memoryId, null);
    assert.equal(draft.question, stray.text);
    assert.equal(validateDraft(draft, SELF).year, "Enter a year.");
  });
});

describe("?memoryId= (the second route)", () => {
  test("finds the moment and its question for this person", () => {
    const moments = momentsFromThreads(threads);
    const m = moments.get("demo_sam_wedding_2019");
    const q = questionForMemory(questions, "demo_sam_wedding_2019");
    assert.equal(q.id, "q_wedding");
    const draft = emptyDraft(SELF, draftFromQuestion({ text: q.text, memoryId: "demo_sam_wedding_2019" }, m));
    assert.equal(validateDraft({ ...draft, text: "My version." }, SELF).text, undefined);
  });

  test("a moment with no question for this person still opens, with no question sent", () => {
    const m = momentsFromThreads(threads).get("sample_hymn_books");
    assert.equal(questionForMemory(questions, "sample_hymn_books"), null);
    const draft = emptyDraft(SELF, draftFromQuestion({ text: "", memoryId: "sample_hymn_books" }, m));
    const fields = toSubmitFields({ ...draft, text: "I dropped every one of them." });
    assert.deepEqual(fields, { text: "I dropped every one of them.", year: 1988, memoryId: "sample_hymn_books" });
  });

  test("an id the person cannot see (or that does not exist) is simply not there", () => {
    assert.equal(momentsFromThreads(threads).get("someone_elses_moment"), undefined);
  });
});

describe("switching family keeps the story", () => {
  test("the story and year reach the other family's blank form; the people do not", () => {
    const map = new Map();
    const storage = {
      getItem: (k) => (map.has(k) ? map.get(k) : null),
      setItem: (k, v) => void map.set(k, String(v)),
      removeItem: (k) => void map.delete(k),
    };
    saveCarry(storage, { familyId: "okafors", text: typed.text, year: typed.year });
    const carried = readCarry(storage, "okafors");
    const draft = emptyDraft("sam_in_okafors", carryInto(undefined, { ...carried, participantIds: null }, false));
    assert.equal(draft.text, typed.text);
    assert.equal(draft.year, "2018");
    assert.deepEqual(draft.participantIds, ["sam_in_okafors"]);
  });
});
