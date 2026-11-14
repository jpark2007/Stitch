/**
 * Prop art textures loaded from URLs, shared and reference counted.
 *
 * `acquireArt(url)` returns an entry whose `texture` exists immediately (so a
 * material can be built once and never recompiled) and fills in when the
 * image arrives; `ready` / `failed` flip then. Callers poll those flags in
 * useFrame, so a load never causes a React re-render. `releaseArt(url)`
 * disposes the GPU texture when the last user lets go.
 *
 * Browser-only: call from effects, useMemo inside a <Canvas>, or useFrame.
 */

import { SRGBColorSpace, Texture, TextureLoader } from "three";

export interface ArtEntry {
  readonly url: string;
  readonly texture: Texture;
  /** The image has loaded and the texture is ready to draw. */
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
let loader: TextureLoader | null = null;

/** Get (and hold a reference to) the texture for `url`. */
export function acquireArt(url: string): ArtEntry {
  const hit = slots.get(url);
  if (hit) {
    hit.refs++;
    return hit.entry;
  }
  loader ??= new TextureLoader();
  let settle!: () => void;
  const done = new Promise<void>((r) => (settle = r));
  const texture = new Texture();
  texture.colorSpace = SRGBColorSpace;
  texture.anisotropy = 4;
  texture.name = `prop-art:${url.slice(0, 64)}`;
  const entry: ArtEntry = { url, texture, ready: false, failed: false, aspect: 1, pixelHeight: 0, done };
  loader.load(
    url,
    (loaded) => {
      const img = loaded.image as { width?: number; height?: number } | undefined;
      const w = img?.width ?? 0;
      const h = img?.height ?? 0;
      // Hand the decoded image to the texture the materials already use.
      texture.image = loaded.image;
      texture.needsUpdate = true;
      entry.aspect = w > 0 && h > 0 ? w / h : 1;
      entry.pixelHeight = h;
      entry.ready = true;
      settle();
    },
    undefined,
    () => {
      entry.failed = true;
      settle();
    },
  );
  slots.set(url, { entry, refs: 1 });
  return entry;
}

/** Drop a reference taken by acquireArt; the last one disposes the texture. */
export function releaseArt(url: string): void {
  const slot = slots.get(url);
  if (!slot) return;
  if (--slot.refs > 0) return;
  slots.delete(url);
  slot.entry.texture.dispose();
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
