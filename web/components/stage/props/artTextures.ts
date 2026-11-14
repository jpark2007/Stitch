/**
 * Prop art textures loaded from URLs, shared and reference counted.
 *
 * `acquireArt(url)` returns an entry whose textures exist immediately (so a
 * material can be built once and never recompiled) and fill in when the image
 * arrives; `ready` / `failed` flip then. Callers poll those flags in useFrame,
 * so a load never causes a React re-render. `releaseArt(url)` disposes the GPU
 * textures when the last user lets go.
 *
 * Clean cut edges. A background-removed PNG has black, fully transparent
 * texels around the shape; filtered at an alphaTest edge they darken it into
 * a grey fringe. So on load the art is split in two: a colour map whose edge
 * colours are bled outward a few pixels, and an alpha map with the original
 * silhouette. Long sides above MAX_SIDE are scaled down.
 *
 * Browser-only: call from effects, useMemo inside a <Canvas>, or useFrame.
 */

import { ClampToEdgeWrapping, NoColorSpace, SRGBColorSpace, Texture } from "three";

/** Largest side kept, in px. Prop art is never shown bigger than this. */
const MAX_SIDE = 2048;
/** How far (px) edge colours are bled outward. */
const BLEED = 4;

export interface ArtEntry {
  readonly url: string;
  /** Colour, edges bled (sRGB). */
  readonly texture: Texture;
  /** Silhouette: white inside, black outside. */
  readonly alpha: Texture;
  /** The image has loaded and the textures are ready to draw. */
  ready: boolean;
  /** The image could not be loaded; draw a placeholder instead. */
  failed: boolean;
  /** Image width / height once ready (1 until then). */
  aspect: number;
  /** Image height in px once ready (0 until then), to size parts relative to
   *  the body art. */
  pixelHeight: number;
  /** Resolves when the image has loaded or failed. */
  readonly done: Promise<void>;
}

interface Slot {
  entry: ArtEntry;
  refs: number;
}

const slots = new Map<string, Slot>();

function emptyTexture(srgb: boolean, name: string): Texture {
  const t = new Texture();
  t.colorSpace = srgb ? SRGBColorSpace : NoColorSpace;
  t.wrapS = t.wrapT = ClampToEdgeWrapping;
  t.anisotropy = 4;
  t.name = name;
  return t;
}

function canvas(w: number, h: number) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return { c, ctx: c.getContext("2d")! };
}

/** Split an image into a bled colour canvas and an alpha canvas. */
function split(img: HTMLImageElement): { color: HTMLCanvasElement; alpha: HTMLCanvasElement } {
  const k = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
  const w = Math.max(1, Math.round(img.naturalWidth * k));
  const h = Math.max(1, Math.round(img.naturalHeight * k));

  // Alpha: the image's coverage as white on black.
  const a = canvas(w, h);
  a.ctx.drawImage(img, 0, 0, w, h);
  a.ctx.globalCompositeOperation = "source-in";
  a.ctx.fillStyle = "#fff";
  a.ctx.fillRect(0, 0, w, h);
  a.ctx.globalCompositeOperation = "destination-over";
  a.ctx.fillStyle = "#000";
  a.ctx.fillRect(0, 0, w, h);

  // Colour: the image, with copies nudged outward underneath it, so pixels
  // just outside the silhouette carry the edge's colour instead of black.
  const col = canvas(w, h);
  col.ctx.drawImage(img, 0, 0, w, h);
  col.ctx.globalCompositeOperation = "destination-over";
  for (let r = 1; r <= BLEED; r++) {
    for (let i = 0; i < 8; i++) {
      const ang = (i / 8) * Math.PI * 2;
      col.ctx.drawImage(img, Math.round(Math.cos(ang) * r), Math.round(Math.sin(ang) * r), w, h);
    }
  }
  return { color: col.c, alpha: a.c };
}

/** Get (and hold a reference to) the textures for `url`. */
export function acquireArt(url: string): ArtEntry {
  const hit = slots.get(url);
  if (hit) {
    hit.refs++;
    return hit.entry;
  }
  let settle!: () => void;
  const done = new Promise<void>((r) => (settle = r));
  const label = `prop-art:${url.slice(0, 48)}`;
  const entry: ArtEntry = {
    url,
    texture: emptyTexture(true, label),
    alpha: emptyTexture(false, `${label}:alpha`),
    ready: false,
    failed: false,
    aspect: 1,
    pixelHeight: 0,
    done,
  };
  const slot: Slot = { entry, refs: 1 };
  slots.set(url, slot);

  const img = new Image();
  img.crossOrigin = "anonymous";
  img.decoding = "async";
  img.src = url;
  img
    .decode()
    .then(() => {
      if (slots.get(url) !== slot) return; // released while loading
      const { color, alpha } = split(img);
      entry.texture.image = color;
      entry.texture.needsUpdate = true;
      entry.alpha.image = alpha;
      entry.alpha.needsUpdate = true;
      entry.aspect = img.naturalWidth / Math.max(1, img.naturalHeight);
      entry.pixelHeight = img.naturalHeight;
      entry.ready = true;
    })
    .catch(() => {
      entry.failed = true;
    })
    .finally(settle);
  return entry;
}

/** Drop a reference taken by acquireArt; the last one disposes the textures. */
export function releaseArt(url: string): void {
  const slot = slots.get(url);
  if (!slot) return;
  if (--slot.refs > 0) return;
  slots.delete(url);
  slot.entry.texture.dispose();
  slot.entry.alpha.dispose();
}

/** Start loading a prop's art ahead of time (e.g. before a spread opens) and
 *  keep it until the returned release function is called. */
export function preloadArt(urls: readonly string[]): { done: Promise<void>; release: () => void } {
  const entries = urls.map(acquireArt);
  let released = false;
  return {
    done: Promise.all(entries.map((e) => e.done)).then(() => undefined),
    release: () => {
      if (released) return;
      released = true;
      for (const u of urls) releaseArt(u);
    },
  };
}
