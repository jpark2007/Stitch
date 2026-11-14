/**
 * The paper kits that pop up on a tradition's right page, one per category:
 * a few hand-cut card pieces (stage/cutout.ts painters, flat paper colours,
 * layered like glued card), placed in stage units (the stage is 2 wide, its
 * back fold at z = 0, a puppet 0.5 tall), and the small things that keep
 * them alive (steam off the pot, lights on the tree, the fire's flames).
 *
 *   food      a kitchen wall, a laid table with a steaming pot
 *   holiday   a snowy night, a tree with twinkling lights, presents
 *   trip      mountains, a lake, a tent and a flickering campfire
 *   music     a parlour, an upright piano, notes drifting up
 *   game      a bookshelf wall, a card table, dice hopping
 *   faith     a chapel window, candles flickering
 *   craft     a sewing corner, a hat on its stand, bunting swaying
 *   other     a garden, a round tree, bunting swaying
 *
 * Painters work in piece units: origin bottom-left, y up, height 1, width
 * `aspect`; the bottom row is the hinge fold (see stage/cutout.ts).
 */

import type { CutoutSpec, PaintKit } from "@/components/stage/cutout";
import type { Tradition } from "@/lib/types";

export type Category = Tradition["category"];

/* ------------------------------------------------------------------------ */
/* Paper colours (the stage's own, stage/art.ts PALETTE, and a few more)     */
/* ------------------------------------------------------------------------ */

const C = {
  cream: "#f8f2e6",
  wall: "#f0e2c8",
  wallDeep: "#e4d2b2",
  wainscot: "#d9bb87",
  wood: "#7c5b40",
  woodDark: "#5e4330",
  woodLight: "#9a7552",
  red: "#b9573d",
  redDeep: "#963f2c",
  rust: "#d98d6b",
  petal: "#f1b9a6",
  ochre: "#d39a2c",
  sun: "#e6b45c",
  lit: "#f3d488",
  teal: "#4e6a69",
  tealDeep: "#3b5452",
  sky: "#c4d6da",
  skyPale: "#dfe8e6",
  night: "#3e5560",
  nightDeep: "#2f434c",
  leaf: "#8ba278",
  leafLight: "#a5b78f",
  leafDeep: "#6c8660",
  pine: "#56704f",
  hillFar: "#cdd3b4",
  hillNear: "#b3bf98",
  stone: "#dfd3bd",
  stoneLine: "#c7b89c",
  mountain: "#a9b5ae",
  mountainDeep: "#8e9c95",
  water: "#9fbdd6",
  waterLight: "#d4e0ea",
  plum: "#8b5a7c",
  blue: "#5b82a6",
  ink: "#2f2620",
} as const;

/* ------------------------------------------------------------------------ */
/* Shapes                                                                    */
/* ------------------------------------------------------------------------ */

type Pt = [number, number];

function poly(pts: Pt[], into = new Path2D()) {
  into.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) into.lineTo(pts[i][0], pts[i][1]);
  into.closePath();
  return into;
}

function rect(x0: number, y0: number, x1: number, y1: number, into = new Path2D()) {
  return poly([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], into);
}

function circle(cx: number, cy: number, r: number, into = new Path2D()) {
  into.moveTo(cx + r, cy);
  into.arc(cx, cy, r, 0, Math.PI * 2);
  return into;
}

function ellipse(cx: number, cy: number, rx: number, ry: number, into = new Path2D()) {
  into.moveTo(cx + rx, cy);
  into.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
  return into;
}

function rounded(x0: number, y0: number, x1: number, y1: number, r: number, into = new Path2D()) {
  into.roundRect(x0, y0, x1 - x0, y1 - y0, r);
  return into;
}

/** A round-topped window or door. */
function arch(cx: number, y0: number, w: number, h: number, into = new Path2D()) {
  const r = w / 2;
  into.moveTo(cx - r, y0);
  into.lineTo(cx - r, y0 + h - r);
  into.arc(cx, y0 + h - r, r, Math.PI, 0, true);
  into.lineTo(cx + r, y0);
  into.closePath();
  return into;
}

/** A rolling line from x0 to x1 through `crests`, closed down past the fold. */
function hills(x0: number, x1: number, base: number, crests: Pt[]) {
  const p = new Path2D();
  p.moveTo(x0, -0.05);
  p.lineTo(x0, base);
  let [px, py] = [x0, base];
  for (const [x, y] of crests) {
    const mx = (px + x) / 2;
    p.bezierCurveTo(mx, py, mx, y, x, y);
    [px, py] = [x, y];
  }
  p.bezierCurveTo((px + x1) / 2, py, (px + x1) / 2, base, x1, base);
  p.lineTo(x1, -0.05);
  p.closePath();
  return p;
}

/** The backdrop card: square shoulders easing into a soft arch, as a pop-up's back panel. */
function backCard(w: number, m = 0.03, top = 0.975) {
  const p = new Path2D();
  p.moveTo(m, -0.05);
  p.lineTo(m, 0.62);
  p.bezierCurveTo(m, 0.88, w * 0.24, top, w / 2, top);
  p.bezierCurveTo(w * 0.76, top, w - m, 0.88, w - m, 0.62);
  p.lineTo(w - m, -0.05);
  p.closePath();
  return p;
}

/** A five-pointed star. */
function star(cx: number, cy: number, r: number, into = new Path2D()) {
  const pts: Pt[] = [];
  for (let i = 0; i < 10; i++) {
    const a = Math.PI / 2 + (i * Math.PI) / 5;
    const rr = i % 2 ? r * 0.45 : r;
    pts.push([cx + Math.cos(a) * rr, cy + Math.sin(a) * rr]);
  }
  return poly(pts, into);
}

/** The string the bunting flags hang from (the flags are cut separately and sway). */
function buntingString(k: PaintKit, w: number) {
  const line = new Path2D();
  line.moveTo(w / 2 - 0.68, 0.87);
  line.quadraticCurveTo(w / 2, 0.71, w / 2 + 0.68, 0.87);
  k.line(line, C.woodDark, 0.008);
}

/* ------------------------------------------------------------------------ */
/* Food: the kitchen on the night of the tamales                             */
/* ------------------------------------------------------------------------ */

const FOOD_WALL_ASPECT = 1.75;
const foodWall: CutoutSpec = {
  key: "tradition:food:wall",
  aspect: FOOD_WALL_ASPECT,
  base: C.wall,
  seed: 101,
  paint(k) {
    const w = k.aspect;
    k.shape(backCard(w), C.wall);
    k.layer(rect(0.03, -0.05, w - 0.03, 0.26), C.wainscot, 0.5);
    k.layer(rect(0.03, 0.255, w - 0.03, 0.28), C.woodLight, 0.4);
    // The window, its sky and the curtains drawn back.
    const wx = w * 0.5;
    k.layer(rect(wx - 0.25, 0.4, wx + 0.25, 0.84), C.cream, 0.7);
    k.layer(rect(wx - 0.21, 0.44, wx + 0.21, 0.8), C.sky, 0.3);
    k.layer(circle(wx + 0.1, 0.7, 0.05), C.lit, 0.4);
    k.layer(rect(wx - 0.008, 0.44, wx + 0.008, 0.8), C.cream, 0.3);
    k.layer(rect(wx - 0.21, 0.615, wx + 0.21, 0.63), C.cream, 0.3);
    for (const s of [-1, 1]) {
      const p = new Path2D();
      p.moveTo(wx + s * 0.28, 0.86);
      p.lineTo(wx + s * 0.16, 0.86);
      p.bezierCurveTo(wx + s * 0.19, 0.7, wx + s * 0.25, 0.55, wx + s * 0.22, 0.4);
      p.lineTo(wx + s * 0.29, 0.4);
      p.closePath();
      k.layer(p, C.rust, 0.8);
    }
    k.layer(rect(wx - 0.31, 0.855, wx + 0.31, 0.875), C.woodDark, 0.6);
    // A shelf of jars on the right.
    const sx = w * 0.8;
    k.layer(rect(sx - 0.17, 0.55, sx + 0.17, 0.57), C.wood, 0.6);
    const jars: [number, number, string][] = [[-0.12, 0.09, C.ochre], [-0.04, 0.12, C.teal], [0.05, 0.08, C.red], [0.12, 0.1, C.leaf]];
    for (const [dx, h, col] of jars) {
      k.layer(rounded(sx + dx - 0.03, 0.57, sx + dx + 0.03, 0.57 + h, 0.012), col, 0.6);
      k.layer(rect(sx + dx - 0.032, 0.57 + h - 0.015, sx + dx + 0.032, 0.57 + h), C.cream, 0.3);
    }
    // Spoons and a ladle hung on the left.
    const hx = w * 0.18;
    k.layer(rect(hx - 0.14, 0.73, hx + 0.14, 0.745), C.wood, 0.5);
    for (const [dx, len, r] of [[-0.09, 0.2, 0.025], [0, 0.26, 0.04], [0.09, 0.18, 0.022]] as const) {
      k.layer(rect(hx + dx - 0.006, 0.73 - len, hx + dx + 0.006, 0.735), C.woodDark, 0.4);
      k.layer(ellipse(hx + dx, 0.73 - len, r, r * 1.2), C.woodDark, 0.5);
    }
  },
};

const FOOD_TABLE_ASPECT = 1.7;
/** Where the pot's mouth is on the table piece (piece units from its centre, up from the fold). */
export const FOOD_POT: Pt = [-0.16, 0.9];
const foodTable: CutoutSpec = {
  key: "tradition:food:table",
  aspect: FOOD_TABLE_ASPECT,
  base: C.red,
  seed: 113,
  paint(k) {
    const w = k.aspect;
    // Legs, then the cloth with a scalloped hem.
    k.shape(rect(0.2, -0.05, 0.27, 0.3), C.woodDark);
    k.shape(rect(w - 0.27, -0.05, w - 0.2, 0.3), C.woodDark);
    const cloth = new Path2D();
    cloth.moveTo(0.06, 0.48);
    cloth.lineTo(w - 0.06, 0.48);
    cloth.lineTo(w - 0.06, 0.24);
    const n = 9;
    for (let i = n; i > 0; i--) {
      const x0 = 0.06 + ((w - 0.12) * i) / n;
      const x1 = 0.06 + ((w - 0.12) * (i - 1)) / n;
      cloth.quadraticCurveTo((x0 + x1) / 2, 0.17, x1, 0.24);
    }
    cloth.closePath();
    k.shape(cloth, C.red);
    for (let i = 1; i < 7; i++) {
      const x = 0.06 + ((w - 0.12) * i) / 7;
      k.layer(rect(x - 0.012, 0.25, x + 0.012, 0.48), C.cream, 0.15);
    }
    k.layer(rect(0.06, 0.44, w - 0.06, 0.48), C.redDeep, 0.2);
    // The big pot.
    const px = w / 2 + FOOD_POT[0];
    k.shape(rounded(px - 0.2, 0.47, px + 0.2, 0.86, 0.06), C.teal);
    k.layer(rect(px - 0.22, 0.82, px + 0.22, 0.87), C.tealDeep, 0.5);
    k.shape(circle(px - 0.23, 0.74, 0.035), C.tealDeep);
    k.shape(circle(px + 0.23, 0.74, 0.035), C.tealDeep);
    k.layer(rect(px - 0.2, 0.6, px + 0.2, 0.63), C.cream, 0.2);
    // A platter of tamales and a stack of plates.
    const tx = w * 0.76;
    k.shape(ellipse(tx, 0.5, 0.2, 0.04), C.cream);
    for (const [dx, dy] of [[-0.1, 0.53], [0, 0.53], [0.1, 0.53], [-0.05, 0.6], [0.05, 0.6], [0, 0.67]] as const) {
      k.shape(rounded(tx + dx - 0.05, dy - 0.03, tx + dx + 0.05, dy + 0.04, 0.03), C.lit);
      k.layer(rect(tx + dx - 0.006, dy - 0.03, tx + dx + 0.006, dy + 0.04), C.ochre, 0.2);
    }
    const bx = w * 0.17;
    for (let i = 0; i < 4; i++) k.shape(ellipse(bx, 0.5 + i * 0.03, 0.11, 0.025), i % 2 ? C.cream : C.stone);
  },
};

/* ------------------------------------------------------------------------ */
/* Holiday: the tree on a snowy night                                        */
/* ------------------------------------------------------------------------ */

const HOLIDAY_SKY_ASPECT = 1.75;
const holidaySky: CutoutSpec = {
  key: "tradition:holiday:sky",
  aspect: HOLIDAY_SKY_ASPECT,
  base: C.night,
  seed: 131,
  paint(k) {
    const w = k.aspect;
    k.shape(backCard(w), C.night);
    k.layer(hills(0.03, w - 0.03, 0.5, [[w * 0.3, 0.56], [w * 0.7, 0.5], [w * 0.92, 0.55]]), C.nightDeep, 0.3);
    k.layer(circle(w * 0.78, 0.8, 0.07), C.lit, 0.6);
    k.layer(circle(w * 0.8, 0.82, 0.06), C.night, 0.1);
    for (let i = 0; i < 22; i++) {
      const x = 0.12 + k.rand() * (w - 0.24);
      const y = 0.5 + k.rand() * 0.4;
      k.layer(star(x, y, 0.008 + k.rand() * 0.012), C.cream, 0.2);
    }
    // A house far off with its windows lit.
    const hx = w * 0.18;
    k.layer(rect(hx - 0.1, 0.3, hx + 0.1, 0.46), C.wallDeep, 0.5);
    k.layer(poly([[hx - 0.13, 0.45], [hx, 0.56], [hx + 0.13, 0.45]]), C.redDeep, 0.5);
    k.layer(rect(hx - 0.06, 0.36, hx - 0.02, 0.41), C.lit, 0.2);
    k.layer(rect(hx + 0.02, 0.36, hx + 0.06, 0.41), C.lit, 0.2);
    k.layer(hills(0.03, w - 0.03, 0.3, [[w * 0.22, 0.36], [w * 0.55, 0.28], [w * 0.85, 0.34]]), C.cream, 0.8);
    k.layer(hills(0.03, w - 0.03, 0.16, [[w * 0.35, 0.2], [w * 0.75, 0.15]]), C.skyPale, 0.8);
  },
};

const TREE_ASPECT = 0.8;
/** Lights on the holiday tree: [u, v] in piece units from its centre line, up from the fold. */
export const TREE_LIGHTS: Pt[] = [
  [-0.03, 0.78], [0.06, 0.7], [-0.09, 0.63],
  [0.12, 0.55], [-0.02, 0.52], [-0.17, 0.44],
  [0.08, 0.4], [0.22, 0.32], [-0.08, 0.3], [-0.26, 0.24],
  [0.14, 0.2], [-0.15, 0.17], [0.3, 0.15], [0.02, 0.14],
];
const holidayTree: CutoutSpec = {
  key: "tradition:holiday:tree",
  aspect: TREE_ASPECT,
  base: C.pine,
  seed: 137,
  crease: true,
  paint(k) {
    const cx = k.aspect / 2;
    k.shape(rect(cx - 0.05, -0.05, cx + 0.05, 0.12), C.wood);
    const tiers: [number, number, number, string][] = [
      [0.08, 0.42, 0.39, C.pine],
      [0.32, 0.64, 0.31, C.leafDeep],
      [0.53, 0.86, 0.22, C.leaf],
    ];
    for (const [y0, y1, r, col] of tiers) {
      const p = new Path2D();
      p.moveTo(cx - r, y0);
      p.quadraticCurveTo(cx, y0 - 0.04, cx + r, y0);
      p.lineTo(cx, y1);
      p.closePath();
      k.shape(p, col);
    }
    // Garlands of paper chain.
    for (const [y, r] of [[0.2, 0.3], [0.42, 0.22], [0.62, 0.14]] as const) {
      const g = new Path2D();
      g.moveTo(cx - r, y + 0.04);
      g.quadraticCurveTo(cx, y - 0.04, cx + r, y + 0.06);
      k.line(g, C.cream, 0.012);
    }
    k.shape(star(cx, 0.9, 0.075), C.sun);
  },
};

const GIFTS_ASPECT = 1.9;
const holidayGifts: CutoutSpec = {
  key: "tradition:holiday:gifts",
  aspect: GIFTS_ASPECT,
  base: C.red,
  seed: 139,
  paint(k) {
    const boxes: [number, number, number, string, string][] = [
      [0.12, 0.42, 0.62, C.red, C.cream],
      [0.48, 0.9, 0.8, C.teal, C.sun],
      [0.92, 1.18, 0.5, C.ochre, C.red],
      [1.24, 1.62, 0.7, C.plum, C.cream],
      [1.58, 1.82, 0.42, C.cream, C.red],
    ];
    for (const [x0, x1, h, col, ribbon] of boxes) {
      k.shape(rect(x0, -0.05, x1, h), col);
      const mx = (x0 + x1) / 2;
      k.layer(rect(mx - 0.018, -0.05, mx + 0.018, h), ribbon, 0.3);
      k.layer(rect(x0, h * 0.55 - 0.018, x1, h * 0.55 + 0.018), ribbon, 0.3);
      k.shape(ellipse(mx - 0.05, h + 0.04, 0.05, 0.035), ribbon);
      k.shape(ellipse(mx + 0.05, h + 0.04, 0.05, 0.035), ribbon);
    }
  },
};

/* ------------------------------------------------------------------------ */
/* Trip: the camp by the lake                                                */
/* ------------------------------------------------------------------------ */

const TRIP_SKY_ASPECT = 1.75;
const tripSky: CutoutSpec = {
  key: "tradition:trip:sky",
  aspect: TRIP_SKY_ASPECT,
  base: C.sky,
  seed: 151,
  paint(k) {
    const w = k.aspect;
    k.shape(backCard(w), C.sky);
    k.layer(circle(w * 0.24, 0.76, 0.08), C.sun, 0.6);
    const peaks: [number, number, number, string][] = [
      [w * 0.36, 0.78, 0.42, C.mountainDeep],
      [w * 0.66, 0.86, 0.48, C.mountain],
      [w * 0.9, 0.7, 0.34, C.mountainDeep],
    ];
    for (const [px, py, r, col] of peaks) {
      k.layer(poly([[px - r, 0.2], [px, py], [px + r, 0.2]]), col, 0.6);
      k.layer(poly([[px - r * 0.24, py - 0.13], [px, py], [px + r * 0.24, py - 0.13], [px + 0.04, py - 0.1], [px - 0.03, py - 0.12]]), C.cream, 0.3);
    }
    k.layer(hills(0.03, w - 0.03, 0.26, [[w * 0.25, 0.32], [w * 0.6, 0.27], [w * 0.88, 0.31]]), C.hillNear, 0.7);
    // A row of pines along the shore.
    for (let i = 0; i < 14; i++) {
      const x = 0.08 + (i / 13) * (w - 0.16) + (k.rand() - 0.5) * 0.04;
      const h = 0.14 + k.rand() * 0.1;
      k.layer(poly([[x - h * 0.3, 0.1], [x, 0.1 + h * 1.4], [x + h * 0.3, 0.1]]), i % 3 ? C.pine : C.leafDeep, 0.5);
    }
  },
};

const LAKE_ASPECT = 2.1;
const tripLake: CutoutSpec = {
  key: "tradition:trip:lake",
  aspect: LAKE_ASPECT,
  base: C.water,
  seed: 157,
  deckle: 1.4,
  paint(k) {
    const w = k.aspect;
    const p = new Path2D();
    p.moveTo(0.1, 0.5);
    p.bezierCurveTo(0.1, 0.92, w * 0.4, 0.98, w * 0.6, 0.9);
    p.bezierCurveTo(w * 0.86, 0.82, w - 0.05, 0.7, w - 0.08, 0.46);
    p.bezierCurveTo(w - 0.1, 0.16, w * 0.6, 0.04, w * 0.42, 0.08);
    p.bezierCurveTo(w * 0.2, 0.12, 0.1, 0.22, 0.1, 0.5);
    p.closePath();
    k.shape(p, C.water);
    for (const [x, y, len] of [[0.5, 0.6, 0.3], [0.9, 0.35, 0.4], [1.3, 0.62, 0.26], [0.7, 0.3, 0.2], [1.5, 0.4, 0.22]] as const) {
      k.layer(rounded(x, y, x + len, y + 0.025, 0.012), C.waterLight, 0.2);
    }
  },
};

const TENT_ASPECT = 1.2;
const tripTent: CutoutSpec = {
  key: "tradition:trip:tent",
  aspect: TENT_ASPECT,
  base: C.ochre,
  seed: 163,
  crease: true,
  paint(k) {
    const cx = k.aspect / 2;
    k.shape(poly([[0.04, -0.05], [0.04, 0.02], [cx, 0.92], [k.aspect - 0.04, 0.02], [k.aspect - 0.04, -0.05]]), C.ochre);
    k.layer(poly([[cx, 0.92], [k.aspect - 0.04, 0.02], [k.aspect - 0.04, -0.02], [cx + 0.04, 0.84]]), C.sun, 0.3);
    k.layer(poly([[cx - 0.2, 0.0], [cx, 0.6], [cx + 0.2, 0.0]]), C.woodDark, 0.6);
    k.layer(poly([[cx, 0.6], [cx + 0.2, 0.0], [cx + 0.27, 0.0], [cx + 0.02, 0.58]]), C.rust, 0.7);
    k.shape(rect(cx - 0.01, 0.9, cx + 0.01, 1.0), C.woodDark);
    k.shape(poly([[cx + 0.01, 0.99], [cx + 0.1, 0.96], [cx + 0.01, 0.93]]), C.red);
  },
};

const FIRE_ASPECT = 1.6;
/** The fire's heart on the campfire piece. */
export const FIRE_HEART: Pt = [0, 0.42];
const tripFire: CutoutSpec = {
  key: "tradition:trip:fire",
  aspect: FIRE_ASPECT,
  base: C.stone,
  seed: 167,
  paint(k) {
    const cx = k.aspect / 2;
    // Logs crossed over a ring of stones.
    const log = (a: number) => {
      const p = new Path2D();
      const dx = Math.cos(a) * 0.42;
      const dy = Math.sin(a) * 0.42;
      p.moveTo(cx - dx, 0.12 - dy * 0.3);
      p.lineTo(cx + dx, 0.12 + dy * 0.3 + 0.18);
      return p;
    };
    k.line(log(0.25), C.woodDark, 0.11);
    k.line(log(Math.PI - 0.25), C.wood, 0.11);
    for (let i = 0; i < 7; i++) {
      const x = 0.12 + (i / 6) * (k.aspect - 0.24);
      k.shape(ellipse(x, 0.04, 0.09, 0.08), i % 2 ? C.stone : C.stoneLine);
    }
  },
};

/* ------------------------------------------------------------------------ */
/* Music: the piano in the parlour                                           */
/* ------------------------------------------------------------------------ */

const PARLOUR_ASPECT = 1.75;
const musicWall: CutoutSpec = {
  key: "tradition:music:wall",
  aspect: PARLOUR_ASPECT,
  base: C.wallDeep,
  seed: 171,
  paint(k) {
    const w = k.aspect;
    k.shape(backCard(w), C.wallDeep);
    for (let i = 1; i < 14; i++) {
      const x = 0.03 + (i / 14) * (w - 0.06);
      k.layer(rect(x - 0.012, -0.05, x + 0.012, 0.92), C.wall, 0.1);
    }
    k.layer(rect(0.03, -0.05, w - 0.03, 0.22), C.woodLight, 0.5);
    // Family pictures in gilt frames.
    const frames: [number, number, number, number, string][] = [
      [w * 0.14, 0.62, 0.1, 0.13, C.sky],
      [w * 0.86, 0.6, 0.11, 0.14, C.leafLight],
      [w * 0.27, 0.74, 0.07, 0.09, C.petal],
      [w * 0.74, 0.76, 0.07, 0.08, C.skyPale],
    ];
    for (const [x, y, rx, ry, col] of frames) {
      k.layer(rect(x - rx, y - ry, x + rx, y + ry), C.ochre, 0.7);
      k.layer(rect(x - rx + 0.02, y - ry + 0.02, x + rx - 0.02, y + ry - 0.02), col, 0.2);
      k.layer(circle(x, y + ry * 0.1, ry * 0.35), C.woodLight, 0.2);
    }
    k.layer(arch(w * 0.5, 0.42, 0.34, 0.5), C.cream, 0.6);
    k.layer(arch(w * 0.5, 0.45, 0.28, 0.44), C.sky, 0.2);
  },
};

const PIANO_ASPECT = 1.3;
/** The top of the piano, where the notes start. */
export const PIANO_TOP: Pt = [0.05, 1.02];
const musicPiano: CutoutSpec = {
  key: "tradition:music:piano",
  aspect: PIANO_ASPECT,
  base: C.woodDark,
  seed: 173,
  paint(k) {
    const w = k.aspect;
    k.shape(rect(0.06, -0.05, w - 0.06, 0.92), C.woodDark);
    k.layer(rect(0.04, 0.9, w - 0.04, 0.96), C.wood, 0.6);
    k.layer(rect(0.14, 0.58, w - 0.14, 0.86), C.wood, 0.4);
    k.layer(rect(0.22, 0.62, w - 0.22, 0.82), C.woodLight, 0.2);
    // The music on its stand.
    k.layer(rect(w / 2 - 0.16, 0.66, w / 2 + 0.16, 0.86), C.cream, 0.7);
    for (let i = 0; i < 4; i++) k.layer(rect(w / 2 - 0.13, 0.7 + i * 0.04, w / 2 + 0.13, 0.705 + i * 0.04), C.stoneLine, 0.1);
    // Keys.
    k.layer(rect(0.02, 0.44, w - 0.02, 0.56), C.cream, 0.8);
    const n = 18;
    for (let i = 0; i < n; i++) {
      if ([2, 6, 9, 13, 16].includes(i)) continue;
      const x = 0.04 + ((w - 0.08) * (i + 0.65)) / n;
      k.layer(rect(x - 0.012, 0.49, x + 0.012, 0.56), C.ink, 0.2);
    }
    k.layer(rect(0.02, 0.4, w - 0.02, 0.45), C.wood, 0.5);
    // Candle sconces.
    for (const x of [0.12, w - 0.12]) {
      k.shape(rect(x - 0.012, 0.92, x + 0.012, 1.0), C.cream);
      k.shape(ellipse(x, 1.02, 0.012, 0.022), C.sun);
    }
    k.layer(rect(0.18, -0.05, 0.26, 0.38), C.wood, 0.3);
    k.layer(rect(w - 0.26, -0.05, w - 0.18, 0.38), C.wood, 0.3);
  },
};

/** One drifting note: a filled head, its stem and flag. */
export const noteCard: CutoutSpec = {
  key: "tradition:music:note",
  aspect: 0.7,
  base: C.ink,
  seed: 179,
  resolution: 128,
  deckle: 0.6,
  paint(k) {
    const p = new Path2D();
    ellipse(0.24, 0.2, 0.17, 0.12, p);
    k.shape(p, C.ink);
    k.shape(rect(0.36, 0.2, 0.43, 0.92), C.ink);
    const flag = new Path2D();
    flag.moveTo(0.42, 0.92);
    flag.bezierCurveTo(0.62, 0.8, 0.7, 0.66, 0.58, 0.48);
    flag.bezierCurveTo(0.62, 0.64, 0.54, 0.72, 0.42, 0.74);
    flag.closePath();
    k.shape(flag, C.ink);
  },
};

/* ------------------------------------------------------------------------ */
/* Game: cards and dice at the table                                         */
/* ------------------------------------------------------------------------ */

const GAME_WALL_ASPECT = 1.75;
const gameWall: CutoutSpec = {
  key: "tradition:game:wall",
  aspect: GAME_WALL_ASPECT,
  base: C.wall,
  seed: 181,
  paint(k) {
    const w = k.aspect;
    k.shape(backCard(w), C.skyPale);
    k.layer(rect(0.03, -0.05, w - 0.03, 0.24), C.wainscot, 0.5);
    // A bookshelf of bright spines.
    const bx0 = w * 0.08;
    const bx1 = w * 0.44;
    k.layer(rect(bx0, 0.24, bx1, 0.86), C.wood, 0.8);
    const spines = [C.red, C.teal, C.ochre, C.plum, C.leaf, C.blue, C.rust, C.cream];
    for (const y of [0.28, 0.47, 0.66]) {
      k.layer(rect(bx0 + 0.02, y, bx1 - 0.02, y + 0.17), C.woodDark, 0.2);
      let x = bx0 + 0.03;
      while (x < bx1 - 0.06) {
        const bw = 0.025 + k.rand() * 0.025;
        const bh = 0.11 + k.rand() * 0.05;
        k.layer(rect(x, y, x + bw, y + bh), spines[Math.floor(k.rand() * spines.length)], 0.3);
        x += bw + 0.006;
      }
    }
    // A clock and a window.
    k.layer(circle(w * 0.62, 0.72, 0.09), C.cream, 0.7);
    k.layer(circle(w * 0.62, 0.72, 0.075), C.wall, 0.1);
    k.line(poly([[w * 0.62, 0.72], [w * 0.62, 0.78], [w * 0.62, 0.72], [w * 0.66, 0.72]]), C.ink, 0.008);
    k.layer(rect(w * 0.74, 0.42, w * 0.93, 0.82), C.cream, 0.6);
    k.layer(rect(w * 0.76, 0.44, w * 0.91, 0.8), C.sky, 0.2);
    k.layer(rect(w * 0.835 - 0.006, 0.44, w * 0.835 + 0.006, 0.8), C.cream, 0.2);
  },
};

const GAME_TABLE_ASPECT = 1.6;
/** Where the dice hop on the card table (piece units). */
export const GAME_TOP: Pt[] = [[0.24, 0.62], [0.42, 0.62]];
const gameTable: CutoutSpec = {
  key: "tradition:game:table",
  aspect: GAME_TABLE_ASPECT,
  base: C.leafDeep,
  seed: 191,
  paint(k) {
    const w = k.aspect;
    k.shape(rect(0.16, -0.05, 0.23, 0.36), C.woodDark);
    k.shape(rect(w - 0.23, -0.05, w - 0.16, 0.36), C.woodDark);
    k.shape(rect(0.06, 0.34, w - 0.06, 0.5), C.leafDeep);
    k.layer(rect(0.06, 0.34, w - 0.06, 0.37), C.pine, 0.2);
    // A checker board leaning on the table's back edge.
    const bx = w * 0.3;
    const s = 0.05;
    k.shape(poly([[bx - 0.22, 0.5], [bx + 0.22, 0.5], [bx + 0.18, 0.86], [bx - 0.18, 0.86]]), C.woodDark);
    for (let r = 0; r < 6; r++) {
      for (let c = 0; c < 7; c++) {
        if ((r + c) % 2) continue;
        const y0 = 0.52 + r * s * 1.1;
        const inset = r * 0.006;
        const x0 = bx - 0.2 + inset + c * ((0.4 - inset * 2) / 7);
        k.layer(rect(x0, y0, x0 + (0.4 - inset * 2) / 7, y0 + s * 1.1), C.cream, 0.1);
      }
    }
    // A fan of cards.
    const cx = w * 0.72;
    for (let i = 0; i < 5; i++) {
      const a = -0.5 + i * 0.25;
      const p = new Path2D();
      const corners: Pt[] = [[-0.05, 0], [0.05, 0], [0.05, 0.2], [-0.05, 0.2]];
      const pts: Pt[] = corners.map(([x, y]) => [cx + x * Math.cos(a) - y * Math.sin(a), 0.5 + x * Math.sin(a) + y * Math.cos(a)]);
      poly(pts, p);
      k.shape(p, C.cream);
      const [mx, my] = [cx - 0.12 * Math.sin(a), 0.5 + 0.12 * Math.cos(a)];
      k.layer(circle(mx, my, 0.018), i % 2 ? C.red : C.ink, 0.1);
    }
  },
};

/* ------------------------------------------------------------------------ */
/* Faith: candles under the chapel window                                    */
/* ------------------------------------------------------------------------ */

const CHAPEL_ASPECT = 1.75;
const faithWall: CutoutSpec = {
  key: "tradition:faith:wall",
  aspect: CHAPEL_ASPECT,
  base: C.stone,
  seed: 201,
  paint(k) {
    const w = k.aspect;
    k.shape(backCard(w), C.stone);
    for (let row = 0; row < 9; row++) {
      const y = 0.06 + row * 0.1;
      k.layer(rect(0.03, y, w - 0.03, y + 0.008), C.stoneLine, 0.05);
      for (let i = 0; i < 9; i++) {
        const x = 0.05 + (i + (row % 2) * 0.5) * (w / 9);
        k.layer(rect(x, y, x + 0.008, y + 0.1), C.stoneLine, 0.05);
      }
    }
    // The window: an arch of coloured glass in lead.
    const cx = w / 2;
    k.layer(arch(cx, 0.28, 0.5, 0.66), C.woodDark, 0.8);
    const panes = [C.blue, C.sun, C.red, C.leaf, C.sky, C.plum];
    const cols = 4;
    const rows = 5;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const x0 = cx - 0.22 + c * 0.11;
        const y0 = 0.3 + r * 0.1;
        k.layer(rect(x0 + 0.008, y0 + 0.008, x0 + 0.11 - 0.008, y0 + 0.1 - 0.008), panes[(r * 3 + c * 2) % panes.length], 0.1);
      }
    }
    k.layer(arch(cx, 0.8, 0.44, 0.12), C.woodDark, 0.1);
    k.layer(circle(cx, 0.83, 0.05), C.lit, 0.2);
  },
};

const ALTAR_ASPECT = 1.5;
/** The three candle wicks on the altar piece. */
export const CANDLES: Pt[] = [[-0.36, 0.86], [0, 0.98], [0.36, 0.86]];
const faithAltar: CutoutSpec = {
  key: "tradition:faith:altar",
  aspect: ALTAR_ASPECT,
  base: C.cream,
  seed: 211,
  paint(k) {
    const w = k.aspect;
    const cx = w / 2;
    k.shape(rect(0.08, -0.05, w - 0.08, 0.5), C.cream);
    k.layer(rect(cx - 0.14, -0.05, cx + 0.14, 0.5), C.red, 0.3);
    k.layer(star(cx, 0.26, 0.05), C.sun, 0.3);
    k.layer(rect(0.06, 0.48, w - 0.06, 0.53), C.wood, 0.4);
    // An open book on its stand.
    k.shape(poly([[cx - 0.3, 0.52], [cx - 0.18, 0.52], [cx - 0.16, 0.6], [cx - 0.3, 0.58]]), C.cream);
    // Candles in brass holders.
    for (const [u, top] of CANDLES) {
      const x = cx + u;
      k.shape(ellipse(x, 0.54, 0.07, 0.02), C.ochre);
      k.shape(rect(x - 0.035, 0.53, x + 0.035, top - 0.02), C.cream);
      k.layer(rect(x - 0.035, top - 0.04, x + 0.035, top - 0.02), C.stone, 0.1);
      k.shape(rect(x - 0.004, top - 0.03, x + 0.004, top), C.ink);
    }
  },
};

/* ------------------------------------------------------------------------ */
/* Craft: the hat on its stand                                               */
/* ------------------------------------------------------------------------ */

const CRAFT_WALL_ASPECT = 1.75;
/** Bunting on a backdrop: [u, v] of each flag's top, from the card's centre line. */
export const BUNTING: Pt[] = Array.from({ length: 9 }, (_, i) => {
  const t = (i + 0.5) / 9;
  // On the string's curve (buntingString): a quadratic from 0.87 sagging to 0.79 mid-way.
  return [(t - 0.5) * 1.36, 0.87 - 0.32 * t * (1 - t)];
});
const craftWall: CutoutSpec = {
  key: "tradition:craft:wall",
  aspect: CRAFT_WALL_ASPECT,
  base: C.wall,
  seed: 221,
  paint(k) {
    const w = k.aspect;
    k.shape(backCard(w), C.petal);
    for (let i = 0; i < 26; i++) {
      for (let j = 0; j < 12; j++) {
        if ((i + j) % 2) continue;
        k.layer(circle(0.06 + i * 0.065, 0.04 + j * 0.075, 0.008), C.cream, 0.05);
      }
    }
    buntingString(k, w);
    // A shelf of yarn and spools.
    k.layer(rect(w * 0.08, 0.48, w * 0.42, 0.5), C.wood, 0.6);
    const yarn: [number, string][] = [[0.13, C.teal], [0.2, C.sun], [0.27, C.plum], [0.34, C.leaf]];
    for (const [x, col] of yarn) {
      k.layer(circle(w * x, 0.545, 0.045), col, 0.6);
      k.line(ellipse(w * x, 0.545, 0.03, 0.04), C.cream, 0.004);
    }
    k.layer(rect(w * 0.6, 0.38, w * 0.9, 0.7), C.cream, 0.7);
    k.layer(rect(w * 0.62, 0.4, w * 0.88, 0.68), C.skyPale, 0.2);
    k.layer(circle(w * 0.75, 0.56, 0.06), C.rust, 0.2);
    k.layer(rect(0.03, -0.05, w - 0.03, 0.2), C.woodLight, 0.5);
  },
};

/** One bunting flag, hung from the top edge (the piece is drawn upside down from its string). */
export const flagCard: CutoutSpec = {
  key: "tradition:flag",
  aspect: 0.8,
  base: C.cream,
  seed: 227,
  resolution: 96,
  deckle: 0.5,
  paint(k) {
    k.shape(poly([[0.02, 0.98], [0.78, 0.98], [0.4, 0.02]]), C.cream);
  },
};

const HAT_ASPECT = 1.5;
const craftTable: CutoutSpec = {
  key: "tradition:craft:table",
  aspect: HAT_ASPECT,
  base: C.wood,
  seed: 233,
  paint(k) {
    const w = k.aspect;
    k.shape(rect(0.15, -0.05, 0.22, 0.3), C.woodDark);
    k.shape(rect(w - 0.22, -0.05, w - 0.15, 0.3), C.woodDark);
    k.shape(rect(0.05, 0.28, w - 0.05, 0.36), C.wood);
    // The hat on its stand, heaped with paper flowers (drawn about a base point, scaled up).
    const cx = w * 0.42;
    const hs = 1.45;
    const at = (x: number, y: number): Pt => [cx + x * hs, 0.36 + y * hs];
    k.shape(rect(...at(-0.015, 0), ...at(0.015, 0.16)), C.woodDark);
    k.shape(ellipse(...at(0, 0), 0.08 * hs, 0.02 * hs), C.woodDark);
    k.shape(ellipse(...at(0, 0.2), 0.3 * hs, 0.05 * hs), C.sun);
    k.shape(rounded(...at(-0.15, 0.19), ...at(0.15, 0.38), 0.07 * hs), C.sun);
    k.layer(rect(...at(-0.15, 0.2), ...at(0.15, 0.25)), C.teal, 0.3);
    const blooms: [number, number, string][] = [
      [-0.12, 0.38, C.petal], [-0.02, 0.43, C.red], [0.09, 0.4, C.rust], [0.16, 0.33, C.cream],
      [-0.18, 0.3, C.plum], [0.03, 0.34, C.petal],
    ];
    for (const [dx, y, col] of blooms) {
      const f = new Path2D();
      const [fx, fy] = at(dx, y);
      for (let i = 0; i < 5; i++) {
        const a = (i / 5) * Math.PI * 2;
        circle(fx + Math.cos(a) * 0.03 * hs, fy + Math.sin(a) * 0.03 * hs, 0.026 * hs, f);
      }
      k.shape(f, col);
      k.layer(circle(fx, fy, 0.016 * hs), C.sun, 0.2);
    }
    k.shape(poly([at(0.15, 0.22), at(0.26, 0.12), at(0.22, 0.1), at(0.14, 0.2)]), C.teal);
    // Spools and scissors.
    for (const [x, col] of [[w * 0.76, C.red], [w * 0.86, C.blue]] as const) {
      k.shape(rect(x - 0.035, 0.36, x + 0.035, 0.46), col);
      k.shape(rect(x - 0.045, 0.36, x + 0.045, 0.375), C.woodLight);
      k.shape(rect(x - 0.045, 0.445, x + 0.045, 0.46), C.woodLight);
    }
  },
};

/* ------------------------------------------------------------------------ */
/* Other: a garden with a round tree                                         */
/* ------------------------------------------------------------------------ */

const GARDEN_ASPECT = 1.75;
const otherGarden: CutoutSpec = {
  key: "tradition:other:garden",
  aspect: GARDEN_ASPECT,
  base: C.sky,
  seed: 241,
  paint(k) {
    const w = k.aspect;
    k.shape(backCard(w), C.sky);
    k.layer(circle(w * 0.8, 0.74, 0.07), C.sun, 0.6);
    k.layer(hills(0.03, w - 0.03, 0.36, [[w * 0.3, 0.46], [w * 0.62, 0.38], [w * 0.9, 0.44]]), C.hillFar, 0.7);
    k.layer(hills(0.03, w - 0.03, 0.2, [[w * 0.2, 0.28], [w * 0.55, 0.2], [w * 0.85, 0.27]]), C.hillNear, 0.8);
    const hx = w * 0.78;
    k.layer(rect(hx - 0.13, 0.22, hx + 0.13, 0.44), C.wall, 0.6);
    k.layer(poly([[hx - 0.17, 0.43], [hx, 0.58], [hx + 0.17, 0.43]]), C.red, 0.6);
    k.layer(rect(hx - 0.035, 0.22, hx + 0.035, 0.33), C.woodDark, 0.3);
    k.layer(rect(hx + 0.06, 0.32, hx + 0.1, 0.37), C.lit, 0.2);
    buntingString(k, w);
  },
};

const ROUND_TREE_ASPECT = 0.9;
const otherTree: CutoutSpec = {
  key: "tradition:other:tree",
  aspect: ROUND_TREE_ASPECT,
  base: C.leaf,
  seed: 251,
  crease: true,
  paint(k) {
    const cx = k.aspect / 2;
    k.shape(rect(cx - 0.04, -0.05, cx + 0.04, 0.45), C.wood);
    const crown = new Path2D();
    circle(cx, 0.66, 0.26, crown);
    circle(cx - 0.18, 0.56, 0.16, crown);
    circle(cx + 0.18, 0.56, 0.16, crown);
    circle(cx, 0.84, 0.15, crown);
    k.shape(crown, C.leaf);
    const light = new Path2D();
    circle(cx - 0.08, 0.74, 0.11, light);
    circle(cx + 0.05, 0.82, 0.07, light);
    k.layer(light, C.leafLight, 0.6);
    for (const [dx, y] of [[-0.12, 0.6], [0.1, 0.66], [0.02, 0.5], [-0.02, 0.78], [0.16, 0.52]] as const) {
      k.layer(circle(cx + dx, y, 0.025), C.red, 0.4);
    }
  },
};

const bench: CutoutSpec = {
  key: "tradition:other:bench",
  aspect: 1.8,
  base: C.woodLight,
  seed: 257,
  paint(k) {
    const w = k.aspect;
    k.shape(rect(0.2, -0.05, 0.27, 0.42), C.woodDark);
    k.shape(rect(w - 0.27, -0.05, w - 0.2, 0.42), C.woodDark);
    k.shape(rect(0.1, 0.4, w - 0.1, 0.52), C.woodLight);
    for (const y of [0.62, 0.8]) k.shape(rect(0.1, y, w - 0.1, y + 0.1), C.woodLight);
    k.shape(rect(0.2, 0.5, 0.26, 0.92), C.woodDark);
    k.shape(rect(w - 0.26, 0.5, w - 0.2, 0.92), C.woodDark);
    k.shape(rounded(w * 0.62, 0.52, w * 0.82, 0.66, 0.03), C.red);
    k.layer(rect(w * 0.62, 0.62, w * 0.82, 0.64), C.cream, 0.2);
  },
};

/* ------------------------------------------------------------------------ */
/* Floors: the ground each kit stands on, glued flat to the page             */
/* ------------------------------------------------------------------------ */

const FLOOR_ASPECT = 2.3;

/** A hand-cut floor shape: a soft-cornered sheet whose front edge bows toward the reader. */
function floorShape(w: number) {
  const p = new Path2D();
  p.moveTo(0.06, 0.92);
  p.bezierCurveTo(w * 0.3, 0.98, w * 0.7, 0.98, w - 0.06, 0.92);
  p.bezierCurveTo(w - 0.01, 0.7, w - 0.01, 0.3, w - 0.08, 0.1);
  p.bezierCurveTo(w * 0.7, 0.02, w * 0.3, 0.02, 0.08, 0.1);
  p.bezierCurveTo(0.01, 0.3, 0.01, 0.7, 0.06, 0.92);
  p.closePath();
  return p;
}

function floorSpec(key: string, base: string, paint: (k: PaintKit, w: number) => void, seed: number): CutoutSpec {
  return {
    key: `tradition:floor:${key}`,
    aspect: FLOOR_ASPECT,
    base,
    seed,
    deckle: 1.3,
    paint(k) {
      k.shape(floorShape(k.aspect), base);
      paint(k, k.aspect);
    },
  };
}

const tileFloor = floorSpec("tiles", C.wainscot, (k, w) => {
  for (let r = 0; r < 6; r++) {
    for (let c = 0; c < 15; c++) {
      if ((r + c) % 2) continue;
      const x = 0.08 + c * ((w - 0.16) / 15);
      const y = 0.1 + r * 0.135;
      k.layer(rect(x, y, x + (w - 0.16) / 15, y + 0.135), C.cream, 0.05);
    }
  }
}, 301);

const snowFloor = floorSpec("snow", C.cream, (k, w) => {
  for (const [x, y, rx] of [[0.4, 0.3, 0.3], [1.3, 0.6, 0.4], [1.9, 0.25, 0.25], [0.9, 0.75, 0.2]] as const) {
    k.layer(ellipse(x * (w / 2.3), y, rx, 0.06), C.skyPale, 0.15);
  }
}, 303);

const grassFloor = floorSpec("grass", C.hillNear, (k, w) => {
  for (let i = 0; i < 40; i++) {
    const x = 0.12 + k.rand() * (w - 0.24);
    const y = 0.14 + k.rand() * 0.72;
    k.layer(poly([[x - 0.02, y], [x, y + 0.05], [x + 0.02, y]]), i % 3 ? C.leaf : C.leafLight, 0.15);
  }
  for (let i = 0; i < 7; i++) {
    const x = 0.2 + k.rand() * (w - 0.4);
    const y = 0.2 + k.rand() * 0.6;
    k.layer(circle(x, y, 0.018), i % 2 ? C.cream : C.sun, 0.2);
  }
}, 305);

/** An oval rug with a border, in two papers. */
function rugFloor(key: string, field: string, border: string, seed: number): CutoutSpec {
  return floorSpec(key, C.wallDeep, (k, w) => {
    k.layer(ellipse(w / 2, 0.52, w * 0.4, 0.36), border, 0.4);
    k.layer(ellipse(w / 2, 0.52, w * 0.36, 0.3), field, 0.2);
    k.layer(ellipse(w / 2, 0.52, w * 0.24, 0.18), border, 0.15);
    k.layer(ellipse(w / 2, 0.52, w * 0.21, 0.145), field, 0.1);
  }, seed);
}

/** A braided rag rug: a pale oval with a few stitched rings. */
const ragRug = floorSpec("rag", C.wallDeep, (k, w) => {
  k.layer(ellipse(w / 2, 0.52, w * 0.4, 0.36), C.cream, 0.4);
  for (const [s, col] of [[0.92, C.teal], [0.7, C.petal], [0.48, C.teal]] as const) {
    k.line(ellipse(w / 2, 0.52, w * 0.4 * s, 0.36 * s), col, 0.016);
  }
}, 307);

const aisleFloor = floorSpec("aisle", C.stone, (k, w) => {
  for (let i = 1; i < 8; i++) k.layer(rect(0.05, i * 0.12, w - 0.05, i * 0.12 + 0.008), C.stoneLine, 0.05);
  k.layer(rect(w / 2 - 0.2, 0.0, w / 2 + 0.2, 1.0), C.red, 0.3);
  k.layer(rect(w / 2 - 0.17, 0.0, w / 2 - 0.15, 1.0), C.sun, 0.1);
  k.layer(rect(w / 2 + 0.15, 0.0, w / 2 + 0.17, 1.0), C.sun, 0.1);
}, 309);

/** Every floor is laid the same way: across the stand, from the back wall to the people's feet. */
const floor = (spec: CutoutSpec): KitPatch => ({ spec, width: 1.98, x: 0, z: 0.58 });

/* ------------------------------------------------------------------------ */
/* The kits                                                                  */
/* ------------------------------------------------------------------------ */

/** A card piece standing on the page (SetPiece), in stage units. Listed back to front: they rise in that order. */
export interface KitPiece {
  spec: CutoutSpec;
  height: number;
  x: number;
  z: number;
  vfold?: number;
  yaw?: number;
}

/** A cutout glued flat to the page (PagePatch). */
export interface KitPatch {
  spec: CutoutSpec;
  width: number;
  x: number;
  z: number;
}

/**
 * Small moving things pinned to a piece (by index into `pieces`), at [u, v]
 * in its piece units: lights that twinkle, flames that flicker, notes that
 * float up, flags that sway, dice that hop.
 */
export interface KitLife {
  kind: "twinkle" | "flicker" | "float" | "sway" | "hop";
  piece: number;
  points: readonly Pt[];
  colors: readonly string[];
  /** Size in stage units. */
  size: number;
}

/** A paper effect (stage/Effects.tsx) from a point on a piece. */
export interface KitEffect {
  kind: "steam" | "smoke";
  piece: number;
  at: Pt;
  scale: number;
  count?: number;
  wind?: number;
}

export interface Kit {
  pieces: KitPiece[];
  patches: KitPatch[];
  life: KitLife[];
  effects: KitEffect[];
  /** Where the people who keep it stand ([x, z], stage units), in front of the kit. */
  stand: Pt[];
}

const LIGHTS = [C.lit, C.rust, C.sky, C.petal, C.sun];
const FLAMES = [C.sun, C.rust, C.lit];
const BUNTING_COLORS = [C.red, C.sun, C.teal, C.cream, C.plum, C.leaf];

/** Where the people stand: either side of the middle piece, a third a step in front. */
const SIDES: Pt[] = [[-0.66, 0.9], [0.7, 0.92], [-0.2, 1.06]];

export const KITS: Record<Category, Kit> = {
  food: {
    pieces: [
      { spec: foodWall, height: 1.1, x: 0, z: 0 },
      { spec: foodTable, height: 0.66, x: 0.02, z: 0.5 },
    ],
    patches: [floor(tileFloor)],
    life: [],
    effects: [{ kind: "steam", piece: 1, at: FOOD_POT, scale: 1.25, count: 12, wind: 0.12 }],
    stand: SIDES,
  },
  holiday: {
    pieces: [
      { spec: holidaySky, height: 1.1, x: 0, z: 0 },
      { spec: holidayTree, height: 1.06, x: 0.04, z: 0.36, vfold: 0.42 },
      { spec: holidayGifts, height: 0.24, x: 0.04, z: 0.78 },
    ],
    patches: [floor(snowFloor)],
    life: [{ kind: "twinkle", piece: 1, points: TREE_LIGHTS, colors: LIGHTS, size: 0.028 }],
    effects: [],
    stand: SIDES,
  },
  trip: {
    pieces: [
      { spec: tripSky, height: 1.08, x: 0, z: 0 },
      { spec: tripTent, height: 0.64, x: -0.5, z: 0.4, vfold: 0.5, yaw: 0.18 },
      { spec: tripFire, height: 0.3, x: -0.02, z: 0.82 },
    ],
    patches: [floor(grassFloor), { spec: tripLake, width: 1.05, x: 0.42, z: 0.46 }],
    life: [{ kind: "flicker", piece: 2, points: [[-0.14, 0.42], [0.02, 0.5], [0.16, 0.4]], colors: FLAMES, size: 0.2 }],
    effects: [],
    stand: [[-0.78, 0.9], [0.74, 0.96], [0.46, 1.08]],
  },
  music: {
    pieces: [
      { spec: musicWall, height: 1.1, x: 0, z: 0 },
      { spec: musicPiano, height: 0.78, x: -0.04, z: 0.42 },
    ],
    patches: [floor(rugFloor("parlour", C.petal, C.red, 311))],
    life: [{ kind: "float", piece: 1, points: [[-0.22, 1.02], [0.1, 1.02], [0.36, 1.0], [-0.44, 1.0]], colors: [C.ink], size: 0.12 }],
    effects: [],
    stand: SIDES,
  },
  game: {
    pieces: [
      { spec: gameWall, height: 1.1, x: 0, z: 0 },
      { spec: gameTable, height: 0.66, x: 0, z: 0.5 },
    ],
    patches: [floor(rugFloor("den", C.teal, C.cream, 313))],
    life: [{ kind: "hop", piece: 1, points: GAME_TOP, colors: [C.cream, C.red], size: 0.07 }],
    effects: [],
    stand: SIDES,
  },
  faith: {
    pieces: [
      { spec: faithWall, height: 1.12, x: 0, z: 0 },
      { spec: faithAltar, height: 0.58, x: 0, z: 0.5 },
    ],
    patches: [floor(aisleFloor)],
    life: [{ kind: "flicker", piece: 1, points: CANDLES, colors: FLAMES, size: 0.07 }],
    effects: [],
    stand: SIDES,
  },
  craft: {
    pieces: [
      { spec: craftWall, height: 1.1, x: 0, z: 0 },
      { spec: craftTable, height: 0.74, x: 0, z: 0.5 },
    ],
    patches: [floor(ragRug)],
    life: [{ kind: "sway", piece: 0, points: BUNTING, colors: BUNTING_COLORS, size: 0.1 }],
    effects: [],
    stand: SIDES,
  },
  other: {
    pieces: [
      { spec: otherGarden, height: 1.08, x: 0, z: 0 },
      { spec: otherTree, height: 0.96, x: -0.38, z: 0.36, vfold: 0.4 },
      { spec: bench, height: 0.34, x: 0.36, z: 0.68 },
    ],
    patches: [floor(grassFloor)],
    life: [{ kind: "sway", piece: 0, points: BUNTING, colors: BUNTING_COLORS, size: 0.1 }],
    effects: [],
    stand: SIDES,
  },
};

/* ------------------------------------------------------------------------ */
/* Food, again: a breakfast nook (a second food tradition gets this one)      */
/* ------------------------------------------------------------------------ */

const NOOK_ASPECT = 1.75;
const breakfastWall: CutoutSpec = {
  key: "tradition:food:nook",
  aspect: NOOK_ASPECT,
  base: C.skyPale,
  seed: 401,
  paint(k) {
    const w = k.aspect;
    k.shape(backCard(w), C.skyPale);
    // Morning: a wide window full of sun, and a striped valance.
    k.layer(rect(w * 0.22, 0.36, w * 0.78, 0.86), C.cream, 0.7);
    k.layer(rect(w * 0.25, 0.39, w * 0.75, 0.83), C.sky, 0.2);
    k.layer(circle(w * 0.62, 0.62, 0.1), C.lit, 0.4);
    k.layer(hills(w * 0.25, w * 0.75, 0.46, [[w * 0.4, 0.52], [w * 0.6, 0.47]]), C.hillNear, 0.3);
    k.layer(rect(w * 0.5 - 0.008, 0.39, w * 0.5 + 0.008, 0.83), C.cream, 0.2);
    for (let i = 0; i < 9; i++) {
      const x0 = w * 0.2 + (i * (w * 0.6)) / 9;
      const x1 = x0 + (w * 0.6) / 9;
      const p = new Path2D();
      p.moveTo(x0, 0.9);
      p.lineTo(x1, 0.9);
      p.lineTo(x1, 0.8);
      p.quadraticCurveTo((x0 + x1) / 2, 0.76, x0, 0.8);
      p.closePath();
      k.layer(p, i % 2 ? C.cream : C.red, 0.6);
    }
    // A plant on the sill and a clock.
    k.layer(rounded(w * 0.7, 0.36, w * 0.76, 0.42, 0.01), C.rust, 0.6);
    k.layer(ellipse(w * 0.73, 0.47, 0.05, 0.06), C.leaf, 0.5);
    k.layer(circle(w * 0.1, 0.68, 0.07), C.cream, 0.7);
    k.line(poly([[w * 0.1, 0.68], [w * 0.1, 0.73], [w * 0.1, 0.68], [w * 0.13, 0.68]]), C.ink, 0.008);
    k.layer(rect(0.03, -0.05, w - 0.03, 0.24), C.leafLight, 0.5);
    k.layer(rect(0.03, 0.235, w - 0.03, 0.26), C.cream, 0.4);
  },
};

const BREAKFAST_ASPECT = 1.6;
/** The coffee pot's spout on the breakfast table. */
export const COFFEE_SPOUT: Pt = [0.42, 0.84];
const breakfastTable: CutoutSpec = {
  key: "tradition:food:breakfast",
  aspect: BREAKFAST_ASPECT,
  base: C.cream,
  seed: 409,
  paint(k) {
    const w = k.aspect;
    k.shape(rect(0.2, -0.05, 0.27, 0.32), C.woodLight);
    k.shape(rect(w - 0.27, -0.05, w - 0.2, 0.32), C.woodLight);
    k.shape(rect(0.06, 0.3, w - 0.06, 0.46), C.cream);
    for (let i = 0; i < 12; i++) {
      const x = 0.06 + ((w - 0.12) * (i + 0.5)) / 12;
      k.layer(rect(x - 0.02, 0.3, x + 0.02, 0.46), C.sky, 0.05);
    }
    // A tall stack of pancakes, butter melting, syrup running.
    const px = w * 0.42;
    k.shape(ellipse(px, 0.47, 0.2, 0.035), C.stone);
    for (let i = 0; i < 6; i++) {
      const y = 0.49 + i * 0.045;
      k.shape(rounded(px - 0.16 + (i % 2) * 0.01, y, px + 0.16 - (i % 3) * 0.01, y + 0.05, 0.025), i % 2 ? C.ochre : C.sun);
    }
    const syrup = new Path2D();
    syrup.moveTo(px - 0.12, 0.76);
    syrup.quadraticCurveTo(px, 0.8, px + 0.13, 0.76);
    syrup.lineTo(px + 0.11, 0.66);
    syrup.quadraticCurveTo(px + 0.08, 0.7, px + 0.06, 0.73);
    syrup.lineTo(px - 0.05, 0.73);
    syrup.quadraticCurveTo(px - 0.1, 0.69, px - 0.12, 0.72);
    syrup.closePath();
    k.layer(syrup, C.wood, 0.3);
    k.shape(rounded(px - 0.04, 0.77, px + 0.04, 0.81, 0.008), C.lit);
    // Mugs and the coffee pot.
    for (const [x, col] of [[w * 0.16, C.red], [w * 0.24, C.teal]] as const) {
      k.shape(rounded(x - 0.035, 0.46, x + 0.035, 0.55, 0.01), col);
      k.line(ellipse(x + 0.045, 0.505, 0.018, 0.025), col, 0.01);
    }
    const cx = w / 2 + COFFEE_SPOUT[0] - 0.06;
    k.shape(poly([[cx - 0.07, 0.46], [cx + 0.07, 0.46], [cx + 0.05, 0.72], [cx - 0.05, 0.72]]), C.tealDeep);
    k.shape(rect(cx - 0.035, 0.72, cx + 0.035, 0.76), C.ink);
    k.shape(poly([[cx + 0.05, 0.66], [cx + 0.12, 0.8], [cx + 0.1, 0.81], [cx + 0.04, 0.7]]), C.tealDeep);
  },
};

const breakfastKit = (): Kit => ({
  pieces: [
    { spec: breakfastWall, height: 1.1, x: 0, z: 0 },
    { spec: breakfastTable, height: 0.66, x: 0.0, z: 0.5 },
  ],
  patches: [floor(tileFloor)],
  life: [],
  effects: [{ kind: "steam", piece: 1, at: COFFEE_SPOUT, scale: 0.9, count: 9, wind: 0.18 }],
  stand: SIDES,
});

/** The kit for a category (an unknown one gets "other"). */
/** Further kits for a category, so a book with two of a kind does not repeat a page. */
const VARIANTS: Partial<Record<Category, Kit[]>> = {
  food: [KITS.food, breakfastKit()],
};

/**
 * The kit for a category (an unknown one gets "other"). `variant` is the
 * tradition's place among those of its category in the book (0 for the
 * first), so the second food tradition stands a breakfast nook.
 */
export function kitFor(category: string | null | undefined, variant = 0): Kit {
  const kits = VARIANTS[category as Category] ?? [KITS[(category ?? "other") as Category] ?? KITS.other];
  return kits[Math.abs(Math.trunc(variant)) % kits.length];
}

/** Every cutout a kit cuts, to build ahead of time. */
export function kitSpecs(kit: Kit): CutoutSpec[] {
  const specs = [...kit.pieces.map((p) => p.spec), ...kit.patches.map((p) => p.spec)];
  for (const l of kit.life) {
    if (l.kind === "float") specs.push(noteCard);
    if (l.kind === "sway") specs.push(flagCard);
  }
  return specs;
}
