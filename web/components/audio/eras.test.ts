/**
 * Era lookup (eras.ts) against the contract in shared/vocabulary.json.
 *
 *   cd web && node --no-warnings --test "components/audio/*.test.ts"
 *
 * The module is imported by a computed path so tsc does not ask for a `.ts`
 * import extension (see components/book/leafShape.test.ts).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

type Eras = typeof import("./eras");
const path = "./eras.ts";
const m: Eras = await import(path);

const vocab = JSON.parse(readFileSync(new URL("../../../shared/vocabulary.json", import.meta.url), "utf8")) as {
  eras: { id: string; from: number; to: number }[];
};

describe("era table", () => {
  it("matches shared/vocabulary.json ids and ranges, in order", () => {
    assert.deepEqual(
      m.ERAS.map((e) => ({ id: e.id, from: e.from, to: e.to })),
      vocab.eras.map((e) => ({ id: e.id, from: e.from, to: e.to })),
    );
  });

  it("is contiguous with no gaps or overlaps", () => {
    for (let i = 1; i < m.ERAS.length; i++) assert.equal(m.ERAS[i].from, m.ERAS[i - 1].to + 1);
  });
});

describe("eraForYear", () => {
  it("puts every boundary year in the right era", () => {
    for (const e of vocab.eras) {
      if (e.from > 0) assert.equal(m.eraForYear(e.from), e.id, `from ${e.from}`);
      assert.equal(m.eraForYear(e.to), e.id, `to ${e.to}`);
    }
  });

  it("maps typical years", () => {
    assert.equal(m.eraForYear(1912), "pre1940");
    assert.equal(m.eraForYear(1939), "pre1940");
    assert.equal(m.eraForYear(1940), "1940s");
    assert.equal(m.eraForYear(1967), "1960s");
    assert.equal(m.eraForYear(2019), "2010s");
    assert.equal(m.eraForYear(2026), "2020s");
    assert.equal(m.eraForYear(1969.9), "1960s");
  });

  it("has no era for a missing, zero or nonsense year", () => {
    for (const y of [null, undefined, 0, -5, NaN, Infinity, -Infinity, 10000]) {
      assert.equal(m.eraForYear(y as number | null | undefined), null, String(y));
    }
  });

  it("gives a mid year inside each era", () => {
    for (const e of m.ERAS) assert.equal(m.eraForYear(m.eraMidYear(e.id)), e.id);
  });

  it("recognises era ids", () => {
    assert.ok(m.isEraId("1980s"));
    assert.ok(!m.isEraId("1980"));
    assert.ok(!m.isEraId(null));
  });
});
