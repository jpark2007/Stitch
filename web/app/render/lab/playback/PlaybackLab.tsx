"use client";

// Developer lab for the timeline player: plays the hymn-books sample through
// useTimelinePlayer and draws what the player returns, flat, so the stage's
// numbers can be checked by eye. ?t=<seconds> freezes time (for screenshots);
// ?narrator=<personId> picks the starting teller.

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

import {
  usePlayerFrame,
  useTimelinePlayer,
  type PlaybackController,
  type PlaybackFrame,
  type PlayerStatus,
} from "@/components/stage/playback";
import type { Interaction, MemoryResponse, Timeline } from "@/lib/types";
import sample from "@/public/samples/hymn_books.memory.json";

import { FrontView, TopView, type SceneDefs } from "./StageViews";

const SAMPLE = sample as unknown as MemoryResponse;
const TIMELINE = SAMPLE.memory.timeline as Timeline;
const DEFS: SceneDefs = {
  people: new Map((SAMPLE.people ?? []).map((p) => [p.id, p])),
  props: new Map((SAMPLE.props ?? []).map((p) => [p.id, p])),
  sets: new Map((SAMPLE.sets ?? []).map((s) => [s.id, s])),
};

const nameOf = (id: string | null | undefined) => (id ? (DEFS.people.get(id)?.name ?? id) : "");
const colorOf = (id: string | null | undefined) => (id ? DEFS.people.get(id)?.character.color : undefined);

/** Parent swaps after t = 0, for the scrubber. */
const SWAPS = TIMELINE.tracks
  .filter((tr) => tr.target.startsWith("prop:") && tr.channel === "parent")
  .flatMap((tr) =>
    tr.keys
      .filter(([t]) => t > 0)
      .map(([t, v]) => ({ t, label: v === "ground" ? "dropped" : `to ${nameOf(String(v).split(":")[1]?.split(".")[0])}` })),
  );

function parseTime(raw: string | null): number | null {
  if (raw === null || raw.trim() === "") return null;
  const t = Number(raw);
  return Number.isFinite(t) ? t : null;
}

const fmt = (n: number, digits = 2) => (Object.is(n, -0) ? 0 : n).toFixed(digits);
const vec = (v: readonly number[]) => v.map((n) => fmt(n)).join("  ");

export function PlaybackLab() {
  const params = useSearchParams();
  const freezeAt = parseTime(params.get("t"));
  const narrator = params.get("narrator");
  const { controller, status } = useTimelinePlayer(TIMELINE, { narrator, freezeAt });
  const frame = usePlayerFrame(controller);
  useKeyboard(controller);

  return (
    <main
      className="mx-auto flex min-h-dvh max-w-[1440px] flex-col gap-5 px-[var(--gutter)] py-7"
      data-player-time={fmt(frame.t, 3)}
      data-frozen={freezeAt !== null ? "" : undefined}
    >
      <header className="flex items-end justify-between gap-8">
        <div>
          <p className="kicker">Lab · timeline player</p>
          <h1 className="mt-2 text-[2.1rem]">{SAMPLE.memory.title}</h1>
          <p className="mt-1 text-sm text-ink-faint">
            {SAMPLE.memory.year} · {DEFS.sets.get(frame.shot?.set ?? "")?.name ?? "no shot"} · {fmt(TIMELINE.duration)} s
          </p>
        </div>
        <TellerTabs controller={controller} status={status} />
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_330px] gap-5">
        <div className="flex min-w-0 flex-col gap-5">
          <Card title="Front" note="x across · y up">
            <div className="aspect-[3/1.28] w-full">
              <FrontView frame={frame} defs={DEFS} />
            </div>
          </Card>
          <div className="grid grid-cols-3 gap-5">
            <LayersPanel frame={frame} />
            <PropsPanel frame={frame} />
            <CameraPanel frame={frame} />
          </div>
        </div>
        <div className="flex min-w-0 flex-col gap-5">
          <Card title="From above" note="x across · z toward you">
            <div className="aspect-[3/3.8] w-full">
              <TopView frame={frame} defs={DEFS} />
            </div>
          </Card>
          {(TIMELINE.interactions ?? []).map((it) => (
            <PullTab key={it.id} controller={controller} interaction={it} />
          ))}
        </div>
      </div>

      <CaptionLine frame={frame} />
      <Transport controller={controller} status={status} frame={frame} />
    </main>
  );
}

// ---------------------------------------------------------------- pieces

function Card({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="rounded-card bg-paper-light/80 px-4 pb-3 pt-3 shadow-paper">
      <div className="mb-1 flex items-baseline justify-between">
        <h2 className="kicker">{title}</h2>
        {note ? <span className="text-xs text-ink-faint">{note}</span> : null}
      </div>
      {children}
    </section>
  );
}

function TellerTabs({ controller, status }: { controller: PlaybackController; status: PlayerStatus }) {
  const tellers = controller.compiled.narrators;
  const note =
    status.audio === "none"
      ? "No recording yet · captions only"
      : status.audio === "blocked"
        ? "Press play for sound"
        : status.audio === "error"
          ? "Recording failed to load"
          : null;
  return (
    <div className="flex flex-col items-end gap-2">
      <div role="tablist" aria-label="Telling" className="flex gap-1.5">
        {tellers.map((n) => {
          const active = status.narrator === n.id;
          return (
            <button
              key={n.id}
              role="tab"
              aria-selected={active}
              onClick={() => controller.setNarrator(n.id)}
              className={`relative rounded-t-[var(--radius-card)] px-4 pb-2 pt-2.5 text-sm font-semibold transition-colors ${
                active ? "bg-paper-light text-ink shadow-paper" : "bg-paper-deep/70 text-ink-soft hover:bg-paper-deep"
              }`}
            >
              <span
                className="mr-2 inline-block size-2 rounded-full align-middle"
                style={{ backgroundColor: colorOf(n.id) ?? "var(--ink-faint)" }}
              />
              {nameOf(n.id)}&rsquo;s telling
            </button>
          );
        })}
      </div>
      {note ? <p className="text-xs text-ink-faint">{note}</p> : null}
    </div>
  );
}

function CaptionLine({ frame }: { frame: PlaybackFrame }) {
  const { caption, line, wordIndex, narrator } = frame.narration;
  const words = line?.words;
  return (
    <div className="flex min-h-[3.2rem] items-center justify-center px-8 text-center" aria-live="polite">
      {caption ? (
        <p className="font-hand text-[2rem] leading-tight text-ink">
          <span className="mr-3 align-middle text-base" style={{ color: colorOf(narrator) }}>
            {nameOf(narrator)}
          </span>
          {words && words.length > 0
            ? caption.text.split(/\s+/).map((w, i) => (
                <span key={i} className={i <= wordIndex ? "text-ink" : "text-ink-faint/70"}>
                  {w}{" "}
                </span>
              ))
            : caption.text}
        </p>
      ) : null}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-card bg-paper-light/80 px-4 py-3 shadow-paper">
      <h2 className="kicker mb-2">{title}</h2>
      {children}
    </section>
  );
}

const LAYER_NAMES = ["Back", "Middle", "Front"];

function LayersPanel({ frame }: { frame: PlaybackFrame }) {
  return (
    <Panel title="Set layers · fold">
      <ul className="space-y-1.5">
        {frame.layers.map((l) => {
          const def = DEFS.sets.get(l.setId)?.layers[l.layer];
          return (
            <li key={l.target} className="grid grid-cols-[3.6rem_1fr_2.4rem] items-center gap-2 text-xs text-ink-soft">
              <span>{LAYER_NAMES[l.layer] ?? `Layer ${l.layer}`}</span>
              <span className="h-1.5 overflow-hidden rounded-full bg-paper-deep">
                <span
                  className="block h-full rounded-full"
                  style={{ width: `${Math.max(0, Math.min(1, l.fold)) * 100}%`, backgroundColor: def?.color ?? "var(--kraft)" }}
                />
              </span>
              <span className="text-right font-mono tabular-nums">{fmt(l.fold)}</span>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

function PropsPanel({ frame }: { frame: PlaybackFrame }) {
  return (
    <Panel title="Props · parent">
      <ul className="space-y-1 text-xs text-ink-soft">
        {frame.props.map((p) => {
          const where =
            p.parent.kind === "ground"
              ? "on the ground"
              : p.parent.kind === "puppet"
                ? `in ${nameOf(p.parent.id)}’s ${p.parent.socket === "hand_l" ? "left hand" : p.parent.socket === "hand_r" ? "right hand" : p.parent.socket}`
                : `on ${p.parent.id}.${p.parent.socket}`;
          return (
            <li key={p.id}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-semibold text-ink">{DEFS.props.get(p.id)?.name ?? p.id}</span>
                <span>{where}</span>
              </div>
              <div className="mt-0.5 font-mono tabular-nums text-ink-faint">
                {p.parent.kind === "ground" ? "at" : "offset"} {vec(p.position)}
                {p.state ? ` · ${p.state}` : ""}
              </div>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

function CameraPanel({ frame }: { frame: PlaybackFrame }) {
  const c = frame.camera;
  return (
    <Panel title="Camera">
      <dl className="grid grid-cols-[3.6rem_1fr] gap-x-2 gap-y-1 text-xs text-ink-soft">
        <dt>position</dt>
        <dd className="font-mono tabular-nums">{vec(c.position)}</dd>
        <dt>look at</dt>
        <dd className="font-mono tabular-nums">{vec(c.lookAt)}</dd>
        <dt>fov</dt>
        <dd className="font-mono tabular-nums">{fmt(c.fov, 1)}°</dd>
      </dl>
    </Panel>
  );
}

// ---------------------------------------------------------------- pull tab

function PullTab({ controller, interaction }: { controller: PlaybackController; interaction: Interaction }) {
  const track = useRef<HTMLDivElement>(null);
  const knob = useRef<HTMLDivElement>(null);
  const ci = controller.compiled.interaction(interaction.id);

  const valueAt = (e: ReactPointerEvent) => {
    const r = track.current!.getBoundingClientRect();
    const inset = 20; // half the knob
    return Math.min(1, Math.max(0, (e.clientX - r.left - inset) / (r.width - 2 * inset)));
  };
  // The knob is positioned imperatively: pointer moves should not re-render React.
  const show = (v: number | null) => {
    const k = knob.current;
    if (!k) return;
    k.style.left = `${(v ?? 0) * 100}%`;
    k.toggleAttribute("data-held", v !== null);
  };
  const onDown = (e: ReactPointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    const v = valueAt(e);
    show(v);
    controller.holdInteraction(interaction.id, v);
  };
  const onMove = (e: ReactPointerEvent) => {
    if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
    const v = valueAt(e);
    show(v);
    controller.holdInteraction(interaction.id, v);
  };
  // Like a real pull tab, it springs back when let go.
  const onUp = () => {
    show(null);
    controller.releaseInteraction(interaction.id);
  };

  if (!ci) return null;
  const targets = (ci.targets ?? ["everything"]).map((t) => {
    const [kind, id] = t.split(":");
    return kind === "puppet" ? nameOf(id) : (DEFS.props.get(id)?.name ?? t);
  });
  return (
    <Panel title="Pull tab">
      <p className="text-sm font-semibold text-ink">{interaction.label}</p>
      <div
        ref={track}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
        className="relative mt-3 h-8 cursor-grab touch-none select-none active:cursor-grabbing"
        role="slider"
        aria-label={interaction.label}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={controller.heldValue(interaction.id) ?? 0}
      >
        <div className="absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 rounded-full bg-paper-deep shadow-inset" />
        {/* The knob travels within the track: inset by half its width on each side. */}
        <div className="pointer-events-none absolute inset-y-0 left-5 right-5">
          <div
            ref={knob}
            className="absolute top-1/2 h-7 w-10 -translate-x-1/2 -translate-y-1/2 rounded-[3px] bg-kraft shadow-lift transition-[left] duration-200 ease-[var(--ease-paper)] data-[held]:transition-none"
            style={{ left: "0%" }}
          >
            <span className="absolute inset-y-1.5 left-1/2 w-px -translate-x-1/2 bg-wood-dark/30" />
          </div>
        </div>
      </div>
      <p className="mt-2 text-xs text-ink-faint">
        {ci.type === "scrub" ? (
          <>
            Scrubs <span className="font-mono tabular-nums">{fmt(ci.from)}–{fmt(ci.to)} s</span> for {targets.join(" and ")};
            the rest holds.
          </>
        ) : (
          <>Drives {ci.prop}</>
        )}
      </p>
    </Panel>
  );
}

// ---------------------------------------------------------------- transport

function Transport({
  controller,
  status,
  frame,
}: {
  controller: PlaybackController;
  status: PlayerStatus;
  frame: PlaybackFrame;
}) {
  const duration = controller.duration;
  const bar = useRef<HTMLDivElement>(null);
  const resume = useRef(false);
  const narratorTrack = controller.compiled.narrator(status.narrator);

  const timeAt = (e: ReactPointerEvent) => {
    const r = bar.current!.getBoundingClientRect();
    return (Math.min(1, Math.max(0, (e.clientX - r.left) / r.width))) * duration;
  };
  const onDown = (e: ReactPointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    resume.current = status.playing;
    controller.pause();
    controller.seek(timeAt(e));
  };
  const onMove = (e: ReactPointerEvent) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) controller.seek(timeAt(e));
  };
  const onUp = () => {
    if (resume.current) controller.play();
    resume.current = false;
  };
  const pct = (t: number) => `${(t / duration) * 100}%`;

  return (
    <div className="flex items-center gap-5 rounded-card bg-paper-light/80 px-5 py-3 shadow-paper">
      <button
        onClick={() => controller.toggle()}
        aria-label={status.playing ? "Pause" : "Play"}
        className="grid size-11 shrink-0 place-items-center rounded-full bg-ink text-paper-light shadow-lift transition-transform active:scale-95"
      >
        {status.playing ? (
          <svg viewBox="0 0 16 16" className="size-4" aria-hidden>
            <rect x="3.5" y="2.5" width="3" height="11" rx="1" fill="currentColor" />
            <rect x="9.5" y="2.5" width="3" height="11" rx="1" fill="currentColor" />
          </svg>
        ) : (
          <svg viewBox="0 0 16 16" className="ml-0.5 size-4" aria-hidden>
            <path d="M4.5 2.8v10.4a.6.6 0 0 0 .9.5l8.2-5.2a.6.6 0 0 0 0-1L5.4 2.3a.6.6 0 0 0-.9.5Z" fill="currentColor" />
          </svg>
        )}
      </button>

      <div className="shrink-0 whitespace-nowrap font-mono text-sm tabular-nums text-ink-faint">
        <span className="text-lg text-ink">{fmt(frame.t)}</span> / {fmt(duration)} s
      </div>

      <div
        ref={bar}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
        role="slider"
        aria-label="Scene time"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={Number(fmt(frame.t))}
        aria-valuetext={`${fmt(frame.t)} seconds`}
        tabIndex={0}
        className="relative h-12 min-w-0 flex-1 cursor-pointer touch-none select-none"
      >
        {/* caption starts for this teller, above the line */}
        {narratorTrack?.captions.map((c, i) => (
          <span
            key={i}
            className="absolute top-[0.55rem] h-2 w-[2px] -translate-x-1/2 rounded-full"
            style={{ left: pct(c.t), backgroundColor: colorOf(status.narrator) ?? "var(--ink-faint)" }}
          />
        ))}
        <div className="absolute inset-x-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-paper-deep" />
        <div className="absolute left-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-ink-soft" style={{ width: pct(frame.t) }} />
        {(TIMELINE.interactions ?? []).map((it) =>
          it.drives.type === "scrub" && it.drives.from != null && it.drives.to != null ? (
            <span
              key={it.id}
              className="absolute top-1/2 h-[7px] -translate-y-1/2 rounded-full bg-kraft/70"
              style={{ left: pct(it.drives.from), width: pct(it.drives.to - it.drives.from) }}
            />
          ) : null,
        )}
        {/* parent swaps, below the line */}
        {SWAPS.map((s) => (
          <span key={s.t} className="absolute top-[1.85rem] -translate-x-1/2 text-center" style={{ left: pct(s.t) }}>
            <span className="mx-auto block size-[7px] rotate-45 bg-ink-faint" />
            <span className="mt-0.5 block whitespace-nowrap text-[0.65rem] leading-none text-ink-faint">{s.label}</span>
          </span>
        ))}
        <span
          className="absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-paper-light bg-ink shadow-paper"
          style={{ left: pct(frame.t) }}
        />
      </div>

      <RateAndLoop controller={controller} status={status} />
    </div>
  );
}

const RATES = [0.5, 1, 2];

function RateAndLoop({ controller, status }: { controller: PlaybackController; status: PlayerStatus }) {
  return (
    <div className="flex shrink-0 items-center gap-3">
      <div role="group" aria-label="Speed" className="flex rounded-full bg-paper-deep/80 p-0.5">
        {RATES.map((r) => (
          <button
            key={r}
            onClick={() => controller.setRate(r)}
            aria-pressed={status.rate === r}
            className={`rounded-full px-2.5 py-1 font-mono text-xs tabular-nums ${
              status.rate === r ? "bg-paper-light text-ink shadow-paper" : "text-ink-soft hover:text-ink"
            }`}
          >
            {r === 0.5 ? "½" : r}×
          </button>
        ))}
      </div>
      <button
        onClick={() => controller.setLoop(!status.loop)}
        aria-pressed={status.loop}
        aria-label="Loop"
        className={`grid size-8 place-items-center rounded-full ${
          status.loop ? "bg-paper-light text-accent shadow-paper" : "text-ink-faint hover:text-ink-soft"
        }`}
      >
        <svg viewBox="0 0 16 16" className="size-4" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 7.5V7a3 3 0 0 1 3-3h6.5M10.5 2l2 2-2 2M13 8.5V9a3 3 0 0 1-3 3H3.5M5.5 14l-2-2 2-2" />
        </svg>
      </button>
    </div>
  );
}

// ---------------------------------------------------------------- keys

/** Space plays and pauses; arrows step 0.1 s (1 s with shift). */
function useKeyboard(controller: PlaybackController) {
  const onKey = useCallback(
    (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      if (e.key === " " && el?.tagName !== "BUTTON") {
        e.preventDefault();
        controller.toggle();
      } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const step = (e.shiftKey ? 1 : 0.1) * (e.key === "ArrowRight" ? 1 : -1);
        controller.seek(controller.time + step);
      }
    },
    [controller],
  );
  useEffect(() => {
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onKey]);
}

/** The static shell shown while the lab loads (before search params are known). */
export function PlaybackLabFallback() {
  return (
    <main className="mx-auto max-w-[1440px] px-[var(--gutter)] py-7">
      <p className="kicker">Lab · timeline player</p>
      <h1 className="mt-2 text-[2.1rem]">{SAMPLE.memory.title}</h1>
      <p className="mt-1 text-sm text-ink-faint">Loading the player…</p>
    </main>
  );
}
