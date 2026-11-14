"use client";

/**
 * The Threads screen: the whole family as stitched thread along time.
 *
 * A single 2D canvas draws threads, knots and the year axis (renderer.ts),
 * driven by the engine (engine.ts: camera, animation, input, hit-testing);
 * a thin DOM overlay carries the moment links and titles so they are
 * focusable, readable by screen readers and set in the real fonts.
 *
 * Interaction: drag to pan, wheel / pinch / buttons to zoom, click a thread
 * to follow that person, click a knot (or its title) to open the moment.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { parseZoom } from "./camera";
import { ThreadsEngine } from "./engine";
import { buildModel, type ThreadMoment, type ThreadPerson } from "./model";
import { paperAt, TITLE_FONT_PX, TITLE_FONT_PX_COMPACT } from "./titles";

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

export default function ThreadsView(props: ThreadsViewProps) {
  const { people, moments, familyName } = props;
  const router = useRouter();
  const model = useMemo(() => buildModel(people, moments, NOW), [people, moments]);
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const linkRefs = useRef<(HTMLAnchorElement | null)[]>([]);
  const titleRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const ringRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const zoomOutRef = useRef<HTMLButtonElement>(null);
  const zoomInRef = useRef<HTMLButtonElement>(null);

  const initialHighlight = model.strands.findIndex((s) => s.person.id === props.highlight);
  const [highlight, setHighlight] = useState(initialHighlight);
  // The gesture hint fades for good after the first touch, scroll or key.
  const [interacted, setInteracted] = useState(false);
  const interact = useCallback(() => setInteracted(true), []);

  // Query params only seed the view, so they are read once.
  const [seed] = useState(() => {
    const focus = props.focus ? Number(props.focus) : NaN;
    return { zoom: parseZoom(props.zoom), focus: Number.isFinite(focus) ? focus + 0.5 : null, highlight: initialHighlight };
  });
  const eng = useRef<ThreadsEngine | null>(null);

  /* ---------------- highlight ---------------- */
  const setLit = useCallback(
    (i: number) => {
      eng.current?.setHighlight(i);
      setHighlight(i);
      const url = new URL(window.location.href);
      if (i >= 0) url.searchParams.set("highlight", model.strands[i].person.id);
      else url.searchParams.delete("highlight");
      window.history.replaceState(null, "", url);
    },
    [model],
  );

  /* ---------------- overlay ---------------- */
  /** After each frame: move links and titles onto their knots, and rest the zoom buttons at the limits. */
  const placeOverlay = useEffectEvent((e: ThreadsEngine) => {
    const g = e.geom;
    if (!g) return;
    for (let i = 0; i < model.knots.length; i++) {
      const a = linkRefs.current[i];
      const t = titleRefs.current[i];
      const ring = ringRefs.current[i];
      const h = g.knots[i];
      if (!a || !t || !ring || !h) continue;
      a.style.transform = `translate3d(${h.x.toFixed(1)}px, ${h.y.toFixed(1)}px, 0)`;
      const hover = i === e.hoverKnot;
      if ((a.dataset.hover === "1") !== hover) a.dataset.hover = hover ? "1" : "0";
      // Off-screen links stay focusable (focus pans to them); only knots not
      // yet sewn in by the intro are hidden.
      a.style.visibility = e.reveal >= model.knots[i].t ? "visible" : "hidden";
      const d = Math.round(h.r * 2 + 10);
      if (ring.dataset.d !== String(d)) {
        ring.dataset.d = String(d);
        ring.style.width = ring.style.height = `${d}px`;
        ring.style.margin = `${-d / 2}px 0 0 ${-d / 2}px`;
      }
      const pl = e.titlePlacements.get(i);
      if (pl) {
        const key = `${pl.dx.toFixed(0)},${pl.dy.toFixed(0)}`;
        if (t.dataset.at !== key) {
          t.dataset.at = key;
          t.style.transform = `translate(${pl.dx.toFixed(1)}px, ${pl.dy.toFixed(1)}px)`;
        }
        const band = String(Math.round((h.y + pl.dy) / 12));
        if (t.dataset.band !== band) {
          t.dataset.band = band;
          t.style.setProperty("--halo", paperAt(h.y + pl.dy + 11));
        }
        const o = pl.alpha.toFixed(2);
        if (t.style.opacity !== o) t.style.opacity = o;
      } else if (t.style.opacity !== "0") {
        t.style.opacity = "0";
      }
    }
    const outOff = !e.canZoomOut;
    const inOff = !e.canZoomIn;
    if (zoomOutRef.current && zoomOutRef.current.disabled !== outOff) zoomOutRef.current.disabled = outOff;
    if (zoomInRef.current && zoomInRef.current.disabled !== inOff) zoomInRef.current.disabled = inOff;
  });

  /* ---------------- input handlers ---------------- */
  const prefetched = useRef(new Set<number>());
  /** Warm a moment's page while the pointer rests on its knot. */
  const prefetchMoment = useEffectEvent((i: number) => {
    if (prefetched.current.has(i)) return;
    prefetched.current.add(i);
    router.prefetch(`/memories/${model.knots[i].moment.id}`);
  });
  const openMoment = useEffectEvent((i: number) => router.push(`/memories/${model.knots[i].moment.id}`));
  const follow = useEffectEvent((i: number) => setLit(i));
  const firstTouch = useEffectEvent(() => interact());

  /* ---------------- engine ---------------- */
  useEffect(() => {
    const canvas = canvasRef.current!;
    const wrap = wrapRef.current!;
    const css = getComputedStyle(document.documentElement);
    const hand = css.getPropertyValue("--font-caveat").trim() || "cursive";
    const e = new ThreadsEngine({
      canvas,
      model,
      fonts: {
        hand,
        sans: css.getPropertyValue("--font-nunito").trim() || "sans-serif",
        serif: css.getPropertyValue("--font-fraunces").trim() || "serif",
      },
      reduced: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      zoom: seed.zoom,
      focus: seed.focus,
      highlight: seed.highlight,
      onFrame: (en) => placeOverlay(en),
    });
    eng.current = e;

    const resize = () => {
      const r = wrap.getBoundingClientRect();
      e.resize(r.width, r.height, window.devicePixelRatio);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    resize();
    const remeasure = () => {
      e.measureTitles();
      e.kick();
    };
    document.fonts?.load(`500 ${TITLE_FONT_PX}px ${hand}`).then(remeasure);
    document.fonts?.load(`500 ${TITLE_FONT_PX_COMPACT}px ${hand}`).then(remeasure);
    document.fonts?.ready.then(remeasure);

    const unbind = e.bindInput({
      open: (i) => openMoment(i),
      follow: (i) => follow(i),
      hoverKnot: (i) => prefetchMoment(i),
      interact: () => firstTouch(),
    });

    if (process.env.NODE_ENV !== "production") {
      (window as unknown as { __threads?: ThreadsEngine }).__threads = e;
    }

    return () => {
      unbind();
      ro.disconnect();
      e.destroy();
      eng.current = null;
    };
  }, [model, seed]);

  /* ---------------- buttons & keys ---------------- */
  const zoomButton = useCallback((dir: 1 | -1) => eng.current?.zoomStep(dir), []);

  /** Keyboard focus on a moment: bring its knot into view and show its title. */
  const focusKnot = useCallback((i: number) => eng.current?.focusKnot(i), []);

  const onKey = useCallback(
    (ev: React.KeyboardEvent) => {
      const e = eng.current;
      if (!e) return;
      if (ev.key === "+" || ev.key === "=") e.zoomStep(1);
      else if (ev.key === "-" || ev.key === "_") e.zoomStep(-1);
      else if (ev.key === "ArrowLeft") e.panBySpan(-0.25);
      else if (ev.key === "ArrowRight") e.panBySpan(0.25);
      else if (ev.key === "0") e.zoomOutAll();
      else if (ev.key === "Escape") setLit(-1);
      else return;
      ev.preventDefault();
      interact();
    },
    [interact, setLit],
  );

  const followed = highlight >= 0 ? model.strands[highlight] : null;
  const followedCount = followed ? followed.momentIds.length : 0;

  return (
    <main className="fixed inset-0 overflow-clip select-none">
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
        <div className="pointer-events-none absolute inset-0 overflow-clip" role="group" aria-label="Moments">
          {model.knots.map((k, i) => (
            <Link
              key={k.moment.id}
              href={`/memories/${k.moment.id}`}
              prefetch={false}
              data-moment={k.moment.id}
              onFocus={() => focusKnot(i)}
              onBlur={() => focusKnot(-1)}
              ref={(el) => {
                linkRefs.current[i] = el;
              }}
              className="threads-moment absolute left-0 top-0"
              style={{ visibility: "hidden" }}
              aria-label={`${k.moment.title}, ${k.moment.year}. ${k.people.map((p) => p.name).join(", ")}.`}
            >
              <span
                ref={(el) => {
                  ringRefs.current[i] = el;
                }}
                className="threads-ring absolute left-0 top-0 block"
              />
              <span
                ref={(el) => {
                  titleRefs.current[i] = el;
                }}
                className="threads-title absolute left-0 top-0 whitespace-nowrap"
                data-title={k.moment.title}
                style={{ opacity: 0 }}
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
          <p className="kicker">
            <Link href="/" className="text-ink-faint no-underline transition-colors hover:text-ink-soft">
              Family tree
            </Link>
            <span aria-hidden className="mx-2 opacity-60">
              /
            </span>
            <span className="text-ink-soft">Threads</span>
          </p>
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

      {/* Zoom: two small paper buttons (top right on wide screens, under the axis on phones). */}
      <div className="absolute bottom-6 right-[var(--gutter)] flex gap-2 sm:bottom-auto sm:top-9">
        <button ref={zoomOutRef} type="button" onClick={() => zoomButton(-1)} className="threads-zoom" aria-label="Zoom out towards decades">
          <span aria-hidden>&minus;</span>
        </button>
        <button ref={zoomInRef} type="button" onClick={() => zoomButton(1)} className="threads-zoom" aria-label="Zoom in towards single years">
          <span aria-hidden>+</span>
        </button>
      </div>

      {/* One quiet line on how to move about, gone after the first gesture. */}
      <p className="threads-hint caption pointer-events-none" style={{ opacity: interacted ? 0 : undefined }} aria-hidden>
        <span className="threads-hint-fine">
          scroll to zoom &middot; drag through time<span className="threads-hint-more"> &middot; click a thread</span>
        </span>
        <span className="threads-hint-coarse">pinch to zoom &middot; drag through time</span>
      </p>

      {/* For keyboard and screen-reader users: follow a person's thread. */}
      <nav className="sr-only" aria-label="Follow a thread">
        <ul>
          {model.strands.map((s, i) => (
            <li key={s.person.id}>
              <button
                type="button"
                data-person={s.person.id}
                onClick={() => setLit(highlight === i ? -1 : i)}
                aria-pressed={highlight === i}
              >
                Follow {s.person.name}&rsquo;s thread ({s.momentIds.length} moments)
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </main>
  );
}
