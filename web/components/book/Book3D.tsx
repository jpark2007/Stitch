"use client";

/**
 * The hero object: a handmade scrapbook lying on a wooden table under a warm
 * spotlight. Kraft card covers with a stitched cloth spine, a page block with
 * visible thickness that dips into the gutter, and skinned paper leaves that
 * bend as they turn (Leaf.tsx). The logic lives in controller.ts.
 *
 *   <Book3D spreads={spreads} cover={cover} renderStage={({ spread, reveal, width, depth }) => <Stage … />} />
 *
 * Closed, the book sits on the table; a click opens it. Turn a page by
 * clicking or dragging its outer edge or corner, with the arrow keys, or with
 * the dots. `initial` sets the starting view, or freezes a leaf mid-turn, for
 * deterministic screenshots. While a moment plays, Escape or the paper
 * "Whole book" toggle switches between its scene (the camera follows the
 * timeline) and the whole open book, with the scene playing on.
 */

import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
  type WheelEvent as ReactWheelEvent,
} from "react";
import {
  BoxGeometry,
  BufferAttribute,
  BufferGeometry,
  DoubleSide,
  type Group,
  type Material,
  type Mesh,
  MeshStandardMaterial,
  type PerspectiveCamera,
  Vector2,
  Vector3,
} from "three";
import {
  createCardEdgeMaterial,
  createCardMaterial,
  createPaperMaterial,
  createWoodMaterial,
  getDeckledMask,
  getPaperTextures,
  preloadPaperTextures,
  type DeckledMaskOptions,
} from "@/components/paper/materials";
import { createBlockGeometry } from "./blockGeometry";
import { BOARD_D, BOARD_W, BookController, COVER_PIVOT_Y, HIT_MARGIN, type BookStart, type BookView } from "./controller";
import { Leaf, type LeafTextures } from "./Leaf";
import { BLOCK_T, COVER_T, PAGE_H, PAGE_W } from "./leafShape";
import { PAGE_PX_H, PAGE_PX_W } from "./pageArt";
import { labelUnder } from "./labelArt";
import { isBookControl } from "./controls";
import { BookRuntimeProvider } from "./pagePrint";
import { isMuted, paperLift, setMuted, subscribeMuted, unlockAudio } from "./sound";
import { SceneView, escapeAction, readSceneViewPref, viewSwitch, writeSceneViewPref } from "./sceneView";
import {
  STAGE_SLOT,
  SlotProbe,
  StageSlot,
  buildBookArt,
  drawSpreadStory,
  useBookTextures,
  type BookArt,
  type StageSlotArgs,
} from "./StoryPage";
import { spreadDate, type BookCover, type BookSpread } from "./types";

const LEAF_MASK: DeckledMaskOptions = { aspect: PAGE_W / PAGE_H, style: "deckled", amount: 0.7, seed: 11, resolution: 1024 };
const KRAFT = "#b58c62";
/** Fraction of the deckle mask skipped at the gutter edge. */
const GUTTER_INSET = 0.035;
const TABLE_BG = "#1a110b";
/** useFrame priority of the book's controller: first in every frame (stages tick at -4..-2). */
const BOOK_PRIORITY = -10;
/** useFrame priority of the book's camera: after the stages tick (-4..-2), before default (0) work. */
const CAMERA_PRIORITY = -1;

export interface BookInitial {
  state?: "closed" | "open";
  /** 1-based spread to open at. */
  spread?: number;
  /** Freeze the next leaf mid-turn at this progress (0..1). */
  turn?: number | null;
  /** Start looking closely at one page: -1 left (story), 1 right (stage). */
  focus?: -1 | 0 | 1;
}

export interface Book3DProps {
  spreads: BookSpread[];
  cover: BookCover;
  initial?: BookInitial;
  /** The pop-up stage for a spread, mounted in the right page's slot. */
  renderStage?: (args: StageSlotArgs) => ReactNode;
  /**
   * Which telling is playing on each spread with `tellings` (spread id ->
   * teller id), as a source the book subscribes to: its label is printed as
   * playing, and a change reprints only that story page, with no React
   * render of the book.
   */
  tellings?: TellingsSource;
  /** A teller's label was chosen on a story page. */
  onTelling?: (spreadId: string, tellerId: string) => void;
  /** Screenshots and tests: no parallax, no hover peels, no hints. */
  still?: boolean;
  /** Tests: expose the raw controller as `window.__book`, and stand a probe card in empty stage slots. */
  debug?: boolean;
  /**
   * The book's view (opened, cover settled, the spread it is on), each time
   * it changes: rarely, never per frame. Called from the book's frame loop
   * (first on its first frame), as the book itself hears it.
   */
  onView?: (view: BookView) => void;
  /** The book's controls once it is drawn (null when it goes), for page chrome like a contents card. */
  onControls?: (controls: BookControls | null) => void;
  /** The way out in the top corner; default "Stitch", home. */
  back?: { href: string; label: string };
  /** Quiet controls beside the mute button (a contents tab). */
  actions?: ReactNode;
  /** Page chrome over the table, under the header (a contents card), shown once the book's art is drawn. */
  children?: ReactNode;
}

export type { BookView };

export interface BookControls {
  open(): void;
  next(): void;
  prev(): void;
  /** Page on as a reader would: on a phone, pan to the stage page before turning. */
  step(dir: 1 | -1): void;
  goTo(spread: number): void;
  /** Look closely at one page (-1 left, 1 right) or the whole spread (0). */
  setFocus(side: -1 | 0 | 1): void;
}

function resolveStart(initial: BookInitial | undefined, n: number): BookStart {
  const opened = initial?.state === "open" || (initial?.state !== "closed" && initial?.spread != null);
  const spread = Math.min(Math.max(1, Math.round(initial?.spread ?? 1)), Math.max(1, n));
  const t = initial?.turn;
  const turn = opened && t != null && Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : null;
  const focus = opened && (initial?.focus === -1 || initial?.focus === 1) ? initial.focus : 0;
  return { opened, spread, turn, focus };
}

/* ------------------------------------------------------------------------ */
/* Shell: loading, overlay UI                                                */
/* ------------------------------------------------------------------------ */

/** Which telling plays on each spread (spread id -> teller id), read when it says it changed. */
export interface TellingsSource {
  get(): Readonly<Record<string, string>>;
  subscribe(listener: () => void): () => void;
}

const NONE: Readonly<Record<string, string>> = {};
const NO_TELLINGS: TellingsSource = { get: () => NONE, subscribe: () => () => {} };

export function Book3D({
  spreads,
  cover,
  initial,
  renderStage,
  tellings = NO_TELLINGS,
  onTelling,
  still = false,
  debug = false,
  onView,
  onControls: onControlsProp,
  back,
  actions,
  children,
}: Book3DProps) {
  // The starting view is read once; later prop changes do not reset the book.
  const [start] = useState(() => resolveStart(initial, spreads.length));
  const [art, setArt] = useState<BookArt | null>(null);
  const [view, setView] = useState<BookView>({ opened: start.opened, ready: start.opened, spread: start.spread });
  const controls = useRef<BookControls | null>(null);
  const onControlsRef = useRef(onControlsProp);
  useEffect(() => {
    onControlsRef.current = onControlsProp;
  }, [onControlsProp]);
  const onControls = useCallback((api: BookControls | null) => {
    controls.current = api;
    onControlsRef.current?.(api);
  }, []);
  // The page hears the view as the book does (a change of spread, the cover
  // settling), in the same call, not an effect later: a slow frame loop can
  // hold effects back for seconds.
  const onViewRef = useRef(onView);
  useEffect(() => {
    onViewRef.current = onView;
  }, [onView]);
  const changeView = useCallback((v: BookView) => {
    setView(v);
    onViewRef.current?.(v);
  }, []);
  const muted = useSyncExternalStore(subscribeMuted, isMuted, () => false);
  // Scene or whole-book view while a moment plays; the reader's last choice, kept per browser.
  const [sceneView] = useState(() => new SceneView(readSceneViewPref(), writeSceneViewPref));

  // The art is drawn with the tellings of the moment it is built; later
  // changes reprint single pages (BookModel).
  const tellingsNow = useRef(tellings);
  useEffect(() => {
    tellingsNow.current = tellings;
  }, [tellings]);
  const onTellingRef = useRef(onTelling);
  useEffect(() => {
    onTellingRef.current = onTelling;
  }, [onTelling]);
  const chooseTelling = useCallback((spreadId: string, tellerId: string) => onTellingRef.current?.(spreadId, tellerId), []);

  useEffect(() => {
    let live = true;
    Promise.all([
      buildBookArt(spreads, cover, tellingsNow.current.get()),
      preloadPaperTextures({ kinds: ["paper", "card"], wood: true, masks: [LEAF_MASK] }),
    ]).then(([a]) => {
      if (live) setArt(a);
    });
    return () => {
      live = false;
    };
  }, [spreads, cover]);

  useEffect(() => {
    // Start audio on the first gesture, before that gesture's turn begins.
    const unlock = () => {
      unlockAudio();
      window.removeEventListener("pointerdown", unlock, true);
      window.removeEventListener("keydown", unlock, true);
    };
    window.addEventListener("pointerdown", unlock, true);
    window.addEventListener("keydown", unlock, true);
    return () => {
      window.removeEventListener("pointerdown", unlock, true);
      window.removeEventListener("keydown", unlock, true);
    };
  }, []);

  // Wheel or trackpad: one step per gesture (the lock lifts once the stream,
  // inertia included, pauses).
  const wheel = useRef({ acc: 0, locked: false, timer: 0 });
  const onWheel = (e: ReactWheelEvent) => {
    const w = wheel.current;
    window.clearTimeout(w.timer);
    w.timer = window.setTimeout(() => {
      w.locked = false;
      w.acc = 0;
    }, 220);
    if (w.locked) return;
    w.acc += Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    if (Math.abs(w.acc) < 50) return;
    controls.current?.step(w.acc > 0 ? 1 : -1);
    w.locked = true;
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && e.target.closest("input, textarea, select")) return;
      const c = controls.current;
      if (!c) return;
      if (e.key === "ArrowRight" || e.key === "PageDown") c.step(1);
      else if (e.key === "ArrowLeft" || e.key === "PageUp") c.step(-1);
      else if (e.key === "Escape") {
        // An open explain note or contents list takes Escape first (capture phase).
        if (e.defaultPrevented) return;
        if (escapeAction(sceneView.available) === "toggle") sceneView.toggle();
        else c.setFocus(0);
      } else if ((e.key === "Enter" || e.key === " ") && e.target === document.body) c.open();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sceneView]);

  return (
    <div
      className="relative h-[100dvh] w-full touch-none overflow-hidden select-none"
      style={{ background: TABLE_BG }}
      onWheel={onWheel}
    >
      {art ? (
        <motion.div
          className="absolute inset-0"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
        >
          <Canvas
            shadows="percentage"
            dpr={[1, 1.75]}
            frameloop="demand"
            camera={{ fov: 30, near: 0.4, far: 40, position: [0, 3, 3] }}
            gl={{ antialias: true, powerPreference: "high-performance" }}
            aria-label="A handmade storybook on a wooden table"
          >
            <BookScene
              art={art}
              spreads={spreads}
              start={start}
              onControls={onControls}
              onView={changeView}
              renderStage={renderStage ?? (debug ? SlotProbe : undefined)}
              tellings={tellings}
              onTelling={chooseTelling}
              still={still}
              debug={debug}
              sceneView={sceneView}
            />
          </Canvas>
        </motion.div>
      ) : (
        // The pages are being printed: the same quiet line as the route's loading table.
        <p role="status" className="absolute inset-0 grid place-items-center font-hand text-2xl text-[#f3e6cf]/60">
          opening the book&hellip;
        </p>
      )}

      {/* Lens: a warm vignette. Depth of field is faked in the scene (fog and
          the lamp's falloff); a CSS backdrop blur here cost frames mid-turn. */}
      <div aria-hidden className="pointer-events-none absolute inset-0" style={VIGNETTE} />

      <header className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between px-4 pt-4 sm:px-6 sm:pt-5">
        <Link
          href={back?.href ?? "/"}
          className="pointer-events-auto font-display text-lg tracking-tight text-[#f3e6cf]/80 no-underline transition-colors hover:text-[#f3e6cf]"
        >
          {back ? (
            <>
              <span aria-hidden className="mr-1.5 inline-block -translate-y-px">
                &larr;
              </span>
              {back.label}
            </>
          ) : (
            "Stitch"
          )}
        </Link>
        <div className="flex items-center gap-3">
          <SceneViewToggle view={sceneView} crowded={actions != null || children != null} />
          {actions}
          <MuteButton muted={muted} />
        </div>
      </header>
      {/* Page chrome lies on the table with the book, not before it. */}
      {art ? children : null}

      <NavDots spreads={spreads} view={view} onPick={(i) => controls.current?.goTo(i)} />

      <motion.p
        className="pointer-events-none absolute inset-x-0 bottom-[9vh] text-center font-hand text-2xl text-[#f3e6cf]/75"
        initial={false}
        animate={{ opacity: art && !view.opened ? 1 : 0, y: art && !view.opened ? 0 : 8 }}
        transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
      >
        <OpenHintText />
      </motion.p>

      {/* Once, after the reader opens the book themselves. */}
      <TurnHint view={view} enabled={!start.opened && !still} />
    </div>
  );
}

const HINT_KEY = "stitch:turn-hint-seen";

function hintSeen() {
  try {
    return localStorage.getItem(HINT_KEY) === "1";
  } catch {
    return false;
  }
}

const subscribeCoarse = (cb: () => void) => {
  const m = window.matchMedia("(pointer: coarse)");
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
};
const isCoarse = () => window.matchMedia("(pointer: coarse)").matches;

function OpenHintText() {
  const coarse = useSyncExternalStore(subscribeCoarse, isCoarse, () => false);
  return coarse ? "tap the book to open it" : "click the book to open it";
}

/** A brief line on how to turn, shown once the cover has opened, then never again. */
function TurnHint({ view, enabled }: { view: BookView; enabled: boolean }) {
  const [done, setDone] = useState(hintSeen);
  const coarse = useSyncExternalStore(subscribeCoarse, isCoarse, () => false);
  const show = enabled && !done && view.ready && view.spread === 1;
  useEffect(() => {
    if (!show) return;
    const t = window.setTimeout(() => {
      setDone(true);
      try {
        localStorage.setItem(HINT_KEY, "1");
      } catch {
        // Private mode: it shows again next visit.
      }
    }, 5200);
    return () => window.clearTimeout(t);
  }, [show]);
  return (
    <motion.p
      aria-hidden={!show}
      className="pointer-events-none absolute inset-x-0 bottom-[calc(4.75rem+env(safe-area-inset-bottom))] text-center font-hand text-xl text-[#f3e6cf]/80 sm:text-2xl"
      initial={false}
      animate={{ opacity: show ? 1 : 0, y: show ? 0 : 6 }}
      transition={{ duration: 0.7, delay: show ? 0.35 : 0, ease: [0.22, 1, 0.36, 1] }}
    >
      {coarse ? "swipe, or tap the page edge, to turn" : "click or drag a page corner to turn"}
    </motion.p>
  );
}

/**
 * While a moment plays: a paper button that names the view it goes to.
 * In the cutscene (the camera in the pop-up) it reads "Whole book" and eases
 * out to the whole open book; there it reads "Cutscene" and zooms back into
 * the scene at the timeline's current shot. The scene plays on either way;
 * Escape does the same. Renders nothing on a spread without a playing moment.
 *
 * The name shows beside the icon at every size, unless the page is
 * `crowded`: it puts other chrome in the header (a Contents tab) or on the
 * table under it (a demo note in the header's middle, "Add a memory" beside
 * the way out). Then, below lg, the icon alone says which way, as before.
 */
function SceneViewToggle({ view, crowded }: { view: SceneView; crowded: boolean }) {
  const { mode, available } = useSyncExternalStore(view.subscribe, view.get, view.get);
  const next = viewSwitch(mode);
  const toScene = next.to === "scene";
  return (
    <AnimatePresence initial={false}>
      {available ? (
        <motion.button
          key="scene-view"
          type="button"
          onClick={() => view.choose(next.to)}
          aria-label={next.action}
          aria-keyshortcuts="Escape"
          title={next.title}
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
          className={`pointer-events-auto flex h-11 min-w-11 cursor-pointer items-center justify-center rounded-full font-display text-[0.95rem] whitespace-nowrap shadow-[0_2px_3px_rgb(0_0_0/0.25),0_8px_20px_-6px_rgb(0_0_0/0.5)] outline-offset-2 outline-[#f3e6cf] transition-[background-color,color,scale] duration-200 hover:scale-105 focus-visible:outline-2 active:scale-95 ${
            crowded ? "w-11 lg:w-auto lg:gap-2 lg:px-4" : "gap-1.5 pr-3.5 pl-3 sm:gap-2 sm:px-4"
          } ${toScene ? "bg-[#e3cfab] text-[#4a3c31]" : "bg-[#f5eee1]/95 text-[#5c4e43]"}`}
        >
          {toScene ? (
            /* A magnifier over the page with a plus: zoom back in. */
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <circle cx="10.5" cy="10.5" r="6.5" fill="currentColor" fillOpacity="0.14" />
              <path d="M15.3 15.3L20.5 20.5" strokeWidth="2.2" />
              <path d="M10.5 7.8v5.4M7.8 10.5h5.4" />
            </svg>
          ) : (
            /* An open book: out to the whole of it. */
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" aria-hidden>
              <path
                d="M12 7c-1.9-1.3-4.6-1.8-8.5-1.6v12.4c3.9-.2 6.6.3 8.5 1.6 1.9-1.3 4.6-1.8 8.5-1.6V5.4C16.6 5.2 13.9 5.7 12 7z"
                fill="currentColor"
                fillOpacity="0.08"
              />
              <path d="M12 7v12.4" />
            </svg>
          )}
          <span className={crowded ? "sr-only lg:not-sr-only" : undefined}>{next.label}</span>
        </motion.button>
      ) : null}
    </AnimatePresence>
  );
}

const VIGNETTE = {
  background:
    "radial-gradient(115% 95% at 50% 46%, transparent 52%, rgb(16 9 5 / 0.38) 78%, rgb(12 7 4 / 0.72) 100%)",
} as const;

function MuteButton({ muted }: { muted: boolean }) {
  return (
    <button
      type="button"
      onClick={() => setMuted(!muted)}
      aria-pressed={muted}
      aria-label={muted ? "Turn sound on" : "Mute sound"}
      title={muted ? "Sound off" : "Sound on"}
      className="pointer-events-auto grid size-11 place-items-center rounded-full bg-[#f5eee1]/95 text-[#5c4e43] shadow-[0_2px_3px_rgb(0_0_0/0.25),0_8px_20px_-6px_rgb(0_0_0/0.5)] transition-transform duration-200 hover:scale-105 active:scale-95"
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M4 9.5h3.2L12 5.5v13l-4.8-4H4z" fill="currentColor" fillOpacity="0.14" />
        {muted ? (
          <path d="M16 9.5l5 5M21 9.5l-5 5" />
        ) : (
          <>
            <path d="M15.5 9.2a4 4 0 0 1 0 5.6" />
            <path d="M18.2 6.8a7.4 7.4 0 0 1 0 10.4" />
          </>
        )}
      </svg>
    </button>
  );
}

/** A dot's label when it is a dateLine, not a year: one line, cut with "…". */
const WORDY_LABEL = { maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } as const;

function NavDots({ spreads, view, onPick }: { spreads: BookSpread[]; view: BookView; onPick: (i: number) => void }) {
  return (
    <motion.nav
      aria-label="Spreads"
      className="absolute inset-x-0 bottom-0 flex justify-center pb-[max(1rem,env(safe-area-inset-bottom))]"
      initial={false}
      animate={{ opacity: view.opened ? 1 : 0, y: view.opened ? 0 : 10 }}
      transition={{ duration: 0.6, delay: view.opened ? 0.8 : 0, ease: [0.22, 1, 0.36, 1] }}
      style={{ pointerEvents: view.opened ? "auto" : "none" }}
    >
      <ol
        className="flex max-w-[calc(100vw-1rem)] items-start px-3 pt-1 pb-1.5"
        // A sewn ribbon, not a pill: a strip of dark cloth with a running stitch around its edge,
        // readable on the wood and where the bar crosses the page.
        style={{
          background: "#2a1c13",
          borderRadius: 3,
          outline: "1.5px dashed rgb(243 230 207 / 0.42)",
          outlineOffset: -5,
          boxShadow: "0 1px 0 rgb(255 255 255 / 0.06) inset, 0 2px 4px rgb(0 0 0 / 0.35)",
        }}
      >
        {spreads.map((s, i) => {
          const active = view.spread === i + 1;
          // Its dateLine ("Every Christmas Eve") when it has one, else its year.
          const date = spreadDate(s);
          const dated = date !== "";
          const wordy = s.kind !== "end" && !!s.dateLine?.trim();
          // A long book narrows its dots to fit the screen; past nine, only
          // the open one, the first and the last keep their years.
          const label = s.kind === "end" ? "end" : dated ? date : "·";
          const quiet = spreads.length > 9 && !active && i !== 0 && i !== spreads.length - 1;
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onPick(i + 1)}
                aria-label={s.kind === "end" ? s.title : dated ? `${s.title}, ${date}` : s.title}
                aria-current={active ? "page" : undefined}
                title={wordy ? `${s.title}, ${date}` : s.title}
                className="group flex flex-col items-center gap-[7px] pt-1.5 pb-0.5 outline-offset-2"
                style={{ width: `min(3.5rem, calc((100vw - 3.5rem) / ${spreads.length} - 0.25rem))` }}
              >
                {/* A stitch, not a dot: a short thread, drawn longer and in terracotta on the open spread. */}
                <span
                  className={
                    "block rounded-[1px] transition-all duration-300 " +
                    (active ? "bg-[#d77a5a]" : "bg-[rgb(243_230_207/0.38)] group-hover:bg-[rgb(243_230_207/0.7)]")
                  }
                  style={{ width: active ? 30 : 14, maxWidth: "100%", height: 2 }}
                />
                <span
                  className={
                    "font-display text-[0.8rem] leading-none font-normal tracking-normal transition-colors duration-300" +
                    (s.kind === "end" ? " italic" : "")
                  }
                  style={{
                    color: active ? "#f3e6cf" : "rgb(243 230 207 / 0.55)",
                    fontVariantNumeric: "oldstyle-nums",

                    visibility: quiet ? "hidden" : undefined,
                    // A dateLine is cut to the dot's width; the title tells it whole.
                    ...(wordy ? WORDY_LABEL : null),
                  }}
                >
                  {label}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </motion.nav>
  );
}

/* ------------------------------------------------------------------------ */
/* Scene                                                                     */
/* ------------------------------------------------------------------------ */

interface SceneProps {
  art: BookArt;
  spreads: BookSpread[];
  start: BookStart;
  onControls: (api: BookControls | null) => void;
  onView: (v: BookView) => void;
  renderStage?: (args: StageSlotArgs) => ReactNode;
  tellings: TellingsSource;
  onTelling: (spreadId: string, tellerId: string) => void;
  still: boolean;
  debug: boolean;
  /** The reader's scene or whole-book view (one per book). */
  sceneView: SceneView;
}

function BookScene(props: SceneProps) {
  // Planks about half a page wide; tinted down so the paper is the brightest thing.
  const wood = useMemo(() => {
    const m = createWoodMaterial({ repeat: [11, 11], color: "#b39478" });
    // Calmer relief: the table is a backdrop, not a subject.
    m.normalScale.set(0.45, 0.45);
    return m;
  }, []);
  useEffect(() => () => wood.dispose(), [wood]);
  return (
    <>
      <color attach="background" args={[TABLE_BG]} />
      <fog attach="fog" args={[TABLE_BG, 4, 11]} />
      <hemisphereLight args={["#ffe6c8", "#24160d", 0.26]} />
      {/* Key: a warm lamp high above, a little left, aimed at the spread. Its
          pool is tight, so the table falls off into darkness at the frame's
          edges: the depth-of-field feel without a blur pass. */}
      <spotLight
        castShadow
        color="#ffd6a3"
        intensity={130}
        position={[-1.3, 6.2, 2.1]}
        angle={0.34}
        penumbra={0.95}
        decay={2}
        distance={0}
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.0012}
        shadow-normalBias={0.02}
        shadow-radius={5}
        shadow-camera-near={3}
        shadow-camera-far={10}
      />
      {/* Fill from the reader's side, and a warm rim from behind. */}
      <directionalLight color="#ffe4c4" intensity={0.42} position={[2.2, 2.6, 3.4]} />
      <directionalLight color="#ffc78f" intensity={0.45} position={[0.6, 2.2, -3.5]} />

      <mesh rotation-x={-Math.PI / 2} receiveShadow material={wood}>
        <planeGeometry args={[30, 30]} />
      </mesh>

      <BookModel {...props} />
    </>
  );
}

/* ------------------------------------------------------------------------ */
/* The book                                                                  */
/* ------------------------------------------------------------------------ */

function useBookMaterials(textures: ReturnType<typeof useBookTextures>) {
  const mats = useMemo(() => {
    const card = getPaperTextures("card", [2, 2.6]);
    const grain = getPaperTextures("paper", [2, 2.6]);
    const edge = createCardEdgeMaterial({ color: KRAFT, darken: 0.22 });
    const outside = new MeshStandardMaterial({
      map: textures.coverOutside,
      normalMap: card.normalMap,
      normalScale: new Vector2(0.9, 0.9),
      roughnessMap: card.roughnessMap,
      roughness: 0.92,
    });
    const inside = new MeshStandardMaterial({
      map: textures.endpaper,
      normalMap: grain.normalMap,
      normalScale: new Vector2(0.6, 0.6),
      roughness: 0.9,
    });
    const backOutside = createCardMaterial({ color: KRAFT });
    const blockTop = createPaperMaterial({ color: "#efe5d0", repeat: [2, 2.6], grain: 0.6 });
    const blockEdge = new MeshStandardMaterial({ map: textures.edges, roughness: 1, color: "#f4ead8" });
    const spine = new MeshStandardMaterial({ map: textures.spine, roughness: 0.95, side: DoubleSide });
    const shadow = new MeshStandardMaterial({
      color: "#000000",
      alphaMap: textures.shadow,
      transparent: true,
      opacity: 0.55,
      depthWrite: false,
    });
    const hit = new MeshStandardMaterial({ transparent: true, opacity: 0, depthWrite: false, colorWrite: false });
    return {
      front: [edge, edge, inside, outside, edge, edge] as Material[],
      back: [edge, edge, inside, backOutside, edge, edge] as Material[],
      block: [blockTop, blockEdge] as Material[],
      spine,
      shadow,
      hit,
      all: [edge, outside, inside, backOutside, blockTop, blockEdge, spine, shadow, hit],
    };
  }, [textures]);
  useEffect(() => () => mats.all.forEach((m) => m.dispose()), [mats]);
  return mats;
}

function useBookGeometry() {
  const geo = useMemo(() => {
    const board = new BoxGeometry(BOARD_W, COVER_T, BOARD_D);
    const blocks = { right: createBlockGeometry(1, 3), left: createBlockGeometry(-1, 5) };
    // The spine ribbon: two rows of points along a curve, posed each frame.
    const segs = 14;
    const spine = new BufferGeometry();
    const uv = new Float32Array((segs + 1) * 4);
    const idx: number[] = [];
    for (let i = 0; i <= segs; i++) {
      uv.set([i / segs, 0, i / segs, 1], i * 4);
      if (i < segs) idx.push(i * 2, i * 2 + 2, i * 2 + 1, i * 2 + 1, i * 2 + 2, i * 2 + 3);
    }
    spine.setAttribute("position", new BufferAttribute(new Float32Array((segs + 1) * 6), 3));
    spine.setAttribute("uv", new BufferAttribute(uv, 2));
    spine.setIndex(idx);
    return { board, blocks, spine };
  }, []);
  useEffect(
    () => () => {
      geo.board.dispose();
      geo.blocks.right.geometry.dispose();
      geo.blocks.left.geometry.dispose();
      geo.spine.dispose();
    },
    [geo],
  );
  return geo;
}

function BookModel({ art, spreads, start, onControls, onView, renderStage, tellings, onTelling, still, debug, sceneView }: SceneProps) {
  const camera = useThree((s) => s.camera) as PerspectiveCamera;
  const gl = useThree((s) => s.gl);
  const invalidate = useThree((s) => s.invalidate);
  const textures = useBookTextures(art, gl.capabilities.getMaxAnisotropy());
  const mats = useBookMaterials(textures);
  const geo = useBookGeometry();

  const [stages, setStages] = useState<number[]>(() =>
    start.opened && start.turn == null ? [start.spread - 1] : [],
  );
  const [ctl] = useState(() => new BookController(spreads.length, start, invalidate, onView, setStages, sceneView));
  // The reader switched views: draw, even on an idle canvas.
  useEffect(() => sceneView.subscribe(invalidate), [sceneView, invalidate]);

  // The gutter edge of a page is a fold, not a deckle: shift each side's mask
  // so the gutter samples inside it (front: u = 0 at the gutter, back: u = 1).
  const leafMasks = useMemo(() => {
    const { alphaMap } = getDeckledMask(LEAF_MASK);
    const front = alphaMap.clone();
    front.repeat.set(1 - GUTTER_INSET, 1);
    front.offset.set(GUTTER_INSET, 0);
    const back = alphaMap.clone();
    back.repeat.set(1 - GUTTER_INSET, 1);
    return { front, back };
  }, []);
  useEffect(
    () => () => {
      leafMasks.front.dispose();
      leafMasks.back.dispose();
    },
    [leafMasks],
  );

  const leafTextures = useMemo<LeafTextures[]>(() => {
    // Fine tiling: at coarser scales the grain's soft fields read as smudges.
    const grain = getPaperTextures("paper", [4.5, 5.85]);
    return textures.leaves.map((l) => ({
      front: l.front,
      back: l.back,
      alphaFront: leafMasks.front,
      alphaBack: leafMasks.back,
      normalMap: grain.normalMap,
    }));
  }, [textures, leafMasks]);

  // A telling chosen: reprint that story page (its labels), in place. The
  // book subscribes itself, so this never renders React.
  useEffect(() => {
    let live = true;
    const sync = () => {
      const now = tellings.get();
      spreads.forEach((spread, i) => {
        const want = now[spread.id] ?? null;
        const leaf = art.leaves[i];
        if (!leaf || leaf.active === want) return;
        leaf.active = want;
        drawSpreadStory(spread, i, art.fonts, want).then((story) => {
          if (!live || leaf.active !== want) return;
          leaf.back = story.canvas;
          leaf.labels = story.labels;
          const t = textures.leaves[i].back;
          t.image = story.canvas;
          t.needsUpdate = true;
          invalidate();
        });
      });
    };
    sync();
    const off = tellings.subscribe(sync);
    return () => {
      live = false;
      off();
    };
  }, [tellings, spreads, art, textures, invalidate]);

  useEffect(() => {
    ctl.attach(camera, () => gl.domElement.getBoundingClientRect());
    const api: BookControls = {
      open: () => ctl.openBook(),
      next: () => ctl.next(),
      prev: () => ctl.prev(),
      step: (d) => ctl.step(d),
      goTo: (s) => ctl.goTo(s),
      setFocus: (f) => ctl.setFocus(f),
    };
    onControls(api);
    // Test hook for the self-testing loop (FPS probe, scripted turns).
    const w = window as Window & { __stitchBook?: unknown; __book?: BookController };
    if (debug) w.__book = ctl;
    w.__stitchBook = {
      ...api,
      frames: () => ctl.rt.frames,
      focus: () => ctl.focus,
      /** How far the camera is into the playing stage's shot (0..1). */
      shot: () => ctl.shotWeight,
      /** The reader's view while a moment plays: "scene" or "book" (the whole open spread). */
      view: () => sceneView.mode,
      /** A moment plays on the open spread, so the view can be switched. */
      viewAvailable: () => sceneView.available,
      /** Switch the view, as the toggle does (only while a moment plays); returns whether it changed. */
      setView: (m: "scene" | "book") => sceneView.choose(m),
      leaves: () => ctl.rt.leaves.map((l) => `${l.mode}:${l.p.toFixed(3)}${l.visible ? "" : "(hidden)"}`),
      /** Screen points (CSS px) of the open spread's teller labels, by teller id. */
      labels: () => {
        const j = ctl.settledSpread;
        const leaf = j > 0 ? art.leaves[j - 1] : null;
        const out: Record<string, { x: number; y: number }> = {};
        if (!leaf) return out;
        const r = gl.domElement.getBoundingClientRect();
        const bx = book.current?.position.x ?? 0;
        const at = new Vector3();
        for (const l of leaf.labels) {
          at.set(bx - PAGE_W + ((l.x + l.w / 2) / PAGE_PX_W) * PAGE_W, ctl.leftSurfaceY, ((l.y + l.h / 2) / PAGE_PX_H) * PAGE_H - PAGE_H / 2);
          at.project(camera);
          out[l.id] = { x: r.left + ((at.x + 1) / 2) * r.width, y: r.top + ((1 - at.y) / 2) * r.height };
        }
        return out;
      },
    };
    invalidate();
    return () => {
      onControls(null);
      delete w.__stitchBook;
      delete w.__book;
    };
  }, [ctl, camera, gl, art, onControls, invalidate, debug, sceneView]);

  // Reduced motion and pointer parallax follow the reader's settings live.
  useEffect(() => {
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
    const fine = window.matchMedia("(hover: hover) and (pointer: fine)");
    const sync = () => {
      ctl.setCalm(calm.matches);
      ctl.setParallax(fine.matches && !still);
    };
    ctl.setPeels(!still);
    sync();
    calm.addEventListener("change", sync);
    fine.addEventListener("change", sync);
    const move = (e: PointerEvent) => {
      if (e.pointerType !== "mouse") return;
      const r = gl.domElement.getBoundingClientRect();
      ctl.pointer(((e.clientX - r.left) / r.width) * 2 - 1, ((e.clientY - r.top) / r.height) * 2 - 1);
    };
    window.addEventListener("pointermove", move);
    return () => {
      calm.removeEventListener("change", sync);
      fine.removeEventListener("change", sync);
      window.removeEventListener("pointermove", move);
    };
  }, [ctl, gl, still]);

  const book = useRef<Group>(null);
  const coverPivot = useRef<Group>(null);
  const leftBlock = useRef<Mesh>(null);
  const spine = useRef<Mesh>(null);
  const shadow = useRef<Mesh>(null);
  const stage = useRef<Group>(null);
  const closedHit = useRef<Mesh>(null);

  useFrame((state) => {
    const moving = ctl.frame({
      camera: state.camera as PerspectiveCamera,
      scene: state.scene,
      width: state.size.width,
      height: state.size.height,
      parts: {
        book: book.current,
        coverPivot: coverPivot.current,
        leftBlock: leftBlock.current,
        spine: spine.current,
        shadow: shadow.current,
        stage: stage.current,
        closedHit: closedHit.current,
        blocks: geo.blocks,
        spineGeometry: geo.spine,
      },
    });
    if (moving) state.invalidate();
    // Screenshot hook: the scene has drawn (a still closed book stops at ~2 frames).
    if (ctl.rt.frames === 2) document.documentElement.dataset.bookReady = "1";
    // Before the leaves read the runtime, and before a mounted stage ticks:
    // otherwise the last frame of a settle draws the stacks a frame stale.
  }, BOOK_PRIORITY);

  // The camera, once the stages have ticked and written this frame's shot,
  // and before anything (labels, pull tabs) projects through it.
  useFrame((state) => {
    if (ctl.poseCamera(state.camera as PerspectiveCamera, state.scene)) state.invalidate();
  }, CAMERA_PRIORITY);

  const toBook = (e: ThreeEvent<PointerEvent>) => ({ x: e.point.x - (book.current?.position.x ?? 0), z: e.point.z });
  const setCursor = (c: string) => {
    document.body.style.cursor = c;
  };

  /**
   * The teller label under the pointer on the open spread's story page, if
   * the book lies still. The ray meets the page at its own height, not the
   * pointer target's, so the labels hit where they are drawn.
   */
  const labelAt = (e: ThreeEvent<PointerEvent>): { spread: number; id: string } | null => {
    const j = ctl.settledSpread;
    const leaf = j > 0 ? art.leaves[j - 1] : null;
    if (!leaf || leaf.labels.length === 0) return null;
    const { origin, direction } = e.ray;
    if (Math.abs(direction.y) < 1e-6) return null;
    const k = (ctl.leftSurfaceY - origin.y) / direction.y;
    const x = origin.x + direction.x * k - (book.current?.position.x ?? 0);
    const z = origin.z + direction.z * k;
    // The story page: x from -PAGE_W (outer edge) to 0 (gutter), z down the page.
    const px = ((x + PAGE_W) / PAGE_W) * PAGE_PX_W;
    const py = ((z + PAGE_H / 2) / PAGE_H) * PAGE_PX_H;
    const hit = labelUnder(leaf.labels, px, py);
    return hit ? { spread: j - 1, id: hit.id } : null;
  };

  /** A control on the page (a pull tab) is under the pointer or held: it handles the event. */
  const overControl = (e: ThreeEvent<PointerEvent>) =>
    ctl.rt.controlsHeld > 0 || e.intersections.some((i) => isBookControl(i.object));

  const onLabelDown = (e: ThreeEvent<PointerEvent>, label: { spread: number; id: string }) => {
    e.stopPropagation();
    const id = e.pointerId;
    const x0 = e.clientX;
    const y0 = e.clientY;
    const up = (ev: PointerEvent) => {
      if (ev.pointerId !== id) return;
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      if (ev.type !== "pointerup" || Math.hypot(ev.clientX - x0, ev.clientY - y0) > 10) return;
      paperLift();
      onTelling(spreads[label.spread].id, label.id);
      // Show the stage the new telling plays on: a phone pans to it, a
      // close look at the story page steps back to the spread.
      if (ctl.focus === -1) ctl.setFocus(ctl.onePage ? 1 : 0);
    };
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  };

  const onPageDown = (e: ThreeEvent<PointerEvent>) => {
    if (overControl(e)) return;
    const label = labelAt(e);
    if (label) return onLabelDown(e, label);
    if (!ctl.pageDown(toBook(e), e.clientX, e.clientY)) return;
    e.stopPropagation();
    const id = e.pointerId;
    const move = (ev: PointerEvent) => {
      if (ev.pointerId !== id) return;
      ctl.dragMove(ev.clientX, ev.clientY);
      if (ctl.dragging) setCursor("grabbing");
    };
    const up = (ev: PointerEvent) => {
      if (ev.pointerId !== id) return;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      ctl.dragEnd(ev.clientX, ev.clientY, ev.type === "pointerup");
      setCursor("");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  };


  return (
    <group ref={book}>
      {/* Contact shadow: soft darkening right under the book. */}
      <mesh ref={shadow} rotation-x={-Math.PI / 2} position={[0, 0.0015, 0]} material={mats.shadow} renderOrder={-1}>
        <planeGeometry args={[1, 1]} />
      </mesh>

      {/* Back board (right) and the front board, hinged at the spine. */}
      <mesh position={[BOARD_W / 2, COVER_T / 2, 0]} geometry={geo.board} material={mats.back} castShadow receiveShadow />
      <group ref={coverPivot} position={[0, COVER_PIVOT_Y, 0]}>
        <mesh
          position={[-BOARD_W / 2, COVER_T / 2 - COVER_PIVOT_Y, 0]}
          geometry={geo.board}
          material={mats.front}
          castShadow
          receiveShadow
        />
      </group>
      <mesh ref={spine} geometry={geo.spine} material={mats.spine} castShadow />

      <mesh geometry={geo.blocks.right.geometry} material={mats.block} castShadow receiveShadow />
      <mesh ref={leftBlock} geometry={geo.blocks.left.geometry} material={mats.block} castShadow receiveShadow />

      {leafTextures.map((t, i) => (
        <Leaf key={i} index={i} runtime={ctl.rt} textures={t} />
      ))}

      {/* Pointer targets (invisible). */}
      <mesh
        position={[0, COVER_T + BLOCK_T * 0.6, 0]}
        rotation-x={-Math.PI / 2}
        material={mats.hit}
        onPointerMove={(e) => {
          if (overControl(e)) {
            ctl.pageLeave();
            return;
          }
          if (labelAt(e)) {
            ctl.pageLeave();
            setCursor("pointer");
            return;
          }
          setCursor(ctl.pageMove(toBook(e)));
        }}
        onPointerLeave={() => {
          ctl.pageLeave();
          setCursor("");
        }}
        onPointerDown={onPageDown}
      >
        <planeGeometry args={[2 * (PAGE_W + HIT_MARGIN), PAGE_H + 2 * HIT_MARGIN]} />
      </mesh>
      <mesh
        ref={closedHit}
        position={[BOARD_W / 2, (COVER_T * 2 + BLOCK_T) / 2, 0]}
        material={mats.hit}
        onPointerOver={(e) => {
          if (!ctl.hoverCover(true)) return;
          e.stopPropagation();
          setCursor("pointer");
        }}
        onPointerOut={() => {
          ctl.hoverCover(false);
          setCursor("");
        }}
        onClick={(e) => {
          if (ctl.isOpen) return;
          e.stopPropagation();
          setCursor("");
          ctl.openBook();
        }}
      >
        <boxGeometry args={[BOARD_W, COVER_T * 2 + BLOCK_T + 0.02, BOARD_D]} />
      </mesh>

      <group ref={stage}>
        <BookRuntimeProvider runtime={ctl.rt}>
          {renderStage
            ? stages.map((i) => (
                <StageSlot key={spreads[i].id}>
                  <StageMount
                    render={renderStage}
                    args={{
                      spread: spreads[i],
                      index: i,
                      reveal: ctl.reveals[i],
                      view: ctl.stageView,
                      shot: ctl.shots[i],
                      fonts: art.fonts,
                      width: STAGE_SLOT.width,
                      depth: STAGE_SLOT.depth,
                    }}
                  />
                </StageSlot>
              ))
            : null}
        </BookRuntimeProvider>
      </group>
    </group>
  );
}

/**
 * Each stage renders as its own component, so a `renderStage` that uses
 * hooks keeps them per stage while stages mount and unmount mid-turn.
 */
function StageMount({ render, args }: { render: (args: StageSlotArgs) => ReactNode; args: StageSlotArgs }) {
  return render(args);
}

export default Book3D;
