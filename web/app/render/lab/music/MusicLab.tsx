"use client";

/**
 * The era music lab: ten era cards, a year slider that steps through eras
 * like turning spreads, the book's own mute, a narration duck, the book
 * open/closed switch and a level meter.
 *
 * It drives the engine exactly as the book will, through useEraMusic. The
 * meter and stats read the engine in their own loops and write straight to
 * the DOM, so playing music never re-renders React.
 */

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore, type RefObject } from "react";
import { ERAS, PALETTES, eraForYear, eraMidYear, useEraMusic, type EraId, type EraMusic } from "@/components/audio";
import type { OfflineCue } from "@/components/audio/offline";
import { isMuted, setMuted, subscribeMuted } from "@/components/book/sound";
import { Tape } from "@/components/paper/Tape";

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
  const music = useEraMusic(year, { active, ducked });
  const era = eraForYear(year);
  const probe = params.get("probe") === "1";

  useProbe(probe, music);

  const step = (dir: 1 | -1) => {
    const i = era ? ERAS.findIndex((e) => e.id === era) : dir === 1 ? -1 : ERAS.length;
    const next = ERAS[Math.max(0, Math.min(ERAS.length - 1, i + dir))];
    setYear(eraMidYear(next.id));
  };

  return (
    <main className="relative min-h-dvh w-full px-[var(--gutter)] pb-10 pt-6 sm:pt-8">
      <div className="mx-auto flex w-full max-w-[76rem] flex-col gap-6 sm:gap-8">
        <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
          <div>
            <p className="kicker">Stitch · lab</p>
            <h1 className="mt-1 text-[2rem] sm:text-[2.75rem]">Era music</h1>
          </div>
          <p className="caption max-w-[30ch] text-[1.2rem] sm:text-right">
            {era ? `${eraLabel(era)} · ${PALETTES[era].name.toLowerCase()}` : "tap an era to start the music"}
          </p>
        </header>

        <ol aria-label="Eras" className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 sm:gap-4 lg:grid-cols-5">
          {ERAS.map((e, i) => (
            <li key={e.id}>
              <EraCard id={e.id} index={i} playing={e.id === era} onPick={() => setYear(eraMidYear(e.id))} />
            </li>
          ))}
        </ol>

        <section
          aria-label="Playback"
          className="relative grid gap-5 rounded-paper border border-rule bg-paper-light p-4 shadow-lift sm:p-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] lg:gap-10"
        >
          <YearSlider year={year} onYear={setYear} onStep={step} />
          <div className="flex min-w-0 flex-col gap-4">
            <div className="flex flex-wrap gap-2">
              <Toggle on={!muted} onClick={() => setMuted(!muted)} label="Sound" title="The book's mute button" />
              <Toggle on={ducked} onClick={() => setDucked((d) => !d)} label="Narrating" title="Duck the music 10 dB under a voice" />
              <Toggle on={active} onClick={() => setActive((a) => !a)} label="Book open" title="Closed, the music ebbs away and sleeps" />
            </div>
            <LevelMeter music={music} />
          </div>
        </section>
      </div>
    </main>
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
      className={`group relative flex h-full w-full flex-col items-start gap-1 rounded-paper border bg-paper-light px-3.5 pb-3 pt-3 text-left transition-[transform,box-shadow,border-color] duration-300 ease-[var(--ease-paper)] sm:px-4 sm:pb-4 sm:pt-4 ${
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
        <span className="font-display text-[1.35rem] leading-tight text-ink sm:text-[1.6rem]">{eraLabel(id)}</span>
        <span aria-hidden className="size-2.5 shrink-0 rounded-full" style={{ background: ERA_COLOR[id], opacity: playing ? 1 : 0.55 }} />
      </span>
      <span className="text-[0.9rem] font-semibold text-ink-soft">{p.name}</span>
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
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="kicker">The spread’s year</p>
          <p className="font-display text-[2.4rem] leading-none tabular-nums text-ink sm:text-[2.8rem]" aria-live="polite">
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
          className="relative z-10 h-8 w-full cursor-pointer appearance-none bg-transparent [&::-moz-range-thumb]:size-5 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-paper-light [&::-moz-range-thumb]:bg-accent [&::-webkit-slider-thumb]:size-5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-paper-light [&::-webkit-slider-thumb]:bg-accent [&::-webkit-slider-thumb]:shadow-[0_1px_2px_rgb(74_47_29/0.35),0_3px_8px_-2px_rgb(74_47_29/0.4)]"
        />
        {/* The track: a stitched thread, sewn solid up to the year. */}
        <span aria-hidden className="pointer-events-none absolute inset-x-0 top-[1.05rem] border-t-[1.5px] border-dashed border-ink-faint/45" />
        <span
          aria-hidden
          className="pointer-events-none absolute left-0 top-[1.0rem] h-[2.5px] rounded-full bg-accent/70"
          style={{ width: `${year ? pct(value) : 0}%` }}
        />
        <ol aria-hidden className="pointer-events-none absolute inset-x-0 top-8 h-5">
          {ERAS.slice(1).map((e) => (
            <li key={e.id} className="absolute -translate-x-1/2" style={{ left: `${pct(e.from)}%` }}>
              <span className="mx-auto block h-1.5 w-px bg-ink-faint/50" />
              <span
                className={`kicker hidden text-[0.58rem] tracking-[0.08em] sm:block ${e.id === era ? "text-accent-deep" : ""}`}
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
      className="grid size-10 place-items-center rounded-full border border-rule bg-paper text-ink-soft shadow-paper transition-[transform,opacity] duration-200 hover:-translate-y-0.5 hover:text-ink active:scale-95 disabled:pointer-events-none disabled:opacity-35"
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        {dir === 1 ? <path d="M9 5.5 15.5 12 9 18.5" /> : <path d="M15 5.5 8.5 12 15 18.5" />}
      </svg>
    </button>
  );
}

function Toggle({ on, onClick, label, title }: { on: boolean; onClick: () => void; label: string; title: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      title={title}
      className={`flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm transition-colors duration-200 ${
        on ? "border-accent/40 bg-accent text-paper-light shadow-paper" : "border-rule bg-paper text-ink-soft hover:text-ink"
      }`}
    >
      <span aria-hidden className={`size-1.5 rounded-full ${on ? "bg-paper-light" : "bg-ink-faint/60"}`} />
      {label}
    </button>
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
      const state = s.context === "none" ? "waiting for a tap" : s.phase === "asleep" ? `asleep (${s.context})` : s.context;
      stats.current.textContent = `scheduler ${perSec.toFixed(2)} ms/s · worst pass ${s.maxTickMs.toFixed(1)} ms · ${s.voices} voice${s.voices === 1 ? "" : "s"} · ${state}`;
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

/* ------------------------------------------------------------------------ */
/* Probe (?probe=1): offline renders for the verification script             */
/* ------------------------------------------------------------------------ */

declare global {
  interface Window {
    __eraMusic?: {
      render(cues: OfflineCue[], seconds: number): Promise<{ wav: string; notes: number; busyMs: number; maxTickMs: number }>;
      stats(): ReturnType<EraMusic["stats"]> | null;
    };
  }
}

function useProbe(on: boolean, music: RefObject<EraMusic | null>) {
  useEffect(() => {
    if (!on) return;
    let live = true;
    void import("@/components/audio/offline").then(({ renderEraMusic, encodeWav }) => {
      if (!live) return;
      window.__eraMusic = {
        async render(cues, seconds) {
          const r = await renderEraMusic(cues, seconds);
          const bytes = new Uint8Array(encodeWav(r.buffer));
          let bin = "";
          for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
          return { wav: btoa(bin), notes: r.notes, busyMs: r.busyMs, maxTickMs: r.maxTickMs };
        },
        stats: () => music.current?.stats() ?? null,
      };
    });
    return () => {
      live = false;
      delete window.__eraMusic;
    };
  }, [on, music]);
}
