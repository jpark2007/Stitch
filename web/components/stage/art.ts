/**
 * Procedural cut-paper scenery for the hand-built demo diorama (and as
 * stand-ins until the set designer's generated cutouts arrive).
 *
 * Flat paper colours only, layered like glued card: no gradients, no outlines,
 * a few details. Each painter draws in piece units (height 1, width = aspect,
 * y up, the bottom row is the hinge fold), see ./cutout.
 */

import type { CutoutSpec, PaintKit } from "./cutout";

export const PALETTE = {
  sky: "#c4d6da",
  skyDeep: "#b4c6c2",
  sun: "#e6b45c",
  cloud: "#f8f2e6",
  hillFar: "#cdd3b4",
  hillNear: "#b3bf98",
  wall: "#f3ebdc",
  wallShade: "#e4d8c2",
  roof: "#b9573d",
  door: "#7a4a33",
  step: "#d6c6a8",
  leaf: "#8ba278",
  leafLight: "#a5b78f",
  leafDeep: "#6c8660",
  trunk: "#7c5b40",
  bush: "#5f7a55",
  bushLight: "#728c64",
  bloom: "#f4ead6",
  bloomWarm: "#d98d6b",
  cardBack: "#e9dfcb",
} as const;

type Pt = [number, number];

function poly(pts: Pt[]) {
  const p = new Path2D();
  p.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) p.lineTo(pts[i][0], pts[i][1]);
  p.closePath();
  return p;
}

function rect(x0: number, y0: number, x1: number, y1: number) {
  return poly([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]);
}

function circle(cx: number, cy: number, r: number, into = new Path2D()) {
  into.moveTo(cx + r, cy);
  into.arc(cx, cy, r, 0, Math.PI * 2);
  return into;
}

/** An arched window or door: a rectangle with a round top. */
function arch(cx: number, y0: number, w: number, h: number) {
  const r = w / 2;
  const p = new Path2D();
  p.moveTo(cx - r, y0);
  p.lineTo(cx - r, y0 + h - r);
  p.arc(cx, y0 + h - r, r, Math.PI, 0, true);
  p.lineTo(cx + r, y0);
  p.closePath();
  return p;
}

/** A soft cloud: overlapping circles along a flat base. */
function cloud(cx: number, cy: number, s: number) {
  const p = new Path2D();
  circle(cx - 0.55 * s, cy + 0.05 * s, 0.32 * s, p);
  circle(cx - 0.1 * s, cy + 0.28 * s, 0.45 * s, p);
  circle(cx + 0.42 * s, cy + 0.12 * s, 0.36 * s, p);
  p.moveTo(cx - 0.85 * s, cy - 0.1 * s);
  p.rect(cx - 0.85 * s, cy - 0.12 * s, 1.6 * s, 0.24 * s);
  return p;
}

/** A rolling hill line from x0 to x1, closed down to below the fold. */
function hills(x0: number, x1: number, base: number, crests: [number, number][]) {
  const p = new Path2D();
  p.moveTo(x0, -0.05);
  p.lineTo(x0, base);
  let px = x0;
  let py = base;
  for (const [x, y] of crests) {
    const mx = (px + x) / 2;
    p.bezierCurveTo(mx, py, mx, y, x, y);
    px = x;
    py = y;
  }
  p.bezierCurveTo((px + x1) / 2, py, (px + x1) / 2, base, x1, base);
  p.lineTo(x1, -0.05);
  p.closePath();
  return p;
}

/* ------------------------------------------------------------------------ */
/* Pieces                                                                    */
/* ------------------------------------------------------------------------ */

export const skyCard: CutoutSpec = {
  key: "demo:sky",
  aspect: 1.32,
  base: PALETTE.sky,
  seed: 11,
  paint(k: PaintKit) {
    const w = k.aspect;
    const m = 0.03;
    const card = new Path2D();
    card.moveTo(m, -0.05);
    card.lineTo(m, 0.6);
    card.bezierCurveTo(m, 0.86, w * 0.26, 0.975, w / 2, 0.975);
    card.bezierCurveTo(w * 0.74, 0.975, w - m, 0.86, w - m, 0.6);
    card.lineTo(w - m, -0.05);
    card.closePath();
    k.shape(card, PALETTE.sky);
    k.layer(circle(w * 0.7, 0.68, 0.085), PALETTE.sun, 0.7);
    k.layer(cloud(w * 0.27, 0.72, 0.15), PALETTE.cloud, 0.9);
    k.layer(cloud(w * 0.82, 0.5, 0.1), PALETTE.cloud, 0.8);
    k.layer(hills(m, w - m, 0.2, [[w * 0.22, 0.3], [w * 0.55, 0.22], [w * 0.8, 0.31]]), PALETTE.hillFar, 0.8);
    k.layer(hills(m, w - m, 0.12, [[w * 0.35, 0.17], [w * 0.7, 0.1], [w * 0.9, 0.15]]), PALETTE.hillNear, 0.9);
  },
};

export const church: CutoutSpec = {
  key: "demo:church",
  aspect: 0.94,
  base: PALETTE.wall,
  seed: 23,
  paint(k: PaintKit) {
    const cx = k.aspect / 2;
    // Wings and their roof.
    k.shape(rect(cx - 0.4, -0.05, cx + 0.4, 0.37), PALETTE.wall);
    k.shape(poly([[cx - 0.44, 0.355], [cx, 0.58], [cx + 0.44, 0.355], [cx + 0.44, 0.34], [cx - 0.44, 0.34]]), PALETTE.roof);
    // Tower and spire.
    k.shape(rect(cx - 0.088, -0.05, cx + 0.088, 0.67), PALETTE.wall);
    k.layer(rect(cx - 0.088, 0.64, cx + 0.088, 0.67), PALETTE.wallShade, 0.4);
    k.shape(poly([[cx - 0.105, 0.655], [cx, 0.925], [cx + 0.105, 0.655]]), PALETTE.roof);
    k.shape(rect(cx - 0.008, 0.9, cx + 0.008, 0.99), PALETTE.door);
    k.shape(rect(cx - 0.03, 0.948, cx + 0.03, 0.962), PALETTE.door);
    // Shade the wings so the tower reads as standing in front.
    k.layer(rect(cx - 0.4, 0.0, cx - 0.088, 0.034), PALETTE.wallShade, 0.3);
    k.layer(rect(cx + 0.088, 0.0, cx + 0.4, 0.034), PALETTE.wallShade, 0.3);
    // Door, step and the windows cut right through the card.
    k.layer(arch(cx, 0.03, 0.082, 0.17), PALETTE.door, 0.8);
    k.layer(rect(cx - 0.13, 0.0, cx + 0.13, 0.03), PALETTE.step, 0.6);
    k.hole(arch(cx, 0.47, 0.05, 0.1));
    k.hole(circle(cx, 0.335, 0.026));
    for (const s of [-1, 1]) {
      k.hole(arch(cx + s * 0.2, 0.12, 0.056, 0.15));
      k.hole(arch(cx + s * 0.315, 0.12, 0.056, 0.15));
    }
  },
};

export const trees: CutoutSpec = {
  key: "demo:trees",
  aspect: 0.9,
  base: PALETTE.leaf,
  seed: 37,
  paint(k: PaintKit) {
    // Ground mound joins both trees into one cut piece.
    const ground = new Path2D();
    ground.moveTo(0.04, -0.05);
    ground.lineTo(0.04, 0.02);
    ground.bezierCurveTo(0.2, 0.075, 0.7, 0.075, 0.86, 0.02);
    ground.lineTo(0.86, -0.05);
    ground.closePath();
    k.shape(ground, PALETTE.leafDeep);
    // Big round tree.
    k.shape(rect(0.28, 0, 0.335, 0.42), PALETTE.trunk);
    const crown = new Path2D();
    circle(0.31, 0.62, 0.22, crown);
    circle(0.17, 0.52, 0.14, crown);
    circle(0.46, 0.53, 0.14, crown);
    circle(0.3, 0.8, 0.15, crown);
    k.shape(crown, PALETTE.leaf);
    const light = new Path2D();
    circle(0.25, 0.7, 0.11, light);
    circle(0.33, 0.78, 0.08, light);
    k.layer(light, PALETTE.leafLight, 0.6);
    // Slim cypress.
    k.shape(rect(0.665, 0, 0.705, 0.2), PALETTE.trunk);
    const cypress = new Path2D();
    cypress.moveTo(0.685, 0.7);
    cypress.bezierCurveTo(0.8, 0.52, 0.81, 0.27, 0.77, 0.15);
    cypress.bezierCurveTo(0.73, 0.12, 0.64, 0.12, 0.6, 0.15);
    cypress.bezierCurveTo(0.56, 0.27, 0.57, 0.52, 0.685, 0.7);
    cypress.closePath();
    k.shape(cypress, PALETTE.leafDeep);
  },
};

export const bush: CutoutSpec = {
  key: "demo:bush",
  aspect: 5.4,
  base: PALETTE.bush,
  seed: 41,
  resolution: 768,
  paint(k: PaintKit) {
    const w = k.aspect;
    const r = k.rand;
    const body = new Path2D();
    body.rect(0.06, -0.05, w - 0.12, 0.45);
    const tops: [number, number, number][] = [];
    for (let x = 0.36; x < w - 0.34; x += 0.3 + r() * 0.16) tops.push([x, 0.42 + r() * 0.16, 0.17 + r() * 0.1]);
    for (const [x, y, rad] of tops) circle(x, y, rad, body);
    k.shape(body, PALETTE.bush);
    const light = new Path2D();
    for (const [x, y, rad] of tops) if (r() > 0.45) circle(x - rad * 0.2, y + rad * 0.15, rad * 0.55, light);
    k.layer(light, PALETTE.bushLight, 0.5);
    for (let i = 0; i < 9; i++) {
      const [x, y, rad] = tops[Math.floor(r() * tops.length)];
      const fx = x + (r() - 0.5) * rad;
      const fy = y + (r() - 0.6) * rad;
      k.layer(circle(fx, fy, 0.028 + r() * 0.012), r() > 0.35 ? PALETTE.bloom : PALETTE.bloomWarm, 0.5);
    }
  },
};

export const DEMO_CUTOUTS = [skyCard, church, trees, bush] as const;
