/**
 * Photos on the page: painted stand-in scenes for photos that are not
 * uploaded yet, and prints with a white border taped onto the paper.
 */

import { canvas, drawTape, INK_SOFT, rng, type Ctx, type PageFonts } from "./paperArt";
import type { BookPhoto, PlaceholderScene } from "./types";

let grainTile: HTMLCanvasElement | null = null;
function photoGrain(): HTMLCanvasElement {
  if (grainTile) return grainTile;
  const { c, ctx } = canvas(128, 128);
  const img = ctx.createImageData(128, 128);
  const r = rng(5);
  for (let i = 0; i < img.data.length; i += 4) {
    const v = 128 + (r() - 0.5) * 90;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
    img.data[i + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  grainTile = c;
  return c;
}

function sky(ctx: Ctx, w: number, h: number, top: string, bottom: string, until = 1) {
  const g = ctx.createLinearGradient(0, 0, 0, h * until);
  g.addColorStop(0, top);
  g.addColorStop(1, bottom);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
}

function figure(ctx: Ctx, x: number, base: number, s: number, body: string, dress = false) {
  ctx.fillStyle = body;
  ctx.beginPath();
  if (dress) {
    ctx.moveTo(x - s * 0.42, base);
    ctx.lineTo(x + s * 0.42, base);
    ctx.lineTo(x + s * 0.14, base - s * 1.25);
    ctx.lineTo(x - s * 0.14, base - s * 1.25);
  } else {
    ctx.roundRect(x - s * 0.2, base - s * 1.3, s * 0.4, s * 1.3, s * 0.12);
  }
  ctx.fill();
  ctx.fillStyle = "#3b2a20";
  ctx.beginPath();
  ctx.arc(x, base - s * 1.45, s * 0.2, 0, Math.PI * 2);
  ctx.fill();
}

/** A painted stand-in photo, in faded print colours. */
export function paintScene(scene: PlaceholderScene, w: number, h: number, seed = 1): HTMLCanvasElement {
  const { c, ctx } = canvas(w, h);
  const r = rng(seed);
  switch (scene) {
    case "steps": {
      sky(ctx, w, h, "#a9b3b4", "#d8d3c4", 0.55);
      // Brick church front with an arched door.
      ctx.fillStyle = "#a35f45";
      ctx.fillRect(w * 0.08, h * 0.08, w * 0.84, h * 0.62);
      ctx.strokeStyle = "rgba(60,30,20,0.18)";
      ctx.lineWidth = 1;
      for (let y = h * 0.08; y < h * 0.7; y += h * 0.035) {
        ctx.beginPath();
        ctx.moveTo(w * 0.08, y);
        ctx.lineTo(w * 0.92, y);
        ctx.stroke();
      }
      ctx.fillStyle = "#e9dcc0";
      ctx.beginPath();
      ctx.moveTo(w * 0.36, h * 0.7);
      ctx.lineTo(w * 0.36, h * 0.36);
      ctx.arc(w * 0.5, h * 0.36, w * 0.14, Math.PI, 0);
      ctx.lineTo(w * 0.64, h * 0.7);
      ctx.fill();
      ctx.fillStyle = "#4a3326";
      ctx.beginPath();
      ctx.moveTo(w * 0.39, h * 0.7);
      ctx.lineTo(w * 0.39, h * 0.38);
      ctx.arc(w * 0.5, h * 0.38, w * 0.11, Math.PI, 0);
      ctx.lineTo(w * 0.61, h * 0.7);
      ctx.fill();
      for (const x of [0.17, 0.76]) {
        ctx.fillStyle = "#e9dcc0";
        ctx.beginPath();
        ctx.moveTo(w * x, h * 0.42);
        ctx.lineTo(w * x, h * 0.26);
        ctx.arc(w * (x + 0.035), h * 0.26, w * 0.035, Math.PI, 0);
        ctx.lineTo(w * (x + 0.07), h * 0.42);
        ctx.fill();
        ctx.fillStyle = "#6f8aa0";
        ctx.fillRect(w * (x + 0.012), h * 0.27, w * 0.046, h * 0.14);
      }
      // Steps.
      for (let i = 0; i < 5; i++) {
        const y = h * (0.7 + i * 0.06);
        ctx.fillStyle = i % 2 ? "#c9c0b0" : "#d8cfbe";
        ctx.fillRect(w * (0.04 - i * 0.02), y, w * (0.92 + i * 0.04), h * 0.06);
        ctx.fillStyle = "rgba(70,50,35,0.18)";
        ctx.fillRect(w * (0.04 - i * 0.02), y, w * (0.92 + i * 0.04), 2);
      }
      // Two people and the scattered hymn books.
      figure(ctx, w * 0.42, h * 0.82, h * 0.16, "#b2473a", true);
      figure(ctx, w * 0.58, h * 0.88, h * 0.17, "#34465c");
      const books = ["#5b3a6d", "#2f4f6f", "#7a2f2f", "#3f5f3f"];
      for (let i = 0; i < 6; i++) {
        ctx.save();
        ctx.translate(w * (0.32 + r() * 0.4), h * (0.85 + r() * 0.1));
        ctx.rotate((r() - 0.5) * 1.6);
        ctx.fillStyle = books[i % books.length];
        ctx.fillRect(-w * 0.025, -h * 0.012, w * 0.05, h * 0.024);
        ctx.restore();
      }
      // Rain.
      ctx.strokeStyle = "rgba(255,255,255,0.28)";
      ctx.lineWidth = 1;
      for (let i = 0; i < 90; i++) {
        const x = r() * w;
        const y = r() * h;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x - 4, y + 14);
        ctx.stroke();
      }
      break;
    }
    case "sea": {
      sky(ctx, w, h, "#e7c9a2", "#f1dfbf", 0.5);
      const sun = ctx.createRadialGradient(w * 0.68, h * 0.3, 0, w * 0.68, h * 0.3, w * 0.22);
      sun.addColorStop(0, "rgba(255,240,205,1)");
      sun.addColorStop(0.3, "rgba(255,235,200,0.75)");
      sun.addColorStop(1, "rgba(255,235,200,0)");
      ctx.fillStyle = sun;
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = "#6f989b";
      ctx.fillRect(0, h * 0.5, w, h * 0.2);
      ctx.strokeStyle = "rgba(255,245,220,0.55)";
      for (let i = 0; i < 26; i++) {
        const y = h * (0.52 + r() * 0.16);
        const x = r() * w;
        ctx.lineWidth = 1 + r();
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + 10 + r() * 30, y);
        ctx.stroke();
      }
      const sand = ctx.createLinearGradient(0, h * 0.7, 0, h);
      sand.addColorStop(0, "#d9c29a");
      sand.addColorStop(1, "#c7aa7c");
      ctx.fillStyle = sand;
      ctx.beginPath();
      ctx.moveTo(0, h * 0.7);
      ctx.bezierCurveTo(w * 0.3, h * 0.68, w * 0.6, h * 0.74, w, h * 0.69);
      ctx.lineTo(w, h);
      ctx.lineTo(0, h);
      ctx.fill();
      figure(ctx, w * 0.36, h * 0.86, h * 0.18, "#b2473a", true);
      figure(ctx, w * 0.45, h * 0.87, h * 0.2, "#34465c");
      break;
    }
    case "wedding": {
      sky(ctx, w, h, "#d6cdbd", "#e8dfcf");
      ctx.fillStyle = "#cbbca3";
      ctx.beginPath();
      ctx.moveTo(w * 0.15, h);
      ctx.lineTo(w * 0.15, h * 0.32);
      ctx.arc(w * 0.5, h * 0.32, w * 0.35, Math.PI, 0);
      ctx.lineTo(w * 0.85, h);
      ctx.fill();
      ctx.fillStyle = "#5a4033";
      ctx.beginPath();
      ctx.moveTo(w * 0.24, h);
      ctx.lineTo(w * 0.24, h * 0.36);
      ctx.arc(w * 0.5, h * 0.36, w * 0.26, Math.PI, 0);
      ctx.lineTo(w * 0.76, h);
      ctx.fill();
      ctx.fillStyle = "#d8cfbe";
      ctx.fillRect(0, h * 0.86, w, h * 0.14);
      figure(ctx, w * 0.42, h * 0.9, h * 0.32, "#f7f1e6", true);
      figure(ctx, w * 0.6, h * 0.9, h * 0.34, "#2e3440");
      // Bouquet and confetti.
      ctx.fillStyle = "#c46a6a";
      ctx.beginPath();
      ctx.arc(w * 0.47, h * 0.7, w * 0.035, 0, Math.PI * 2);
      ctx.fill();
      const cols = ["#e7b7a6", "#f3e2b8", "#b9cdb8", "#ffffff"];
      for (let i = 0; i < 70; i++) {
        ctx.fillStyle = cols[i % cols.length];
        ctx.save();
        ctx.translate(r() * w, r() * h * 0.8);
        ctx.rotate(r() * 3);
        ctx.fillRect(-3, -2, 6, 4);
        ctx.restore();
      }
      break;
    }
    case "house": {
      sky(ctx, w, h, "#bfd0d2", "#e8e0c9", 0.6);
      ctx.fillStyle = "#8c9b6b";
      ctx.fillRect(0, h * 0.66, w, h * 0.34);
      ctx.fillStyle = "#7a8a5a";
      ctx.beginPath();
      ctx.arc(w * 0.84, h * 0.46, w * 0.14, 0, Math.PI * 2);
      ctx.arc(w * 0.78, h * 0.36, w * 0.1, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#5e4535";
      ctx.fillRect(w * 0.82, h * 0.5, w * 0.03, h * 0.17);
      // House with a crooked porch and a round window.
      ctx.fillStyle = "#d9a37c";
      ctx.fillRect(w * 0.22, h * 0.4, w * 0.46, h * 0.3);
      ctx.fillStyle = "#7b4c3a";
      ctx.beginPath();
      ctx.moveTo(w * 0.18, h * 0.41);
      ctx.lineTo(w * 0.45, h * 0.16);
      ctx.lineTo(w * 0.72, h * 0.41);
      ctx.fill();
      ctx.fillStyle = "#f3e5bd";
      ctx.beginPath();
      ctx.arc(w * 0.45, h * 0.31, w * 0.035, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillRect(w * 0.27, h * 0.47, w * 0.08, h * 0.08);
      ctx.fillRect(w * 0.55, h * 0.47, w * 0.08, h * 0.08);
      ctx.fillStyle = "#4f6a73";
      ctx.fillRect(w * 0.41, h * 0.52, w * 0.08, h * 0.18);
      ctx.save();
      ctx.translate(w * 0.45, h * 0.6);
      ctx.rotate(-0.03);
      ctx.fillStyle = "#efe6d4";
      ctx.fillRect(-w * 0.2, -h * 0.07, w * 0.4, h * 0.02);
      ctx.fillRect(-w * 0.19, -h * 0.07, w * 0.012, h * 0.1);
      ctx.fillRect(w * 0.18, -h * 0.07, w * 0.012, h * 0.1);
      ctx.restore();
      // Moving boxes.
      ctx.fillStyle = "#c4a37a";
      ctx.fillRect(w * 0.12, h * 0.74, w * 0.1, h * 0.08);
      ctx.fillRect(w * 0.16, h * 0.68, w * 0.08, h * 0.06);
      ctx.fillStyle = "rgba(80,55,35,0.25)";
      ctx.fillRect(w * 0.12, h * 0.775, w * 0.1, 2);
      break;
    }
    default: {
      sky(ctx, w, h, "#e3cfa6", "#efdcb8", 0.6);
      const sun = ctx.createRadialGradient(w * 0.7, h * 0.28, 0, w * 0.7, h * 0.28, w * 0.18);
      sun.addColorStop(0, "rgba(255,243,210,1)");
      sun.addColorStop(1, "rgba(255,243,210,0)");
      ctx.fillStyle = sun;
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = "#8a9a64";
      ctx.beginPath();
      ctx.ellipse(w * 0.8, h * 0.95, w * 0.6, h * 0.42, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#6f8458";
      ctx.beginPath();
      ctx.ellipse(w * 0.15, h * 1.02, w * 0.6, h * 0.42, 0, 0, Math.PI * 2);
      ctx.fill();
      // The green sedan on the road home.
      ctx.fillStyle = "#c9b48a";
      ctx.beginPath();
      ctx.moveTo(0, h * 0.86);
      ctx.bezierCurveTo(w * 0.4, h * 0.8, w * 0.6, h * 0.84, w, h * 0.78);
      ctx.lineTo(w, h * 0.84);
      ctx.bezierCurveTo(w * 0.6, h * 0.9, w * 0.4, h * 0.86, 0, h * 0.93);
      ctx.fill();
      ctx.fillStyle = "#4f6f4a";
      ctx.beginPath();
      ctx.roundRect(w * 0.42, h * 0.72, w * 0.22, h * 0.07, 6);
      ctx.roundRect(w * 0.46, h * 0.67, w * 0.12, h * 0.07, 8);
      ctx.fill();
      ctx.fillStyle = "#2a2a28";
      ctx.beginPath();
      ctx.arc(w * 0.47, h * 0.795, w * 0.022, 0, Math.PI * 2);
      ctx.arc(w * 0.59, h * 0.795, w * 0.022, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  // Print finish: faded, warm, grainy, darker corners.
  ctx.globalCompositeOperation = "saturation";
  ctx.fillStyle = "rgba(128,128,128,0.22)";
  ctx.fillRect(0, 0, w, h);
  ctx.globalCompositeOperation = "soft-light";
  ctx.fillStyle = "rgba(255,214,160,0.45)";
  ctx.fillRect(0, 0, w, h);
  ctx.globalCompositeOperation = "overlay";
  ctx.globalAlpha = 0.16;
  ctx.fillStyle = ctx.createPattern(photoGrain(), "repeat") ?? "#888";
  ctx.fillRect(0, 0, w, h);
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";
  const v = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.3, w / 2, h / 2, Math.max(w, h) * 0.75);
  v.addColorStop(0, "rgba(40,25,10,0)");
  v.addColorStop(1, "rgba(40,25,10,0.32)");
  ctx.fillStyle = v;
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "rgba(255,250,235,0.08)";
  ctx.fillRect(0, 0, w, h);
  return c;
}

function loadImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;
  });
}

/** A print with a white border taped onto the page; returns its bottom y. */
export async function drawPhoto(
  ctx: Ctx,
  photo: BookPhoto,
  x: number,
  y: number,
  width: number,
  angle: number,
  seed: number,
  fonts: PageFonts,
  tape: "top" | "corners",
) {
  const aspect = photo.aspect ?? 4 / 3;
  const pad = Math.round(width * 0.05);
  const imgW = width - pad * 2;
  const imgH = Math.round(imgW / aspect);
  const capH = photo.caption ? Math.round(width * 0.17) : 0;
  const h = imgH + pad * 2 + capH;

  let image: CanvasImageSource = paintScene(photo.scene ?? "hills", imgW * 2, imgH * 2, seed);
  if (photo.src) {
    const loaded = await loadImage(photo.src);
    if (loaded) image = loaded;
  }

  ctx.save();
  ctx.translate(x + width / 2, y + h / 2);
  ctx.rotate(angle);
  ctx.shadowColor = "rgba(74,47,29,0.28)";
  ctx.shadowBlur = 14;
  ctx.shadowOffsetY = 5;
  ctx.fillStyle = "#fbf8f1";
  ctx.fillRect(-width / 2, -h / 2, width, h);
  ctx.shadowColor = "transparent";
  // Photo paper: very slight warm sheen.
  const sheen = ctx.createLinearGradient(-width / 2, -h / 2, width / 2, h / 2);
  sheen.addColorStop(0, "rgba(255,255,255,0.25)");
  sheen.addColorStop(1, "rgba(200,180,150,0.12)");
  ctx.fillStyle = sheen;
  ctx.fillRect(-width / 2, -h / 2, width, h);

  const ix = -width / 2 + pad;
  const iy = -h / 2 + pad;
  if (image instanceof HTMLImageElement) {
    // Cover-fit an uploaded photo.
    const s = Math.max(imgW / image.width, imgH / image.height);
    const sw = imgW / s;
    const sh = imgH / s;
    ctx.drawImage(image, (image.width - sw) / 2, (image.height - sh) / 2, sw, sh, ix, iy, imgW, imgH);
  } else {
    ctx.drawImage(image, ix, iy, imgW, imgH);
  }
  ctx.strokeStyle = "rgba(60,35,15,0.12)";
  ctx.lineWidth = 1.5;
  ctx.strokeRect(ix, iy, imgW, imgH);

  if (photo.caption) {
    ctx.fillStyle = INK_SOFT;
    ctx.font = `500 ${Math.round(width * 0.1)}px ${fonts.hand}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(photo.caption, 0, h / 2 - capH / 2 - pad * 0.3, imgW);
  }

  if (tape === "top") {
    drawTape(ctx, (seed % 3) * 6 - 6, -h / 2 + 2, width * 0.38, width * 0.095, -angle * 0.8 + 0.05, seed);
  } else {
    drawTape(ctx, -width / 2 + width * 0.07, -h / 2 + width * 0.06, width * 0.3, width * 0.085, -0.72, seed);
    drawTape(ctx, width / 2 - width * 0.07, h / 2 - width * 0.06, width * 0.3, width * 0.085, -0.72, seed + 3);
  }
  ctx.restore();
  return y + h;
}
