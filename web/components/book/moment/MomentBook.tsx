"use client";

/**
 * <MomentBook>: a book whose moments play, each on its own spread. A
 * person's book (/people/[id]) and one moment's book (/memories/[id], the
 * one-moment case) are both this.
 *
 *   <MomentBook spreads={spreads} cover={cover} scenes={{ [spreadId]: scene }}
 *               initial={initial} narrator={null} freezeAt={null} contents />
 *
 * Every spread with a scene has its own player (ScenePlayers, ./tellings.ts):
 * the open spread's and its neighbours' are made as the reader nears them,
 * far ones are let go. Each has its own narration element, muted with the
 * book. A stage stands only on a spread that lies open or is turning
 * (Book3D mounts it with the page), and only a standing stage ticks its
 * player; MomentStage's play gate rises, plays and folds it with the page.
 * Turning the page pauses the old one. A teller's label switches only that
 * spread's telling; turning back to a spread replays its own last telling.
 *
 * Nothing here re-renders while a scene plays: the book subscribes to the
 * tellings itself, and the stages read their players in the frame loop.
 * This component renders when the book moves to another spread, no more.
 *
 * Keyboards and screen readers get the open spread's controls as plain HTML
 * (MomentKeys): a button per telling and a slider per pull tab.
 *
 * Test hooks: window.__stitchMoment (moment/probe.ts) reports the OPEN
 * spread's player; window.__moment is that player with `debug`; <html
 * data-stage-ready="1"> once the open spread's scene is drawn.
 *
 * Also here, for the two routes: the paper sheets for every state that is
 * not a book (BookSheet), the loading table (BookLoading), and a small
 * loader hook (useBookLoad).
 */

import dynamic from "next/dynamic";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import type { BookControls, BookInitial, BookView } from "@/components/book/Book3D";
import { ContentsCard, ContentsTab } from "@/components/book/Contents";
import { pickTeller } from "@/components/book/moment";
import { isMuted, subscribeMuted } from "@/components/book/sound";
import type { StageSlotArgs } from "@/components/book/StoryPage";
import type { BookCover, BookPullTab, BookSpread, BookTeller } from "@/components/book/types";
import { preloadScene, type SceneData } from "@/components/stage/play";
import { PlaybackController, PUPPET_BONES, compileTimeline } from "@/components/stage/playback";
import { Button, ButtonLink } from "@/components/flow/Button";
import { hrefs } from "@/components/flow/session";
import { api, bareMediaId } from "@/lib/api";
import { installMomentProbe } from "./probe";
import { ScenePlayers, type NarrationAudio } from "./tellings";

// three.js needs the browser: load the book on the client only.
const Book3D = dynamic(() => import("@/components/book/Book3D").then((m) => m.Book3D), {
  ssr: false,
  loading: () => <BookLoading />,
});
const MomentStage = dynamic(() => import("./MomentStage").then((m) => m.MomentStage), { ssr: false });

/** The spread a book opens at (1-based), as Book3D resolves it. */
export function startSpread(initial: BookInitial | undefined, count: number): number {
  return Math.min(Math.max(1, Math.round(initial?.spread ?? 1)), Math.max(1, count));
}

/** Where a narration's media id is heard: the family's media route, or nowhere for a local sample. */
function audioUrl(scene: SceneData, mediaId: string): string | null {
  return scene.familyId ? api.mediaUrl(scene.familyId, bareMediaId(mediaId)) : null;
}

function createNarration(): NarrationAudio | null {
  if (typeof Audio === "undefined") return null;
  const el = new Audio();
  el.preload = "auto";
  return el;
}

const MUTE = { get: isMuted, subscribe: subscribeMuted };

/**
 * Test hooks for the open spread's player: window.__stitchMoment always,
 * the raw player as window.__moment with ?debug=1. Returns the uninstall.
 */
function installProbes(c: PlaybackController, debug: boolean): () => void {
  const uninstall = installMomentProbe(c);
  const w = window as Window & { __moment?: unknown };
  if (debug) w.__moment = c;
  return () => {
    uninstall();
    if (w.__moment === c) delete w.__moment;
  };
}

/** For capture scripts: the open spread's scene is fully drawn. */
function markStageReady(ready: boolean) {
  if (ready) document.documentElement.dataset.stageReady = "1";
  else delete document.documentElement.dataset.stageReady;
}

/**
 * Replace query params on the current URL without a navigation. The
 * router's own replaceState is passed by: it would treat the new query as
 * another page (cacheComponents keeps a page per URL) and render the book
 * afresh beside this one, while this one keeps playing.
 */
function setQuery(changes: Record<string, string | null>) {
  try {
    const url = new URL(window.location.href);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null) url.searchParams.delete(k);
      else url.searchParams.set(k, v);
    }
    History.prototype.replaceState.call(window.history, window.history.state, "", url);
  } catch {
    // The URL is a convenience; the book works without it.
  }
}

export interface MomentBookProps {
  spreads: BookSpread[];
  cover: BookCover;
  /** Spread id -> the scene that plays on it. Remount (a new `key`) for another book. */
  scenes: Readonly<Record<string, SceneData>>;
  initial: BookInitial;
  /** ?narrator: the telling the opening spread starts with (person id or name). */
  narrator: string | null;
  /** ?t: hold each scene at this time once it opens, instead of playing. */
  freezeAt: number | null;
  still: boolean;
  debug: boolean;
  /** The way back, in the top corner. */
  back?: { href: string; label: string };
  /** A contents card beside the closed book, and a contents tab once it is open. */
  contents?: boolean;
  /** A quiet line over the table (the demo, Stitch out of reach). */
  note?: ReactNode;
}

export function MomentBook({
  spreads,
  cover,
  scenes,
  initial,
  narrator,
  freezeAt,
  still,
  debug,
  back,
  contents = false,
  note,
}: MomentBookProps) {
  const [first] = useState(() => startSpread(initial, spreads.length) - 1);
  const [players] = useState(() => {
    const opening = spreads[first];
    const want = opening ? pickTeller(opening.tellings ?? [], narrator) : null;
    const timelines: Record<string, SceneData["timeline"]> = {};
    for (const [id, scene] of Object.entries(scenes)) timelines[id] = scene.timeline;
    return new ScenePlayers({
      order: spreads.map((s) => s.id),
      timelines,
      start: opening && want ? { [opening.id]: want } : {},
      // Ticked by its stage in the book's frame loop, so a paused scene costs nothing.
      create: (id, teller) =>
        new PlaybackController(compileTimeline(scenes[id].timeline, { bones: PUPPET_BONES }), {
          narrator: teller,
          resolveAudio: (mediaId) => audioUrl(scenes[id], mediaId),
        }),
      createAudio: createNarration,
      muted: MUTE,
    });
  });

  // Which spread the book is on; React hears only when that changes.
  const [view, setView] = useState<BookView>(() => ({
    opened: initial.state === "open" || (initial.state !== "closed" && initial.spread != null),
    ready: false,
    spread: first + 1,
  }));
  const openSpread = spreads[view.spread - 1] ?? null;
  const openRef = useRef<string | null>(openSpread?.id ?? null);
  const controls = useRef<BookControls | null>(null);
  const onControls = useCallback((c: BookControls | null) => {
    controls.current = c;
  }, []);

  // Narration elements and mute while the page is shown (an Activity hides it on navigation).
  useEffect(() => players.activate(), [players]);

  // The book is on another spread: the players follow at once (from the
  // book's own call, not an effect later), and so do the test hooks.
  const readyIds = useRef(new Set<string>());
  const probeOff = useRef<(() => void) | null>(null);
  const settle = useCallback(
    (id: string | null) => {
      openRef.current = id;
      players.setOpen(id);
      markStageReady(id !== null && readyIds.current.has(id));
      probeOff.current?.();
      const c = id ? players.player(id) : null;
      probeOff.current = c ? installProbes(c, debug) : null;
    },
    [players, debug],
  );
  useEffect(() => {
    settle(openRef.current);
    return () => {
      probeOff.current?.();
      probeOff.current = null;
    };
  }, [settle]);
  const onView = useCallback(
    (v: BookView) => {
      setView(v);
      const id = spreads[v.spread - 1]?.id ?? null;
      if (id !== openRef.current) settle(id);
    },
    [spreads, settle],
  );

  // The scenes start when their spread settles, not on a click, so a browser
  // may hold the voice back: the reader's next touch lets it in, in step.
  useEffect(() => {
    const resume = () => players.resumeBlocked();
    window.addEventListener("pointerdown", resume, true);
    window.addEventListener("keydown", resume, true);
    return () => {
      window.removeEventListener("pointerdown", resume, true);
      window.removeEventListener("keydown", resume, true);
    };
  }, [players]);

  // Test hook with ?debug=1: the players as __momentBook.
  useEffect(() => {
    if (!debug) return;
    const w = window as Window & { __momentBook?: unknown };
    w.__momentBook = {
      open: () => players.openId,
      live: () => players.liveIds,
      tellings: () => players.get(),
    };
    return () => {
      delete w.__momentBook;
    };
  }, [players, debug]);

  // The opening spread's scene was got ready before the book showed; the
  // rest get ready in the background, nearest first, without holding anything up.
  useEffect(() => {
    let live = true;
    const ids = spreads
      .map((s, i) => ({ id: s.id, d: Math.abs(i - first) }))
      .filter(({ id, d }) => d > 0 && scenes[id])
      .sort((a, b) => a.d - b.d)
      .map(({ id }) => id);
    (async () => {
      for (const id of ids) {
        if (!live) return;
        try {
          await preloadScene(scenes[id]);
        } catch (e) {
          console.warn(`[book] ${id}: the scene did not get ready ahead`, e);
        }
      }
    })();
    return () => {
      live = false;
    };
  }, [spreads, scenes, first]);

  const stageReady = useCallback((id: string) => {
    readyIds.current.add(id);
    if (openRef.current === id) markStageReady(true);
  }, []);
  const stageGone = useCallback((id: string) => {
    readyIds.current.delete(id);
  }, []);

  const momentCount = useMemo(() => spreads.filter((s) => s.kind !== "end").length, [spreads]);
  const onTelling = useCallback(
    (spreadId: string, tellerId: string) => {
      players.tell(spreadId, tellerId);
      // A shared link hears the same telling; in a book of several moments, on the same spread.
      const i = spreads.findIndex((s) => s.id === spreadId);
      setQuery({ narrator: tellerId, ...(momentCount > 1 && i >= 0 ? { spread: String(i + 1) } : {}) });
    },
    [players, spreads, momentCount],
  );

  const hasScenes = useMemo(() => spreads.some((s) => s.stage && scenes[s.id]), [spreads, scenes]);
  const renderStage = useCallback(
    (args: StageSlotArgs) => {
      const scene = scenes[args.spread.id];
      return scene && args.spread.stage ? (
        <SpreadStage players={players} scene={scene} args={args} freezeAt={freezeAt} onReady={stageReady} onGone={stageGone} />
      ) : null;
    },
    [players, scenes, freezeAt, stageReady, stageGone],
  );

  const goTo = useCallback((n: number) => controls.current?.goTo(n), []);
  const chrome = contents ? <ContentsCard spreads={spreads} view={view} onPick={goTo} /> : null;
  const actions = contents ? <ContentsTab spreads={spreads} view={view} onPick={goTo} /> : null;

  const openScene = openSpread ? scenes[openSpread.id] : undefined;
  return (
    <>
      <Book3D
        spreads={spreads}
        cover={cover}
        initial={initial}
        renderStage={hasScenes ? renderStage : undefined}
        tellings={players}
        onTelling={onTelling}
        still={still}
        debug={debug}
        onView={onView}
        onControls={onControls}
        back={back}
        actions={actions}
      >
        {chrome}
        {note ? <BookNote opened={view.opened}>{note}</BookNote> : null}
      </Book3D>
      {openSpread && openScene && openSpread.stage ? (
        <MomentKeys
          key={openSpread.id}
          controller={players.player(openSpread.id)!}
          title={openSpread.title}
          tellers={openSpread.tellings ?? []}
          tabs={openSpread.stage.pullTabs ?? []}
          onTelling={(id) => onTelling(openSpread.id, id)}
        />
      ) : null}
    </>
  );
}

/**
 * One spread's pop-up: its player, held while the stage stands so the book
 * moving on never lets it go mid-fold.
 */
function SpreadStage({
  players,
  scene,
  args,
  freezeAt,
  onReady,
  onGone,
}: {
  players: ScenePlayers;
  scene: SceneData;
  args: StageSlotArgs;
  freezeAt: number | null;
  onReady: (id: string) => void;
  onGone: (id: string) => void;
}) {
  const id = args.spread.id;
  const controller = players.player(id)!;
  useEffect(() => players.retain(id), [players, id]);
  useEffect(() => () => onGone(id), [onGone, id]);
  const ready = useCallback(() => onReady(id), [onReady, id]);
  return (
    <MomentStage
      controller={controller}
      scene={scene}
      spread={args.spread}
      fonts={args.fonts}
      reveal={args.reveal}
      view={args.view}
      shot={args.shot}
      size={[args.width, args.depth]}
      freezeAt={freezeAt}
      onReady={ready}
    />
  );
}

/**
 * A quiet line on the table: in the header's middle on a wide screen; on a
 * phone at the foot while the book is closed (the contents strip has the
 * top), under the header once it is open (the dots have the foot).
 */
function BookNote({ opened, children }: { opened: boolean; children: ReactNode }) {
  return (
    <p
      className={`pointer-events-auto absolute left-1/2 z-10 w-max max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-full bg-[#140c07]/55 px-4 py-1.5 text-center font-sans text-[0.8rem] text-[#f3e6cf]/75 sm:bottom-auto sm:top-[1.6rem] ${
        opened ? "top-[3.9rem]" : "bottom-[calc(0.9rem+env(safe-area-inset-bottom))]"
      }`}
    >
      {children}
    </p>
  );
}

/**
 * The open moment's controls as plain HTML, for keyboards and screen
 * readers: a button per telling ("Hear Ana's version") and a slider per pull
 * tab. They are hidden until one has focus; then they show as a small paper
 * card in the corner. The 3D tab follows the slider (PullTabLever reads the
 * held value), and leaving the slider lets go, as releasing the tab does.
 */
export function MomentKeys({
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

/* ------------------------------------------------------------------------ */
/* The routes: their query, what they show, and the sheets                  */
/* ------------------------------------------------------------------------ */

export type Search = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

/** -1|left, 1|right; anything else is the whole spread. */
function readFocus(v: string | undefined): -1 | 0 | 1 {
  if (v === "-1" || v === "left") return -1;
  if (v === "1" || v === "right") return 1;
  return 0;
}

function readTime(v: string | undefined): number | null {
  if (v === undefined || v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

export interface BookQuery {
  /** ?state=closed|open, ?spread=<n>, ?turn=<0..1>, ?focus=left|right. */
  initial: BookInitial;
  /** ?narrator=<person id or name>: the opening spread starts with that telling. */
  narrator: string | null;
  /** ?t=<seconds>: hold each scene there once it opens, instead of playing. */
  freezeAt: number | null;
  /** ?still=1: no parallax, peels or hints. */
  still: boolean;
  /** ?debug=1: window.__book and window.__moment. */
  debug: boolean;
  /** ?family=<fid>. */
  family: string | null;
  /** ?demo=1. */
  demo: boolean;
}

/**
 * The query both book routes take, for deterministic views and tests:
 * ?state, ?spread, ?turn, ?focus, ?still, ?debug, ?narrator, ?t, and the
 * family scope (?family, ?demo).
 */
export function readBookQuery(q: Search): BookQuery {
  const state = one(q.state);
  const spread = Number(one(q.spread));
  const turn = Number(one(q.turn));
  const demo = one(q.demo);
  return {
    initial: {
      state: state === "open" || state === "closed" ? state : undefined,
      spread: Number.isFinite(spread) && spread > 0 ? spread : undefined,
      turn: one(q.turn) != null && Number.isFinite(turn) ? turn : null,
      focus: readFocus(one(q.focus)),
    },
    narrator: one(q.narrator)?.trim() || null,
    freezeAt: readTime(one(q.t)),
    still: one(q.still) === "1",
    debug: one(q.debug) === "1",
    family: one(q.family)?.trim() || null,
    demo: demo !== undefined && demo !== "0",
  };
}

/** Why the demo family is showing: chosen (?demo=1), mock mode, or Stitch out of reach. */
export type DemoWhy = "chosen" | "mock" | "offline";

/** What a book route shows once it has loaded. */
export type BookShown =
  | {
      kind: "book";
      /** Remounts the book when it is a different one. */
      key: string;
      spreads: BookSpread[];
      cover: BookCover;
      scenes: Readonly<Record<string, SceneData>>;
      /** Set when this is the demo family's. */
      demo: DemoWhy | null;
    }
  /** A person with no moments yet. */
  | { kind: "empty"; name: string; familyId: string | null; demo: boolean }
  | { kind: "signed-out" }
  | { kind: "no-family" }
  | { kind: "not-found" }
  | { kind: "error"; detail: string };

/**
 * Get the scene of the spread the book opens at ready before the book shows
 * (its art and puppets built in idle time); the rest follow in the
 * background (MomentBook). A failure only means it builds as it mounts.
 */
export async function preloadOpening(shown: BookShown, initial: BookInitial): Promise<void> {
  if (shown.kind !== "book") return;
  const spread = shown.spreads[startSpread(initial, shown.spreads.length) - 1];
  const scene = spread && shown.scenes[spread.id];
  if (!scene) return;
  try {
    await preloadScene(scene);
  } catch (e) {
    console.warn(`[book] ${spread.id}: the scene did not get ready ahead`, e);
  }
}

const TREE = { href: "/", label: "Family tree" } as const;
const DEMO_TREE = { href: "/?demo=1", label: "Family tree" } as const;
const DEMO_BOOK = "/people/david?demo=1";

/** This page's path and query, to come back to after signing in (the demo switch left out). */
function here(): string {
  if (typeof window === "undefined") return "/";
  const url = new URL(window.location.href);
  url.searchParams.delete("demo");
  return `${url.pathname}${url.search}`;
}

function DemoLink() {
  return (
    <p className="mt-5 text-[0.95rem] text-ink-faint">
      or{" "}
      <Link href={DEMO_BOOK} className="font-semibold text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2">
        explore the demo family
      </Link>
    </p>
  );
}

function DemoNote({ why, onRetry }: { why: DemoWhy; onRetry: () => void }) {
  if (why === "mock") return <>a demo family &middot; mock mode</>;
  if (why === "offline") {
    return (
      <>
        Stitch is out of reach, so this is a demo book &middot;{" "}
        <button type="button" onClick={onRetry} className="cursor-pointer text-[#f0b49a] underline decoration-1 underline-offset-[0.2em]">
          try again
        </button>
      </>
    );
  }
  return (
    <>
      a demo family &middot;{" "}
      <Link href={hrefs.login("/")} className="text-[#f0b49a] underline decoration-1 underline-offset-[0.2em]">
        sign in to see yours
      </Link>
    </>
  );
}

/**
 * A book route's screen: the book, or the sheet for why there is none.
 * `contents` gives a person's book its table of contents; `onDemo` is
 * called when the reader asks for the demo from a sheet.
 */
export function BookScreen({
  shown,
  query,
  retry,
  contents,
}: {
  shown: BookShown | null;
  query: BookQuery;
  retry: () => void;
  contents: boolean;
}) {
  if (!shown) return <BookLoading />;
  const back = query.demo ? DEMO_TREE : TREE;
  switch (shown.kind) {
    case "book":
      return (
        <MomentBook
          key={shown.key}
          spreads={shown.spreads}
          cover={shown.cover}
          scenes={shown.scenes}
          initial={query.initial}
          narrator={query.narrator}
          freezeAt={query.freezeAt}
          still={query.still}
          debug={query.debug}
          back={shown.demo === "chosen" || shown.demo === "mock" ? DEMO_TREE : TREE}
          contents={contents}
          note={shown.demo ? <DemoNote why={shown.demo} onRetry={retry} /> : undefined}
        />
      );
    case "empty":
      return (
        <BookSheet kicker={shown.demo ? "A demo family" : undefined} title={`Nothing in ${shown.name}’s book yet`} back={shown.demo ? DEMO_TREE : TREE}>
          <p>When someone tells a moment {shown.name} was part of, it is bound in here, one spread for each.</p>
          <div className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-3">
            {shown.familyId ? <ButtonLink href={hrefs.add({ familyId: shown.familyId })}>Add a memory</ButtonLink> : null}
            <Link href={shown.demo ? DEMO_TREE.href : TREE.href} className="text-accent-deep">
              Back to the family tree
            </Link>
          </div>
        </BookSheet>
      );
    case "signed-out":
      return (
        <BookSheet title="This book is kept for the family" back={back}>
          <p>Every family on Stitch is private. Sign in, and the books of everyone in yours open here.</p>
          <div className="mt-6">
            <ButtonLink href={hrefs.login(here())}>Sign in to open this book</ButtonLink>
          </div>
          <DemoLink />
        </BookSheet>
      );
    case "no-family":
      return (
        <BookSheet title="No family here yet" back={back}>
          <p>Start your family, or join one with an invite link, and everyone&rsquo;s books will gather here.</p>
          <div className="mt-6">
            <ButtonLink href={hrefs.newFamily()}>Start a family</ButtonLink>
          </div>
          <DemoLink />
        </BookSheet>
      );
    case "not-found":
      return (
        <BookSheet kicker={contents ? "A book" : "A moment"} title={<>This page isn&rsquo;t in the book yet</>} back={back}>
          <p>It may belong to another family, or it has not been told yet. Open a book from the family tree.</p>
          <div className="mt-6">
            <ButtonLink href={back.href} variant="secondary">
              Back to the family tree
            </ButtonLink>
          </div>
          <DemoLink />
        </BookSheet>
      );
    case "error":
      return (
        <BookSheet title="The book wouldn’t open" back={back}>
          <p role="alert">{shown.detail}</p>
          <div className="mt-6">
            <Button onClick={retry}>Try again</Button>
          </div>
          <DemoLink />
        </BookSheet>
      );
  }
}

/* ------------------------------------------------------------------------ */
/* Loading, and the sheets for everything that is not a book                 */
/* ------------------------------------------------------------------------ */

const TABLE_BG = "#1a110b";

export function BookLoading() {
  return (
    <div className="grid h-[100dvh] place-items-center" style={{ background: TABLE_BG }}>
      <p role="status" className="font-hand text-2xl text-[#f3e6cf]/60">
        opening the book&hellip;
      </p>
    </div>
  );
}

/**
 * A sheet of paper on the table, for a book that cannot open: signed out,
 * no family yet, not in the book, an error, nothing in it yet. The way back
 * to the tree stays in the corner.
 */
export function BookSheet({
  kicker,
  title,
  back,
  children,
}: {
  kicker?: ReactNode;
  title: ReactNode;
  back: { href: string; label: string };
  children: ReactNode;
}) {
  return (
    <div className="relative min-h-[100dvh] overflow-hidden" style={{ background: TABLE_BG }}>
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(70% 60% at 50% 42%, rgb(255 214 163 / 0.14), transparent 70%), radial-gradient(115% 95% at 50% 46%, transparent 55%, rgb(12 7 4 / 0.6) 100%)",
        }}
      />
      <Link
        href={back.href}
        className="absolute left-4 top-4 z-10 font-display text-lg tracking-tight text-[#f3e6cf]/80 no-underline transition-colors hover:text-[#f3e6cf] sm:left-6 sm:top-5"
      >
        <span aria-hidden className="mr-1.5 inline-block -translate-y-px">
          &larr;
        </span>
        {back.label}
      </Link>
      <div className="relative flex min-h-[100dvh] items-center justify-center px-4 py-20">
        <section className="relative w-full max-w-md -rotate-[0.6deg] rounded-paper border border-rule bg-paper-light p-6 text-ink shadow-[0_2px_3px_rgb(0_0_0/0.3),0_24px_48px_-16px_rgb(0_0_0/0.75)] sm:p-8">
          {kicker ? <p className="kicker break-words">{kicker}</p> : null}
          <h1
            className="mt-2 text-[1.7rem] leading-tight sm:text-[2rem]"
            style={{ fontVariationSettings: '"SOFT" 100, "WONK" 1' }}
          >
            {title}
          </h1>
          <div className="mt-4 text-ink-soft">{children}</div>
        </section>
      </div>
    </div>
  );
}

/**
 * Load a book for a route: `run` resolves it (never rejecting except on
 * abort); null while it runs. A new `key` loads afresh and abandons the
 * load under way; `retry` loads again. When the page comes back from being
 * hidden (an Activity), a result `keep` approves stays as it is; any other
 * (a sheet asking to sign in, say) is asked again.
 */
export function useBookLoad<T>(
  key: string,
  run: (signal: AbortSignal) => Promise<T>,
  keep: (value: T) => boolean,
): { value: T | null; retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const full = `${attempt}:${key}`;
  const [result, setResult] = useState<{ key: string; value: T } | null>(null);
  const latest = useRef({ run, keep, result });
  useEffect(() => {
    latest.current = { run, keep, result };
  });
  useEffect(() => {
    const { run: go, keep: kept, result: had } = latest.current;
    if (had && had.key === full && kept(had.value)) return;
    const ctl = new AbortController();
    go(ctl.signal).then(
      (value) => {
        if (!ctl.signal.aborted) setResult({ key: full, value });
      },
      (e: unknown) => {
        if (!ctl.signal.aborted) console.warn("[book] the load failed", e);
      },
    );
    return () => ctl.abort();
  }, [full]);
  const retry = useCallback(() => setAttempt((a) => a + 1), []);
  return { value: result && result.key === full ? result.value : null, retry };
}
