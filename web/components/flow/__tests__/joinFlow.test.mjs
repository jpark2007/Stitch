// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in joinFlow.ts. It imports "@/lib/api",
// so this file first registers a resolve hook (for this test process only)
// that maps "@/x" to web/x.ts, then loads both modules dynamically. ApiError
// comes from the same file URL, so instanceof checks line up.
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";

const WEB_ROOT = new URL("../../../", import.meta.url).href;

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const target = new URL(specifier.slice(2), WEB_ROOT).href;
      return nextResolve(/\.[cm]?[jt]sx?$/.test(target) ? target : `${target}.ts`, context);
    }
    return nextResolve(specifier, context);
  },
});

const { ApiError } = await import("@/lib/api");
const { addedSelf, familyPhrase, isGoneError, joinView, relationshipPeople, stepAfterJoin } =
  await import("../joinFlow.ts");

const FID = "fam_riveras";
const PREVIEW = {
  familyId: FID,
  familyName: "The Riveras",
  unclaimedPeople: [{ id: "rosa", name: "Rosa", born: 1938, color: "#7b5ea7" }],
};
const USER = { id: "u_new", email: "new@example.com", name: "Nina" };
const ME_OUTSIDER = { user: USER, families: [{ id: "fam_other", name: "The Maos", role: "admin" }] };
const ME_MEMBER = {
  user: USER,
  families: [
    { id: "fam_other", name: "The Maos", role: "admin" },
    { id: FID, name: "The Riveras", role: "member", personId: "nina" },
  ],
};
const JOINED = {
  family: { id: FID, name: "The Riveras", role: "member", personId: "nina" },
  person: { id: "nina", familyId: FID, userId: "u_new", name: "Nina", character: { color: "#c96f4a" } },
};

/** Inputs for a signed-in visitor who has loaded everything and not joined yet. */
function inputs(over = {}) {
  return {
    preview: PREVIEW,
    previewError: null,
    me: ME_OUTSIDER,
    meStatus: "signed-in",
    joined: null,
    step: "choose",
    ...over,
  };
}

// ---- joinView ---------------------------------------------------------------

describe("joinView", () => {
  test("loading while either load is outstanding", () => {
    assert.equal(joinView(inputs({ preview: null })), "loading");
    assert.equal(joinView(inputs({ me: null, meStatus: "loading" })), "loading");
    assert.equal(joinView(inputs({ preview: null, me: null, meStatus: "loading" })), "loading");
    // Signed out but the preview is still on its way: nothing to show yet.
    assert.equal(joinView(inputs({ preview: null, me: null, meStatus: "signed-out" })), "loading");
  });

  test("gone for a 404 or 410 preview, signed in or out, even before me answers", () => {
    for (const status of [404, 410]) {
      const err = new ApiError(status);
      assert.equal(joinView(inputs({ preview: null, previewError: err })), "gone");
      assert.equal(
        joinView(inputs({ preview: null, previewError: err, me: null, meStatus: "signed-out" })),
        "gone",
      );
      assert.equal(
        joinView(inputs({ preview: null, previewError: err, me: null, meStatus: "loading" })),
        "gone",
      );
      // A gone link wins over a failed me(): nothing on the page could help.
      assert.equal(
        joinView(inputs({ preview: null, previewError: err, me: null, meStatus: "error" })),
        "gone",
      );
    }
  });

  test("error for a preview failure that is not gone", () => {
    for (const err of [new ApiError(500), new ApiError(0), new ApiError(409), new Error("boom")]) {
      assert.equal(joinView(inputs({ preview: null, previewError: err })), "error");
    }
  });

  test("error when me() fails with anything but 401", () => {
    assert.equal(joinView(inputs({ me: null, meStatus: "error" })), "error");
    assert.equal(joinView(inputs({ preview: null, me: null, meStatus: "error" })), "error");
  });

  test("signed-out shows the invitation once the preview is in", () => {
    assert.equal(joinView(inputs({ me: null, meStatus: "signed-out" })), "signed-out");
  });

  test("signed-out takes priority over a family we cannot check membership of", () => {
    // No me to compare against: never guess already-member or choose.
    assert.equal(joinView(inputs({ me: null, meStatus: "signed-out" })), "signed-out");
    // A stale me left over from before signing out does not count either.
    assert.equal(joinView(inputs({ me: ME_MEMBER, meStatus: "signed-out" })), "signed-out");
    // Nor does a join result: the session is what decides.
    assert.equal(
      joinView(inputs({ me: null, meStatus: "signed-out", joined: JOINED, step: "done" })),
      "signed-out",
    );
  });

  test("already-member when me.families holds the invite's family id", () => {
    assert.equal(joinView(inputs({ me: ME_MEMBER })), "already-member");
  });

  test("membership is judged by id, not by name", () => {
    const sameNameOtherFamily = {
      user: USER,
      families: [{ id: "fam_elsewhere", name: "The Riveras", role: "admin" }],
    };
    assert.equal(joinView(inputs({ me: sameNameOtherFamily })), "choose");
  });

  test("choose for a signed-in visitor who is not a member", () => {
    assert.equal(joinView(inputs()), "choose");
    assert.equal(joinView(inputs({ me: { user: USER, families: [] } })), "choose");
  });

  test("a join made on this visit moves through its own steps", () => {
    assert.equal(joinView(inputs({ joined: JOINED, step: "relationships" })), "relationships");
    assert.equal(joinView(inputs({ joined: JOINED, step: "done" })), "done");
  });

  test("after joining, a refreshed me listing the family does not flip to already-member", () => {
    assert.equal(joinView(inputs({ me: ME_MEMBER, joined: JOINED, step: "relationships" })), "relationships");
    assert.equal(joinView(inputs({ me: ME_MEMBER, joined: JOINED, step: "done" })), "done");
    assert.equal(joinView(inputs({ me: ME_MEMBER, joined: JOINED, step: "choose" })), "choose");
  });

  test("a step past choose without a join result stays on the choice", () => {
    assert.equal(joinView(inputs({ step: "done" })), "choose");
    assert.equal(joinView(inputs({ step: "relationships" })), "choose");
  });
});

// ---- isGoneError ------------------------------------------------------------

describe("isGoneError", () => {
  test("404 and 410 are gone", () => {
    assert.equal(isGoneError(new ApiError(404)), true);
    assert.equal(isGoneError(new ApiError(410, "That link is no longer available.")), true);
  });

  test("other statuses are not", () => {
    for (const status of [0, 400, 401, 403, 409, 422, 500, 503]) {
      assert.equal(isGoneError(new ApiError(status)), false, `status ${status}`);
    }
  });

  test("anything that is not an ApiError is not", () => {
    for (const err of [
      null,
      undefined,
      "404",
      404,
      new Error("404"),
      { status: 404 },
      { name: "ApiError", status: 410, detail: "gone" },
      Object.assign(new Error("gone"), { status: 410 }),
    ]) {
      assert.equal(isGoneError(err), false, String(err));
    }
  });
});

// ---- addedSelf and stepAfterJoin --------------------------------------------

describe("addedSelf", () => {
  test("a claim did not add anyone", () => {
    assert.equal(addedSelf({ personId: "rosa" }), false);
    assert.equal(addedSelf({ personId: "rosa", person: null }), false);
  });

  test("an add did", () => {
    assert.equal(addedSelf({ person: { name: "Nina", born: 1999 } }), true);
    assert.equal(addedSelf({ personId: null, person: { name: "Nina", born: null } }), true);
  });
});

describe("stepAfterJoin", () => {
  const others = [{ id: "rosa", name: "Rosa" }];

  test("adding yourself with others on the tree asks for relationships", () => {
    assert.equal(stepAfterJoin({ person: { name: "Nina" } }, others), "relationships");
  });

  test("adding yourself to an empty tree goes to done", () => {
    assert.equal(stepAfterJoin({ person: { name: "Nina" } }, []), "done");
  });

  test("a claim always goes to done", () => {
    assert.equal(stepAfterJoin({ personId: "rosa" }, others), "done");
    assert.equal(stepAfterJoin({ personId: "rosa" }, []), "done");
  });
});

// ---- relationshipPeople -----------------------------------------------------

describe("relationshipPeople", () => {
  const tree = {
    people: [
      { id: "rosa", familyId: FID, name: "Rosa", born: 1938, character: { color: "#7b5ea7" } },
      { id: "nina", familyId: FID, userId: "u_new", name: "Nina", born: 1999, character: { color: "#c96f4a" } },
      { id: "luis", familyId: FID, name: "Luis", character: { color: "#5f7f52", item: "flat cap" } },
      { id: "kid", familyId: FID, name: "Kid", born: null, character: null },
      { id: "blank", familyId: FID, name: "Blank", born: 2010, character: { color: "" } },
    ],
  };

  test("excludes self and keeps tree order", () => {
    const out = relationshipPeople(tree, "nina");
    assert.deepEqual(
      out.map((p) => p.id),
      ["rosa", "luis", "kid", "blank"],
    );
  });

  test("maps name, born and character color", () => {
    assert.deepEqual(relationshipPeople(tree, "nina"), [
      { id: "rosa", name: "Rosa", born: 1938, color: "#7b5ea7" },
      { id: "luis", name: "Luis", born: null, color: "#5f7f52" },
      { id: "kid", name: "Kid", born: null, color: null },
      { id: "blank", name: "Blank", born: 2010, color: null },
    ]);
  });

  test("an unknown self id excludes nobody; an empty tree gives nobody", () => {
    assert.equal(relationshipPeople(tree, "someone-else").length, 5);
    assert.deepEqual(relationshipPeople({ people: [] }, "nina"), []);
  });
});

// ---- familyPhrase -----------------------------------------------------------

describe("familyPhrase", () => {
  test("reads well mid-sentence", () => {
    assert.equal(familyPhrase("The Riveras"), "the Riveras");
    assert.equal(familyPhrase("the  Maos "), "the Maos");
    assert.equal(familyPhrase("Mao family"), "the Mao family");
    assert.equal(familyPhrase("Rivera"), "the Rivera family");
  });

  test("falls back for an empty name", () => {
    assert.equal(familyPhrase(""), "the family");
    assert.equal(familyPhrase("   "), "the family");
    assert.equal(familyPhrase("The"), "the family");
  });
});
