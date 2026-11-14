"use client";

/**
 * What is printed on each leaf, and the stage slot on the right page.
 *
 * Spread j (1-based) shows the verso of leaf j-1 on the left (the story page:
 * year, title, storybook text, tellers and taped-in photos) and the recto of
 * leaf j on the right (an empty, clearly marked stage slot where the pop-up
 * stage mounts). Leaf 0's recto is the title page, seen as the book opens.
 *
 * Pages are drawn to canvases (pageArt.ts) so the print bends with the paper.
 */

import { useEffect, useMemo, type ReactNode } from "react";
import { CanvasTexture, ClampToEdgeWrapping, RepeatWrapping, SRGBColorSpace } from "three";
import { COVER_OVERHANG, PAGE_H, PAGE_W } from "./leafShape";
import { drawContactShadow, drawCoverArt, drawEndpaper, drawPageEdges, drawSpineCloth } from "./coverArt";
import { drawBlankPage, drawStagePage, drawStoryPage, drawTitlePage } from "./pageArt";
import { loadPageFonts } from "./paperArt";
import type { BookCover, BookSpread } from "./types";

export interface BookArt {
  leaves: { front: HTMLCanvasElement; back: HTMLCanvasElement }[];
  coverOutside: HTMLCanvasElement;
  endpaper: HTMLCanvasElement;
  edges: HTMLCanvasElement;
  spine: HTMLCanvasElement;
  shadow: HTMLCanvasElement;
}

/** Yield to the browser between canvases so no single task is long. */
const yieldTask = () =>
  new Promise<void>((resolve) => {
    const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number };
    if (w.requestIdleCallback) w.requestIdleCallback(() => resolve(), { timeout: 120 });
    else setTimeout(resolve, 0);
  });

/** Draw every page of the book (fonts first). Browser-only. */
export async function buildBookArt(spreads: BookSpread[], cover: BookCover): Promise<BookArt> {
  const fonts = await loadPageFonts();
  const n = spreads.length;
  const leaves: BookArt["leaves"] = [];
  for (let i = 0; i <= n; i++) {
    const front = i === 0 ? drawTitlePage(cover, fonts, 3) : drawStagePage(spreads[i - 1], 2 * i + 1, fonts, 10 + i);
    await yieldTask();
    const back = i < n ? await drawStoryPage(spreads[i], 2 * (i + 1), fonts, 30 + i) : drawBlankPage(90, "right");
    await yieldTask();
    leaves.push({ front, back });
  }
  const bw = 860;
  const bh = Math.round((bw * (PAGE_H + 2 * COVER_OVERHANG)) / (PAGE_W + COVER_OVERHANG));
  const coverOutside = drawCoverArt(cover, fonts, bw, bh);
  await yieldTask();
  return {
    leaves,
    coverOutside,
    endpaper: drawEndpaper(512, Math.round((512 * bh) / bw)),
    edges: drawPageEdges(),
    spine: drawSpineCloth(),
    shadow: drawContactShadow(),
  };
}

export interface BookTextures {
  leaves: { front: CanvasTexture; back: CanvasTexture }[];
  coverOutside: CanvasTexture;
  endpaper: CanvasTexture;
  edges: CanvasTexture;
  spine: CanvasTexture;
  shadow: CanvasTexture;
}

function tex(c: HTMLCanvasElement, opts: { srgb?: boolean; repeat?: boolean; anisotropy?: number } = {}) {
  const t = new CanvasTexture(c);
  if (opts.srgb !== false) t.colorSpace = SRGBColorSpace;
  t.anisotropy = opts.anisotropy ?? 4;
  t.wrapS = t.wrapT = opts.repeat ? RepeatWrapping : ClampToEdgeWrapping;
  return t;
}

/** GPU textures for the drawn art; disposed on unmount. */
export function useBookTextures(art: BookArt, maxAnisotropy = 8): BookTextures {
  const textures = useMemo<BookTextures>(() => {
    // Print stays crisp at grazing angles.
    const page = { anisotropy: Math.min(16, maxAnisotropy) };
    const coverOutside = tex(art.coverOutside, page);
    // The board is flipped closed over the block, so its outside reads rotated.
    coverOutside.center.set(0.5, 0.5);
    coverOutside.rotation = Math.PI;
    return {
      leaves: art.leaves.map((l) => ({ front: tex(l.front, page), back: tex(l.back, page) })),
      coverOutside,
      endpaper: tex(art.endpaper),
      edges: tex(art.edges, { repeat: true }),
      spine: tex(art.spine),
      shadow: tex(art.shadow, { srgb: false }),
    };
  }, [art, maxAnisotropy]);
  useEffect(
    () => () => {
      textures.leaves.forEach((l) => {
        l.front.dispose();
        l.back.dispose();
      });
      [textures.coverOutside, textures.endpaper, textures.edges, textures.spine, textures.shadow].forEach((t) => t.dispose());
    },
    [textures],
  );
  return textures;
}

export interface StageSlotArgs {
  spread: BookSpread;
  /** 0-based spread index. */
  index: number;
  /**
   * 0..1, read it in useFrame: rises to 1 once the spread lies open and still,
   * falls to 0 as a turn starts (fold the pop-up down with it).
   */
  reveal: { readonly current: number };
}

/**
 * The right page's stage slot. Children are placed in page space: origin at
 * the centre of the right page's surface, x across the page (PAGE_W wide), z
 * down the page (PAGE_H tall, +z toward the reader), y up off the paper.
 */
export function StageSlot({ y, children }: { y: number; children: ReactNode }) {
  return (
    <group position={[PAGE_W / 2, y, 0]} name="stage-slot">
      {children}
    </group>
  );
}
