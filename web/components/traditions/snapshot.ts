/**
 * The snapshot taped onto a tradition's story page: the people who keep it,
 * as their paper puppets (the tree's, drawn as the book draws a teller's
 * label) standing in a row before a painted place, each name hand-lettered
 * under their feet. The book prints it as a photo (BookPhoto.src), so it
 * gets the white border, tape and caption of every print.
 *
 * Browser-only (canvas). Uses the book's own painters, never copies them.
 */

import { drawPuppetAvatar } from "@/components/book/labelArt";
import { INK, type PageFonts } from "@/components/book/paperArt";
import { paintScene } from "@/components/book/photoArt";
import type { PlaceholderScene } from "@/components/book/types";
import type { Tradition } from "@/lib/types";
import type { TraditionPerson } from "./traditionBook";

const W = 960;
const H = 720;
/** At most this many puppets stand in the picture; the rest are "+ 3 more". */
const MAX_SHOWN = 6;

/** The painted place behind the people, by category. */
const PLACE: Record<Tradition["category"], PlaceholderScene> = {
  food: "house",
  holiday: "house",
  trip: "sea",
  music: "house",
  game: "hills",
  faith: "steps",
  craft: "hills",
  other: "hills",
};

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return (h >>> 0) % 997;
}

/**
 * Draw the snapshot of `keepers` for `tradition` and return it as an image
 * URL (a JPEG data URL), or null when the canvas is unavailable.
 */
export function drawKeepersSnapshot(tradition: Pick<Tradition, "id" | "category">, keepers: readonly TraditionPerson[], fonts: PageFonts): string | null {
  if (typeof document === "undefined" || keepers.length === 0) return null;
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const ctx = c.getContext("2d");
  if (!ctx) return null;
  const seed = 7 + hash(tradition.id);
  ctx.drawImage(paintScene(PLACE[tradition.category] ?? "hills", W, H, seed), 0, 0);
  // A soft lift of light where the people stand, so they read before the place.
  const glow = ctx.createRadialGradient(W / 2, H * 0.82, 40, W / 2, H * 0.8, W * 0.62);
  glow.addColorStop(0, "rgba(255,246,228,0.5)");
  glow.addColorStop(1, "rgba(255,246,228,0)");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, W, H);

  const shown = keepers.slice(0, MAX_SHOWN);
  const more = keepers.length - shown.length;
  const n = shown.length;
  const step = Math.min(200, (W - 120) / n);
  const tall = Math.min(380, step * 1.75);
  const wide = (tall * 100) / 130;
  const base = H * 0.86;
  const x0 = W / 2 - ((n - 1) * step) / 2;
  shown.forEach((p, i) => {
    // A little up and down, as a family stands for a picture; the first one waves.
    const lift = (i % 2) * tall * 0.04;
    drawPuppetAvatar(ctx, x0 + i * step - wide / 2, base - tall - lift, tall, p.color, i === 0 && n > 1);
  });

  // Names under their feet, on a pale strip like a label stuck to the print.
  const nameSize = Math.round(Math.min(46, step * 0.3));
  ctx.font = `600 ${nameSize}px ${fonts.hand}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const stripY = base + 6;
  const stripH = H - stripY - 8;
  ctx.fillStyle = "rgba(251,247,238,0.82)";
  ctx.fillRect(24, stripY, W - 48, stripH);
  ctx.fillStyle = INK;
  shown.forEach((p, i) => {
    ctx.fillText(p.name, x0 + i * step, stripY + stripH / 2 + 2, step - 10);
  });
  if (more > 0) {
    ctx.font = `600 ${Math.round(nameSize * 0.8)}px ${fonts.hand}`;
    ctx.textAlign = "right";
    ctx.fillText(`+ ${more} more`, W - 40, 44);
  }
  try {
    return c.toDataURL("image/jpeg", 0.9);
  } catch {
    return null;
  }
}
