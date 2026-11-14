/**
 * Procedural paper toolkit for the 3D book and stage.
 *
 * Everything here is generated in code on a <canvas> (no image assets) and
 * cached, so every page, cutout and card in the scene shares a handful of GPU
 * textures. Browser-only: call these from client components, inside effects,
 * `useMemo` within an r3f <Canvas>, or event handlers, never during SSR.
 *
 * Textures
 *   getPaperTextures(kind?, repeat?)  -> { map, normalMap, roughnessMap }
 *       kind "paper" is fine fibrous sheet, "card" is coarser board with flecks.
 *       The colour map is near-white grain, so one texture serves every colour:
 *       the material's `color` tints it.
 *   getWoodTextures(repeat?)          -> { map, normalMap, roughnessMap }
 *       Warm planked table top, already coloured (use material colour white).
 *   getDeckledMask(options)           -> { alphaMap, rimMap }
 *       Deckled (hand-made sheet) or torn edge mask for a cutout. `alphaMap` is
 *       white inside, black outside. `rimMap` is a thin band just inside the
 *       edge, where torn paper shows its lighter fibrous core.
 *
 * Materials (each call returns a NEW MeshStandardMaterial that shares textures)
 *   createPaperMaterial({ color, roughness?, grain?, kind?, repeat?, side? })
 *   createCardMaterial({ color, ... })        thicker, rougher card stock
 *   createCardEdgeMaterial({ color })          the cut edge of card (darker)
 *   createCardBoxMaterials({ color })          6-array for a flat BoxGeometry
 *                                              (+y/-y are faces, rest edges)
 *   createCutoutMaterial({ color, mask?, ... }) pop-up cutout with a deckled
 *                                              alpha edge; casts clean shadows
 *   createWoodMaterial({ repeat? })            the table
 *
 * Lifetime
 *   Materials are yours: dispose them when done. r3f does not dispose a
 *   material passed as a `material={...}` prop, so prefer the hooks in
 *   ./usePaper, which do. Textures are shared and cached: never
 *   dispose them yourself. `disposePaperResources()` frees every cached texture
 *   (only on full teardown, e.g. tests).
 *
 * Performance: generation happens once per (kind, repeat) or mask key and takes
 * a few ms to ~40 ms; nothing here allocates per frame.
 */

import {
  CanvasTexture,
  ClampToEdgeWrapping,
  Color,
  DoubleSide,
  FrontSide,
  LinearMipmapLinearFilter,
  MeshStandardMaterial,
  NoColorSpace,
  RepeatWrapping,
  SRGBColorSpace,
  Vector2,
  type ColorRepresentation,
  type Side,
  type Texture,
} from "three";

/* ------------------------------------------------------------------------ */
/* Public types                                                              */
/* ------------------------------------------------------------------------ */

export type PaperKind = "paper" | "card";

export interface PaperTextureSet {
  /** sRGB grain colour map (near white; tint with material.color). */
  map: Texture;
  /** Tangent-space normal map of fibres and tooth. */
  normalMap: Texture;
  /** Roughness map (green channel), multiplied by material.roughness. */
  roughnessMap: Texture;
}

export interface PaperMaterialOptions {
  /** Base paper colour. Default: warm cream. */
  color?: ColorRepresentation;
  /** 0..1, default 0.92 (paper is matte). */
  roughness?: number;
  /** Strength of the grain relief, 0..2. Default 1 (paper) / 1.2 (card). */
  grain?: number;
  /** Texture flavour. Default "paper". */
  kind?: PaperKind;
  /** How many times the grain tiles across the UVs. Default 1. */
  repeat?: number | [number, number];
  /** Default FrontSide (DoubleSide for cutouts). */
  side?: Side;
}

export type EdgeStyle = "deckled" | "torn" | "clean";

export interface DeckledMaskOptions {
  /** Width / height of the cutout. Default 1. */
  aspect?: number;
  /** Edge style. Default "deckled". */
  style?: EdgeStyle;
  /** Edge roughness multiplier, 0..2. Default 1. */
  amount?: number;
  /** Different seeds give different edges. Default 1. */
  seed?: number;
  /** Long-side resolution in px. Default 512. */
  resolution?: number;
  /** Corner rounding as a fraction of the short side. Default 0.015. */
  cornerRadius?: number;
}

export interface DeckledMask {
  alphaMap: Texture;
  rimMap: Texture;
}

export interface CutoutMaterialOptions extends PaperMaterialOptions {
  /** Mask from getDeckledMask, or options to build one. Default deckled 1:1. */
  mask?: DeckledMask | DeckledMaskOptions;
  /** How much the torn rim lightens, 0..1. Default 0.5 for torn, 0.2 otherwise. */
  rim?: number;
}

/* ------------------------------------------------------------------------ */
/* Seeded noise (tileable)                                                   */
/* ------------------------------------------------------------------------ */

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const smooth = (t: number) => t * t * (3 - 2 * t);

/** A periodic 2D value-noise lattice of px × py cells. */
function lattice(px: number, py: number, rand: () => number): Float32Array {
  const l = new Float32Array(px * py);
  for (let i = 0; i < l.length; i++) l[i] = rand();
  return l;
}

/** Sample a periodic lattice at (u, v) in [0,1) (wraps), bilinear + smoothstep. */
function sampleLattice(l: Float32Array, px: number, py: number, u: number, v: number): number {
  const x = u * px;
  const y = v * py;
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const fx = smooth(x - x0);
  const fy = smooth(y - y0);
  const xa = ((x0 % px) + px) % px;
  const ya = ((y0 % py) + py) % py;
  const xb = (xa + 1) % px;
  const yb = (ya + 1) % py;
  const a = l[ya * px + xa];
  const b = l[ya * px + xb];
  const c = l[yb * px + xa];
  const d = l[yb * px + xb];
  return a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy;
}

/** Tileable fbm field (size w × h) in roughly [0,1]. */
function fbmField(
  w: number,
  h: number,
  basePx: number,
  basePy: number,
  octaves: number,
  rand: () => number,
  gain = 0.5,
): Float32Array {
  const out = new Float32Array(w * h);
  let amp = 1;
  let norm = 0;
  for (let o = 0; o < octaves; o++) {
    const px = basePx << o;
    const py = basePy << o;
    const l = lattice(px, py, rand);
    for (let y = 0; y < h; y++) {
      const v = y / h;
      const row = y * w;
      for (let x = 0; x < w; x++) out[row + x] += amp * sampleLattice(l, px, py, x / w, v);
    }
    norm += amp;
    amp *= gain;
  }
  for (let i = 0; i < out.length; i++) out[i] /= norm;
  return out;
}

/** 1D periodic fbm, n samples over one period, roughly [-1, 1]. */
function fbm1D(n: number, basePeriod: number, octaves: number, rand: () => number, gain = 0.55): Float32Array {
  const out = new Float32Array(n);
  let amp = 1;
  let norm = 0;
  for (let o = 0; o < octaves; o++) {
    const p = basePeriod << o;
    const pts = new Float32Array(p);
    for (let i = 0; i < p; i++) pts[i] = rand() * 2 - 1;
    for (let i = 0; i < n; i++) {
      const x = (i / n) * p;
      const i0 = Math.floor(x);
      const f = smooth(x - i0);
      const a = pts[i0 % p];
      const b = pts[(i0 + 1) % p];
      out[i] += amp * (a + (b - a) * f);
    }
    norm += amp;
    amp *= gain;
  }
  for (let i = 0; i < n; i++) out[i] /= norm;
  return out;
}

/* ------------------------------------------------------------------------ */
/* Canvas helpers                                                            */
/* ------------------------------------------------------------------------ */

function assertBrowser(fn: string) {
  if (typeof document === "undefined") {
    throw new Error(`paper/materials: ${fn}() needs a browser (call it on the client).`);
  }
}

function makeCanvas(w: number, h: number) {
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("paper/materials: 2D canvas unavailable");
  return { canvas, ctx };
}

function toTexture(canvas: HTMLCanvasElement, srgb: boolean, wrap: boolean): CanvasTexture {
  const t = new CanvasTexture(canvas);
  t.colorSpace = srgb ? SRGBColorSpace : NoColorSpace;
  t.wrapS = t.wrapT = wrap ? RepeatWrapping : ClampToEdgeWrapping;
  t.minFilter = LinearMipmapLinearFilter;
  t.anisotropy = 8; // three clamps to the GPU's max
  t.generateMipmaps = true;
  t.needsUpdate = true;
  return t;
}

/** Write a height field into a tangent-space normal map canvas (wrapping). */
function normalCanvasFromHeight(h: Float32Array, w: number, hgt: number, strength: number) {
  const { canvas, ctx } = makeCanvas(w, hgt);
  const img = ctx.createImageData(w, hgt);
  const d = img.data;
  for (let y = 0; y < hgt; y++) {
    const yu = ((y - 1 + hgt) % hgt) * w;
    const yd = ((y + 1) % hgt) * w;
    const row = y * w;
    for (let x = 0; x < w; x++) {
      const xl = (x - 1 + w) % w;
      const xr = (x + 1) % w;
      const dx = (h[row + xl] - h[row + xr]) * strength;
      const dy = (h[yd + x] - h[yu + x]) * strength;
      const inv = 1 / Math.sqrt(dx * dx + dy * dy + 1);
      const i = (row + x) * 4;
      d[i] = (dx * inv * 0.5 + 0.5) * 255;
      d[i + 1] = (dy * inv * 0.5 + 0.5) * 255;
      d[i + 2] = (inv * 0.5 + 0.5) * 255;
      d[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return canvas;
}

/** Draw short curved fibres onto a canvas, duplicated across the wrap seams. */
function drawFibres(
  ctx: CanvasRenderingContext2D,
  size: number,
  count: number,
  rand: () => number,
  len: [number, number],
  width: [number, number],
  alpha: [number, number],
  dark: number,
) {
  ctx.lineCap = "round";
  for (let i = 0; i < count; i++) {
    const x = rand() * size;
    const y = rand() * size;
    const a = rand() * Math.PI * 2;
    const l = len[0] + rand() * (len[1] - len[0]);
    const bend = (rand() - 0.5) * l * 0.8;
    const ex = x + Math.cos(a) * l;
    const ey = y + Math.sin(a) * l;
    const cx = (x + ex) / 2 - Math.sin(a) * bend;
    const cy = (y + ey) / 2 + Math.cos(a) * bend;
    const isDark = rand() < dark;
    const al = alpha[0] + rand() * (alpha[1] - alpha[0]);
    ctx.strokeStyle = isDark ? `rgba(0,0,0,${al})` : `rgba(255,255,255,${al})`;
    ctx.lineWidth = width[0] + rand() * (width[1] - width[0]);
    for (let ox = -1; ox <= 1; ox++) {
      for (let oy = -1; oy <= 1; oy++) {
        const sx = ox * size;
        const sy = oy * size;
        // Only redraw copies that can touch the tile.
        if (x + sx > size + l || x + sx < -l || y + sy > size + l || y + sy < -l) continue;
        ctx.beginPath();
        ctx.moveTo(x + sx, y + sy);
        ctx.quadraticCurveTo(cx + sx, cy + sy, ex + sx, ey + sy);
        ctx.stroke();
      }
    }
  }
}

/* ------------------------------------------------------------------------ */
/* Paper / card textures                                                     */
/* ------------------------------------------------------------------------ */

interface PaperRecipe {
  size: number;
  seed: number;
  cloud: number; // formation (cloudy density) contrast in colour
  tooth: number; // fine-grain contrast
  fibres: number;
  fibreLen: [number, number];
  fibreWidth: [number, number];
  fibreAlpha: [number, number];
  flecks: number; // dark recycled inclusions (card)
  normalStrength: number;
  roughBase: number;
  roughVar: number;
}

const RECIPES: Record<PaperKind, PaperRecipe> = {
  paper: {
    size: 512,
    seed: 11,
    cloud: 0.07,
    tooth: 0.05,
    fibres: 1400,
    fibreLen: [5, 26],
    fibreWidth: [0.5, 1.1],
    fibreAlpha: [0.05, 0.16],
    flecks: 0,
    normalStrength: 1.5,
    roughBase: 0.86,
    roughVar: 0.12,
  },
  card: {
    size: 512,
    seed: 23,
    cloud: 0.1,
    tooth: 0.09,
    fibres: 2200,
    fibreLen: [3, 16],
    fibreWidth: [0.6, 1.4],
    fibreAlpha: [0.06, 0.2],
    flecks: 140,
    normalStrength: 2.2,
    roughBase: 0.9,
    roughVar: 0.1,
  },
};

interface BaseSet {
  map: CanvasTexture;
  normalMap: CanvasTexture;
  roughnessMap: CanvasTexture;
}

const baseCache = new Map<string, BaseSet>();
const repeatCache = new Map<string, PaperTextureSet>();
const maskCache = new Map<string, DeckledMask>();

function buildPaperBase(kind: PaperKind): BaseSet {
  const r = RECIPES[kind];
  const { size } = r;
  const rand = mulberry32(r.seed);

  // Formation: soft cloudy density, like light through a sheet.
  const cloud = fbmField(size, size, 4, 4, 5, rand, 0.55);
  // Tooth: fine surface grain.
  const tooth = fbmField(size, size, 64, 64, 3, rand, 0.6);

  // Fibres on their own canvas, mid-grey background.
  const fib = makeCanvas(size, size);
  fib.ctx.fillStyle = "rgb(128,128,128)";
  fib.ctx.fillRect(0, 0, size, size);
  drawFibres(fib.ctx, size, r.fibres, rand, r.fibreLen, r.fibreWidth, r.fibreAlpha, 0.35);
  if (r.flecks) {
    for (let i = 0; i < r.flecks; i++) {
      const x = rand() * size;
      const y = rand() * size;
      const rad = 0.4 + rand() * 1.4;
      fib.ctx.fillStyle = `rgba(40,25,10,${0.15 + rand() * 0.35})`;
      fib.ctx.beginPath();
      fib.ctx.ellipse(x, y, rad * (1 + rand()), rad, rand() * Math.PI, 0, Math.PI * 2);
      fib.ctx.fill();
    }
  }
  const fibData = fib.ctx.getImageData(0, 0, size, size).data;

  const n = size * size;
  const height = new Float32Array(n);
  const col = makeCanvas(size, size);
  const rough = makeCanvas(size, size);
  const colImg = col.ctx.createImageData(size, size);
  const roughImg = rough.ctx.createImageData(size, size);
  const cd = colImg.data;
  const rd = roughImg.data;

  for (let i = 0; i < n; i++) {
    const f = (fibData[i * 4] - 128) / 128; // -1..1, fibre light/dark
    const c = cloud[i] - 0.5;
    const t = tooth[i] - 0.5;
    height[i] = c * 0.35 + t * 0.9 + f * 0.6;

    // Colour: near-white, slightly warm, formation + fibres show faintly.
    const v = 0.955 - c * r.cloud * 2 + t * r.tooth + f * 0.05;
    const j = i * 4;
    cd[j] = Math.min(255, v * 255);
    cd[j + 1] = Math.min(255, v * 0.992 * 255);
    cd[j + 2] = Math.min(255, v * 0.975 * 255);
    cd[j + 3] = 255;

    const rv = Math.min(1, Math.max(0, r.roughBase + (t + c * 0.5) * r.roughVar * 2 - f * 0.05));
    rd[j] = rd[j + 1] = rd[j + 2] = rv * 255;
    rd[j + 3] = 255;
  }
  col.ctx.putImageData(colImg, 0, 0);
  rough.ctx.putImageData(roughImg, 0, 0);

  return {
    map: toTexture(col.canvas, true, true),
    normalMap: toTexture(normalCanvasFromHeight(height, size, size, r.normalStrength), false, true),
    roughnessMap: toTexture(rough.canvas, false, true),
  };
}

function normRepeat(repeat: number | [number, number] | undefined): [number, number] {
  if (repeat === undefined) return [1, 1];
  return typeof repeat === "number" ? [repeat, repeat] : repeat;
}

/** Clone a cached base set with its own repeat (clones share the GPU image). */
function withRepeat(cacheKey: string, base: BaseSet, rx: number, ry: number): PaperTextureSet {
  const key = `${cacheKey}|${rx}x${ry}`;
  const hit = repeatCache.get(key);
  if (hit) return hit;
  let set: PaperTextureSet;
  if (rx === 1 && ry === 1) {
    set = base;
  } else {
    const clone = (t: CanvasTexture) => {
      const c = t.clone();
      c.repeat.set(rx, ry);
      c.needsUpdate = true;
      return c;
    };
    set = { map: clone(base.map), normalMap: clone(base.normalMap), roughnessMap: clone(base.roughnessMap) };
  }
  repeatCache.set(key, set);
  return set;
}

/** Shared paper or card grain textures. Cached; do not dispose. */
export function getPaperTextures(kind: PaperKind = "paper", repeat?: number | [number, number]): PaperTextureSet {
  assertBrowser("getPaperTextures");
  let base = baseCache.get(kind);
  if (!base) {
    base = buildPaperBase(kind);
    baseCache.set(kind, base);
  }
  const [rx, ry] = normRepeat(repeat);
  return withRepeat(kind, base, rx, ry);
}

/* ------------------------------------------------------------------------ */
/* Wood table                                                                */
/* ------------------------------------------------------------------------ */

const WOOD_LIGHT = [132, 99, 74];
const WOOD_DARK = [76, 54, 40];

function buildWoodBase(): BaseSet {
  const w = 1024;
  const h = 1024;
  const rand = mulberry32(97);
  const planks = 5;
  const plankH = h / planks;

  // Long streaks along x (grain), plus a slow warp for rings.
  const streak = fbmField(w, h, 3, 40, 4, rand, 0.5);
  const warp = fbmField(w, h, 2, 4, 3, rand, 0.5);
  const fine = fbmField(w, h, 8, 256, 2, rand, 0.5);

  const plankTone = Array.from({ length: planks }, () => rand() * 0.22 - 0.11);
  const plankRing = Array.from({ length: planks }, () => 10 + rand() * 18);
  const plankPhase = Array.from({ length: planks }, () => rand() * 10);
  // One end-joint per plank, at a random x.
  const joint = Array.from({ length: planks }, () => Math.floor(rand() * w));

  const col = makeCanvas(w, h);
  const rough = makeCanvas(w, h);
  const ci = col.ctx.createImageData(w, h);
  const ri = rough.ctx.createImageData(w, h);
  const height = new Float32Array(w * h);

  for (let y = 0; y < h; y++) {
    const p = Math.min(planks - 1, Math.floor(y / plankH));
    const py = (y - p * plankH) / plankH; // 0..1 across the plank
    const seamY = Math.min(py, 1 - py) * plankH; // px from the long seam
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      const s = streak[i];
      const ring = Math.sin((py * plankRing[p] + warp[i] * 6 + plankPhase[p]) * Math.PI);
      const ringLine = Math.pow(Math.abs(ring), 6); // thin darker late-wood lines
      let t = 0.52 + (s - 0.5) * 0.7 + (warp[i] - 0.5) * 0.35 + plankTone[p] - ringLine * 0.12 + (fine[i] - 0.5) * 0.14;
      // Seams: long edges and one butt joint per plank.
      const jx = Math.abs(x - joint[p]);
      const jd = Math.min(jx, w - jx);
      const seam = Math.min(seamY, jd);
      let groove = 0;
      if (seam < 1.6) groove = 1 - seam / 1.6;
      t -= groove * 0.35;
      t = Math.min(1, Math.max(0, t));

      const j = i * 4;
      ci.data[j] = WOOD_DARK[0] + (WOOD_LIGHT[0] - WOOD_DARK[0]) * t;
      ci.data[j + 1] = WOOD_DARK[1] + (WOOD_LIGHT[1] - WOOD_DARK[1]) * t;
      ci.data[j + 2] = WOOD_DARK[2] + (WOOD_LIGHT[2] - WOOD_DARK[2]) * t;
      ci.data[j + 3] = 255;

      height[i] = (s - 0.5) * 0.4 - ringLine * 0.25 + (fine[i] - 0.5) * 0.5 - groove * 2.5;
      const rv = 0.62 + (1 - t) * 0.18 + groove * 0.2;
      ri.data[j] = ri.data[j + 1] = ri.data[j + 2] = Math.min(255, rv * 255);
      ri.data[j + 3] = 255;
    }
  }
  col.ctx.putImageData(ci, 0, 0);
  rough.ctx.putImageData(ri, 0, 0);
  return {
    map: toTexture(col.canvas, true, true),
    normalMap: toTexture(normalCanvasFromHeight(height, w, h, 2.5), false, true),
    roughnessMap: toTexture(rough.canvas, false, true),
  };
}

/** Shared wood-table textures (coloured). Cached; do not dispose. */
export function getWoodTextures(repeat?: number | [number, number]): PaperTextureSet {
  assertBrowser("getWoodTextures");
  let base = baseCache.get("wood");
  if (!base) {
    base = buildWoodBase();
    baseCache.set("wood", base);
  }
  const [rx, ry] = normRepeat(repeat);
  return withRepeat("wood", base, rx, ry);
}

/* ------------------------------------------------------------------------ */
/* Deckled / torn edge masks                                                 */
/* ------------------------------------------------------------------------ */

/**
 * An alpha mask for a cutout with a hand-made edge. The shape fills the UV
 * square (so put it on a plane whose width/height equals `aspect`), inset just
 * enough to leave room for the edge wobble. Cached by options.
 */
export function getDeckledMask(options: DeckledMaskOptions = {}): DeckledMask {
  assertBrowser("getDeckledMask");
  const {
    aspect = 1,
    style = "deckled",
    amount = 1,
    seed = 1,
    resolution = 512,
    cornerRadius = 0.015,
  } = options;
  const key = `${aspect.toFixed(3)}|${style}|${amount}|${seed}|${resolution}|${cornerRadius}`;
  const hit = maskCache.get(key);
  if (hit) return hit;

  const W = aspect >= 1 ? resolution : Math.max(8, Math.round(resolution * aspect));
  const H = aspect >= 1 ? Math.max(8, Math.round(resolution / aspect)) : resolution;
  const short = Math.min(W, H);
  const rand = mulberry32(seed * 7919 + (style === "torn" ? 3 : style === "clean" ? 5 : 1));

  // Amplitude (px) and character of the edge.
  const amp =
    style === "torn" ? short * 0.03 * amount : style === "deckled" ? short * 0.011 * amount : 0;
  const inset = Math.max(2, amp * 1.15 + 1.5);
  const perim = 2 * (W + H - 4 * inset);
  const steps = Math.max(64, Math.round(perim / 1.5));
  const coarse = fbm1D(steps, style === "torn" ? 9 : 40, style === "torn" ? 5 : 3, rand);
  const fuzz = fbm1D(steps, Math.max(64, Math.round(steps / 3)), 1, rand);

  // Walk the inset rectangle (rounded corners) and push each point inward.
  const r = Math.max(0, cornerRadius * short);
  const x0 = inset;
  const y0 = inset;
  const x1 = W - inset;
  const y1 = H - inset;
  const pts: number[] = [];
  const pointAt = (s: number): [number, number, number, number] => {
    // Returns position and inward normal along a rounded rectangle.
    const w = x1 - x0 - 2 * r;
    const h = y1 - y0 - 2 * r;
    const arc = (Math.PI / 2) * r;
    const segs = [w, arc, h, arc, w, arc, h, arc];
    let d = s % (2 * (w + h) + 4 * arc);
    for (let k = 0; k < 8; k++) {
      if (d <= segs[k] || k === 7) {
        const f = segs[k] > 0 ? d / segs[k] : 0;
        switch (k) {
          case 0: return [x0 + r + f * w, y0, 0, 1];
          case 2: return [x1, y0 + r + f * h, -1, 0];
          case 4: return [x1 - r - f * w, y1, 0, -1];
          case 6: return [x0, y1 - r - f * h, 1, 0];
          default: {
            const cx = k === 1 ? x1 - r : k === 3 ? x1 - r : k === 5 ? x0 + r : x0 + r;
            const cy = k === 1 ? y0 + r : k === 3 ? y1 - r : k === 5 ? y1 - r : y0 + r;
            const a0 = k === 1 ? -Math.PI / 2 : k === 3 ? 0 : k === 5 ? Math.PI / 2 : Math.PI;
            const a = a0 + f * (Math.PI / 2);
            return [cx + Math.cos(a) * r, cy + Math.sin(a) * r, -Math.cos(a), -Math.sin(a)];
          }
        }
      }
      d -= segs[k];
    }
    return [x0, y0, 1, 1];
  };
  const total = 2 * (x1 - x0 - 2 * r + (y1 - y0 - 2 * r)) + 2 * Math.PI * r;
  for (let i = 0; i < steps; i++) {
    const [px, py, nx, ny] = pointAt((i / steps) * total);
    const off = amp * (0.5 + 0.5 * coarse[i]) + (style === "clean" ? 0 : amp * 0.35 * fuzz[i]);
    pts.push(px + nx * off, py + ny * off);
  }

  const traceShape = (ctx: CanvasRenderingContext2D) => {
    ctx.beginPath();
    ctx.moveTo(pts[0], pts[1]);
    for (let i = 1; i < steps; i++) ctx.lineTo(pts[i * 2], pts[i * 2 + 1]);
    ctx.closePath();
  };

  // Alpha: white shape on black, softened a hair for anti-aliasing.
  const a = makeCanvas(W, H);
  a.ctx.fillStyle = "#000";
  a.ctx.fillRect(0, 0, W, H);
  a.ctx.filter = style === "clean" ? "none" : "blur(0.6px)";
  a.ctx.fillStyle = "#fff";
  traceShape(a.ctx);
  a.ctx.fill();
  // Stray fibres poking out of a torn edge.
  if (style !== "clean") {
    a.ctx.filter = "none";
    a.ctx.strokeStyle = "rgba(255,255,255,0.8)";
    a.ctx.lineCap = "round";
    const count = Math.round(steps * (style === "torn" ? 0.18 : 0.06));
    for (let k = 0; k < count; k++) {
      const i = Math.floor(rand() * steps);
      const [, , nx, ny] = pointAt((i / steps) * total);
      const len = (style === "torn" ? 2 : 1) + rand() * amp * 0.5;
      const sx = pts[i * 2];
      const sy = pts[i * 2 + 1];
      const jitter = (rand() - 0.5) * 1.2;
      a.ctx.lineWidth = 0.6 + rand() * 0.6;
      a.ctx.beginPath();
      a.ctx.moveTo(sx + nx * 1, sy + ny * 1);
      a.ctx.lineTo(sx - nx * len - ny * jitter * len, sy - ny * len + nx * jitter * len);
      a.ctx.stroke();
    }
  }

  // Rim: band just inside the edge (wider and lighter on torn paper).
  const rim = makeCanvas(W, H);
  rim.ctx.fillStyle = "#000";
  rim.ctx.fillRect(0, 0, W, H);
  if (style !== "clean") {
    const band = style === "torn" ? Math.max(2, amp * 0.55) : Math.max(1.2, amp * 0.35);
    rim.ctx.filter = `blur(${(band * 0.45).toFixed(2)}px)`;
    rim.ctx.strokeStyle = "#fff";
    rim.ctx.lineWidth = band * 2;
    traceShape(rim.ctx);
    rim.ctx.stroke();
  }

  const mask: DeckledMask = {
    alphaMap: toTexture(a.canvas, false, false),
    rimMap: toTexture(rim.canvas, false, false),
  };
  maskCache.set(key, mask);
  return mask;
}

/* ------------------------------------------------------------------------ */
/* Materials                                                                 */
/* ------------------------------------------------------------------------ */

const DEFAULT_PAPER = "#f3ead8";
const DEFAULT_CARD = "#e2d2b4";

function applyGrain(m: MeshStandardMaterial, set: PaperTextureSet, grain: number) {
  m.map = set.map;
  m.normalMap = set.normalMap;
  m.normalScale = new Vector2(grain, grain);
  m.roughnessMap = set.roughnessMap;
}

/** A sheet of paper. New material; shared textures. */
export function createPaperMaterial(options: PaperMaterialOptions = {}): MeshStandardMaterial {
  const { color = DEFAULT_PAPER, roughness = 0.92, kind = "paper", side = FrontSide } = options;
  const grain = options.grain ?? (kind === "card" ? 1.2 : 1);
  const m = new MeshStandardMaterial({ color, roughness, metalness: 0, side });
  applyGrain(m, getPaperTextures(kind, options.repeat), grain);
  m.name = `paper:${kind}`;
  return m;
}

/** Thicker card / cardboard stock. */
export function createCardMaterial(options: Omit<PaperMaterialOptions, "kind"> = {}): MeshStandardMaterial {
  return createPaperMaterial({ color: DEFAULT_CARD, roughness: 0.95, ...options, kind: "card" });
}

/** The cut edge of card or a page block: darker, dense, no sheen. */
export function createCardEdgeMaterial(options: { color?: ColorRepresentation; darken?: number } = {}): MeshStandardMaterial {
  const c = new Color(options.color ?? DEFAULT_CARD).multiplyScalar(1 - (options.darken ?? 0.16));
  const m = new MeshStandardMaterial({ color: c, roughness: 1, metalness: 0 });
  // Edge grain runs along the cut: stretch the card texture.
  applyGrain(m, getPaperTextures("card", [6, 0.25]), 0.6);
  m.name = "paper:card-edge";
  return m;
}

/**
 * Materials for a flat BoxGeometry(width, thickness, depth): +y / -y get the
 * card face, the four sides get the darker cut edge. Order matches three's
 * BoxGeometry groups [+x, -x, +y, -y, +z, -z]. The edge material is shared by
 * the four sides; dispose each unique material once.
 */
export function createCardBoxMaterials(
  options: Omit<PaperMaterialOptions, "kind"> & { edgeColor?: ColorRepresentation } = {},
): MeshStandardMaterial[] {
  const face = createCardMaterial(options);
  const back = createCardMaterial({ ...options, grain: (options.grain ?? 1.2) * 0.8 });
  const edge = createCardEdgeMaterial({ color: options.edgeColor ?? options.color });
  return [edge, edge, face, back, edge, edge];
}

/**
 * A pop-up cutout: paper with a deckled or torn alpha edge. Uses
 * alpha-to-coverage for smooth edges without transparency sorting (keep the
 * Canvas's default antialias on), and alphaTest so shadows follow the edge.
 */
export function createCutoutMaterial(options: CutoutMaterialOptions = {}): MeshStandardMaterial {
  const maskOpt = options.mask;
  const mask: DeckledMask =
    maskOpt && "alphaMap" in maskOpt ? maskOpt : getDeckledMask(maskOpt as DeckledMaskOptions | undefined);
  const style = maskOpt && !("alphaMap" in maskOpt) ? maskOpt.style ?? "deckled" : "deckled";
  const m = createPaperMaterial({ side: DoubleSide, ...options });
  m.alphaMap = mask.alphaMap;
  m.alphaTest = 0.5;
  m.alphaToCoverage = true;
  const rim = options.rim ?? (style === "torn" ? 0.5 : 0.2);
  if (rim > 0) {
    m.emissive = new Color("#fff4e2");
    m.emissiveMap = mask.rimMap;
    m.emissiveIntensity = rim * 0.45;
  }
  m.name = "paper:cutout";
  return m;
}

/** The wooden table under the book. */
export function createWoodMaterial(options: { repeat?: number | [number, number]; color?: ColorRepresentation } = {}): MeshStandardMaterial {
  const set = getWoodTextures(options.repeat);
  const m = new MeshStandardMaterial({ color: options.color ?? "#ffffff", roughness: 0.75, metalness: 0 });
  m.map = set.map;
  m.normalMap = set.normalMap;
  m.normalScale = new Vector2(0.8, 0.8);
  m.roughnessMap = set.roughnessMap;
  m.name = "wood:table";
  return m;
}

/** Free every cached texture. Only for full teardown; materials made before
 *  this call lose their textures. */
export function disposePaperResources(): void {
  const seen = new Set<Texture>();
  const drop = (t: Texture) => {
    if (seen.has(t)) return;
    seen.add(t);
    t.dispose();
  };
  for (const s of repeatCache.values()) [s.map, s.normalMap, s.roughnessMap].forEach(drop);
  for (const s of baseCache.values()) [s.map, s.normalMap, s.roughnessMap].forEach(drop);
  for (const m of maskCache.values()) [m.alphaMap, m.rimMap].forEach(drop);
  repeatCache.clear();
  baseCache.clear();
  maskCache.clear();
}
