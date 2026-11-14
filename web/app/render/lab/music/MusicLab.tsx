"use client";

/**
 * The era music lab: ten era cards, a year slider that steps through eras
 * like turning spreads, the book's own mute, a narration duck, the book
 * open/closed switch and a level meter.
 *
 * It drives the engine exactly as the book will, through useEraMusic, and
 * prefetches the next era as the book will prefetch the next spread's. The
 * meter and stats read the engine in their own loops and write straight to
 * the DOM, so playing music never re-renders React. The probe for the
 * verification scripts lives in probe.ts.
 */

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore, type RefObject } from "react";
import { ERAS, PALETTES, eraForYear, eraMidYear, useEraMusic, type EraId, type EraMusic } from "@/components/audio";
import { isMuted, setMuted, subscribeMuted } from "@/components/book/sound";
import { Tape } from "@/components/paper/Tape";
import { useMusicProbe } from "./probe";

const MIN_YEAR = 1925;
const MAX_YEAR = 2026;

/** A thread colour per era, for the card's swatch and tape. */
const ERA_COLOR: Record<EraId, string> = {
  pre1940: "#a9825a",
  "1940s": "#7f9873",
  "1950s": "#d9a6a0",
  "1960s": "#d3a03d",
  "1970s": "#b9573d",
  "1980s": "#8b5a7c",
  "1990s": "#5b82a6",
  "2000s": "#9aae7c",
  "2010s": "#c4a37a",
  "2020s": "#8fa7bd",
};

/** What each era sounds like, in a sentence (the now-playing note). */
const ERA_NOTE: Record<EraId, string> = {
  pre1940: "A music box picks out a waltz over a parlour piano’s oom-pah-pah.",
  "1940s": "Brushes and a walking bass keep a slow swing while the vibraphone hums.",
  "1950s": "A soft ‘ooh’ choir drifts over triplet piano and the old I–vi–IV–V.",
  "1960s": "One acoustic guitar, fingerpicked, the thumb walking the bass.",
  "1970s": "Warm electric-piano chords, a round bass and a rim that barely clicks.",
  "1980s": "A detuned synth pad breathes under a quiet square-wave arpeggio.",
  "1990s": "A lazy drum machine, chorused clean guitar and a sub bass felt more than heard.",
  "2000s": "Strummed acoustic guitar with a glockenspiel line and a shaker.",
  "2010s": "A wobbly jazz piano on a dusty beat, with vinyl crackle underneath.",
  "2020s": "An airy pad, an echoing pluck and a half-time beat, soft as a bedroom demo.",
};

const KEYS = ["C", "D♭", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"];

function eraLabel(id: EraId) {
  return id === "pre1940" ? "Before 1940" : id;
}

function meta(id: EraId) {
  const p = PALETTES[id];
  const mode = p.mode === "major" ? "major" : p.mode === "minor" ? "minor" : "dorian";
  return `${p.bpm} bpm · ${p.beatsPerBar}/4 · ${KEYS[p.tonic % 12]} ${mode}`;
}

function yearParam(v: string | null): number | null {
  if (!v) return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= MIN_YEAR && n <= MAX_YEAR ? Math.round(n) : null;
}

export function MusicLab() {
  const params = useSearchParams();
  const [year, setYear] = useState<number | null>(() => yearParam(params.get("year")));
  const [active, setActive] = useState(true);
  const [ducked, setDucked] = useState(false);
  const muted = useSyncExternalStore(subscribeMuted, isMuted, () => false);
  // The engine lives in <Player>, so the probe can unmount it to check teardown.
  const [mounted, setMounted] = useState(true);
  const music = useRef<EraMusic | null>(null);
  const onEngine = useCallback((m: EraMusic | null) => {
    music.current = m;
  }, []);
  const era = eraForYear(year);
  const probe = params.get("probe") === "1";
  const touched = useFirstGesture();

  useMusicProbe(probe, music, setMounted);

  const step = (dir: 1 | -1) => {
    const i = era ? ERAS.findIndex((e) => e.id === era) : dir === 1 ? -1 : ERAS.length;
    const next = ERAS[Math.max(0, Math.min(ERAS.length - 1, i + dir))];
    setYear(eraMidYear(next.id));
  };

  return (
    <main className="relative min-h-dvh w-full px-[var(--gutter)] pb-8 pt-5 sm:pb-10 sm:pt-8">
      {mounted && <Player year={year} active={active} ducked={ducked} onEngine={onEngine} />}
      <div className="mx-auto flex w-full max-w-[76rem] flex-col gap-4 sm:gap-8">
        <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-1">
          <div>
            <p className="kicker">Stitch · lab</p>
            <h1 className="mt-1 text-[1.85rem] sm:text-[2.75rem]">Era music</h1>
          </div>
          <NowPlaying era={era} muted={muted} active={active} ducked={ducked} touched={touched} />
        </header>

        <ol aria-label="Eras" className="grid grid-cols-2 gap-2 sm:grid-cols-3 sm:gap-4 lg:grid-cols-5">
          {ERAS.map((e, i) => (
            <li key={e.id}>
              <EraCard id={e.id} index={i} playing={e.id === era} onPick={() => setYear(eraMidYear(e.id))} />
            </li>
          ))}
        </ol>

        <section
          aria-label="Playback"
          className="relative grid gap-3 rounded-paper border border-rule bg-paper-light px-4 py-3.5 shadow-lift sm:gap-5 sm:p-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] lg:gap-10"
        >
          <YearSlider year={year} onYear={setYear} onStep={step} />
          <div className="flex min-w-0 flex-col gap-4">
            <div className="flex flex-wrap gap-x-2 gap-y-2">
              <Toggle on={!muted} onClick={() => setMuted(!muted)} label="Sound" hint="the book’s mute" />
              <Toggle on={ducked} onClick={() => setDucked((d) => !d)} label="Narrating" hint="duck 10 dB" />
              <Toggle on={active} onClick={() => setActive((a) => !a)} label="Book open" hint="shut, it ebbs away" />
            </div>
            <LevelMeter music={music} />
          </div>
        </section>
      </div>
    </main>
  );
}

/** Runs the engine through the book's hook and lends it to the lab's meter. */
function Player({
  year,
  active,
  ducked,
  onEngine,
}: {
  year: number | null;
  active: boolean;
  ducked: boolean;
  onEngine: (m: EraMusic | null) => void;
}) {
  const music = useEraMusic(year, { active, ducked });
  useEffect(() => {
    onEngine(music.engine());
    return () => onEngine(null);
  }, [music, onEngine]);
  // Warm the next era up, as the book will warm the next spread's.
  const era = eraForYear(year);
  useEffect(() => {
    const i = ERAS.findIndex((e) => e.id === era);
    if (i >= 0 && i + 1 < ERAS.length) music.prefetchYear(eraMidYear(ERAS[i + 1].id));
  }, [music, era]);
  return null;
}

/** Whether the reader has pressed anything yet (the music waits for that). */
function useFirstGesture() {
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (touched) return;
    const on = () => setTouched(true);
    window.addEventListener("pointerdown", on, { capture: true, once: true });
    window.addEventListener("keydown", on, { capture: true, once: true });
    return () => {
      window.removeEventListener("pointerdown", on, { capture: true });
      window.removeEventListener("keydown", on, { capture: true });
    };
  }, [touched]);
  return touched;
}

type Status = "waiting" | "muted" | "closed" | "ready" | "playing" | "ducked";

const STATUS_WORDS: Record<Status, string> = {
  waiting: "Waiting for a tap",
  muted: "Muted",
  closed: "Book closed",
  ready: "Ready",
  playing: "Now playing",
  ducked: "Under narration",
};

/**
 * The engine's state in words, and what the playing era sounds like. Derived
 * from the lab's own controls (and the reader's first press), never from the
 * audio, so it re-renders only when a control changes.
 */
function NowPlaying({
  era,
  muted,
  active,
  ducked,
  touched,
}: {
  era: EraId | null;
  muted: boolean;
  active: boolean;
  ducked: boolean;
  touched: boolean;
}) {
  const status: Status = muted ? "muted" : !active ? "closed" : !era ? "ready" : !touched ? "waiting" : ducked ? "ducked" : "playing";
  const sounding = status === "playing" || status === "ducked";
  const note = !touched && !muted ? "Tap anywhere to let the music start." : era ? ERA_NOTE[era] : "Pick an era, or slide through the years.";
  return (
    <div className="flex min-w-0 max-w-[34ch] flex-col gap-0.5 sm:items-end sm:text-right" aria-live="polite">
      <p className="kicker flex items-center gap-1.5">
        <span aria-hidden className={`size-1.5 rounded-full transition-colors duration-500 ${sounding ? "bg-accent" : "bg-ink-faint/50"}`} />
        {STATUS_WORDS[status]}
        {era && <span className="text-ink-faint">· {eraLabel(era)}</span>}
      </p>
      <p className={`caption text-[1.1rem] leading-snug transition-opacity duration-500 sm:text-[1.2rem] ${era && !sounding && touched ? "opacity-55" : ""}`}>
        {note}
      </p>
    </div>
  );
}

function EraCard({ id, index, playing, onPick }: { id: EraId; index: number; playing: boolean; onPick: () => void }) {
  const p = PALETTES[id];
  const tilt = ((index * 37) % 5) - 2;
  return (
    <button
      type="button"
      onClick={onPick}
      aria-pressed={playing}
      className={`group relative flex h-full w-full flex-col items-start gap-0.5 rounded-paper border bg-paper-light px-3 pb-2 pt-2 text-left transition-[transform,box-shadow,border-color] duration-300 ease-[var(--ease-paper)] sm:gap-1 sm:px-4 sm:pb-4 sm:pt-4 ${
        playing ? "border-accent/50 shadow-float" : "border-rule shadow-paper hover:-translate-y-0.5 hover:shadow-lift"
      }`}
      style={{ transform: playing ? `translateY(-4px) rotate(${tilt * 0.35}deg)` : undefined }}
    >
      {playing && (
        <Tape
          color={ERA_COLOR[id]}
          pattern="stripes"
          width={64}
          height={18}
          rotate={tilt * 2 - 3}
          seed={index + 3}
          className="pointer-events-none absolute -top-2 left-1/2 -translate-x-1/2"
        />
      )}
      <span className="flex w-full items-center justify-between gap-2">
        <span className="font-display text-[1.2rem] leading-tight text-ink sm:text-[1.6rem]">{eraLabel(id)}</span>
        <span aria-hidden className="size-2 shrink-0 rounded-full sm:size-2.5" style={{ background: ERA_COLOR[id], opacity: playing ? 1 : 0.55 }} />
      </span>
      <span className="text-[0.8rem] font-semibold text-ink-soft sm:text-[0.9rem]">{p.name}</span>
      <span className="caption hidden text-[1.05rem] leading-snug sm:block">{p.character}</span>
      <span className="kicker mt-auto hidden pt-2 text-[0.62rem] sm:block">{meta(id)}</span>
      {playing && <span className="sr-only">(playing)</span>}
    </button>
  );
}

function YearSlider({ year, onYear, onStep }: { year: number | null; onYear: (y: number) => void; onStep: (d: 1 | -1) => void }) {
  const value = year ?? 1967;
  const era = eraForYear(year);
  const pct = (y: number) => ((y - MIN_YEAR) / (MAX_YEAR - MIN_YEAR)) * 100;
  return (
    <div className="flex min-w-0 flex-col gap-2 sm:gap-3">
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="kicker">The spread’s year</p>
          <p className="mt-0.5 font-display text-[2rem] leading-none tabular-nums text-ink sm:text-[2.8rem]" aria-live="polite">
            {year ?? "—"}
          </p>
        </div>
        <div className="flex gap-1.5">
          <TurnButton dir={-1} onClick={() => onStep(-1)} disabled={era === "pre1940"} />
          <TurnButton dir={1} onClick={() => onStep(1)} disabled={era === "2020s"} />
        </div>
      </div>
      <div className="relative pb-6 pt-1">
        <input
          type="range"
          min={MIN_YEAR}
          max={MAX_YEAR}
          step={1}
          value={value}
          aria-label="Year"
          aria-valuetext={year ? `${year}, ${era ? eraLabel(era) : ""}` : "not started"}
          onChange={(e) => onYear(Number(e.target.value))}
          className="relative z-10 h-8 w-full cursor-pointer appearance-none rounded-full bg-transparent focus-visible:outline-offset-0 [&::-moz-range-thumb]:size-5 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-paper-light [&::-moz-range-thumb]:bg-accent [&::-webkit-slider-thumb]:size-5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-paper-light [&::-webkit-slider-thumb]:bg-accent [&::-webkit-slider-thumb]:shadow-[0_1px_2px_rgb(74_47_29/0.35),0_3px_8px_-2px_rgb(74_47_29/0.4)]"
        />
        {/* The track: a stitched thread, sewn solid up to the year. */}
        <span aria-hidden className="pointer-events-none absolute inset-x-0 top-[1.05rem] border-t-[1.5px] border-dashed border-ink-faint/45" />
        <span
          aria-hidden
          className="pointer-events-none absolute left-0 top-[1.0rem] h-[2.5px] rounded-full bg-accent/70"
          style={{ width: `${year ? pct(value) : 0}%` }}
        />
        <ol aria-hidden className="pointer-events-none absolute inset-x-0 top-[2.3rem] h-5">
          {ERAS.slice(1).map((e) => (
            <li key={e.id} className="absolute -translate-x-1/2" style={{ left: `${pct(e.from)}%` }}>
              <span className="mx-auto block h-1.5 w-px bg-ink-faint/50" />
              <span
                className={`kicker block text-[0.56rem] tracking-[0.04em] sm:text-[0.58rem] sm:tracking-[0.08em] ${e.id === era ? "text-accent-deep" : ""}`}
              >
                {e.id === "pre1940" ? "" : `’${String(e.from).slice(2)}`}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function TurnButton({ dir, onClick, disabled }: { dir: 1 | -1; onClick: () => void; disabled: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={dir === 1 ? "Next era" : "Previous era"}
      className="grid size-11 place-items-center rounded-full border border-rule bg-paper text-ink-soft shadow-paper transition-[transform,opacity] duration-200 hover:-translate-y-0.5 hover:text-ink active:scale-95 disabled:pointer-events-none disabled:opacity-35"
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        {dir === 1 ? <path d="M9 5.5 15.5 12 9 18.5" /> : <path d="M15 5.5 8.5 12 15 18.5" />}
      </svg>
    </button>
  );
}

function Toggle({ on, onClick, label, hint }: { on: boolean; onClick: () => void; label: string; hint: string }) {
  const id = useId();
  return (
    <span className="flex flex-col items-center gap-1">
      <button
        type="button"
        onClick={onClick}
        aria-pressed={on}
        aria-describedby={id}
        className={`flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm transition-colors duration-200 ${
          on ? "border-accent/40 bg-accent text-paper-light shadow-paper" : "border-rule bg-paper text-ink-soft hover:text-ink"
        }`}
      >
        <span aria-hidden className={`size-1.5 rounded-full ${on ? "bg-paper-light" : "bg-ink-faint/60"}`} />
        {label}
      </button>
      <span id={id} className="text-[0.68rem] leading-none text-ink-faint">
        {hint}
      </span>
    </span>
  );
}

/* ------------------------------------------------------------------------ */
/* Meter                                                                     */
/* ------------------------------------------------------------------------ */

const FLOOR_DB = -48;
const toPct = (db: number) => Math.max(0, Math.min(1, (db - FLOOR_DB) / -FLOOR_DB));

/**
 * RMS bar, peak tick with a slow-falling hold, and a dBFS readout; the
 * dashed line marks the -12 dBFS ceiling the music stays under. Reads the
 * engine's analyser each frame (lab only) and writes styles directly.
 */
function LevelMeter({ music }: { music: RefObject<EraMusic | null> }) {
  const bar = useRef<HTMLSpanElement>(null);
  const tick = useRef<HTMLSpanElement>(null);
  const readout = useRef<HTMLSpanElement>(null);
  const stats = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    let raf = 0;
    let hold = FLOOR_DB;
    let holdAt = 0;
    let shownPeak = "";
    let rmsSmooth = FLOOR_DB;
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const m = music.current?.meter();
      const peakDb = m && m.peak > 0 ? 20 * Math.log10(m.peak) : FLOOR_DB;
      const rmsDb = m && m.rms > 0 ? 20 * Math.log10(m.rms) : FLOOR_DB;
      rmsSmooth += (Math.max(FLOOR_DB, rmsDb) - rmsSmooth) * 0.25;
      if (peakDb >= hold) {
        hold = peakDb;
        holdAt = now;
      } else if (now - holdAt > 1200) {
        // After a hold, fall back slowly toward the current peak.
        hold = Math.max(peakDb, FLOOR_DB, hold - 0.4);
      }
      if (bar.current) bar.current.style.transform = `scaleX(${toPct(rmsSmooth)})`;
      if (tick.current) tick.current.style.left = `${toPct(hold) * 100}%`;
      const label = hold <= FLOOR_DB + 0.5 ? "silent" : `${hold.toFixed(1)} dBFS peak`;
      if (label !== shownPeak && readout.current) {
        readout.current.textContent = label;
        shownPeak = label;
      }
    };
    raf = requestAnimationFrame(frame);
    // Scheduler cost, refreshed once a second.
    let last = { ticks: 0, busyMs: 0, at: performance.now() };
    const iv = setInterval(() => {
      const s = music.current?.stats();
      if (!s || !stats.current) return;
      const now = performance.now();
      const perSec = ((s.busyMs - last.busyMs) / (now - last.at)) * 1000;
      last = { ticks: s.ticks, busyMs: s.busyMs, at: now };
      const state =
        (s.context === "none" ? "no context yet" : s.phase === "asleep" ? `asleep (${s.context})` : s.context) + (s.warming ? " · warming the next era" : "");
      stats.current.textContent = `scheduler ${perSec.toFixed(2)} ms/s · worst pass ${s.maxTickMs.toFixed(1)} ms (first ${s.firstTickMs.toFixed(1)}) · ${s.voices} voice${s.voices === 1 ? "" : "s"} · ${state}`;
    }, 1000);
    return () => {
      cancelAnimationFrame(raf);
      clearInterval(iv);
    };
  }, [music]);

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <p className="kicker">Level</p>
        <span ref={readout} className="text-sm tabular-nums text-ink-soft">
          silent
        </span>
      </div>
      <div
        aria-hidden
        className="relative h-3.5 overflow-hidden rounded-full bg-paper-deep shadow-inset"
      >
        <span ref={bar} className="absolute inset-y-0 left-0 w-full origin-left rounded-full bg-ink-soft/70" style={{ transform: "scaleX(0)" }} />
        <span aria-hidden className="absolute inset-y-0 border-l-[1.5px] border-dashed border-accent-deep/60" style={{ left: `${toPct(-12) * 100}%` }} />
        <span ref={tick} aria-hidden className="absolute inset-y-0 w-[2px] -translate-x-1/2 bg-accent" style={{ left: "0%" }} />
      </div>
      <div aria-hidden className="relative h-4 text-[0.62rem] tabular-nums text-ink-faint">
        {[-48, -36, -24, -12, 0].map((db) => (
          <span key={db} className="absolute -translate-x-1/2" style={{ left: `${toPct(db) * 100}%` }}>
            {db}
          </span>
        ))}
      </div>
      <p ref={stats} className="text-[0.72rem] leading-snug text-ink-faint">
        scheduler idle
      </p>
    </div>
  );
}
