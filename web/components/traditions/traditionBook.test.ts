/**
 * The Book of Family Traditions as a book: the "when" line, "since", the
 * names, the title, book order and the spreads.
 *
 *   cd web && node --no-warnings --test "components/traditions/*.test.ts"
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";
import type { Person, Tradition } from "@/lib/types";

// By a computed path, so the type check reads it as the app does (no ".ts").
type Module = typeof import("./traditionBook");
const paths = ["./traditionBook.ts", "../book/types.ts"];
const { spreadDate }: typeof import("../book/types") = await import(paths[1]);
const {
  WHEN_MAX,
  STORY_MAX_ALONE,
  dateLineOf,
  STORY_MAX_WITH_PHOTOS,
  familyCore,
  fitStory,
  keepersOf,
  monthName,
  namesLine,
  orderTraditions,
  peopleById,
  sinceLine,
  storyBudget,
  storyText,
  traditionBook,
  traditionSpread,
  traditionsCover,
  traditionsTitle,
  whenLine,
}: Module = await import(paths[0]);

function tradition(over: Partial<Tradition> = {}): Tradition {
  return {
    id: "t1",
    familyId: "f1",
    title: "Tamales on Christmas Eve",
    description: "Every Christmas Eve the kitchen turns into an assembly line.",
    category: "food",
    when: { kind: "yearly", label: "Christmas Eve", month: 12 },
    participantIds: ["rosa", "luis"],
    photoIds: [],
    startedYear: 1962,
    createdBy: "u1",
    createdAt: "2026-11-14T00:00:00Z",
    updatedAt: "2026-11-14T00:00:00Z",
    ...over,
  };
}

function person(id: string, name: string, color = "#7b5ea7"): Person {
  return { id, familyId: "f1", name, character: { color, item: "straw hat", build: "average" } };
}

describe("whenLine", () => {
  it("leads with the label, as a person says it", () => {
    assert.equal(whenLine({ kind: "yearly", label: "Christmas Eve", month: 12 }), "Every Christmas Eve");
    assert.equal(whenLine({ kind: "yearly", label: "Easter Sunday", month: 4 }), "Every Easter Sunday");
    assert.equal(whenLine({ kind: "weekly", label: "Sunday" }), "Every Sunday");
    assert.equal(whenLine({ kind: "seasonal", label: "summer" }), "Every summer");
  });

  it("keeps a label that already reads as a phrase, capitalised", () => {
    assert.equal(whenLine({ kind: "weekly", label: "every Sunday" }), "Every Sunday");
    assert.equal(whenLine({ kind: "yearly", label: "every July", month: 7 }), "Every July");
    assert.equal(whenLine({ kind: "occasional", label: "when everyone is home" }), "When everyone is home");
    assert.equal(whenLine({ kind: "yearly", label: "on the first snow" }), "On the first snow");
    assert.equal(whenLine({ kind: "monthly", label: "Each full moon." }), "Each full moon");
  });

  it("prints an occasional label as it is", () => {
    assert.equal(whenLine({ kind: "occasional", label: "birthdays" }), "Birthdays");
  });

  it("says it from the kind and month without a label", () => {
    assert.equal(whenLine({ kind: "yearly", month: 7 }), "Every July");
    assert.equal(whenLine({ kind: "yearly" }), "Every year");
    assert.equal(whenLine({ kind: "seasonal", month: 1 }), "Every winter");
    assert.equal(whenLine({ kind: "seasonal", month: 4 }), "Every spring");
    assert.equal(whenLine({ kind: "seasonal", month: 10 }), "Every autumn");
    assert.equal(whenLine({ kind: "seasonal", month: null }), "Every season");
    assert.equal(whenLine({ kind: "monthly", label: "   " }), "Every month");
    assert.equal(whenLine({ kind: "weekly", label: null }), "Every week");
    assert.equal(whenLine({ kind: "occasional" }), "Now and then");
    assert.equal(whenLine(null), "Now and then");
  });

  it("falls back to the kind and month when the label is too long for one line", () => {
    const label = "the Saturday after the first frost, if the creek is frozen";
    const line = whenLine({ kind: "yearly", label, month: 11 });
    assert.equal(line, "Every November");
    assert.ok(whenLine({ kind: "yearly", label: "x".repeat(40) }).length <= WHEN_MAX);
  });

  it("ignores a month that is not one", () => {
    assert.equal(whenLine({ kind: "yearly", month: 13 }), "Every year");
    assert.equal(monthName(0), null);
    assert.equal(monthName(12), "December");
  });
});

describe("dateLineOf", () => {
  it("leads a yearly tradition with its occasion", () => {
    assert.equal(dateLineOf({ kind: "yearly", label: "Christmas Eve", month: 12 }), "Christmas Eve, every year");
    assert.equal(dateLineOf({ kind: "yearly", label: "Easter Sunday", month: 4 }), "Easter Sunday, every year");
    assert.equal(dateLineOf({ kind: "yearly", label: "every July", month: 7 }), "July, every year");
    assert.equal(dateLineOf({ kind: "yearly", label: "first Saturday in October", month: 10 }), "First Saturday in October");
    assert.equal(dateLineOf({ kind: "yearly", label: "on the first snow" }), "On the first snow");
    assert.equal(dateLineOf({ kind: "yearly", month: 7 }), "July, every year");
    assert.equal(dateLineOf({ kind: "yearly" }), "Once a year");
  });

  it("says a season by name", () => {
    assert.equal(dateLineOf({ kind: "seasonal", label: "summer", month: 7 }), "Summer, every year");
    assert.equal(dateLineOf({ kind: "seasonal", label: "every autumn" }), "Autumn, every year");
    assert.equal(dateLineOf({ kind: "seasonal", month: 1 }), "Winter, every year");
    assert.equal(dateLineOf({ kind: "seasonal", month: null }), "Once a season");
  });

  it("says a monthly one", () => {
    assert.equal(dateLineOf({ kind: "monthly", label: "Each full moon." }), "Full moon, every month");
    assert.equal(dateLineOf({ kind: "monthly", label: "   " }), "Once a month");
    assert.equal(dateLineOf({ kind: "monthly", month: 3 }), "Once a month");
  });

  it("says a weekly one by its day", () => {
    assert.equal(dateLineOf({ kind: "weekly", label: "every Sunday" }), "Sundays");
    assert.equal(dateLineOf({ kind: "weekly", label: "friday" }), "Fridays");
    assert.equal(dateLineOf({ kind: "weekly", label: "game night" }), "Game night, every week");
    assert.equal(dateLineOf({ kind: "weekly", label: null }), "Once a week");
  });

  it("keeps an occasional label as written", () => {
    assert.equal(dateLineOf({ kind: "occasional", label: "when everyone is home" }), "When everyone is home");
    assert.equal(dateLineOf({ kind: "occasional", label: "birthdays" }), "Birthdays");
    assert.equal(dateLineOf({ kind: "occasional" }), "Now and then");
    assert.equal(dateLineOf(null), "Now and then");
  });

  it("falls back to the kind and month when the label is too long", () => {
    const label = "the Saturday after the first frost, if the creek is frozen";
    assert.equal(dateLineOf({ kind: "yearly", label, month: 11 }), "November, every year");
    assert.equal(dateLineOf({ kind: "weekly", label: "x".repeat(40) }), "Once a week");
  });

  it("never opens on \"Every\", so the dots tell the demo's traditions apart", () => {
    const demo = [
      { kind: "yearly", label: "Christmas Eve", month: 12 },
      { kind: "weekly", label: "every Sunday" },
      { kind: "yearly", label: "every July", month: 7 },
      { kind: "occasional", label: "when everyone is home" },
      { kind: "yearly", label: "Easter Sunday", month: 4 },
    ] as const;
    const lines = demo.map((w) => dateLineOf(w));
    for (const line of lines) assert.ok(!/^every\b/i.test(line), line);
    assert.equal(new Set(lines.map((l) => l.slice(0, 4))).size, lines.length);
  });
});

describe("sinceLine and namesLine", () => {
  it("says since when, only for a real year", () => {
    assert.equal(sinceLine(1962), "since 1962");
    assert.equal(sinceLine(null), null);
    assert.equal(sinceLine(undefined), null);
    assert.equal(sinceLine(0), null);
    assert.equal(sinceLine(1962.5), null);
  });

  it("joins names as a person writes them", () => {
    assert.equal(namesLine([]), "");
    assert.equal(namesLine(["Rosa"]), "Rosa");
    assert.equal(namesLine(["Rosa", "Luis"]), "Rosa & Luis");
    assert.equal(namesLine(["Rosa", " ", "Luis", "David"]), "Rosa, Luis & David");
  });
});

describe("the title", () => {
  it("names the family as the title says it", () => {
    assert.equal(familyCore("The Alvarez–Moore family"), "Alvarez–Moore");
    assert.equal(familyCore("The Riveras"), "Rivera");
    assert.equal(familyCore("The Joneses"), "Jones");
    assert.equal(familyCore("The Ross"), "Ross");
    assert.equal(familyCore("Maos"), "Maos");
    assert.equal(familyCore("  Park  Family "), "Park");
    assert.equal(familyCore(""), "");
    assert.equal(traditionsTitle("The Riveras"), "The Rivera Book of Traditions");
    assert.equal(traditionsTitle(null), "The Book of Family Traditions");
  });

  it("puts the family above the title on the cover, and the oldest year under it", () => {
    assert.deepEqual(traditionsCover("The Alvarez–Moore family", [{ startedYear: 1995 }, { startedYear: 1962 }, { startedYear: null }]), {
      title: "Book of Traditions",
      kicker: "The Alvarez–Moore",
      subtitle: "since 1962",
    });
    assert.deepEqual(traditionsCover(null, [{ startedYear: null }]), { title: "Book of Family Traditions" });
  });
});

describe("the spreads", () => {
  const people = [person("rosa", "Rosa"), person("luis", "Luis", "#5f7f52"), person("dad", "David", "not a colour")];

  it("orders the oldest tradition first, undated ones last, then by title", () => {
    const list = [
      tradition({ id: "a", title: "Hymns", startedYear: null }),
      tradition({ id: "b", title: "Pancakes", startedYear: 1995 }),
      tradition({ id: "c", title: "Tamales", startedYear: 1962 }),
      tradition({ id: "d", title: "Apples", startedYear: null }),
    ];
    assert.deepEqual(orderTraditions(list).map((t) => t.id), ["c", "b", "d", "a"]);
  });

  it("knows each keeper once, in the tradition's order, with a safe colour", () => {
    const known = peopleById(people);
    const keepers = keepersOf({ participantIds: ["dad", "ghost", "rosa", "dad"] }, known);
    assert.deepEqual(keepers.map((k) => k.id), ["dad", "rosa"]);
    assert.equal(keepers[0].color, "#b9573d");
  });

  it("writes a line for a tradition with no story", () => {
    const known = peopleById(people);
    const t = tradition({ description: "  " });
    assert.equal(storyText(t, keepersOf(t, known)), "Every Christmas Eve, Rosa & Luis keep it going.");
    assert.equal(storyText(t, []), "Every Christmas Eve, without fail.");
  });

  it("says keeps for one keeper, keep for several, and needs none", () => {
    const known = peopleById([...people, person("pat", "Pat Probe")]);
    const monthly = tradition({ description: "", when: { kind: "monthly" } });
    const line = (ids: string[]) => storyText(monthly, keepersOf({ participantIds: ids }, known));
    assert.equal(line(["pat"]), "Every month, Pat Probe keeps it going.");
    assert.equal(line(["pat", "rosa"]), "Every month, Pat Probe & Rosa keep it going.");
    assert.equal(line(["pat", "rosa", "luis"]), "Every month, Pat Probe, Rosa & Luis keep it going.");
    assert.equal(line([]), "Every month, without fail.");
    assert.equal(line(["ghost"]), "Every month, without fail.");
  });

  it("binds one spread per tradition, then the closing spread", () => {
    const traditions = [tradition(), tradition({ id: "t2", title: "Hymns", startedYear: null, participantIds: ["dad"], category: "music" })];
    const book = traditionBook(traditions, people, {
      familyName: "The Riveras",
      snapshot: (t, keepers) => `data:image/png;base64,${t.id}-${keepers.length}`,
    });
    assert.equal(book.title, "The Rivera Book of Traditions");
    assert.deepEqual(book.spreads.map((s) => s.id), ["t1", "t2", "traditions:end"]);
    const first = book.spreads[0];
    assert.equal(first.dateLine, "Christmas Eve, every year");
    assert.equal(first.year, 1962);
    assert.equal(spreadDate(first), "Christmas Eve, every year", "contents and the dots show the date line");
    const undated = book.spreads[1];
    assert.equal(undated.dateLine, "Christmas Eve, every year");
    assert.equal(undated.year, 0, "no year it began: 0, which the book reads as undated");
    assert.deepEqual(first.stage, {});
    assert.deepEqual(first.photos, [{ id: "t1:keepers", src: "data:image/png;base64,t1-2", caption: "since 1962" }]);
    assert.equal(book.spreads[1].photos[0].caption, undefined);
    const end = book.spreads[2];
    assert.equal(end.kind, "end");
    assert.deepEqual(end.tellings?.map((t) => t.id), ["rosa", "luis", "dad"]);
    assert.equal(book.byId.get("t2")?.keepers[0].name, "David");
  });

  it("counts each tradition's place among its category, for the kit's variant", () => {
    const book = traditionBook(
      [
        tradition({ id: "a", startedYear: 1960 }),
        tradition({ id: "b", startedYear: 1970, category: "music" }),
        tradition({ id: "c", startedYear: 1980 }),
      ],
      people,
    );
    assert.deepEqual(["a", "b", "c"].map((id) => book.byId.get(id)?.variant), [0, 0, 1]);
  });

  it("tapes in one photo beside the snapshot, or two photos without one", () => {
    const t = tradition({ photoIds: ["m1", "m2", "m3"] });
    const photoUrl = (id: string) => (id === "m2" ? null : `blob:${id}`);
    const withShot = traditionBook([t], people, { photoUrl, snapshot: () => "data:x" }).spreads[0];
    assert.deepEqual(withShot.photos.map((p) => p.id), ["m1", "t1:keepers"]);
    assert.equal(withShot.photos[0].caption, undefined);
    assert.equal(withShot.photos[1].caption, "since 1962");
    const noShot = traditionBook([t], people, { photoUrl }).spreads[0];
    assert.deepEqual(noShot.photos.map((p) => [p.id, p.caption]), [
      ["m1", undefined],
      ["m3", "since 1962"],
    ]);
    const nobody = traditionBook([tradition({ participantIds: [] })], people, { snapshot: () => "data:x" }).spreads[0];
    assert.deepEqual(nobody.photos, []);
  });

  it("is empty without traditions", () => {
    const book = traditionBook([], people, { familyName: "The Riveras" });
    assert.deepEqual(book.spreads, []);
  });
});

describe("the story fits the page", () => {
  const people = [person("rosa", "Rosa"), person("luis", "Luis", "#5f7f52")];
  const sentence = "Every year we gather at the old house and cook together. ";
  const long = sentence.repeat(100).slice(0, 5000);

  it("keeps a story that fits as it is", () => {
    assert.equal(fitStory("  We make tamales.  ", 440), "We make tamales.");
  });

  it("cuts a long story at a sentence, with an ellipsis", () => {
    const out = fitStory(long, 440);
    assert.ok(out.length <= 440, `${out.length}`);
    assert.ok(out.length >= 440 * 0.6);
    assert.match(out, /together\. …$/);
  });

  it("cuts at a word when no sentence ends late enough", () => {
    const words = "tamales and pancakes and hymns ".repeat(40);
    const out = fitStory(words, 100);
    assert.ok(out.length <= 100);
    assert.match(out, /[a-z]…$/);
    assert.ok(words.startsWith(out.slice(0, -1)), "never half a word");
  });

  it("gives less room beside prints and under a long title", () => {
    assert.equal(storyBudget("Tamales", true), STORY_MAX_WITH_PHOTOS);
    assert.equal(storyBudget("Tamales", false), STORY_MAX_ALONE);
    assert.ok(storyBudget("A very long title that wraps over several lines of the page", true) < STORY_MAX_WITH_PHOTOS);
    assert.ok(storyBudget("x".repeat(200), true) >= 160);
  });

  it("counts no more title lines than the book prints (three)", () => {
    assert.equal(storyBudget("x".repeat(22), true), STORY_MAX_WITH_PHOTOS);
    assert.equal(storyBudget("x".repeat(44), true), STORY_MAX_WITH_PHOTOS - 75);
    assert.equal(storyBudget("x".repeat(66), true), STORY_MAX_WITH_PHOTOS - 150);
    assert.equal(storyBudget("x".repeat(120), true), STORY_MAX_WITH_PHOTOS - 150);
    assert.equal(storyBudget("x".repeat(200), true), STORY_MAX_WITH_PHOTOS - 150);
    assert.equal(storyBudget("x".repeat(200), false), STORY_MAX_ALONE - 150);
  });

  it("prints a 5000-character description within the page's limit", () => {
    const withShot = traditionBook([tradition({ description: long })], people, { snapshot: () => "data:image/png;base64,x" });
    assert.ok(withShot.spreads[0].photos.length > 0);
    assert.ok(withShot.spreads[0].text.length <= STORY_MAX_WITH_PHOTOS, `${withShot.spreads[0].text.length}`);
    const plain = traditionBook([tradition({ description: long })], people);
    assert.equal(plain.spreads[0].photos.length, 0);
    assert.ok(plain.spreads[0].text.length <= STORY_MAX_ALONE, `${plain.spreads[0].text.length}`);
    assert.ok(plain.spreads[0].text.endsWith("… Kept since 1962."), plain.spreads[0].text.slice(-40));
  });
});

describe("a long title", () => {
  const people = [person("rosa", "Rosa"), person("luis", "Luis", "#5f7f52")];
  const long = "We walk to the old orchard and pick apples for the pies. ".repeat(20);

  // The book cuts a title to three lines (pageArt TITLE_MAX_LINES) and never
  // prints a photo narrower than PHOTO_MIN_W, so even the longest title the
  // API allows (200) leaves the prints their size: they stay, captioned.
  for (const n of [60, 120, 200]) {
    it(`keeps the prints and their "since" under a ${n}-character title`, () => {
      const t = tradition({ title: "x".repeat(n), description: long, photoIds: ["m1"] });
      const spread = traditionBook([t], people, { photoUrl: (id) => `blob:${id}`, snapshot: () => "data:x" }).spreads[0];
      assert.deepEqual(spread.photos.map((p) => [p.id, p.caption]), [
        ["m1", undefined],
        ["t1:keepers", "since 1962"],
      ]);
      assert.ok(!spread.text.includes("since"));
      assert.ok(spread.text.length <= storyBudget(t.title, true), `${spread.text.length}`);
    });
  }
});

describe("since, on the left page", () => {
  it("closes the story on the year when there is no print to caption", () => {
    const t = tradition({ participantIds: [], photoIds: [], startedYear: 2001 });
    const spread = traditionSpread(t, [], {});
    assert.deepEqual(spread.photos, []);
    assert.equal(spread.text, "Every Christmas Eve the kitchen turns into an assembly line. Kept since 2001.");
  });

  it("captions the print instead when there is one, and says nothing without a year", () => {
    const withPhoto = traditionSpread(tradition({ photoIds: ["m1"] }), [], { photoUrl: (id) => `blob:${id}` });
    assert.equal(withPhoto.photos[0].caption, "since 1962");
    assert.ok(!withPhoto.text.includes("since"));
    const undated = traditionSpread(tradition({ participantIds: [], startedYear: null }), [], {});
    assert.ok(!undated.text.includes("since"));
  });
});
