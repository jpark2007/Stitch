// Run with: cd web && node --test --experimental-strip-types "components/demo/__tests__/*.test.mjs"
import { describe, test } from "node:test";
import assert from "node:assert/strict";

import {
  AUDIENCE_WINDOW,
  HOLD_PATH,
  QUICK_DESTINATIONS,
  audienceHrefs,
  describeAudience,
  momentBookPerson,
} from "../audience.ts";

describe("audienceHrefs", () => {
  test("every quick destination takes ?family=", () => {
    assert.equal(audienceHrefs.home("demo_family"), "/?family=demo_family");
    assert.equal(audienceHrefs.threads("demo_family"), "/threads?family=demo_family");
    assert.equal(audienceHrefs.ask("demo_family"), "/ask?family=demo_family");
    assert.equal(audienceHrefs.films("demo_family"), "/films?family=demo_family");
    assert.equal(audienceHrefs.traditions("demo_family"), "/traditions?family=demo_family");
  });

  test("the quick list names a builder for each key, in order", () => {
    assert.deepEqual(QUICK_DESTINATIONS.map((d) => d.key), ["home", "threads", "ask", "films", "traditions"]);
    for (const d of QUICK_DESTINATIONS) assert.equal(typeof audienceHrefs[d.key], "function");
  });

  test("a person's book lies open", () => {
    assert.equal(audienceHrefs.person("demo_family", "grandma_rosa"), "/people/grandma_rosa?family=demo_family&state=open");
  });

  test("a moment opens in a book that holds it, or on its own page without a person", () => {
    assert.equal(
      audienceHrefs.moment("demo_family", "m-1", "dad"),
      "/people/dad?family=demo_family&memory=m-1",
    );
    assert.equal(audienceHrefs.moment("demo_family", "m-1", null), "/memories/m-1?family=demo_family");
  });

  test("films, the prefilled add page and the holding screen", () => {
    assert.equal(audienceHrefs.film("demo_family", "f 1"), "/films/f%201?family=demo_family");
    assert.equal(audienceHrefs.addDraft("demo_family"), "/add?family=demo_family&demoDraft=1");
    assert.equal(audienceHrefs.hold(), HOLD_PATH);
    assert.equal(HOLD_PATH, "/demo/hold");
  });

  test("ids and the family are URL-encoded", () => {
    assert.equal(audienceHrefs.person("a&b", "x/y"), "/people/x%2Fy?family=a%26b&state=open");
    assert.equal(audienceHrefs.moment("f", "m?1", "p#2"), "/people/p%232?family=f&memory=m%3F1");
  });

  test("one named window", () => {
    assert.equal(AUDIENCE_WINDOW, "stitch-audience");
  });
});

describe("momentBookPerson", () => {
  test("the first teller, else the first participant, else none", () => {
    assert.equal(momentBookPerson({ participantIds: ["a", "b"], tellingAuthorIds: ["b"] }), "b");
    assert.equal(momentBookPerson({ participantIds: ["a", "b"], tellingAuthorIds: [] }), "a");
    assert.equal(momentBookPerson({ participantIds: ["a"] }), "a");
    assert.equal(momentBookPerson({ participantIds: [] }), null);
    assert.equal(momentBookPerson({ participantIds: ["", "c"], tellingAuthorIds: [""] }), "c");
  });
});

describe("describeAudience", () => {
  const names = { grandma_rosa: "Rosa" };
  test("named pages", () => {
    assert.equal(describeAudience("/?family=f"), "Family tree");
    assert.equal(describeAudience("/"), "Family tree");
    assert.equal(describeAudience("/demo/hold"), "Holding screen");
    assert.equal(describeAudience("/threads?family=f"), "Threads");
    assert.equal(describeAudience("/ask?family=f&q=who"), "Ask the family");
    assert.equal(describeAudience("/films?family=f"), "Films");
    assert.equal(describeAudience("/films/f1?family=f"), "A film");
    assert.equal(describeAudience("/traditions?family=f"), "Traditions");
    assert.equal(describeAudience("/add?family=f&demoDraft=1"), "Add a memory");
    assert.equal(describeAudience("/add?family=f&run=r1"), "Add a memory: the studio at work");
    assert.equal(describeAudience("/memories/m1?family=f"), "A moment");
    assert.equal(describeAudience("about:blank"), "Opening…");
  });

  test("books by name when known", () => {
    assert.equal(describeAudience("/people/grandma_rosa?family=f&state=open", names), "Rosa’s book");
    assert.equal(describeAudience("/people/grandma_rosa?family=f&memory=m1", names), "Rosa’s book, at a moment");
    assert.equal(describeAudience("/people/nobody?family=f", names), "A book");
  });

  test("anything else is its path", () => {
    assert.equal(describeAudience("/settings/notifications?family=f"), "/settings/notifications");
  });
});
