/**
 * The threads engine: owns the camera, the per-frame animation (zoom and
 * pan easing, fling, highlight and hover fades, the sew-in intro), drawing
 * through renderer.ts, title placement, hit-testing and pointer input.
 *
 * It knows nothing about React or the DOM overlay: the view creates one per
 * model, calls `resize` when the box changes, and reads the engine's state
 * in `onFrame` to place links and titles. Pointer and wheel input is wired
 * by `bindInput`, which reports taps and hovers back through handlers.
 */

import {
  clampCam,
  initialCamera,
  maxScale,
  minScale,
  toT,
  zoomAt,
  type Bounds,
  type Camera,
  type ZoomParam,
} from "./camera";
import type { ThreadsModel } from "./model";
import { drawFrame, makeLayout, type FrameGeometry, type Layout } from "./renderer";
import { placeTitles, titleFontPx, type TitleBox, type TitlePlacement } from "./titles";

const INTRO_MS = 1900;
const KNOT_POP_MS = 460;

export interface EngineOptions {
  canvas: HTMLCanvasElement;
  model: ThreadsModel;
  fonts: { hand: string; sans: string; serif: string };
  reduced: boolean;
  /** Seeds from the URL. */
  zoom: ZoomParam;
  /** Year to centre on (already +0.5 to sit mid-year), or null. */
  focus: number | null;
  /** Strand to follow, or -1. */
  highlight: number;
  /** Called after every drawn frame. */
  onFrame: (engine: ThreadsEngine) => void;
}

export type Hit = { kind: "knot"; i: number } | { kind: "strand"; i: number } | null;

export interface InputHandlers {
  /** A knot (or its title) was tapped. */
  open(knot: number): void;
  /** A thread was tapped (its index), or empty paper while following (-1). */
  follow(strand: number): void;
  /** The pointer came to rest on a knot. */
  hoverKnot(knot: number): void;
  /** Any first touch, click or scroll. */
  interact(): void;
}

/**
 * Distance between two fingers for pinch zoom: the full gap, floored so that
 * fingers set down close together cannot turn a small spread into a huge ratio.
 */
function pinchGap(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.max(40, Math.hypot(a.x - b.x, a.y - b.y));
}

interface Drag {
  x: number;
  moved: boolean;
  lastX: number;
  lastT: number;
  pinch: number;
}

export class ThreadsEngine {
  readonly model: ThreadsModel;
  layout: Layout;
  bounds: Bounds;
  cam: Camera = { cx: 0, s: 1 };
  /** Followed strand, or -1. */
  highlight: number;
  hoverKnot = -1;
  hoverStrand = -1;
  /** Per strand: 0 = full colour, 1 = faded; and 0..1 how lifted. */
  dim: number[];
  lift: number[];
  /** Per knot 0..1 hover swell. */
  swell: number[];
  /** Intro: threads are sewn up to this year. */
  reveal: number;
  /** Per knot: when the intro needle reached it (ms), and its pop 0..1. */
  pop: number[];
  geom: FrameGeometry | null = null;
  titles: TitleBox[] = [];
  titlePlacements = new Map<number, TitlePlacement>();
  /** Last frame's draw time (ms), for profiling. */
  drawMs = 0;

  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D | null;
  private readonly fonts: EngineOptions["fonts"];
  private readonly onFrame: EngineOptions["onFrame"];
  private readonly seed: { zoom: ZoomParam; focus: number | null };
  private dpr = 1;
  private ready = false;
  /** Zoom animation target and the screen point it is anchored on. */
  private target: { s: number; ax: number; at: number } | null = null;
  private panTarget: number | null = null;
  /** Fling speed, px/s. */
  private velocity = 0;
  private revealStart: number;
  private popAt: number[];
  private titleSpot = new Map<number, number>();
  private titleW: number[];
  private raf = 0;
  private last = 0;
  private pointers = new Map<number, { x: number; y: number }>();
  private drag: Drag | null = null;
  private lastTap: { t: number; x: number; y: number } | null = null;

  constructor(o: EngineOptions) {
    const { model } = o;
    this.model = model;
    this.canvas = o.canvas;
    this.ctx = o.canvas.getContext("2d");
    this.fonts = o.fonts;
    this.onFrame = o.onFrame;
    this.seed = { zoom: o.zoom, focus: o.focus };
    const n = model.strands.length;
    this.layout = makeLayout(1, 1, n);
    this.bounds = { tMin: model.tMin, tMax: model.tMax, width: 1 };
    this.highlight = o.highlight;
    this.dim = model.strands.map((_, i) => (o.highlight >= 0 && i !== o.highlight ? 1 : 0));
    this.lift = model.strands.map((_, i) => (i === o.highlight ? 1 : 0));
    this.swell = model.knots.map(() => 0);
    this.revealStart = o.reduced ? -1e9 : performance.now();
    this.reveal = o.reduced ? model.tNow : model.tMin;
    this.popAt = model.knots.map(() => (o.reduced ? -1e9 : NaN));
    this.pop = model.knots.map(() => (o.reduced ? 1 : 0));
    this.titleW = model.knots.map((k) => k.moment.title.length * 8.5);
    this.measureTitles();
  }

  /* ---------------- lifecycle ---------------- */

  /** Size the canvas to the box (CSS px). The first call places the camera. */
  resize(width: number, height: number, dpr: number) {
    const W = Math.max(1, width);
    const H = Math.max(1, height);
    this.dpr = Math.min(2, dpr || 1);
    this.canvas.width = Math.round(W * this.dpr);
    this.canvas.height = Math.round(H * this.dpr);
    this.canvas.style.width = `${W}px`;
    this.canvas.style.height = `${H}px`;
    this.layout = makeLayout(W, H, this.model.strands.length);
    const prevW = this.bounds.width;
    if (titleFontPx(W) !== titleFontPx(prevW)) this.measureTitles();
    this.bounds = { tMin: this.model.tMin, tMax: this.model.tMax, width: W };
    if (!this.ready) {
      // Open on the followed person's life, or on the whole family's.
      const id = this.highlight >= 0 ? this.model.strands[this.highlight].person.id : null;
      const mine = this.model.knots.filter((k) => !id || k.people.some((p) => p.id === id));
      this.cam = initialCamera(this.seed.zoom, this.seed.focus, this.bounds, mine.map((k) => k.t));
      this.ready = true;
    } else {
      // Keep the same span of years on screen when the window resizes.
      this.cam = clampCam({ cx: this.cam.cx, s: this.cam.s * (W / prevW) }, this.bounds);
    }
    this.kick();
  }

  /** Re-measure title widths in the handwriting font (call once it loads). */
  measureTitles() {
    const ctx = this.ctx;
    if (!ctx) return;
    ctx.font = `500 ${titleFontPx(this.layout.width)}px ${this.fonts.hand}`;
    this.titleW = this.model.knots.map((k) => ctx.measureText(k.moment.title).width + 6);
  }

  /** Make sure a frame is coming. */
  kick = () => {
    if (!this.raf) {
      this.last = performance.now();
      this.raf = requestAnimationFrame(this.frame);
    }
  };

  destroy() {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  /* ---------------- camera ---------------- */

  get canZoomIn(): boolean {
    return (this.target ? this.target.s : this.cam.s) < maxScale(this.bounds) * 0.999;
  }

  get canZoomOut(): boolean {
    return (this.target ? this.target.s : this.cam.s) > minScale(this.bounds) * 1.001;
  }

  /** Eased zoom by `factor`, keeping the year under screen x `ax` still. */
  zoomBy(factor: number, ax: number) {
    this.panTarget = null;
    this.velocity = 0;
    const base = this.target ? this.target.s : this.cam.s;
    const at = this.target && Math.abs(this.target.ax - ax) < 2 ? this.target.at : toT(ax, this.cam, this.bounds.width);
    const s = Math.min(maxScale(this.bounds), Math.max(minScale(this.bounds), base * factor));
    this.target = { s, ax, at };
    this.kick();
  }

  /** The zoom buttons: double or halve, about the centre of the view. */
  zoomStep(dir: 1 | -1) {
    this.panTarget = null;
    const base = this.target ? this.target.s : this.cam.s;
    const s = Math.min(maxScale(this.bounds), Math.max(minScale(this.bounds), base * (dir > 0 ? 2 : 0.5)));
    const at = this.target ? this.target.at : this.cam.cx;
    this.target = { s, ax: this.bounds.width / 2, at };
    this.kick();
  }

  /** Back out to the whole family. */
  zoomOutAll() {
    this.target = { s: minScale(this.bounds), ax: this.bounds.width / 2, at: (this.model.tMin + this.model.tMax) / 2 };
    this.kick();
  }

  /** Eased pan by a fraction of the visible span (+ = later). */
  panBySpan(f: number) {
    const span = this.bounds.width / this.cam.s;
    this.panTo((this.panTarget ?? this.cam.cx) + f * span);
  }

  /** Ease the centre toward year `t`, held to where the camera can actually go. */
  private panTo(t: number) {
    this.panTarget = clampCam({ cx: t, s: this.cam.s }, this.bounds).cx;
    this.kick();
  }

  /* ---------------- state ---------------- */

  setHighlight(i: number) {
    this.highlight = i;
    this.kick();
  }

  /** Keyboard focus on a moment: show its title and bring it into view. */
  focusKnot(i: number) {
    this.hoverKnot = i;
    const h = this.geom?.knots[i];
    const W = this.bounds.width;
    if (h && (h.x < W * 0.15 || h.x > W * 0.85)) this.panTo(this.model.knots[i].t);
    this.kick();
  }

  /* ---------------- hit testing ---------------- */

  hitTest(x: number, y: number): Hit {
    const g = this.geom;
    if (!g) return null;
    for (const b of this.titles) {
      if (x >= b.x0 - 4 && x <= b.x1 + 4 && y >= b.y0 - 4 && y <= b.y1 + 4) return { kind: "knot", i: b.knot };
    }
    let best = -1;
    let bestD = Infinity;
    for (const k of g.knots) {
      if (!k || !k.visible) continue;
      const d = Math.hypot(k.x - x, k.y - y) - k.r;
      if (d < bestD) {
        bestD = d;
        best = k.index;
      }
    }
    if (best >= 0 && bestD < 9) return { kind: "knot", i: best };
    let sBest = -1;
    let sD = Infinity;
    for (let i = 0; i < g.strands.length; i++) {
      const pts = g.strands[i];
      const n = g.strandLens[i];
      // Prefer lit threads when several pass close by.
      const bias = this.dim[i] * 4;
      for (let k = 0; k < n; k++) {
        const dx = pts[k * 2] - x;
        if (dx < -14 || dx > 14) continue;
        const d = Math.hypot(dx, pts[k * 2 + 1] - y) + bias;
        if (d < sD) {
          sD = d;
          sBest = i;
        }
      }
    }
    if (sBest >= 0 && sD < 12) return { kind: "strand", i: sBest };
    return null;
  }

  /* ---------------- frame ---------------- */

  private frame = (now: number) => {
    this.raf = 0;
    // rAF's timestamp can predate the kick that scheduled it: never step backwards.
    const dt = Math.max(0, Math.min(0.05, (now - this.last) / 1000));
    this.last = now;
    let moving = this.stepCamera(dt);
    if (this.stepFades(dt)) moving = true;
    if (this.stepIntro(now)) moving = true;

    const ctx = this.ctx;
    if (ctx) {
      const t0 = performance.now();
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this.geom = drawFrame(ctx, this.model, {
        cam: this.cam,
        layout: this.layout,
        dim: this.dim,
        lift: this.lift,
        hoverKnot: this.hoverKnot,
        swell: this.swell,
        reveal: this.reveal,
        pop: this.pop,
        fonts: this.fonts,
      });
      this.drawMs = performance.now() - t0;
      const { boxes, shown } = placeTitles(this.model, {
        geom: this.geom,
        dim: this.dim,
        highlight: this.highlight,
        hoverKnot: this.hoverKnot,
        s: this.cam.s,
        width: this.layout.width,
        widths: this.titleW,
        spots: this.titleSpot,
      });
      this.titles = boxes;
      this.titlePlacements = shown;
      this.onFrame(this);
    }
    if (moving) this.raf = requestAnimationFrame(this.frame);
  };

  /** Eased zoom about an anchor, eased pan, fling. True while moving. */
  private stepCamera(dt: number): boolean {
    let moving = false;
    const b = this.bounds;
    if (this.target) {
      const t = this.target;
      const k = 1 - Math.exp(-dt * 13);
      const s = this.cam.s + (t.s - this.cam.s) * k;
      const done = Math.abs(s - t.s) / t.s < 0.002;
      const sNew = done ? t.s : s;
      this.cam = clampCam({ s: sNew, cx: t.at - (t.ax - b.width / 2) / sNew }, b);
      if (done) this.target = null;
      else moving = true;
    }
    if (this.panTarget !== null) {
      const k = 1 - Math.exp(-dt * 10);
      const cx = this.cam.cx + (this.panTarget - this.cam.cx) * k;
      const before = this.cam.cx;
      const want = Math.abs(cx - this.panTarget) * this.cam.s < 0.3 ? this.panTarget : cx;
      this.cam = clampCam({ s: this.cam.s, cx: want }, b);
      // Arrived, or held at the edge (a zoom can move the bounds under the target).
      const held = want !== before && this.cam.cx === before;
      const done = held || Math.abs(this.cam.cx - this.panTarget) * this.cam.s < 0.3;
      if (done) this.panTarget = null;
      else moving = true;
    }
    if (!this.drag && Math.abs(this.velocity) > 0.02) {
      const before = this.cam.cx;
      this.cam = clampCam({ s: this.cam.s, cx: this.cam.cx - (this.velocity * dt) / this.cam.s }, b);
      this.velocity *= Math.exp(-dt * 4.5);
      if (this.cam.cx === before) this.velocity = 0;
      moving = true;
    } else if (!this.drag) this.velocity = 0;
    return moving;
  }

  /** Highlight fade and hover swell. True while easing. */
  private stepFades(dt: number): boolean {
    let moving = false;
    const ease = 1 - Math.exp(-dt * 9);
    for (let i = 0; i < this.dim.length; i++) {
      const dT = this.highlight < 0 || this.highlight === i ? 0 : 1;
      const lT = this.highlight === i ? 1 : this.hoverStrand === i ? 0.5 : 0;
      this.dim[i] += (dT - this.dim[i]) * ease;
      this.lift[i] += (lT - this.lift[i]) * ease;
      if (Math.abs(dT - this.dim[i]) > 0.004 || Math.abs(lT - this.lift[i]) > 0.004) moving = true;
      else {
        this.dim[i] = dT;
        this.lift[i] = lT;
      }
    }
    const k = 1 - Math.exp(-dt * 14);
    for (let i = 0; i < this.swell.length; i++) {
      const sT = i === this.hoverKnot ? 1 : 0;
      this.swell[i] += (sT - this.swell[i]) * k;
      if (Math.abs(sT - this.swell[i]) > 0.004) moving = true;
      else this.swell[i] = sT;
    }
    return moving;
  }

  /** The threads are sewn in left to right; each knot springs in as the needle reaches it. */
  private stepIntro(now: number): boolean {
    let moving = false;
    const m = this.model;
    const p = Math.min(1, (now - this.revealStart) / INTRO_MS);
    const eased = p < 1 ? 1 - Math.pow(1 - p, 2.2) : 1;
    this.reveal = m.tMin + (m.tNow + 2 - m.tMin) * eased;
    if (p < 1) moving = true;
    for (let i = 0; i < this.pop.length; i++) {
      if (this.pop[i] >= 1) continue;
      if (Number.isNaN(this.popAt[i])) {
        if (this.reveal < m.knots[i].t) continue;
        this.popAt[i] = now;
      }
      this.pop[i] = Math.min(1, (now - this.popAt[i]) / KNOT_POP_MS);
      moving = true;
    }
    return moving;
  }

  /* ---------------- input ---------------- */

  /** Wire wheel and pointer input on the canvas. Returns an unbind function. */
  bindInput(h: InputHandlers): () => void {
    const canvas = this.canvas;
    const local = (ev: { clientX: number; clientY: number }) => {
      const r = canvas.getBoundingClientRect();
      return { x: ev.clientX - r.left, y: ev.clientY - r.top };
    };

    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      h.interact();
      const { x } = local(ev);
      const unit = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? 400 : 1;
      const dx = ev.deltaX * unit;
      const dy = ev.deltaY * unit;
      if (ev.ctrlKey) {
        // Trackpad pinch.
        this.zoomBy(Math.exp(-dy * 0.012), x);
      } else if (Math.abs(dx) > Math.abs(dy) || ev.shiftKey) {
        this.target = null;
        this.panTarget = null;
        const d = Math.abs(dx) > Math.abs(dy) ? dx : dy;
        this.cam = clampCam({ s: this.cam.s, cx: this.cam.cx + d / this.cam.s }, this.bounds);
        this.kick();
      } else {
        this.zoomBy(Math.exp(-Math.max(-120, Math.min(120, dy)) * 0.0028), x);
      }
    };

    const onDown = (ev: PointerEvent) => {
      canvas.setPointerCapture(ev.pointerId);
      h.interact();
      const p = local(ev);
      this.pointers.set(ev.pointerId, p);
      this.target = null;
      this.panTarget = null;
      this.velocity = 0;
      if (this.pointers.size === 1) {
        this.drag = { x: p.x, moved: false, lastX: p.x, lastT: performance.now(), pinch: 0 };
      } else if (this.pointers.size === 2 && this.drag) {
        const [a, b] = [...this.pointers.values()];
        this.drag.pinch = pinchGap(a, b);
        this.drag.moved = true;
        this.drag.lastX = (a.x + b.x) / 2;
      }
    };

    const onMove = (ev: PointerEvent) => {
      const p = local(ev);
      const drag = this.drag;
      if (!drag || !this.pointers.has(ev.pointerId)) {
        // Hover.
        const hit = this.hitTest(p.x, p.y);
        const hk = hit?.kind === "knot" ? hit.i : -1;
        const hs = hit?.kind === "strand" ? hit.i : -1;
        if (hk !== this.hoverKnot || hs !== this.hoverStrand) {
          if (hk >= 0 && hk !== this.hoverKnot) h.hoverKnot(hk);
          this.hoverKnot = hk;
          this.hoverStrand = hs;
          canvas.style.cursor = hit ? "pointer" : "grab";
          this.kick();
        }
        return;
      }
      this.pointers.set(ev.pointerId, p);
      const now = performance.now();
      if (this.pointers.size >= 2) {
        const [a, b] = [...this.pointers.values()];
        const mid = (a.x + b.x) / 2;
        const dist = pinchGap(a, b);
        // Pan with the midpoint, zoom about it; one event never jumps more than 2x.
        const factor = Math.min(2, Math.max(0.5, dist / drag.pinch));
        this.cam = clampCam({ s: this.cam.s, cx: this.cam.cx - (mid - drag.lastX) / this.cam.s }, this.bounds);
        this.cam = zoomAt(this.cam, factor, mid, this.bounds);
        drag.pinch = dist;
        drag.lastX = mid;
        this.kick();
        return;
      }
      const dx = p.x - drag.lastX;
      if (!drag.moved && Math.abs(p.x - drag.x) < 5) return;
      if (!drag.moved) {
        drag.moved = true;
        canvas.style.cursor = "grabbing";
        this.hoverKnot = -1;
        this.hoverStrand = -1;
      }
      const dtMs = Math.max(1, now - drag.lastT);
      this.velocity = this.velocity * 0.6 + (dx / dtMs) * 1000 * 0.4;
      this.cam = clampCam({ s: this.cam.s, cx: this.cam.cx - dx / this.cam.s }, this.bounds);
      drag.lastX = p.x;
      drag.lastT = now;
      this.kick();
    };

    const onUp = (ev: PointerEvent) => {
      this.pointers.delete(ev.pointerId);
      const d = this.drag;
      if (this.pointers.size === 1 && d) {
        const [rest] = [...this.pointers.values()];
        d.lastX = rest.x;
        d.lastT = performance.now();
        return;
      }
      if (this.pointers.size > 0) return;
      this.drag = null;
      canvas.style.cursor = "grab";
      if (d && !d.moved) {
        this.velocity = 0;
        const p = local(ev);
        // A second tap close by and soon after: zoom in about it.
        const now = performance.now();
        const lt = this.lastTap;
        if (lt && now - lt.t < 320 && Math.hypot(p.x - lt.x, p.y - lt.y) < 24) {
          this.lastTap = null;
          this.zoomBy(2.2, p.x);
          return;
        }
        this.lastTap = { t: now, x: p.x, y: p.y };
        const hit = this.hitTest(p.x, p.y);
        if (hit?.kind === "knot") h.open(hit.i);
        else if (hit?.kind === "strand") h.follow(this.highlight === hit.i ? -1 : hit.i);
        else if (this.highlight >= 0) h.follow(-1);
      } else if (performance.now() - (d?.lastT ?? 0) > 80) {
        this.velocity = 0; // held still before letting go: no fling
      }
      this.kick();
    };

    const onLeave = () => {
      if (this.drag) return;
      this.hoverKnot = -1;
      this.hoverStrand = -1;
      this.kick();
    };

    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointercancel", onUp);
    canvas.addEventListener("pointerleave", onLeave);
    return () => {
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointercancel", onUp);
      canvas.removeEventListener("pointerleave", onLeave);
    };
  }
}
