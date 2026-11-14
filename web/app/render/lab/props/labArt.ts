/**
 * Stand-in "generated" art for the props lab, drawn on a canvas and handed to
 * PropActor as PNG data URLs, so the lab exercises the real art path (URL ->
 * texture, alpha cut, parts sized from the body's pixels, state swaps).
 *
 * Flat cut-paper colours, no outlines, transparent background. Browser-only.
 */

type Ctx = CanvasRenderingContext2D;

function sheet(w: number, h: number, draw: (ctx: Ctx) => void): string {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d")!;
  draw(ctx);
  return c.toDataURL("image/png");
}

function rr(ctx: Ctx, x: number, y: number, w: number, h: number, r: number | number[]) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

/** A glued paper layer: soft shadow under it. */
function glue(ctx: Ctx, fill: string, lift = 1) {
  ctx.save();
  ctx.shadowColor = "rgba(47, 30, 18, 0.3)";
  ctx.shadowBlur = 6 * lift;
  ctx.shadowOffsetY = 3 * lift;
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.restore();
}

/* The green sedan ------------------------------------------------------- */

/** Body art size. The wheel art is drawn at the same pixel scale. */
export const CAR_W = 1100;
export const CAR_H = 500;
/** Wheel diameter in body pixels (0.34 of the body height). */
export const WHEEL_PX = 170;
/** Wheel centres as fractions of the body image. */
export const CAR_WHEELS = { wheel_back: [0.215, 0.8], wheel_front: [0.785, 0.8] } as const;

const SAGE = "#6f8f62";
const SAGE_DARK = "#56734c";
const SAGE_LIGHT = "#8eaa7d";
const CREAM = "#f4ecdb";
const PANE = "#dfe6dc";
const RUST = "#b9573d";
const INK = "#3b322b";

function carBody(): string {
  return sheet(CAR_W, CAR_H, (ctx) => {
    const W = CAR_W;
    const H = CAR_H;
    // Cabin.
    ctx.beginPath();
    ctx.moveTo(W * 0.22, H * 0.44);
    ctx.bezierCurveTo(W * 0.27, H * 0.2, W * 0.3, H * 0.1, W * 0.38, H * 0.09);
    ctx.lineTo(W * 0.63, H * 0.09);
    ctx.bezierCurveTo(W * 0.69, H * 0.1, W * 0.73, H * 0.22, W * 0.8, H * 0.44);
    ctx.closePath();
    ctx.fillStyle = SAGE;
    ctx.fill();
    // Body.
    rr(ctx, W * 0.015, H * 0.4, W * 0.97, H * 0.44, [H * 0.16, H * 0.2, H * 0.1, H * 0.1]);
    ctx.fillStyle = SAGE;
    ctx.fill();
    // Roof highlight.
    rr(ctx, W * 0.38, H * 0.1, W * 0.25, H * 0.035, H * 0.02);
    glue(ctx, SAGE_LIGHT, 0.4);
    // Windows.
    ctx.beginPath();
    ctx.moveTo(W * 0.27, H * 0.42);
    ctx.bezierCurveTo(W * 0.3, H * 0.27, W * 0.33, H * 0.17, W * 0.39, H * 0.165);
    ctx.lineTo(W * 0.485, H * 0.165);
    ctx.lineTo(W * 0.485, H * 0.42);
    ctx.closePath();
    glue(ctx, PANE, 0.6);
    ctx.beginPath();
    ctx.moveTo(W * 0.515, H * 0.42);
    ctx.lineTo(W * 0.515, H * 0.165);
    ctx.lineTo(W * 0.62, H * 0.165);
    ctx.bezierCurveTo(W * 0.67, H * 0.18, W * 0.7, H * 0.28, W * 0.745, H * 0.42);
    ctx.closePath();
    glue(ctx, PANE, 0.6);
    // Lower body panel, a shade darker, and the waist stripe.
    rr(ctx, W * 0.03, H * 0.63, W * 0.94, H * 0.2, [0, 0, H * 0.09, H * 0.09]);
    glue(ctx, SAGE_DARK, 0.5);
    rr(ctx, W * 0.03, H * 0.6, W * 0.94, H * 0.035, H * 0.015);
    glue(ctx, CREAM, 0.4);
    // Door seams and handles.
    for (const x of [0.5, 0.29]) {
      rr(ctx, W * x - 2, H * 0.44, 4, H * 0.36, 2);
      glue(ctx, SAGE_DARK, 0.2);
    }
    for (const x of [0.43, 0.64]) {
      rr(ctx, W * x, H * 0.48, W * 0.04, H * 0.025, H * 0.012);
      glue(ctx, CREAM, 0.3);
    }
    // Lamps and bumpers.
    rr(ctx, W * 0.93, H * 0.46, W * 0.05, H * 0.08, H * 0.03);
    glue(ctx, "#f6dc9a", 0.5);
    rr(ctx, W * 0.017, H * 0.47, W * 0.03, H * 0.07, H * 0.02);
    glue(ctx, RUST, 0.5);
    rr(ctx, W * 0.0, H * 0.76, W * 0.11, H * 0.07, H * 0.035);
    glue(ctx, CREAM, 0.6);
    rr(ctx, W * 0.89, H * 0.76, W * 0.11, H * 0.07, H * 0.035);
    glue(ctx, CREAM, 0.6);
    // Wheel wells.
    for (const [u, v] of Object.values(CAR_WHEELS)) {
      ctx.beginPath();
      ctx.arc(W * u, H * v, WHEEL_PX * 0.6, Math.PI, 0);
      ctx.closePath();
      glue(ctx, "#2f2620", 0.3);
    }
  });
}

function carWheel(): string {
  const S = WHEEL_PX;
  return sheet(S, S, (ctx) => {
    const c = S / 2;
    const r = S * 0.49;
    ctx.beginPath();
    ctx.arc(c, c, r, 0, Math.PI * 2);
    ctx.fillStyle = INK;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(c, c, r * 0.62, 0, Math.PI * 2);
    glue(ctx, CREAM, 0.5);
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2;
      ctx.save();
      ctx.translate(c, c);
      ctx.rotate(a);
      rr(ctx, -r * 0.05, 0, r * 0.1, r * 0.58, r * 0.04);
      ctx.restore();
      glue(ctx, "#cbbfa8", 0.15);
    }
    ctx.beginPath();
    ctx.arc(c, c, r * 0.2, 0, Math.PI * 2);
    glue(ctx, RUST, 0.4);
    // One painted dot on the tyre so even a slow roll reads.
    ctx.beginPath();
    ctx.arc(c, c - r * 0.8, r * 0.07, 0, Math.PI * 2);
    glue(ctx, "#6c5d50", 0.1);
  });
}

/* The suitcase ----------------------------------------------------------- */

export const CASE_SIZE = 600;
const TAN = "#c98f52";
const TAN_DARK = "#a8723d";
const TAN_DEEP = "#7c5130";
const LINING = "#e7c9b5";

function caseBody(ctx: Ctx, S: number) {
  rr(ctx, S * 0.06, S * 0.42, S * 0.88, S * 0.56, S * 0.06);
  ctx.fillStyle = TAN;
  ctx.fill();
  // Straps.
  for (const x of [0.27, 0.67]) {
    rr(ctx, S * x, S * 0.42, S * 0.06, S * 0.56, 2);
    glue(ctx, TAN_DEEP, 0.4);
  }
  // Corner caps.
  rr(ctx, S * 0.06, S * 0.88, S * 0.1, S * 0.1, [0, 0, 0, S * 0.06]);
  glue(ctx, TAN_DEEP, 0.3);
  rr(ctx, S * 0.84, S * 0.88, S * 0.1, S * 0.1, [0, 0, S * 0.06, 0]);
  glue(ctx, TAN_DEEP, 0.3);
}

function caseClosed(): string {
  const S = CASE_SIZE;
  return sheet(S, S, (ctx) => {
    // Handle loop.
    ctx.beginPath();
    ctx.roundRect(S * 0.36, S * 0.28, S * 0.28, S * 0.17, S * 0.07);
    ctx.roundRect(S * 0.41, S * 0.33, S * 0.18, S * 0.12, S * 0.03);
    ctx.fillStyle = TAN_DEEP;
    ctx.fill("evenodd");
    caseBody(ctx, S);
    // Lid seam and clasps.
    rr(ctx, S * 0.06, S * 0.55, S * 0.88, S * 0.025, 2);
    glue(ctx, TAN_DARK, 0.4);
    for (const x of [0.36, 0.58]) {
      rr(ctx, S * x, S * 0.52, S * 0.06, S * 0.08, S * 0.012);
      glue(ctx, "#e6c375", 0.5);
    }
    luggageTag(ctx, S);
  });
}

/** A paper tag tied to the right of the handle. It makes the case
 *  asymmetric, so the held demo shows the case never mirrors. */
function luggageTag(ctx: Ctx, S: number) {
  ctx.save();
  ctx.strokeStyle = TAN_DEEP;
  ctx.lineWidth = S * 0.008;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(S * 0.6, S * 0.4);
  ctx.quadraticCurveTo(S * 0.68, S * 0.43, S * 0.75, S * 0.47);
  ctx.stroke();
  ctx.translate(S * 0.79, S * 0.55);
  ctx.rotate(-0.22);
  rr(ctx, -S * 0.05, -S * 0.08, S * 0.1, S * 0.15, [S * 0.03, S * 0.03, S * 0.012, S * 0.012]);
  glue(ctx, CREAM, 0.6);
  ctx.beginPath();
  ctx.arc(0, -S * 0.05, S * 0.012, 0, Math.PI * 2);
  ctx.fillStyle = TAN_DEEP;
  ctx.fill();
  ctx.fillStyle = RUST;
  ctx.fillRect(-S * 0.03, S * 0.0, S * 0.06, S * 0.008);
  ctx.fillRect(-S * 0.03, S * 0.025, S * 0.04, S * 0.008);
  ctx.restore();
}

function caseOpen(): string {
  const S = CASE_SIZE;
  return sheet(S, S, (ctx) => {
    // The lid, swung up behind, showing its lining.
    rr(ctx, S * 0.08, S * 0.04, S * 0.84, S * 0.44, S * 0.06);
    ctx.fillStyle = TAN_DARK;
    ctx.fill();
    rr(ctx, S * 0.13, S * 0.09, S * 0.74, S * 0.36, S * 0.04);
    glue(ctx, LINING, 0.5);
    rr(ctx, S * 0.13, S * 0.3, S * 0.74, S * 0.15, [S * 0.06, S * 0.06, 0, 0]);
    glue(ctx, "#d9b39b", 0.3);
    caseBody(ctx, S);
    // Folded things peeking out of the open case.
    rr(ctx, S * 0.14, S * 0.36, S * 0.36, S * 0.11, S * 0.03);
    glue(ctx, "#9fb3c8", 0.6);
    rr(ctx, S * 0.44, S * 0.38, S * 0.4, S * 0.09, S * 0.03);
    glue(ctx, CREAM, 0.6);
    rr(ctx, S * 0.06, S * 0.44, S * 0.88, S * 0.06, 2);
    glue(ctx, TAN_DARK, 0.5);
  });
}

/* ----------------------------------------------------------------------- */

let cached: Record<string, string> | null = null;

/** Media id -> data URL for the lab's art. Drawn once, on first use. */
export function labArt(): Record<string, string> {
  if (!cached) {
    cached = {
      "lab/car_body": carBody(),
      "lab/car_wheel": carWheel(),
      "lab/case_closed": caseClosed(),
      "lab/case_open": caseOpen(),
    };
  }
  return cached;
}

/** A resolveArt for PropActor: lab ids to data URLs, anything else as is. */
export function resolveLabArt(mediaId: string): string {
  return labArt()[mediaId] ?? mediaId;
}
