/**
 * Unit tests for the API-to-threads adapter (and that its output lays out):
 *
 *   cd web && node --no-warnings --test "components/threads/*.test.ts"
 *
 * Modules are imported by computed paths so tsc does not ask for `.ts`
 * import extensions (as in components/book/leafShape.test.ts).
 */
import assert from "node:assert/strict";
import test from "node:test";

const dataPath = "./data.ts";
const modelPath = "./model.ts";
const { normalizeColor, shortTitle, threadLabels, toThreadsData }: typeof import("./data") = await import(dataPath);
const { buildModel }: typeof import("./model") = await import(modelPath);

const NOW = 2026;

function person(id: string, extra: Record<string, unknown> = {}) {
  return { id, familyId: "f", name: id, character: { color: "#336699" }, parentIds: [], spouseIds: [], ...extra };
}

test("people take their colour from character.color, then the summary, then a fallback", () => {
  const d = toThreadsData(
    {
      people: [
        { id: "a", name: "A", born: 1950, color: "#000000" },
        { id: "b", name: "B", born: 1950, color: "#ABC" },
        { id: "c", name: "C", born: 1950, color: "not a colour" },
      ],
      moments: [],
    },
    { people: [person("a", { character: { color: "#C0503A" } })] as never },
    NOW,
  );
  assert.equal(d.people.find((p) => p.id === "a")!.color, "#c0503a");
  assert.equal(d.people.find((p) => p.id === "b")!.color, "#aabbcc");
  assert.match(d.people.find((p) => p.id === "c")!.color, /^#[0-9a-f]{6}$/);
});

test("moments without a usable year or without known people are kept off the timeline", () => {
  const d = toThreadsData(
    {
      people: [{ id: "a", name: "A", born: 1950 }],
      moments: [
        { id: "m1", title: "Fine", year: 1980, participantIds: ["a", "a", "ghost"] },
        { id: "m2", title: "No year", year: 0, participantIds: ["a"] },
        { id: "m3", title: "Nobody", year: 1990, participantIds: ["ghost"] },
        { id: "m4", title: "Far future", year: 3000, participantIds: ["a"] },
        { id: "m1", title: "Duplicate", year: 1981, participantIds: ["a"] },
        { id: "m5", title: "  ", year: Number.NaN, participantIds: [] },
      ],
    },
    null,
    NOW,
  );
  assert.deepEqual(
    d.moments.map((m) => [m.id, m.participantIds]),
    [["m1", ["a"]]],
  );
  assert.deepEqual(
    d.loose.map((m) => [m.id, m.reason]),
    [
      ["m2", "year"],
      ["m3", "people"],
      ["m4", "year"],
      ["m5", "year"],
    ],
  );
  assert.equal(d.loose[3].title, "Untitled moment");
});

test("unknown birth years are guessed from family and never come after the first moment", () => {
  const d = toThreadsData(
    {
      people: [
        { id: "dad", name: "Dad", born: 1960 },
        { id: "kid", name: "Kid", born: null },
        { id: "gran", name: "Gran", born: null },
        { id: "loner", name: "Loner" },
      ],
      moments: [{ id: "m", title: "Early", year: 1980, participantIds: ["kid", "loner"] }],
    },
    {
      people: [
        person("dad", { born: 1960, parentIds: ["gran"] }),
        person("kid", { born: null, parentIds: ["dad"] }),
        person("gran", { born: null }),
        person("loner", { born: null }),
      ] as never,
    },
    NOW,
  );
  const born = Object.fromEntries(d.people.map((p) => [p.id, p.born]));
  assert.equal(born.gran, 1960 - 27);
  assert.equal(born.kid, 1980, "dad + 27 = 1987, held to the 1980 moment");
  assert.equal(born.loner, 1979);
});

test("parent links that loop are cut, so the layout terminates", () => {
  const d = toThreadsData(
    { people: [], moments: [{ id: "m", title: "T", year: 2000, participantIds: ["a", "b"] }] },
    {
      people: [
        person("a", { born: 1950, parentIds: ["b", "a"] }),
        person("b", { born: 1950, parentIds: ["a"] }),
      ] as never,
    },
    NOW,
  );
  const a = d.people.find((p) => p.id === "a")!;
  const b = d.people.find((p) => p.id === "b")!;
  assert.ok(!(a.parentIds.includes("b") && b.parentIds.includes("a")));
  assert.ok(!a.parentIds.includes("a"));
  const model = buildModel(d.people, d.moments, NOW);
  assert.equal(model.strands.length, 2);
});

test("long names get short thread labels that stay distinct", () => {
  const labels = threadLabels([
    { id: "1", name: "Maria Guadalupe Fernández" },
    { id: "2", name: "Maria Guadalupe Rodríguez" },
    { id: "3", name: "Sam" },
    { id: "4", name: "Bartholomew-Alexander" },
  ]);
  assert.equal(labels.get("1"), "Maria F.");
  assert.equal(labels.get("2"), "Maria R.");
  assert.equal(labels.get("3"), "Sam");
  assert.equal([...labels.get("4")!].length, 16);
  assert.ok(labels.get("4")!.endsWith("…"));
});

test("titles and colours are tidied", () => {
  assert.equal(shortTitle("Short"), "Short");
  assert.ok([...shortTitle("x".repeat(80))].length <= 42);
  assert.equal(normalizeColor(" #FfF "), "#ffffff");
  assert.equal(normalizeColor("red"), null);
});

test("many people and moments lay out quickly", () => {
  const people = Array.from({ length: 60 }, (_, i) => ({ id: `p${i}`, name: `Person ${i}`, born: 1920 + (i % 70) }));
  const moments = Array.from({ length: 500 }, (_, i) => ({
    id: `m${i}`,
    title: `Moment ${i}`,
    year: 1950 + (i % 75),
    participantIds: [`p${i % 60}`, `p${(i * 7) % 60}`],
  }));
  const d = toThreadsData({ people, moments }, null, NOW);
  const t0 = performance.now();
  const model = buildModel(d.people, d.moments, NOW);
  assert.ok(performance.now() - t0 < 1500);
  assert.equal(model.knots.length, 500);
  assert.equal(model.strands.length, 60);
});
