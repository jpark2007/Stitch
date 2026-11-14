"use client";

/**
 * What is printed on each leaf, and the stage slot on the right page.
 *
 * Spread j (1-based) shows the verso of leaf j-1 on the left (the story page:
 * year, title, storybook text, tellers and taped-in photos) and the recto of
 * leaf j on the right (an empty, clearly marked stage slot where the pop-up
 * stage mounts; see StageSlotArgs). Leaf 0's recto is the title page, seen as the book opens.
 *
 * Pages are drawn to canvases (pageArt.ts) so the print bends with the paper.
 */

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { CanvasTexture, ClampToEdgeWrapping, RepeatWrapping, SRGBColorSpace, type Group } from "three";
import { COVER_OVERHANG, PAGE_H, PAGE_W } from "./leafShape";
import { drawContactShadow, drawCoverArt, drawEndpaper, drawPageEdges, drawSpineCloth } from "./coverArt";
import { drawBlankPage, drawStagePage, drawStoryPage, drawTitlePage, PAGE_PX_W, STAGE_SLOT_PX } from "./pageArt";
import type { LabelRect } from "./labelArt";
import { loadPageFonts, type PageFonts } from "./paperArt";
import type { StageShot } from "./shot";
import type { BookCover, BookSpread } from "./types";

export interface BookArt {
  /**
   * Per leaf: its recto and verso. `labels` are the verso's teller labels
   * (page px), and `active` the telling drawn as playing there.
   */
  leaves: { front: HTMLCanvasElement; back: HTMLCanvasElement; labels: LabelRect[]; active: string | null }[];
  fonts: PageFonts;
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

/** Spread i's story page (leaf i's verso), with `active` drawn as the telling playing. */
export function drawSpreadStory(spread: BookSpread, i: number, fonts: PageFonts, active: string | null) {
  return drawStoryPage(spread, 2 * (i + 1), fonts, 30 + i, active);
}

/**
 * Draw every page of the book (fonts first). Browser-only. `tellings`
 * (spread id -> teller id) says which telling each story page shows playing.
 */
export async function buildBookArt(
  spreads: BookSpread[],
  cover: BookCover,
  tellings: Readonly<Record<string, string>> = {},
): Promise<BookArt> {
  const fonts = await loadPageFonts();
  const n = spreads.length;
  const leaves: BookArt["leaves"] = [];
  for (let i = 0; i <= n; i++) {
    const front = i === 0 ? drawTitlePage(cover, fonts, 3) : drawStagePage(spreads[i - 1], 2 * i + 1, fonts, 10 + i);
    await yieldTask();
    const active = i < n ? (tellings[spreads[i].id] ?? null) : null;
    const story = i < n ? await drawSpreadStory(spreads[i], i, fonts, active) : null;
    await yieldTask();
    leaves.push({ front, back: story?.canvas ?? drawBlankPage(90, "right"), labels: story?.labels ?? [], active });
  }
  const bw = 860;
  const bh = Math.round((bw * (PAGE_H + 2 * COVER_OVERHANG)) / (PAGE_W + COVER_OVERHANG));
  const coverOutside = drawCoverArt(cover, fonts, bw, bh);
  await yieldTask();
  return {
    leaves,
    fonts,
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

/** The stage slot in world units, relative to the right page (x from the spine, z from the page's centre line). */
const PX = PAGE_W / PAGE_PX_W;
export const STAGE_SLOT = {
  width: (STAGE_SLOT_PX.x1 - STAGE_SLOT_PX.x0) * PX,
  depth: (STAGE_SLOT_PX.y1 - STAGE_SLOT_PX.y0) * PX,
  x: ((STAGE_SLOT_PX.x0 + STAGE_SLOT_PX.x1) / 2) * PX,
  z: ((STAGE_SLOT_PX.y0 + STAGE_SLOT_PX.y1) / 2) * PX - PAGE_H / 2,
} as const;

/** What a spread's pop-up stage is given (close to v2's StageSlotState). */
export interface StageSlotArgs {
  spread: BookSpread;
  /** 0-based spread index. */
  index: number;
  /**
   * 0..1, how much the stage page is the page being read, as a live ref
   * (read it in useFrame): 1 with the whole spread in view; on a phone,
   * which reads one page at a time, it falls toward 0 as the camera pans
   * to the story page. A stage can wait for it before it plays.
   */
  view: { readonly current: number };
  /** The page fonts, loaded: for anything the stage prints on the page. */
  fonts: PageFonts;
  /**
   * 0..1, how far the pop-up should stand, as a live ref: read
   * `reveal.current` in useFrame (it changes every frame without a
   * re-render). It rises once this spread's stage page lies uncovered and
   * still, and falls quickly as a leaf lifts off it or comes down over it.
   * During a turn the outgoing spread's stage folds while the incoming one
   * rises, so two stages can be mounted at once; fold flat at 0.
   */
  reveal: { readonly current: number };
  /**
   * The camera this stage offers the book (shot.ts), written each frame in
   * world space after the stage ticks: set `want` to 1 while its telling
   * plays and the book's camera eases toward it (a phone only on the stage
   * page; reduced motion never), back out at 0, a page turn, or a tap.
   */
  shot: StageShot;
  /** Slot size in world units: x across the page, z down it (one page is PAGE_W × PAGE_H). */
  width: number;
  depth: number;
}

/**
 * The right page's stage slot. Children are placed in slot space: origin at
 * the centre of the marked slot on the right page's surface, x across the
 * page (`STAGE_SLOT.width` wide), z down the page (`STAGE_SLOT.depth`, +z
 * toward the reader), y up off the paper.
 */
export function StageSlot({ children }: { children: ReactNode }) {
  return (
    <group position={[STAGE_SLOT.x, 0, STAGE_SLOT.z]} name="stage-slot">
      {children}
    </group>
  );
}

/**
 * A stand-in pop-up for tests (?debug=1): a card hinged at the slot's centre
 * line that stands with `reveal` and folds flat at 0, outlined to the slot.
 */
export function SlotProbe({ reveal, width, depth }: StageSlotArgs) {
  const card = useRef<Group>(null);
  useFrame(() => {
    if (card.current) card.current.rotation.x = -(Math.PI / 2) * (1 - reveal.current);
  });
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} position-y={0.0008}>
        <planeGeometry args={[width, depth]} />
        <meshBasicMaterial color="#b9573d" transparent opacity={0.08} depthWrite={false} />
      </mesh>
      <group ref={card} rotation-x={-Math.PI / 2}>
        <mesh position-y={depth * 0.17} castShadow>
          <boxGeometry args={[width * 0.6, depth * 0.34, 0.006]} />
          <meshStandardMaterial color="#efe3cc" roughness={0.9} />
        </mesh>
      </group>
    </group>
  );
}
