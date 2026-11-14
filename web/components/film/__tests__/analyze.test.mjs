// Run with: cd web && node --test --experimental-strip-types "components/film/__tests__/*.test.mjs"
// The pure maths of components/film/analyze.ts: pixels to hash, colour
// layout and sharpness, and the EXIF capture-time reader on hand-built
// JPEG headers (both byte orders) and on garbage.
import { test } from "node:test";
import assert from "node:assert/strict";

const {
  boxResize,
  colourLayout,
  dHash,
  grayFromRgba,
  lookFromPixels,
  parseExifDate,
  readExifTime,
  sharpness,
} = await import("../analyze.ts");
const { hamming } = await import("../shortlist.ts");

/** An RGBA image from a function of (x, y) -> [r, g, b]. */
function image(w, h, fn) {
  const px = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const [r, g, b] = fn(x, y);
      const p = (y * w + x) * 4;
      px[p] = r;
      px[p + 1] = g;
      px[p + 2] = b;
      px[p + 3] = 255;
    }
  }
  return px;
}

const grayOf = (w, h, fn) => grayFromRgba(image(w, h, (x, y) => { const v = fn(x, y); return [v, v, v]; }), w, h);

test("grayFromRgba is BT.601 luma", () => {
  const g = grayFromRgba(new Uint8ClampedArray([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 10, 10, 10, 255]), 4, 1);
  assert.deepEqual(Array.from(g, (v) => Math.round(v * 1000) / 1000), [76.245, 149.685, 29.07, 10]);
});

test("boxResize averages exactly the area each cell covers", () => {
  const four = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16];
  assert.deepEqual(Array.from(boxResize(four, 4, 4, 2, 2)), [3.5, 5.5, 11.5, 13.5]);
  // 3 -> 2 across: the middle pixel is split between both cells.
  assert.deepEqual(Array.from(boxResize([0, 3, 6], 3, 1, 2, 1)), [1, 5]);
  // One channel of an interleaved buffer.
  assert.deepEqual(Array.from(boxResize([1, 100, 3, 100], 2, 1, 1, 1, 2, 0)), [2]);
});

test("dHash reads left-to-right brightness and ignores scale and overall level", () => {
  const rising = grayOf(64, 64, (x) => x * 3);
  const falling = grayOf(64, 64, (x) => 255 - x * 3);
  assert.equal(dHash(rising, 64, 64), "ffffffffffffffff");
  assert.equal(dHash(falling, 64, 64), "0000000000000000");

  const scene = (x, y) => 100 + 80 * Math.sin(x / 7) * Math.cos(y / 9) + (x > 30 && y < 20 ? 60 : 0);
  const a = dHash(grayOf(64, 48, scene), 64, 48);
  const brighter = dHash(grayOf(64, 48, (x, y) => scene(x, y) + 30), 64, 48);
  const bigger = dHash(grayOf(128, 96, (x, y) => scene(x / 2, y / 2)), 128, 96);
  assert.equal(a.length, 16);
  assert.equal(hamming(a, brighter), 0);
  assert.ok(hamming(a, bigger) <= 2, `scaled copy differs by ${hamming(a, bigger)} bits`);
  const other = dHash(grayOf(64, 48, (x, y) => 100 + 80 * Math.cos(x / 4 + y / 3)), 64, 48);
  assert.ok(hamming(a, other) > 20, "a different picture has a different hash");
});

test("colourLayout is 8x8 cells of mean RGB", () => {
  const flat = colourLayout(image(64, 48, () => [200, 100, 50]), 64, 48);
  assert.equal(flat.length, 192);
  assert.deepEqual(flat.slice(0, 6), [200, 100, 50, 200, 100, 50]);
  const halves = colourLayout(image(16, 16, (x) => (x < 8 ? [255, 0, 0] : [0, 0, 255])), 16, 16);
  assert.deepEqual(halves.slice(0, 3), [255, 0, 0]);
  assert.deepEqual(halves.slice(21, 24), [0, 0, 255]);
});

test("sharpness is high for crisp edges, low for soft ones, zero when flat", () => {
  const crisp = grayOf(64, 64, (x, y) => ((x >> 2) + (y >> 2)) % 2 ? 230 : 20);
  const soft = grayOf(64, 64, (x, y) => 125 + 60 * Math.sin(x / 6) * Math.sin(y / 6));
  assert.equal(sharpness(grayOf(32, 32, () => 128), 32, 32), 0);
  assert.ok(sharpness(crisp, 64, 64) > 50 * sharpness(soft, 64, 64));
  assert.equal(sharpness(new Float32Array(4), 2, 2), 0);
});

test("lookFromPixels gives hash, layout and sharpness together", () => {
  const look = lookFromPixels(image(32, 24, (x, y) => [x * 8, y * 10, 128]), 32, 24);
  assert.match(look.hash, /^[0-9a-f]{16}$/);
  assert.equal(look.layout.length, 192);
  assert.ok(Number.isFinite(look.sharpness));
});

// ---------------------------------------------------------------------------
// EXIF

/**
 * A minimal JPEG: SOI, a JFIF APP0 (to be skipped), an Exif APP1 holding a
 * TIFF with IFD0 (+ Exif IFD), then SOS. Entries: { tag, ascii } or { tag, long }.
 */
function jpegWithExif({ little, ifd0 = [], exif = null }) {
  const bytes = [];
  const u16 = (v, le = little) => (le ? [v & 255, v >> 8] : [v >> 8, v & 255]);
  const u32 = (v, le = little) => (le ? [v & 255, (v >> 8) & 255, (v >> 16) & 255, v >>> 24] : [v >>> 24, (v >> 16) & 255, (v >> 8) & 255, v & 255]);

  const ifdSize = (n) => 2 + 12 * n + 4;
  const ifd0Entries = exif ? [...ifd0, { tag: 0x8769, pointer: true }] : ifd0;
  const ifd0At = 8;
  const exifAt = ifd0At + ifdSize(ifd0Entries.length);
  let dataAt = exifAt + (exif ? ifdSize(exif.length) : 0);
  const data = [];
  const encode = (entries) => {
    const out = [...u16(entries.length)];
    for (const e of entries.sort((a, b) => a.tag - b.tag)) {
      out.push(...u16(e.tag));
      if (e.pointer) out.push(...u16(4), ...u32(1), ...u32(exifAt));
      else if (e.long !== undefined) out.push(...u16(4), ...u32(1), ...u32(e.long));
      else {
        const chars = [...Buffer.from(`${e.ascii}\0`, "latin1")];
        out.push(...u16(2), ...u32(chars.length));
        if (chars.length <= 4) out.push(...chars, ...new Array(4 - chars.length).fill(0));
        else {
          out.push(...u32(dataAt));
          data.push(...chars);
          dataAt += chars.length;
        }
      }
    }
    out.push(...u32(0));
    return out;
  };
  const tiff = [...(little ? [0x49, 0x49] : [0x4d, 0x4d]), ...u16(42), ...u32(ifd0At), ...encode(ifd0Entries)];
  if (exif) tiff.push(...encode(exif));
  tiff.push(...data);

  const app1 = [...Buffer.from("Exif\0\0", "latin1"), ...tiff];
  bytes.push(0xff, 0xd8);
  bytes.push(0xff, 0xe0, ...u16(16, false), ...Buffer.from("JFIF\0", "latin1"), 1, 1, 0, 0, 1, 0, 1, 0, 0);
  bytes.push(0xff, 0xe1, ...u16(app1.length + 2, false), ...app1);
  bytes.push(0xff, 0xda, 0, 8, 1, 2, 3, 4, 5, 6, 0xff, 0xd9);
  return new Uint8Array(bytes).buffer;
}

const JULY_4 = Date.UTC(2025, 6, 4, 12, 34, 56);

test("readExifTime reads DateTimeOriginal (and its fraction) in both byte orders", () => {
  for (const little of [true, false]) {
    const buf = jpegWithExif({
      little,
      ifd0: [{ tag: 0x0110, ascii: "Phone" }, { tag: 0x0132, ascii: "2030:01:01 00:00:00" }],
      exif: [
        { tag: 0x9003, ascii: "2025:07:04 12:34:56" },
        { tag: 0x9291, ascii: "25" },
      ],
    });
    assert.equal(readExifTime(buf), JULY_4 + 250, little ? "II" : "MM");
  }
});

test("readExifTime falls back to DateTimeDigitized, then IFD0 DateTime", () => {
  const digitized = jpegWithExif({ little: true, exif: [{ tag: 0x9004, ascii: "2025:07:04 12:34:56" }] });
  assert.equal(readExifTime(digitized), JULY_4);
  const plain = jpegWithExif({ little: false, ifd0: [{ tag: 0x0132, ascii: "2025:07:04 12:34:56" }] });
  assert.equal(readExifTime(plain), JULY_4);
  const blank = jpegWithExif({ little: true, exif: [{ tag: 0x9003, ascii: "0000:00:00 00:00:00" }] });
  assert.equal(readExifTime(blank), null, "a zeroed date is no date");
});

test("readExifTime is null, never an error, for anything else", () => {
  const good = new Uint8Array(jpegWithExif({ little: true, exif: [{ tag: 0x9003, ascii: "2025:07:04 12:34:56" }] }));
  assert.equal(readExifTime(new ArrayBuffer(0)), null);
  assert.equal(readExifTime(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]).buffer), null, "a PNG");
  for (let cut = 1; cut < good.length; cut += 3) {
    const t = readExifTime(good.slice(0, cut).buffer);
    assert.ok(t === null || t === JULY_4, `cut at ${cut}`);
  }
  let seed = 7;
  const rand = () => (seed = (seed * 1103515245 + 12345) >>> 0) & 255;
  for (let k = 0; k < 200; k++) {
    const junk = Uint8Array.from({ length: 64 + (k % 50) }, rand);
    if (k % 2) junk.set([0xff, 0xd8, 0xff, 0xe1], 0);
    assert.doesNotThrow(() => readExifTime(junk.buffer));
  }
  // Offsets pointing past the end are refused.
  const broken = good.slice();
  broken[broken.length - 30] ^= 0xff;
  assert.doesNotThrow(() => readExifTime(broken.buffer));
});

test("parseExifDate reads the EXIF form as UTC wall-clock time", () => {
  assert.equal(parseExifDate("2025:07:04 12:34:56"), JULY_4);
  assert.equal(parseExifDate("2025:07:04 12:34:56", "5"), JULY_4 + 500);
  assert.equal(parseExifDate("not a date"), null);
  assert.equal(parseExifDate("2025:13:04 12:34:56"), null);
});
