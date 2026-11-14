/**
 * A person's book: book order, each moment's own slice of the bundle, the
 * cover and the closing spread, and the demo family's books.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * personBook.ts imports moment.ts, demoSpreads.ts and the demo family at
 * run time, by extensionless and "@/" paths as the app does; a resolve hook
 * maps those for node (the same rule the bundler applies).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as nodeModule from "node:module";
import { describe, it } from "node:test";
import type { BookResponse, Memory, MemoryResponse, Person, Prop, SetDef } from "@/lib/types";

type ResolveNext = (specifier: string, context: unknown) => unknown;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: unknown, next: ResolveNext): unknown }): unknown;
};
const webRoot = new URL("../../", import.meta.url);
registerHooks({
  resolve(specifier, context, next) {
    const spec = specifier.startsWith("@/") ? new URL(specifier.slice(2), webRoot).href : specifier;
    try {
      return next(spec, context);
    } catch (e) {
      if (/^(\.|file:)/.test(spec) && !/\.[cm]?[jt]sx?$/.test(spec)) return next(`${spec}.ts`, context);
      throw e;
    }
  },
});

type PersonBookModule = typeof import("./personBook");
type MomentModule = typeof import("./moment");
const paths = ["./personBook.ts", "./moment.ts"];
const pb: PersonBookModule = await import(paths[0]);
const mo: MomentModule = await import(paths[1]);

const sample: MemoryResponse = JSON.parse(
  readFileSync(new URL("../../public/samples/hymn_books.memory.json", import.meta.url), "utf8"),
);

const person = (id: string, name: string, color = "#334455", extra: Partial<Person> = {}): Person => ({
  id,
  familyId: "f",
  name,
  character: { color },
  ...extra,
});
const prop = (id: string): Prop => ({ id, name: id, size: "hand", art: {} });
const set = (id: string): SetDef => ({ id, name: id, layers: [] });

const memory = (id: string, year: number, title: string, extra: Partial<Memory> = {}): Memory => ({
  id,
  familyId: "f",
  title,
  year,
  ...extra,
});

/** A paper-only moment (no compiled scene yet) told by three people. */
const picnic = memory("m-picnic", 1975, "The picnic", {
  participantIds: ["dad", "rosa", "sam"],
  tellings: [
    { authorId: "rosa", text: "We ate on the hill.", photoIds: ["media/p1", "p2", "p3"] },
    { authorId: "dad", text: "It rained on the sandwiches." },
    { authorId: "sam", text: "I found a frog." },
  ],
  timeline: null,
  propIds: ["basket"],
});

const bundle: BookResponse = {
  person: person("dad", "David", "#3f6fb5"),
  // Deliberately out of order: the book must not trust the API's order.
  memories: [sample.memory, picnic],
  props: [...(sample.props ?? []), prop("basket"), prop("cap"), prop("unused")],
  sets: [...(sample.sets ?? []), set("unused_set")],
  people: [
    ...(sample.people ?? []),
    person("rosa", "Rosa", "#c0503a"),
    person("sam", "Sam", "#5a8f4e", { character: { color: "#5a8f4e", itemPropId: "cap" } }),
  ],
};

const ids = (xs: readonly { id: string }[] | undefined) => (xs ?? []).map((x) => x.id).sort();

describe("orderMoments", () => {
  it("orders by year, ties by title then id, undated last", () => {
    const got = pb.orderMoments([
      { id: "b", title: "Same", year: 1990 },
      { id: "z", title: "Undated", year: 0 },
      { id: "c", title: "Earlier", year: 1980 },
      { id: "a", title: "Same", year: 1990 },
      { id: "y", title: "Also undated", year: undefined as unknown as number },
      { id: "d", title: "Alpha", year: 1990 },
    ]);
    assert.deepEqual(
      got.map((m) => m.id),
      ["c", "d", "a", "b", "y", "z"],
    );
  });

  it("does not change its input", () => {
    const input = [
      { id: "2", title: "b", year: 2000 },
      { id: "1", title: "a", year: 1990 },
    ];
    pb.orderMoments(input);
    assert.deepEqual(
      input.map((m) => m.id),
      ["2", "1"],
    );
  });
});

describe("yearRange", () => {
  it("one year is just that year; several a range; none nothing", () => {
    assert.equal(pb.yearRange([1988]), "1988");
    assert.equal(pb.yearRange([1988, 1988]), "1988");
    assert.equal(pb.yearRange([2019, 1972, 1997]), "1972 – 2019");
    assert.equal(pb.yearRange([]), undefined);
    assert.equal(pb.yearRange([0, null, undefined, Number.NaN]), undefined);
    assert.equal(pb.yearRange([0, 1990]), "1990");
  });
});

describe("memorySlice", () => {
  it("cuts exactly what the API sends for that one moment", () => {
    const slice = pb.memorySlice(bundle, sample.memory);
    assert.equal(slice.memory, sample.memory);
    assert.deepEqual(ids(slice.people), ids(sample.people));
    assert.deepEqual(ids(slice.props), ids(sample.props));
    assert.deepEqual(ids(slice.sets), ids(sample.sets));
  });

  it("brings the tellers, participants and their signature items along for a paper-only moment", () => {
    const slice = pb.memorySlice(bundle, picnic);
    assert.deepEqual(ids(slice.people), ["dad", "rosa", "sam"]);
    assert.deepEqual(ids(slice.props), ["basket", "cap"]);
    assert.deepEqual(slice.sets, []);
  });

  it("skips ids the bundle does not have", () => {
    const slice = pb.memorySlice({ people: [], props: [], sets: [] }, sample.memory);
    assert.deepEqual([slice.people, slice.props, slice.sets], [[], [], []]);
  });

  it("names the scene's people, props and sets from the timeline when the stored lists lag", () => {
    const lagging = { ...sample.memory, propIds: [], setIds: [], participantIds: [], tellings: [] };
    const refs = pb.sceneRefs(lagging);
    assert.ok(refs.people.includes("dad") && refs.people.includes("mom"));
    assert.ok(refs.props.includes("hymn_books"));
    assert.ok(refs.sets.includes("church_steps_1988"));
  });
});

describe("personBook", () => {
  const photoUrl = (id: string) => `https://api.test/media/${id.replace(/^media\//, "")}`;
  const book = pb.personBook(bundle, { photoUrl, familyName: "The Maos" });

  it("one spread per moment in year order, then the closing spread", () => {
    assert.deepEqual(
      book.spreads.map((s) => s.id),
      ["m-picnic", "sample_hymn_books", "dad:end"],
    );
  });

  it("each moment's spread is the single-moment page's spread", () => {
    const spread = book.spreads[1];
    const alone = mo.momentSpread(sample, { photoUrl });
    assert.deepEqual(spread, alone);
    assert.equal(book.slices.sample_hymn_books.memory.id, "sample_hymn_books");
    assert.ok(spread.stage, "a compiled moment has a stage");
    assert.deepEqual(
      spread.tellings?.map((t) => t.id),
      ["dad", "mom"],
    );
  });

  it("a moment with no timeline is a paper spread with no stage, a label per teller and its photos", () => {
    const spread = book.spreads[0];
    assert.equal(spread.stage, undefined);
    assert.deepEqual(
      spread.tellings?.map((t) => t.name),
      ["Rosa", "David", "Sam"],
    );
    // Its own words, from the first teller, when no page text was written.
    assert.equal(spread.text, "");
    assert.equal(spread.tellings?.[0].text, "We ate on the hill.");
    assert.deepEqual(
      spread.photos.map((p) => p.src),
      ["https://api.test/media/p1", "https://api.test/media/p2"],
    );
  });

  it("the cover is the person's book, with the years and the family", () => {
    assert.deepEqual(book.cover, { title: "David’s book", kicker: "The Maos", subtitle: "1975 – 1988" });
  });

  it("closes with The end, the person's puppet and a line", () => {
    const end = book.spreads[2];
    assert.equal(end.kind, "end");
    assert.equal(end.title, "The end");
    assert.equal(end.year, 1988);
    assert.deepEqual(end.tellings, [{ id: "dad", name: "David", color: "#3f6fb5" }]);
    assert.match(end.text, /David/);
  });

  it("one year: just that year on the cover", () => {
    const one = pb.personBook({ ...bundle, memories: [sample.memory] });
    assert.equal(one.cover.subtitle, "1988");
    assert.equal(one.cover.kicker, undefined);
  });

  it("no moments: no spreads, no years", () => {
    const empty = pb.personBook({ person: person("lily", "Lily"), memories: [] });
    assert.deepEqual(empty.spreads, []);
    assert.deepEqual(empty.cover, { title: "Lily’s book" });
  });

  it("an undated moment (year 0) goes last and is left out of the years", () => {
    const undated = memory("m-undated", 0, "Some summer", { tellings: [{ authorId: "dad", text: "Hot." }] });
    const b = pb.personBook({ ...bundle, memories: [undated, sample.memory] });
    assert.deepEqual(
      b.spreads.map((s) => s.id),
      ["sample_hymn_books", "m-undated", "dad:end"],
    );
    assert.equal(b.cover.subtitle, "1988");
    assert.equal(b.spreads[2].year, 1988);
  });

  it("survives a bundle with missing lists", () => {
    const bare = pb.personBook({ person: person("dad", "David"), memories: [picnic] } as BookResponse);
    assert.equal(bare.spreads.length, 2);
    assert.deepEqual(bare.slices["m-picnic"].people, []);
  });
});

describe("the demo family's books", () => {
  const played = mo.momentSpread(sample);

  it("David's book: his moments in order, the church steps playing the sample", () => {
    const book = pb.demoPersonBook("david", played)!;
    assert.deepEqual(
      book.spreads.map((s) => s.id),
      ["m-sedan-1968", "m-steps-1987", "m-wedding-1989", "m-sofia-1990", "m-maple-1996", "david:end"],
    );
    assert.equal(book.cover.title, "David’s book");
    assert.equal(book.cover.subtitle, "1968 – 1996");
    const steps = book.spreads[1];
    // The hand-made words and photos, with the sample's labels and stage.
    assert.match(steps.text, /hymn books/);
    assert.equal(steps.photos.length, 2);
    assert.deepEqual(steps.tellings, played.tellings);
    assert.deepEqual(steps.stage, played.stage);
  });

  it("a moment with no hand-made page gets a short line from its year and tellers", () => {
    const book = pb.demoPersonBook("david")!;
    const sedan = book.spreads[0];
    assert.equal(sedan.text, "It was 1968. Here is how it went, just as it was told.");
    // The tellers are printed under it ("as told by Rosa & David").
    assert.deepEqual(sedan.tellers, ["Rosa", "David"]);
    assert.equal(sedan.stage, undefined);
    assert.equal(pb.demoLine({ year: 1990, tellers: [] }), "It was 1990, and it went just like this.");
    // Without the sample the church steps are paper only.
    assert.equal(book.spreads[1].stage, undefined);
  });

  it("Rosa's book", () => {
    const book = pb.demoPersonBook("rosa")!;
    assert.equal(book.spreads[0].id, "m-dance-1960");
    assert.equal(book.spreads.at(-1)?.kind, "end");
    assert.equal(book.cover.kicker, "The Alvarez–Moore family");
  });

  it("someone not in the demo family has no book", () => {
    assert.equal(pb.demoPersonBook("nobody"), null);
  });

  it("one demo moment as its own book", () => {
    const one = pb.demoMomentBook("m-steps-1987", played)!;
    assert.deepEqual(
      one.spreads.map((s) => s.id),
      ["m-steps-1987", "m-steps-1987:end"],
    );
    assert.deepEqual(one.spread.stage, played.stage);
    assert.equal(one.cover.kicker, "A moment from 1987");
    assert.equal(pb.demoMomentBook("m-wedding-1989")?.spread.stage, undefined);
    assert.equal(pb.demoMomentBook("nope"), null);
  });
});
