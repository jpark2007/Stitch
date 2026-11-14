// Run with: cd web && node --test --experimental-strip-types "components/film/__tests__/*.test.mjs"
// Choosing a film's photos from an album (components/film/shortlist.ts), on
// synthetic signatures: scenes with random hashes and colour layouts, and
// copies of them with a few bits flipped and the colours nudged, the way a
// re-encode, a crop or a burst frame comes out of analyze.ts.
import { test } from "node:test";
import assert from "node:assert/strict";

const {
  ALIKE,
  chooseAgain,
  chooseTimes,
  hamming,
  layoutDistance,
  looksAlike,
  planAlbum,
  prepareAlbum,
  replaceTaken,
  shortlist,
  signalsFromLooks,
} = await import("../shortlist.ts");

// A small seeded generator, so every run sees the same album.
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

const hex = (hi, lo) => (hi >>> 0).toString(16).padStart(8, "0") + (lo >>> 0).toString(16).padStart(8, "0");

function randomHash(r) {
  return hex(Math.floor(r() * 2 ** 32), Math.floor(r() * 2 ** 32));
}

/** `hash` with `bits` distinct bits flipped. */
function flip(hash, bits, r) {
  let hi = parseInt(hash.slice(0, 8), 16);
  let lo = parseInt(hash.slice(8), 16);
  const picked = new Set();
  while (picked.size < bits) picked.add(Math.floor(r() * 64));
  for (const b of picked) {
    if (b < 32) hi ^= 1 << b;
    else lo ^= 1 << (b - 32);
  }
  return hex(hi, lo);
}

const randomLayout = (r) => Array.from({ length: 192 }, () => Math.round(r() * 255));
/** Each value moved by up to ±`spread` (a mean change of about spread/2), plus an overall `shift`. */
const nudge = (layout, spread, r, shift = 0) => layout.map((v) => v + shift + (r() * 2 - 1) * spread);

function scene(r) {
  return { hash: randomHash(r), layout: randomLayout(r) };
}

/** A copy of a scene: `bits` flipped and colours moved by `spread`. */
function copyOf(base, bits, spread, r, shift = 0) {
  return { hash: flip(base.hash, bits, r), layout: nudge(base.layout, spread, r, shift) };
}

test("looksAlike: identical, a re-save and a brighter copy look alike", () => {
  const r = rng(1);
  const a = { ...scene(r), takenAt: null };
  assert.equal(looksAlike(a, a), true);
  assert.equal(looksAlike(a, { ...copyOf(a, 3, 4, r), takenAt: null }), true);
  const brighter = { ...copyOf(a, 1, 2, r, 35), takenAt: Date.now() };
  assert.ok(layoutDistance(a.layout, brighter.layout) < 3, "the overall level is taken out");
  assert.equal(looksAlike(a, brighter), true);
  assert.equal(looksAlike(a, brighter, { strict: true }), true, "a re-save is an exact near-duplicate");
});

test("looksAlike: a crop of the same moment matches whenever it was taken, but not in strict mode", () => {
  const r = rng(2);
  const a = scene(r);
  const crop = copyOf(a, 14, 24, r);
  const far = (t) => ({ ...a, takenAt: t });
  assert.equal(looksAlike(far(0), { ...crop, takenAt: 6 * 3600_000 }), true, "hours apart");
  assert.equal(looksAlike(far(null), { ...crop, takenAt: null }), true, "no times at all");
  assert.equal(looksAlike(far(null), { ...crop, takenAt: null }, { strict: true }), false);
});

test("looksAlike: burst frames are judged loosely only when a few seconds apart", () => {
  const r = rng(3);
  const a = scene(r);
  const frame = copyOf(a, 20, 36, r); // about 18 of layout
  assert.ok(hamming(a.hash, frame.hash) > ALIKE.sameMoment.hash);
  const at = (p, t) => ({ ...p, takenAt: t });
  assert.equal(looksAlike(at(a, 10_000), at(frame, 12_000)), true, "2 s apart");
  assert.equal(looksAlike(at(a, 10_000), at(frame, 15_000)), true, "5 s apart");
  assert.equal(looksAlike(at(a, 10_000), at(frame, 70_000)), false, "a minute apart");
  assert.equal(looksAlike(at(a, null), at(frame, 12_000)), false, "one time unknown");
  assert.equal(looksAlike(at(a, 10_000), at(frame, 12_000), { bursts: false }), false, "times are only file dates");
});

test("looksAlike: different scenes never do, even taken together", () => {
  const r = rng(4);
  for (let k = 0; k < 200; k++) {
    const a = { ...scene(r), takenAt: 1000 };
    const b = { ...scene(r), takenAt: 2000 };
    assert.equal(looksAlike(a, b), false);
  }
  // Same colours, different structure; same structure, different colours.
  const a = scene(r);
  assert.equal(looksAlike({ ...a, takenAt: 0 }, { hash: randomHash(r), layout: a.layout, takenAt: 0 }), false);
  assert.equal(looksAlike({ ...a, takenAt: 0 }, { hash: a.hash, layout: randomLayout(r), takenAt: 0 }), false);
  // Hashes as bigints work too.
  assert.equal(hamming(BigInt("0x" + a.hash), a.hash), 0);
});

/**
 * An album of `scenes` moments over a day, each with copies: re-saves (no
 * time), crops (hours later) and burst frames (a second or two on). Returns
 * the photos plus each photo's scene.
 */
function album(scenes, seed = 5, { times = true } = {}) {
  const r = rng(seed);
  const photos = [];
  const sceneOf = [];
  const day = Date.UTC(2025, 6, 4, 9);
  let t = day;
  const add = (p, s, takenAt, sharp) => {
    sceneOf.push(s);
    photos.push({ index: photos.length, takenAt: times ? takenAt : null, sharpness: sharp, hash: p.hash, layout: p.layout });
  };
  for (let s = 0; s < scenes; s++) {
    t += 60_000 + Math.floor(r() * 600_000);
    if (s === Math.floor(scenes / 2)) t += 2 * 3600_000; // the drive
    const base = scene(r);
    add(base, s, t, 50 + r() * 50);
    const extra = s % 4; // 0 to 3 copies
    for (let k = 0; k < extra; k++) {
      const kind = (s + k) % 3;
      if (kind === 0) add(copyOf(base, 2, 4, r), s, null, 40 + r() * 50);
      else if (kind === 1) add(copyOf(base, 12, 20, r), s, t + 5 * 3600_000, 40 + r() * 50);
      else add(copyOf(base, 19, 30, r), s, t + (k + 1) * 1000, 10 + r() * 30);
    }
  }
  return { photos, sceneOf };
}

function assertNoLookAlikes(chosen, photos, opts) {
  const byIndex = new Map(photos.map((p) => [p.index, p]));
  for (let i = 0; i < chosen.length; i++) {
    for (let j = i + 1; j < chosen.length; j++) {
      assert.equal(looksAlike(byIndex.get(chosen[i]), byIndex.get(chosen[j]), opts), false, `${chosen[i]} and ${chosen[j]} look alike`);
    }
  }
}

test("shortlist: 40 from a 1000-photo album, no look-alikes, earliest first, the whole day covered, fast", () => {
  const { photos, sceneOf } = album(400);
  assert.ok(photos.length >= 990 && photos.length <= 1010, `album of ${photos.length}`);
  const started = performance.now();
  const result = shortlist(photos);
  const took = performance.now() - started;
  assert.ok(took < 750, `took ${took.toFixed(0)} ms`);

  assert.equal(result.considered, photos.length);
  assert.equal(result.chosen.length, 40);
  assert.equal(result.duplicatesDropped, photos.length - 400, "every copy is found, no scene merged");
  assertNoLookAlikes(result.chosen, photos);
  assert.equal(new Set(result.chosen.map((i) => sceneOf[i])).size, 40, "40 different scenes");

  // Earliest first, by the scene's own time.
  const firstTime = new Map();
  photos.forEach((p) => {
    if (!firstTime.has(sceneOf[p.index])) firstTime.set(sceneOf[p.index], p.takenAt);
  });
  const times = result.chosen.map((i) => firstTime.get(sceneOf[i]));
  assert.deepEqual(times, times.slice().sort((a, b) => a - b));

  // Covers the day: opens within the first fortieth of it, closes within the
  // last, and never skips more than a few slots' worth at a stretch.
  const scenes = result.chosen.map((i) => sceneOf[i]);
  assert.ok(scenes[0] < 10, `starts at scene ${scenes[0]}`);
  assert.ok(scenes[scenes.length - 1] >= 390, `ends at scene ${scenes[scenes.length - 1]}`);
  const gaps = scenes.slice(1).map((s, k) => s - scenes[k]);
  assert.ok(Math.max(...gaps) <= 25, `largest gap ${Math.max(...gaps)} scenes`);
});

test("shortlist keeps the sharpest of each look-alike group", () => {
  const r = rng(6);
  const base = scene(r);
  const photos = [8, 30, 12].map((sharpness, index) => ({ index, takenAt: 1000 + index * 1000, sharpness, ...copyOf(base, 1, 2, r) }));
  photos.push({ index: 3, takenAt: 60_000, sharpness: 1, ...scene(r) });
  assert.deepEqual(shortlist(photos).chosen, [1, 3]);
});

test("shortlist respects max, and returns every distinct photo when there are fewer", () => {
  const { photos } = album(60, 7);
  assert.equal(shortlist(photos, 10).chosen.length, 10);
  assert.equal(shortlist(photos, 0).chosen.length, 0);
  const all = shortlist(photos, 100);
  assert.equal(all.chosen.length, 60);
  assertNoLookAlikes(all.chosen, photos);
});

test("shortlist without times keeps pick order and still drops look-alikes", () => {
  const { photos, sceneOf } = album(120, 8, { times: false });
  const result = shortlist(photos);
  assert.equal(result.chosen.length, 40);
  assert.deepEqual(result.chosen, result.chosen.slice().sort((a, b) => a - b));
  assert.equal(new Set(result.chosen.map((i) => sceneOf[i])).size, 40);
  assertNoLookAlikes(result.chosen, photos);
});

test("a photo without a time sits after the one picked before it", () => {
  const r = rng(9);
  const photos = [
    { index: 0, takenAt: 5000, sharpness: 1, ...scene(r) },
    { index: 1, takenAt: null, sharpness: 1, ...scene(r) },
    { index: 2, takenAt: 1000, sharpness: 1, ...scene(r) },
  ];
  assert.deepEqual(shortlist(photos).chosen, [2, 0, 1]);
});

test("taking one out brings in the next best that is unlike everything, while any remain", () => {
  const { photos } = album(200, 10);
  const prepared = prepareAlbum(photos);
  let current = shortlist(photos).chosen;
  const removed = [];
  for (let round = 0; round < 30; round++) {
    const taken = current[(round * 7) % current.length];
    removed.push(taken);
    const next = replaceTaken(prepared, current, removed, taken);
    assert.equal(next.length, current.length, `round ${round}`);
    assert.ok(!next.includes(taken));
    assertNoLookAlikes(next, photos);
    const byIndex = new Map(photos.map((p) => [p.index, p]));
    for (const idx of next) {
      for (const out of removed) assert.equal(looksAlike(byIndex.get(idx), byIndex.get(out)), false, "a look-alike of a removed photo came back");
    }
    assert.deepEqual(next, next.slice().sort((a, b) => prepared.order.indexOf(prepared.at.get(a)) - prepared.order.indexOf(prepared.at.get(b))));
    current = next;
  }
  // When nothing is left to bring in, the film just has one fewer.
  const small = shortlist(photos.slice(0, 20), 40).chosen;
  const smallAlbum = prepareAlbum(photos.slice(0, 20));
  const after = replaceTaken(smallAlbum, small, [small[0]], small[0]);
  assert.equal(after.length, small.length - 1);
});

test("planAlbum: over 40 chooses; 40 or fewer drops only exact copies, and only if 8 remain", () => {
  const big = album(100, 11).photos;
  const plan = planAlbum(big, { exactTimes: true });
  assert.equal(plan.mode, "album");
  assert.equal(plan.chosen.length, 40);
  assert.equal(plan.setAside, big.length - 100);

  const r = rng(12);
  const picks = Array.from({ length: 10 }, (_, index) => ({ index, takenAt: index * 60_000, sharpness: 10, ...scene(r) }));
  // Two exact copies and one crop: only the copies go.
  const withCopies = [
    ...picks,
    { index: 10, takenAt: null, sharpness: 5, ...copyOf(picks[2], 1, 2, r) },
    { index: 11, takenAt: null, sharpness: 5, ...copyOf(picks[5], 2, 3, r) },
    { index: 12, takenAt: null, sharpness: 5, ...copyOf(picks[7], 14, 20, r) },
  ];
  const small = planAlbum(withCopies, { exactTimes: true });
  assert.equal(small.mode, "picks");
  assert.equal(small.chosen.length, 11);
  assert.equal(small.setAside, 2);
  assert.ok(!small.chosen.includes(10) && !small.chosen.includes(11));

  // Eight picks, one a copy: dropping it would leave seven, so all stay.
  const eight = [...picks.slice(0, 7), { index: 7, takenAt: null, sharpness: 5, ...copyOf(picks[0], 1, 2, r) }];
  const kept = planAlbum(eight, { exactTimes: true });
  assert.equal(kept.mode, "as-picked");
  assert.equal(kept.chosen.length, 8);
  assert.equal(kept.setAside, 0);

  // Removed photos stay out of a fresh choice.
  const again = chooseAgain(plan.album, "album", plan.chosen.slice(0, 5));
  assert.equal(again.chosen.length, 40);
  for (const idx of plan.chosen.slice(0, 5)) assert.ok(!again.chosen.includes(idx));
});

test("planAlbum tops an album of a few moments up to eight", () => {
  const r = rng(13);
  const moments = [scene(r), scene(r), scene(r)];
  const photos = Array.from({ length: 50 }, (_, index) => ({
    index,
    takenAt: index * 1000,
    sharpness: r(),
    ...copyOf(moments[index % 3], 2, 3, r),
  }));
  const plan = planAlbum(photos, { exactTimes: true });
  assert.equal(plan.mode, "album");
  assert.equal(plan.chosen.length, 8);
  assert.equal(new Set(plan.chosen).size, 8);
  assert.ok(plan.setAside <= 42);
});

test("chooseTimes trusts EXIF when at least half the album has it, else file dates for all", () => {
  assert.deepEqual(chooseTimes([1, null, 3, null], [10, 20, 30, 40]), { times: [1, null, 3, null], exact: true });
  assert.deepEqual(chooseTimes([1, null, null], [10, 20, 0]), { times: [10, 20, null], exact: false });
  assert.deepEqual(chooseTimes([], []), { times: [], exact: false });
});

test("signalsFromLooks leaves out unreadable photos and keeps album indices", () => {
  const look = (exifTime) => ({ hash: "00000000000000ff", layout: [1, 2, 3], sharpness: 2, exifTime, fileTime: 99 });
  const { photos, exactTimes } = signalsFromLooks([look(5), null, look(null), look(7)]);
  assert.equal(exactTimes, true);
  assert.deepEqual(photos.map((p) => [p.index, p.takenAt]), [[0, 5], [2, null], [3, 7]]);
});
