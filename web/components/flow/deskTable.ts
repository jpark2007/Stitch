// The book desk's table, beyond the book's own canvas.
//
// On a wide window (/add, /traditions/new) the book's WebGL canvas covers the
// right of the window and the writing lies on the left. The canvas draws the
// book's wooden table under its own camera; this file works out where that
// same table lies on the rest of the window, so the page can draw it there
// too (BookDesk: plank bands of the same wood texture, with the same seams,
// grain scale and light) and the two meet with no seam.
//
// Pure maths, no React or three, so it is cheap on every resize:
//
//   const layout = deskLayout(innerWidth, innerHeight);  // the book's box and the canvas inside it
//   const bands = tableBands(layout, innerHeight);       // one per plank in view, top to bottom
//
// The camera and table here mirror the book's own (read-only) code: the
// controller's frameCamera for an open book looking at the whole spread with
// no parallax (Book3D `still`), and Book3D's table plane (30 x 30, its wood
// texture repeated 11 x 11, five planks to a tile; paper/materials.ts). If
// those change, the drawn planks drift from the canvas's at the join; nothing
// else depends on them.

const DEG = Math.PI / 180;

// controller.ts / leafShape.ts: PAGE_W 1, PAGE_H 1.3, COVER_OVERHANG 0.045.
const BOARD_W = 1 + 0.045;
const BOARD_D = 1.3 + 2 * 0.045;
/** Book3D's camera fov (vertical, degrees). */
const FOV = 30;

// Book3D's table: a 30-unit square plane, the wood repeated 11 times across,
// five planks to a 512 px tile.
const PLANE = 30;
const REPEAT = 11;
const TILE_PX = 512;
const PLANKS = 5;
/** World units per plank (along z) and per texel (along x). */
const PLANK_Z = PLANE / REPEAT / PLANKS;
const TEXEL_X = PLANE / (REPEAT * TILE_PX);

/** From this width the split is 30/70; below it (from 1024) 40/60. Matches Tailwind's `xl`. */
export const DESK_XL = 1280;
/** The writing's share of the window. */
export function writingShare(width: number): number {
  return width >= DESK_XL ? 0.3 : 0.4;
}
/**
 * The canvas is wider than the book's area by this much (it runs on under
 * the writing on the left and past the window on the right): the book's
 * camera frames the spread to about 82% of the canvas's width, and this
 * brings the spread to about 92% of its area.
 */
export const BLEED = 1.125;
/** Never squarer than this: the camera keeps the whole spread in view (below ~0.9 it reads one page). */
const MIN_ASPECT = 1.05;

export interface DeskLayout {
  /** The book's area (the aside), window px. */
  area: { x: number; width: number; height: number };
  /** The canvas, relative to the area. */
  canvas: { x: number; y: number; width: number; height: number };
}

/** Where the book's area and its canvas lie on a wide window. */
export function deskLayout(width: number, height: number): DeskLayout {
  const ax = Math.round(width * writingShare(width));
  const aw = width - ax;
  const cw = Math.round(aw * BLEED);
  const ch = Math.min(height, Math.round(cw / MIN_ASPECT));
  return {
    area: { x: ax, width: aw, height },
    canvas: { x: Math.round((aw - cw) / 2), y: Math.round((height - ch) / 2), width: cw, height: ch },
  };
}

interface Camera {
  /** Position and target (world). */
  py: number;
  pz: number;
  ty: number;
  tz: number;
  /** Unit forward and up (y, z; x is 0: the camera looks straight down the table). */
  fy: number;
  fz: number;
  uy: number;
  uz: number;
  tanV: number;
}

/** controller.ts frameCamera, for an open book, the whole spread, no lift or parallax. */
export function bookCamera(aspect: number): Camera {
  const tanV = Math.tan((FOV * DEG) / 2);
  const tanH = tanV * aspect;
  const portrait = aspect < 1;
  const e = (portrait ? 66 : 58) * DEG;
  const halfDO = BOARD_D / 2 + 0.05;
  const halfW = BOARD_W + (portrait ? 0.06 : 0.33);
  const d = Math.max(halfW / tanH, (halfDO * Math.sin(e) + 0.16) / tanV);
  const ty = 0.03;
  const tz = portrait ? 0 : 0.07;
  const py = ty + d * Math.sin(e);
  const pz = tz + d * Math.cos(e);
  return { py, pz, ty, tz, fy: -Math.sin(e), fz: -Math.cos(e), uy: Math.cos(e), uz: -Math.sin(e), tanV };
}

/** Depth along the view and screen y (px from the canvas's top) of the table line at world z. */
function project(cam: Camera, z: number, ch: number): { depth: number; y: number } {
  const ry = 0 - cam.py;
  const rz = z - cam.pz;
  const depth = ry * cam.fy + rz * cam.fz;
  const up = ry * cam.uy + rz * cam.uz;
  return { depth, y: (ch / 2) * (1 - up / (depth * cam.tanV)) };
}

/** One plank of the table as the window shows it: a band of the wood texture. */
export interface TableBand {
  /** Window px. */
  top: number;
  height: number;
  /** The texture row (0..512) at the band's top: the plank's first row. */
  row: number;
  /** Texture px to window px, across and down. */
  sx: number;
  sy: number;
  /** Window x of the texture's column 0 (any whole tile left of the canvas's centre). */
  x0: number;
}

/**
 * The table's planks from the window's top to its bottom, as the book's
 * camera sees them: seams where the canvas has them, each plank's grain
 * scaled to its distance. Outside the canvas the same table runs on.
 */
export function tableBands(layout: DeskLayout, windowHeight: number): TableBand[] {
  const { area, canvas } = layout;
  const cam = bookCamera(canvas.width / canvas.height);
  const top = canvas.y; // window y of the canvas's top (the area spans the window's height)
  const cx = area.x + canvas.x + canvas.width / 2;
  // Plank seams lie at z = PLANK_Z * (n + 1/2) (the plane's v runs from its
  // far edge; 15 / PLANK_Z is a whole number and a half).
  const bands: TableBand[] = [];
  // From the nearest seam beyond the window's top to the first past its bottom.
  let n = Math.floor(-PLANE / 2 / PLANK_Z);
  for (let guard = 0; guard < 400; guard++, n++) {
    const z0 = PLANK_Z * (n + 0.5);
    const z1 = z0 + PLANK_Z;
    const a = project(cam, z0, canvas.height);
    const b = project(cam, z1, canvas.height);
    if (a.depth <= 0.05 || b.depth <= 0.05) break;
    const y0 = top + a.y;
    const y1 = top + b.y;
    if (y1 < 0) continue;
    if (y0 > windowHeight) break;
    // The texture row at z0: v = REPEAT (15 - z) / 30 (fraction), row = (1 - v) * 512.
    const v = (((REPEAT * (PLANE / 2 - z0)) / PLANE) % 1 + 1) % 1;
    const row = Math.round(((1 - v) * TILE_PX) % TILE_PX * 10) / 10;
    // Scale across at the plank's middle distance.
    const mid = (a.depth + b.depth) / 2;
    const pxPerUnit = canvas.height / 2 / (mid * cam.tanV);
    const sx = pxPerUnit * TEXEL_X;
    // u = REPEAT (x + 15) / 30: column 256 at x = 0.
    const col0 = (((REPEAT * (PLANE / 2)) / PLANE) % 1) * TILE_PX;
    bands.push({ top: y0, height: y1 - y0, row, sx, sy: (y1 - y0) / (TILE_PX / PLANKS), x0: cx - col0 * sx });
  }
  return bands;
}
