// Looking at photos in the browser, so a whole album can be narrowed down
// without sending it anywhere. For each File: a small decode (EXIF
// orientation honoured), its difference hash, colour layout and sharpness,
// and its capture time from the EXIF header. Nothing here leaves the page.
//
// The maths is in pure exported functions (pixels in, numbers out) so it is
// tested without a browser: components/film/__tests__/analyze.test.mjs.
// Erasable TypeScript, import-free.

/** What one photo looks like to the shortlist. */
export interface PhotoLook {
  /** 64-bit difference hash, 16 hex digits. */
  hash: string;
  /** 8x8 cells of mean RGB, [r, g, b, …], 192 numbers. */
  layout: number[];
  /** Variance of the Laplacian on the small grayscale copy; higher is crisper. */
  sharpness: number;
  /** EXIF DateTimeOriginal (wall clock read as UTC, ms), or null. */
  exifTime: number | null;
  /** The file's own date (ms), or null. */
  fileTime: number | null;
}

/** Width of the copy every measurement is taken from. */
export const LOOK_WIDTH = 256;
/** At most this many photos are decoded at once. */
export const LOOKS_IN_FLIGHT = 5;
/** Only this much of each file is read for its EXIF header. */
export const EXIF_READ_BYTES = 128 * 1024;

// ---------------------------------------------------------------------------
// Pixels to numbers

/** Luma (BT.601) of an RGBA buffer. */
export function grayFromRgba(rgba: ArrayLike<number>, width: number, height: number): Float32Array {
  const n = width * height;
  const out = new Float32Array(n);
  for (let i = 0, p = 0; i < n; i++, p += 4) out[i] = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2];
  return out;
}

/** Per output cell along one axis: the source pixels it covers and how much of each (fractions sum to 1). */
function axisWeights(size: number, out: number): { from: number; weights: number[] }[] {
  const cells: { from: number; weights: number[] }[] = [];
  const step = size / out;
  for (let o = 0; o < out; o++) {
    const a = o * step;
    const b = (o + 1) * step;
    const from = Math.floor(a);
    const weights: number[] = [];
    for (let s = from; s < Math.min(size, Math.ceil(b)); s++) weights.push((Math.min(b, s + 1) - Math.max(a, s)) / step);
    cells.push({ from, weights });
  }
  return cells;
}

/**
 * Area-average resize of one channel of an interleaved buffer (`stride`
 * values per pixel, the channel at `offset`): every output cell is the mean
 * of exactly the source area it covers, partial pixels weighted.
 */
export function boxResize(
  values: ArrayLike<number>,
  width: number,
  height: number,
  outWidth: number,
  outHeight: number,
  stride = 1,
  offset = 0,
): Float64Array {
  const xs = axisWeights(width, outWidth);
  const ys = axisWeights(height, outHeight);
  const out = new Float64Array(outWidth * outHeight);
  for (let oy = 0; oy < outHeight; oy++) {
    const ry = ys[oy];
    for (let ox = 0; ox < outWidth; ox++) {
      const rx = xs[ox];
      let sum = 0;
      for (let j = 0; j < ry.weights.length; j++) {
        const row = (ry.from + j) * width;
        let line = 0;
        for (let i = 0; i < rx.weights.length; i++) line += values[(row + rx.from + i) * stride + offset] * rx.weights[i];
        sum += line * ry.weights[j];
      }
      out[oy * outWidth + ox] = sum;
    }
  }
  return out;
}

/**
 * The 64-bit difference hash: the grayscale image squeezed to 9x8, one bit
 * per neighbouring pair in each row (1 where the left one is darker). Robust
 * to scaling, re-encoding and overall brightness. 16 hex digits.
 */
export function dHash(gray: ArrayLike<number>, width: number, height: number): string {
  const g = boxResize(gray, width, height, 9, 8);
  let hi = 0;
  let lo = 0;
  for (let y = 0; y < 8; y++) {
    for (let x = 0; x < 8; x++) {
      const bit = g[y * 9 + x] < g[y * 9 + x + 1] ? 1 : 0;
      const k = y * 8 + x;
      if (k < 32) hi = (hi | (bit << (31 - k))) >>> 0;
      else lo = (lo | (bit << (63 - k))) >>> 0;
    }
  }
  return hi.toString(16).padStart(8, "0") + lo.toString(16).padStart(8, "0");
}

/** Mean RGB of each cell of a `cells` x `cells` grid, as [r, g, b, …], rounded to tenths. */
export function colourLayout(rgba: ArrayLike<number>, width: number, height: number, cells = 8): number[] {
  const channels = [0, 1, 2].map((c) => boxResize(rgba, width, height, cells, cells, 4, c));
  const out: number[] = [];
  for (let i = 0; i < cells * cells; i++) for (const ch of channels) out.push(Math.round(ch[i] * 10) / 10);
  return out;
}

/**
 * Variance of the 4-neighbour Laplacian over the inside of a grayscale
 * image: a soft or shaken photo has weak edges and scores low. Only ever
 * compared between photos measured the same way.
 */
export function sharpness(gray: ArrayLike<number>, width: number, height: number): number {
  if (width < 3 || height < 3) return 0;
  let sum = 0;
  let sq = 0;
  let n = 0;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const i = y * width + x;
      const lap = 4 * gray[i] - gray[i - 1] - gray[i + 1] - gray[i - width] - gray[i + width];
      sum += lap;
      sq += lap * lap;
      n++;
    }
  }
  const mean = sum / n;
  return Math.max(0, sq / n - mean * mean);
}

/** Hash, layout and sharpness of an RGBA image. */
export function lookFromPixels(
  rgba: ArrayLike<number>,
  width: number,
  height: number,
): Pick<PhotoLook, "hash" | "layout" | "sharpness"> {
  const gray = grayFromRgba(rgba, width, height);
  return { hash: dHash(gray, width, height), layout: colourLayout(rgba, width, height), sharpness: sharpness(gray, width, height) };
}

// ---------------------------------------------------------------------------
// EXIF

const EXIF_DATE = /^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/;

/** "YYYY:MM:DD HH:MM:SS" as ms (read as UTC: times are only compared with each other), or null. */
export function parseExifDate(text: string, subsec?: string | null): number | null {
  const m = EXIF_DATE.exec(text.trim());
  if (!m) return null;
  const [y, mo, d, h, mi, s] = m.slice(1).map(Number);
  if (y < 1800 || mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59 || s > 60) return null;
  let ms = Date.UTC(y, mo - 1, d, h, mi, s);
  const frac = subsec?.trim().match(/^\d+/)?.[0];
  if (frac) ms += Math.round(Number(`0.${frac}`) * 1000);
  return ms;
}

const TAG = { exifIfd: 0x8769, dateTime: 0x0132, original: 0x9003, digitized: 0x9004, subsecOriginal: 0x9291 } as const;

interface Tiff {
  view: DataView;
  /** Where the TIFF header starts (offsets count from here). */
  base: number;
  /** One past the last readable byte. */
  end: number;
  little: boolean;
}

/** The entries of one IFD as tag -> absolute offset of its 12-byte entry. */
function readIfd(t: Tiff, offset: number): Map<number, number> {
  const entries = new Map<number, number>();
  const at = t.base + offset;
  if (offset < 8 || at + 2 > t.end) return entries;
  const count = t.view.getUint16(at, t.little);
  if (count > 1000) return entries;
  for (let i = 0; i < count; i++) {
    const e = at + 2 + i * 12;
    if (e + 12 > t.end) break;
    entries.set(t.view.getUint16(e, t.little), e);
  }
  return entries;
}

function readAscii(t: Tiff, entry: number | undefined): string | null {
  if (entry === undefined) return null;
  const type = t.view.getUint16(entry + 2, t.little);
  const count = t.view.getUint32(entry + 4, t.little);
  if (type !== 2 || count === 0 || count > 256) return null;
  const at = count <= 4 ? entry + 8 : t.base + t.view.getUint32(entry + 8, t.little);
  if (at < t.base || at + count > t.end) return null;
  let s = "";
  for (let i = 0; i < count; i++) {
    const c = t.view.getUint8(at + i);
    if (c === 0) break;
    s += String.fromCharCode(c);
  }
  return s;
}

function readLong(t: Tiff, entry: number | undefined): number | null {
  if (entry === undefined) return null;
  const type = t.view.getUint16(entry + 2, t.little);
  if (type === 4) return t.view.getUint32(entry + 8, t.little);
  if (type === 3) return t.view.getUint16(entry + 8, t.little);
  return null;
}

function timeFromTiff(t: Tiff): number | null {
  if (t.base + 8 > t.end) return null;
  const order = t.view.getUint16(t.base);
  if (order !== 0x4949 && order !== 0x4d4d) return null;
  t.little = order === 0x4949;
  if (t.view.getUint16(t.base + 2, t.little) !== 42) return null;
  const ifd0 = readIfd(t, t.view.getUint32(t.base + 4, t.little));
  const exifAt = readLong(t, ifd0.get(TAG.exifIfd));
  const exif = exifAt !== null ? readIfd(t, exifAt) : new Map<number, number>();
  const subsec = readAscii(t, exif.get(TAG.subsecOriginal));
  const original = readAscii(t, exif.get(TAG.original));
  const fromOriginal = original ? parseExifDate(original, subsec) : null;
  if (fromOriginal !== null) return fromOriginal;
  const digitized = readAscii(t, exif.get(TAG.digitized));
  const fromDigitized = digitized ? parseExifDate(digitized) : null;
  if (fromDigitized !== null) return fromDigitized;
  const plain = readAscii(t, ifd0.get(TAG.dateTime));
  return plain ? parseExifDate(plain) : null;
}

/**
 * The capture time in a JPEG's EXIF header (DateTimeOriginal, else
 * DateTimeDigitized, else DateTime), from the first bytes of the file. Null
 * for anything else, including truncated or malformed headers. Never throws.
 */
export function readExifTime(buffer: ArrayBuffer): number | null {
  try {
    const view = new DataView(buffer);
    const len = view.byteLength;
    if (len < 4 || view.getUint16(0) !== 0xffd8) return null;
    let p = 2;
    while (p + 4 <= len) {
      if (view.getUint8(p) !== 0xff) return null;
      const marker = view.getUint8(p + 1);
      if (marker === 0xff) {
        p += 1; // fill byte
        continue;
      }
      if (marker === 0xd9 || marker === 0xda) return null; // end, or the image data: no header after this
      if ((marker >= 0xd0 && marker <= 0xd7) || marker === 0x01) {
        p += 2;
        continue;
      }
      const size = view.getUint16(p + 2);
      if (size < 2) return null;
      const body = p + 4;
      // "Exif\0\0"
      if (
        marker === 0xe1 &&
        size >= 16 &&
        body + 6 <= len &&
        view.getUint32(body) === 0x45786966 &&
        view.getUint16(body + 4) === 0
      ) {
        const time = timeFromTiff({ view, base: body + 6, end: Math.min(len, p + 2 + size), little: false });
        if (time !== null) return time;
      }
      p += 2 + size;
    }
    return null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// In the browser

export interface AnalyzeOptions {
  signal?: AbortSignal;
  /** Called after each photo, looked at or skipped. */
  onProgress?: (done: number, total: number) => void;
  concurrency?: number;
}

function abortError(): Error {
  return typeof DOMException === "function" ? new DOMException("Stopped", "AbortError") : new Error("Stopped");
}

/** Lets the page breathe between photos (typing, scrolling, progress). */
function yieldToPage(): Promise<void> {
  const s = (globalThis as { scheduler?: { yield?: () => Promise<void> } }).scheduler;
  if (s?.yield) return s.yield();
  return new Promise((r) => setTimeout(r, 0));
}

type Surface = { canvas: HTMLCanvasElement | OffscreenCanvas; ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D };

function surface(): Surface | null {
  const canvas: HTMLCanvasElement | OffscreenCanvas =
    typeof OffscreenCanvas === "function" ? new OffscreenCanvas(1, 1) : document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true }) as Surface["ctx"] | null;
  return ctx ? { canvas, ctx } : null;
}

/** Fits width x height inside LOOK_WIDTH across (and twice that down), keeping the shape. */
function fitted(width: number, height: number): [number, number] {
  const scale = Math.min(1, LOOK_WIDTH / width, (LOOK_WIDTH * 2) / height);
  return [Math.max(3, Math.round(width * scale)), Math.max(3, Math.round(height * scale))];
}

/**
 * A small decode of the photo, upright: createImageBitmap resizing while it
 * decodes where it can, else a full decode drawn small, else an <img>.
 */
async function decodeSmall(file: Blob, width: number): Promise<ImageBitmap | HTMLImageElement> {
  if (typeof createImageBitmap === "function") {
    try {
      return await createImageBitmap(file, { resizeWidth: width, resizeQuality: "medium", imageOrientation: "from-image" });
    } catch {
      try {
        return await createImageBitmap(file, { imageOrientation: "from-image" });
      } catch {
        // Fall through to <img>, which some browsers decode more kinds with.
      }
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.decoding = "async";
    img.src = url;
    await img.decode();
    return img;
  } finally {
    URL.revokeObjectURL(url);
  }
}

function sizeOf(source: ImageBitmap | HTMLImageElement): [number, number] {
  return source instanceof HTMLImageElement ? [source.naturalWidth, source.naturalHeight] : [source.width, source.height];
}

function release(source: ImageBitmap | HTMLImageElement) {
  if (!(source instanceof HTMLImageElement)) source.close();
}

async function lookAt(file: File, s: Surface): Promise<PhotoLook> {
  const [exifTime, source] = await Promise.all([
    file
      .slice(0, EXIF_READ_BYTES)
      .arrayBuffer()
      .then(readExifTime, () => null),
    decodeSmall(file, LOOK_WIDTH),
  ]);
  try {
    const [w0, h0] = sizeOf(source);
    if (!w0 || !h0) throw new Error("empty image");
    const [w, h] = fitted(w0, h0);
    if (s.canvas.width !== w || s.canvas.height !== h) {
      s.canvas.width = w;
      s.canvas.height = h;
    }
    s.ctx.clearRect(0, 0, w, h);
    s.ctx.drawImage(source, 0, 0, w, h);
    const { data } = s.ctx.getImageData(0, 0, w, h);
    const fileTime = Number.isFinite(file.lastModified) && file.lastModified > 0 ? file.lastModified : null;
    return { ...lookFromPixels(data, w, h), exifTime, fileTime };
  } finally {
    release(source);
  }
}

/**
 * Looks at every file, a few at a time, yielding to the page between
 * photos. A file that cannot be read is null in the result (one bad file
 * never fails the rest). Rejects with an AbortError when `signal` aborts.
 */
export async function analyzePhotos(files: readonly File[], opts: AnalyzeOptions = {}): Promise<(PhotoLook | null)[]> {
  const { signal, onProgress } = opts;
  const total = files.length;
  const out: (PhotoLook | null)[] = new Array(total).fill(null);
  let next = 0;
  let done = 0;
  if (signal?.aborted) throw abortError();
  const worker = async () => {
    const s = surface();
    while (next < total) {
      if (signal?.aborted) return;
      const i = next++;
      try {
        if (!s) throw new Error("no canvas");
        out[i] = await lookAt(files[i], s);
      } catch {
        out[i] = null;
      }
      done += 1;
      if (!signal?.aborted) onProgress?.(done, total);
      await yieldToPage();
    }
  };
  const lanes = Math.max(1, Math.min(opts.concurrency ?? LOOKS_IN_FLIGHT, total));
  await Promise.all(Array.from({ length: lanes }, worker));
  if (signal?.aborted) throw abortError();
  return out;
}

// ---------------------------------------------------------------------------
// Thumbnails

/** Long side of a thumbnail preview, in pixels (a 88px tile at 2x, with room to crop). */
export const PREVIEW_SIZE = 240;
const PREVIEWS_IN_FLIGHT = 4;
let previewsRunning = 0;
const previewQueue: (() => void)[] = [];

async function inPreviewLane<T>(fn: () => Promise<T>): Promise<T> {
  if (previewsRunning >= PREVIEWS_IN_FLIGHT) await new Promise<void>((r) => previewQueue.push(r));
  previewsRunning += 1;
  try {
    return await fn();
  } finally {
    previewsRunning -= 1;
    previewQueue.shift()?.();
  }
}

/**
 * An object URL of a small JPEG copy of the photo, for a thumbnail. The
 * caller revokes it. Falls back to the file itself if it cannot be shrunk.
 */
export function makePreview(file: File): Promise<string> {
  return inPreviewLane(async () => {
    let source: ImageBitmap | HTMLImageElement | null = null;
    try {
      source = await decodeSmall(file, PREVIEW_SIZE);
      const [w0, h0] = sizeOf(source);
      const scale = Math.min(1, PREVIEW_SIZE / Math.min(w0, h0));
      const w = Math.max(1, Math.round(w0 * scale));
      const h = Math.max(1, Math.round(h0 * scale));
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("no canvas");
      ctx.drawImage(source, 0, 0, w, h);
      const blob = await new Promise<Blob | null>((r) => canvas.toBlob(r, "image/jpeg", 0.82));
      if (!blob) throw new Error("no blob");
      return URL.createObjectURL(blob);
    } catch {
      return URL.createObjectURL(file);
    } finally {
      if (source) release(source);
    }
  });
}
