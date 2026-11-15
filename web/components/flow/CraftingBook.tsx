"use client";

// The teller's own book on /add, lying open at its next blank page.
//
// While they write, the story page (left) fills in as a printed storybook
// page: the year as its heading, the opening lines, a taped card of the
// people who were there and their photos; the stage page (right) is blank
// paper marked where the pop-up will stand. Updates are debounced and the
// book only draws when something changes, so typing never waits on it.
//
// Once the story is sent, the run makes the pop-up on the right page
// (CraftStage), piece by piece as the studio's events arrive, with one plain
// caption under the book that is also the screen reader's progress line.
// The narrator inks the rest of the story onto the left page. When the scene
// is saved, the pieces fold down and the real moment opens in their place
// (MomentBook, as the moment's own page plays it), with the way into the
// page and back to the teller's book.
//
// One WebGL canvas at a time: the crafting book unmounts before the moment's
// book mounts. In mock mode there is no saved scene to play, so the crafted
// page stays.
//
// The box it lies in, its caption and the page's keys are the desk's
// (BookDesk.tsx, shared with the tradition book); its photos print through
// photoPrints.ts.
import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

import type { BookControls } from "@/components/book/Book3D";
import type { BookShown } from "@/components/book/moment/MomentBook";
import { drawPuppetAvatar } from "@/components/book/labelArt";
import { paper } from "@/components/book/paperArt";
import type { StageSlotArgs } from "@/components/book/StoryPage";
import type { BookCover, BookPhoto, BookSpread } from "@/components/book/types";
import { preloadScene } from "@/components/stage/play";

import { BookPanel, TableNote, useDebounced, usePrefersReducedMotion } from "./BookDesk";
import { FOCUS_RING } from "./Button";
import { warmCraftPieces } from "./craftPieces";
import {
  craftScene,
  holdScene,
  inkSteps,
  inkedLines,
  namesLine,
  openingLines,
  personBookHref,
  sentences,
  type CastMember,
  type CraftProp,
  type CraftRun,
  type CraftScene,
  type Watch,
} from "./crafting";
import { usePhotoPrints } from "./photoPrints";

// three.js needs the browser: the books load on the client only.
const Book3D = dynamic(() => import("@/components/book/Book3D").then((m) => m.Book3D), {
  ssr: false,
  loading: () => <TableNote>opening your book&hellip;</TableNote>,
});
const MomentBook = dynamic(() => import("@/components/book/moment/MomentBook").then((m) => m.MomentBook), {
  ssr: false,
  loading: () => <TableNote>opening your page&hellip;</TableNote>,
});
const CraftStage = dynamic(() => import("./CraftStage").then((m) => m.CraftStage), { ssr: false });

/** What the story page holds: from the form while writing, from what was sent once it runs. */
export interface DraftPage {
  title: string;
  year: number | null;
  /** The whole story; the page prints its opening, the narrator inks the rest. */
  text: string;
  /** The puppets: the teller first, then everyone ticked. */
  cast: readonly CastMember[];
  /** Who was ticked under "Who was there?" (their avatars are taped to the page). */
  participantIds: readonly string[];
  photos: readonly File[];
}

export const EMPTY_PAGE: DraftPage = { title: "A new memory", year: null, text: "", cast: [], participantIds: [], photos: [] };

/** What a run hands the book: its state, and the props the prop tap kept. */
export interface CraftInput {
  run: CraftRun;
  props: CraftProp[];
}

export interface CraftingBookProps {
  /** Whose book it is: "Ana's book", and where their book opens. */
  owner: { title: string; personId: string | null; familyId: string | null };
  page: Watch<DraftPage>;
  /** The run, once the story is sent; null while writing. */
  run: {
    watch: Watch<CraftInput | null>;
    /** Started on this page just now (else reattached: the replay is simply there). */
    fresh: boolean;
    /** The family, for loading the saved moment. */
    familyId: string;
    second: boolean;
    /** Loads the saved moment's book (null in mock mode, which has none to play). */
    loadMoment: ((memoryId: string, signal: AbortSignal) => Promise<BookShown>) | null;
  } | null;
  /** "side": the panel beside the page (desktop). "preview": a small still picture in the page (a phone). */
  placement: "side" | "preview";
}

const castCards = new Map<string, string>();

/** A taped card of the people who were there: their paper puppets, side by side. */
function castCard(people: readonly CastMember[]): string | null {
  if (people.length === 0 || typeof document === "undefined") return null;
  const key = people.map((p) => `${p.id}:${p.color}`).join("|");
  const hit = castCards.get(key);
  if (hit) return hit;
  const W = 520;
  const H = 390;
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const ctx = c.getContext("2d");
  if (!ctx) return null;
  paper(ctx, W, H, { seed: 17 + people.length, tone: "#efe6d2" });
  const shown = people.slice(0, 4);
  const h = 300;
  const step = Math.min(118, (W - 60) / shown.length);
  const x0 = W / 2 - ((shown.length - 1) * step) / 2 - (h * 100) / 130 / 2;
  shown.forEach((p, i) => drawPuppetAvatar(ctx, x0 + i * step, 52, h, p.color, i === 0 && shown.length > 1));
  const url = c.toDataURL("image/png");
  castCards.set(key, url);
  return url;
}

/**
 * BookSpread.year is a number printed as text. Until a year is typed the
 * heading is left blank (not "0"): this empty value prints as nothing, and
 * nothing in the book does arithmetic on it. (Requested from the book's
 * owner: an optional year.)
 */
const NO_YEAR = "" as unknown as number;

/** One sentence more every this many ms while the narrator works. */
const INK_STEP_MS = 1400;

export function CraftingBook({ owner, page: pageWatch, run, placement }: CraftingBookProps) {
  const reduced = usePrefersReducedMotion();
  const page = useSyncExternalStore(pageWatch.subscribe, pageWatch.get, pageWatch.get);
  const input = useSyncExternalStore(
    run?.watch.subscribe ?? NO_SUBSCRIBE,
    run?.watch.get ?? NO_INPUT,
    run?.watch.get ?? NO_INPUT,
  );
  const second = run?.second ?? false;
  const teller = page.cast[0]?.name.split(/\s+/)[0] ?? null;

  // The crafting scene, held as it was while a reconnect replays the run.
  const next = useMemo(
    () =>
      run && input
        ? craftScene(input.run, input.props, { cast: page.cast.map((c) => c.name.split(/\s+/)[0]), teller, second })
        : null,
    [run, input, page.cast, teller, second],
  );
  // Held per run: another run on this page never shows the last one's pieces.
  const watch = run?.watch ?? null;
  const [held, setHeld] = useState<{ watch: typeof watch; scene: CraftScene | null }>({ watch, scene: null });
  const before = held.watch === watch ? held.scene : null;
  const scene = next && input ? holdScene(before, next, input.run) : null;
  if (scene !== held.scene || watch !== held.watch) setHeld({ watch, scene });

  // Warm the set's cut-outs in idle time once a run is under way.
  const running = run !== null;
  useEffect(() => (running ? warmCraftPieces() : undefined), [running]);

  // The narrator inks the story in, a sentence at a time; a resumed run (or
  // reduced motion) shows as far as it has got at once.
  const steps = useMemo(() => inkSteps(page.text), [page.text]);
  const opening = useMemo(() => Math.max(1, Math.min(steps, sentences(openingLines(page.text)).length)), [page.text, steps]);
  const ink = scene?.ink ?? 0;
  // Under way: everything but the last line; written: all of it.
  const goal = ink === 2 ? steps : ink === 1 ? Math.max(opening, steps - 1) : opening;
  const [inked, setInked] = useState(0);
  if (ink === 0 && inked !== 0) setInked(0);
  const instantInk = reduced || !run?.fresh;
  useEffect(() => {
    if (ink === 0 || instantInk) return;
    const t = window.setInterval(() => setInked((n) => n + 1), INK_STEP_MS);
    return () => window.clearInterval(t);
  }, [ink, instantInk]);
  const lines = instantInk ? goal : Math.min(goal, opening + inked);

  const text = run ? inkedLines(page.text, lines) : openingLines(page.text);
  const photos = usePhotoPrints(page.photos);
  const ticked = useMemo(
    () => page.cast.filter((c) => page.participantIds.includes(c.id)),
    [page.cast, page.participantIds],
  );
  const card = useMemo(() => castCard(ticked), [ticked]);

  // The page as printed, debounced while typing.
  const content = useMemo(() => {
    const prints: BookPhoto[] = photos.slice(0, card ? 1 : 2).map((p, i) => ({ id: `photo-${i}`, src: p.url, aspect: p.aspect }));
    if (card) prints.push({ id: "cast", src: card, aspect: 4 / 3, caption: namesLine(ticked.map((c) => c.name.split(/\s+/)[0])) });
    return {
      title: page.title,
      year: page.year,
      text,
      photos: prints,
      tellers: ink === 2 && teller ? [teller] : undefined,
      // The pencilled "the pop-up rises here" marks stay until the pop-up
      // itself starts to stand in them.
      plain: Boolean(scene && (scene.layers > 0 || scene.cast)),
    };
  }, [page.title, page.year, text, photos, card, ticked, ink, teller, scene]);
  const sig = JSON.stringify(content);
  const settled = useDebounced(sig, run ? 0 : 450);
  const spreads = useMemo<BookSpread[]>(() => {
    const c = JSON.parse(settled) as typeof content;
    return [
      {
        id: "draft",
        title: c.title,
        year: c.year ?? NO_YEAR,
        text: c.text,
        photos: c.photos,
        tellers: c.tellers,
        stage: c.plain ? {} : undefined,
      },
    ];
  }, [settled]);
  const cover = useMemo<BookCover>(() => ({ title: owner.title }), [owner.title]);

  // The saved moment: loaded once the scene is ready, then the placeholder
  // pieces fold down and the moment's own book takes their place.
  const memoryId = scene?.memoryId ?? null;
  const loadMoment = run?.loadMoment ?? null;
  const [moment, setMoment] = useState<{ id: string; shown: Extract<BookShown, { kind: "book" }> } | null>(null);
  useEffect(() => {
    if (!memoryId || !loadMoment) return;
    const ctl = new AbortController();
    loadMoment(memoryId, ctl.signal)
      .then(async (shown) => {
        if (ctl.signal.aborted || shown.kind !== "book") return;
        const scenes = Object.values(shown.scenes);
        await Promise.all(scenes.map((s) => preloadScene(s).catch(() => undefined)));
        if (!ctl.signal.aborted) setMoment({ id: memoryId, shown });
      })
      .catch(() => {
        // The crafted page stays, with the way into the moment's page.
      });
    return () => ctl.abort();
  }, [memoryId, loadMoment]);
  const ready = moment !== null && moment.id === memoryId;
  const [swapped, setSwapped] = useState<string | null>(null);
  useEffect(() => {
    if (!ready || !memoryId) return;
    const t = window.setTimeout(() => setSwapped(memoryId), reduced || !run?.fresh ? 0 : 1200);
    return () => window.clearTimeout(t);
  }, [ready, memoryId, reduced, run?.fresh]);
  const playing = ready && swapped === memoryId && moment !== null;
  // The moment's book names the tab after the moment; the page has its own name back after.
  const [tabTitle] = useState(() => (typeof document === "undefined" ? null : document.title));
  useEffect(() => {
    if (!playing || !tabTitle) return;
    return () => {
      document.title = tabTitle;
    };
  }, [playing, tabTitle]);

  const renderStage = useCallback(
    (args: StageSlotArgs) =>
      scene ? (
        <CraftStage
          scene={scene}
          cast={page.cast}
          fonts={args.fonts}
          width={args.width}
          depth={args.depth}
          reveal={args.reveal}
          resumed={!run?.fresh}
          reduced={reduced}
          settle={ready}
        />
      ) : null,
    [scene, page.cast, run?.fresh, reduced, ready],
  );

  // The teller's book: plain while there is no moment yet; once the run has
  // saved it, opened at the page just made.
  const bookHref = owner.personId ? personBookHref(owner.personId, owner.familyId, memoryId) : "/";
  const back = useMemo(() => ({ href: bookHref, label: owner.title }), [bookHref, owner.title]);

  // Where the book's camera looks: the whole spread while writing; once the
  // story is sent, the story page while the studio reads it (and later while
  // the narrator inks it), the stage page while the pop-up is made, and the
  // whole book while it waits on the teller and once it is made (or
  // stopped). The camera eases between them on its own. Under reduced
  // motion it never moves: the whole spread, always. Beside the page (the
  // desk) it stays on the whole spread too: the book already fills its side
  // of the window, and the table under it runs on, drawn for this view, into
  // the page's own (BookDesk); a closer look would crop the book at the
  // writing's edge. The small picture on a phone still leans in.
  const look: -1 | 0 | 1 =
    reduced || !scene || placement === "side"
      ? 0
      : scene.waiting || scene.stage === "done" || scene.stage === "stopped"
        ? 0
        : scene.stage === "narration" || scene.stage === "reading" || scene.stage === "opening"
          ? -1
          : 1;
  const [initial] = useState(() => ({ state: "open" as const, spread: 1, focus: look }));
  const [controls, setControls] = useState<BookControls | null>(null);
  useEffect(() => {
    controls?.setFocus(look);
  }, [controls, look]);

  // The page's keys stay the page's (BookPanel keeps the book's window key
  // handlers off them). The made moment's book is held the same way: on this
  // page its two spreads are not worth losing the keyboard's scrolling for,
  // and its tellings and pull tabs keep their own focusable controls.

  // Built only when what it shows changes, so a keystroke that does not
  // reprint the page does not render (or draw) the book at all.
  const book = useMemo(
    () =>
      playing && moment ? (
        <MomentBook
          key={moment.id}
          spreads={moment.shown.spreads}
          cover={moment.shown.cover}
          scenes={moment.shown.scenes}
          initial={MOMENT_OPEN}
          narrator={null}
          freezeAt={null}
          still
          debug={false}
          back={back}
        />
      ) : (
        <Book3D
          spreads={spreads}
          cover={cover}
          initial={initial}
          renderStage={renderStage}
          still
          back={back}
          onControls={setControls}
        />
      ),
    [playing, moment, back, spreads, cover, initial, renderStage],
  );

  const done = scene?.stage === "done" && scene.memoryId;
  return (
    <BookPanel
      placement={placement}
      stage={scene?.stage ?? "writing"}
      // The run's progress line is the screen reader's news; the hint while writing only sits there.
      live={run !== null}
      quiet={run === null}
      caption={run ? (scene ? scene.caption : null) : "your page fills in as you write"}
      note={scene?.note || null}
      // Nothing plays until the made moment does: no mute button till then.
      sound={playing}
      after={
        // The way into the page is the feed's one button; under the book, only the way back to the teller's book.
        done && run ? (
          <Link
            href={bookHref}
            className={`mt-2 inline-flex min-h-11 items-center rounded-paper px-1 text-[0.95rem] font-medium text-[#f3e6cf]/85 underline decoration-1 underline-offset-[0.25em] hover:decoration-2 ${FOCUS_RING}`}
          >
            Back to your book
          </Link>
        ) : null
      }
    >
      {book}
    </BookPanel>
  );
}

/** The made moment opens on its spread; its scene's own camera leans into the pop-up. */
const MOMENT_OPEN = { state: "open", spread: 1 } as const;

const NO_SUBSCRIBE = () => () => {};
const NO_INPUT = () => null;
