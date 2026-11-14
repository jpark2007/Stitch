/**
 * Paper cutouts for pop-up set pieces.
 *
 * A cutout is drawn twice by a painter function: once as a white silhouette
 * (the mask) and once in colour. The mask gets a hand-cut deckled edge (a
 * small noise displacement, kept straight along the bottom fold), and its
 * outline is traced with marching squares into thin wall quads: the visible
 * thickness of the card. The colour image stays fully opaque (shapes bleed past
 * the silhouette) so mipmaps never pull dark fringes into the edge.
 *
 * Painters work in piece units: origin at the bottom-left, y up, height 1 and
 * width `aspect`. The bottom row (y = 0) is the hinge fold.
 *
 * Later, generated cutout PNGs can feed the same path: draw the image as the
 * colour pass and its alpha as the mask pass.
 *
 * Browser-only. Results are cached by key; textures and geometry are shared,
 * never dispose them per piece.
 */

import {
  BufferGeometry,
  CanvasTexture,
  Float32BufferAttribute,
  LinearMipmapLinearFilter,
  NoColorSpace,
  SRGBColorSpace,
  type Texture,
} from "three";
import { getPaperTextures } from "@/components/paper/materials";
import { rng } from "./timeline";

export type PaintPass = "color" | "mask";

/** Drawing helpers handed to a painter. All coordinates are piece units. */
export interface PaintKit {
  ctx: CanvasRenderingContext2D;
  pass: PaintPass;
  aspect: number;
  /** Seeded random numbers, identical in both passes. */
  rand: () => number;
  /** Part of the silhouette. Mask pass: white. Colour pass: filled, with bleed. */
  shape(path: Path2D, color: string): void;
  /** A hole cut through the card (windows you can see through). */
  hole(path: Path2D): void;
  /** A paper piece glued on top (colour pass only), with a faint lift shadow. */
  layer(path: Path2D, color: string, lift?: number): void;
  /** A thin pencil/ink line (colour pass only). */
  line(path: Path2D, color: string, width: number): void;
}

export interface CutoutSpec {
  /** Cache key. Same key, same cutout. */
  key: string;
  /** Width / height. */
  aspect: number;
  /** Colour under everything, also the bleed colour. */
  base: string;
  paint: (kit: PaintKit) => void;
  /** Long-side resolution of the textures, px. Default 512. */
  resolution?: number;
  /** Edge roughness multiplier. Default 1. */
  deckle?: number;
  seed?: number;
}

export interface Cutout {
  key: string;
  aspect: number;
  /** Opaque sRGB colour image. */
  map: Texture;
  /** Silhouette (white inside) for alphaMap + alphaTest. */
  alphaMap: Texture;
  /** Card thickness walls around every outline, unit height, z in [-0.5, 0.5]. */
  edge: BufferGeometry;
  /** The walls split at the centre crease, for V-fold pieces. */
  edgeLeft: BufferGeometry;
  edgeRight: BufferGeometry;
  /** Where the cutout touches the fold, as [from, to] runs in piece units
   *  (x centred on the piece). Glue tabs go behind these. */
  feet: [number, number][];
}

const cache = new Map<string, Cutout>();

function canvas(w: number, h: number) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("2D canvas unavailable");
  return { c, ctx };
}

function makeKit(ctx: CanvasRenderingContext2D, pass: PaintPass, aspect: number, seed: number, px: number): PaintKit {
  const bleed = 3 / px; // ~3px of colour past every silhouette edge
  const kit: PaintKit = {
    ctx,
    pass,
    aspect,
    rand: rng(seed),
    shape(path, color) {
      if (pass === "mask") {
        ctx.fillStyle = "#fff";
        ctx.fill(path);
        return;
      }
      ctx.fillStyle = color;
      ctx.strokeStyle = color;
      ctx.lineWidth = bleed * 2;
      ctx.lineJoin = "round";
      ctx.fill(path);
      ctx.stroke(path);
    },
    hole(path) {
      if (pass !== "mask") return;
      ctx.save();
      ctx.globalCompositeOperation = "destination-out";
      ctx.fill(path);
      ctx.restore();
    },
    layer(path, color, lift = 1) {
      if (pass !== "color") return;
      ctx.save();
      ctx.shadowColor = `rgba(58, 36, 20, ${0.22 * lift})`;
      ctx.shadowBlur = 5 * lift;
      ctx.shadowOffsetY = 2 * lift;
      ctx.fillStyle = color;
      ctx.fill(path);
      ctx.restore();
    },
    line(path, color, width) {
      if (pass !== "color") return;
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.stroke(path);
    },
  };
  return kit;
}

/** Smooth value noise on a coarse lattice, sampled bilinearly (periodic-free). */
function noiseField(cols: number, rows: number, rand: () => number) {
  const v = new Float32Array((cols + 1) * (rows + 1));
  for (let i = 0; i < v.length; i++) v[i] = rand() * 2 - 1;
  return (x: number, y: number) => {
    const cx = Math.min(cols, Math.max(0, x));
    const cy = Math.min(rows, Math.max(0, y));
    const ix = Math.min(cols - 1, Math.floor(cx));
    const iy = Math.min(rows - 1, Math.floor(cy));
    let fx = cx - ix;
    let fy = cy - iy;
    fx = fx * fx * (3 - 2 * fx);
    fy = fy * fy * (3 - 2 * fy);
    const r = cols + 1;
    const a = v[iy * r + ix];
    const b = v[iy * r + ix + 1];
    const c = v[(iy + 1) * r + ix];
    const d = v[(iy + 1) * r + ix + 1];
    return a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy;
  };
}

/**
 * Deckle a crisp mask: displace the lookup by smooth noise (hand-cut wobble)
 * plus a little per-pixel fuzz (fibres). Displacement fades out at the bottom
 * so the fold line stays straight.
 */
function deckleMask(src: Uint8ClampedArray, W: number, H: number, amount: number, seed: number): Float32Array {
  const rand = rng(seed * 92821 + 17);
  const cell = 26;
  const nx = noiseField(Math.ceil(W / cell) + 1, Math.ceil(H / cell) + 1, rand);
  const ny = noiseField(Math.ceil(W / cell) + 1, Math.ceil(H / cell) + 1, rand);
  const fine = 7;
  const fx = noiseField(Math.ceil(W / fine) + 1, Math.ceil(H / fine) + 1, rand);
  const fy = noiseField(Math.ceil(W / fine) + 1, Math.ceil(H / fine) + 1, rand);
  const amp = 2.1 * amount * (Math.max(W, H) / 512);
  const ampFine = 0.7 * amount * (Math.max(W, H) / 512);
  const A = new Float32Array(W * H);
  for (let i = 0; i < W * H; i++) A[i] = src[i * 4] / 255;
  const out = new Float32Array(W * H);
  const sample = (x: number, y: number): number => {
    if (x < 0 || y < 0 || x > W - 1 || y > H - 1) return y > H - 1 ? sample(x, H - 1) : 0;
    const x0 = Math.floor(x);
    const y0 = Math.floor(y);
    const x1 = Math.min(W - 1, x0 + 1);
    const y1 = Math.min(H - 1, y0 + 1);
    const tx = x - x0;
    const ty = y - y0;
    const a = A[y0 * W + x0];
    const b = A[y0 * W + x1];
    const c = A[y1 * W + x0];
    const d = A[y1 * W + x1];
    return a + (b - a) * tx + (c - a) * ty + (a - b - c + d) * tx * ty;
  };
  const band = Math.max(4, Math.ceil(amp + ampFine + 2));
  for (let y = 0; y < H; y++) {
    // Straight fold: no wobble in the bottom 2% of the piece.
    const fromBottom = (H - 1 - y) / H;
    const keep = Math.min(1, Math.max(0, (fromBottom - 0.012) / 0.03));
    for (let x = 0; x < W; x++) {
      const i = y * W + x;
      // Skip pixels far from any edge (cheap check on the crisp mask).
      const xl = Math.max(0, x - band);
      const xr = Math.min(W - 1, x + band);
      const yt = Math.max(0, y - band);
      const yb = Math.min(H - 1, y + band);
      const s = A[i];
      if (A[yt * W + xl] === s && A[yt * W + xr] === s && A[yb * W + xl] === s && A[yb * W + xr] === s && A[y * W + xl] === s && A[y * W + xr] === s && A[yt * W + x] === s && A[yb * W + x] === s) {
        out[i] = s;
        continue;
      }
      const dx = (nx(x / cell, y / cell) * amp + fx(x / fine, y / fine) * ampFine) * keep;
      const dy = (ny(x / cell, y / cell) * amp + fy(x / fine, y / fine) * ampFine) * keep;
      out[i] = sample(x + dx, y + dy);
    }
  }
  return out;
}

/* ------------------------------------------------------------------------ */
/* Marching squares -> card edge walls                                       */
/* ------------------------------------------------------------------------ */

function buildEdges(A: Float32Array, W: number, H: number, aspect: number) {
  // Work on a downsampled grid: walls only need to follow the outline roughly.
  const step = Math.max(1, Math.round(Math.max(W, H) / 200));
  const gw = Math.floor((W - 1) / step) + 1;
  const gh = Math.floor((H - 1) / step) + 1;
  const g = new Float32Array(gw * gh);
  for (let y = 0; y < gh; y++) for (let x = 0; x < gw; x++) g[y * gw + x] = A[y * step * W + x * step];
  const at = (x: number, y: number) => g[Math.min(gh - 1, Math.max(0, y)) * gw + Math.min(gw - 1, Math.max(0, x))];
  // Gradient (bilinear of central differences) at a grid point.
  const grad = (x: number, y: number): [number, number] => {
    const ix = Math.round(x);
    const iy = Math.round(y);
    return [(at(ix + 1, iy) - at(ix - 1, iy)) * 0.5, (at(ix, iy + 1) - at(ix, iy - 1)) * 0.5];
  };
  // Grid coords -> piece units (x centred, y up from the fold).
  const ux = (x: number) => ((x * step) / (W - 1) - 0.5) * aspect;
  const uy = (y: number) => 1 - (y * step) / (H - 1);

  const all: number[][] = [[], [], []]; // [all, left, right] positions
  const nrm: number[][] = [[], [], []];
  const iso = 0.5;
  const pushSeg = (ax: number, ay: number, bx: number, by: number) => {
    const ga = grad(ax, ay);
    const gb = grad(bx, by);
    // Outward = down the alpha gradient. Grid y runs down, piece y runs up.
    const na: [number, number] = [-ga[0], ga[1]];
    const nb: [number, number] = [-gb[0], gb[1]];
    const la = Math.hypot(na[0], na[1]) || 1;
    const lb = Math.hypot(nb[0], nb[1]) || 1;
    na[0] /= la; na[1] /= la;
    nb[0] /= lb; nb[1] /= lb;
    const p = [ux(ax), uy(ay), ux(bx), uy(by)];
    // Skip the fold line itself: nobody sees the bottom wall.
    if (p[1] < 0.004 && p[3] < 0.004) return;
    // Wind each quad so its front face points along the outward normal.
    const ex = p[2] - p[0];
    const ey = p[3] - p[1];
    // Quad (a,-), (b,-), (b,+), (a,+): face normal = (ey, -ex, 0) direction.
    const flip = ey * (na[0] + nb[0]) - ex * (na[1] + nb[1]) < 0;
    const [x0, y0, x1, y1] = flip ? [p[2], p[3], p[0], p[1]] : p;
    const [n0, n1] = flip ? [nb, na] : [na, nb];
    const quad = [
      [x0, y0, -0.5, n0], [x1, y1, -0.5, n1], [x1, y1, 0.5, n1],
      [x0, y0, -0.5, n0], [x1, y1, 0.5, n1], [x0, y0, 0.5, n0],
    ] as const;
    const side = (p[0] + p[2]) * 0.5 < 0 ? 1 : 2;
    for (const target of [0, side]) {
      for (const [x, y, z, n] of quad) {
        all[target].push(x, y, z);
        nrm[target].push(n[0], n[1], 0);
      }
    }
  };
  const lerpT = (a: number, b: number) => (iso - a) / (b - a || 1e-6);
  for (let y = 0; y < gh - 1; y++) {
    for (let x = 0; x < gw - 1; x++) {
      const tl = at(x, y);
      const tr = at(x + 1, y);
      const br = at(x + 1, y + 1);
      const bl = at(x, y + 1);
      const c = (tl > iso ? 8 : 0) | (tr > iso ? 4 : 0) | (br > iso ? 2 : 0) | (bl > iso ? 1 : 0);
      if (c === 0 || c === 15) continue;
      // Edge points: top, right, bottom, left.
      const T: [number, number] = [x + lerpT(tl, tr), y];
      const R: [number, number] = [x + 1, y + lerpT(tr, br)];
      const B: [number, number] = [x + lerpT(bl, br), y + 1];
      const L: [number, number] = [x, y + lerpT(tl, bl)];
      const seg = (a: [number, number], b: [number, number]) => pushSeg(a[0], a[1], b[0], b[1]);
      switch (c) {
        case 1: case 14: seg(L, B); break;
        case 2: case 13: seg(B, R); break;
        case 3: case 12: seg(L, R); break;
        case 4: case 11: seg(T, R); break;
        case 6: case 9: seg(T, B); break;
        case 7: case 8: seg(L, T); break;
        case 5: case 10: {
          const centre = (tl + tr + br + bl) / 4 > iso;
          if ((c === 5) === centre) { seg(L, T); seg(B, R); } else { seg(L, B); seg(T, R); }
          break;
        }
      }
    }
  }
  return [0, 1, 2].map((k) => {
    const geo = new BufferGeometry();
    geo.setAttribute("position", new Float32BufferAttribute(all[k], 3));
    geo.setAttribute("normal", new Float32BufferAttribute(nrm[k], 3));
    geo.computeBoundingSphere();
    return geo;
  });
}

/* ------------------------------------------------------------------------ */
/* Public                                                                    */
/* ------------------------------------------------------------------------ */

function toTexture(c: HTMLCanvasElement, srgb: boolean) {
  const t = new CanvasTexture(c);
  t.colorSpace = srgb ? SRGBColorSpace : NoColorSpace;
  t.minFilter = LinearMipmapLinearFilter;
  t.anisotropy = 4;
  t.needsUpdate = true;
  return t;
}

/** Build (or fetch from cache) the textures and edge walls for a cutout. */
export function getCutout(spec: CutoutSpec): Cutout {
  const hit = cache.get(spec.key);
  if (hit) return hit;
  const { aspect, resolution = 512, deckle = 1, seed = 1 } = spec;
  const W = aspect >= 1 ? resolution : Math.max(16, Math.round(resolution * aspect));
  const H = aspect >= 1 ? Math.max(16, Math.round(resolution / aspect)) : resolution;
  const toPixels = (ctx: CanvasRenderingContext2D) => ctx.setTransform(H, 0, 0, -H, 0, H);

  // Mask pass.
  const m = canvas(W, H);
  m.ctx.fillStyle = "#000";
  m.ctx.fillRect(0, 0, W, H);
  toPixels(m.ctx);
  spec.paint(makeKit(m.ctx, "mask", aspect, seed, H));
  const crisp = m.ctx.getImageData(0, 0, W, H).data;
  const A = deckleMask(crisp, W, H, deckle, seed);
  const maskImg = m.ctx.createImageData(W, H);
  for (let i = 0; i < W * H; i++) {
    const v = Math.round(A[i] * 255);
    maskImg.data[i * 4] = v;
    maskImg.data[i * 4 + 1] = v;
    maskImg.data[i * 4 + 2] = v;
    maskImg.data[i * 4 + 3] = 255;
  }
  m.ctx.setTransform(1, 0, 0, 1, 0, 0);
  m.ctx.putImageData(maskImg, 0, 0);

  // Colour pass, then a faint card grain and a lighter cut rim.
  const col = canvas(W, H);
  col.ctx.fillStyle = spec.base;
  col.ctx.fillRect(0, 0, W, H);
  toPixels(col.ctx);
  spec.paint(makeKit(col.ctx, "color", aspect, seed, H));
  col.ctx.setTransform(1, 0, 0, 1, 0, 0);
  const grain = getPaperTextures("card").map.image as CanvasImageSource | undefined;
  if (grain) {
    col.ctx.save();
    col.ctx.globalCompositeOperation = "multiply";
    col.ctx.globalAlpha = 0.55;
    const pat = col.ctx.createPattern(grain, "repeat");
    if (pat) {
      col.ctx.fillStyle = pat;
      col.ctx.fillRect(0, 0, W, H);
    }
    col.ctx.restore();
  }
  // Rim: inside band near the cut, where the card's paler core shows.
  const blur = canvas(W, H);
  blur.ctx.filter = `blur(${Math.max(1.5, H / 260).toFixed(2)}px)`;
  blur.ctx.drawImage(m.c, 0, 0);
  const B = blur.ctx.getImageData(0, 0, W, H).data;
  const img = col.ctx.getImageData(0, 0, W, H);
  const d = img.data;
  for (let i = 0; i < W * H; i++) {
    const rim = Math.max(0, Math.min(1, (A[i] - B[i * 4] / 255) * 3.2));
    if (rim <= 0) continue;
    const k = rim * 0.32;
    d[i * 4] += (250 - d[i * 4]) * k;
    d[i * 4 + 1] += (243 - d[i * 4 + 1]) * k;
    d[i * 4 + 2] += (229 - d[i * 4 + 2]) * k;
  }
  col.ctx.putImageData(img, 0, 0);

  const [edge, edgeLeft, edgeRight] = buildEdges(A, W, H, aspect);
  const feet: [number, number][] = [];
  const row = (H - 1) * W;
  for (let x = 0, from = -1; x <= W; x++) {
    const solid = x < W && A[row + x] > 0.5;
    if (solid && from < 0) from = x;
    if (!solid && from >= 0) {
      if (x - from > 2) feet.push([(from / (W - 1) - 0.5) * aspect, ((x - 1) / (W - 1) - 0.5) * aspect]);
      from = -1;
    }
  }
  const cut: Cutout = {
    key: spec.key,
    aspect,
    map: toTexture(col.c, true),
    alphaMap: toTexture(m.c, false),
    edge,
    edgeLeft,
    edgeRight,
    feet,
  };
  cache.set(spec.key, cut);
  return cut;
}

/** Build cutouts ahead of time, one per idle task, so mounting is instant. */
export async function preloadCutouts(specs: readonly CutoutSpec[]): Promise<void> {
  if (typeof document === "undefined") return;
  type IdleWindow = Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number };
  for (const spec of specs) {
    if (cache.has(spec.key)) continue;
    await new Promise<void>((resolve, reject) => {
      const run = () => {
        try {
          getCutout(spec);
          resolve();
        } catch (e) {
          reject(e);
        }
      };
      const w = window as IdleWindow;
      if (w.requestIdleCallback) w.requestIdleCallback(run, { timeout: 200 });
      else setTimeout(run, 0);
    });
  }
}
