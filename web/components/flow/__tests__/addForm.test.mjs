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
  addRelativeHref,
  addedFromQuery,
  carriedPhotosNote,
  carryInto,
  hasTold,
  momentsFromThreads,
  preselectAdded,
  questionForMemory,
  readAddQuery,
  readCarry,
  saveCarry,
  sentDraftFromSubmit,
  tellingFromSubmit,
  withoutAdded,
  clearTypedDraft,
  draftForForm,
  questionIdFromQuery,
  readTypedDraft,
  saveTypedDraft,
  withQuestionId,
} = await import("../addPage.ts");
const { withAddedParam } = await import("../relativeForm.ts");
const { safeNext } = await import("../session.ts");

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

describe("adding a relative from /add and coming back", () => {
  function storage() {
    const map = new Map();
    return {
      map,
      getItem: (k) => (map.has(k) ? map.get(k) : null),
      setItem: (k, v) => void map.set(k, String(v)),
      removeItem: (k) => void map.delete(k),
    };
  }

  /** What the add-relative page does with its `next` once the person is saved. */
  function addRelative(href, personId) {
    const next = safeNext(new URL(href, "http://stitch.invalid").searchParams.get("next"));
    assert.ok(next, "the add-relative page keeps the next it was given");
    return withAddedParam(next, personId);
  }

  test("the draft and the picked general question come back, with the new person ticked", () => {
    const st = storage();
    const people = ["mom", "dad", "lily"];
    // Ana types, picks the general prompt, ticks Lily, attaches two photos, then follows the link.
    const before = emptyDraft(SELF, carryInto(draftFromQuestion(questions[0], null), typed, false));
    saveCarry(st, {
      familyId: "demo_family",
      text: before.text,
      year: before.year,
      participantIds: before.participantIds.filter((id) => id !== SELF),
      questionId: questions[0].id,
      question: null,
      photoCount: 2,
    });
    const href = addRelativeHref("demo_family");
    assert.equal(href, "/families/demo_family/people/new?next=%2Fadd%3Ffamily%3Ddemo_family");

    // Aunt Rosa is saved; the add-relative page goes back with ?added=.
    const back = addRelative(href, "aunt_rosa");
    assert.equal(back, "/add?family=demo_family&added=aunt_rosa");
    const search = back.slice(back.indexOf("?"));
    assert.equal(readAddQuery(search).familyId, "demo_family");

    // /add reads the carry, re-picks the question, ticks Aunt Rosa.
    const c = readCarry(st, "demo_family");
    const ids = preselectAdded(c.participantIds, addedFromQuery(search), [...people, "aunt_rosa"]);
    const q = questions.find((x) => x.id === c.questionId);
    const after = emptyDraft(
      SELF,
      carryInto(draftFromQuestion(q, null), { text: c.text, year: c.year, participantIds: ids, question: c.question }, false),
    );
    assert.equal(after.text, typed.text);
    assert.equal(after.year, "2018");
    assert.equal(after.question, questions[0].text);
    assert.deepEqual(after.participantIds, ["mom", "lily", "aunt_rosa"]);
    assert.match(carriedPhotosNote(c.photoCount), /photos didn’t come along/);
    // Then ?added= leaves the URL, the family stays.
    assert.equal(withoutAdded(search), "/add?family=demo_family");
    assert.deepEqual(toSubmitFields(after).participantIds, ["mom", "lily", "aunt_rosa"]);
  });

  test("an id that is not in the family is ignored; nothing carried gives just the story", () => {
    const back = addRelative(addRelativeHref("demo_family"), "nobody_here");
    const ids = preselectAdded(null, addedFromQuery(back.slice(back.indexOf("?"))), ["mom", "dad"]);
    assert.equal(ids, null);
    const draft = emptyDraft(SELF, carryInto(undefined, { text: "", year: "", participantIds: ids }, false));
    assert.deepEqual(draft.participantIds, [SELF]);
  });
});

describe("reloading /add in the same tab", () => {
  function storage() {
    const map = new Map();
    return {
      map,
      getItem: (k) => (map.has(k) ? map.get(k) : null),
      setItem: (k, v) => void map.set(k, String(v)),
      removeItem: (k) => void map.delete(k),
    };
  }

  /** What AddForm's onDraftChange keeps for family `fid` (baseMemoryId: the ?memoryId= moment). */
  function keep(st, fid, draft, { baseMemoryId = null, pickedId = null, photos = 0 } = {}) {
    const second = Boolean(draft.memoryId);
    saveTypedDraft(st, {
      familyId: fid,
      memoryId: baseMemoryId,
      questionId: pickedId,
      text: draft.text,
      year: second ? "" : draft.year,
      participantIds: second ? [] : draft.participantIds.filter((id) => id !== SELF),
      question: pickedId || second ? "" : draft.question,
      photoCount: photos,
    });
  }

  /** What AddForm does on mount after a reload of `url`: the pick and the draft it starts from. */
  function reload(st, url) {
    const search = url.slice(url.indexOf("?"));
    const { familyId, memoryId } = readAddQuery(search);
    const questionId = questionIdFromQuery(search);
    const kept = draftForForm(readTypedDraft(st, familyId), { memoryId, questionId });
    const pickId = questionId ?? kept?.questionId ?? null;
    const q = pickId ? questions.find((x) => x.id === pickId) : null;
    const carried = {
      text: kept?.text ?? "",
      year: kept?.year ?? "",
      participantIds: kept?.participantIds.length ? kept.participantIds : null,
      question: kept?.question ?? "",
    };
    return { q, kept, ...(q ? pick(q, carried) : { moment: null, draft: emptyDraft(SELF, carryInto(undefined, carried, false)) }) };
  }

  test("a picked moment's question and the story come back: still the second telling", () => {
    const st = storage();
    // Ana picks the wedding question and starts typing; the page puts the pick in the URL.
    const url = withQuestionId("?family=demo_family", "q_wedding");
    const { draft } = pick(questions[1], { text: "His flower fell right off.", year: "", participantIds: null });
    keep(st, "demo_family", draft, { pickedId: "q_wedding" });
    const back = reload(st, url);
    assert.equal(back.q.id, "q_wedding");
    assert.equal(back.moment.title, "Sam's wedding in the orchard");
    assert.equal(back.draft.memoryId, "demo_sam_wedding_2019");
    assert.equal(back.draft.year, "2019");
    assert.equal(back.draft.text, "His flower fell right off.");
    assert.equal(back.draft.question, questions[1].text);
  });

  test("a new memory's story, year, people and typed question come back; photos are counted", () => {
    const st = storage();
    const draft = { ...emptyDraft(SELF), text: "The porch light", year: "1987", participantIds: [SELF, "lily"], question: "What was the porch like?" };
    keep(st, "demo_family", draft, { photos: 1 });
    const back = reload(st, "/add?family=demo_family");
    assert.equal(back.q, null);
    assert.deepEqual(
      { text: back.draft.text, year: back.draft.year, question: back.draft.question },
      { text: "The porch light", year: "1987", question: "What was the porch like?" },
    );
    assert.deepEqual(back.draft.participantIds, [SELF, "lily"]);
    assert.match(carriedPhotosNote(back.kept.photoCount), /photo didn’t come along/);
  });

  test("a second telling from ?memoryId= gets its story back, and only on that moment", () => {
    const st = storage();
    const draft = emptyDraft(SELF, { text: "My side of it.", memoryId: "sample_hymn_books", year: "1988" });
    keep(st, "demo_family", draft, { baseMemoryId: "sample_hymn_books" });
    const search = "?family=demo_family&memoryId=sample_hymn_books";
    assert.equal(draftForForm(readTypedDraft(st, "demo_family"), { memoryId: "sample_hymn_books", questionId: null }).text, "My side of it.");
    assert.equal(draftForForm(readTypedDraft(st, "demo_family"), { memoryId: null, questionId: null }), null);
    assert.equal(questionIdFromQuery(search), null);
  });

  test("letting the question go drops it from the URL; sending drops the draft", () => {
    const st = storage();
    const url = withQuestionId("?family=demo_family&questionId=q_wedding", null);
    assert.equal(url, "/add?family=demo_family");
    keep(st, "demo_family", { ...emptyDraft(SELF), text: "Kept" });
    clearTypedDraft(st, "demo_family");
    const back = reload(st, url);
    assert.equal(back.draft.text, "");
    assert.equal(back.q, null);
  });

  test("a fresh link to another question does not take a draft typed for the first", () => {
    const st = storage();
    keep(st, "demo_family", { ...emptyDraft(SELF), text: "About the wedding" }, { pickedId: "q_wedding" });
    const back = reload(st, withQuestionId("?family=demo_family", "q_born"));
    assert.equal(back.q.id, "q_born");
    assert.equal(back.draft.text, "");
  });
});
