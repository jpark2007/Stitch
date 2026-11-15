/**
 * A tradition being written (/traditions/new): the page the book beside the
 * form prints (draftSpread), which kit it gets (variantIn), and the link that
 * opens the book at it once saved (openAt.ts).
 *
 *   cd web && node --no-warnings --test "components/traditions/*.test.ts"
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";
import type { Tradition } from "@/lib/types";

// By computed paths, so the type check reads them as the app does (no ".ts").
const paths = ["./traditionBook.ts", "./openAt.ts", "../book/types.ts"];
const { DRAFT_TRADITION_ID, draftSpread, draftTradition, draftVariant, traditionSpread, variantIn }: typeof import("./traditionBook") =
  await import(paths[0]);
const { openAtTradition, traditionHref, traditionParam, traditionSpreadAt }: typeof import("./openAt") = await import(paths[1]);
const { spreadDate }: typeof import("../book/types") = await import(paths[2]);

type Page = Parameters<typeof draftSpread>[0];

const BLANK: Page = {
  title: "",
  description: "",
  category: null,
  when: { kind: null },
  participantIds: [],
  photoIds: [],
  startedYear: null,
};

const ROSA = { id: "rosa", name: "Rosa", color: "#b9573d" };
const shot = () => "data:image/jpeg;base64,SNAP";

function saved(over: Partial<Tradition> = {}): Tradition {
  return {
    id: "t-new",
    familyId: "f1",
    title: "Tamales on Christmas Eve",
    description: "",
    category: "food",
    when: { kind: "yearly", label: "Christmas Eve" },
    participantIds: [],
    photoIds: [],
    startedYear: null,
    createdBy: "u1",
    createdAt: "2026-11-14T00:00:00Z",
    updatedAt: "2026-11-14T00:00:00Z",
    ...over,
  };
}

describe("draftSpread: the page of a tradition being written", () => {
  it("a blank draft is a blank page: no title, no date, no words, no prints, the pop-up's marks", () => {
    const s = draftSpread(BLANK, []);
    assert.equal(s.title, "");
    assert.equal(s.dateLine, undefined);
    assert.equal(spreadDate(s), "");
    assert.equal(String(s.year), "");
    assert.equal(s.text, "");
    assert.deepEqual(s.photos, []);
    assert.equal(s.stage, undefined, "no kind yet: the right page keeps 'the pop-up rises here'");
    assert.equal(s.id, DRAFT_TRADITION_ID);
  });

  it("prints the name as typed, and nothing made up around it", () => {
    const s = draftSpread({ ...BLANK, title: "Sunday pancakes" }, []);
    assert.equal(s.title, "Sunday pancakes");
    assert.equal(s.text, "", "no 'Now and then, without fail.' before how often is picked");
    assert.equal(s.dateLine, undefined);
  });

  it("the when line appears once how often is picked, as the book prints it", () => {
    const page: Page = { ...BLANK, title: "Tamales", when: { kind: "yearly", label: "Christmas Eve" } };
    const s = draftSpread(page, []);
    assert.equal(s.dateLine, "Christmas Eve, every year");
    assert.equal(s.dateLine, traditionSpread(draftTradition(page), []).dateLine);
  });

  it("is the saved tradition's page once everything is given (same builder, same words)", () => {
    const page: Page = {
      title: "Tamales on Christmas Eve",
      description: "The kitchen turns into an assembly line.",
      category: "food",
      when: { kind: "yearly", label: "Christmas Eve", month: 12 },
      participantIds: ["rosa"],
      photoIds: ["p0"],
      startedYear: 1962,
    };
    const opts = { photoUrl: (id: string) => (id === "p0" ? "blob:p0" : null), snapshot: shot };
    const draft = draftSpread(page, [ROSA], opts);
    const real = traditionSpread(
      saved({
        id: DRAFT_TRADITION_ID,
        title: page.title,
        description: page.description,
        category: "food",
        when: { kind: "yearly", label: "Christmas Eve", month: 12 },
        participantIds: ["rosa"],
        photoIds: ["p0"],
        startedYear: 1962,
      }),
      [ROSA],
      opts,
    );
    assert.deepEqual(draft, real);
    // The first photo, then the snapshot captioned with the year.
    assert.equal(draft.photos.length, 2);
    assert.equal(draft.photos[1].src, "data:image/jpeg;base64,SNAP");
    assert.equal(draft.photos[1].caption, "since 1962");
    assert.deepEqual(draft.stage, {}, "a kind picked: printed plain for the kit");
  });

  it("with how often but no story, the story is the book's own line about it", () => {
    const s = draftSpread({ ...BLANK, title: "Pancakes", when: { kind: "weekly", label: "every Sunday" } }, [ROSA]);
    assert.equal(s.text, "Every Sunday, Rosa keeps it going.");
  });

  it("a story typed before how often is printed as typed", () => {
    const s = draftSpread({ ...BLANK, description: "Dad flips them." }, []);
    assert.equal(s.text, "Dad flips them.");
  });

  it("a kind alone takes the pencil marks off the right page", () => {
    assert.deepEqual(draftSpread({ ...BLANK, category: "music" }, []).stage, {});
  });

  it("the keepers' snapshot is taped in for the people ticked", () => {
    const s = draftSpread({ ...BLANK, title: "x", participantIds: ["rosa"] }, [ROSA], { snapshot: shot });
    assert.equal(s.photos.length, 1);
    assert.equal(s.photos[0].id, `${DRAFT_TRADITION_ID}:keepers`);
  });
});

describe("variantIn: which of its kind's kits it gets", () => {
  const book = [
    saved({ id: "a", category: "food", startedYear: 1950 }),
    saved({ id: "b", category: "food", startedYear: 1990 }),
    saved({ id: "c", category: "music", startedYear: 1970 }),
  ];
  it("counts the same kind before it in book order", () => {
    assert.equal(variantIn(book, saved({ id: "n", category: "food", startedYear: 1970 })), 1);
    assert.equal(variantIn(book, saved({ id: "n", category: "food", startedYear: 1940 })), 0);
    assert.equal(variantIn(book, saved({ id: "n", category: "food", startedYear: null })), 2, "undated goes last");
    assert.equal(variantIn(book, saved({ id: "n", category: "trip" })), 0);
    assert.equal(variantIn([], saved()), 0);
  });
  it("does not count itself when it is already in the list", () => {
    const self = saved({ id: "a", category: "food", startedYear: 1950 });
    assert.equal(variantIn(book, self), 0);
  });
});

describe("draftVariant: the kit held from the draft through the saved beat", () => {
  // An exact twin already in the book: same title, year and kind.
  const twin = saved({ id: "tr-tamales", category: "food", title: "Tamales", startedYear: 1962 });
  const page: Page = { ...BLANK, title: "Tamales", category: "food", when: { kind: "yearly" }, startedYear: 1962 };
  // A real new id that sorts before the twin's ("6…" < "t…"), where the draft id sorted after it.
  const real = saved({ id: "6af8eb30e9cf7cb6adb72361", category: "food", title: "Tamales", startedYear: 1962 });

  it("the saved one's own place would differ from the draft's (the swap this prevents)", () => {
    assert.equal(variantIn([twin], draftTradition(page)), 1);
    assert.equal(variantIn([twin], real), 0);
  });

  it("keeps the draft's variant once saved", () => {
    assert.equal(draftVariant([twin], page), 1);
    assert.equal(draftVariant([twin], page, real), 1);
  });

  it("goes by the saved one only without a draft, and is 0 without either or a kind", () => {
    assert.equal(draftVariant([twin], null, real), 0);
    assert.equal(draftVariant([twin], null, null), 0);
    assert.equal(draftVariant([twin], { ...page, category: null }), 0);
  });
});

describe("openAt: the book opened at a tradition", () => {
  const spreads = [{ id: "t1" }, { id: "t2" }, { id: "traditions:end", kind: "end" as const }];

  it("links the family's book with the tradition", () => {
    assert.equal(traditionHref("fam 1", "t/2"), "/traditions?family=fam%201&tradition=t%2F2");
  });

  it("reads ?tradition=, else ?memory=", () => {
    assert.equal(traditionParam({ tradition: "t2" }), "t2");
    assert.equal(traditionParam({ tradition: ["t2", "t1"] }), "t2");
    assert.equal(traditionParam({ memory: " t1 " }), "t1");
    assert.equal(traditionParam({ tradition: "", memory: "t1" }), "t1");
    assert.equal(traditionParam({}), null);
  });

  it("finds the spread, never the closing one", () => {
    assert.equal(traditionSpreadAt(spreads, "t2"), 2);
    assert.equal(traditionSpreadAt(spreads, "traditions:end"), null);
    assert.equal(traditionSpreadAt(spreads, "nope"), null);
    assert.equal(traditionSpreadAt(spreads, null), null);
  });

  it("opens there over ?spread and ?state, and leaves the view alone otherwise", () => {
    const initial = { state: "closed" as const, spread: 1, focus: 1 as const };
    assert.deepEqual(openAtTradition(initial, spreads, "t2"), { state: "open", spread: 2, focus: 1 });
    assert.equal(openAtTradition(initial, spreads, "nope"), initial);
    assert.equal(openAtTradition(initial, spreads, null), initial);
  });
});
