// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// /add loading again when the window comes back (app/add/AddForm.tsx): when to
// reload, how a fresh load lands under the form already on screen, and how a
// picked question finds its moment. addPage.ts only has type imports today,
// but this adds the same resolve hook as the other tests so a later value
// import without an extension still loads.
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

const { ADD_REFRESH_MIN_GAP_MS, knownMoment, mergeRefreshed, shouldRefreshOnShow } = await import("../addPage.ts");

describe("shouldRefreshOnShow", () => {
  const shown = { visible: true, ready: true, inFlight: false, lastLoadedAt: 10_000, now: 10_000 + 5_000 };

  test("the gap is two seconds", () => {
    assert.equal(ADD_REFRESH_MIN_GAP_MS, 2000);
  });

  test("reloads when shown, ready, idle and past the gap", () => {
    assert.equal(shouldRefreshOnShow(shown), true);
  });

  test("a hidden window never reloads", () => {
    assert.equal(shouldRefreshOnShow({ ...shown, visible: false }), false);
  });

  test("not before the form is up (the first load is still running or failed)", () => {
    assert.equal(shouldRefreshOnShow({ ...shown, ready: false }), false);
  });

  test("not while a load is running", () => {
    assert.equal(shouldRefreshOnShow({ ...shown, inFlight: true }), false);
  });

  test("not within the gap after the last load", () => {
    assert.equal(shouldRefreshOnShow({ ...shown, now: 10_000 + 1_999 }), false);
    assert.equal(shouldRefreshOnShow({ ...shown, now: 10_000 }), false);
  });

  test("again once the gap has passed (exactly the gap counts)", () => {
    assert.equal(shouldRefreshOnShow({ ...shown, now: 10_000 + ADD_REFRESH_MIN_GAP_MS }), true);
    assert.equal(shouldRefreshOnShow({ ...shown, now: 10_000 + ADD_REFRESH_MIN_GAP_MS + 1 }), true);
  });

  test("nothing loaded yet (null) reloads straight away", () => {
    assert.equal(shouldRefreshOnShow({ ...shown, lastLoadedAt: null, now: 0 }), true);
  });

  test("a custom gap is used when given", () => {
    assert.equal(shouldRefreshOnShow({ ...shown, now: 10_000 + 500, minGapMs: 500 }), true);
    assert.equal(shouldRefreshOnShow({ ...shown, now: 10_000 + 499, minGapMs: 500 }), false);
    assert.equal(shouldRefreshOnShow({ ...shown, now: 10_000 + 2_500, minGapMs: 5_000 }), false);
  });
});

describe("mergeRefreshed", () => {
  const base = { kind: "new", note: null };
  const oldMoments = new Map([["m1", { title: "Old" }]]);
  const prev = {
    base,
    people: [{ id: "p1" }],
    questions: [{ id: "q1" }],
    moments: oldMoments,
    addedFor: null,
  };

  test("keeps the base itself, so the form's key and what is typed stay", () => {
    const out = mergeRefreshed(prev, { people: [], questions: [], moments: null }, null);
    assert.equal(out.base, base);
  });

  test("takes the fresh people and questions", () => {
    const people = [{ id: "p1" }, { id: "p2" }];
    const questions = [{ id: "q2" }];
    const out = mergeRefreshed(prev, { people, questions, moments: new Map() }, null);
    assert.equal(out.people, people);
    assert.equal(out.questions, questions);
  });

  test("takes the fresh moments when they were read", () => {
    const moments = new Map([["m2", { title: "New" }]]);
    const out = mergeRefreshed(prev, { people: [], questions: [], moments }, null);
    assert.equal(out.moments, moments);
  });

  test("keeps the old moments when the threads could not be read this time", () => {
    const out = mergeRefreshed(prev, { people: [], questions: [], moments: null }, null);
    assert.equal(out.moments, oldMoments);
  });

  test("stays null when the moments were never read", () => {
    const out = mergeRefreshed({ ...prev, moments: null }, { people: [], questions: [], moments: null }, null);
    assert.equal(out.moments, null);
  });

  test("records the ?added= person the load was for", () => {
    const out = mergeRefreshed(prev, { people: [], questions: [], moments: null }, "p9");
    assert.equal(out.addedFor, "p9");
  });

  test("does not change the old data", () => {
    mergeRefreshed(prev, { people: [{ id: "x" }], questions: [], moments: new Map() }, "p9");
    assert.deepEqual(prev.people, [{ id: "p1" }]);
    assert.equal(prev.moments, oldMoments);
    assert.equal(prev.addedFor, null);
  });
});

describe("knownMoment", () => {
  const a = { title: "A" };
  const b = { title: "B" };

  test("a moment in the fresh list", () => {
    assert.equal(knownMoment("m1", new Map(), new Map([["m1", a]])), a);
  });

  test("the fresh list wins over one kept as missing (it 404'd earlier, exists now)", () => {
    assert.equal(knownMoment("m1", new Map([["m1", null]]), new Map([["m1", a]])), a);
  });

  test("the fresh list wins over an older kept copy", () => {
    assert.equal(knownMoment("m1", new Map([["m1", b]]), new Map([["m1", a]])), a);
  });

  test("one fetched before, not in the fresh list", () => {
    assert.equal(knownMoment("m1", new Map([["m1", a]]), new Map()), a);
    assert.equal(knownMoment("m1", new Map([["m1", a]]), null), a);
  });

  test("one kept as missing stays missing when nothing fresher says otherwise", () => {
    assert.equal(knownMoment("m1", new Map([["m1", null]]), null), null);
  });

  test("the moments are loaded and it is not among them: gone (null)", () => {
    assert.equal(knownMoment("m1", new Map(), new Map([["m2", b]])), null);
    assert.equal(knownMoment("m1", new Map(), new Map()), null);
  });

  test("nothing loaded and nothing kept: unknown (undefined), so fetch it", () => {
    assert.equal(knownMoment("m1", new Map(), null), undefined);
  });
});
