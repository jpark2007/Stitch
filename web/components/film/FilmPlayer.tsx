"use client";

// The photo film player: a ready Film played live in the browser. One shot at
// a time fills a taped paper print, drifting slowly toward its focus (pan or
// zoom per its motion) and crossfading into the next; each beat's title comes
// up as a small card when the beat starts, the shot's caption is handwritten
// in the print's bottom margin, and subtitles follow the narration a few
// words at a time, the word being spoken marked.
//
// Time: the narration <audio> is the master clock when the film has audio;
// with none (or if it fails to load) a silent requestAnimationFrame clock
// drives the same timeline. All the time math is in ./timeline (pure, unit
// tested). Each frame writes only transform and opacity to the shot layers
// (GPU-only, no React render); React renders only when the shot, card,
// subtitle or whole second changes. Shots around the current one stay
// mounted, so the next photo is loaded before its cut. Reduced motion: the
// photos hold still and only crossfade.
//
// Controls: one big play/pause, a thin scrub bar with a tick at each beat,
// the time, subtitles on/off, and "Watch again" at the end. Space plays and
// pauses, the arrow keys seek five seconds. The controls fade while playing
// and come back on any pointer move or key.
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { FOCUS_RING, FOCUS_RING_WITHIN } from "@/components/flow/Button";
import type { Film } from "@/lib/types";

import { Tape } from "./Tape";
import {
  SEEK_STEP_SECONDS,
  TITLE_CARD_SECONDS,
  beatTicks,
  clampTime,
  createSilentClock,
  filmLength,
  fitFor,
  formatTime,
  kenBurns,
  layersAt,
  phrases,
  seekBy,
  shotIndexAt,
  spokenLength,
  subtitleAt,
  titleCardAt,
  transformCss,
  type FilmClock,
  type Phrase,
  type ShotLayer,
  type Subtitle,
} from "./timeline";

/** The print's photo window: landscape 3:2, like a print from a roll of film. */
const FRAME_ASPECT = 3 / 2;
/** Controls fade this long after the last pointer move while playing. */
const IDLE_MS = 2600;

const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";

function subscribeReduced(onChange: () => void): () => void {
  const mq = window.matchMedia(REDUCED_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

function useReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReduced,
    () => window.matchMedia(REDUCED_QUERY).matches,
    () => false,
  );
}

/** The narration audio as the film's clock. */
function audioClock(a: HTMLAudioElement, length: number): FilmClock {
  return {
    time: () => clampTime(a.currentTime, length),
    playing: () => !a.paused && !a.ended,
    play: async () => {
      if (a.ended || a.currentTime >= length) a.currentTime = 0;
      await a.play();
    },
    pause: () => a.pause(),
    seek: (t) => {
      a.currentTime = clampTime(t, length);
    },
  };
}

const perfNow = () => performance.now();

/** What React renders at a time: everything that changes in steps, not every frame. */
interface View {
  shot: number;
  card: number | null;
  sub: Subtitle | null;
  second: number;
}

function viewAt(film: Film, lines: readonly Phrase[], t: number): View {
  return {
    shot: Math.max(0, shotIndexAt(film.shots, t)),
    card: titleCardAt(film.beats, t),
    sub: subtitleAt(film.words, lines, t),
    second: Math.floor(t),
  };
}

function sameView(a: View, b: View): boolean {
  return (
    a.shot === b.shot &&
    a.card === b.card &&
    a.second === b.second &&
    (a.sub === null ? b.sub === null : b.sub !== null && a.sub.phrase === b.sub.phrase && a.sub.current === b.sub.current)
  );
}

// Keyframes for the beat title card and the caption, hoisted once by React.
const PLAYER_CSS = `
@keyframes stitch-film-card {
  0% { opacity: 0; transform: translate3d(0, 6px, 0) rotate(-1.5deg); }
  14% { opacity: 1; transform: translate3d(0, 0, 0) rotate(-1.5deg); }
  84% { opacity: 1; transform: translate3d(0, 0, 0) rotate(-1.5deg); }
  100% { opacity: 0; transform: translate3d(0, -3px, 0) rotate(-1.5deg); }
}
@keyframes stitch-film-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
`;

export function FilmPlayer(props: {
  film: Film;
  /** A media id (bare or "media/<id>") as a URL the browser can load. */
  mediaUrl: (mediaId: string) => string;
  className?: string;
}) {
  const { film, mediaUrl, className } = props;
  const length = useMemo(() => filmLength(film), [film]);
  const lines = useMemo(() => phrases(film.words), [film.words]);
  const ticks = useMemo(() => beatTicks(film.beats, length), [film.beats, length]);
  const reduced = useReducedMotion();

  const [audioFailed, setAudioFailed] = useState(false);
  const audioSrc = film.audio && !audioFailed ? mediaUrl(film.audio) : null;
  // The <audio> element, once mounted, is the clock; until then (and with no
  // audio, or after it failed) the silent clock is. Switching carries the time over.
  const [audioEl, setAudioEl] = useState<HTMLAudioElement | null>(null);
  const clock = useMemo<FilmClock>(
    () => (audioEl ? audioClock(audioEl, length) : createSilentClock(length, perfNow)),
    [audioEl, length],
  );

  const [playing, setPlaying] = useState(false);
  const [ended, setEnded] = useState(false);
  const [started, setStarted] = useState(false);
  const [subtitlesOn, setSubtitlesOn] = useState(true);
  const [active, setActive] = useState(true);
  const [fits, setFits] = useState<Record<string, "cover" | "contain">>({});
  const [view, setView] = useState<View>(() => viewAt(film, lines, 0));

  const lastT = useRef(0);
  const viewRef = useRef(view);
  const layerEls = useRef(new Map<number, HTMLDivElement>());
  const fillRef = useRef<HTMLDivElement>(null);
  const thumbRef = useRef<HTMLDivElement>(null);
  const rangeRef = useRef<HTMLInputElement>(null);
  const scrubbing = useRef(false);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** One shot layer's opacity, stacking and pan/zoom at the layers given. */
  const applyLayer = useCallback(
    (el: HTMLDivElement, i: number, layers: readonly ShotLayer[]) => {
      const shot = film.shots[i];
      if (!shot) return;
      const at = layers.findIndex((l) => l.index === i);
      const layer = at >= 0 ? layers[at] : null;
      el.style.opacity = layer ? String(layer.opacity) : "0";
      el.style.zIndex = layer ? String(at + 1) : "0";
      const motion = el.firstElementChild as HTMLElement | null;
      if (motion) motion.style.transform = transformCss(kenBurns(shot.motion, shot.focus, layer ? layer.progress : 0, reduced));
    },
    [film.shots, reduced],
  );

  /** Draw time `t`: shot layers and the scrub bar directly, the rest through React when it changes. */
  const paint = useCallback(
    (t: number) => {
      lastT.current = t;
      const layers = layersAt(film.shots, t);
      for (const [i, el] of layerEls.current) applyLayer(el, i, layers);
      const f = length > 0 ? clampTime(t, length) / length : 0;
      if (fillRef.current) fillRef.current.style.transform = `scaleX(${f})`;
      if (thumbRef.current) thumbRef.current.style.transform = `translate3d(${f * 100}%, 0, 0)`;
      if (rangeRef.current && !scrubbing.current) rangeRef.current.value = String(t);
      const v = viewAt(film, lines, t);
      if (!sameView(v, viewRef.current)) {
        viewRef.current = v;
        setView(v);
      }
    },
    [film, lines, length, applyLayer],
  );

  /** A shot layer as it mounts: drawn at once at the current time, so it never shows unstyled. */
  const setLayer = useCallback(
    (el: HTMLDivElement | null) => {
      if (!el) return;
      const i = Number(el.dataset.shot);
      const map = layerEls.current;
      map.set(i, el);
      applyLayer(el, i, layersAt(film.shots, lastT.current));
      return () => {
        if (map.get(i) === el) map.delete(i);
      };
    },
    [applyLayer, film.shots],
  );

  // Redraw when the drawing itself changes (reduced motion turned on or off).
  useEffect(() => {
    paint(lastT.current);
  }, [paint]);

  // A new clock (the audio failed and the silent one takes over): carry on from the same place.
  useEffect(() => {
    clock.seek(lastT.current);
  }, [clock]);

  const finish = useCallback(() => {
    setPlaying(false);
    setEnded(true);
    paint(length);
  }, [paint, length]);

  // While playing: a frame loop reading the clock. Stopping (pause, the end,
  // leaving the page) pauses the clock, so nothing plays on in a hidden page.
  useEffect(() => {
    if (!playing) return;
    let live = true;
    if (!clock.playing()) {
      clock.play().catch(() => {
        if (live) setPlaying(false);
      });
    }
    let raf = 0;
    const loop = () => {
      const t = clock.time();
      paint(t);
      if (t >= length - 1e-3) {
        finish();
        return;
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      live = false;
      cancelAnimationFrame(raf);
      clock.pause();
    };
  }, [playing, clock, paint, finish, length]);

  const wake = useCallback(() => {
    setActive(true);
    if (idleTimer.current) clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(() => setActive(false), IDLE_MS);
  }, []);

  useEffect(
    () => () => {
      if (idleTimer.current) clearTimeout(idleTimer.current);
    },
    [],
  );

  const play = useCallback(() => {
    if (lastT.current >= length - 1e-3) {
      clock.seek(0);
      paint(0);
    }
    // Started here, in the press itself, so the browser lets the narration play.
    clock.play().catch(() => setPlaying(false));
    setEnded(false);
    setStarted(true);
    setPlaying(true);
    wake();
  }, [clock, length, paint, wake]);

  const pause = useCallback(() => {
    clock.pause();
    setPlaying(false);
    paint(clock.time());
  }, [clock, paint]);

  const toggle = useCallback(() => (playing ? pause() : play()), [playing, pause, play]);

  const seekTo = useCallback(
    (t: number) => {
      const c = clampTime(t, length);
      clock.seek(c);
      paint(c);
      setStarted(true);
      if (c < length - 1e-3) setEnded(false);
    },
    [clock, length, paint],
  );

  // Space plays and pauses, the arrows seek, wherever focus is on the page
  // (but in a text field, and space on a button presses that button).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const el = e.target instanceof HTMLElement ? e.target : null;
      const tag = el?.tagName ?? "";
      if (tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      if (tag === "INPUT" && (el as HTMLInputElement).type !== "range") return;
      if (e.key === " ") {
        if (tag === "BUTTON" || tag === "A") return;
        e.preventDefault();
        toggle();
        wake();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        seekTo(seekBy(lastT.current, e.key === "ArrowLeft" ? -SEEK_STEP_SECONDS : SEEK_STEP_SECONDS, length));
        wake();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle, seekTo, wake, length]);

  const shots = film.shots;
  // The shot on screen, the one before (fading out of view) and the next two (loading before their cuts).
  const mounted: number[] = [];
  for (let i = view.shot - 1; i <= view.shot + 2; i++) if (i >= 0 && i < shots.length) mounted.push(i);
  const current = shots[view.shot];
  const caption = started && current?.caption ? current.caption : null;
  const card = started && view.card !== null ? film.beats[view.card] : null;
  const showControls = !playing || active;
  const atStart = !started;

  return (
    <section
      aria-label={`${film.title}, a film`}
      onPointerMove={wake}
      className={`relative mx-auto w-full ${className ?? ""}`}
    >
      <style href="stitch-film-player" precedence="default">
        {PLAYER_CSS}
      </style>

      {/* The print: light paper with a wide bottom margin for the caption, taped to the page. */}
      <div className="relative rounded-paper bg-paper-light p-2 pb-11 shadow-float sm:p-3.5 sm:pb-16">
        <Tape className="-top-3 left-[7%] -rotate-[7deg]" />
        <Tape className="-top-3 right-[7%] rotate-[5deg]" />

        <div
          className={`relative aspect-[3/2] w-full overflow-hidden rounded-[1px] bg-wood-dark ${
            playing && !active ? "cursor-none" : "cursor-pointer"
          }`}
          onClick={toggle}
        >
          {mounted.map((i) => {
            const shot = shots[i];
            const url = mediaUrl(shot.photoId);
            const fit = fits[shot.photoId] ?? "cover";
            const [u, v] = shot.focus ?? [0.5, 0.5];
            const onScreen = i === view.shot;
            return (
              <div key={i} ref={setLayer} data-shot={i} aria-hidden={!onScreen} className="absolute inset-0 will-change-[opacity]">
                <div className="absolute inset-0 will-change-transform">
                  {fit === "contain" ? (
                    // A soft, blurred copy fills the frame behind a photo of another shape.
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={url}
                      alt=""
                      draggable={false}
                      className="absolute inset-0 h-full w-full scale-110 object-cover opacity-75 blur-2xl saturate-[0.85]"
                    />
                  ) : null}
                  {/* Family media (and mock blob URLs): next/image cannot optimise these. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={url}
                    alt={onScreen ? (shot.caption ?? `Photo ${i + 1} of ${shots.length}`) : ""}
                    draggable={false}
                    decoding="async"
                    onLoad={(e) => {
                      const img = e.currentTarget;
                      const next = fitFor(img.naturalWidth, img.naturalHeight, FRAME_ASPECT);
                      setFits((f) => (f[shot.photoId] === next ? f : { ...f, [shot.photoId]: next }));
                    }}
                    className={`absolute inset-0 h-full w-full select-none ${
                      fit === "contain" ? "object-contain drop-shadow-[0_6px_18px_rgb(0_0_0/0.35)]" : "object-cover"
                    }`}
                    style={{ objectPosition: `${u * 100}% ${v * 100}%` }}
                  />
                </div>
              </div>
            );
          })}

          {/* Warm vignette and a soft shade under the subtitles and controls. */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 z-10 bg-[radial-gradient(120%_95%_at_50%_45%,transparent_55%,rgb(47_30_18/0.35))]"
          />
          <div
            aria-hidden="true"
            className={`pointer-events-none absolute inset-x-0 bottom-0 z-10 h-1/2 bg-gradient-to-t from-[rgb(30_20_12/0.62)] via-[rgb(30_20_12/0.18)] to-transparent transition-opacity duration-500 ${
              atStart ? "opacity-0" : "opacity-100"
            }`}
          />

          {card ? (
            <div
              key={`card-${view.card}`}
              className="pointer-events-none absolute top-3 left-3 z-20 max-w-[75%] origin-top-left rounded-paper bg-paper-light px-3 py-1.5 shadow-lift sm:top-6 sm:left-6 sm:px-4 sm:py-2.5"
              style={
                reduced
                  ? { transform: "rotate(-1.5deg)" }
                  : {
                      animation: `stitch-film-card ${TITLE_CARD_SECONDS}s cubic-bezier(0.22, 0.8, 0.24, 1) both`,
                      animationPlayState: playing ? "running" : "paused",
                    }
              }
            >
              <p className="font-display text-[0.95rem] leading-tight text-ink sm:text-[1.35rem]">{card.title}</p>
              <span aria-hidden="true" className="mt-1 block h-0 w-10 border-t-[1.5px] border-dashed border-accent sm:w-14" />
            </div>
          ) : null}

          {subtitlesOn && started && view.sub ? (
            <p
              className={`pointer-events-none absolute inset-x-4 z-20 text-center font-serif text-[0.9rem] leading-snug text-balance text-paper-light [text-shadow:0_1px_3px_rgb(20_12_6/0.75)] transition-[bottom] duration-300 sm:inset-x-14 sm:text-[1.3rem] ${
                showControls ? "bottom-12 sm:bottom-16" : "bottom-3 sm:bottom-6"
              }`}
            >
              {view.sub.words.map((w, i) => (
                <span
                  key={`${view.sub?.phrase}-${i}`}
                  className={
                    i < view.sub!.current
                      ? ""
                      : i === view.sub!.current
                        ? "underline decoration-mustard decoration-2 underline-offset-[0.28em]"
                        : "opacity-55"
                  }
                >
                  {w}
                  {i < view.sub!.words.length - 1 ? " " : ""}
                </span>
              ))}
            </p>
          ) : null}

          {atStart ? (
            <Poster film={film} length={length} onPlay={play} />
          ) : ended ? (
            <EndSheet film={film} onReplay={play} />
          ) : !playing ? (
            <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-[rgb(30_20_12/0.12)]">
              <BigButton label="Play" onClick={play} className="pointer-events-auto" />
            </div>
          ) : null}

          {atStart ? null : (
            <div
              onClick={(e) => e.stopPropagation()}
              className={`absolute inset-x-0 bottom-0 z-30 flex items-center gap-1 px-1.5 pb-0.5 transition-opacity duration-300 focus-within:opacity-100 motion-reduce:transition-none sm:gap-3 sm:px-4 sm:pb-2 ${
                showControls ? "opacity-100" : "opacity-0"
              }`}
            >
              <button
                type="button"
                onClick={toggle}
                aria-label={playing ? "Pause" : ended ? "Watch again" : "Play"}
                className={`flex size-11 shrink-0 items-center justify-center rounded-full text-paper-light hover:bg-paper-light/15 ${FOCUS_RING}`}
              >
                {playing ? <PauseIcon className="size-5" /> : <PlayIcon className="size-5" />}
              </button>
              <span className="shrink-0 text-xs font-semibold text-paper-light tabular-nums [text-shadow:0_1px_2px_rgb(0_0_0/0.5)] sm:text-[0.8125rem]">
                {formatTime(view.second)}
                <span className="text-paper-light/70"> / {formatTime(length)}</span>
              </span>
              <div className={`relative mx-1 flex h-11 min-w-0 flex-1 items-center rounded-paper sm:mx-2 ${FOCUS_RING_WITHIN}`}>
                <div className="relative h-[3px] w-full rounded-full bg-paper-light/30">
                  <div
                    ref={fillRef}
                    className="absolute inset-0 origin-left rounded-full bg-paper-light will-change-transform"
                    style={{ transform: "scaleX(0)" }}
                  />
                  {ticks.map((f, i) => (
                    <span
                      key={i}
                      aria-hidden="true"
                      className="absolute top-1/2 h-2.5 w-[2px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-mustard"
                      style={{ left: `${f * 100}%` }}
                    />
                  ))}
                  <div ref={thumbRef} className="pointer-events-none absolute inset-0 will-change-transform" style={{ transform: "translate3d(0, 0, 0)" }}>
                    <span className="absolute top-1/2 left-0 block size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-paper-light shadow-[0_1px_3px_rgb(0_0_0/0.4)]" />
                  </div>
                </div>
                <input
                  ref={rangeRef}
                  type="range"
                  min={0}
                  max={length}
                  step={0.1}
                  defaultValue={0}
                  aria-label="Seek"
                  aria-valuetext={`${formatTime(view.second)} of ${formatTime(length)}`}
                  onPointerDown={() => {
                    scrubbing.current = true;
                  }}
                  onPointerUp={() => {
                    scrubbing.current = false;
                  }}
                  onPointerCancel={() => {
                    scrubbing.current = false;
                  }}
                  onInput={(e) => seekTo(Number(e.currentTarget.value))}
                  className="absolute inset-0 h-full w-full cursor-pointer appearance-none opacity-0"
                />
              </div>
              <button
                type="button"
                onClick={() => setSubtitlesOn((on) => !on)}
                aria-pressed={subtitlesOn}
                aria-label="Subtitles"
                className={`flex h-11 shrink-0 items-center justify-center rounded-full px-2 text-paper-light hover:bg-paper-light/15 ${FOCUS_RING}`}
              >
                <span
                  aria-hidden="true"
                  className={`rounded-[3px] border-[1.5px] border-current px-1 text-[0.6875rem] leading-[1.15] font-bold tracking-wide ${
                    subtitlesOn ? "bg-paper-light text-ink" : "opacity-70"
                  }`}
                >
                  CC
                </span>
              </button>
            </div>
          )}
        </div>

        {/* The caption, handwritten in the print's bottom margin. */}
        <div className="pointer-events-none absolute inset-x-3 bottom-0 flex h-11 items-center sm:inset-x-5 sm:h-16">
          {caption ? (
            <p
              key={view.shot}
              className="truncate font-hand text-[1.3rem] leading-none text-ink-soft sm:text-[1.85rem]"
              style={reduced ? undefined : { animation: "stitch-film-fade 0.7s ease-out both" }}
            >
              {caption}
            </p>
          ) : null}
        </div>
      </div>

      {audioSrc ? (
        <audio
          ref={setAudioEl}
          src={audioSrc}
          preload="auto"
          onEnded={finish}
          onError={() => setAudioFailed(true)}
        />
      ) : null}
    </section>
  );
}

function PlayIcon(props: { className?: string }) {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className={props.className}>
      <path d="M6 4.2v11.6a.6.6 0 0 0 .92.5l9.1-5.8a.6.6 0 0 0 0-1L6.92 3.7A.6.6 0 0 0 6 4.2Z" fill="currentColor" />
    </svg>
  );
}

function PauseIcon(props: { className?: string }) {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className={props.className}>
      <rect x="5" y="4" width="3.4" height="12" rx="0.9" fill="currentColor" />
      <rect x="11.6" y="4" width="3.4" height="12" rx="0.9" fill="currentColor" />
    </svg>
  );
}

/** The one big round button: paper, with the play mark in the accent. */
function BigButton(props: { label: string; onClick: () => void; className?: string }) {
  return (
    <button
      type="button"
      aria-label={props.label}
      onClick={(e) => {
        e.stopPropagation();
        props.onClick();
      }}
      className={`flex size-16 items-center justify-center rounded-full bg-paper-light/95 text-accent-deep shadow-float transition-transform duration-200 ease-paper hover:scale-[1.04] motion-reduce:transition-none sm:size-20 ${FOCUS_RING} ${props.className ?? ""}`}
    >
      <PlayIcon className="ml-1 size-7 sm:size-8" />
    </button>
  );
}

/** Before the first play: the film's title over its first photo, and the big play button. */
function Poster(props: { film: Film; length: number; onPlay: () => void }) {
  const { film, length, onPlay } = props;
  return (
    <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-[rgb(30_20_12/0.42)] px-6 text-center sm:gap-5">
      <div>
        <p className="text-[0.6875rem] font-bold tracking-[0.18em] text-paper-light/85 uppercase sm:text-xs">
          {film.year} &middot; {spokenLength(length)}
        </p>
        <p className="mt-1 font-display text-[1.5rem] leading-[1.1] text-balance text-paper-light [text-shadow:0_2px_10px_rgb(20_12_6/0.5)] sm:mt-2 sm:text-[2.6rem]">
          {film.title}
        </p>
      </div>
      <BigButton label={`Play ${film.title}`} onClick={onPlay} />
    </div>
  );
}

/** At the end: a quiet "the end" and the way to watch it again. */
function EndSheet(props: { film: Film; onReplay: () => void }) {
  return (
    <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2 bg-[rgb(30_20_12/0.55)] px-6 text-center sm:gap-4">
      <p className="font-hand text-[1.9rem] leading-none text-paper-light sm:text-[2.75rem]">The end</p>
      <p className="font-display text-base text-balance text-paper-light/85 sm:text-xl">{props.film.title}</p>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          props.onReplay();
        }}
        className={`mt-1 inline-flex min-h-11 items-center gap-2 rounded-card bg-paper-light px-5 py-2.5 font-semibold text-ink shadow-lift hover:bg-paper ${FOCUS_RING}`}
      >
        <PlayIcon className="size-4 text-accent-deep" />
        Watch again
      </button>
    </div>
  );
}
