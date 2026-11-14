"use client";

/**
 * /memories/<id>: one moment, opened as a pop-up book.
 *
 *   /memories/demo                   the hand-made demo book; its first spread plays the reference sample
 *   /memories/hymn_books             the reference sample: its scene plays as the pop-up
 *   /memories/<id>?family=<fid>      a saved moment from the API
 *
 * A moment is one spread: the story page on the left (year, title, the
 * storybook text, a taped label per telling, photos) and its scene on the
 * right, rising from the page when the spread opens and playing once it lies
 * still. Choosing a teller's label replays the scene from the start with
 * their words. The mute button silences the narration with the paper sounds.
 * Keyboards and screen readers get the same controls as plain HTML
 * (MomentKeys): a button per telling and a slider per pull tab.
 *
 * Query, for deterministic views and tests: ?state=closed|open, ?spread=<n>,
 * ?turn=<0..1>, ?focus=left|right, ?still=1, ?debug=1 (see readInitial),
 * ?narrator=<person id or name> (start with that telling), ?t=<seconds>
 * (hold the scene at that time once it opens, instead of playing).
 */

import dynamic from "next/dynamic";
import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { StageSlotArgs } from "@/components/book/StoryPage";
import { pickTeller } from "@/components/book/moment";
import { installMomentProbe } from "@/components/book/moment/probe";
import { playingTellings } from "@/components/book/moment/tellings";
import { isMuted, subscribeMuted } from "@/components/book/sound";
import type { BookPullTab, BookSpread, BookTeller } from "@/components/book/types";
import { useTimelinePlayer, type PlaybackController } from "@/components/stage/playback";
import { preloadScene, type SceneData } from "@/components/stage/play";
import { api, bareMediaId } from "@/lib/api";
import { isDemoBook, isSampleMoment, loadMomentBook, type MomentBookData } from "./moment";

// three.js needs the browser: load the book on the client only.
const Book3D = dynamic(() => import("@/components/book/Book3D").then((m) => m.Book3D), {
  ssr: false,
  loading: () => <BookLoading />,
});
const MomentStage = dynamic(() => import("@/components/book/moment/MomentStage").then((m) => m.MomentStage), { ssr: false });

type Search = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

/** -1|left, 1|right; anything else is the whole spread. */
function readFocus(v: string | undefined): -1 | 0 | 1 {
  if (v === "-1" || v === "left") return -1;
  if (v === "1" || v === "right") return 1;
  return 0;
}

/**
 * Reads ?state=closed|open, ?spread=<n>, ?turn=<0..1> (freeze mid-turn) and
 * ?focus=left|right (close-up on one page) for deterministic views, and the
 * test switches ?still=1 (no parallax, peels or hints) and ?debug=1
 * (`window.__book` is the controller; a probe card stands in each stage slot).
 */
function readInitial(q: Search) {
  const state = one(q.state);
  const spread = Number(one(q.spread));
  const turn = Number(one(q.turn));
  return {
    state: state === "open" || state === "closed" ? state : undefined,
    spread: Number.isFinite(spread) && spread > 0 ? spread : undefined,
    turn: one(q.turn) != null && Number.isFinite(turn) ? turn : null,
    focus: readFocus(one(q.focus)),
  } as const;
}

function readTime(v: string | undefined): number | null {
  if (v === undefined || v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

export function MemoryBook({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Search> }) {
  const { id } = use(params);
  const q = use(searchParams);
  const family = one(q.family)?.trim() || null;
  if (!isDemoBook(id) && !isSampleMoment(id) && !family) return <NotInBook />;
  return (
    <MomentBook
      id={id}
      family={family}
      initial={readInitial(q)}
      narrator={one(q.narrator) ?? null}
      freezeAt={readTime(one(q.t))}
      still={one(q.still) === "1"}
      debug={one(q.debug) === "1"}
    />
  );
}

interface MomentBookProps {
  id: string;
  family: string | null;
  initial: ReturnType<typeof readInitial>;
  narrator: string | null;
  freezeAt: number | null;
  still: boolean;
  debug: boolean;
}

type ViewProps = Omit<MomentBookProps, "id" | "family">;

type Loaded = { kind: "book"; book: MomentBookData } | { kind: "error"; detail: string };

/** Loads the book and gets its scene's art ready, then opens it. */
function MomentBook({ id, family, ...rest }: MomentBookProps) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    loadMomentBook(id, family, ctl.signal)
      .then(async (book) => {
        if (book.scene) await preloadScene(book.scene);
        if (!ctl.signal.aborted) setLoaded({ kind: "book", book });
      })
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        console.warn(`[book] ${id}:`, e);
        setLoaded({ kind: "error", detail: e instanceof Error ? e.message : String(e) });
      });
    return () => ctl.abort();
  }, [id, family]);

  if (!loaded) return <BookLoading />;
  if (loaded.kind === "error") return <NotInBook detail={loaded.detail} />;
  const { book } = loaded;
  return book.scene && book.playing ? (
    <PlayingBook book={book} scene={book.scene} playing={book.playing} {...rest} />
  ) : (
    <Book3D spreads={book.spreads} cover={book.cover} initial={rest.initial} still={rest.still} debug={rest.debug} />
  );
}

/**
 * A book with a moment that plays. Nothing here re-renders while it plays:
 * the book subscribes to which telling is playing itself (playingTellings),
 * and the stage reads the player inside the Canvas's frame loop.
 */
function PlayingBook({
  book,
  scene,
  playing,
  initial,
  narrator,
  freezeAt,
  still,
  debug,
}: ViewProps & { book: MomentBookData; scene: SceneData; playing: BookSpread }) {
  const tellers = useMemo(() => playing.tellings ?? [], [playing]);
  const familyId = scene.familyId;
  const resolveAudio = useCallback(
    (mediaId: string) => (familyId ? api.mediaUrl(familyId, bareMediaId(mediaId)) : null),
    [familyId],
  );
  // ?narrator by person id or name; the start is read once.
  const [start] = useState(() => pickTeller(tellers, narrator));
  // The book's own loop ticks the player (MomentStage), so a paused scene costs nothing.
  const { controller } = useTimelinePlayer(scene.timeline, { narrator: start, audio: false, clock: "manual", resolveAudio });

  // Narration plays through an element the page owns, so the book's mute
  // button silences it with the paper sounds.
  useEffect(() => {
    if (typeof Audio === "undefined") return;
    const el = new Audio();
    el.preload = "auto";
    el.muted = isMuted();
    controller.attachAudio(el);
    const off = subscribeMuted((m) => {
      el.muted = m;
    });
    return () => {
      off();
      controller.attachAudio(null);
    };
  }, [controller]);

  // The scene starts when the spread settles, not on a click, so a browser
  // may hold the voice back: the reader's next touch lets it in, in step.
  useEffect(() => {
    const resume = () => {
      const s = controller.getStatus();
      if (s.audio === "blocked" && s.playing) controller.play();
    };
    window.addEventListener("pointerdown", resume, true);
    window.addEventListener("keydown", resume, true);
    return () => {
      window.removeEventListener("pointerdown", resume, true);
      window.removeEventListener("keydown", resume, true);
    };
  }, [controller]);

  // Test hooks: window.__stitchMoment always; the raw player as __moment with ?debug=1.
  useEffect(() => installMomentProbe(controller), [controller]);
  useEffect(() => {
    if (!debug) return;
    const w = window as Window & { __moment?: unknown };
    w.__moment = controller;
    return () => {
      delete w.__moment;
    };
  }, [controller, debug]);

  const tellings = useMemo(() => playingTellings(controller, playing.id), [controller, playing.id]);

  const onTelling = useCallback(
    (_spreadId: string, tellerId: string) => {
      if (controller.getStatus().narrator === tellerId) {
        // The telling already playing: hear it again from the start.
        controller.seek(0);
        controller.play();
      } else controller.setNarrator(tellerId);
      const url = new URL(window.location.href);
      url.searchParams.set("narrator", tellerId);
      window.history.replaceState(window.history.state, "", url);
    },
    [controller],
  );

  // The pop-up stands on the playing spread only.
  const renderStage = useCallback(
    (args: StageSlotArgs) =>
      args.spread.id === playing.id && args.spread.stage ? (
        <MomentStage
          controller={controller}
          scene={scene}
          spread={args.spread}
          fonts={args.fonts}
          reveal={args.reveal}
          view={args.view}
          size={[args.width, args.depth]}
          freezeAt={freezeAt}
          onReady={markStageReady}
        />
      ) : null,
    [controller, scene, playing.id, freezeAt],
  );

  // useTimelinePlayer re-renders this component on every status change (each
  // caption, play, pause). The same elements back again let React skip the
  // book and its Canvas entirely: they hear what they need themselves.
  const bookView = useMemo(
    () => (
      <Book3D
        spreads={book.spreads}
        cover={book.cover}
        initial={initial}
        renderStage={renderStage}
        tellings={tellings}
        onTelling={onTelling}
        still={still}
        debug={debug}
      />
    ),
    [book.spreads, book.cover, initial, renderStage, tellings, onTelling, still, debug],
  );
  const keys = useMemo(
    () => (
      <MomentKeys
        controller={controller}
        title={playing.title}
        tellers={tellers}
        tabs={playing.stage?.pullTabs ?? []}
        onTelling={(id) => onTelling(playing.id, id)}
      />
    ),
    [controller, playing, tellers, onTelling],
  );

  return (
    <>
      {bookView}
      {keys}
    </>
  );
}

/**
 * The moment's controls as plain HTML, for keyboards and screen readers: a
 * button per telling ("Hear Ana's version") and a slider per pull tab. They
 * are hidden until one has focus; then they show as a small paper card in
 * the corner. The 3D tab follows the slider (PullTabLever reads the held
 * value), and leaving the slider lets go, as releasing the tab does.
 */
function MomentKeys({
  controller,
  title,
  tellers,
  tabs,
  onTelling,
}: {
  controller: PlaybackController;
  title: string;
  tellers: readonly BookTeller[];
  tabs: readonly BookPullTab[];
  onTelling: (tellerId: string) => void;
}) {
  // Re-renders this card only, when the teller changes.
  const narrator = useSyncExternalStore(
    controller.subscribe,
    () => controller.getStatus().narrator,
    () => null,
  );
  if (tellers.length < 2 && tabs.length === 0) return null;
  return (
    <section
      aria-label={`${title}: tellings and pull tabs`}
      className="pointer-events-none fixed bottom-[calc(4.75rem+env(safe-area-inset-bottom))] left-4 z-20 flex max-w-[min(22rem,calc(100vw-2rem))] translate-y-2 flex-col gap-3 rounded-[4px] bg-[#f7f0e1] p-4 text-ink opacity-0 shadow-[0_10px_30px_-10px_rgb(0_0_0/0.7)] transition-[opacity,translate] duration-300 focus-within:pointer-events-auto focus-within:translate-y-0 focus-within:opacity-100"
    >
      {tellers.length > 1 && (
        <nav aria-label={`Tellings of ${title}`} className="flex flex-wrap gap-2">
          {tellers.map((t) => (
            <button
              key={t.id}
              type="button"
              aria-pressed={narrator === t.id}
              onClick={() => onTelling(t.id)}
              className="rounded-[3px] border border-[#d9c9ab] bg-[#fbf7ee] px-3 py-1 font-hand text-xl leading-tight"
              style={{ borderBottom: `3px solid ${t.color}` }}
            >
              Hear {t.name}&rsquo;s version
            </button>
          ))}
        </nav>
      )}
      {tabs.map((tab) => (
        <PullSlider key={tab.id} controller={controller} tab={tab} />
      ))}
    </section>
  );
}

/**
 * A slider standing in for a pull tab: arrow keys pull it out (holding the
 * interaction, as a hand does), and leaving it lets go, so the scene
 * returns to its time as when the tab is released (CONTRACTS.md
 * Clarifications 7).
 */
function PullSlider({ controller, tab }: { controller: PlaybackController; tab: BookPullTab }) {
  const [value, setValue] = useState(0);
  const release = () => {
    controller.releaseInteraction(tab.id, { settle: "return" });
    setValue(0);
  };
  return (
    <label className="flex flex-col gap-1 font-hand text-xl leading-tight">
      {tab.label}
      <input
        type="range"
        min={0}
        max={100}
        value={value}
        aria-valuetext={`${value}% pulled`}
        onChange={(e) => {
          const v = Number(e.target.value);
          setValue(v);
          controller.holdInteraction(tab.id, v / 100);
        }}
        onBlur={release}
        onKeyDown={(e) => {
          // Escape lets go, as a hand does.
          if (e.key === "Escape") release();
        }}
        className="accent-[#b9573d]"
      />
    </label>
  );
}

/** For capture scripts: the scene on the page is fully drawn. */
function markStageReady() {
  document.documentElement.dataset.stageReady = "1";
}

function NotInBook({ detail }: { detail?: string }) {
  return (
    <div className="grid min-h-[100dvh] place-items-center px-6 text-center">
      <div>
        <p className="kicker">Moment</p>
        <h1 className="mt-3 text-4xl">This page isn&rsquo;t in the book yet</h1>
        <p className="mt-4 text-ink-soft">
          {detail ? "It could not be opened. " : "Open it from the family’s tree or threads. "}
          Meanwhile, <Link href="/memories/hymn_books">open a moment</Link> or <Link href="/memories/demo">the demo book</Link>.
        </p>
      </div>
    </div>
  );
}

export function BookLoading() {
  return (
    <div className="grid h-[100dvh] place-items-center" style={{ background: "#1a110b" }}>
      <p className="font-hand text-2xl text-[#f3e6cf]/60">opening the book&hellip;</p>
    </div>
  );
}
