/**
 * Procedural cut-paper art for each signature item kind (see items.ts), in
 * the head socket's frame: origin on top of the head, +x the way the puppet
 * faces, rig units. The head oval sits below the origin (HEAD in shapes.ts,
 * moved down by the head bone's length). Pure data, no three.js.
 */

import type { ItemKind } from "./items";
import {
  HEAD,
  PathBuilder,
  ellipse,
  ensureCCW,
  hullOfCircles,
  stroke,
  superellipse,
  transform,
  type Outline,
} from "./shapes";

/** Which of the item's paper colours a piece is cut from. */
export type ItemTone = "main" | "dark" | "light" | "ink";

export interface ItemPiece {
  outline: Outline;
  holes?: Outline[];
  tone: ItemTone;
  /** Stacking order within the item: higher is nearer the viewer. */
  layer: number;
  /** Edge roughness multiplier (fluffy pom-poms, fringes). Default 1. */
  rough?: number;
}

/** Head oval centre and radii in the socket frame. */
function headIn(socketY: number) {
  return { cx: HEAD.cx, cy: HEAD.cy - socketY, rx: HEAD.rx, ry: HEAD.ry };
}

function flatCap(h: ReturnType<typeof headIn>): ItemPiece[] {
  const top = h.cy + h.ry; // top of the head (about 0)
  // Soft crown puffing forward over the brow, flat on top.
  const crown = new PathBuilder()
    .move(-0.079, top - 0.058)
    .quad(-0.005, top - 0.044, 0.066, top - 0.05)
    .quad(0.088, top - 0.022, 0.05, top + 0.012)
    .quad(-0.02, top + 0.034, -0.07, top + 0.004)
    .quad(-0.09, top - 0.022, -0.079, top - 0.058)
    .close();
  // Short stiff brim jutting forward.
  const brim = new PathBuilder()
    .move(0.03, top - 0.06)
    .quad(0.09, top - 0.066, 0.122, top - 0.052)
    .quad(0.126, top - 0.043, 0.112, top - 0.04)
    .quad(0.07, top - 0.04, 0.03, top - 0.043)
    .close();
  const button = ellipse(-0.006, top + 0.024, 0.0085, 0.0065);
  return [
    { outline: crown, tone: "main", layer: 0 },
    { outline: brim, tone: "dark", layer: 1 },
    { outline: button, tone: "dark", layer: 1 },
  ];
}

function hat(h: ReturnType<typeof headIn>): ItemPiece[] {
  const top = h.cy + h.ry;
  const base = top - 0.05;
  const crown = superellipse(h.cx - 0.002, base + 0.048, 0.06, 0.05, 3.2, (u) => 0.06 - 0.008 * u);
  const band = superellipse(h.cx - 0.002, base + 0.012, 0.062, 0.011, 6);
  // Bucket brim, drooping a little at both ends.
  const brim = new PathBuilder()
    .move(-0.118, base - 0.012)
    .quad(h.cx, base + 0.006, 0.13, base - 0.012)
    .quad(0.136, base - 0.022, 0.124, base - 0.026)
    .quad(h.cx, base - 0.012, -0.112, base - 0.026)
    .quad(-0.124, base - 0.022, -0.118, base - 0.012)
    .close();
  return [
    { outline: crown, tone: "main", layer: 0 },
    { outline: band, tone: "dark", layer: 1 },
    { outline: brim, tone: "main", layer: 2 },
  ];
}

function beanie(h: ReturnType<typeof headIn>): ItemPiece[] {
  const top = h.cy + h.ry;
  const base = top - 0.052;
  const dome = new PathBuilder()
    .move(-0.08, base)
    .line(0.094, base)
    .arc(h.cx, base, 0.087, 0, Math.PI, 0.07)
    .close();
  const cuff = superellipse(h.cx, base, 0.09, 0.016, 5);
  const pom = ellipse(h.cx - 0.004, base + 0.078, 0.02, 0.018);
  return [
    { outline: dome, tone: "main", layer: 0 },
    { outline: cuff, tone: "dark", layer: 1 },
    { outline: pom, tone: "light", layer: 1, rough: 2.4 },
  ];
}

/** A hanging scarf tail from (x0, y0), `len` long, swinging back by `lean`. */
function scarfTail(x0: number, y0: number, len: number, lean: number, w: number): Outline {
  const x1 = x0 + lean;
  const y1 = y0 - len;
  const b = new PathBuilder().move(x0 - w / 2, y0);
  b.quad(x0 - w / 2 + lean * 0.3, y0 - len * 0.55, x1 - w / 2, y1);
  // Fringe: little cut teeth across the bottom.
  const teeth = 4;
  for (let i = 0; i < teeth; i++) {
    const a = x1 - w / 2 + (w * (i + 0.5)) / teeth;
    const c = x1 - w / 2 + (w * (i + 1)) / teeth;
    b.line(a, y1 - 0.011);
    b.line(c, y1);
  }
  b.quad(x0 + w / 2 + lean * 0.3, y0 - len * 0.55, x0 + w / 2, y0);
  return b.close();
}

function scarf(socketY: number): ItemPiece[] {
  // In the socket frame the neck (head pivot) is at y = −socketY.
  // A thick wrap round the neck with both ends thrown back over the shoulder,
  // trailing behind the way the puppet faces.
  const neck = -socketY;
  const wrap = superellipse(0.002, neck + 0.002, 0.06, 0.026, 3.4);
  const under = scarfTail(-0.024, neck - 0.004, 0.118, -0.02, 0.036);
  const over = scarfTail(-0.04, neck - 0.002, 0.15, -0.034, 0.042);
  return [
    { outline: under, tone: "dark", layer: 0, rough: 1.2 },
    { outline: over, tone: "main", layer: 1, rough: 1.2 },
    { outline: wrap, tone: "main", layer: 2 },
  ];
}

function glasses(h: ReturnType<typeof headIn>): ItemPiece[] {
  // Profile spectacles: one round lens at the front of the face and the arm
  // running back to the ear. The lens is a ring (the head shows through).
  const ly = h.cy + 0.006;
  const lx = h.cx + h.rx - 0.004;
  const lens = ellipse(lx, ly, 0.025, 0.023);
  const hole = ellipse(lx, ly, 0.0165, 0.015);
  // The arm runs back only as far as the ear (mid-head in profile) and
  // hooks down behind it.
  const arm: number[] = [];
  const b = new PathBuilder()
    .move(lx - 0.024, ly + 0.004)
    .quad(h.cx + 0.012, ly + 0.008, h.cx - 0.014, ly + 0.004)
    .quad(h.cx - 0.022, ly + 0.002, h.cx - 0.021, ly - 0.012);
  arm.push(...b.pts);
  return [
    { outline: lens, holes: [hole], tone: "ink", layer: 1, rough: 0.5 },
    { outline: stroke(arm, 0.0055), tone: "ink", layer: 0, rough: 0.5 },
  ];
}

function bow(h: ReturnType<typeof headIn>): ItemPiece[] {
  const top = h.cy + h.ry;
  const loop = (dir: number) =>
    hullOfCircles([
      [0, 0, 0.009],
      [dir * 0.036, 0.012, 0.017],
      [dir * 0.03, -0.012, 0.012],
    ]);
  const place = (o: Outline) => transform(o, 0.45, -0.04, top - 0.012);
  return [
    { outline: place(loop(-1)), tone: "main", layer: 0 },
    { outline: place(loop(1)), tone: "main", layer: 0 },
    { outline: place(ellipse(0, 0, 0.011, 0.012)), tone: "dark", layer: 1 },
  ];
}

function headband(h: ReturnType<typeof headIn>): ItemPiece[] {
  const t0 = 0.3;
  const t1 = 2.8;
  const n = 64;
  const pts: number[] = [];
  const out = 0.004;
  const inn = -0.011;
  for (let i = 0; i <= n; i++) {
    const t = t0 + ((t1 - t0) * i) / n;
    pts.push(h.cx + Math.cos(t) * (h.rx + out), h.cy + Math.sin(t) * (h.ry + out));
  }
  for (let i = n; i >= 0; i--) {
    const t = t0 + ((t1 - t0) * i) / n;
    pts.push(h.cx + Math.cos(t) * (h.rx + inn), h.cy + Math.sin(t) * (h.ry + inn));
  }
  const kx = h.cx + Math.cos(t1) * (h.rx - 0.002);
  const ky = h.cy + Math.sin(t1) * (h.ry - 0.002);
  return [
    { outline: ensureCCW(pts), tone: "main", layer: 0 },
    { outline: ellipse(kx, ky, 0.012, 0.01), tone: "dark", layer: 1 },
  ];
}

function token(h: ReturnType<typeof headIn>): ItemPiece[] {
  // A little paper flower tucked behind the ear: the generic keepsake.
  const cx = h.cx - 0.04;
  const cy = h.cy + 0.06;
  const pts: number[] = [];
  const n = 120;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const r = 0.017 + 0.0065 * Math.cos(5 * a);
    pts.push(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
  }
  return [
    { outline: ensureCCW(pts), tone: "main", layer: 0, rough: 0.6 },
    { outline: ellipse(cx, cy, 0.007), tone: "light", layer: 1 },
  ];
}

/**
 * The pieces for an item kind. `socketY` is the head socket's height in the
 * head bone's frame (puppet.json sockets.head.at[1]).
 */
export function itemPieces(kind: ItemKind, socketY: number): ItemPiece[] {
  const h = headIn(socketY);
  switch (kind) {
    case "flat_cap":
      return flatCap(h);
    case "hat":
      return hat(h);
    case "beanie":
      return beanie(h);
    case "scarf":
      return scarf(socketY);
    case "glasses":
      return glasses(h);
    case "bow":
      return bow(h);
    case "headband":
      return headband(h);
    case "token":
      return token(h);
  }
}
