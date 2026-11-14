/**
 * A moment as a spread: tellers, page text, pull tabs, cover and the shared
 * link's teller, against the reference sample
 * (web/public/samples/hymn_books.*.json).
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The module is imported by a computed path so tsc does not ask for a `.ts`
 * import extension (see leafShape.test.ts).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import type { Interaction, MemoryResponse, Screenplay, Timeline } from "@/lib/types";

type Moment = typeof import("./moment");
const paths = ["./moment.ts"];
const m: Moment = await import(paths[0]);

const sample = (suffix: string) =>
  JSON.parse(readFileSync(new URL(`../../public/samples/hymn_books.${suffix}.json`, import.meta.url), "utf8"));
const memory: MemoryResponse = sample("memory");
const timeline: Timeline = sample("timeline");
const screenplay: Screenplay = sample("screenplay");
const people = memory.people ?? [];
const tellings = memory.memory.tellings ?? [];

describe("momentSpread (hymn_books)", () => {
  const spread = m.momentSpread(memory, { timeline, screenplayShots: screenplay.shots });

  it("prints the shots' page text", () => {
    assert.equal(spread.text, "One Sunday in 1988, a stack of hymn books changed everything.");
  });

  it("tapes a label per telling, default narrator first, with their own words", () => {
    assert.deepEqual(
      spread.tellings?.map((t) => [t.id, t.name, t.color]),
      [
        ["dad", "David", "#3f6fb5"],
        ["mom", "Ana", "#c0504d"],
      ],
    );
    assert.deepEqual(spread.tellers, ["David", "Ana"]);
    assert.equal(spread.tellings?.[1].text, tellings.find((t) => t.authorId === "mom")?.text.trim());
  });

  it("cuts a slit for the pull tab on the stage page", () => {
    assert.deepEqual(spread.stage, { pullTabs: [{ id: "drop_books", label: "Pull to drop the books", at: [0.85, 0.82] }] });
  });

  it("leaves out photos it cannot show", () => {
    assert.deepEqual(spread.photos, []);
  });

  it("tapes in at most two photos, with URLs", () => {
    const withPhotos: MemoryResponse = {
      ...memory,
      memory: {
        ...memory.memory,
        tellings: tellings.map((t, i) => ({ ...t, photoIds: i ? ["media/b", "media/c"] : ["media/a", "media/b"] })),
      },
    };
    const s = m.momentSpread(withPhotos, { timeline, photoUrl: (id) => `/media/${id}` });
    assert.deepEqual(
      s.photos.map((p) => [p.id, p.src]),
      [
        ["media/a", "/media/media/a"],
        ["media/b", "/media/media/b"],
      ],
    );
  });

  it("has no stage without a timeline", () => {
    const bare: MemoryResponse = { ...memory, memory: { ...memory.memory, timeline: null } };
    const s = m.momentSpread(bare);
    assert.equal(s.stage, undefined);
    // Still a label per telling, from the tellings themselves.
    assert.deepEqual(s.tellings?.map((t) => t.id), ["dad", "mom"]);
  });
});

describe("momentPageText", () => {
  it("falls back to the screenplay's page text", () => {
    const bare = (timeline.shots ?? []).map((s) => ({ ...s, pageText: null }));
    assert.equal(m.momentPageText(bare, [{ pageText: "From the screenplay." }]), "From the screenplay.");
  });

  it("is empty with no page text anywhere, so the page prints the teller's words", () => {
    assert.equal(m.momentPageText([{ pageText: "  " }], []), "");
    assert.equal(m.momentPageText(null), "");
  });

  it("joins several shots in order", () => {
    assert.equal(m.momentPageText([{ pageText: "One." }, { pageText: null }, { pageText: " Two. " }]), "One. Two.");
  });
});

describe("momentTellers", () => {
  it("uses the tellings when there is no timeline", () => {
    assert.deepEqual(
      m.momentTellers(null, people, tellings).map((t) => t.id),
      ["dad", "mom"],
    );
  });

  it("puts the default narrator first", () => {
    assert.deepEqual(
      m.momentTellers({ ...timeline, defaultNarrator: "mom" }, people).map((t) => t.id),
      ["mom", "dad"],
    );
  });

  it("names an unknown person by id, in the fallback thread", () => {
    const [t] = m.momentTellers({ narrations: { zed: timeline.narrations!.dad }, defaultNarrator: null }, people);
    assert.deepEqual([t.id, t.name, t.color], ["zed", "zed", "#b9573d"]);
  });
});

describe("momentPullTabs", () => {
  const tab = timeline.interactions?.[0] as Interaction;

  it("clamps a tab onto the page, and places one with no position at the foot", () => {
    const tabs = m.momentPullTabs([
      { ...tab, id: "a", at: [1.4, -0.2] },
      { ...tab, id: "b", at: [] },
      { ...tab, id: "c", at: [Number.NaN, 0.5] },
    ]);
    assert.deepEqual(
      tabs.map((t) => t.at),
      [
        [1, 0],
        [0.85, 0.82],
        [0.85, 0.5],
      ],
    );
  });

  it("leaves out story-page interactions", () => {
    assert.deepEqual(m.momentPullTabs([{ ...tab, page: "story" }]), []);
    assert.deepEqual(m.momentPullTabs(null), []);
  });
});

describe("cover, closing page and names", () => {
  it("joins names like a person would", () => {
    assert.equal(m.joinNames(["Rosa", "Ana", "David"]), "Rosa, Ana & David");
    assert.equal(m.joinNames(["David", "Ana"]), "David & Ana");
    assert.equal(m.joinNames(["Ana"]), "Ana");
    assert.equal(m.joinNames([]), "");
  });

  it("titles a one-moment book with the moment", () => {
    const c = m.momentCover(m.momentSpread(memory, { timeline }));
    assert.deepEqual(c, { kicker: "A moment from 1988", title: "How I met your mother", subtitle: "as told by David & Ana" });
  });

  it("closes the book with the tellers", () => {
    const spread = m.momentSpread(memory, { timeline });
    const end = m.momentEnd(spread);
    assert.equal(end.kind, "end");
    assert.equal(end.text, "as David & Ana remember it");
    assert.equal(m.momentEnd({ ...spread, tellers: ["Ana"] }).text, "as Ana remembers it");
  });
});

describe("pickTeller (?narrator)", () => {
  const tellers = m.momentTellers(timeline, people);

  it("takes a person id or name, in any case", () => {
    assert.equal(m.pickTeller(tellers, "mom"), "mom");
    assert.equal(m.pickTeller(tellers, "ana"), "mom");
    assert.equal(m.pickTeller(tellers, " David "), "dad");
    assert.equal(m.pickTeller(tellers, "MOM"), "mom");
  });

  it("is null for no one it knows", () => {
    assert.equal(m.pickTeller(tellers, "rosa"), null);
    assert.equal(m.pickTeller(tellers, ""), null);
    assert.equal(m.pickTeller(tellers, null), null);
  });
});
