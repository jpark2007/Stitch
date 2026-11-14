/**
 * The time camera: which year sits at the centre of the screen and how many
 * pixels one year takes. Zoom keeps the year under the cursor still.
 */

export interface Camera {
  /** Year at the horizontal centre of the view. */
  cx: number;
  /** Pixels per year. */
  s: number;
}

export interface Bounds {
  tMin: number;
  tMax: number;
  width: number;
}

/** Side margin (px) kept free at full zoom-out so threads never touch the edge. */
const EDGE = 28;

/** Fully zoomed out: the whole family's time fits the screen. */
export function minScale(b: Bounds): number {
  return Math.max(1, (b.width - EDGE * 2) / (b.tMax - b.tMin));
}

/** Closest zoom: a couple of years across, or ~320px per year on wide screens. */
export function maxScale(b: Bounds): number {
  return Math.max(minScale(b) * 2, Math.min(340, b.width / 2.2));
}

/** "Single years with titles". */
export function yearScale(b: Bounds): number {
  return clampScale(Math.max(b.width / 11, 118), b);
}

export function clampScale(s: number, b: Bounds): number {
  return Math.min(maxScale(b), Math.max(minScale(b), s));
}

/** Keep the view inside the family's time range (centred if it is wider). */
export function clampCam(c: Camera, b: Bounds): Camera {
  const s = clampScale(c.s, b);
  const halfSpan = b.width / 2 / s;
  const lo = b.tMin + halfSpan - EDGE / s;
  const hi = b.tMax - halfSpan + EDGE / s;
  const cx = lo > hi ? (b.tMin + b.tMax) / 2 : Math.min(hi, Math.max(lo, c.cx));
  return { cx, s };
}

export function toX(t: number, c: Camera, width: number): number {
  return width / 2 + (t - c.cx) * c.s;
}

export function toT(x: number, c: Camera, width: number): number {
  return c.cx + (x - width / 2) / c.s;
}

/** Zoom by `factor` keeping screen x `ax` fixed on the same year. */
export function zoomAt(c: Camera, factor: number, ax: number, b: Bounds): Camera {
  const s = clampScale(c.s * factor, b);
  const t = toT(ax, c, b.width);
  return clampCam({ s, cx: t - (ax - b.width / 2) / s }, b);
}

export type ZoomParam = "decade" | "year" | number | null;

export function parseZoom(v: string | undefined): ZoomParam {
  if (!v) return null;
  if (v === "decade" || v === "decades") return "decade";
  if (v === "year" || v === "years") return "year";
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * Starting camera from the URL. A numeric zoom is a multiple of the
 * fully-zoomed-out scale (1 = whole family, ~8 = single years on a laptop).
 */
export function initialCamera(zoom: ZoomParam, focus: number | null, b: Bounds, fallbackFocus: number): Camera {
  let s = minScale(b);
  if (zoom === "year") s = yearScale(b);
  else if (typeof zoom === "number") s = clampScale(minScale(b) * zoom, b);
  const cx = focus ?? (zoom && zoom !== "decade" ? fallbackFocus : (b.tMin + b.tMax) / 2);
  return clampCam({ cx, s }, b);
}
