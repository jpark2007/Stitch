"use client";

/**
 * The Threads screen: the whole family as stitched thread along time.
 *
 * A single 2D canvas draws threads, knots and the year axis (renderer.ts);
 * a thin DOM overlay carries the moment links and titles so they are
 * focusable, readable by screen readers and set in the real fonts.
 *
 * Interaction: drag to pan, wheel / pinch / buttons to zoom, click a thread
 * to follow that person, click a knot (or its title) to open the moment.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  clampCam,
  initialCamera,
  maxScale,
  minScale,
  parseZoom,
  toT,
  zoomAt,
  type Bounds,
  type Camera,
} from "./camera";
import { buildModel, type ThreadMoment, type ThreadPerson } from "./model";
import { drawFrame, makeLayout, type FrameGeometry, type Layout } from "./renderer";
import css from "./threads.module.css";

export interface ThreadsViewProps {
  familyName: string;
  people: ThreadPerson[];
  moments: ThreadMoment[];
  /** Raw query params: ?zoom=decade|year|<multiple>, ?focus=<year>, ?highlight=<personId>. */
  zoom?: string;
  focus?: string;
  highlight?: string;
}

const NOW = new Date().getFullYear();
const TITLE_FONT_PX = 20;

interface TitleBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  knot: number;
}

interface Engine {
  ctx: CanvasRenderingContext2D | null;
  dpr: number;
  layout: Layout;
  bounds: Bounds;
  cam: Camera;
  /** Zoom animation target and the screen point it is anchored on. */
  target: { s: number; ax: number; at: number } | null;
  panTarget: number | null;
  velocity: number;
  dim: number[];
  lift: number[];
  swell: number[];
  hoverKnot: number;
  hoverStrand: number;
  highlight: number;
  revealStart: number;
  reveal: number;
  geom: FrameGeometry | null;
  titles: TitleBox[];
  titleW: number[];
  fonts: { hand: string; sans: string };
  raf: number;
  last: number;
  ready: boolean;
  pointers: Map<number, { x: number; y: number }>;
  drag: { x: number; moved: boolean; lastX: number; lastT: number; pinch: number; startCx: number } | null;
  drawMs: number;
}

export default function ThreadsView(props: ThreadsViewProps) {
  const { people, moments, familyName } = props;
  const router = useRouter();
  const model = useMemo(() => buildModel(people, moments, NOW), [people, moments]);
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const linkRefs = useRef<(HTMLAnchorElement | null)[]>([]);
  const titleRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const ringRefs = useRef<(HTMLSpanElement | null)[]>([]);

  const initialHighlight = model.strands.findIndex((s) => s.person.id === props.highlight);
  const [highlight, setHighlight] = useState(initialHighlight);

  const eng = useRef<Engine | null>(null);
  const kick = useRef<() => void>(() => {});

  /* ---------------- engine ---------------- */
  useEffect(() => {
    const canvas = canvasRef.current!;
    const wrap = wrapRef.current!;
    const n = model.strands.length;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const css = getComputedStyle(document.documentElement);
    const hand = css.getPropertyValue("--font-caveat").trim() || "cursive";
    const sans = css.getPropertyValue("--font-nunito").trim() || "sans-serif";

    const e: Engine = {
      ctx: canvas.getContext("2d"),
      dpr: 1,
      layout: makeLayout(1, 1, n),
      bounds: { tMin: model.tMin, tMax: model.tMax, width: 1 },
      cam: { cx: 0, s: 1 },
      target: null,
      panTarget: null,
      velocity: 0,
      dim: model.strands.map((_, i) => (initialHighlight >= 0 && i !== initialHighlight ? 1 : 0)),
      lift: model.strands.map((_, i) => (i === initialHighlight ? 1 : 0)),
      swell: model.knots.map(() => 0),
      hoverKnot: -1,
      hoverStrand: -1,
      highlight: initialHighlight,
      revealStart: performance.now(),
      reveal: reduced ? model.tNow : model.tMin,
      geom: null,
      titles: [],
      titleW: model.knots.map((k) => k.moment.title.length * 8.5),
      fonts: { hand, sans },
      raf: 0,
      last: performance.now(),
      ready: false,
      pointers: new Map(),
      drag: null,
      drawMs: 0,
    };
    if (reduced) e.revealStart = -1e9;
    eng.current = e;

    const measure = () => {
      if (!e.ctx) return;
      e.ctx.font = `500 ${TITLE_FONT_PX}px ${hand}`;
      e.titleW = model.knots.map((k) => e.ctx!.measureText(k.moment.title).width + 6);
    };

    const resize = () => {
      const rect = wrap.getBoundingClientRect();
      const W = Math.max(1, rect.width);
      const H = Math.max(1, rect.height);
      e.dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.round(W * e.dpr);
      canvas.height = Math.round(H * e.dpr);
      canvas.style.width = `${W}px`;
      canvas.style.height = `${H}px`;
      e.layout = makeLayout(W, H, n);
      const prevW = e.bounds.width;
      e.bounds = { tMin: model.tMin, tMax: model.tMax, width: W };
      if (!e.ready) {
        const focus = props.focus ? Number(props.focus) : NaN;
        const busiest = [...model.knots].sort((a, b) => b.people.length - a.people.length)[0];
        e.cam = initialCamera(
          parseZoom(props.zoom),
          Number.isFinite(focus) ? focus + 0.5 : null,
          e.bounds,
          busiest ? busiest.t : (model.tMin + model.tMax) / 2,
        );
        e.ready = true;
      } else {
        // Keep the same span of years on screen when the window resizes.
        e.cam = clampCam({ cx: e.cam.cx, s: e.cam.s * (W / prevW) }, e.bounds);
      }
      start();
    };

    const frame = (now: number) => {
      e.raf = 0;
      const dt = Math.min(0.05, (now - e.last) / 1000);
      e.last = now;
      let moving = false;

      // Camera: eased zoom about an anchor, eased pan, inertia.
      if (e.target) {
        const k = 1 - Math.exp(-dt * 13);
        const s = e.cam.s + (e.target.s - e.cam.s) * k;
        const done = Math.abs(s - e.target.s) / e.target.s < 0.002;
        const sNew = done ? e.target.s : s;
        e.cam = clampCam({ s: sNew, cx: e.target.at - (e.target.ax - e.bounds.width / 2) / sNew }, e.bounds);
        if (done) e.target = null;
        else moving = true;
      }
      if (e.panTarget !== null) {
        const k = 1 - Math.exp(-dt * 10);
        const cx = e.cam.cx + (e.panTarget - e.cam.cx) * k;
        const done = Math.abs(cx - e.panTarget) * e.cam.s < 0.3;
        e.cam = clampCam({ s: e.cam.s, cx: done ? e.panTarget : cx }, e.bounds);
        if (done) e.panTarget = null;
        else moving = true;
      }
      if (!e.drag && Math.abs(e.velocity) > 0.02) {
        const before = e.cam.cx;
        e.cam = clampCam({ s: e.cam.s, cx: e.cam.cx - (e.velocity * dt) / e.cam.s }, e.bounds);
        e.velocity *= Math.exp(-dt * 4.5);
        if (e.cam.cx === before) e.velocity = 0;
        moving = true;
      } else if (!e.drag) e.velocity = 0;

      // Highlight and hover easing.
      const ease = 1 - Math.exp(-dt * 9);
      for (let i = 0; i < n; i++) {
        const lit = e.highlight < 0 || e.highlight === i;
        const dT = lit ? 0 : 1;
        const lT = e.highlight === i ? 1 : e.hoverStrand === i ? 0.5 : 0;
        e.dim[i] += (dT - e.dim[i]) * ease;
        e.lift[i] += (lT - e.lift[i]) * ease;
        if (Math.abs(dT - e.dim[i]) > 0.004 || Math.abs(lT - e.lift[i]) > 0.004) moving = true;
        else {
          e.dim[i] = dT;
          e.lift[i] = lT;
        }
      }
      for (let i = 0; i < e.swell.length; i++) {
        const sT = i === e.hoverKnot ? 1 : 0;
        e.swell[i] += (sT - e.swell[i]) * (1 - Math.exp(-dt * 14));
        if (Math.abs(sT - e.swell[i]) > 0.004) moving = true;
        else e.swell[i] = sT;
      }

      // Intro: the threads are sewn left to right.
      const introMs = 1900;
      const p = Math.min(1, (now - e.revealStart) / introMs);
      const eased = p < 1 ? 1 - Math.pow(1 - p, 2.2) : 1;
      e.reveal = model.tMin + (model.tNow + 2 - model.tMin) * eased;
      if (p < 1) moving = true;

      const ctx = e.ctx;
      if (ctx) {
        const t0 = performance.now();
        ctx.setTransform(e.dpr, 0, 0, e.dpr, 0, 0);
        e.geom = drawFrame(ctx, model, {
          cam: e.cam,
          layout: e.layout,
          dim: e.dim,
          lift: e.lift,
          hoverKnot: e.hoverKnot,
          swell: e.swell,
          reveal: e.reveal,
          fonts: e.fonts,
        });
        e.drawMs = performance.now() - t0;
        placeOverlay(e);
      }
      if (moving) e.raf = requestAnimationFrame(frame);
    };

    const start = () => {
      if (!e.raf) {
        e.last = performance.now();
        e.raf = requestAnimationFrame(frame);
      }
    };
    kick.current = start;

    const placeOverlay = (e: Engine) => {
      const g = e.geom;
      if (!g) return;
      const W = e.layout.width;
      const s = e.cam.s;
      // Titles: only at close zoom (or for the followed person / hovered knot),
      // placed greedily above or below their knot without overlapping.
      const showAll = s >= 72;
      const showLit = s >= 26;
      const candidates: number[] = [];
      for (let i = 0; i < model.knots.length; i++) {
        const h = g.knots[i];
        if (!h || !h.visible) continue;
        const lit = model.knots[i].people.some((p) => {
          const idx = model.strands.findIndex((q) => q.person.id === p.id);
          return e.dim[idx] < 0.5;
        });
        const followed = e.highlight >= 0 && lit;
        if (i === e.hoverKnot || (showAll && (e.highlight < 0 || lit)) || (showLit && followed)) candidates.push(i);
      }
      candidates.sort((a, b) => {
        if (a === e.hoverKnot) return -1;
        if (b === e.hoverKnot) return 1;
        return model.knots[b].people.length - model.knots[a].people.length || a - b;
      });
      const placed: TitleBox[] = [];
      const shown = new Map<number, { dx: number; dy: number }>();
      const knotsBoxes = g.knots.filter((k) => k && k.visible);
      const hTxt = TITLE_FONT_PX * 1.15;
      for (const i of candidates) {
        const h = g.knots[i];
        const w = e.titleW[i];
        const tries = [
          { dy: -(h.r + 8 + hTxt), dx: -w / 2 },
          { dy: h.r + 8, dx: -w / 2 },
          { dy: -(h.r + 8 + hTxt), dx: -w * 0.15 },
          { dy: h.r + 8, dx: -w * 0.85 },
        ];
        for (const tr of tries) {
          const box: TitleBox = { x0: h.x + tr.dx, y0: h.y + tr.dy, x1: h.x + tr.dx + w, y1: h.y + tr.dy + hTxt, knot: i };
          if (box.x0 < 8 || box.x1 > W - 8) continue;
          const hitsTitle = placed.some((b) => b.x0 < box.x1 + 10 && box.x0 < b.x1 + 10 && b.y0 < box.y1 + 2 && box.y0 < b.y1 + 2);
          const hitsKnot = knotsBoxes.some(
            (k) => k.index !== i && k.x + k.r > box.x0 && k.x - k.r < box.x1 && k.y + k.r > box.y0 && k.y - k.r < box.y1,
          );
          if (hitsTitle || hitsKnot) continue;
          placed.push(box);
          shown.set(i, tr);
          break;
        }
      }
      e.titles = placed;

      for (let i = 0; i < model.knots.length; i++) {
        const a = linkRefs.current[i];
        const t = titleRefs.current[i];
        const ring = ringRefs.current[i];
        const h = g.knots[i];
        if (!a || !t || !ring || !h) continue;
        a.style.transform = `translate3d(${h.x.toFixed(1)}px, ${h.y.toFixed(1)}px, 0)`;
        a.style.visibility = h.visible ? "visible" : "hidden";
        const d = Math.round(h.r * 2 + 10);
        if (ring.dataset.d !== String(d)) {
          ring.dataset.d = String(d);
          ring.style.width = ring.style.height = `${d}px`;
          ring.style.margin = `${-d / 2}px 0 0 ${-d / 2}px`;
        }
        const pl = shown.get(i);
        if (pl) {
          const key = `${pl.dx.toFixed(0)},${pl.dy.toFixed(0)}`;
          if (t.dataset.at !== key) {
            t.dataset.at = key;
            t.style.transform = `translate(${pl.dx.toFixed(1)}px, ${pl.dy.toFixed(1)}px)`;
          }
          t.style.opacity = "1";
        } else {
          t.style.opacity = "0";
        }
      }
    };

    measure();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    resize();
    document.fonts?.load(`500 ${TITLE_FONT_PX}px ${hand}`).then(() => {
      measure();
      start();
    });
    document.fonts?.ready.then(() => {
      measure();
      start();
    });

    if (process.env.NODE_ENV !== "production") {
      (window as unknown as { __threads?: Engine }).__threads = e;
    }

    return () => {
      ro.disconnect();
      if (e.raf) cancelAnimationFrame(e.raf);
      eng.current = null;
    };
    // The engine is built once per model; query params only seed it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model]);

  /* ---------------- highlight ---------------- */
  const setLit = useCallback(
    (i: number) => {
      const e = eng.current;
      if (!e) return;
      e.highlight = i;
      setHighlight(i);
      kick.current();
      const url = new URL(window.location.href);
      if (i >= 0) url.searchParams.set("highlight", model.strands[i].person.id);
      else url.searchParams.delete("highlight");
      window.history.replaceState(null, "", url);
    },
    [model],
  );

  /* ---------------- hit testing ---------------- */
  const hitTest = useCallback(
    (x: number, y: number): { kind: "knot"; i: number } | { kind: "strand"; i: number } | null => {
      const e = eng.current;
      const g = e?.geom;
      if (!e || !g) return null;
      for (const b of e.titles) {
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
        const bias = e.dim[i] * 4;
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
    },
    [],
  );

  /* ---------------- input ---------------- */
  useEffect(() => {
    const canvas = canvasRef.current!;
    const local = (ev: { clientX: number; clientY: number }) => {
      const r = canvas.getBoundingClientRect();
      return { x: ev.clientX - r.left, y: ev.clientY - r.top };
    };

    const zoomBy = (factor: number, ax: number) => {
      const e = eng.current;
      if (!e) return;
      e.panTarget = null;
      e.velocity = 0;
      const base = e.target ? e.target.s : e.cam.s;
      const at = e.target && Math.abs(e.target.ax - ax) < 2 ? e.target.at : toT(ax, e.cam, e.bounds.width);
      const s = Math.min(maxScale(e.bounds), Math.max(minScale(e.bounds), base * factor));
      e.target = { s, ax, at };
      kick.current();
    };

    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const e = eng.current;
      if (!e) return;
      const { x } = local(ev);
      const unit = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? 400 : 1;
      const dx = ev.deltaX * unit;
      const dy = ev.deltaY * unit;
      if (ev.ctrlKey) {
        zoomBy(Math.exp(-dy * 0.012), x);
      } else if (Math.abs(dx) > Math.abs(dy) || ev.shiftKey) {
        e.target = null;
        e.panTarget = null;
        const d = Math.abs(dx) > Math.abs(dy) ? dx : dy;
        e.cam = clampCam({ s: e.cam.s, cx: e.cam.cx + d / e.cam.s }, e.bounds);
        kick.current();
      } else {
        zoomBy(Math.exp(-Math.max(-120, Math.min(120, dy)) * 0.0028), x);
      }
    };

    const onDown = (ev: PointerEvent) => {
      const e = eng.current;
      if (!e) return;
      canvas.setPointerCapture(ev.pointerId);
      const p = local(ev);
      e.pointers.set(ev.pointerId, p);
      e.target = null;
      e.panTarget = null;
      e.velocity = 0;
      if (e.pointers.size === 1) {
        e.drag = { x: p.x, moved: false, lastX: p.x, lastT: performance.now(), pinch: 0, startCx: e.cam.cx };
      } else if (e.pointers.size === 2 && e.drag) {
        const [a, b] = [...e.pointers.values()];
        e.drag.pinch = Math.abs(a.x - b.x) || 1;
        e.drag.moved = true;
        e.drag.lastX = (a.x + b.x) / 2;
      }
    };

    const onMove = (ev: PointerEvent) => {
      const e = eng.current;
      if (!e) return;
      const p = local(ev);
      if (!e.drag || !e.pointers.has(ev.pointerId)) {
        // Hover.
        const hit = hitTest(p.x, p.y);
        const hk = hit?.kind === "knot" ? hit.i : -1;
        const hs = hit?.kind === "strand" ? hit.i : -1;
        if (hk !== e.hoverKnot || hs !== e.hoverStrand) {
          e.hoverKnot = hk;
          e.hoverStrand = hs;
          canvas.style.cursor = hit ? "pointer" : "grab";
          kick.current();
        }
        return;
      }
      e.pointers.set(ev.pointerId, p);
      const now = performance.now();
      if (e.pointers.size >= 2) {
        const [a, b] = [...e.pointers.values()];
        const mid = (a.x + b.x) / 2;
        const dist = Math.abs(a.x - b.x) || 1;
        const factor = dist / e.drag.pinch;
        // Pan with the midpoint, zoom about it.
        e.cam = clampCam({ s: e.cam.s, cx: e.cam.cx - (mid - e.drag.lastX) / e.cam.s }, e.bounds);
        e.cam = zoomAt(e.cam, factor, mid, e.bounds);
        e.drag.pinch = dist;
        e.drag.lastX = mid;
        kick.current();
        return;
      }
      const dx = p.x - e.drag.lastX;
      if (!e.drag.moved && Math.abs(p.x - e.drag.x) < 5) return;
      if (!e.drag.moved) {
        e.drag.moved = true;
        canvas.style.cursor = "grabbing";
        e.hoverKnot = -1;
        e.hoverStrand = -1;
      }
      const dtMs = Math.max(1, now - e.drag.lastT);
      // px/s, smoothed.
      e.velocity = e.velocity * 0.6 + ((dx / dtMs) * 1000) * 0.4;
      e.cam = clampCam({ s: e.cam.s, cx: e.cam.cx - dx / e.cam.s }, e.bounds);
      e.drag.lastX = p.x;
      e.drag.lastT = now;
      kick.current();
    };

    const onUp = (ev: PointerEvent) => {
      const e = eng.current;
      if (!e) return;
      e.pointers.delete(ev.pointerId);
      const d = e.drag;
      if (e.pointers.size === 1 && d) {
        const [rest] = [...e.pointers.values()];
        d.lastX = rest.x;
        d.lastT = performance.now();
        return;
      }
      if (e.pointers.size > 0) return;
      e.drag = null;
      canvas.style.cursor = "grab";
      if (d && !d.moved) {
        e.velocity = 0;
        const p = local(ev);
        const hit = hitTest(p.x, p.y);
        if (hit?.kind === "knot") {
          router.push(`/memories/${model.knots[hit.i].moment.id}`);
        } else if (hit?.kind === "strand") {
          setLit(e.highlight === hit.i ? -1 : hit.i);
        } else if (e.highlight >= 0) {
          setLit(-1);
        }
      } else if (performance.now() - (d?.lastT ?? 0) > 80) {
        e.velocity = 0; // held still before letting go: no fling
      }
      kick.current();
    };

    const onLeave = () => {
      const e = eng.current;
      if (!e || e.drag) return;
      e.hoverKnot = -1;
      e.hoverStrand = -1;
      kick.current();
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
  }, [hitTest, model, router, setLit]);

  /* ---------------- buttons & keys ---------------- */
  const zoomButton = useCallback((dir: 1 | -1) => {
    const e = eng.current;
    if (!e) return;
    e.panTarget = null;
    const W = e.bounds.width;
    const base = e.target ? e.target.s : e.cam.s;
    const s = Math.min(maxScale(e.bounds), Math.max(minScale(e.bounds), base * (dir > 0 ? 2 : 0.5)));
    const at = e.target ? e.target.at : e.cam.cx;
    e.target = { s, ax: W / 2, at };
    kick.current();
  }, []);

  const onKey = useCallback(
    (ev: React.KeyboardEvent) => {
      const e = eng.current;
      if (!e) return;
      const span = e.bounds.width / e.cam.s;
      if (ev.key === "+" || ev.key === "=") zoomButton(1);
      else if (ev.key === "-" || ev.key === "_") zoomButton(-1);
      else if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
        const dir = ev.key === "ArrowLeft" ? -1 : 1;
        e.panTarget = (e.panTarget ?? e.cam.cx) + dir * span * 0.25;
        kick.current();
      } else if (ev.key === "0") {
        e.target = { s: minScale(e.bounds), ax: e.bounds.width / 2, at: (model.tMin + model.tMax) / 2 };
        kick.current();
      } else if (ev.key === "Escape") setLit(-1);
      else return;
      ev.preventDefault();
    },
    [model, setLit, zoomButton],
  );

  const followed = highlight >= 0 ? model.strands[highlight] : null;
  const followedCount = followed ? followed.momentIds.length : 0;

  return (
    <main className="fixed inset-0 overflow-hidden select-none">
      <div
        ref={wrapRef}
        className="absolute inset-0 outline-none"
        tabIndex={0}
        role="application"
        aria-roledescription="timeline"
        aria-label={`${familyName} threads. Drag or use the arrow keys to move through time; plus and minus zoom; Escape shows everyone.`}
        onKeyDown={onKey}
      >
        <canvas ref={canvasRef} className="absolute inset-0 touch-none" style={{ cursor: "grab" }} aria-hidden />

        {/* Moment links + titles, positioned each frame over their knots. */}
        <div className="pointer-events-none absolute inset-0" aria-label="Moments">
          {model.knots.map((k, i) => (
            <Link
              key={k.moment.id}
              href={`/memories/${k.moment.id}`}
              prefetch={false}
              ref={(el) => {
                linkRefs.current[i] = el;
              }}
              className={`${css.moment} absolute left-0 top-0 no-underline`}
              style={{ visibility: "hidden" }}
              aria-label={`${k.moment.title}, ${k.moment.year}. ${k.people.map((p) => p.name).join(", ")}.`}
            >
              <span
                ref={(el) => {
                  ringRefs.current[i] = el;
                }}
                className={`${css.ring} absolute left-0 top-0 block rounded-full`}
              />
              <span
                ref={(el) => {
                  titleRefs.current[i] = el;
                }}
                className={`${css.title} absolute left-0 top-0 whitespace-nowrap`}
                style={{ opacity: 0, fontSize: TITLE_FONT_PX }}
                aria-hidden
              >
                {k.moment.title}
              </span>
            </Link>
          ))}
        </div>
      </div>

      {/* Quiet header: where you are, and whose thread you are following. */}
      <header className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between px-[var(--gutter)] pt-6 sm:pt-8">
        <div className="pointer-events-auto">
          <Link href="/" className="kicker no-underline hover:text-ink-soft">
            Family threads
          </Link>
          <h1 className="mt-1 text-[1.6rem] leading-tight sm:text-[2rem]" style={{ fontVariationSettings: '"SOFT" 100, "WONK" 1' }}>
            {familyName}
          </h1>
          <p className="caption mt-1 h-7 text-[1.25rem]" aria-live="polite">
            {followed ? (
              <button
                type="button"
                onClick={() => setLit(-1)}
                className="cursor-pointer text-left"
                style={{ color: followed.person.color }}
                aria-label={`Following ${followed.person.name}. Show everyone.`}
              >
                {followed.person.name}&rsquo;s thread &middot; {followedCount} {followedCount === 1 ? "moment" : "moments"}
                <span className="ml-2 text-ink-faint">&times;</span>
              </button>
            ) : null}
          </p>
        </div>
      </header>

      {/* Zoom: two small paper buttons. */}
      <div className="absolute bottom-5 right-[var(--gutter)] flex gap-2 sm:bottom-6">
        <button type="button" onClick={() => zoomButton(-1)} className={css.zoom} aria-label="Zoom out towards decades">
          <span aria-hidden>&minus;</span>
        </button>
        <button type="button" onClick={() => zoomButton(1)} className={css.zoom} aria-label="Zoom in towards single years">
          <span aria-hidden>+</span>
        </button>
      </div>

      {/* For keyboard and screen-reader users: follow a person's thread. */}
      <nav className="sr-only" aria-label="Follow a thread">
        <ul>
          {model.strands.map((s, i) => (
            <li key={s.person.id}>
              <button type="button" onClick={() => setLit(highlight === i ? -1 : i)} aria-pressed={highlight === i}>
                Follow {s.person.name}&rsquo;s thread ({s.momentIds.length} moments)
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </main>
  );
}
