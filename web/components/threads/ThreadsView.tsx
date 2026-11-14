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
import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState, type ReactNode } from "react";
import { parseZoom } from "./camera";
import { ThreadsEngine } from "./engine";
import { MenuDisclosure } from "./MenuDisclosure";
import { buildModel, type ThreadMoment, type ThreadPerson } from "./model";
import { paperAt, TITLE_FONT_PX, TITLE_FONT_PX_COMPACT } from "./titles";

export interface ThreadsViewProps {
  familyName: string;
  people: ThreadPerson[];
  moments: ThreadMoment[];
  /**
   * The family these threads belong to. Moment and book links carry it as
   * ?family=<id> (those pages are not under /families/<id>/ yet); left out
   * for the local demo family.
   */
  familyId?: string;
  /** The user's families, for the switcher under the title (shown for two or more). */
  families?: { id: string; name: string }[];
  onSwitchFamily?: (id: string) => void;
  /** One quiet line under the title: where the data comes from, moments without a year. */
  note?: ReactNode;
  /** Raw query params: ?zoom=decade|year|<multiple>, ?focus=<year>, ?highlight=<personId>. */
  zoom?: string;
  focus?: string;
  highlight?: string;
}

const NOW = new Date().getFullYear();

const enc = encodeURIComponent;

const TITLE_CLASS = "mt-1 text-[1.6rem] leading-tight sm:text-[2rem]";
const TITLE_STYLE = { fontVariationSettings: '"SOFT" 100, "WONK" 1' } as const;

export default function ThreadsView(props: ThreadsViewProps) {
  const { people, moments, familyName, familyId, families, onSwitchFamily, note } = props;
  const familyQuery = familyId ? `?family=${enc(familyId)}` : "";
  const momentHref = (id: string) => `/memories/${enc(id)}${familyQuery}`;
  const personHref = (id: string) => `/people/${enc(id)}${familyQuery}`;
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
    router.prefetch(momentHref(model.knots[i].moment.id));
  });
  const openMoment = useEffectEvent((i: number) => router.push(momentHref(model.knots[i].moment.id)));
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
              href={momentHref(k.moment.id)}
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
                data-title={k.moment.label ?? k.moment.title}
                style={{ opacity: 0 }}
                aria-hidden
              >
                {k.moment.label ?? k.moment.title}
              </span>
            </Link>
          ))}
        </div>
      </div>

      {/* Quiet header: where you are, and whose thread you are following. */}
      <header className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between px-[var(--gutter)] pt-6 sm:pt-8">
        <div className="pointer-events-auto min-w-0 max-w-full sm:max-w-[min(44rem,calc(100%-7rem))]">
          <p className="kicker">
            <Link href="/" className="text-ink-faint no-underline transition-colors hover:text-ink-soft">
              Family tree
            </Link>
            <span aria-hidden className="mx-2 opacity-60">
              /
            </span>
            <span className="text-ink-soft">Threads</span>
          </p>
          {families && families.length > 1 && onSwitchFamily ? (
            <MenuDisclosure
              className="block"
              ariaLabel={`${familyName}. Switch family`}
              buttonClassName="flex items-center"
              trigger={(button) => (
                <h1 className={TITLE_CLASS} style={TITLE_STYLE} title={familyName}>
                  {button}
                </h1>
              )}
              label={
                <>
                  <span className="threads-clip min-w-0">{familyName}</span>
                  <svg width="16" height="10" viewBox="0 0 16 10" aria-hidden className="ml-2 mt-1 shrink-0 text-ink-faint">
                    <path d="M2 2.5 L8 7.5 L14 2.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeDasharray="2.6 1.8" strokeLinecap="round" />
                  </svg>
                </>
              }
            >
              <p className="kicker px-3 pb-1 pt-1.5">Your families</p>
              {families.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  className="threads-menu-item font-display text-[1.05rem] leading-snug"
                  aria-current={f.id === familyId ? "true" : undefined}
                  onClick={() => f.id !== familyId && onSwitchFamily(f.id)}
                >
                  <span className="min-w-0 break-words">{f.name}</span>
                </button>
              ))}
            </MenuDisclosure>
          ) : (
            <h1 className={TITLE_CLASS} style={TITLE_STYLE} title={familyName}>
              <span className="threads-clip block">{familyName}</span>
            </h1>
          )}
          {note ? <div className="-mt-0.5 text-[0.85rem] leading-snug text-ink-faint">{note}</div> : null}
          <p className="caption mt-1 flex h-7 min-w-0 items-baseline gap-3 text-[1.25rem]" aria-live="polite">
            {followed ? (
              <>
                <button
                  type="button"
                  onClick={() => setLit(-1)}
                  className="flex min-w-0 cursor-pointer items-baseline text-left"
                  style={{ color: followed.person.color }}
                  aria-label={`Following ${followed.person.name}. Show everyone.`}
                >
                  {/* Padded so the clip never shaves the handwriting's last stroke. */}
                  <span className="min-w-0 truncate pr-[0.2em]">{followed.person.name}&rsquo;s thread</span>
                  <span className="shrink-0 whitespace-pre">
                    &middot; {followedCount} {followedCount === 1 ? "moment" : "moments"}
                  </span>
                  <span className="ml-2 shrink-0 text-ink-faint">&times;</span>
                </button>
                <Link
                  href={personHref(followed.person.id)}
                  className="shrink-0 text-ink-faint no-underline transition-colors hover:text-ink-soft"
                  aria-label={`Open ${followed.person.name}'s book`}
                >
                  their book &rarr;
                </Link>
              </>
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
