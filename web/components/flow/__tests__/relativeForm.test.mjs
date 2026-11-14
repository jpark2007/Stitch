// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
// Node strips the erasable TypeScript in relativeForm.ts. relativeForm.ts
// imports "./validation" and "./relationships" without an extension (as the
// Next build wants), so this file first adds a resolve hook that maps
// extensionless relative imports made from .ts files to the .ts file next to
// them, then loads the module dynamically.
import { test, describe } from "node:test";
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
  RELATIVE_FIELDS,
  RELATIVE_YEAR_MIN,
  childCandidates,
  childPatches,
  duplicateNameHint,
  emptyRelative,
  relativeServerError,
  toNewPerson,
  validateRelative,
  withAddedParam,
} = await import("../relativeForm.ts");

const thisYear = new Date().getFullYear();

// The demo family: Joseph and Rosa, Luis and Carmen; their children David and
// Ana (married); David and Ana's children Sam and Lily.
const PEOPLE = [
  { id: "joseph", name: "Joseph", born: 1934, parentIds: [], spouseIds: [] },
  { id: "rosa", name: "Rosa", born: 1938, parentIds: [], spouseIds: ["joseph"] },
  { id: "luis", name: "Luis", born: 1936, parentIds: [], spouseIds: [] },
  { id: "carmen", name: "Carmen", born: 1940, parentIds: [], spouseIds: ["luis"] },
  { id: "dad", name: "David", born: 1962, parentIds: ["joseph", "rosa"], spouseIds: [] },
  { id: "mom", name: "Ana", born: 1964, parentIds: ["luis", "carmen"], spouseIds: ["dad"] },
  { id: "sam", name: "Sam", born: 1990, parentIds: ["dad", "mom"], spouseIds: [] },
  { id: "lily", name: "Lily", born: 1993, parentIds: ["dad", "mom"], spouseIds: [] },
  { id: "nora", name: "Nora", born: 2019, parentIds: ["sam"], spouseIds: [] },
];

function draft(over = {}) {
  return { ...emptyRelative(), name: "Maria", ...over };
}

// ---- emptyRelative and RELATIVE_FIELDS -------------------------------------

describe("emptyRelative", () => {
  test("is blank, with empty id lists", () => {
    assert.deepEqual(emptyRelative(), {
      name: "",
      born: "",
      died: "",
      parentIds: [],
      spouseIds: [],
      childIds: [],
    });
  });

  test("returns a fresh object each time", () => {
    const a = emptyRelative();
    a.parentIds.push("x");
    assert.deepEqual(emptyRelative().parentIds, []);
  });

  test("RELATIVE_FIELDS lists every field once, in form order", () => {
    assert.deepEqual([...RELATIVE_FIELDS], ["name", "born", "died", "parentIds", "spouseIds", "childIds"]);
    assert.equal(RELATIVE_YEAR_MIN, 1700);
  });
});

// ---- validateRelative --------------------------------------------------------

describe("validateRelative", () => {
  test("a name alone is enough", () => {
    assert.deepEqual(validateRelative(draft(), PEOPLE), {});
  });

  test("a full, sensible draft passes", () => {
    const d = draft({ born: "1910", died: "1988", childIds: ["joseph"] });
    assert.deepEqual(validateRelative(d, PEOPLE), {});
  });

  test("blank name", () => {
    assert.equal(validateRelative(draft({ name: "   " }), PEOPLE).name, "Enter their name.");
  });

  test("too-long name", () => {
    const e = validateRelative(draft({ name: "x".repeat(81) }), PEOPLE);
    assert.equal(e.name, "Keep their name to 80 characters or fewer.");
  });

  test("born after died", () => {
    const e = validateRelative(draft({ born: "1950", died: "1940" }), PEOPLE);
    assert.equal(e.died, "The year they died can’t be before the year they were born.");
    assert.equal(e.born, undefined);
  });

  test("born and died in the same year is fine", () => {
    assert.deepEqual(validateRelative(draft({ born: "1920", died: "1920" }), PEOPLE), {});
  });

  test("born before 1700", () => {
    const e = validateRelative(draft({ born: "1699" }), PEOPLE);
    assert.equal(e.born, "Pick a year from 1700 on.");
    assert.deepEqual(validateRelative(draft({ born: "1700" }), PEOPLE), {});
  });

  test("a year in the future", () => {
    const e = validateRelative(draft({ died: String(thisYear + 1) }), PEOPLE);
    assert.equal(e.died, "That year hasn't happened yet.");
  });

  test("three parents", () => {
    const e = validateRelative(draft({ parentIds: ["joseph", "rosa", "luis"] }), PEOPLE);
    assert.equal(e.parentIds, "Pick up to two parents.");
  });

  test("the same parent twice", () => {
    const e = validateRelative(draft({ parentIds: ["joseph", "joseph"] }), PEOPLE);
    assert.equal(e.parentIds, "Each person can only be picked once.");
  });

  test("an unknown id, in each group", () => {
    const unknown = /isn’t in this family’s tree any more/;
    assert.match(validateRelative(draft({ parentIds: ["ghost"] }), PEOPLE).parentIds, unknown);
    assert.match(validateRelative(draft({ spouseIds: ["ghost"] }), PEOPLE).spouseIds, unknown);
    assert.match(validateRelative(draft({ childIds: ["ghost"] }), PEOPLE).childIds, unknown);
  });

  test("the same id as parent and spouse", () => {
    const e = validateRelative(draft({ parentIds: ["dad"], spouseIds: ["dad"] }), PEOPLE);
    assert.equal(e.spouseIds, "Someone can’t be both a parent and a spouse. Pick one for each person.");
  });

  test("the same id as parent and child", () => {
    const e = validateRelative(draft({ parentIds: ["nora"], childIds: ["nora"] }), PEOPLE);
    assert.equal(e.childIds, "Someone can’t be both their parent and their child.");
  });

  test("a child who already has two parents", () => {
    const e = validateRelative(draft({ childIds: ["sam"] }), PEOPLE);
    assert.equal(e.childIds, "Sam already has two parents, so they can’t take another.");
    const two = validateRelative(draft({ childIds: ["sam", "lily"] }), PEOPLE);
    assert.equal(two.childIds, "Sam and Lily already have two parents each, so they can’t take another.");
  });

  test("a spouse among the chosen children", () => {
    const e = validateRelative(draft({ spouseIds: ["joseph"], childIds: ["joseph"] }), PEOPLE);
    assert.equal(e.spouseIds, "Someone can’t be both their spouse and their child.");
  });

  test("a spouse below a chosen child", () => {
    // Joseph becomes their child, so David, Sam, Lily and Nora are their descendants.
    const e = validateRelative(draft({ spouseIds: ["sam"], childIds: ["joseph"] }), PEOPLE);
    assert.match(e.spouseIds, /their own line/);
  });

  test("a spouse above a chosen parent", () => {
    // Child of Sam, so David, Ana and all four grandparents are above them.
    const e = validateRelative(draft({ parentIds: ["sam"], spouseIds: ["rosa"] }), PEOPLE);
    assert.match(e.spouseIds, /their own line/);
    // Someone outside their line is fine.
    assert.deepEqual(validateRelative(draft({ parentIds: ["sam"], spouseIds: ["lily"] }), PEOPLE), {});
  });

  test("a child who is above a chosen parent (a loop)", () => {
    const e = validateRelative(draft({ parentIds: ["sam"], childIds: ["joseph"] }), PEOPLE);
    assert.equal(e.childIds, "Joseph is already one of their ancestors, so Joseph can’t also be their child.");
  });

  test("several problems at once land on their own fields", () => {
    const e = validateRelative(
      draft({ name: "", born: "abc", parentIds: ["a", "b", "c"], childIds: ["sam"] }),
      PEOPLE,
    );
    assert.deepEqual(Object.keys(e).sort(), ["born", "childIds", "name", "parentIds"]);
  });
});

// ---- toNewPerson ----------------------------------------------------------------

describe("toNewPerson", () => {
  test("trims the name and turns years into numbers", () => {
    const body = toNewPerson(
      draft({ name: "  Great-grandma Inés  ", born: "1899", died: "1975", parentIds: [], spouseIds: ["joseph"] }),
    );
    assert.deepEqual(body, {
      name: "Great-grandma Inés",
      born: 1899,
      died: 1975,
      parentIds: [],
      spouseIds: ["joseph"],
    });
  });

  test("empty years become null, and children are not part of the body", () => {
    const body = toNewPerson(draft({ born: "", died: " ", parentIds: ["sam"], childIds: ["joseph"] }));
    assert.equal(body.born, null);
    assert.equal(body.died, null);
    assert.deepEqual(body.parentIds, ["sam"]);
    assert.equal("childIds" in body, false);
  });

  test("dedupes the id lists", () => {
    const body = toNewPerson(draft({ parentIds: ["sam", "sam"], spouseIds: ["lily", "lily"] }));
    assert.deepEqual(body.parentIds, ["sam"]);
    assert.deepEqual(body.spouseIds, ["lily"]);
  });
});

// ---- childPatches ---------------------------------------------------------------

describe("childPatches", () => {
  test("appends the new person to each child's parents", () => {
    assert.deepEqual(childPatches(PEOPLE, "new1", ["joseph", "nora"]), [
      { personId: "joseph", patch: { parentIds: ["new1"] } },
      { personId: "nora", patch: { parentIds: ["sam", "new1"] } },
    ]);
  });

  test("dedupes children and parents", () => {
    const people = [{ id: "kid", name: "Kid", parentIds: ["new1"] }];
    assert.deepEqual(childPatches(people, "new1", ["kid", "kid"]), [
      { personId: "kid", patch: { parentIds: ["new1"] } },
    ]);
  });

  test("no children, no patches", () => {
    assert.deepEqual(childPatches(PEOPLE, "new1", []), []);
  });
});

// ---- withAddedParam ---------------------------------------------------------------

describe("withAddedParam", () => {
  test("no query", () => {
    assert.equal(withAddedParam("/add", "p1"), "/add?added=p1");
  });

  test("an existing query", () => {
    assert.equal(withAddedParam("/add?family=f1", "p1"), "/add?family=f1&added=p1");
  });

  test("replaces an existing added=", () => {
    assert.equal(withAddedParam("/add?added=old&family=f1", "p1"), "/add?family=f1&added=p1");
    assert.equal(withAddedParam("/add?family=f1&added=a&added=b", "p1"), "/add?family=f1&added=p1");
    // Only the exact key: "addedBy" stays.
    assert.equal(withAddedParam("/x?addedBy=me", "p1"), "/x?addedBy=me&added=p1");
  });

  test("a #hash stays at the end", () => {
    assert.equal(withAddedParam("/add?family=f1#people", "p1"), "/add?family=f1&added=p1#people");
    assert.equal(withAddedParam("/add#people", "p1"), "/add?added=p1#people");
  });

  test("an encoded id", () => {
    assert.equal(withAddedParam("/add?family=f1", "a b&c/d"), "/add?family=f1&added=a%20b%26c%2Fd");
  });

  test("a trailing ? or & leaves no empty pair", () => {
    assert.equal(withAddedParam("/add?", "p1"), "/add?added=p1");
    assert.equal(withAddedParam("/add?family=f1&", "p1"), "/add?family=f1&added=p1");
  });
});

// ---- childCandidates ---------------------------------------------------------------

describe("childCandidates", () => {
  test("only people with fewer than two parents, oldest first", () => {
    const ids = childCandidates(PEOPLE, []).map((p) => p.id);
    assert.deepEqual(ids, ["joseph", "luis", "rosa", "carmen", "nora"]);
  });

  test("leaves out the excluded ids", () => {
    const ids = childCandidates(PEOPLE, ["luis", "nora"]).map((p) => p.id);
    assert.deepEqual(ids, ["joseph", "rosa", "carmen"]);
  });

  test("people without parentIds count as having none; duplicates count once", () => {
    const people = [
      { id: "a", name: "Bea" },
      { id: "b", name: "Al", parentIds: ["x", "x"] },
      { id: "c", name: "Cy", parentIds: ["x", "y"] },
    ];
    assert.deepEqual(childCandidates(people, []).map((p) => p.id), ["b", "a"]);
  });

  test("leaves the input alone", () => {
    const copy = PEOPLE.map((p) => p.id);
    childCandidates(PEOPLE, ["joseph"]);
    assert.deepEqual(PEOPLE.map((p) => p.id), copy);
  });
});

// ---- relativeServerError -------------------------------------------------------------

describe("relativeServerError", () => {
  const cases = [
    ["a person has at most 2 parents", "parentIds", "Pick up to two parents."],
    ["duplicate parent id", "parentIds", "Each person can only be picked once."],
    ["a person cannot be their own parent or spouse", "parentIds", "Someone can’t be their own parent or spouse."],
    [
      "a person cannot be both a parent and a spouse",
      "spouseIds",
      "Someone can’t be both a parent and a spouse. Pick one for each person.",
    ],
    [
      "unknown person id(s) in this family: ['abc']",
      null,
      "Someone you picked isn’t in this family’s tree any more. Reload the page and pick again.",
    ],
    [
      "a person cannot be married to their own ancestor or descendant",
      "spouseIds",
      "They can’t be married to someone in their own line, like a grandparent or a grandchild.",
    ],
    [
      "a person cannot be their own ancestor",
      "childIds",
      "That would make someone their own ancestor. Check the children you picked.",
    ],
    ["A person needs a name", "name", "Enter their name."],
  ];

  for (const [detail, field, message] of cases) {
    test(`maps "${detail}"`, () => {
      assert.deepEqual(relativeServerError(detail), { field, message });
    });
  }

  test("an unknown detail passes through on no field", () => {
    assert.deepEqual(relativeServerError("Something odd happened"), {
      field: null,
      message: "Something odd happened",
    });
  });
});

// ---- the API's year and lineage rules, checked before the round trip -------

describe("validateRelative: years against the people picked (api/db/people.py)", () => {
  test("born before a picked parent is refused on Born, naming the parent", () => {
    const e = validateRelative(draft({ born: "1950", parentIds: ["dad"] }), PEOPLE);
    assert.equal(e.born, "They can’t be born before their parent David (born 1962).");
  });

  test("the oldest parent who is too young is named", () => {
    const e = validateRelative(draft({ born: "1930", parentIds: ["rosa", "joseph"] }), PEOPLE);
    assert.equal(e.born, "They can’t be born before their parent Joseph (born 1934).");
  });

  test("born after a picked child is refused on Born, naming the child", () => {
    const e = validateRelative(draft({ born: "1995", childIds: ["nora", "joseph"] }), PEOPLE);
    assert.equal(e.born, "They can’t be born after their child Joseph (born 1934).");
  });

  test("same year as a parent or child passes; unknown years pass", () => {
    assert.deepEqual(validateRelative(draft({ born: "1962", parentIds: ["dad"] }), PEOPLE), {});
    assert.deepEqual(validateRelative(draft({ born: "1934", childIds: ["joseph"] }), PEOPLE), {});
    assert.deepEqual(validateRelative(draft({ parentIds: ["dad"], childIds: ["nora"] }), PEOPLE), {});
    const noYear = [...PEOPLE, { id: "x", name: "X", parentIds: [], spouseIds: [] }];
    assert.deepEqual(validateRelative(draft({ born: "2020", childIds: ["x"] }), noYear), {});
  });

  test("a bad year keeps its own message", () => {
    assert.equal(validateRelative(draft({ born: "abc", parentIds: ["dad"] }), PEOPLE).born, "Enter the year as a number, like 1987.");
  });
});

describe("validateRelative: a child whose spouse would become their ancestor", () => {
  test("the child of Rosa can't be the parent of Joseph, Rosa's husband", () => {
    const people = PEOPLE.map((p) => (p.id === "joseph" ? { ...p, spouseIds: ["rosa"] } : p));
    const e = validateRelative(draft({ parentIds: ["rosa"], childIds: ["joseph"] }), people);
    assert.equal(
      e.childIds,
      "Joseph can’t be their child: Joseph is married to Rosa, who would then be one of Joseph’s ancestors too.",
    );
  });

  test("a spouse further up counts too", () => {
    // Carmen is Luis's wife; the new person is David's child, and Joseph is David's father.
    const people = PEOPLE.map((p) => (p.id === "luis" ? { ...p, spouseIds: ["joseph"] } : p));
    const e = validateRelative(draft({ parentIds: ["dad"], childIds: ["luis"] }), people);
    assert.match(e.childIds, /^Luis can’t be their child: Luis is married to Joseph/);
  });

  test("an unrelated child is fine", () => {
    assert.deepEqual(validateRelative(draft({ parentIds: ["rosa"], childIds: ["luis"] }), PEOPLE), {});
  });
});

describe("duplicateNameHint", () => {
  test("names the person already in the tree, with their year", () => {
    assert.equal(
      duplicateNameHint("  rosa ", PEOPLE),
      "Rosa (born 1938) is already in the tree. Only add them again if this is someone else.",
    );
  });

  test("several with the name", () => {
    const people = [...PEOPLE, { id: "rosa2", name: "Rosa", born: 1990, parentIds: [], spouseIds: [] }];
    assert.equal(
      duplicateNameHint("Rosa", people),
      "2 people called Rosa are already in the tree. Only add one more if this is someone else.",
    );
  });

  test("inner spaces and case don't matter; no year shows just the name", () => {
    const people = [{ id: "a", name: "Great  Grandma Ines", parentIds: [], spouseIds: [] }];
    assert.equal(
      duplicateNameHint("great grandma ines", people),
      "Great  Grandma Ines is already in the tree. Only add them again if this is someone else.",
    );
  });

  test("a new or blank name has no hint", () => {
    assert.equal(duplicateNameHint("Maria", PEOPLE), null);
    assert.equal(duplicateNameHint("   ", PEOPLE), null);
    assert.equal(duplicateNameHint("Ros", PEOPLE), null);
  });
});

describe("relativeServerError: the API's year rules", () => {
  test("died before born goes on Died", () => {
    assert.deepEqual(relativeServerError("a person cannot die before they were born"), {
      field: "died",
      message: "The year they died can’t be before the year they were born.",
    });
  });

  test("born before a parent and after a child go on Born, keeping the names", () => {
    assert.deepEqual(relativeServerError("a person cannot be born before their parent (David, born 1962)"), {
      field: "born",
      message: "They can’t be born before their parent David (born 1962).",
    });
    assert.deepEqual(relativeServerError("a person cannot be born after their child (Great Grandma Inés, born 1910)"), {
      field: "born",
      message: "They can’t be born after their child Great Grandma Inés (born 1910).",
    });
  });
});
