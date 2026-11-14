/**
 * Where a book comes from: every branch of resolveBook, with a stub API and
 * stub session helpers.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";
import type { FamilySummary, MeResponse } from "@/lib/types";

type Source = typeof import("./bookSource");
const paths = ["./bookSource.ts"];
const s: Source = await import(paths[0]);

const fam = (id: string, name = id): FamilySummary => ({ id, name, role: "member", personId: "p" });
const meWith = (...families: FamilySummary[]): MeResponse => ({ user: { id: "u", email: "u@x", name: "U" }, families });

/** An ApiError stand-in: what resolveBook reads is `status` and `detail`. */
const apiError = (status: number, detail?: string) => Object.assign(new Error(detail ?? `HTTP ${status}`), { status, detail: detail ?? "" });

function harness(opts: {
  me?: () => Promise<MeResponse>;
  load?: (fid: string) => Promise<string>;
  remembered?: string | null;
}) {
  const calls = { me: 0, load: [] as string[], remembered: [] as string[] };
  const deps = {
    api: {
      me: async () => {
        calls.me++;
        return (opts.me ?? (async () => meWith(fam("f1"), fam("f2"))))();
      },
    },
    session: {
      // The real pickFamily's rule: the preferred family when it is one of theirs, else the first.
      pickFamily: (families: FamilySummary[], preferred: string | null | undefined) =>
        (preferred ? families.find((f) => f.id === preferred) : undefined) ?? families[0] ?? null,
      recallFamily: () => opts.remembered ?? null,
      rememberFamily: (fid: string) => void calls.remembered.push(fid),
    },
    load: async (fid: string) => {
      calls.load.push(fid);
      return (opts.load ?? (async (f: string) => `book of ${f}`))(fid);
    },
  };
  return { deps, calls };
}

const scope = (o: Partial<{ demo: boolean; family: string | null; mock: boolean }> = {}) => ({
  demo: false,
  family: null,
  mock: false,
  ...o,
});

describe("resolveBook", () => {
  it("mock mode is the demo, with no API call", async () => {
    const { deps, calls } = harness({});
    assert.deepEqual(await s.resolveBook(scope({ mock: true, family: "f1" }), deps), { kind: "demo", why: "mock" });
    assert.equal(calls.me, 0);
    assert.deepEqual(calls.load, []);
  });

  it("?demo=1 is the demo, with no API call", async () => {
    const { deps, calls } = harness({});
    assert.deepEqual(await s.resolveBook(scope({ demo: true }), deps), { kind: "demo", why: "chosen" });
    assert.equal(calls.me, 0);
  });

  it("?family= wins over the remembered family, and is remembered when it is the user's", async () => {
    const { deps, calls } = harness({ remembered: "f1" });
    const r = await s.resolveBook(scope({ family: "f2" }), deps);
    assert.deepEqual(r, { kind: "family", familyId: "f2", family: fam("f2"), data: "book of f2" });
    assert.deepEqual(calls.load, ["f2"]);
    assert.deepEqual(calls.remembered, ["f2"]);
  });

  it("?family= the user is not in: the API's 404 is not-found, nothing remembered", async () => {
    const { deps, calls } = harness({
      load: async () => {
        throw apiError(404, "Not found");
      },
    });
    assert.deepEqual(await s.resolveBook(scope({ family: "someone-elses" }), deps), { kind: "not-found" });
    assert.deepEqual(calls.remembered, []);
  });

  it("?family= loads even when /me fails, but is not remembered", async () => {
    const { deps, calls } = harness({
      me: async () => {
        throw apiError(500);
      },
    });
    const r = await s.resolveBook(scope({ family: "f9" }), deps);
    assert.deepEqual(r, { kind: "family", familyId: "f9", family: null, data: "book of f9" });
    assert.deepEqual(calls.remembered, []);
  });

  it("?family= while signed out is signed-out", async () => {
    const { deps } = harness({
      me: async () => {
        throw apiError(401);
      },
      load: async () => {
        throw apiError(401);
      },
    });
    assert.deepEqual(await s.resolveBook(scope({ family: "f1" }), deps), { kind: "signed-out" });
  });

  it("signed in without ?family=: the remembered family when it is still theirs", async () => {
    const { deps, calls } = harness({ remembered: "f2" });
    const r = await s.resolveBook(scope(), deps);
    assert.equal(r.kind, "family");
    assert.deepEqual(calls.load, ["f2"]);
    // Picking is not choosing: nothing new is remembered.
    assert.deepEqual(calls.remembered, []);
  });

  it("signed in without ?family=: the first family when the remembered one is gone", async () => {
    const { deps, calls } = harness({ remembered: "gone" });
    const r = await s.resolveBook(scope(), deps);
    assert.deepEqual(r, { kind: "family", familyId: "f1", family: fam("f1"), data: "book of f1" });
    assert.deepEqual(calls.load, ["f1"]);
  });

  it("signed out (401 from /me)", async () => {
    const { deps, calls } = harness({
      me: async () => {
        throw apiError(401);
      },
    });
    assert.deepEqual(await s.resolveBook(scope(), deps), { kind: "signed-out" });
    assert.deepEqual(calls.load, []);
  });

  it("signed in with no family", async () => {
    const { deps, calls } = harness({ me: async () => meWith() });
    assert.deepEqual(await s.resolveBook(scope(), deps), { kind: "no-family" });
    assert.deepEqual(calls.load, []);
  });

  it("a /me without a families list counts as no family", async () => {
    const { deps } = harness({ me: async () => ({ user: { id: "u", email: "e", name: "n" } }) as unknown as MeResponse });
    assert.deepEqual(await s.resolveBook(scope(), deps), { kind: "no-family" });
  });

  it("unreachable (status 0) from /me or from the book", async () => {
    const offline = async () => {
      throw apiError(0);
    };
    assert.deepEqual(await s.resolveBook(scope(), harness({ me: offline }).deps), { kind: "unreachable" });
    assert.deepEqual(await s.resolveBook(scope(), harness({ load: offline }).deps), { kind: "unreachable" });
    assert.deepEqual(await s.resolveBook(scope({ family: "f1" }), harness({ load: offline }).deps), { kind: "unreachable" });
  });

  it("a person or moment that is not there is not-found", async () => {
    const { deps } = harness({
      load: async () => {
        throw apiError(404, "Not found");
      },
    });
    assert.deepEqual(await s.resolveBook(scope(), deps), { kind: "not-found" });
  });

  it("other 4xx show the API's sentence", async () => {
    const { deps } = harness({
      load: async () => {
        throw apiError(403, "You don't have permission to do that.");
      },
    });
    assert.deepEqual(await s.resolveBook(scope(), deps), { kind: "error", detail: "You don't have permission to do that." });
  });

  it("5xx and unknown failures show a friendly sentence", async () => {
    const { deps } = harness({
      load: async () => {
        throw apiError(500, "Traceback (most recent call last)");
      },
    });
    assert.deepEqual(await s.resolveBook(scope(), deps), { kind: "error", detail: s.BOOK_ERROR });
    const odd = harness({
      me: async () => {
        throw new TypeError("boom");
      },
    });
    assert.deepEqual(await s.resolveBook(scope(), odd.deps), { kind: "error", detail: s.BOOK_ERROR });
  });

  it("an aborted load is rethrown, never settled", async () => {
    const abort = async () => {
      throw Object.assign(new Error("aborted"), { name: "AbortError" });
    };
    await assert.rejects(s.resolveBook(scope(), harness({ load: abort }).deps), { name: "AbortError" });
    await assert.rejects(s.resolveBook(scope({ family: "f1" }), harness({ load: abort }).deps), { name: "AbortError" });
    await assert.rejects(s.resolveBook(scope(), harness({ me: abort }).deps), { name: "AbortError" });
  });
});
