// Run with: cd web && node --test "components/film/__tests__/*.test.mjs"
// The "Make a film" form's pure pieces (components/film/filmForm.ts), the
// film links, and the mock film routes the pages are built against, through
// the real API client. filmForm.ts imports "../flow/validation" without an
// extension (as the Next build wants), so this file first maps extensionless
// relative imports made from .ts files to the .ts file next to them.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
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
  FILM_ACCEPT,
  MAX_FILM_PHOTOS,
  MIN_FILM_PHOTOS,
  acceptFilmPhotos,
  filmRequest,
  filmServerErrors,
  photoCountLine,
  runQueue,
  sentIds,
  tallyUploads,
  uploadLine,
  validateFilmDraft,
} = await import("../filmForm.ts");
const { familyFromQuery, filmHref, filmsHref, newFilmHref } = await import("../filmLinks.ts");
const apiModule = await import("../../../lib/api.ts");

const SAMPLE = JSON.parse(fs.readFileSync(new URL("../../../public/samples/film.json", import.meta.url), "utf8"));

const photo = (name, size = 1000, type = "image/jpeg") => ({ name, size, type });
const photos = (n) => Array.from({ length: n }, (_, i) => photo(`p${i}.jpg`));

test("the photo bounds match lib/api.ts", () => {
  assert.equal(MIN_FILM_PHOTOS, apiModule.MIN_FILM_PHOTOS);
  assert.equal(MAX_FILM_PHOTOS, apiModule.MAX_FILM_PHOTOS);
  assert.equal(FILM_ACCEPT, "image/jpeg,image/png,image/webp");
});

test("acceptFilmPhotos keeps JPEG, PNG and WebP up to the cap and explains the rest", () => {
  const { accepted, errors } = acceptFilmPhotos(
    [photo("a.jpg")],
    [
      photo("a.jpg"),
      photo("b.png", 10, "image/png"),
      photo("c.heic", 10, "image/heic"),
      photo("d.webp", 10, ""),
      photo("e.jpg", 0),
      photo("f.jpg", 11 * 1024 * 1024),
      photo("g.gif", 10, "image/gif"),
    ],
  );
  assert.deepEqual(accepted, [1, 3]);
  assert.deepEqual(errors, [
    "“a.jpg” is already in.",
    "“c.heic” and “g.gif” aren’t photos we can use. Try JPEG, PNG or WebP.",
    "“e.jpg” looks empty. Try picking it again.",
    "“f.jpg” is over 10 MB.",
  ]);
});

test("acceptFilmPhotos stops at 40 and names what was left out", () => {
  const { accepted, errors } = acceptFilmPhotos(photos(38), [photo("x1.jpg"), photo("x2.jpg"), photo("x3.jpg"), photo("x4.jpg")]);
  assert.deepEqual(accepted, [0, 1]);
  assert.deepEqual(errors, ["“x3.jpg” and “x4.jpg” weren’t added: a film can use up to 40 photos."]);
  const many = acceptFilmPhotos(photos(40), photos(45).map((p, i) => photo(`y${i}.jpg`)));
  assert.equal(many.accepted.length, 0);
  assert.match(many.errors[0], /^“y0.jpg”, “y1.jpg”, “y2.jpg” and 42 more weren’t added/);
});

test("photoCountLine makes the 8-to-40 rule obvious", () => {
  assert.deepEqual(photoCountLine(0), { text: "Pick 8 to 40 photos from one day or trip.", ready: false });
  assert.deepEqual(photoCountLine(1), { text: "1 photo. Add at least 7 more.", ready: false });
  assert.deepEqual(photoCountLine(7), { text: "7 photos. Add at least 1 more.", ready: false });
  assert.deepEqual(photoCountLine(8), { text: "8 photos. Stitch picks the best of them.", ready: true });
  assert.deepEqual(photoCountLine(40), { text: "40 photos, the most one film can use.", ready: true });
});

test("validateFilmDraft checks photos, story, year and title", () => {
  const ok = { text: " We went to the lake. ", year: "1998", title: "" };
  assert.deepEqual(validateFilmDraft(ok, 12), {});
  const bad = validateFilmDraft({ text: "  ", year: "", title: "x".repeat(201) }, 3);
  assert.deepEqual(Object.keys(bad).sort(), ["photos", "text", "title", "year"]);
  assert.equal(bad.photos, "A film needs at least 8 photos. Add 5 more.");
  assert.equal(validateFilmDraft(ok, 0).photos, "Add 8 to 40 photos.");
  assert.equal(validateFilmDraft(ok, 41).photos, "A film can use up to 40 photos. Remove 1.");
  assert.equal(validateFilmDraft({ ...ok, year: "1700" }, 8).year, "Pick a year from 1800 on.");
  assert.equal(validateFilmDraft({ ...ok, year: String(new Date().getFullYear() + 1) }, 8).year, "That year hasn't happened yet.");
  assert.ok(validateFilmDraft({ ...ok, text: "x".repeat(20_001) }, 8).text);
});

test("filmRequest trims and keeps the photo order", () => {
  assert.deepEqual(filmRequest({ text: " Story. ", year: " 1998 ", title: "  " }, ["m1", "m2"]), {
    title: null,
    text: "Story.",
    year: 1998,
    photoIds: ["m1", "m2"],
  });
  assert.equal(filmRequest({ text: "s", year: "2001", title: " Lake " }, []).title, "Lake");
});

test("filmServerErrors puts photoIds on the photos and keeps the rest for the form", () => {
  assert.deepEqual(filmServerErrors({ photoIds: "Choose 8 to 40 photos", year: "Bad year", participantIds: "Too many" }), {
    fields: { photos: "Choose 8 to 40 photos", year: "Bad year" },
    rest: "Too many",
  });
  assert.deepEqual(filmServerErrors({}), { fields: {}, rest: null });
});

test("the upload tally, line and ids", () => {
  const items = [
    { status: "sent", mediaId: "m1" },
    { status: "sending", mediaId: null },
    { status: "waiting", mediaId: null },
    { status: "failed", mediaId: null },
  ];
  assert.deepEqual(tallyUploads(items), { total: 4, sent: 1, sending: 1, failed: 1, waiting: 1 });
  assert.equal(uploadLine(tallyUploads(items)), "Sending photos: 1 of 4 sent");
  assert.equal(uploadLine(tallyUploads([items[0], items[3]])), "One photo didn’t send. Try it again, or remove it.");
  assert.equal(uploadLine(tallyUploads([items[3], items[3]])), "2 photos didn’t send. Try them again, or remove them.");
  assert.equal(uploadLine(tallyUploads([items[0]])), "Photo sent.");
  assert.equal(uploadLine(tallyUploads([items[0], items[0]])), "All 2 photos sent.");
  assert.equal(uploadLine(tallyUploads([])), "");
  assert.equal(sentIds(items), null);
  assert.deepEqual(sentIds([items[0], { status: "sent", mediaId: "m2" }]), ["m1", "m2"]);
});

test("runQueue keeps at most three uploads in flight and runs every one", async () => {
  let inFlight = 0;
  let most = 0;
  const done = [];
  const keys = Array.from({ length: 10 }, (_, i) => `k${i}`);
  await runQueue(
    keys,
    async (key) => {
      inFlight += 1;
      most = Math.max(most, inFlight);
      await new Promise((r) => setTimeout(r, 2 + (key.length % 3)));
      inFlight -= 1;
      if (key === "k4") throw new Error("one fails");
      done.push(key);
    },
    3,
  );
  assert.equal(most, 3);
  assert.equal(done.length, 9, "a failure never stops the others");
  await runQueue([], async () => assert.fail("nothing to run"));
});

test("film links carry the family", () => {
  assert.equal(filmsHref("fam 1"), "/films?family=fam%201");
  assert.equal(filmsHref(), "/films");
  assert.equal(newFilmHref("f"), "/films/new?family=f");
  assert.equal(filmHref("a/b", "f"), "/films/a%2Fb?family=f");
  assert.equal(filmHref("x", ""), "/films/x");
  assert.equal(familyFromQuery("?family=f&x=1"), "f");
  assert.equal(familyFromQuery("family="), null);
  assert.equal(familyFromQuery(null), null);
});

// The mock film routes, as the film pages use them (filmApi wraps the mock
// backend's fetch the same way).
async function mockClient() {
  const { createMockBackend } = await import("../../flow/mock/mockBackend.ts");
  const { buildDemoSeed } = await import("../../flow/mock/demoSeed.ts");
  const memory = JSON.parse(
    fs.readFileSync(new URL("../../../public/samples/hymn_books.memory.json", import.meta.url), "utf8"),
  );
  let now = Date.parse("2026-11-14T12:00:00Z");
  const backend = createMockBackend({ seed: () => buildDemoSeed(memory, new Date(now)), stepDelayMs: 0, now: () => new Date(now) });
  const fetch = apiModule.withMockFilms(backend.fetch, { loadSample: async () => SAMPLE, now: () => now, readyAfterMs: 2000 });
  const api = apiModule.createApiClient({ baseUrl: "http://localhost:8000", fetch });
  return { api, tick: (ms) => (now += ms) };
}

test("mock: upload eight photos, make a film, poll it to ready, find it in the list", async () => {
  const { api, tick } = await mockClient();
  await api.login({ email: "david@example.com", password: "stitch-demo" });
  const me = await api.me();
  const fid = me.families[0].id;

  const listed = await api.listFilms(fid);
  assert.equal(listed.films.length, 1, "every family sees the sample film");
  assert.equal(listed.films[0].status, "ready");

  const ids = [];
  await runQueue(
    Array.from({ length: 8 }, (_, i) => i),
    async (i) => {
      const { id } = await api.uploadMedia(fid, new Blob([new Uint8Array([1, 2, 3])], { type: "image/jpeg" }));
      ids[i] = id;
    },
  );
  assert.equal(ids.filter(Boolean).length, 8);

  const draft = { text: "We drove to the lake. It rained. Then the sun came out.", year: "1998", title: "" };
  assert.deepEqual(validateFilmDraft(draft, ids.length), {});
  const film = await api.createFilm(fid, filmRequest(draft, ids));
  assert.equal(film.status, "processing");
  assert.equal((await api.getFilm(fid, film.id)).status, "processing");
  tick(2500);
  const ready = await api.getFilm(fid, film.id);
  assert.equal(ready.status, "ready");
  assert.ok(ready.shots.length > 0 && ready.words.length > 0);
  assert.deepEqual((await api.listFilms(fid)).films.map((f) => f.id), [film.id, SAMPLE.id]);

  // Too few photos: a 422 whose message lands on the photos field.
  try {
    await api.createFilm(fid, filmRequest(draft, ids.slice(0, 3)));
    assert.fail("expected a 422");
  } catch (e) {
    assert.equal(e.status, 422);
    assert.ok(filmServerErrors(e.fieldErrors).fields.photos);
  }
  // Another family's film is a 404.
  await assert.rejects(api.getFilm("not_my_family", SAMPLE.id), (e) => e.status === 404);
});
