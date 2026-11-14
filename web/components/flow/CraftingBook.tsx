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
import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";

import type { BookControls } from "@/components/book/Book3D";
import type { BookShown } from "@/components/book/moment/MomentBook";
import { drawPuppetAvatar } from "@/components/book/labelArt";
import { paper } from "@/components/book/paperArt";
import type { StageSlotArgs } from "@/components/book/StoryPage";
import type { BookCover, BookPhoto, BookSpread } from "@/components/book/types";
import { preloadScene } from "@/components/stage/play";

import { FOCUS_RING } from "./Button";
import { warmCraftPieces } from "./craftPieces";
import {
  craftScene,
  holdScene,
  inkSteps,
  inkedLines,
  namesLine,
  openingLines,
  sentences,
  type CastMember,
  type CraftProp,
  type CraftRun,
  type CraftScene,
  type Watch,
} from "./crafting";

const TABLE_BG = "#1a110b";

/** A quiet line on the table while a book is brought out. */
function TableNote({ children }: { children: ReactNode }) {
  return (
    <div className="grid h-full w-full place-items-center" style={{ background: TABLE_BG }}>
      <p className="font-hand text-2xl text-[#f3e6cf]/55">{children}</p>
    </div>
  );
}

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
  cast: CastMember[];
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
  owner: { title: string; href: string };
  page: Watch<DraftPage>;
  /** The run, once the story is sent; null while writing. */
  run: {
    watch: Watch<CraftInput | null>;
    /** Started on this page just now (else reattached: the replay is simply there). */
    fresh: boolean;
    /** The family, for loading the saved moment. */
    familyId: string;
    second: boolean;
    /** Builds the moment's page link. */
    momentHref: (memoryId: string) => string;
    /** Loads the saved moment's book (null in mock mode, which has none to play). */
    loadMoment: ((memoryId: string, signal: AbortSignal) => Promise<BookShown>) | null;
  } | null;
  /** "side": the panel beside the page (desktop). "preview": a small still picture in the page (a phone). */
  placement: "side" | "preview";
}

const subscribeMotion = (cb: () => void) => {
  const m = window.matchMedia("(prefers-reduced-motion: reduce)");
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
};
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribeMotion, reducedMotion, () => false);
}

/** The latest value, `ms` after it last changed (at once when `ms` is 0). */
function useDebounced<T>(value: T, ms: number): T {
  const [shown, setShown] = useState(value);
  useEffect(() => {
    if (ms <= 0) return;
    const t = window.setTimeout(() => setShown(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return ms <= 0 ? value : shown;
}

/** Longest side of a photo as printed on the page, px: big enough for the print, small enough to redraw fast. */
const PHOTO_PX = 640;
const shrunk = new WeakMap<File, Promise<{ url: string; aspect: number } | null>>();

/** A photo file as a small same-origin image for the page (made once per file, kept for the page's life). */
function shrinkPhoto(file: File): Promise<{ url: string; aspect: number } | null> {
  let hit = shrunk.get(file);
  if (!hit) {
    hit = (async () => {
      try {
        const bitmap = await createImageBitmap(file);
        const s = Math.min(1, PHOTO_PX / Math.max(bitmap.width, bitmap.height));
        const w = Math.max(1, Math.round(bitmap.width * s));
        const h = Math.max(1, Math.round(bitmap.height * s));
        const c = document.createElement("canvas");
        c.width = w;
        c.height = h;
        c.getContext("2d")?.drawImage(bitmap, 0, 0, w, h);
        bitmap.close();
        const blob = await new Promise<Blob | null>((r) => c.toBlob(r, "image/jpeg", 0.85));
        return blob ? { url: URL.createObjectURL(blob), aspect: Math.min(1.6, Math.max(0.7, w / h)) } : null;
      } catch {
        return null;
      }
    })();
    shrunk.set(file, hit);
  }
  return hit;
}

/** The page's prints of the photos, in order (missing ones left out). */
function usePhotoPrints(files: readonly File[]): { url: string; aspect: number }[] {
  const [prints, setPrints] = useState<{ files: readonly File[]; out: { url: string; aspect: number }[] }>({ files: [], out: [] });
  useEffect(() => {
    let live = true;
    Promise.all(files.slice(0, 2).map(shrinkPhoto)).then((all) => {
      if (live) setPrints({ files, out: all.filter((p): p is { url: string; aspect: number } => p !== null) });
    });
    return () => {
      live = false;
    };
  }, [files]);
  return prints.files === files ? prints.out : prints.out.slice(0, files.length);
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
  const [held, setHeld] = useState<CraftScene | null>(null);
  const scene = next && input ? holdScene(held, next, input.run) : null;
  if (scene !== held) setHeld(scene);

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

  const back = useMemo(() => ({ href: owner.href, label: owner.title }), [owner.href, owner.title]);

  // Where the book's camera looks: the whole spread while writing; once the
  // story is sent, the story page while the studio reads it (and later while
  // the narrator inks it), the stage page while the pop-up is made, and the
  // whole book while it waits on the teller and once it is made (or
  // stopped). The camera eases between them on its own.
  const look: -1 | 0 | 1 = !scene
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

  const preview = placement === "preview";
  const book = playing && moment ? (
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
  );

  const caption = scene ? scene.caption : null;
  const done = scene?.stage === "done" && scene.memoryId;
  return (
    <div
      className={`flex w-full flex-col ${preview ? "overflow-hidden rounded-paper shadow-lift" : "h-full justify-center"}`}
      style={preview ? { background: TABLE_BG } : undefined}
      data-crafting-stage={scene?.stage ?? "writing"}
    >
      <div
        className={`relative w-full overflow-hidden ${preview ? "aspect-[4/3]" : "aspect-square max-h-[calc(100dvh-9rem)]"}`}
        style={{ background: TABLE_BG }}
      >
        <style href="stitch-crafting-book" precedence="default">
          {BOX_CSS}
        </style>
        <div data-craft-box={preview ? "preview" : "side"} className="absolute inset-0">
          {book}
        </div>
        {/* The table runs on past the picture's edges. */}
        <div aria-hidden className="pointer-events-none absolute inset-0" style={EDGES} />
      </div>
      <div className={`flex flex-col items-center px-6 text-center ${preview ? "min-h-[4.75rem] pt-1 pb-3" : "min-h-[7rem] pt-1"}`}>
        {run ? (
          <p
            role="status"
            aria-live="polite"
            className={`font-hand leading-tight text-balance text-[#f3e6cf]/90 ${preview ? "text-[1.35rem]" : "text-[1.75rem]"}`}
          >
            {caption}
          </p>
        ) : (
          <p className={`font-hand leading-tight text-[#f3e6cf]/55 ${preview ? "text-xl" : "text-2xl"}`}>
            your page fills in as you write
          </p>
        )}
        {scene?.note ? <p className="mt-1 text-sm leading-snug text-[#f3e6cf]/65">{scene.note}</p> : null}
        {done && run ? (
          <div className="mt-3 flex flex-wrap items-center justify-center gap-x-5 gap-y-1">
            <Link
              href={run.momentHref(scene.memoryId as string)}
              className={`inline-flex min-h-11 items-center gap-1.5 rounded-paper px-1 text-[0.95rem] font-semibold text-[#f0b49a] underline decoration-1 underline-offset-[0.25em] hover:decoration-2 ${FOCUS_RING}`}
            >
              Open the page <span aria-hidden="true">&rarr;</span>
            </Link>
            <Link
              href={owner.href}
              className={`inline-flex min-h-11 items-center rounded-paper px-1 text-[0.95rem] font-medium text-[#f3e6cf]/80 underline decoration-1 underline-offset-[0.25em] hover:decoration-2 ${FOCUS_RING}`}
            >
              Back to your book
            </Link>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/*
 * The book on /add lies in a box on the page, not across the whole window as
 * on the book routes: Book3D (and MomentBook's loading table) size themselves
 * to the viewport (h-[100dvh]), so inside the box they fill the box instead.
 * One spread has no pages to dot through, so its dots are left out. A preview
 * (a phone) has nothing to press, and the page scrolls over it.
 */
const BOX_CSS = `
[data-craft-box] > div,
[data-craft-box] [class~="h-[100dvh]"],
[data-craft-box] [class~="min-h-[100dvh]"] { height: 100% !important; min-height: 0 !important; }
[data-craft-box] nav[aria-label="Spreads"] { display: none; }
[data-craft-box="preview"] { pointer-events: none; }
[data-craft-box="preview"] header { display: none; }
`;

/** The made moment opens on its spread; its scene's own camera leans into the pop-up. */
const MOMENT_OPEN = { state: "open", spread: 1 } as const;

const NO_SUBSCRIBE = () => () => {};
const NO_INPUT = () => null;

const EDGES = {
  background: `linear-gradient(to bottom, ${TABLE_BG} 0%, transparent 9%, transparent 90%, ${TABLE_BG} 100%), linear-gradient(to right, ${TABLE_BG} 0%, transparent 7%, transparent 93%, ${TABLE_BG} 100%)`,
} as const;
