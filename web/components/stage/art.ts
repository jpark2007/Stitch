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
  bloomWarm: "#d98d6b",
  cardBack: "#e9dfcb",
  bandWarm: "#ecd6b8",
  bandPale: "#d3dfd9",
  cottage: "#e7cd9c",
  cottageShade: "#d9bb87",
  cottageRoof: "#9c5640",
  chimney: "#a8573f",
  teal: "#4e6a69",
  lit: "#f3d488",
  lawn: "#bcc394",
  lawnDeep: "#a8b37f",
  path: "#ead9b6",
  stone: "#dfd3bd",
  stoneLine: "#c7b89c",
  petal: "#f1b9a6",
  petalCream: "#f6ecd8",
  pollen: "#e6b45c",
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
  aspect: 1.2,
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
    // Layered bands of evening light behind the hills.
    k.layer(hills(m, w - m, 0.36, [[w * 0.3, 0.38], [w * 0.65, 0.35], [w * 0.9, 0.38]]), PALETTE.bandPale, 0.35);
    k.layer(hills(m, w - m, 0.29, [[w * 0.25, 0.31], [w * 0.6, 0.29], [w * 0.85, 0.31]]), PALETTE.bandWarm, 0.4);
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
  crease: true,
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

/** A cottage with a chimney (the demo's smoke comes out of it). */
export const COTTAGE_ASPECT = 0.86;
/** Top of the chimney in piece units: across from the centre, up from the fold. */
export const COTTAGE_CHIMNEY: [number, number] = [0.62 - COTTAGE_ASPECT / 2, 0.95];

export const cottage: CutoutSpec = {
  key: "demo:cottage",
  aspect: COTTAGE_ASPECT,
  base: PALETTE.cottage,
  seed: 53,
  paint(k: PaintKit) {
    // Chimney first, so the roof overlaps its foot.
    k.shape(rect(0.575, 0.55, 0.665, 0.93), PALETTE.chimney);
    k.shape(rect(0.56, 0.915, 0.68, 0.95), PALETTE.chimney);
    k.shape(rect(0.11, -0.05, 0.75, 0.5), PALETTE.cottage);
    k.layer(rect(0.11, 0.44, 0.75, 0.5), PALETTE.cottageShade, 0.4);
    k.shape(poly([[0.03, 0.47], [0.43, 0.86], [0.83, 0.47], [0.83, 0.45], [0.03, 0.45]]), PALETTE.cottageRoof);
    // Round attic window and a lit cross-paned window.
    k.layer(circle(0.43, 0.62, 0.045), PALETTE.lit, 0.6);
    const win = rect(0.47, 0.17, 0.66, 0.36);
    k.layer(win, PALETTE.lit, 0.7);
    const bars = new Path2D();
    bars.moveTo(0.565, 0.17);
    bars.lineTo(0.565, 0.36);
    bars.moveTo(0.47, 0.265);
    bars.lineTo(0.66, 0.265);
    k.line(bars, PALETTE.cottageRoof, 0.012);
    // Door with a step.
    k.layer(arch(0.27, 0.0, 0.12, 0.27), PALETTE.teal, 0.8);
    k.layer(rect(0.19, 0.0, 0.35, 0.02), PALETTE.stone, 0.5);
  },
};

/** A rounded flowering bush: lumps with light tops and five-petal flowers. */
function flowerBush(key: string, seed: number, aspect: number, petal: string): CutoutSpec {
  return {
    key,
    aspect,
    base: PALETTE.bush,
    seed,
    resolution: 640,
    paint(k: PaintKit) {
      const w = k.aspect;
      const r = k.rand;
      const lumps: [number, number, number][] = [];
      const n = Math.max(3, Math.round(w / 0.36));
      for (let i = 0; i < n; i++) {
        const x = 0.2 + ((w - 0.4) * (i + 0.5)) / n + (r() - 0.5) * 0.06;
        const top = 0.75 - Math.pow(Math.abs(x / w - 0.5) * 2, 2) * 0.3;
        lumps.push([x, top - 0.2 + r() * 0.06, 0.2 + r() * 0.07]);
      }
      const body = new Path2D();
      body.moveTo(0.1, -0.05);
      body.lineTo(0.1, 0.25);
      body.bezierCurveTo(0.1, 0.45, w - 0.1, 0.45, w - 0.1, 0.25);
      body.lineTo(w - 0.1, -0.05);
      body.closePath();
      k.shape(body, PALETTE.bush);
      // Lumps filled one by one: in one path their winding would cancel the
      // body's where they overlap and punch holes.
      for (const [x, y, rad] of lumps) k.shape(circle(x, y, rad), PALETTE.bush);
      // Light catches the top-left of each lump.
      for (const [x, y, rad] of lumps) k.layer(circle(x - rad * 0.22, y + rad * 0.2, rad * 0.62), PALETTE.bushLight, 0.45);
      for (let i = 0; i < Math.round(w * 4.5); i++) {
        const [x, y, rad] = lumps[Math.floor(r() * lumps.length)];
        const fx = x + (r() - 0.5) * rad * 1.3;
        const fy = y + (r() - 0.45) * rad * 1.1;
        const pr = 0.026 + r() * 0.01;
        const flower = new Path2D();
        const turn = r() * 6.28;
        for (let p = 0; p < 5; p++) {
          const a = turn + (p * Math.PI * 2) / 5;
          circle(fx + Math.cos(a) * pr * 0.9, fy + Math.sin(a) * pr * 0.9, pr * 0.62, flower);
        }
        k.layer(flower, r() > 0.3 ? petal : PALETTE.petalCream, 0.45);
        k.layer(circle(fx, fy, pr * 0.42), PALETTE.pollen, 0.2);
      }
    },
  };
}

export const bushLeft = flowerBush("demo:bush-left", 61, 2.3, PALETTE.petal);
export const bushRight = flowerBush("demo:bush-right", 67, 1.9, PALETTE.bloomWarm);

/** The stone riser the church stands on: block joints and two step lines. */
export const STEPS_ASPECT = 9;
export const steps: CutoutSpec = {
  key: "demo:steps",
  aspect: STEPS_ASPECT,
  base: PALETTE.stone,
  seed: 71,
  resolution: 900,
  deckle: 0,
  paint(k: PaintKit) {
    const w = k.aspect;
    k.shape(rect(-0.1, -0.1, w + 0.1, 1.1), PALETTE.stone);
    const lines = new Path2D();
    for (const y of [0.36, 0.7]) {
      lines.moveTo(0, y);
      lines.lineTo(w, y);
    }
    let row = 0;
    for (const [y0, y1] of [[0, 0.36], [0.36, 0.7], [0.7, 1]]) {
      for (let x = 0.4 + (row % 2) * 0.35; x < w; x += 0.7 + k.rand() * 0.2) {
        lines.moveTo(x, y0);
        lines.lineTo(x, y1);
      }
      row += 1;
    }
    k.line(lines, PALETTE.stoneLine, 0.05);
  },
};

/** The lawn glued flat on the page, up to the hills (patch: up = away from the reader). */
export function lawn(aspect: number): CutoutSpec {
  return {
    key: `demo:lawn:${aspect.toFixed(3)}`,
    aspect,
    base: PALETTE.lawn,
    seed: 83,
    resolution: 768,
    paint(k: PaintKit) {
      const w = k.aspect;
      const r = k.rand;
      // A soft-cornered meadow with a wavy hand-cut rim.
      const p = new Path2D();
      const pts: Pt[] = [];
      const steps = 48;
      for (let i = 0; i < steps; i++) {
        const a = (i / steps) * Math.PI * 2;
        // Superellipse: square-ish with round corners.
        const c = Math.cos(a);
        const s2 = Math.sin(a);
        const ex = Math.sign(c) * Math.pow(Math.abs(c), 0.35);
        const ey = Math.sign(s2) * Math.pow(Math.abs(s2), 0.35);
        const wob = 1 + (r() - 0.5) * 0.025;
        pts.push([w / 2 + ex * (w / 2 - 0.03) * wob, 0.5 + ey * 0.47 * wob]);
      }
      p.moveTo(pts[0][0], pts[0][1]);
      for (let i = 0; i < pts.length; i++) {
        const a = pts[i];
        const b = pts[(i + 1) % pts.length];
        p.quadraticCurveTo(a[0], a[1], (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
      }
      p.closePath();
      k.shape(p, PALETTE.lawn);
      // Sparse tufts of grass.
      const tufts = new Path2D();
      for (let i = 0; i < 18; i++) {
        const x = 0.12 + r() * (w - 0.24);
        const y = 0.08 + r() * 0.84;
        tufts.moveTo(x - 0.006, y);
        tufts.lineTo(x - 0.002, y + 0.011);
        tufts.moveTo(x, y);
        tufts.lineTo(x + 0.001, y + 0.014);
        tufts.moveTo(x + 0.006, y);
        tufts.lineTo(x + 0.0035, y + 0.01);
      }
      k.line(tufts, PALETTE.lawnDeep, 0.0035);
    },
  };
}

/** A path winding from the front edge of the page up to the church steps. */
export const PATH_ASPECT = 0.34;
export const path: CutoutSpec = {
  key: "demo:path",
  aspect: PATH_ASPECT,
  base: PALETTE.path,
  seed: 89,
  paint(k: PaintKit) {
    const w = k.aspect;
    const p = new Path2D();
    // Left edge up, right edge down: wide at the reader, narrow at the steps.
    p.moveTo(w * 0.04, 0);
    p.bezierCurveTo(w * 0.3, 0.35, w * 0.05, 0.62, w * 0.3, 1);
    p.lineTo(w * 0.7, 1);
    p.bezierCurveTo(w * 0.5, 0.62, w * 0.95, 0.35, w * 0.96, 0);
    p.closePath();
    k.shape(p, PALETTE.path);
  },
};
