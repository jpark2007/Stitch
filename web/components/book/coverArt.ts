/**
 * The binding, drawn in code on <canvas>: the card cover with its stitched
 * cloth spine, endpapers, the page-block edges and the contact shadow.
 */

import { ACCENT, canvas, drawStitchLine, drawTape, INK, INK_FAINT, INK_SOFT, paper, rng, setSpacing, type Ctx, type PageFonts } from "./paperArt";
import type { BookCover } from "./types";

/* ------------------------------------------------------------------------ */
/* Cover, endpapers, edges                                                   */
/* ------------------------------------------------------------------------ */

const KRAFT = "#b58c62";
const SPINE_CLOTH = "#7a3a2c";
const THREAD = "#efe2c6";

/** Binding holes, as fractions of the board's length (same on spine and face). */
function stitchHoles(len: number) {
  const n = 7;
  const margin = 0.07;
  return Array.from({ length: n }, (_, i) => len * (margin + ((1 - 2 * margin) * i) / (n - 1)));
}

/** A length of waxed linen thread: shadow, body, a twist of highlights. */
function thread(ctx: Ctx, x0: number, y0: number, x1: number, y1: number, width: number) {
  ctx.save();
  ctx.lineCap = "round";
  ctx.strokeStyle = "rgba(30,15,8,0.35)";
  ctx.lineWidth = width + 1.5;
  ctx.beginPath();
  ctx.moveTo(x0 + 1.2, y0 + 1.8);
  ctx.lineTo(x1 + 1.2, y1 + 1.8);
  ctx.stroke();
  ctx.strokeStyle = THREAD;
  ctx.lineWidth = width;
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.stroke();
  // Twist: short diagonal glints along the strand.
  const len = Math.hypot(x1 - x0, y1 - y0);
  const ux = (x1 - x0) / len;
  const uy = (y1 - y0) / len;
  ctx.strokeStyle = "rgba(255,255,255,0.55)";
  ctx.lineWidth = 1.1;
  for (let d = 3; d < len - 2; d += 4.5) {
    const cx = x0 + ux * d;
    const cy = y0 + uy * d;
    ctx.beginPath();
    ctx.moveTo(cx - uy * width * 0.35 - ux, cy + ux * width * 0.35 - uy);
    ctx.lineTo(cx + uy * width * 0.35 + ux, cy - ux * width * 0.35 + uy);
    ctx.stroke();
  }
  ctx.restore();
}

/**
 * The cover's outside: kraft card, an oxblood cloth tape over the spine held
 * with a whip stitch, and a pasted, taped paper label. The spine runs down
 * the canvas's left edge. `w` × `h` matches the board's aspect.
 */
export function drawCoverArt(cover: BookCover, fonts: PageFonts, w: number, h: number) {
  const { c, ctx } = canvas(w, h);
  paper(ctx, w, h, { seed: 41, tone: KRAFT });
  // Kraft is darker and more flecked than page paper.
  const r = rng(43);
  for (let i = 0; i < (w * h) / 900; i++) {
    ctx.fillStyle = r() > 0.6 ? "rgba(255,240,210,0.12)" : "rgba(70,45,25,0.1)";
    ctx.fillRect(r() * w, r() * h, 1 + r() * 2, 1 + r() * 1.5);
  }

  // Cloth spine tape with a fine weave.
  const tapeW = Math.round(w * 0.14);
  ctx.fillStyle = SPINE_CLOTH;
  ctx.fillRect(0, 0, tapeW, h);
  ctx.globalAlpha = 0.16;
  for (let x = 0; x < tapeW; x += 3) {
    ctx.fillStyle = x % 6 ? "#000" : "#fff";
    ctx.fillRect(x, 0, 1, h);
  }
  for (let y = 0; y < h; y += 3) {
    ctx.fillStyle = y % 6 ? "#000" : "#fff";
    ctx.fillRect(0, y, tapeW, 1);
  }
  ctx.globalAlpha = 1;
  // Tape edge: slightly frayed, with a shadow onto the card.
  const edge = ctx.createLinearGradient(tapeW, 0, tapeW + 14, 0);
  edge.addColorStop(0, "rgba(50,25,15,0.35)");
  edge.addColorStop(1, "rgba(50,25,15,0)");
  ctx.fillStyle = edge;
  ctx.fillRect(tapeW, 0, 14, h);
  ctx.strokeStyle = "rgba(122,58,44,0.8)";
  ctx.lineWidth = 1;
  for (let y = 0; y < h; y += 2 + r() * 3) {
    ctx.beginPath();
    ctx.moveTo(tapeW - 1, y);
    ctx.lineTo(tapeW + 1 + r() * 3, y + (r() - 0.5) * 2);
    ctx.stroke();
  }
  // Stab binding: a row of holes through the tape, thread running hole to
  // hole down the face and wrapping round the spine at every hole.
  const holeX = tapeW * 0.58;
  const holes = stitchHoles(h);
  for (const y of holes) thread(ctx, 0, y, holeX, y, 5);
  for (let i = 0; i < holes.length - 1; i++) thread(ctx, holeX, holes[i], holeX, holes[i + 1], 5);
  for (const y of holes) {
    ctx.fillStyle = "rgba(30,15,8,0.55)";
    ctx.beginPath();
    ctx.arc(holeX, y, 4.2, 0, Math.PI * 2);
    ctx.fill();
  }

  // Pasted label.
  const lw = w * 0.62;
  const lh = h * 0.34;
  const lx = tapeW + (w - tapeW - lw) / 2;
  const ly = h * 0.2;
  ctx.save();
  ctx.translate(lx + lw / 2, ly + lh / 2);
  ctx.rotate(-0.018);
  ctx.shadowColor = "rgba(50,30,15,0.35)";
  ctx.shadowBlur = 10;
  ctx.shadowOffsetY = 3;
  ctx.fillStyle = "#f3ead8";
  ctx.beginPath();
  const lr = rng(47);
  const pts = 60;
  for (let i = 0; i <= pts; i++) {
    // Deckled outline.
    const t = i / pts;
    const per = 2 * (lw + lh);
    let d = t * per;
    let px: number;
    let py: number;
    if (d < lw) [px, py] = [d - lw / 2, -lh / 2];
    else if ((d -= lw) < lh) [px, py] = [lw / 2, d - lh / 2];
    else if ((d -= lh) < lw) [px, py] = [lw / 2 - d, lh / 2];
    else [px, py] = [-lw / 2, lh / 2 - (d - lw)];
    const j = (lr() - 0.5) * 3;
    if (i === 0) ctx.moveTo(px + j, py + j);
    else ctx.lineTo(px + j, py + j);
  }
  ctx.closePath();
  ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.clip();
  paper(ctx, lw, lh, { seed: 49 });
  ctx.restore();

  ctx.save();
  ctx.translate(lx + lw / 2, ly + lh / 2);
  ctx.rotate(-0.018);
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  if (cover.kicker) {
    ctx.fillStyle = INK_FAINT;
    ctx.font = `700 ${Math.round(w * 0.019)}px ${fonts.sans}`;
    setSpacing(ctx, Math.round(w * 0.005));
    ctx.fillText(cover.kicker.toUpperCase(), 0, -lh * 0.22);
    setSpacing(ctx, 0);
  }
  ctx.fillStyle = INK;
  ctx.font = `600 ${Math.round(w * 0.082)}px ${fonts.display}`;
  ctx.fillText(cover.title, 0, lh * 0.07);
  drawStitchLine(ctx, -lw * 0.16, lh * 0.19, lw * 0.16, lh * 0.19, ACCENT, 3, 14, 10);
  if (cover.subtitle) {
    ctx.fillStyle = INK_SOFT;
    ctx.font = `500 ${Math.round(w * 0.042)}px ${fonts.hand}`;
    ctx.fillText(cover.subtitle, 0, lh * 0.37);
  }
  ctx.restore();
  // Tape holding the label down at two corners.
  drawTape(ctx, lx + 8, ly + 6, lw * 0.26, h * 0.032, -0.62, 51, "rgba(233,220,189,0.85)");
  drawTape(ctx, lx + lw - 8, ly + lh - 6, lw * 0.26, h * 0.032, -0.62, 53, "rgba(233,220,189,0.85)");

  // Worn edges.
  const vg = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.4, w / 2, h / 2, Math.max(w, h) * 0.75);
  vg.addColorStop(0, "rgba(0,0,0,0)");
  vg.addColorStop(1, "rgba(60,35,15,0.28)");
  ctx.fillStyle = vg;
  ctx.fillRect(0, 0, w, h);
  return c;
}

/** Endpaper on the inside of the boards: small sprigs on cream. */
export function drawEndpaper(w: number, h: number) {
  const { c, ctx } = canvas(w, h);
  paper(ctx, w, h, { seed: 61, tone: "#e9dcc2" });
  const r = rng(63);
  const cell = w / 9;
  for (let gy = 0; gy < h / cell + 1; gy++) {
    for (let gx = 0; gx < 10; gx++) {
      const x = gx * cell + (gy % 2 ? cell / 2 : 0) + (r() - 0.5) * cell * 0.2;
      const y = gy * cell + (r() - 0.5) * cell * 0.2;
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(r() * Math.PI * 2);
      ctx.strokeStyle = "rgba(127,152,115,0.45)";
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(0, -cell * 0.22);
      ctx.quadraticCurveTo(cell * 0.05, 0, 0, cell * 0.22);
      ctx.stroke();
      ctx.fillStyle = gx % 3 === 0 ? "rgba(185,87,61,0.32)" : "rgba(127,152,115,0.38)";
      for (const s of [-1, 1]) {
        ctx.beginPath();
        ctx.ellipse(s * cell * 0.06, -cell * 0.05 * s, cell * 0.06, cell * 0.025, s * 0.6, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }
  }
  return c;
}

/** Page-block edge: many sheets, so fine uneven stripes across v. */
export function drawPageEdges() {
  const w = 64;
  const h = 512;
  const { c, ctx } = canvas(w, h);
  const r = rng(71);
  let y = 0;
  while (y < h) {
    const t = 1 + Math.floor(r() * 3);
    const shade = r();
    const base = shade > 0.85 ? [214, 200, 174] : [236 - shade * 14, 226 - shade * 14, 206 - shade * 16];
    ctx.fillStyle = `rgb(${base[0]},${base[1]},${base[2]})`;
    ctx.fillRect(0, y, w, t);
    if (r() > 0.55) {
      ctx.fillStyle = "rgba(120,90,60,0.18)";
      ctx.fillRect(0, y + t - 0.5, w, 0.6);
    }
    y += t;
  }
  // A little wobble along the edge.
  ctx.globalAlpha = 0.08;
  for (let i = 0; i < 40; i++) {
    ctx.fillStyle = r() > 0.5 ? "#fff" : "#6b4c30";
    ctx.fillRect(r() * w, 0, 1 + r() * 2, h);
  }
  ctx.globalAlpha = 1;
  return c;
}

/** Soft dark blob for the contact shadow under the book. */
export function drawContactShadow() {
  const { c, ctx } = canvas(256, 256);
  ctx.filter = "blur(20px)";
  ctx.fillStyle = "rgba(0,0,0,1)";
  ctx.fillRect(44, 44, 168, 168);
  return c;
}

/** Cloth over the spine (seen on the closed book's side), with whip stitches. */
export function drawSpineCloth() {
  const w = 128;
  const h = 1024;
  const { c, ctx } = canvas(w, h);
  ctx.fillStyle = SPINE_CLOTH;
  ctx.fillRect(0, 0, w, h);
  ctx.globalAlpha = 0.16;
  for (let x = 0; x < w; x += 3) {
    ctx.fillStyle = x % 6 ? "#000" : "#fff";
    ctx.fillRect(x, 0, 1, h);
  }
  for (let y = 0; y < h; y += 3) {
    ctx.fillStyle = y % 6 ? "#000" : "#fff";
    ctx.fillRect(0, y, w, 1);
  }
  ctx.globalAlpha = 1;
  // The wraps of the stab binding, at the same holes as on the cover (the
  // hole rows are symmetric, so the flipped v lines up).
  for (const y of stitchHoles(h)) thread(ctx, -4, y, w + 4, y, 6);
  // Worn, rounded spine: lighter where it catches the light.
  const g = ctx.createLinearGradient(0, 0, w, 0);
  g.addColorStop(0, "rgba(0,0,0,0.25)");
  g.addColorStop(0.5, "rgba(255,230,200,0.1)");
  g.addColorStop(1, "rgba(0,0,0,0.25)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  return c;
}

