/**
 * Unit tests for the threads screen's links:
 *
 *   cd web && node --no-warnings --test "components/threads/*.test.ts"
 */
import assert from "node:assert/strict";
import * as nodeModule from "node:module";
import test from "node:test";

// links.ts imports "@/components/flow/session": map "@/" to web/ and add ".ts".
// (registerHooks is newer than this repo's @types/node, hence the local type.)
type Next = (specifier: string, context: object) => unknown;
const { registerHooks } = nodeModule as unknown as {
  registerHooks(hooks: { resolve(specifier: string, context: object, next: Next): unknown }): void;
};
const WEB = new URL("../../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) return nextResolve(new URL(`${specifier.slice(2)}.ts`, WEB).href, context);
    return nextResolve(specifier, context);
  },
});

const linksPath = "./links.ts";
const { momentHref, personHref, treeHref }: typeof import("./links") = await import(linksPath);

test("the demo family's moment and person links carry ?demo=1", () => {
  const demo = { demo: true };
  assert.equal(momentHref("m-steps-1987", demo), "/memories/m-steps-1987?demo=1");
  assert.equal(personHref("david", demo), "/people/david?demo=1");
});

test("a real family's links carry ?family=<id>", () => {
  const real = { familyId: "demo_family" };
  assert.equal(momentHref("demo_sunday_tamales", real), "/memories/demo_sunday_tamales?family=demo_family");
  assert.equal(personHref("dad", real), "/people/dad?family=demo_family");
});

test("a family id wins over the demo flag", () => {
  const both = { familyId: "f1", demo: true };
  assert.equal(momentHref("m", both), "/memories/m?family=f1");
  assert.equal(personHref("p", both), "/people/p?family=f1");
  assert.equal(treeHref(both), "/");
});

test("ids and family ids are URL-encoded", () => {
  assert.equal(momentHref("a b/c?d", { demo: true }), "/memories/a%20b%2Fc%3Fd?demo=1");
  assert.equal(personHref("é&x", { demo: true }), "/people/%C3%A9%26x?demo=1");
  assert.equal(momentHref("m#1", { familyId: "fam/1 &2" }), "/memories/m%231?family=fam%2F1%20%262");
  assert.equal(personHref("p", { familyId: "x?y=z" }), "/people/p?family=x%3Fy%3Dz");
});

test("with no scope, links carry none", () => {
  assert.equal(momentHref("m", {}), "/memories/m");
  assert.equal(personHref("p", { familyId: "" }), "/people/p");
  assert.equal(treeHref({}), "/");
});

test("the Family tree crumb opens the same demo family, or home for a real one", () => {
  assert.equal(treeHref({ demo: true }), "/?demo=1");
  assert.equal(treeHref({ familyId: "demo_family" }), "/");
  assert.equal(treeHref({ demo: false }), "/");
});
