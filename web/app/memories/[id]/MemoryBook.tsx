"use client";

/**
 * /memories/<id>: one moment, opened as a pop-up book.
 *
 *   /memories/demo                   the hardcoded demo book (three spreads, no stages)
 *   /memories/hymn_books             the reference sample: its scene plays as the pop-up
 *   /memories/<id>?family=<fid>      a saved moment from the API
 *
 * A moment is one spread: the story page on the left (year, title, the
 * storybook text, a taped label per telling, photos) and its scene on the
 * right, rising from the page when the spread opens and playing once it lies
 * still. Choosing a teller's label replays the scene from the start with
 * their words. The mute button silences the narration with the paper sounds.
 *
 * Query, for deterministic views and tests: ?state=closed|open, ?spread=<n>,
 * ?turn=<0..1>, ?focus=left|right, ?still=1, ?debug=1 (see readInitial),
 * ?narrator=<personId> (start with that telling), ?t=<seconds> (hold the
 * scene at that time once it opens, instead of playing).
 */

import dynamic from "next/dynamic";
import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { demoCover, demoSpreads } from "@/components/book/demoSpreads";
import type { StageSlotArgs } from "@/components/book/StoryPage";
import { isMuted, subscribeMuted } from "@/components/book/sound";
import { useTimelinePlayer } from "@/components/stage/playback";
import { preloadScene, type SceneData } from "@/components/stage/play";
import { api, bareMediaId } from "@/lib/api";
import { isSampleMoment, loadMoment, type Moment } from "./moment";

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
  const initial = readInitial(q);
  const still = one(q.still) === "1";
  const debug = one(q.debug) === "1";

  if (id === "demo") {
    return <Book3D spreads={demoSpreads} cover={demoCover} initial={initial} still={still} debug={debug} />;
  }
  const family = one(q.family)?.trim() || null;
  if (!isSampleMoment(id) && !family) return <NotInBook />;
  return (
    <MomentBook
      id={id}
      family={family}
      initial={initial}
      narrator={one(q.narrator) ?? null}
      freezeAt={readTime(one(q.t))}
      still={still}
      debug={debug}
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

type Loaded = { kind: "moment"; moment: Moment } | { kind: "error"; detail: string };

/** Loads the moment and gets its scene's art ready, then opens the book. */
function MomentBook({ id, family, ...rest }: MomentBookProps) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    loadMoment(id, family, ctl.signal)
      .then(async (moment) => {
        if (moment.scene) await preloadScene(moment.scene);
        if (!ctl.signal.aborted) setLoaded({ kind: "moment", moment });
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
  const { moment } = loaded;
  return moment.scene ? <PlayingBook moment={moment} scene={moment.scene} {...rest} /> : <StillBook moment={moment} {...rest} />;
}

/** A moment with no compiled scene yet: its pages, no pop-up. */
function StillBook({ moment, initial, still, debug }: Omit<MomentBookProps, "id" | "family"> & { moment: Moment }) {
  const spreads = useMemo(() => [moment.spread, moment.end], [moment]);
  return <Book3D spreads={spreads} cover={moment.cover} initial={initial} still={still} debug={debug} />;
}

function PlayingBook({
  moment,
  scene,
  initial,
  narrator,
  freezeAt,
  still,
  debug,
}: Omit<MomentBookProps, "id" | "family"> & { moment: Moment; scene: SceneData }) {
  const { spread, end, cover } = moment;
  const spreads = useMemo(() => [spread, end], [spread, end]);
  const familyId = scene.familyId;
  const resolveAudio = useCallback(
    (mediaId: string) => (familyId ? api.mediaUrl(familyId, bareMediaId(mediaId)) : null),
    [familyId],
  );
  const start = narrator && spread.tellings?.some((t) => t.id === narrator) ? narrator : null;
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

  // Test hook (?debug=1): the moment's player.
  useEffect(() => {
    if (!debug) return;
    const w = window as Window & { __moment?: unknown };
    w.__moment = controller;
    return () => {
      delete w.__moment;
    };
  }, [controller, debug]);

  // The telling being told, or none once it has ended (every label then
  // offers its telling, the last one included: choose it to hear it again).
  // Only this changing re-renders the book, to reprint the labels.
  const telling = useCallback(() => {
    const s = controller.getStatus();
    return s.ended ? null : s.narrator;
  }, [controller]);
  const playing = useSyncExternalStore(controller.subscribe, telling, telling);
  const tellings = useMemo(() => (playing ? { [spread.id]: playing } : {}), [spread.id, playing]);

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

  // The pop-up stands on the moment's spread only (not the closing one).
  const renderStage = useCallback(
    (args: StageSlotArgs) =>
      args.spread.stage ? (
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
    [controller, scene, freezeAt],
  );

  return (
    <Book3D
      spreads={spreads}
      cover={cover}
      initial={initial}
      renderStage={renderStage}
      tellings={tellings}
      onTelling={onTelling}
      still={still}
      debug={debug}
    />
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
