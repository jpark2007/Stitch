// Run with: cd web && node --test "components/flow/__tests__/*.test.mjs"
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MAX_PARENTS,
  describeRelationships,
  sortPeopleForPicking,
  toggleRelation,
  validateRelationships,
} from "../relationships.ts";
import {
  MAX_PHOTOS,
  MAX_PHOTO_BYTES,
  PHOTO_TYPES,
  acceptPhotos,
  overCapMessage,
  photoTypeFor,
} from "../photos.ts";

const empty = () => ({ parentIds: [], spouseIds: [] });
const names = { rosa: "Rosa", luis: "Luis", ana: "Ana", bea: "Bea", tom: "Tom" };

// ---------------------------------------------------------------- toggleRelation

test("toggleRelation adds and removes a person in one role", () => {
  const a = toggleRelation(empty(), "parent", "rosa");
  assert.deepEqual(a, { parentIds: ["rosa"], spouseIds: [] });
  const b = toggleRelation(a, "parent", "rosa");
  assert.deepEqual(b, { parentIds: [], spouseIds: [] });
  const c = toggleRelation(b, "spouse", "ana");
  assert.deepEqual(c, { parentIds: [], spouseIds: ["ana"] });
});

test("toggleRelation moves a person between roles", () => {
  const asParent = toggleRelation(empty(), "parent", "rosa");
  const asSpouse = toggleRelation(asParent, "spouse", "rosa");
  assert.deepEqual(asSpouse, { parentIds: [], spouseIds: ["rosa"] });
  const backToParent = toggleRelation(asSpouse, "parent", "rosa");
  assert.deepEqual(backToParent, { parentIds: ["rosa"], spouseIds: [] });
});

test("toggleRelation caps parents at 2 and ignores a third", () => {
  assert.equal(MAX_PARENTS, 2);
  let r = empty();
  r = toggleRelation(r, "parent", "rosa");
  r = toggleRelation(r, "parent", "luis");
  const third = toggleRelation(r, "parent", "tom");
  assert.equal(third, r, "same object back, so callers can tell nothing changed");
  assert.deepEqual(third.parentIds, ["rosa", "luis"]);
});

test("a third parent attempt does not pull the person out of spouses", () => {
  const r = { parentIds: ["rosa", "luis"], spouseIds: ["ana"] };
  assert.equal(toggleRelation(r, "parent", "ana"), r);
  assert.deepEqual(r.spouseIds, ["ana"]);
});

test("toggleRelation never mutates its input", () => {
  const r = { parentIds: ["rosa"], spouseIds: ["ana"] };
  const frozen = JSON.stringify(r);
  toggleRelation(r, "parent", "luis");
  toggleRelation(r, "spouse", "rosa");
  toggleRelation(r, "spouse", "ana");
  assert.equal(JSON.stringify(r), frozen);
});

test("spouses are not capped", () => {
  let r = empty();
  for (const id of ["ana", "bea", "tom"]) r = toggleRelation(r, "spouse", id);
  assert.deepEqual(r.spouseIds, ["ana", "bea", "tom"]);
});

// ---------------------------------------------------------- validateRelationships

test("validateRelationships accepts a normal set and an empty set", () => {
  assert.equal(validateRelationships(empty()), null);
  assert.equal(
    validateRelationships({ parentIds: ["rosa", "luis"], spouseIds: ["ana"] }, { selfId: "me" }),
    null,
  );
});

test("validateRelationships rejects yourself as parent or spouse", () => {
  const asParent = validateRelationships({ parentIds: ["me"], spouseIds: [] }, { selfId: "me" });
  const asSpouse = validateRelationships({ parentIds: [], spouseIds: ["me"] }, { selfId: "me" });
  assert.match(asParent ?? "", /yourself/);
  assert.match(asSpouse ?? "", /yourself/);
});

test("validateRelationships rejects duplicates", () => {
  assert.match(
    validateRelationships({ parentIds: ["rosa", "rosa"], spouseIds: [] }) ?? "",
    /only be picked once/,
  );
  assert.match(
    validateRelationships({ parentIds: [], spouseIds: ["ana", "ana"] }) ?? "",
    /only be picked once/,
  );
});

test("validateRelationships rejects someone who is both parent and spouse", () => {
  assert.match(
    validateRelationships({ parentIds: ["rosa"], spouseIds: ["rosa"] }) ?? "",
    /both a parent and a spouse/,
  );
});

test("validateRelationships rejects more than 2 parents", () => {
  assert.match(
    validateRelationships({ parentIds: ["rosa", "luis", "tom"], spouseIds: [] }) ?? "",
    /up to 2 parents/,
  );
});

// --------------------------------------------------------- describeRelationships

test("describeRelationships with nobody picked", () => {
  assert.equal(describeRelationships(empty(), names), "No relatives picked yet");
});

test("describeRelationships with one and two parents", () => {
  assert.equal(describeRelationships({ parentIds: ["rosa"], spouseIds: [] }, names), "Child of Rosa");
  assert.equal(
    describeRelationships({ parentIds: ["rosa", "luis"], spouseIds: [] }, names),
    "Child of Rosa and Luis",
  );
});

test("describeRelationships with spouses only", () => {
  assert.equal(describeRelationships({ parentIds: [], spouseIds: ["ana"] }, names), "Married to Ana");
  assert.equal(
    describeRelationships({ parentIds: [], spouseIds: ["ana", "bea"] }, names),
    "Married to Ana and Bea",
  );
  assert.equal(
    describeRelationships({ parentIds: [], spouseIds: ["ana", "bea", "tom"] }, names),
    "Married to Ana, Bea and Tom",
  );
});

test("describeRelationships with parents and a spouse", () => {
  assert.equal(
    describeRelationships({ parentIds: ["rosa", "luis"], spouseIds: ["ana"] }, names),
    "Child of Rosa and Luis, married to Ana",
  );
  assert.equal(
    describeRelationships({ parentIds: ["rosa"], spouseIds: ["ana", "bea"] }, names),
    "Child of Rosa, married to Ana and Bea",
  );
});

test("describeRelationships falls back for unknown names", () => {
  assert.equal(
    describeRelationships({ parentIds: ["ghost"], spouseIds: [] }, names),
    "Child of someone",
  );
});

// ---------------------------------------------------------- sortPeopleForPicking

test("sortPeopleForPicking puts the oldest first, unknown years last, then by name", () => {
  const people = [
    { id: "a", name: "Zed", born: null },
    { id: "b", name: "Mia", born: 1990 },
    { id: "c", name: "Abe" },
    { id: "d", name: "Rosa", born: 1948 },
    { id: "e", name: "Luis", born: 1948 },
    { id: "f", name: "ana", born: 1990 },
  ];
  const sorted = sortPeopleForPicking(people);
  assert.deepEqual(
    sorted.map((p) => p.name),
    ["Luis", "Rosa", "ana", "Mia", "Abe", "Zed"],
  );
  assert.equal(people[0].name, "Zed", "input is not mutated");
  assert.notEqual(sorted, people);
});

test("sortPeopleForPicking handles an empty list", () => {
  assert.deepEqual(sortPeopleForPicking([]), []);
});

// ------------------------------------------------------------------ acceptPhotos

const jpg = (name, size = 1000) => ({ name, size, type: "image/jpeg" });

test("photo constants match the contract", () => {
  assert.equal(MAX_PHOTOS, 6);
  assert.equal(MAX_PHOTO_BYTES, 10 * 1024 * 1024);
  assert.deepEqual(PHOTO_TYPES, [
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
    "image/gif",
  ]);
});

test("acceptPhotos accepts ordinary photos of every allowed type", () => {
  const incoming = PHOTO_TYPES.map((type, i) => ({ name: `p${i}`, size: 10, type }));
  const { accepted, errors } = acceptPhotos([], incoming);
  assert.deepEqual(accepted, [0, 1, 2, 3, 4, 5]);
  assert.deepEqual(errors, []);
});

test("acceptPhotos rejects a file of the wrong type with a friendly message", () => {
  const { accepted, errors } = acceptPhotos([], [
    { name: "notes.pdf", size: 10, type: "application/pdf" },
    jpg("ok.jpg"),
  ]);
  assert.deepEqual(accepted, [1]);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /notes\.pdf/);
  assert.match(errors[0], /isn't a photo/);
});

test("acceptPhotos falls back on the extension when the browser gives no type", () => {
  const { accepted, errors } = acceptPhotos([], [
    { name: "IMG_0001.HEIC", size: 10, type: "" },
    { name: "mystery", size: 10, type: "" },
  ]);
  assert.deepEqual(accepted, [0]);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /mystery/);
});

test("photoTypeFor uses the file's own type, else its extension, else null", () => {
  assert.equal(photoTypeFor({ name: "a.HEIF", type: "" }), "image/heif");
  assert.equal(photoTypeFor({ name: "a.txt", type: "" }), null);
  assert.equal(photoTypeFor({ name: "a.jpg", type: "image/webp" }), "image/webp");
  assert.equal(photoTypeFor({ name: "a.jpg", type: "  IMAGE/JPEG " }), "image/jpeg");
  assert.equal(photoTypeFor({ name: "IMG_1.JPeG", type: " " }), "image/jpeg");
  assert.equal(photoTypeFor({ name: "noextension", type: "" }), null);
  assert.equal(photoTypeFor({ name: "heic", type: "" }), null);
  assert.equal(photoTypeFor({ name: "a.heic.txt", type: "" }), null);
  for (const [ext, type] of [
    ["jpg", "image/jpeg"],
    ["png", "image/png"],
    ["webp", "image/webp"],
    ["heic", "image/heic"],
    ["gif", "image/gif"],
  ]) {
    assert.equal(photoTypeFor({ name: `x.${ext}`, type: "" }), type);
    assert.ok(PHOTO_TYPES.includes(type));
  }
});

test("acceptPhotos keeps a typeless .heic and rejects a typeless .txt", () => {
  const { accepted, errors } = acceptPhotos([], [
    { name: "IMG_0002.heic", size: 10, type: "" },
    { name: "notes.txt", size: 10, type: "" },
  ]);
  assert.deepEqual(accepted, [0]);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /notes\.txt/);
  assert.match(errors[0], /isn't a photo/);
});

test("acceptPhotos rejects an oversize file and says the limit", () => {
  const { accepted, errors } = acceptPhotos([], [
    jpg("huge.jpg", MAX_PHOTO_BYTES + 1),
    jpg("edge.jpg", MAX_PHOTO_BYTES),
  ]);
  assert.deepEqual(accepted, [1]);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /huge\.jpg/);
  assert.match(errors[0], /too big/);
  assert.match(errors[0], /10 MB/);
});

test("acceptPhotos rejects an empty file", () => {
  const { accepted, errors } = acceptPhotos([], [jpg("blank.jpg", 0)]);
  assert.deepEqual(accepted, []);
  assert.match(errors[0], /blank\.jpg/);
});

test("acceptPhotos stops at the cap of 6, counting what is already there", () => {
  const current = [jpg("a"), jpg("b"), jpg("c"), jpg("d")];
  const { accepted, errors } = acceptPhotos(current, [jpg("e"), jpg("f"), jpg("g"), jpg("h")]);
  assert.deepEqual(accepted, [0, 1]);
  assert.deepEqual(errors, ["“g” and “h” weren't added: a memory can hold up to 6 photos."]);
});

test("files over the cap share one line, after the per-file problems", () => {
  const current = ["a", "b", "c", "d", "e"].map((n) => jpg(n));
  const { accepted, errors } = acceptPhotos(current, [
    jpg("f"),
    jpg("g"),
    { name: "notes.pdf", size: 10, type: "application/pdf" },
    jpg("h"),
  ]);
  assert.deepEqual(accepted, [0]);
  assert.equal(errors.length, 2);
  assert.match(errors[0], /notes\.pdf/);
  assert.equal(errors[1], "“g” and “h” weren't added: a memory can hold up to 6 photos.");
});

test("overCapMessage names one, two, three, then counts the rest", () => {
  assert.equal(overCapMessage([]), "");
  assert.equal(overCapMessage(["g.png"]), "“g.png” wasn't added: a memory can hold up to 6 photos.");
  assert.equal(
    overCapMessage(["photo-5.png", "photo-6.png"]),
    "“photo-5.png” and “photo-6.png” weren't added: a memory can hold up to 6 photos.",
  );
  assert.equal(
    overCapMessage(["a", "b", "c"]),
    "“a”, “b” and “c” weren't added: a memory can hold up to 6 photos.",
  );
  assert.equal(
    overCapMessage(["a", "b", "c", "d", "e"]),
    "“a”, “b”, “c” and 2 more weren't added: a memory can hold up to 6 photos.",
  );
  assert.equal(overCapMessage([""]), "“That file” wasn't added: a memory can hold up to 6 photos.");
});

test("a pick of twelve onto an empty form keeps six and says one line about the rest", () => {
  const incoming = Array.from({ length: 12 }, (_, i) => jpg(`p${i + 1}.jpg`));
  const { accepted, errors } = acceptPhotos([], incoming);
  assert.deepEqual(accepted, [0, 1, 2, 3, 4, 5]);
  assert.deepEqual(errors, [
    "“p7.jpg”, “p8.jpg”, “p9.jpg” and 3 more weren't added: a memory can hold up to 6 photos.",
  ]);
});

test("acceptPhotos with a full set rejects everything", () => {
  const current = ["a", "b", "c", "d", "e", "f"].map((n) => jpg(n));
  const { accepted, errors } = acceptPhotos(current, [jpg("g")]);
  assert.deepEqual(accepted, []);
  assert.equal(errors.length, 1);
});

test("rejected files do not use up room under the cap", () => {
  const current = ["a", "b", "c", "d", "e"].map((n) => jpg(n));
  const { accepted } = acceptPhotos(current, [
    { name: "x.txt", size: 1, type: "text/plain" },
    jpg("big", MAX_PHOTO_BYTES + 5),
    jpg("fits"),
  ]);
  assert.deepEqual(accepted, [2]);
});

test("acceptPhotos skips exact duplicates by name and size, with a message", () => {
  const current = [jpg("wedding.jpg", 500)];
  const { accepted, errors } = acceptPhotos(current, [
    jpg("wedding.jpg", 500), // already there
    jpg("wedding.jpg", 501), // same name, different size: a different photo
    jpg("porch.jpg", 42),
    jpg("porch.jpg", 42), // repeated within the same pick
  ]);
  assert.deepEqual(accepted, [1, 2]);
  assert.equal(errors.length, 2);
  for (const e of errors) assert.match(e, /already added/);
});
