// Run with: cd web && node --test --experimental-strip-types "components/film/__tests__/*.test.mjs"
// Choosing a film's photos from an album (components/film/shortlist.ts), on
// small generated pictures run through the real analysis (analyze.ts
// lookFromPixels): photographic-ish scenes and their copies (re-saved,
// brightened, recoloured, cropped, shifted, a blurred burst frame), and
// different moments against one shared background (people in a park, people
// in a dark room), the case where copies and moments are hardest to tell apart.
import { test } from "node:test";
import assert from "node:assert/strict";

const { lookFromPixels } = await import("../analyze.ts");
const S = await import("../shortlist.ts");
const { chooseAgain, chooseTimes, hamming, looksAlike, planAlbum, prepareAlbum, replaceTaken, shortlist, signalsFromLooks } = S;

const W = 128;
const H = 96;

// A small seeded generator, so every run sees the same pictures.
function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

/** Float RGB canvas helpers (3 values per pixel). */
const canvas = () => new Float32Array(W * H * 3);
function fill(c, fn) {
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) c.set(fn(x, y), (y * W + x) * 3);
}
/** A soft-edged ellipse. */
function blob(c, cx, cy, rx, ry, colour, soft) {
  for (let y = Math.max(0, Math.floor(cy - ry)); y < Math.min(H, Math.ceil(cy + ry)); y++) {
    for (let x = Math.max(0, Math.floor(cx - rx)); x < Math.min(W, Math.ceil(cx + rx)); x++) {
      const d = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2;
      const a = Math.min(1, Math.max(0, (1 - d) / soft));
      if (a <= 0) continue;
      const p = (y * W + x) * 3;
      for (let k = 0; k < 3; k++) c[p + k] = c[p + k] * (1 - a) + colour[k] * a;
    }
  }
}
/** Smooth texture: a coarse random grid, bilinearly enlarged. */
function texture(c, r, cell, amp) {
  const gw = Math.ceil(W / cell) + 2;
  const gh = Math.ceil(H / cell) + 2;
  const g = Array.from({ length: gw * gh }, () => (r() * 2 - 1) * amp);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const fx = x / cell;
      const fy = y / cell;
      const x0 = Math.floor(fx);
      const y0 = Math.floor(fy);
      const tx = fx - x0;
      const ty = fy - y0;
      const v =
        (g[y0 * gw + x0] * (1 - tx) + g[y0 * gw + x0 + 1] * tx) * (1 - ty) +
        (g[(y0 + 1) * gw + x0] * (1 - tx) + g[(y0 + 1) * gw + x0 + 1] * tx) * ty;
      const p = (y * W + x) * 3;
      for (let k = 0; k < 3; k++) c[p + k] += v;
    }
  }
}
const lerp3 = (a, b, t) => a.map((v, k) => v * (1 - t) + b[k] * t);

/** A photographic-ish scene: graded sky and ground, a few soft shapes, texture. */
function sceneImage(seed) {
  const r = rng(seed);
  const c = canvas();
  const top = [0, 0, 0].map(() => 20 + r() * 215);
  const bottom = [0, 0, 0].map(() => 20 + r() * 215);
  const ground = [0, 0, 0].map(() => 20 + r() * 180);
  const horizon = 0.3 + r() * 0.4;
  fill(c, (x, y) => (y / H < horizon ? lerp3(top, bottom, y / H) : lerp3(lerp3(top, bottom, y / H), ground, 0.7)));
  const shapes = 4 + Math.floor(r() * 6);
  for (let k = 0; k < shapes; k++) {
    blob(c, r() * W, r() * H, 4 + r() * 26, 4 + r() * 26, [r() * 255, r() * 255, r() * 255], 0.2 + r() * 0.7);
  }
  texture(c, r, 6, 14);
  return c;
}

const BACKGROUNDS = {
  park(c, r) {
    fill(c, (x, y) => lerp3([110, 160, 220], [200, 220, 235], y / H));
    for (let x = 0; x < W; x += 10) blob(c, x + 3, H * 0.45, 9, 12, [40, 90, 45], 0.6);
    fill(c, (x, y) => (y >= H * 0.55 ? [80, 150, 70] : Array.from(c.subarray((y * W + x) * 3, (y * W + x) * 3 + 3))));
    texture(c, r, 5, 10);
  },
  room(c, r) {
    fill(c, () => [28, 22, 20]);
    blob(c, W * 0.8, H * 0.3, 30, 27, [120, 90, 55], 1);
    blob(c, W * 0.35, H * 0.75, 43, 12, [60, 35, 30], 0.5);
    texture(c, r, 4, 6);
  },
};

/** One moment against a shared background: 1 to 5 people in different places, exposure a touch different. */
function momentImage(kind, k) {
  const c = canvas();
  BACKGROUNDS[kind](c, rng(7)); // the same background every time
  const r = rng(10_000 + k * 7919 + (kind === "room" ? 500 : 0));
  const exposure = 0.93 + r() * 0.14;
  for (let i = 0; i < c.length; i++) c[i] *= exposure;
  const people = 1 + Math.floor(r() * 5);
  for (let p = 0; p < people; p++) {
    const tall = (0.25 + r() * 0.35) * H;
    const cx = (0.08 + r() * 0.84) * W;
    const base = (0.7 + r() * 0.28) * H;
    blob(c, cx, base - tall * 0.45, tall * 0.17, tall * 0.38, [20 + r() * 220, 20 + r() * 220, 20 + r() * 220], 0.35);
    const skin = 0.6 + r() * 0.5;
    blob(c, cx, base - tall * 0.88, tall * 0.13, tall * 0.14, [205 * skin, 165 * skin, 135 * skin], 0.3);
  }
  for (let i = 0; i < c.length; i++) c[i] += (r() * 2 - 1) * 3; // sensor noise
  return c;
}

/** Canvas to RGBA bytes, through an optional per-value edit. */
function rgba(c, edit = (v) => v) {
  const px = new Uint8ClampedArray(W * H * 4);
  for (let i = 0, p = 0; i < W * H; i++, p += 4) {
    px[p] = edit(c[i * 3], 0, i);
    px[p + 1] = edit(c[i * 3 + 1], 1, i);
    px[p + 2] = edit(c[i * 3 + 2], 2, i);
    px[p + 3] = 255;
  }
  return px;
}
/** The part (x0, y0, cw, ch) of a canvas, enlarged back to the full frame. */
function reframe(c, x0, y0, cw, ch) {
  const out = canvas();
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const sx = Math.min(W - 1.001, x0 + ((x + 0.5) * cw) / W - 0.5);
      const sy = Math.min(H - 1.001, y0 + ((y + 0.5) * ch) / H - 0.5);
      const ix = Math.max(0, Math.floor(sx));
      const iy = Math.max(0, Math.floor(sy));
      const tx = Math.max(0, sx - ix);
      const ty = Math.max(0, sy - iy);
      for (let k = 0; k < 3; k++) {
        const at = (xx, yy) => c[(yy * W + xx) * 3 + k];
        out[(y * W + x) * 3 + k] =
          (at(ix, iy) * (1 - tx) + at(ix + 1, iy) * tx) * (1 - ty) + (at(ix, iy + 1) * (1 - tx) + at(ix + 1, iy + 1) * tx) * ty;
      }
    }
  }
  return out;
}
function boxBlur(c) {
  const out = canvas();
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      for (let k = 0; k < 3; k++) {
        let s = 0;
        let n = 0;
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            const xx = Math.min(W - 1, Math.max(0, x + dx));
            const yy = Math.min(H - 1, Math.max(0, y + dy));
            s += c[(yy * W + xx) * 3 + k];
            n++;
          }
        }
        out[(y * W + x) * 3 + k] = s / n;
      }
    }
  }
  return out;
}

/** The copies a photo can have, as RGBA. */
const COPIES = {
  resave: (c, r) => rgba(c, (v) => v + (r() * 2 - 1) * 2),
  bright: (c) => rgba(c, (v) => v * 1.25),
  dark: (c) => rgba(c, (v) => v * 0.8),
  contrast: (c) => rgba(c, (v) => (v - 128) * 1.3 + 128),
  saturation: (c) =>
    rgba(c, (v, ch, i) => {
      const l = 0.299 * c[i * 3] + 0.587 * c[i * 3 + 1] + 0.114 * c[i * 3 + 2];
      return l + (v - l) * 0.4;
    }),
  crop: (c) => rgba(reframe(c, W * 0.03, H * 0.05, W * 0.92, H * 0.92)),
  shift: (c) => rgba(reframe(c, W * 0.12, 0, W * 0.88, H * 0.95)),
  burst: (c) => rgba(boxBlur(reframe(c, W * 0.02, H * 0.01, W * 0.96, H * 0.97))),
};

function signal(px, index, takenAt) {
  const look = lookFromPixels(px, W, H);
  return { index, takenAt, sharpness: look.sharpness, hash: look.hash, fine: look.fine };
}

const DAY = Date.UTC(2025, 6, 4, 9);
const MIN = 60_000;

/**
 * An album of `scenes` scenes a few minutes apart, each with `copies` copies
 * cycling through the kinds above: edits keep the capture time, a burst frame
 * is 2 s on. Returns the photos and each photo's scene.
 */
function copiesAlbum(scenes, copies, seed = 1) {
  const kinds = Object.keys(COPIES);
  const photos = [];
  const sceneOf = [];
  const r = rng(seed);
  for (let s = 0; s < scenes; s++) {
    const c = sceneImage(seed * 100_000 + s);
    const t = DAY + s * 3 * MIN + Math.floor(r() * 30_000);
    photos.push(signal(rgba(c), photos.length, t));
    sceneOf.push(s);
    for (let k = 0; k < copies; k++) {
      const kind = kinds[(s * 3 + k) % kinds.length];
      const at = kind === "burst" ? t + 2000 : kind === "resave" && s % 2 ? null : t; // some re-saves lost their EXIF
      photos.push(signal(COPIES[kind](c, r), photos.length, at));
      sceneOf.push(s);
    }
  }
  return { photos, sceneOf };
}

/** `count` moments against one background, `apartMs` apart. */
function momentsAlbum(kind, count, apartMs = 4 * MIN, from = 0) {
  return Array.from({ length: count }, (_, k) => signal(rgba(momentImage(kind, from + k)), k, DAY + k * apartMs));
}

function assertNoLookAlikes(chosen, photos) {
  const byIndex = new Map(photos.map((p) => [p.index, p]));
  for (let i = 0; i < chosen.length; i++) {
    for (let j = i + 1; j < chosen.length; j++) {
      assert.equal(looksAlike(byIndex.get(chosen[i]), byIndex.get(chosen[j])), false, `${chosen[i]} and ${chosen[j]} look alike`);
    }
  }
}

// ---------------------------------------------------------------------------
// The rule

test("looksAlike: re-saves and colour edits of a picture look alike whenever they were taken", () => {
  const c = sceneImage(11);
  const r = rng(3);
  const base = signal(rgba(c), 0, DAY);
  for (const kind of ["resave", "bright", "dark", "contrast", "saturation"]) {
    const copy = signal(COPIES[kind](c, r), 1, DAY + 6 * 3600_000);
    assert.equal(looksAlike(base, copy), true, `${kind}, hours apart`);
    assert.equal(looksAlike(base, { ...copy, takenAt: null }), true, `${kind}, no time`);
    assert.equal(looksAlike(base, copy, { strict: true }), true, `${kind} is an exact copy`);
  }
});

test("looksAlike: crops, shifts and burst frames of a moment look alike, lined up first", () => {
  const c = sceneImage(12);
  const base = signal(rgba(c), 0, DAY);
  for (const kind of ["crop", "shift"]) {
    const copy = signal(COPIES[kind](c), 1, DAY);
    assert.equal(looksAlike(base, copy), true, `${kind}, same capture time`);
    assert.equal(looksAlike(base, copy, { strict: true }), false, `a ${kind} is not an exact copy`);
  }
  const frame = signal(COPIES.burst(c), 1, DAY + 2000);
  assert.equal(looksAlike(base, frame), true, "a burst frame 2 s on");
});

test("looksAlike: different moments in one place do not look alike", () => {
  for (const kind of ["park", "room"]) {
    const moments = momentsAlbum(kind, 30);
    let alike = 0;
    let strict = 0;
    for (let i = 0; i < moments.length; i++) {
      for (let j = i + 1; j < moments.length; j++) {
        if (looksAlike(moments[i], moments[j])) alike++;
        if (looksAlike(moments[i], moments[j], { strict: true })) strict++;
      }
    }
    assert.equal(strict, 0, `${kind}: never exact copies`);
    assert.ok(alike <= 4, `${kind}: ${alike} of 435 pairs called alike`);
  }
  // Even taken a few seconds apart (judged as a burst), most stay apart.
  const quick = momentsAlbum("park", 12, 3000);
  let alike = 0;
  for (let i = 0; i < quick.length - 1; i++) if (looksAlike(quick[i], quick[i + 1])) alike++;
  assert.ok(alike <= 2, `${alike} of 11 neighbouring pairs 3 s apart called alike`);
  assert.equal(hamming(BigInt("0x" + quick[0].hash), quick[0].hash), 0, "hashes as bigints work too");
});

// ---------------------------------------------------------------------------
// Same background: moments must survive

test("an album of 60 moments in one place keeps at least 50 of them and fills the film", () => {
  for (const kind of ["park", "room"]) {
    const photos = momentsAlbum(kind, 60);
    const album = prepareAlbum(photos);
    assert.ok(album.best.length >= 50, `${kind}: ${album.best.length} of 60 kept as distinct`);
    const plan = planAlbum(photos, { exactTimes: true });
    assert.equal(plan.chosen.length, 40, kind);
    assert.ok(plan.setAside <= 10, `${kind}: ${plan.setAside} set aside`);
  }
});

test("40 or fewer: 25 moments in one place and 15 other scenes keep all 40", () => {
  const photos = [
    ...momentsAlbum("park", 25),
    ...Array.from({ length: 15 }, (_, k) => signal(rgba(sceneImage(500 + k)), 25 + k, DAY + (200 + k) * MIN)),
  ];
  const plan = planAlbum(photos, { exactTimes: true });
  assert.equal(plan.mode, "picks");
  assert.equal(plan.chosen.length, 40);
  assert.equal(plan.setAside, 0);
});

// ---------------------------------------------------------------------------
// Copies must go

const DUPS = copiesAlbum(120, 3);

test("an album of copies: 40 chosen from 40 different scenes, earliest first, the whole day covered", () => {
  const { photos, sceneOf } = DUPS;
  const plan = planAlbum(photos, { exactTimes: true });
  assert.equal(plan.mode, "album");
  assert.equal(plan.chosen.length, 40);
  assert.equal(new Set(plan.chosen.map((i) => sceneOf[i])).size, 40, "40 different scenes");
  assert.equal(plan.album.best.length, 120, "every copy found, no scene split or merged");
  assert.equal(plan.setAside, photos.length - 120);
  assertNoLookAlikes(plan.chosen, photos);
  const scenes = plan.chosen.map((i) => sceneOf[i]);
  assert.deepEqual(scenes, scenes.slice().sort((a, b) => a - b), "earliest first");
  assert.ok(scenes[0] < 6 && scenes[scenes.length - 1] >= 114, `from scene ${scenes[0]} to ${scenes[scenes.length - 1]}`);
  assert.ok(Math.max(...scenes.slice(1).map((s, k) => s - scenes[k])) <= 8, "no long stretch skipped");
});

test("taking photos out never brings a copy of them back, and Choose again keeps them out", () => {
  const { photos, sceneOf } = DUPS;
  const plan = planAlbum(photos, { exactTimes: true });
  const r = rng(99);
  for (let run = 0; run < 10; run++) {
    let current = plan.chosen.slice();
    const removed = [];
    for (let k = 0; k < 10; k++) {
      const taken = current[Math.floor(r() * current.length)];
      removed.push(taken);
      current = replaceTaken(plan.album, current, removed, taken);
      assert.equal(current.length, 40, "the next best takes its place");
      const scenes = current.map((i) => sceneOf[i]);
      assert.equal(new Set(scenes).size, 40, "no scene twice");
      for (const out of removed) assert.ok(!scenes.includes(sceneOf[out]), "a removed scene came back");
    }
    const again = chooseAgain(plan.album, "album", removed).chosen;
    assert.equal(new Set(again.map((i) => sceneOf[i])).size, again.length);
    for (const out of removed) assert.ok(!again.some((i) => sceneOf[i] === sceneOf[out]), "Choose again brought one back");
  }
  // When nothing is left to bring in, the film just has one fewer.
  const small = planAlbum(photos.slice(0, 30), { exactTimes: true });
  const after = replaceTaken(small.album, small.chosen, [small.chosen[0]], small.chosen[0]);
  assert.equal(after.length, small.chosen.length - 1);
});

// ---------------------------------------------------------------------------
// No capture times (an album gathered from a messaging app, say)

/** `photos` with every time gone and the edited copies moved to the end of the pick order. */
function untimed(photos, isEdit) {
  const order = [...photos.filter((p) => !isEdit(p)), ...photos.filter(isEdit)];
  return order.map((p, k) => ({ ...p, index: k, takenAt: null, from: p.index }));
}

test("without times, copies far apart in the pick order are still refused", () => {
  const { photos, sceneOf } = DUPS;
  // Edits (crops, shifts, colour changes) land at the end, far from their originals.
  const firstOfScene = new Set();
  const isEdit = (p) => {
    const s = sceneOf[p.index];
    if (!firstOfScene.has(s)) {
      firstOfScene.add(s);
      return false;
    }
    return true;
  };
  const shuffled = untimed(photos, isEdit);
  const scene = (idx) => sceneOf[shuffled[idx].from];
  const plan = planAlbum(shuffled, { exactTimes: false });
  assert.equal(plan.chosen.length, 40);
  assert.equal(new Set(plan.chosen.map(scene)).size, 40, "40 different scenes");
  assert.equal(plan.album.best.length, 120, "every copy found");
  const r = rng(17);
  for (let run = 0; run < 5; run++) {
    let current = plan.chosen.slice();
    const removed = [];
    for (let k = 0; k < 8; k++) {
      const taken = current[Math.floor(r() * current.length)];
      removed.push(taken);
      current = replaceTaken(plan.album, current, removed, taken);
      const scenes = current.map(scene);
      assert.equal(new Set(scenes).size, current.length, "no scene twice");
      for (const out of removed) assert.ok(!scenes.includes(scene(out)), "a removed scene came back");
    }
  }
});

test("without times, different moments in one place survive", () => {
  for (const kind of ["park", "room"]) {
    const photos = momentsAlbum(kind, 60).map((p) => ({ ...p, takenAt: null }));
    const plan = planAlbum(photos, { exactTimes: false });
    assert.ok(plan.album.best.length >= 50, `${kind}: ${plan.album.best.length} of 60 kept as distinct`);
    assert.equal(plan.chosen.length, 40, kind);
  }
  const mix = [
    ...momentsAlbum("park", 25),
    ...Array.from({ length: 15 }, (_, k) => signal(rgba(sceneImage(500 + k)), 25 + k, null)),
  ].map((p) => ({ ...p, takenAt: null }));
  const small = planAlbum(mix, { exactTimes: false });
  assert.equal(small.chosen.length, 40, "25 moments and 15 scenes, no times: all 40 kept");
});

test("without times, crops and shifts of moments in one place, added at the end, go with their originals", () => {
  for (const kind of ["park", "room"]) {
    const moments = momentsAlbum(kind, 60).map((p) => ({ ...p, takenAt: null }));
    // A crop or shift of every third moment, saved later and added at the end.
    const copies = [];
    const momentOf = moments.map((_, k) => k);
    for (let k = 0; k < 60; k += 3) {
      const kindOfCopy = (k / 3) % 2 ? "shift" : "crop";
      copies.push({ ...signal(COPIES[kindOfCopy](momentImage(kind, k)), 60 + copies.length, null) });
      momentOf.push(k);
    }
    const photos = [...moments, ...copies];
    const plan = planAlbum(photos, { exactTimes: false });
    const album = plan.album;
    const groupOf = (idx) => album.group[album.at.get(idx)];
    for (const c of copies) assert.equal(groupOf(c.index), groupOf(momentOf[c.index]), `${kind}: copy of moment ${momentOf[c.index]} kept apart`);
    const distinctMoments = new Set(moments.map((m) => groupOf(m.index))).size;
    assert.ok(distinctMoments >= 50, `${kind}: ${distinctMoments} of 60 moments kept apart`);
    assert.equal(new Set(plan.chosen.map((i) => momentOf[i])).size, plan.chosen.length, `${kind}: one moment twice`);
    const r = rng(23);
    for (let run = 0; run < 4; run++) {
      let current = plan.chosen.slice();
      const removed = [];
      for (let k = 0; k < 8; k++) {
        const taken = current[Math.floor(r() * current.length)];
        removed.push(taken);
        current = replaceTaken(album, current, removed, taken);
        const ms = current.map((i) => momentOf[i]);
        assert.equal(new Set(ms).size, ms.length, `${kind}: one moment twice after removals`);
        for (const out of removed) assert.ok(!ms.includes(momentOf[out]), `${kind}: a removed moment came back`);
      }
    }
  }
});

test("without times, 1000 photos in one place still plan in a few seconds", () => {
  const photos = momentsAlbum("park", 1000, 5000, 3000).map((p) => ({ ...p, takenAt: null }));
  const started = performance.now();
  const plan = planAlbum(photos, { exactTimes: false });
  const ms = performance.now() - started;
  assert.equal(plan.chosen.length, 40);
  assert.ok(ms < 10_000, `planned in ${ms.toFixed(0)} ms`);
  console.log(`# 1000 photos, one place, no times: ${ms.toFixed(0)} ms (${plan.album.best.length} kept)`);
});

test("shortlist keeps the sharpest copy and respects max", () => {
  const c = sceneImage(21);
  const photos = [
    signal(rgba(boxBlur(boxBlur(c))), 0, DAY),
    signal(rgba(c), 1, DAY),
    signal(COPIES.resave(c, rng(1)), 2, DAY),
    signal(rgba(sceneImage(22)), 3, DAY + 10 * MIN),
  ];
  const result = shortlist(photos);
  assert.equal(result.chosen.length, 2);
  assert.ok(result.chosen.includes(3));
  assert.ok(!result.chosen.includes(0), "not the blurred one");
  assert.equal(result.duplicatesDropped, 2);
  assert.equal(shortlist(DUPS.photos, 10).chosen.length, 10);
  assert.equal(shortlist(DUPS.photos, 0).chosen.length, 0);
});

test("planAlbum: 40 or fewer drops only exact copies, and only if 8 remain; few moments are topped up to 8", () => {
  const r = rng(5);
  const scenes = Array.from({ length: 10 }, (_, k) => sceneImage(300 + k));
  const picks = scenes.map((c, k) => signal(rgba(c), k, DAY + k * MIN));
  // Two exact copies and a crop: only the copies go.
  const withCopies = [
    ...picks,
    signal(COPIES.resave(scenes[2], r), 10, null),
    signal(COPIES.saturation(scenes[5]), 11, DAY + 5 * MIN),
    signal(COPIES.crop(scenes[7]), 12, DAY + 7 * MIN),
  ];
  const small = planAlbum(withCopies, { exactTimes: true });
  assert.equal(small.mode, "picks");
  assert.equal(small.chosen.length, 11);
  assert.equal(small.setAside, 2);
  assert.ok(small.chosen.includes(12), "the crop stays");
  // Eight picks, one a copy: dropping it would leave seven, so all stay.
  const eight = [...picks.slice(0, 7), signal(COPIES.resave(scenes[0], r), 7, null)];
  const kept = planAlbum(eight, { exactTimes: true });
  assert.equal(kept.mode, "as-picked");
  assert.equal(kept.chosen.length, 8);
  // An album of 50 frames of three moments: topped up to eight.
  const few = Array.from({ length: 50 }, (_, k) => signal(COPIES.resave(scenes[k % 3], r), k, DAY + k * 1000));
  const topped = planAlbum(few, { exactTimes: true });
  assert.equal(topped.mode, "album");
  assert.equal(topped.chosen.length, 8);
});

test("1000 photos plan fast: copies, and a whole match shot from one spot", () => {
  const { photos } = copiesAlbum(250, 3, 2);
  assert.equal(photos.length, 1000);
  let started = performance.now();
  const plan = planAlbum(photos, { exactTimes: true });
  const copiesMs = performance.now() - started;
  assert.equal(plan.chosen.length, 40);
  assert.equal(plan.album.best.length, 250);
  assert.ok(copiesMs < 3000, `copies album planned in ${copiesMs.toFixed(0)} ms`);

  const match = momentsAlbum("park", 1000, 5000);
  started = performance.now();
  const matchPlan = planAlbum(match, { exactTimes: true });
  const matchMs = performance.now() - started;
  assert.equal(matchPlan.chosen.length, 40);
  assert.ok(matchMs < 5000, `same-place album planned in ${matchMs.toFixed(0)} ms`);
  console.log(`# 1000 photos: copies album ${copiesMs.toFixed(0)} ms, one-place album ${matchMs.toFixed(0)} ms (${matchPlan.album.best.length} kept)`);
});

test("planAlbumAsync gives the page room between slices and the same answer", async () => {
  const photos = momentsAlbum("park", 300, 5000, 2000);
  let pauses = 0;
  const plan = await S.planAlbumAsync(photos, { exactTimes: true }, () => {
    pauses++;
    return new Promise((resolve) => setTimeout(resolve, 0));
  });
  assert.ok(pauses > 0, "paused at least once");
  assert.deepEqual(plan.chosen, planAlbum(photos, { exactTimes: true }).chosen);
});

// ---------------------------------------------------------------------------
// Times and inputs

test("a photo without a time sits after the one picked before it", () => {
  const photos = [
    signal(rgba(sceneImage(31)), 0, DAY + 5000),
    signal(rgba(sceneImage(32)), 1, null),
    signal(rgba(sceneImage(33)), 2, DAY + 1000),
  ];
  assert.deepEqual(shortlist(photos).chosen, [2, 0, 1]);
});

test("chooseTimes trusts EXIF when at least half the album has it, else file dates for all", () => {
  assert.deepEqual(chooseTimes([1, null, 3, null], [10, 20, 30, 40]), { times: [1, null, 3, null], exact: true });
  assert.deepEqual(chooseTimes([1, null, null], [10, 20, 0]), { times: [10, 20, null], exact: false });
  assert.deepEqual(chooseTimes([], []), { times: [], exact: false });
});

test("signalsFromLooks leaves out unreadable photos and keeps album indices", () => {
  const fine = new Float32Array(3072);
  const look = (exifTime) => ({ hash: "00000000000000ff", layout: [], fine, sharpness: 2, exifTime, fileTime: 99 });
  const { photos, exactTimes } = signalsFromLooks([look(5), null, look(null), look(7)]);
  assert.equal(exactTimes, true);
  assert.deepEqual(photos.map((p) => [p.index, p.takenAt]), [[0, 5], [2, null], [3, 7]]);
  assert.equal(photos[0].fine, fine);
});
