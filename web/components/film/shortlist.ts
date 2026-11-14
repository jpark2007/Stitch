// Choosing a film's photos from a whole album, in the browser. Pure: no DOM,
// no I/O. analyze.ts turns each File into a PhotoSignal; this file groups the
// look-alikes, orders the album by capture time and picks up to 40 photos
// that cover the day evenly, never two that look alike, sharper ones first.
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
  /** 64-bit difference hash: a bigint, or 16 hex digits. */
  hash: bigint | string;
  /** Colour layout: 8x8 cells of mean RGB, as [r, g, b, r, g, b, …] (any length, as long as both match). */
  layout: readonly number[];
}

/** Two captures this close together are a burst, and look-alikes are judged more loosely. */
export const BURST_WINDOW_MS = 5000;

/**
 * The look-alike thresholds. `hash` is the dHash Hamming distance (0 to 64
 * bits); `layout` the mean absolute difference of the colour layouts once
 * each channel's overall level is taken out (0 to 255), so a brighter or
 * warmer copy of the same picture still matches.
 *
 * Measured on a synthetic album (150 scenes with re-encodes, crops, brightness
 * edits and shifted or blurred burst frames): copies of one scene came within
 * 22 bits and 20 of layout, while photos of different scenes were never
 * closer than 25 of layout at under 14 bits.
 */
export const ALIKE = {
  /** The same picture saved again: re-encodes, resizes, exposure or colour edits. Always applies. */
  samePicture: { hash: 10, layout: 12 },
  /** A small crop, shift or tilt of the same moment, whenever it was taken. */
  sameMoment: { hash: 16, layout: 18 },
  /** A blurred or noisy frame: weak edges scramble the hash, but the colours stay put. */
  softFrame: { hash: 22, layout: 8 },
  /** Taken within BURST_WINDOW_MS of each other: the camera moved a little between frames. */
  burst: { hash: 22, layout: 22 },
} as const;

export interface AlikeOptions {
  /** Only the samePicture rule: for a handful of photos someone chose one by one. */
  strict?: boolean;
  /** Apply the burst rule (default true). Off when the times are file dates, not capture times. */
  bursts?: boolean;
}

// ---------------------------------------------------------------------------
// Distances

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

/** The layout with each channel's mean taken out (RGB triples when the length allows, else one channel). */
export function centredLayout(layout: readonly number[]): Float64Array {
  const n = layout.length;
  const channels = n % 3 === 0 ? 3 : 1;
  const mean = [0, 0, 0];
  for (let i = 0; i < n; i++) mean[i % channels] += layout[i];
  for (let c = 0; c < channels; c++) mean[c] /= n / channels || 1;
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) out[i] = layout[i] - mean[i % channels];
  return out;
}

function centredGap(a: Float64Array, b: Float64Array): number {
  if (a.length !== b.length || a.length === 0) return Infinity;
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += Math.abs(a[i] - b[i]);
  return sum / a.length;
}

/** Mean absolute difference of two colour layouts, each channel's overall level taken out. */
export function layoutDistance(a: readonly number[], b: readonly number[]): number {
  return centredGap(centredLayout(a), centredLayout(b));
}

function alikeBy(bits: number, layout: number, dt: number | null, opts: AlikeOptions): boolean {
  const within = (t: { hash: number; layout: number }) => bits <= t.hash && layout <= t.layout;
  if (within(ALIKE.samePicture)) return true;
  if (opts.strict) return false;
  if (opts.bursts !== false && dt !== null && dt <= BURST_WINDOW_MS && within(ALIKE.burst)) return true;
  return within(ALIKE.sameMoment) || within(ALIKE.softFrame);
}

function timeGap(a: number | null, b: number | null): number | null {
  return a === null || b === null || !Number.isFinite(a) || !Number.isFinite(b) ? null : Math.abs(a - b);
}

/**
 * Whether two photos are near-duplicates (see ALIKE). Time only ever loosens
 * the rule (a burst); without times, or far apart, near-identical frames are
 * still caught.
 */
export function looksAlike(
  a: Pick<PhotoSignal, "hash" | "layout" | "takenAt">,
  b: Pick<PhotoSignal, "hash" | "layout" | "takenAt">,
  opts: AlikeOptions = {},
): boolean {
  return alikeBy(hamming(a.hash, b.hash), layoutDistance(a.layout, b.layout), timeGap(a.takenAt, b.takenAt), opts);
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
  layout: readonly number[];
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
    layout: r.look.layout,
  }));
  return { photos, exactTimes: exact };
}

// ---------------------------------------------------------------------------
// The album

/** An album made ready for choosing: look-alikes grouped, ordered in time, placed on a 0-to-1 line. */
export interface Album {
  photos: readonly PhotoSignal[];
  /** For each photo (by array position): its look-alike group. */
  group: Int32Array;
  /** For each group: the sharpest photo in it (array position). */
  best: number[];
  /** Array positions, earliest moment first (a group together, from its earliest photo). */
  order: number[];
  /** For each photo: where it sits in the day, 0 (first) to 1 (last). */
  place: Float64Array;
  /** For each photo: its sharpness rank, 0 (softest) to 1 (sharpest). */
  quality: Float64Array;
  /** Photos set aside as near-duplicates of a sharper one. */
  duplicates: number;
  /** Array position by caller index. */
  at: Map<number, number>;
}

function find(parent: Int32Array, x: number): number {
  while (parent[x] !== x) {
    parent[x] = parent[parent[x]];
    x = parent[x];
  }
  return x;
}

/**
 * Groups look-alikes (a photo joins every group it looks like, so a burst
 * that drifts stays one group), orders the photos by capture time (a photo
 * without one sits after the one picked before it) and places each on a line
 * from 0 to 1: half by time, half by count, so a long quiet stretch gets some
 * photos but not all of them.
 */
export function prepareAlbum(photos: readonly PhotoSignal[], opts: AlikeOptions = {}): Album {
  const n = photos.length;
  const hi = new Uint32Array(n);
  const lo = new Uint32Array(n);
  const layouts: Float64Array[] = [];
  const at = new Map<number, number>();
  for (let i = 0; i < n; i++) {
    [hi[i], lo[i]] = hashHalves(photos[i].hash);
    layouts.push(centredLayout(photos[i].layout));
    at.set(photos[i].index, i);
  }

  // Order: by capture time; a photo with none takes the time of the nearest
  // photo picked before it (or after, at the start). Ties keep pick order.
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

  // Look-alike groups. The time-free rules have to compare every pair, which
  // is cheap: a 1000-photo album is half a million XOR-and-count steps, and
  // the layout is only measured for the few pairs whose hashes are close.
  const parent = new Int32Array(n);
  for (let i = 0; i < n; i++) parent[i] = i;
  const loosest = opts.strict ? ALIKE.samePicture.hash : Math.max(ALIKE.sameMoment.hash, ALIKE.softFrame.hash, ALIKE.burst.hash);
  for (let x = 0; x < n; x++) {
    const i = byTime[x];
    for (let y = x + 1; y < n; y++) {
      const j = byTime[y];
      const bits = popcount32(hi[i] ^ hi[j]) + popcount32(lo[i] ^ lo[j]);
      if (bits > loosest) continue;
      if (find(parent, i) === find(parent, j)) continue;
      const dt = timeGap(photos[i].takenAt, photos[j].takenAt);
      if (alikeBy(bits, centredGap(layouts[i], layouts[j]), dt, opts)) parent[find(parent, i)] = find(parent, j);
    }
  }

  // Sharpness as a rank, so one odd score cannot swamp the rest.
  const quality = new Float64Array(n);
  const bySharp = Array.from({ length: n }, (_, i) => i).sort(
    (a, b) => (photos[a].sharpness || 0) - (photos[b].sharpness || 0) || pickRank[b] - pickRank[a],
  );
  bySharp.forEach((i, r) => (quality[i] = n > 1 ? r / (n - 1) : 1));

  // Groups are numbered by their earliest photo, which is when the moment
  // happened (a copy edited hours later still belongs back then).
  const group = new Int32Array(n);
  const groupOf = new Map<number, number>();
  const best: number[] = [];
  const when: number[] = [];
  for (const i of byTime) {
    const root = find(parent, i);
    let g = groupOf.get(root);
    if (g === undefined) {
      g = best.length;
      groupOf.set(root, g);
      best.push(i);
      when.push(time[i]);
    } else if (quality[i] > quality[best[g]]) {
      best[g] = i;
    }
    group[i] = g;
  }
  const order = byTime.slice().sort((a, b) => group[a] - group[b] || time[a] - time[b] || pickRank[a] - pickRank[b]);

  // Place on the day: half by time, half by count of distinct moments.
  // Every photo of a group sits where the moment does.
  const moments = best.length;
  const t0 = moments ? when[0] : 0;
  const span = moments ? when[moments - 1] - t0 : 0;
  const groupPlace = when.map((t, g) => {
    const byCount = moments > 1 ? g / (moments - 1) : 0.5;
    return span > 0 ? 0.5 * ((t - t0) / span) + 0.5 * byCount : byCount;
  });
  const place = new Float64Array(n);
  for (let i = 0; i < n; i++) place[i] = groupPlace[group[i]];

  return { photos, group, best, order, place, quality, duplicates: n - moments, at };
}

/**
 * Up to `target` photos for the film: everything in `keep` (caller indices),
 * then the best of the rest, never from a look-alike group already in or in
 * `exclude` (photos taken out, whose look-alikes stay out with them). Each
 * pick goes where the chosen photos leave the widest gap in the day, a softer
 * photo needing a wider gap to win over a sharper one. The first picks open
 * and close the day. Returns caller indices, earliest first.
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
  const chosen = new Set<number>();
  for (const idx of exclude) {
    const i = at.get(idx);
    if (i === undefined) continue;
    out[i] = 1;
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

  const want = Math.max(0, Math.floor(target));
  // Only the best photo of each open group is ever a candidate (with
  // `alikeOk`, any photo not yet in or out).
  const open = () => (alikeOk ? order.filter((i) => !out[i]) : best.filter((i) => !blocked[group[i]]));
  if (chosen.size === 0 && want > 0) {
    const edge = 1 / Math.max(2, want);
    for (const side of [0, 1]) {
      if (chosen.size >= want) break;
      let pick = -1;
      for (const i of open()) {
        const near = side === 0 ? place[i] <= edge : place[i] >= 1 - edge;
        if (near && (pick < 0 || quality[i] > quality[pick])) pick = i;
      }
      if (pick >= 0) take(pick);
    }
  }
  while (chosen.size < want) {
    let pick = -1;
    let score = -1;
    for (const i of open()) {
      const gap = chosen.size === 0 ? 1 : minGap[i];
      const s = gap * (0.6 + 0.4 * quality[i]);
      if (s > score || (s === score && quality[i] > quality[pick])) {
        score = s;
        pick = i;
      }
    }
    if (pick < 0) break;
    take(pick);
  }
  return order.filter((i) => chosen.has(i)).map((i) => photos[i].index);
}

export interface Shortlist {
  /** Caller indices, earliest first. */
  chosen: number[];
  /** Photos set aside as near-duplicates of a sharper one. */
  duplicatesDropped: number;
  /** Photos looked at. */
  considered: number;
}

/**
 * The film's photos from an album: one per look-alike group (the sharpest),
 * up to `max`, spread evenly over the day.
 */
export function shortlist(photos: readonly PhotoSignal[], max = 40, opts: AlikeOptions = {}): Shortlist {
  const album = prepareAlbum(photos, opts);
  return { chosen: pickFrom(album, [], [], max), duplicatesDropped: album.duplicates, considered: photos.length };
}

// ---------------------------------------------------------------------------
// The form's plan

/**
 * - "album": more than `max` photos, so Stitch chose up to `max`.
 * - "picks": `max` or fewer, all kept but exact near-duplicates.
 * - "as-picked": too few would be left without the near-duplicates, so the
 *   picks stand as they are.
 */
export type PlanMode = "album" | "picks" | "as-picked";

export interface AlbumPlan {
  album: Album;
  mode: PlanMode;
  /** Caller indices, earliest first. */
  chosen: number[];
  /** Near-duplicates set aside (never more than the photos left out). */
  setAside: number;
}

/**
 * Which photos go into the film, never any in `removed` or like them:
 * - more than `max`: the shortlist (look-alikes judged loosely, bursts too
 *   when the times are capture times); topped up to `min` with look-alikes
 *   when the album has fewer distinct moments than that;
 * - `max` or fewer: only exact near-duplicates go, and only if `min` remain.
 */
export function planAlbum(
  photos: readonly PhotoSignal[],
  opts: { exactTimes: boolean; removed?: readonly number[]; max?: number; min?: number },
): AlbumPlan {
  const max = opts.max ?? 40;
  const min = opts.min ?? 8;
  const removed = opts.removed ?? [];
  const isOut = new Set(removed);
  const left = photos.filter((p) => !isOut.has(p.index)).length;
  if (left > max) {
    const album = prepareAlbum(photos, { bursts: opts.exactTimes });
    return { album, ...chooseAgain(album, "album", removed, max, min) };
  }
  const album = prepareAlbum(photos, { strict: true });
  return { album, ...chooseAgain(album, "picks", removed, max, min) };
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
    return { mode, chosen, setAside: Math.min(album.duplicates, left - chosen.length) };
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
