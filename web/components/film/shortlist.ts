// Choosing a film's photos from a whole album, in the browser. Pure: no DOM,
// no I/O. analyze.ts turns each File into a PhotoSignal; this file finds the
// copies (re-saves, edits, crops, burst frames), orders the album by capture
// time and picks up to 40 photos that cover the day evenly, never two that
// look alike, sharper ones first.
//
// Telling a copy from a different moment in the same place is the hard part.
// A copy differs from its original a little everywhere (or not at all, once
// the frames are lined up); a different moment in the same place differs a
// lot in a few places (where the people are) and hardly at all elsewhere. So
// two photos are compared on their fine structure (analyze.ts fineSignature)
// block by block, and it is the WORST block that decides: one person-sized
// difference is enough to call them different moments.
//
// Erasable TypeScript, import-free. Tests: components/film/__tests__/shortlist.test.mjs.

/** One analysed photo. */
export interface PhotoSignal {
  /** The caller's id for the photo (its place in the album). Returned in `chosen`. */
  index: number;
  /** Capture time in ms, or null when unknown. */
  takenAt: number | null;
  /** Higher is crisper (analyze.ts: variance of the Laplacian). Only compared, never shown. */
  sharpness: number;
  /** 64-bit difference hash: a bigint, or 16 hex digits. A cheap first look. */
  hash: bigint | string;
  /** analyze.ts fineSignature: 3 channels of FINE_SIZE x FINE_SIZE ranks. */
  fine: ArrayLike<number>;
}

/** Side of the fine-structure grid (analyze.ts FINE_SIZE). */
export const FINE_SIZE = 32;
/** Two captures this close together are a burst, and judged more loosely. */
export const BURST_WINDOW_MS = 5000;
/**
 * Photos this close in time are compared allowing for a crop or shift
 * between them: an edit keeps its capture time, a burst frame is seconds
 * away. Photos further apart are only compared frame to frame, which also
 * keeps the work small when a whole match is shot from one spot.
 */
export const NEAR_WINDOW_MS = 10_000;
/**
 * When either capture time is unknown, nothing says how far apart two photos
 * were taken (an edited copy can land anywhere in the album), so any two
 * whose palettes (how light pairs with colour, wherever it is in the frame)
 * are within UNKNOWN_TIME_PALETTE are compared lined up; for a photo with no
 * colour, any two within UNKNOWN_TIME_BITS hash bits. Measured on the review
 * albums: 98.6% of copy pairs within 0.8 of palette and 0.6% of pairs of
 * different scenes; a crop or shift can move the hash 30 bits or more.
 */
export const UNKNOWN_TIME_PALETTE = 0.8;
export const UNKNOWN_TIME_BITS = 26;
/**
 * A photo with no time is lined up against at most this many kept photos,
 * the most promising first: ranked by how well a coarse 8x8 grid of the two
 * lines up under a few crops and shifts (coarseKey). On the review albums a
 * copy's original came first by that ranking in the median, and in the top 8
 * for 139 of 140 copies (by palette alone it came as low as 63rd).
 *
 * The limit is about distinct moments more than time. In an album shot in
 * one place with no times, every pair passes the palette test, and each
 * extra lining-up between two different moments carries a small chance (2%
 * of those tried) of calling them alike. Measured on 200 moments in one
 * place plus 40 copies: lining up every candidate kept 147 of the 200
 * moments, took 8 s and still left 2 copies kept apart; 16 kept 158; 8 kept
 * 168 to 181 with at most one copy kept apart, in about 1.4 s. What the
 * limit misses is caught later: a photo set aside is moved to the group of
 * the photo it matches best (prepareAlbum), and the picker never puts two
 * copies of one picture in the film (Album.sameUntimed).
 */
export const UNTIMED_LINE_UPS = 8;
/** A coarse key above this has never been a copy of one picture (copies measured up to 1.70). */
const COPY_KEY_MAX = 1.8;

/**
 * The look-alike rules. `distance` is the fine difference of the worst block
 * (an eighth of the frame each way) between two photos, in standard
 * deviations of the photos' own light and colour ranks (cells blown out or
 * crushed in either photo left out), after lining the frames up where
 * allowed.
 *
 * Measured on the review albums (621 photos: 150 scenes with re-encodes,
 * resizes, crops, shifts, exposure, contrast and saturation edits, burst
 * frames, EXIF-less and rotated copies; and 60 distinct moments each against
 * one park, one beach and one dark living-room background): re-saves and
 * colour edits of one picture came within 6 hash bits and 0.93, frame to
 * frame; crops, shifts and burst frames, once lined up, mostly within 1.0
 * and all within 1.4 (bursts are allowed 1.54). Distinct moments in the same
 * place were never closer than 0.98 frame to frame and mostly above 1.5;
 * some shared a hash to the bit, so the hash alone never decides.
 */
export const ALIKE = {
  /** The same picture saved again or recoloured: the frames already line up. */
  samePicture: { bits: 6, distance: 0.85 },
  /** A copy or a near-identical frame (lined up first when the two are near in time). */
  lookAlike: 1.1,
  /** Within BURST_WINDOW_MS the camera moved a little between frames: this much looser. */
  burstFactor: 1.4,
  /** Photos far apart in time are only compared at all within this many hash bits. */
  farBits: 6,
} as const;

export interface AlikeOptions {
  /** Only exact copies (samePicture): for a handful of photos someone chose one by one. */
  strict?: boolean;
  /** Times are capture times, so bursts can be told (default true). Off when they are file dates. */
  bursts?: boolean;
}

// ---------------------------------------------------------------------------
// Hashes

/** A 64-bit hash as two unsigned 32-bit halves. Garbage reads as 0. */
export function hashHalves(hash: bigint | string): [number, number] {
  const hex = (typeof hash === "bigint" ? hash.toString(16) : hash.trim().replace(/^0x/i, "")).padStart(16, "0").slice(-16);
  const hi = parseInt(hex.slice(0, 8), 16);
  const lo = parseInt(hex.slice(8), 16);
  return [Number.isFinite(hi) ? hi >>> 0 : 0, Number.isFinite(lo) ? lo >>> 0 : 0];
}

function popcount32(x: number): number {
  x = x - ((x >>> 1) & 0x55555555);
  x = (x & 0x33333333) + ((x >>> 2) & 0x33333333);
  x = (x + (x >>> 4)) & 0x0f0f0f0f;
  return Math.imul(x, 0x01010101) >>> 24;
}

/** Bits that differ between two 64-bit hashes. */
export function hamming(a: bigint | string, b: bigint | string): number {
  const [ah, al] = hashHalves(a);
  const [bh, bl] = hashHalves(b);
  return popcount32(ah ^ bh) + popcount32(al ^ bl);
}

// ---------------------------------------------------------------------------
// Fine structure

const N = FINE_SIZE;
const NN = N * N;
/** Blocks are BLOCK x BLOCK cells: an 8 x 8 grid of blocks over the frame. */
const BLOCK = 4;
const BLOCKS = N / BLOCK;
/** Colour counts half as much as light. */
const CHROMA_WEIGHT = 0.5;
/** Without colour, light alone is scaled to stand for all three channels. */
const LIGHT_ONLY_WEIGHT = 1.6;
/** A moved frame only counts if it lines up the bulk of the picture this much better than leaving it. */
const BULK_GAIN = 0.85;
/** A move trimming no more than this off any edge needs no such proof. */
const SMALL_MOVE = 0.05;
/** A lined-up fit within this many times the bound is nudged to see if it gets inside. */
const REFINE_WITHIN = 2;
/** A result within this many times the bound is tried again from the other photo's side. */
const RETRY_WITHIN = 1.3;
/** How far frames may be moved: each side may lose up to a fifth, the scale may change by a quarter. */
const MIN_OVERLAP = 0.7;
const MIN_SCALE = 0.78;
const MAX_SCALE = 1.28;

/** A photo's fine structure made ready to compare. */
interface Fine {
  /** Each channel standardised (mean 0, spread 1). */
  full: Float32Array;
  /** Per axis, four profiles of N: plain light, then light, blue and red detail. */
  col: Float32Array;
  row: Float32Array;
  /** No colour to speak of: compared on light alone. */
  grey: boolean;
  /**
   * Clipped cells: 1 blown out or black (nothing to compare), 0.5 one channel
   * crushed (light only); null when there are none.
   */
  mask: Float32Array | null;
  /** Each block's mean in each standardised channel: a quick lower bound on the worst block. */
  blockMeans: Float32Array;
  /**
   * How light and colour go together, wherever they are in the frame: a 4x4
   * histogram of (light, blue) rank quarters and one of (light, red), as
   * fractions. A crop or shift barely changes it; another scene does.
   */
  palette: Float32Array;
  /** Made when first needed: the coarse grids under each of COARSE_MOVES (see coarseKey). */
  coarse: Float32Array | null;
}

function standardise(src: ArrayLike<number>, off: number, out: Float32Array, outOff: number): number {
  let s = 0;
  let q = 0;
  for (let i = 0; i < NN; i++) {
    const v = src[off + i];
    s += v;
    q += v * v;
  }
  const mean = s / NN;
  const spread = Math.sqrt(Math.max(0, q / NN - mean * mean));
  const sd = Math.max(0.02, spread);
  for (let i = 0; i < NN; i++) out[outOff + i] = (src[off + i] - mean) / sd;
  return spread;
}

const prepared = new WeakMap<object, Fine>();

function fineOf(signal: Pick<PhotoSignal, "fine">): Fine {
  const key = signal.fine as unknown as object;
  const known = prepared.get(key);
  if (known) return known;
  const src = signal.fine;
  const full = new Float32Array(3 * NN);
  let grey = false;
  if (src.length >= 3 * NN) {
    standardise(src, 0, full, 0);
    const u = standardise(src, NN, full, NN);
    const v = standardise(src, 2 * NN, full, 2 * NN);
    grey = u < 1e-4 || v < 1e-4;
  } else {
    grey = true;
  }
  // Profiles for lining frames up: column (and row) means of light, plus the
  // mean distance of each channel from its row (column) mean, which shows
  // shapes and edges whatever their tone.
  const col = new Float32Array(4 * N);
  const row = new Float32Array(4 * N);
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      const v = full[y * N + x] / N;
      col[x] += v;
      row[y] += v;
    }
  }
  const rowMean = new Float32Array(N);
  const colMean = new Float32Array(N);
  for (let c = 0; c < 3; c++) {
    rowMean.fill(0);
    colMean.fill(0);
    for (let y = 0; y < N; y++) {
      for (let x = 0; x < N; x++) {
        const v = full[c * NN + y * N + x] / N;
        rowMean[y] += v;
        colMean[x] += v;
      }
    }
    for (let y = 0; y < N; y++) {
      for (let x = 0; x < N; x++) {
        const v = full[c * NN + y * N + x];
        col[(c + 1) * N + x] += Math.abs(v - rowMean[y]) / N;
        row[(c + 1) * N + y] += Math.abs(v - colMean[x]) / N;
      }
    }
  }
  let mask: Float32Array | null = null;
  if (src.length >= 4 * NN) {
    for (let i = 0; i < NN; i++) {
      const m = src[3 * NN + i];
      if (m > 0.25) {
        mask ??= new Float32Array(NN);
        mask[i] = m > 0.75 ? 1 : 0.5;
      }
    }
  }
  const blockMeans = new Float32Array(3 * BLOCKS * BLOCKS);
  for (let c = 0; c < 3; c++) {
    for (let y = 0; y < N; y++) {
      for (let x = 0; x < N; x++) {
        blockMeans[c * BLOCKS * BLOCKS + ((y / BLOCK) | 0) * BLOCKS + ((x / BLOCK) | 0)] += full[c * NN + y * N + x] / (BLOCK * BLOCK);
      }
    }
  }
  const palette = new Float32Array(2 * PALETTE_BINS * PALETTE_BINS);
  if (src.length >= 3 * NN) {
    let counted = 0;
    const bin = (v: number) => Math.min(PALETTE_BINS - 1, Math.max(0, Math.floor(v * PALETTE_BINS)));
    for (let i = 0; i < NN; i++) {
      if (mask && mask[i]) continue; // clipped colour says nothing about the palette
      const y = bin(src[i]);
      palette[y * PALETTE_BINS + bin(src[NN + i])]++;
      palette[PALETTE_BINS * PALETTE_BINS + y * PALETTE_BINS + bin(src[2 * NN + i])]++;
      counted++;
    }
    for (let k = 0; k < palette.length; k++) palette[k] /= Math.max(1, counted);
  }
  const made = { full, col, row, grey, mask, blockMeans, palette, coarse: null };
  prepared.set(key, made);
  return made;
}

const PALETTE_BINS = 4;

/** How differently two photos pair light with colour (0 to 4), wherever things are in the frame. */
function paletteGap(a: Fine, b: Fine): number {
  let s = 0;
  for (let k = 0; k < a.palette.length; k++) s += Math.abs(a.palette[k] - b.palette[k]);
  return s;
}

/**
 * Whether two photos with no capture time between them are worth lining up:
 * their hashes are within ALIKE.farBits (all but the same picture), or their
 * palettes are close (a crop or shift of one picture; or another moment in
 * the same place), or, when either has no colour, their hashes are within
 * UNKNOWN_TIME_BITS.
 */
function worthLiningUp(a: Pick<PhotoSignal, "fine">, b: Pick<PhotoSignal, "fine">, bits: number): boolean {
  return liningUpKey(a, b, bits) < Infinity;
}

/**
 * worthLiningUp as a ranking (lower is more promising, see coarseKey),
 * Infinity when not worth it.
 */
function liningUpKey(a: Pick<PhotoSignal, "fine">, b: Pick<PhotoSignal, "fine">, bits: number, ceiling = Infinity): number {
  if (bits <= ALIKE.farBits) return 0;
  const fa = fineOf(a);
  const fb = fineOf(b);
  if (fa.grey || fb.grey) return bits <= UNKNOWN_TIME_BITS ? coarseKey(fa, fb, ceiling) : Infinity;
  return paletteGap(fa, fb) <= UNKNOWN_TIME_PALETTE ? coarseKey(fa, fb, ceiling) : Infinity;
}

/** The few lowest keys seen so far, to give coarseKey its ceiling. */
class Lowest {
  private keys: number[] = [];
  private readonly size: number;
  constructor(size: number) {
    this.size = size;
  }
  /** The key a new one must beat to be among them. */
  get ceiling(): number {
    return this.keys.length < this.size ? Infinity : this.keys[this.size - 1];
  }
  add(key: number) {
    if (key >= this.ceiling) return;
    let at = this.keys.length;
    while (at > 0 && this.keys[at - 1] > key) at--;
    this.keys.splice(at, 0, key);
    if (this.keys.length > this.size) this.keys.pop();
  }
}

/**
 * The crops and shifts coarseKey tries: [left, top, share across, share
 * down], the first being none. Each axis is either left alone or cut to 88%
 * at its start, middle or end, so a sideways shift (a lot off one side, a
 * little off the top) is covered as well as an even crop.
 */
const COARSE_AXIS: [number, number][] = [
  [0, 1],
  [0, 0.88],
  [0.06, 0.88],
  [0.12, 0.88],
];
const COARSE_MOVES: [number, number, number, number][] = [];
for (const [ox, sx] of COARSE_AXIS) for (const [oy, sy] of COARSE_AXIS) COARSE_MOVES.push([ox, oy, sx, sy]);
const COARSE_CELLS = BLOCKS * BLOCKS;

/** A photo's coarse 8x8 grid (its block means) seen through each of COARSE_MOVES. */
function coarseGrids(f: Fine): Float32Array {
  if (f.coarse) return f.coarse;
  const out = new Float32Array(COARSE_MOVES.length * 3 * COARSE_CELLS);
  COARSE_MOVES.forEach(([ox, oy, sx, sy], t) => {
    for (let c = 0; c < 3; c++) {
      for (let j = 0; j < BLOCKS; j++) {
        for (let i = 0; i < BLOCKS; i++) {
          const fx = Math.min(BLOCKS - 1, Math.max(0, (ox + (sx * (i + 0.5)) / BLOCKS) * BLOCKS - 0.5));
          const fy = Math.min(BLOCKS - 1, Math.max(0, (oy + (sy * (j + 0.5)) / BLOCKS) * BLOCKS - 0.5));
          const x0 = Math.floor(fx);
          const y0 = Math.floor(fy);
          const x1 = Math.min(BLOCKS - 1, x0 + 1);
          const y1 = Math.min(BLOCKS - 1, y0 + 1);
          const tx = fx - x0;
          const ty = fy - y0;
          const g = f.blockMeans;
          const b = c * COARSE_CELLS;
          out[(t * 3 + c) * COARSE_CELLS + j * BLOCKS + i] =
            (g[b + y0 * BLOCKS + x0] * (1 - tx) + g[b + y0 * BLOCKS + x1] * tx) * (1 - ty) +
            (g[b + y1 * BLOCKS + x0] * (1 - tx) + g[b + y1 * BLOCKS + x1] * tx) * ty;
        }
      }
    }
  });
  f.coarse = out;
  return out;
}

/**
 * How well two photos' coarse 8x8 grids line up under the best of a few
 * crops and shifts (either way round): the worst cell's difference. Cheap
 * (a few microseconds), used only to decide which photos to line up first:
 * a crop or shift of one picture scores low, another moment in the same
 * place usually higher, since its people stand elsewhere. Only ever needs to
 * beat `ceiling` (the last of the few that will be lined up): once no move
 * can, it stops and returns `ceiling`.
 */
function coarseKey(a: Fine, b: Fine, ceiling = Infinity): number {
  const ga = coarseGrids(a);
  const gb = coarseGrids(b);
  const chroma = a.grey || b.grey ? 0 : CHROMA_WEIGHT;
  const light = chroma ? 1 : LIGHT_ONLY_WEIGHT;
  let best = ceiling;
  for (let t = 0; t < COARSE_MOVES.length; t++) {
    for (let way = 0; way < (t === 0 ? 1 : 2); way++) {
      // p moved by t against q as it is (move 0).
      const p = way ? gb : ga;
      const q = way ? ga : gb;
      const pOff = t * 3 * COARSE_CELLS;
      let worst = 0;
      for (let k = 0; k < COARSE_CELLS && worst < best; k++) {
        let d = light * Math.abs(p[pOff + k] - q[k]);
        if (chroma) {
          d += chroma * (Math.abs(p[pOff + COARSE_CELLS + k] - q[COARSE_CELLS + k]) + Math.abs(p[pOff + 2 * COARSE_CELLS + k] - q[2 * COARSE_CELLS + k]));
        }
        if (d > worst) worst = d;
      }
      if (worst < best) best = worst;
    }
  }
  return best;
}

/**
 * A lower bound on the worst block, frame to frame: a block's mean
 * difference is at least the difference of its means. Cheap (a few hundred
 * steps against a few thousand), and enough to tell most different moments
 * apart, since a person standing in a block moves its mean.
 */
function worstBlockAtLeast(a: Fine, b: Fine, light: number, chroma: number): number {
  const m = BLOCKS * BLOCKS;
  let worst = 0;
  for (let k = 0; k < m; k++) {
    let s = light * Math.abs(a.blockMeans[k] - b.blockMeans[k]);
    if (chroma) s += chroma * (Math.abs(a.blockMeans[m + k] - b.blockMeans[m + k]) + Math.abs(a.blockMeans[2 * m + k] - b.blockMeans[2 * m + k]));
    if (s > worst) worst = s;
  }
  return worst;
}

// Scratch space (one comparison at a time).
const blockScores = new Float32Array(BLOCKS * BLOCKS);
const rawA = new Float32Array(3 * NN);
const rawB = new Float32Array(3 * NN);
const viewA = new Float32Array(3 * NN);
const viewB = new Float32Array(3 * NN);
const maskViewA = new Float32Array(NN);
const maskViewB = new Float32Array(NN);
const lineA = new Float32Array(32);
const lineB = new Float32Array(32);
/** A block with fewer usable cells than this is not judged. */
const MIN_CELLS = (BLOCK * BLOCK) / 4;

/**
 * The worst block's mean difference, leaving out cells clipped in either
 * photo. Stops as soon as one block passes `bound`; with `bulk`, measures
 * every block and also returns the median.
 */
function worstBlock(
  a: Float32Array,
  b: Float32Array,
  light: number,
  chroma: number,
  bound: number,
  bulk: { median: number } | null,
  maskA: Float32Array | null = null,
  maskB: Float32Array | null = null,
): number {
  let worst = 0;
  let n = 0;
  for (let by = 0; by < BLOCKS; by++) {
    for (let bx = 0; bx < BLOCKS; bx++) {
      let s = 0;
      let cells = 0;
      for (let j = 0; j < BLOCK; j++) {
        const rowStart = (by * BLOCK + j) * N + bx * BLOCK;
        for (let i = 0; i < BLOCK; i++) {
          const k = rowStart + i;
          const m = Math.max(maskA ? maskA[k] : 0, maskB ? maskB[k] : 0);
          if (m >= 0.75) continue; // blown out or black in one of them
          cells++;
          if (chroma && m < 0.25) {
            s += light * Math.abs(a[k] - b[k]) + chroma * (Math.abs(a[NN + k] - b[NN + k]) + Math.abs(a[2 * NN + k] - b[2 * NN + k]));
          } else {
            // No colour to trust in this cell: its light alone.
            s += light * Math.abs(a[k] - b[k]);
          }
        }
      }
      if (cells < MIN_CELLS) continue;
      s /= cells;
      blockScores[n++] = s;
      if (s > worst) {
        worst = s;
        if (worst > bound && !bulk) return worst;
      }
    }
  }
  if (bulk) bulk.median = n ? blockScores.slice(0, n).sort()[n >> 1] : 0;
  return worst;
}

function lerp(a: Float32Array, off: number, u: number): number {
  const f = Math.min(N - 1, Math.max(0, u * N - 0.5));
  const i = Math.floor(f);
  const j = Math.min(N - 1, i + 1);
  const t = f - i;
  return a[off + i] * (1 - t) + a[off + j] * t;
}

/**
 * How badly two profile sets disagree under x_b = (x_a - o) / s, over their
 * overlap (k samples each, the first `profiles` profiles).
 */
function profileGap(a: Float32Array, b: Float32Array, o: number, s: number, k: number, profiles = 4): number {
  const l = Math.max(0, o);
  const r = Math.min(1, o + s);
  if (s < MIN_SCALE || s > MAX_SCALE || r - l < MIN_OVERLAP || (r - l) / s < MIN_OVERLAP) return Infinity;
  let total = 0;
  for (let p = 0; p < profiles; p++) {
    const off = p * N;
    let ma = 0;
    let mb = 0;
    for (let t = 0; t < k; t++) {
      const ua = l + ((r - l) * (t + 0.5)) / k;
      lineA[t] = lerp(a, off, ua);
      lineB[t] = lerp(b, off, (ua - o) / s);
      ma += lineA[t];
      mb += lineB[t];
    }
    ma /= k;
    mb /= k;
    let qa = 0;
    let qb = 0;
    for (let t = 0; t < k; t++) {
      qa += (lineA[t] - ma) ** 2;
      qb += (lineB[t] - mb) ** 2;
    }
    const sa = Math.sqrt(qa / k);
    const sb = Math.sqrt(qb / k);
    // A flat profile says nothing about where the frames line up.
    const weight = Math.min(sa, sb);
    if (weight < 1e-3) continue;
    let e = 0;
    for (let t = 0; t < k; t++) e += Math.abs((lineA[t] - ma) / sa - (lineB[t] - mb) / sb);
    total += (e / k) * weight;
  }
  return total;
}

const SCALES = [0.8, 0.84, 0.88, 0.92, 0.96, 1, 1.04, 1.09, 1.14, 1.19, 1.25];
/** How many alignments per axis go on to be tried in two dimensions. */
const LINE_UP_CANDIDATES = 2;

/**
 * The offsets and scales along one axis that best line up two profile sets,
 * best first: a coarse search, then the best few distinct starting points
 * refined. One minimum is not trusted on its own, since a picture with few
 * features along an axis can fit a wrong scale nearly as well.
 */
function lineUp(a: Float32Array, b: Float32Array): [number, number][] {
  // Coarse: light only (plain and detail), a few samples.
  const coarse: [number, number, number][] = [];
  for (const scale of SCALES) {
    for (let off = -0.24; off <= 0.2401; off += 0.04) {
      const e = profileGap(a, b, off, scale, 10, 2);
      if (e < Infinity) coarse.push([e, off, scale]);
    }
  }
  coarse.sort((x, y) => x[0] - y[0]);
  const starts: [number, number][] = [];
  for (const [, off, scale] of coarse) {
    if (starts.length >= LINE_UP_CANDIDATES) break;
    if (starts.every(([o, s]) => Math.abs(o - off) > 0.05 || Math.abs(s - scale) > 0.05)) starts.push([off, scale]);
  }
  return starts.map(([o0, s0]) => {
    let o = o0;
    let s = s0;
    // The two-dimensional nudging in fineDistance takes it the rest of the way.
    for (const [step, k, profiles] of [
      [0.02, 20, 4],
      [0.01, 20, 4],
    ]) {
      let val = profileGap(a, b, o, s, k, profiles);
      for (let it = 0; it < 3; it++) {
        let moved = false;
        for (const [d0, d1] of [
          [step, 0],
          [-step, 0],
          [0, step],
          [0, -step],
          [step, -step],
          [-step, step],
        ]) {
          const v = profileGap(a, b, o + d0, s + d1, k, profiles);
          if (v < val) {
            val = v;
            o += d0;
            s += d1;
            moved = true;
          }
        }
        if (!moved) break;
      }
    }
    return [o, s] as [number, number];
  });
}

function sampleCell(f: Float32Array, x: number, y: number, c: number): number {
  const fx = Math.min(N - 1, Math.max(0, x * N - 0.5));
  const fy = Math.min(N - 1, Math.max(0, y * N - 0.5));
  const x0 = Math.floor(fx);
  const y0 = Math.floor(fy);
  const x1 = Math.min(N - 1, x0 + 1);
  const y1 = Math.min(N - 1, y0 + 1);
  const tx = fx - x0;
  const ty = fy - y0;
  const base = c * NN;
  return (
    (f[base + y0 * N + x0] * (1 - tx) + f[base + y0 * N + x1] * tx) * (1 - ty) +
    (f[base + y1 * N + x0] * (1 - tx) + f[base + y1 * N + x1] * tx) * ty
  );
}

/** Both photos sampled over their common part under x_b = (x_a - ox) / sx (and the same down), each standardised there. */
function overlapViews(a: Fine, b: Fine, ox: number, oy: number, sx: number, sy: number) {
  const lx = Math.max(0, ox);
  const rx = Math.min(1, ox + sx);
  const ly = Math.max(0, oy);
  const ry = Math.min(1, oy + sy);
  for (let c = 0; c < 3; c++) {
    for (let j = 0; j < N; j++) {
      const ya = ly + ((ry - ly) * (j + 0.5)) / N;
      const yb = (ya - oy) / sy;
      for (let i = 0; i < N; i++) {
        const xa = lx + ((rx - lx) * (i + 0.5)) / N;
        rawA[c * NN + j * N + i] = sampleCell(a.full, xa, ya, c);
        rawB[c * NN + j * N + i] = sampleCell(b.full, (xa - ox) / sx, yb, c);
      }
    }
    standardise(rawA, c * NN, viewA, c * NN);
    standardise(rawB, c * NN, viewB, c * NN);
  }
  // Clipped cells follow along, blended like everything else (worstBlock reads 0.75 and 0.25 as the cut-offs).
  for (const [fine, view, toB] of [
    [a, maskViewA, false],
    [b, maskViewB, true],
  ] as const) {
    if (!fine.mask) continue;
    for (let j = 0; j < N; j++) {
      const ya = ly + ((ry - ly) * (j + 0.5)) / N;
      for (let i = 0; i < N; i++) {
        const xa = lx + ((rx - lx) * (i + 0.5)) / N;
        view[j * N + i] = sampleCell(fine.mask, toB ? (xa - ox) / sx : xa, toB ? (ya - oy) / sy : ya, 0);
      }
    }
  }
}

function allowed(ox: number, oy: number, sx: number, sy: number): boolean {
  const fits = (o: number, s: number) => {
    const l = Math.max(0, o);
    const r = Math.min(1, o + s);
    return s >= MIN_SCALE && s <= MAX_SCALE && r - l >= MIN_OVERLAP && (r - l) / s >= MIN_OVERLAP;
  };
  return fits(ox, sx) && fits(oy, sy);
}

/**
 * The fine difference between two photos: the worst block, frame to frame,
 * or (with `lineUp`) after lining the frames up when that helps the bulk of
 * the picture. Once it is clear the answer is above `bound`, the exact
 * figure is not chased.
 */
export function fineDistance(
  a: Pick<PhotoSignal, "fine">,
  b: Pick<PhotoSignal, "fine">,
  bound = Infinity,
  allowLineUp = true,
  refineWithin = REFINE_WITHIN,
): number {
  const d = fineDistanceOneWay(a, b, bound, allowLineUp, refineWithin);
  // The search for an alignment starts from one photo's side; on a close
  // call, starting from the other's can find what the first missed.
  if (allowLineUp && d > bound && d <= bound * RETRY_WITHIN) return Math.min(d, fineDistanceOneWay(b, a, bound, true, refineWithin));
  return d;
}

function fineDistanceOneWay(
  a: Pick<PhotoSignal, "fine">,
  b: Pick<PhotoSignal, "fine">,
  bound: number,
  allowLineUp: boolean,
  refineWithin: number,
): number {
  const fa = fineOf(a);
  const fb = fineOf(b);
  const grey = fa.grey || fb.grey;
  const light = grey ? LIGHT_ONLY_WEIGHT : 1;
  const chroma = grey ? 0 : CHROMA_WEIGHT;
  const still = { median: 0 };
  if (!allowLineUp && !fa.mask && !fb.mask) {
    const atLeast = worstBlockAtLeast(fa, fb, light, chroma);
    if (atLeast > bound) return atLeast;
  }
  const direct = worstBlock(fa.full, fb.full, light, chroma, bound, allowLineUp ? still : null, fa.mask, fb.mask);
  const mA = fa.mask ? maskViewA : null;
  const mB = fb.mask ? maskViewB : null;
  if (direct <= bound || !allowLineUp) return direct;

  // Line the frames up both ways round (a onto b, and b onto a turned back),
  // trying the best few alignments of each axis together, and keep whichever
  // lines up best.
  let best = Infinity;
  let ox = 0;
  let oy = 0;
  let sx = 1;
  let sy = 1;
  let hopeless = false;
  const tryAll = (tries: number[][]): boolean => {
    for (let k = 0; k < tries.length; k++) {
      const t = tries[k];
      if (!allowed(t[0], t[1], t[2], t[3])) continue;
      if (Math.abs(t[0]) < 0.004 && Math.abs(t[1]) < 0.004 && Math.abs(t[2] - 1) < 0.004 && Math.abs(t[3] - 1) < 0.004) continue;
      overlapViews(fa, fb, t[0], t[1], t[2], t[3]);
      // A cheap first look: no need to measure every block of a poor fit.
      // When even the best-placed fit (the first) is far off, the rest of
      // this way round are not worth a look.
      const cap = Math.min(best, bound * 2);
      if (worstBlock(viewA, viewB, light, chroma, cap, null, mA, mB) >= cap) {
        if (k === 0) {
          hopeless = true;
          return false;
        }
        continue;
      }
      const moved = { median: 0 };
      const v = worstBlock(viewA, viewB, light, chroma, Infinity, moved, mA, mB);
      // Two moments in one place already line up where nothing happened;
      // moving a frame far enough to hide a difference at its edge is only
      // allowed when it lines up the rest of the picture clearly better. A
      // small move (at most SMALL_MOVE off any edge) cannot hide anyone.
      const trim = Math.max(Math.abs(t[0]), Math.abs(t[1]), Math.abs(t[0] + t[2] - 1), Math.abs(t[1] + t[3] - 1));
      if (trim > SMALL_MOVE && !(moved.median < BULK_GAIN * still.median)) continue;
      if (v < best) {
        best = v;
        [ox, oy, sx, sy] = t;
        if (best <= bound) return true; // that settles it
      }
    }
    return false;
  };
  const pairs = (xs: [number, number][], ys: [number, number][], back: boolean) => {
    const out: number[][] = [];
    for (const [o1, s1] of xs) for (const [o2, s2] of ys) out.push(back ? [-o1 / s1, -o2 / s2, 1 / s1, 1 / s2] : [o1, o2, s1, s2]);
    return out;
  };
  if (tryAll(pairs(lineUp(fa.col, fb.col), lineUp(fa.row, fb.row), false))) return best;
  // The other way round gets a go only when the first was neither close nor
  // hopeless (its best-placed fit more than twice the bound out).
  if (!hopeless && best > bound * 1.5 && tryAll(pairs(lineUp(fb.col, fa.col), lineUp(fb.row, fa.row), true))) return best;
  if (best === Infinity) return direct;
  // Only a close call is worth nudging: further out, a nudge has never turned
  // a different moment into a copy, and it is the costly part.
  if (best <= bound || best > bound * refineWithin) return Math.min(direct, best);

  // A close call: nudge the alignment.
  for (const step of [0.015, 0.0075, 0.004]) {
    for (let it = 0; it < 4; it++) {
      let improved = false;
      for (let k = 0; k < 4; k++) {
        for (const d of [-step, step]) {
          const t = [ox, oy, sx, sy];
          t[k] += d;
          if (k >= 2) t[k - 2] -= d / 2; // grow or shrink about the centre
          if (!allowed(t[0], t[1], t[2], t[3])) continue;
          overlapViews(fa, fb, t[0], t[1], t[2], t[3]);
          const v = worstBlock(viewA, viewB, light, chroma, best, null, mA, mB);
          if (v < best) {
            best = v;
            [ox, oy, sx, sy] = t;
            improved = true;
          }
        }
      }
      if (!improved) break;
    }
    if (best <= bound) break;
  }
  return Math.min(direct, best);
}

/** The same picture saved again or recoloured (re-encode, resize, exposure, contrast, saturation). */
function samePicture(bits: number, a: Pick<PhotoSignal, "fine">, b: Pick<PhotoSignal, "fine">): boolean {
  return bits <= ALIKE.samePicture.bits && fineDistance(a, b, ALIKE.samePicture.distance, false) <= ALIKE.samePicture.distance;
}

/**
 * The album rule for two photos, given how many hash bits differ and whether
 * they were taken near each other (`near`) or in one burst (`burst`).
 * `untimed`: lined up only because their times are unknown, so the costly
 * nudging is kept to the closest calls.
 */
function albumAlike(
  bits: number,
  a: Pick<PhotoSignal, "fine">,
  b: Pick<PhotoSignal, "fine">,
  near: boolean,
  burst: boolean,
  untimed = false,
): boolean {
  if (samePicture(bits, a, b)) return true;
  const bound = burst ? ALIKE.lookAlike * ALIKE.burstFactor : ALIKE.lookAlike;
  if (near) return fineDistance(a, b, bound, true, untimed ? RETRY_WITHIN : REFINE_WITHIN) <= bound;
  if (bits <= ALIKE.farBits) return fineDistance(a, b, bound, false) <= bound;
  return false;
}

/**
 * The loosest rule, for keeping a removed photo out: judged as loosely as a
 * burst, frame to frame whatever the times and lined up when the two are
 * near (crops and shifts keep their capture time). A copy of a photo someone
 * took out never comes back in its place.
 */
function looseAlike(a: Pick<PhotoSignal, "fine">, b: Pick<PhotoSignal, "fine">, near: boolean): boolean {
  const bound = ALIKE.lookAlike * ALIKE.burstFactor;
  return fineDistance(a, b, bound, near) <= bound;
}

function timeGap(a: number | null, b: number | null): number | null {
  return a === null || b === null || !Number.isFinite(a) || !Number.isFinite(b) ? null : Math.abs(a - b);
}

/**
 * Whether two photos are near-duplicates (see ALIKE). With both capture times
 * known, photos more than NEAR_WINDOW_MS apart are compared frame to frame
 * only; with either unknown they may be lined up. `strict`: exact copies only.
 */
export function looksAlike(
  a: Pick<PhotoSignal, "hash" | "fine" | "takenAt">,
  b: Pick<PhotoSignal, "hash" | "fine" | "takenAt">,
  opts: AlikeOptions = {},
): boolean {
  const bits = hamming(a.hash, b.hash);
  if (opts.strict) return samePicture(bits, a, b);
  const dt = opts.bursts === false ? null : timeGap(a.takenAt, b.takenAt);
  return albumAlike(bits, a, b, dt === null || dt <= NEAR_WINDOW_MS, dt !== null && dt <= BURST_WINDOW_MS);
}

// ---------------------------------------------------------------------------
// Capture times

/**
 * Which times to trust: EXIF capture times when at least half the album has
 * them (the rest are placed by pick order), otherwise the file dates for all
 * (consistent, if rough). Mixing the two would put a photo copied last week
 * hours away from the ones it was taken with. `exact` says whether bursts can
 * be told from the times.
 */
export function chooseTimes(
  exif: readonly (number | null)[],
  file: readonly (number | null)[],
): { times: (number | null)[]; exact: boolean } {
  const known = exif.filter((t) => t !== null && Number.isFinite(t)).length;
  if (exif.length > 0 && known * 2 >= exif.length) {
    return { times: exif.map((t) => (t !== null && Number.isFinite(t) ? t : null)), exact: true };
  }
  return { times: file.map((t) => (t !== null && Number.isFinite(t) && t > 0 ? t : null)), exact: false };
}

/** What analyze.ts reports for one photo (null: it could not be read). */
export interface LookLike {
  hash: string;
  fine: ArrayLike<number>;
  sharpness: number;
  exifTime: number | null;
  fileTime: number | null;
}

/**
 * The shortlist's input from the looks of a whole album (by album index),
 * unreadable photos left out, with the times chooseTimes trusts.
 */
export function signalsFromLooks(looks: readonly (LookLike | null)[]): { photos: PhotoSignal[]; exactTimes: boolean } {
  const read: { look: LookLike; index: number }[] = [];
  looks.forEach((look, index) => {
    if (look) read.push({ look, index });
  });
  const { times, exact } = chooseTimes(
    read.map((r) => r.look.exifTime),
    read.map((r) => r.look.fileTime),
  );
  const photos = read.map((r, k) => ({
    index: r.index,
    takenAt: times[k],
    sharpness: r.look.sharpness,
    hash: r.look.hash,
    fine: r.look.fine,
  }));
  return { photos, exactTimes: exact };
}

// ---------------------------------------------------------------------------
// The album

/** An album made ready for choosing: copies found, ordered in time, placed on a 0-to-1 line. */
export interface Album {
  photos: readonly PhotoSignal[];
  /** For each photo (by array position): the kept photo it is, or is a copy of (a group). */
  group: Int32Array;
  /** For each group: its kept photo, the sharpest of the copies first seen (array position). */
  best: number[];
  /** Array positions, earliest moment first (a group together, from its earliest photo). */
  order: number[];
  /** For each photo: where it sits in the day, 0 (first) to 1 (last). */
  place: Float64Array;
  /** For each photo: its sharpness rank, 0 (softest) to 1 (sharpest). */
  quality: Float64Array;
  /** Photos set aside as copies or near-duplicates of a kept photo. */
  duplicates: number;
  /** Array position by caller index. */
  at: Map<number, number>;
  /** Whether two photos (array positions) are compared lined up (see NEAR_WINDOW_MS, UNKNOWN_TIME_BITS). */
  near: (i: number, j: number) => boolean;
  /**
   * Whether two kept photos (array positions) with no capture time between
   * them are still copies of one picture, which the per-photo limit on lining
   * up (UNTIMED_LINE_UPS) can let through. The picker checks each new pick
   * against those already chosen, so the film never holds both.
   */
  sameUntimed: (i: number, j: number) => boolean;
}

function find(parent: Int32Array, x: number): number {
  while (parent[x] !== x) {
    parent[x] = parent[parent[x]];
    x = parent[x];
  }
  return x;
}

/**
 * Finds the copies and orders the album:
 * 1. Copies of the very same picture (samePicture) are bundled: re-saves and
 *    recolourings of one frame stay one picture however many there are. The
 *    rule is far tighter than any two moments, so this never joins moments.
 * 2. Going from the sharpest bundle down, a bundle is kept only if none of
 *    its photos looks like a photo of a bundle already kept (the album rule,
 *    pairwise: nothing chains through photos that were set aside). In
 *    `strict` mode step 2 is skipped.
 * Then the photos are ordered by capture time (a photo without one sits
 * after the one picked before it) and each group placed on a line from 0 to
 * 1: half by time, half by count, so a long quiet stretch gets some photos
 * but not all of them.
 */
export function prepareAlbum(photos: readonly PhotoSignal[], opts: AlikeOptions = {}): Album {
  const steps = prepareSteps(photos, opts);
  for (;;) {
    const step = steps.next();
    if (step.done) return step.value;
  }
}

/**
 * prepareAlbum a slice at a time: after each `sliceMs` of work it waits for
 * `pause` (a yield to the page), so even a long album never freezes typing.
 */
export async function prepareAlbumAsync(
  photos: readonly PhotoSignal[],
  opts: AlikeOptions,
  pause: () => Promise<void>,
  sliceMs = 12,
): Promise<Album> {
  const steps = prepareSteps(photos, opts);
  let started = Date.now();
  for (;;) {
    const step = steps.next();
    if (step.done) return step.value;
    if (Date.now() - started >= sliceMs) {
      await pause();
      started = Date.now();
    }
  }
}

/** The work of prepareAlbum, yielding now and then so a caller can pause between slices. */
function* prepareSteps(photos: readonly PhotoSignal[], opts: AlikeOptions): Generator<void, Album, void> {
  const n = photos.length;
  const hi = new Uint32Array(n);
  const lo = new Uint32Array(n);
  const at = new Map<number, number>();
  for (let i = 0; i < n; i++) {
    [hi[i], lo[i]] = hashHalves(photos[i].hash);
    at.set(photos[i].index, i);
  }
  const bits = (i: number, j: number) => popcount32(hi[i] ^ hi[j]) + popcount32(lo[i] ^ lo[j]);

  // Times: a photo with none takes the time of the nearest photo picked
  // before it (or after, at the start). Ties keep pick order.
  const byPick = Array.from({ length: n }, (_, i) => i).sort((a, b) => photos[a].index - photos[b].index);
  const time = new Float64Array(n);
  let last: number | null = null;
  for (const i of byPick) {
    const t = photos[i].takenAt;
    if (t !== null && Number.isFinite(t)) last = t;
    time[i] = last ?? NaN;
  }
  let next: number | null = null;
  for (let k = byPick.length - 1; k >= 0; k--) {
    const i = byPick[k];
    if (!Number.isNaN(time[i])) next = time[i];
    else time[i] = next ?? 0;
  }
  const pickRank = new Int32Array(n);
  byPick.forEach((i, r) => (pickRank[i] = r));
  const byTime = Array.from({ length: n }, (_, i) => i).sort((a, b) => time[a] - time[b] || pickRank[a] - pickRank[b]);

  // Sharpness as a rank, so one odd score cannot swamp the rest.
  const quality = new Float64Array(n);
  const bySharp = Array.from({ length: n }, (_, i) => i).sort(
    (a, b) => (photos[a].sharpness || 0) - (photos[b].sharpness || 0) || pickRank[b] - pickRank[a],
  );
  bySharp.forEach((i, r) => (quality[i] = n > 1 ? r / (n - 1) : 1));

  // 1. Bundles of the same picture. The hash gate keeps this to a handful of
  // fine comparisons even for a thousand photos.
  const parent = new Int32Array(n);
  for (let i = 0; i < n; i++) parent[i] = i;
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const b = bits(i, j);
      if (b > ALIKE.samePicture.bits || find(parent, i) === find(parent, j)) continue;
      if (samePicture(b, photos[i], photos[j])) parent[find(parent, i)] = find(parent, j);
    }
    if ((i & 31) === 31) yield;
  }
  const bundleOf = new Map<number, number[]>();
  for (const i of bySharp.slice().reverse()) {
    const root = find(parent, i);
    const list = bundleOf.get(root);
    if (list) list.push(i);
    else bundleOf.set(root, [i]);
  }
  // Sharpest bundle first (each list is sharpest first).
  const bundles = [...bundleOf.values()].sort((a, b) => quality[b[0]] - quality[a[0]]);

  // 2. Pairwise refusal against kept bundles only.
  const known = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const t = photos[i].takenAt;
    known[i] = opts.bursts !== false && t !== null && Number.isFinite(t) ? 1 : 0;
  }
  const near = (i: number, j: number) =>
    known[i] && known[j]
      ? Math.abs(time[i] - time[j]) <= NEAR_WINDOW_MS
      : worthLiningUp(photos[i], photos[j], bits(i, j));
  const timedNear = (i: number, j: number) => known[i] === 1 && known[j] === 1 && Math.abs(time[i] - time[j]) <= NEAR_WINDOW_MS;
  const inBurst = (i: number, j: number) => known[i] === 1 && known[j] === 1 && Math.abs(time[i] - time[j]) <= BURST_WINDOW_MS;
  /**
   * Whether bundle `u` looks like kept bundle `k`. Compared through their
   * sharpest photos first, as near and as much a burst as any of their
   * photos are; if those differ, the other pictures of the bundles get a
   * look (an edit can make the sharpest photo the odd one out). Pairs with
   * no capture time between them are only lined up when `lineUpUntimed`;
   * otherwise, if they would be worth it, the answer is a number (how close
   * their palettes are) so the caller can line up the most promising first.
   */
  const relate = (u: number[], k: number[], lineUpUntimed: boolean, ceiling = Infinity): boolean | number => {
    let b = 64;
    let isNear = false;
    let burst = false;
    let untimed = Infinity;
    for (let x = 0; x < u.length; x++) {
      const i = u[x];
      for (let y = 0; y < k.length; y++) {
        const j = k[y];
        const bij = popcount32(hi[i] ^ hi[j]) + popcount32(lo[i] ^ lo[j]);
        if (bij < b) b = bij;
        if (known[i] && known[j]) {
          const dt = Math.abs(time[i] - time[j]);
          if (dt <= NEAR_WINDOW_MS) isNear = true;
          if (dt <= BURST_WINDOW_MS) burst = true;
        } else {
          untimed = Math.min(untimed, liningUpKey(photos[i], photos[j], bij, ceiling));
        }
      }
    }
    const onlyUntimed = !isNear && lineUpUntimed && untimed < Infinity;
    if (onlyUntimed) isNear = true;
    const later = !lineUpUntimed && untimed < Infinity ? untimed : false;
    if (!isNear && b > ALIKE.farBits) return later; // the cheap way out for most pairs
    if (albumAlike(b, photos[u[0]], photos[k[0]], isNear, burst, onlyUntimed)) return true;
    for (const i of u) {
      for (const j of k) {
        if (i === u[0] && j === k[0]) continue;
        const bij = bits(i, j);
        const timed = timedNear(i, j);
        const nij = timed || (lineUpUntimed && near(i, j));
        if (!nij && bij > ALIKE.farBits) continue;
        if (albumAlike(bij, photos[i], photos[j], nij, inBurst(i, j), nij && !timed)) return true;
      }
    }
    return later;
  };
  const kept: number[][] = [];
  const keptOf = new Int32Array(n).fill(-1);
  const untimedLineUps = UNTIMED_LINE_UPS;
  const setAsideUntimed: number[][] = [];
  for (const bundle of bundles) {
    yield;
    let match = -1;
    if (!opts.strict) {
      // Everything cheap first: times close enough to line up, or the frames
      // as they are. Then, for photos with no time between them, the kept
      // ones most likely to be the same picture (coarseKey), lined up, at most
      // UNTIMED_LINE_UPS of them.
      const untimed: [number, number][] = [];
      const lowest = new Lowest(untimedLineUps);
      for (let k = 0; k < kept.length && match < 0; k++) {
        const ceiling = lowest.ceiling;
        const r = relate(bundle, kept[k], false, ceiling);
        if (r === true) match = k;
        else if (typeof r === "number" && r < ceiling) {
          untimed.push([r, k]);
          lowest.add(r);
        }
      }
      if (match < 0 && untimed.length) {
        untimed.sort((x, y) => x[0] - y[0]);
        for (let t = 0; t < Math.min(untimedLineUps, untimed.length) && match < 0; t++) {
          if (relate(bundle, kept[untimed[t][1]], true) === true) {
            match = untimed[t][1];
            setAsideUntimed.push(bundle);
          }
        }
      }
    }
    if (match < 0) {
      match = kept.length;
      kept.push(bundle);
    }
    for (const i of bundle) keptOf[i] = match;
  }

  // A photo set aside by lining up across unknown times may have been taken
  // before its own original was placed (the sharper one goes first), and so
  // joined another moment it nearly matched. Now that every photo is placed,
  // it moves to the group of the photo it matches most closely, kept or set
  // aside itself. Only set-aside photos move, so this never changes which
  // photos are kept.
  const inBundle = new Uint8Array(n);
  for (const bundle of setAsideUntimed) {
    yield;
    for (const i of bundle) inBundle[i] = 1;
    const ranked: [number, number][] = [];
    const lowest = new Lowest(untimedLineUps);
    for (let j = 0; j < n; j++) {
      if (inBundle[j]) continue;
      const ceiling = lowest.ceiling;
      const key = liningUpKey(photos[bundle[0]], photos[j], bits(bundle[0], j), ceiling);
      if (key < ceiling) {
        ranked.push([key, j]);
        lowest.add(key);
      }
    }
    for (const i of bundle) inBundle[i] = 0;
    ranked.sort((x, y) => x[0] - y[0]);
    let best = keptOf[bundle[0]];
    let bestDistance = Infinity;
    for (const [, j] of ranked.slice(0, untimedLineUps)) {
      const d = fineDistance(photos[bundle[0]], photos[j], ALIKE.lookAlike, true, RETRY_WITHIN);
      if (d < bestDistance) {
        bestDistance = d;
        best = keptOf[j];
      }
    }
    if (bestDistance <= ALIKE.lookAlike) for (const i of bundle) keptOf[i] = best;
  }

  // Groups numbered by their earliest photo, which is when the moment
  // happened (a copy edited hours later still belongs back then).
  const groupOf = new Map<number, number>();
  const group = new Int32Array(n);
  const best: number[] = [];
  const when: number[] = [];
  for (const i of byTime) {
    let g = groupOf.get(keptOf[i]);
    if (g === undefined) {
      g = best.length;
      groupOf.set(keptOf[i], g);
      best.push(kept[keptOf[i]][0]);
      when.push(time[i]);
    }
    group[i] = g;
  }
  const order = byTime.slice().sort((a, b) => group[a] - group[b] || time[a] - time[b] || pickRank[a] - pickRank[b]);

  // Place on the day: half by time, half by count of distinct moments.
  const moments = best.length;
  const t0 = moments ? when[0] : 0;
  const span = moments ? when[moments - 1] - t0 : 0;
  const groupPlace = when.map((t, g) => {
    const byCount = moments > 1 ? g / (moments - 1) : 0.5;
    return span > 0 ? 0.5 * ((t - t0) / span) + 0.5 * byCount : byCount;
  });
  const place = new Float64Array(n);
  for (let i = 0; i < n; i++) place[i] = groupPlace[group[i]];

  const sameUntimed = (i: number, j: number): boolean => {
    if (known[i] && known[j]) return false; // timed pairs were all compared already
    const key = liningUpKey(photos[i], photos[j], bits(i, j));
    if (key > COPY_KEY_MAX) return false;
    return albumAlike(bits(i, j), photos[i], photos[j], true, false, true);
  };

  return { photos, group, best, order, place, quality, duplicates: n - moments, at, near, sameUntimed };
}

/**
 * Up to `target` photos for the film: everything in `keep` (caller indices),
 * then the best of the rest: one kept photo per group, never from a group
 * already in, and never one that looks like anything in `exclude` (photos
 * taken out), judged by the loosest rule. Each pick goes where the chosen
 * photos leave the widest gap in the day, a softer photo needing a wider gap
 * to win over a sharper one. The first picks open and close the day.
 * With `alikeOk` any photo may be picked (to reach a minimum). Returns
 * caller indices, earliest first.
 */
export function pickFrom(
  album: Album,
  keep: readonly number[],
  exclude: readonly number[],
  target: number,
  opts: { alikeOk?: boolean } = {},
): number[] {
  const { photos, group, best, place, quality, order, at } = album;
  const alikeOk = !!opts.alikeOk;
  const blocked = new Uint8Array(best.length);
  const out = new Uint8Array(photos.length);
  const cleared = new Uint8Array(photos.length);
  const chosen = new Set<number>();
  const removed: number[] = [];
  for (const idx of exclude) {
    const i = at.get(idx);
    if (i === undefined) continue;
    out[i] = 1;
    removed.push(i);
    if (!alikeOk) blocked[group[i]] = 1;
  }
  const minGap = new Float64Array(photos.length).fill(Infinity);
  const take = (i: number) => {
    chosen.add(i);
    out[i] = 1;
    if (!alikeOk) blocked[group[i]] = 1;
    for (let j = 0; j < photos.length; j++) minGap[j] = Math.min(minGap[j], Math.abs(place[j] - place[i]));
  };
  for (const idx of keep) {
    const i = at.get(idx);
    if (i !== undefined && !chosen.has(i)) take(i);
  }
  /** Not like anything taken out (checked once, when it is first in the running). */
  const fine = (i: number): boolean => {
    if (cleared[i]) return true;
    if (removed.some((r) => looseAlike(photos[i], photos[r], album.near(i, r)))) {
      out[i] = 1;
      if (!alikeOk) blocked[group[i]] = 1;
      return false;
    }
    cleared[i] = 1;
    return true;
  };
  const open = () => (alikeOk ? order.filter((i) => !out[i]) : best.filter((i) => !blocked[group[i]] && !out[i]));
  /** The open photo scoring highest that is unlike everything taken out. */
  const nextBest = (score: (i: number) => number): number => {
    for (;;) {
      let pick = -1;
      let top = -Infinity;
      for (const i of open()) {
        const s = score(i);
        if (s > top || (s === top && pick >= 0 && quality[i] > quality[pick])) {
          top = s;
          pick = i;
        }
      }
      if (pick < 0 || top === -Infinity) return -1;
      if (!fine(pick)) continue;
      // Never two copies of one picture in the film, even ones that ended up
      // kept apart (see Album.sameUntimed).
      let copy = false;
      if (!alikeOk) for (const c of chosen) if (album.sameUntimed(pick, c)) copy = true;
      if (!copy) return pick;
      out[pick] = 1;
    }
  };

  const want = Math.max(0, Math.floor(target));
  if (chosen.size === 0 && want > 0) {
    const edge = 1 / Math.max(2, want);
    for (const side of [0, 1]) {
      if (chosen.size >= want) break;
      const pick = nextBest((i) => ((side === 0 ? place[i] <= edge : place[i] >= 1 - edge) ? quality[i] : -Infinity));
      if (pick >= 0) take(pick);
    }
  }
  while (chosen.size < want) {
    const pick = nextBest((i) => (chosen.size === 0 ? 1 : minGap[i]) * (0.6 + 0.4 * quality[i]));
    if (pick < 0) break;
    take(pick);
  }
  return order.filter((i) => chosen.has(i)).map((i) => photos[i].index);
}

export interface Shortlist {
  /** Caller indices, earliest first. */
  chosen: number[];
  /** Photos set aside as copies or near-duplicates of a kept photo. */
  duplicatesDropped: number;
  /** Photos looked at. */
  considered: number;
}

/**
 * The film's photos from an album: one per moment (the sharpest of its
 * copies), up to `max`, spread evenly over the day.
 */
export function shortlist(photos: readonly PhotoSignal[], max = 40, opts: AlikeOptions = {}): Shortlist {
  const album = prepareAlbum(photos, opts);
  return { chosen: pickFrom(album, [], [], max), duplicatesDropped: album.duplicates, considered: photos.length };
}

// ---------------------------------------------------------------------------
// The form's plan

/**
 * - "album": more than `max` photos, so Stitch chose up to `max`.
 * - "picks": `max` or fewer, all kept but exact copies.
 * - "as-picked": too few would be left without the copies, so the picks
 *   stand as they are.
 */
export type PlanMode = "album" | "picks" | "as-picked";

export interface AlbumPlan {
  album: Album;
  mode: PlanMode;
  /** Caller indices, earliest first. */
  chosen: number[];
  /** Copies and near-duplicates set aside (never more than the photos left out). */
  setAside: number;
}

/**
 * Which photos go into the film, never any in `removed` or like them:
 * - more than `max`: the shortlist (copies and near-identical frames set
 *   aside; bursts judged loosely when the times are capture times); topped
 *   up to `min` with look-alikes when the album has fewer distinct moments;
 * - `max` or fewer: only exact copies go, and only if `min` remain.
 */
export function planAlbum(photos: readonly PhotoSignal[], opts: PlanOptions): AlbumPlan {
  const { mode, albumOpts } = planShape(photos, opts);
  return finishPlan(prepareAlbum(photos, albumOpts), mode, opts);
}

/** planAlbum, pausing for the page between slices of work (see prepareAlbumAsync). */
export async function planAlbumAsync(photos: readonly PhotoSignal[], opts: PlanOptions, pause: () => Promise<void>): Promise<AlbumPlan> {
  const { mode, albumOpts } = planShape(photos, opts);
  return finishPlan(await prepareAlbumAsync(photos, albumOpts, pause), mode, opts);
}

export interface PlanOptions {
  exactTimes: boolean;
  removed?: readonly number[];
  max?: number;
  min?: number;
}

function planShape(photos: readonly PhotoSignal[], opts: PlanOptions): { mode: PlanMode; albumOpts: AlikeOptions } {
  const isOut = new Set(opts.removed ?? []);
  const left = photos.filter((p) => !isOut.has(p.index)).length;
  return left > (opts.max ?? 40)
    ? { mode: "album", albumOpts: { bursts: opts.exactTimes } }
    : { mode: "picks", albumOpts: { strict: true } };
}

function finishPlan(album: Album, mode: PlanMode, opts: PlanOptions): AlbumPlan {
  return { album, ...chooseAgain(album, mode, opts.removed ?? [], opts.max ?? 40, opts.min ?? 8) };
}

/** A fresh choice from an album already prepared (the "Choose again" action). */
export function chooseAgain(
  album: Album,
  mode: PlanMode,
  removed: readonly number[],
  max = 40,
  min = 8,
): { mode: PlanMode; chosen: number[]; setAside: number } {
  const isOut = new Set(removed);
  const left = album.photos.filter((p) => !isOut.has(p.index)).length;
  let chosen = pickFrom(album, [], removed, max);
  if (mode === "album") {
    if (chosen.length < min) chosen = pickFrom(album, chosen, removed, Math.min(min, left), { alikeOk: true });
    return { mode, chosen, setAside: Math.max(0, Math.min(album.duplicates, left - chosen.length)) };
  }
  if (chosen.length < Math.min(min, left)) {
    const all = album.order.map((i) => album.photos[i].index).filter((idx) => !isOut.has(idx));
    return { mode: "as-picked", chosen: all.slice(0, max), setAside: 0 };
  }
  return { mode: "picks", chosen, setAside: left - chosen.length };
}

/**
 * After the person takes `taken` out of `current`: the photos that stay
 * plus, while any remain, the next best from the album that is unlike all
 * of them and unlike everything taken out so far (`removed`, which includes
 * `taken`). Earliest first.
 */
export function replaceTaken(album: Album, current: readonly number[], removed: readonly number[], taken: number): number[] {
  const stay = current.filter((idx) => idx !== taken);
  return pickFrom(album, stay, removed, current.length);
}
