/**
 * Tests for cleaning people before the tree draws them, and for deciding what
 * the home screen shows from the API's answers.
 *
 *   cd web && node --no-warnings --test "components/tree/*.test.ts"
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type TreeData = typeof import("./treeData");
type HomeData = typeof import("./homeData");
type StitchApi = import("@/lib/api").StitchApi;
type MeResponse = import("@/lib/types").MeResponse;
type TreeResponse = import("@/lib/types").TreeResponse;
const treeDataPath = "./treeData.ts";
const homeDataPath = "./homeData.ts";
const { cleanColor, toTreePeople }: TreeData = await import(treeDataPath);
const { loadHome, personHref, threadsHref, viewForError }: HomeData = await import(homeDataPath);
type NameFit = typeof import("./nameFit");
const nameFitPath = "./nameFit.ts";
const { NAME_MIN, NAME_SIZE, nameExtra, nameFit }: NameFit = await import(nameFitPath);

describe("toTreePeople", () => {
  it("keeps the first of repeated ids and drops links to people not in the tree", () => {
    const out = toTreePeople([
      { id: "a", name: "Ana", parentIds: ["ghost"], spouseIds: ["b", "zed"], character: { color: "#123456" } },
      { id: "b", name: "Ben", spouseIds: ["a"], character: { color: "#654321" } },
      { id: "a", name: "Second Ana", character: { color: "#000000" } },
    ]);
    assert.deepEqual(
      out.map((p) => p.id),
      ["a", "b"],
    );
    assert.equal(out[0].name, "Ana");
    assert.deepEqual(out[0].parentIds, []);
    assert.deepEqual(out[0].spouseIds, ["b"]);
  });

  it("orders people oldest first, keeping data order for unknown years", () => {
    const out = toTreePeople([
      { id: "young", born: 1990 },
      { id: "unknown1" },
      { id: "old", born: 1930 },
      { id: "unknown2", born: null },
    ]);
    assert.deepEqual(
      out.map((p) => p.id),
      ["old", "young", "unknown1", "unknown2"],
    );
  });

  it("gives everyone a drawable sheet even when theirs is missing or odd", () => {
    const [a, b, c] = toTreePeople([
      { id: "a", name: "  Rosa   Maria  ", character: { color: "#ABC", item: "  red scarf ", build: "tall" } },
      { id: "b", name: "", character: null },
      { id: "c", name: "Cy", character: { color: "rebeccapurple", item: "   ", build: "giant" } },
    ]);
    assert.deepEqual(a.character, { color: "#aabbcc", item: "red scarf", build: "tall" });
    assert.equal(a.name, "Rosa Maria");
    assert.equal(b.name, "Someone");
    assert.match(b.character.color, /^#[0-9a-f]{6}$/);
    assert.match(c.character.color, /^#[0-9a-f]{6}$/);
    assert.equal(c.character.item, null);
    assert.equal(c.character.build, undefined);
    // The fallback colour is stable for a person.
    assert.equal(toTreePeople([{ id: "c" }])[0].character.color, c.character.color);
  });

  it("cleans hex colours", () => {
    assert.equal(cleanColor("#3F6FB5"), "#3f6fb5");
    assert.equal(cleanColor(" #fff "), "#ffffff");
    assert.equal(cleanColor("#12345"), null);
    assert.equal(cleanColor(undefined), null);
  });
});

/** A client whose me() and getTree() answer from the given functions. */
function client(me: () => Promise<MeResponse>, getTree: (fid: string) => Promise<TreeResponse>) {
  return { me, getTree } as Pick<StitchApi, "me" | "getTree">;
}

const user = { id: "u1", email: "d@example.com", name: "David" };
const twoFamilies: MeResponse = {
  user,
  families: [
    { id: "maos", name: "The Maos", role: "admin", personId: "dad" },
    { id: "lees", name: "The Lees", role: "member", personId: null },
  ],
};
const treeOf = (fid: string): TreeResponse => ({
  familyId: fid,
  familyName: fid === "maos" ? "The Maos" : "The Lees",
  people: [{ id: "p", familyId: fid, name: "P", character: { color: "#123456" } }],
});
const fail = (status: number, detail?: string) => () => Promise.reject({ status, detail });

describe("loadHome", () => {
  it("opens the remembered family when the user still belongs to it", async () => {
    const asked: string[] = [];
    const v = await loadHome(
      client(
        async () => twoFamilies,
        async (fid) => {
          asked.push(fid);
          return treeOf(fid);
        },
      ),
      "lees",
    );
    assert.equal(v.kind, "tree");
    assert.deepEqual(asked, ["lees"]);
    if (v.kind === "tree") {
      assert.equal(v.familyName, "The Lees");
      assert.equal(v.myPersonId, null);
      assert.equal(v.families.length, 2);
    }
  });

  it("falls back to the first family for a stale or missing choice", async () => {
    const v = await loadHome(client(async () => twoFamilies, async (fid) => treeOf(fid)), "gone");
    assert.equal(v.kind === "tree" && v.familyId, "maos");
    assert.equal(v.kind === "tree" && v.myPersonId, "dad");
  });

  it("is signed out on a 401, offline on status 0, and an error otherwise", async () => {
    const tree = async (fid: string) => treeOf(fid);
    assert.deepEqual(await loadHome(client(fail(401), tree), null), { kind: "signed-out" });
    assert.deepEqual(await loadHome(client(fail(0), tree), null), { kind: "offline" });
    assert.deepEqual(await loadHome(client(fail(500, "The server tripped."), tree), null), {
      kind: "error",
      message: "The server tripped.",
    });
    assert.deepEqual(await loadHome(client(async () => twoFamilies, fail(401)), null), { kind: "signed-out" });
    assert.deepEqual(await loadHome(client(async () => twoFamilies, fail(0)), null), { kind: "offline" });
  });

  it("has no family to show for someone in none", async () => {
    const v = await loadHome(client(async () => ({ user, families: [] }), fail(404)), null);
    assert.deepEqual(v, { kind: "no-family" });
  });

  it("turns anything thrown into a friendly view", () => {
    assert.equal(viewForError(new Error("boom")).kind, "error");
    assert.equal(viewForError("nope").kind, "error");
  });
});

describe("nameFit", () => {
  it("keeps short names in the full hand size on one line", () => {
    for (const name of ["Ana", "Joseph", "Christina"]) assert.deepEqual(nameFit(name), { lines: [name], size: NAME_SIZE });
  });

  it("sets slightly longer names a little smaller", () => {
    const { lines, size } = nameFit("Christopher");
    assert.deepEqual(lines, ["Christopher"]);
    assert.ok(size < NAME_SIZE && size >= 24);
  });

  it("breaks long names over two balanced lines, at a space or after a hyphen", () => {
    assert.deepEqual(nameFit("Margaret Fitzgerald").lines, ["Margaret", "Fitzgerald"]);
    assert.deepEqual(nameFit("Great-grandpa Chinedu").lines, ["Great-grandpa", "Chinedu"]);
    assert.deepEqual(nameFit("Okonkwo-Fitzgeraldson").lines, ["Okonkwo-", "Fitzgeraldson"]);
    assert.ok(nameExtra("Margaret Fitzgerald") > 0);
    assert.equal(nameExtra("Ana"), 0);
  });

  it("never goes below the smallest size", () => {
    const fit = nameFit("Wolfeschlegelsteinhausenbergerdorff");
    assert.equal(fit.lines.length, 1);
    assert.equal(fit.size, NAME_MIN);
    assert.equal(nameFit("A B").lines.length, 1);
  });
});

describe("links", () => {
  it("carries the family to people and threads", () => {
    assert.equal(personHref("grandma rosa", "demo family"), "/people/grandma%20rosa?family=demo%20family");
    assert.equal(personHref("luis"), "/people/luis");
    assert.equal(threadsHref("maos"), "/threads?family=maos");
    assert.equal(threadsHref(null), "/threads");
    assert.equal(threadsHref(null, { demo: true }), "/threads?demo=1");
    assert.equal(personHref("grandma rosa", null, { demo: true }), "/people/grandma%20rosa?demo=1");
  });
});
