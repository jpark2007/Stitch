"use client";

// The writing desk shared by the "add" pages (add a memory, add a tradition):
// the writing on the left and, from 1024px, a book lying open on a dark table
// to the right, the page being written showing in it as it is written.
//
//   <BookDesk mode="write" label="Your book" book={<BookPanel caption={…}>{book3d}</BookPanel>}>
//     <FlowPage …>…form…</FlowPage>
//   </BookDesk>
//
// - BookDesk: the two columns (the book's half sticky, full height), and the
//   book's larger share once the page is being made ("run"). Narrower than
//   1024px the page is one column and the desk mounts no book at all, so a
//   resize across the line never leaves two canvases.
// - BookPanel: the box the book lies in, beside the page or (on a phone, while
//   /add's page is made) as a small still picture in it. Book3D sizes itself
//   to the window; in the box it fills the box. Its canvas is hidden from
//   assistive tech (the caption under it says what it shows), and its window
//   key handlers (arrows, PageUp/PageDown, Space, Enter, Escape turn and open
//   the pages) are kept off this page, so those keys scroll and press as
//   anywhere else.
// - useDebounced, usePrefersReducedMotion: the book reprints a while after
//   the typing stops, and moves the camera never under reduced motion.
//
// Both books (/add's CraftingBook, /traditions/new's TraditionDraftBook) lie
// on this desk and in this panel; photoPrints.ts prints their photos.
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";

/** The table the book lies on. */
export const TABLE_BG = "#1a110b";

/** From this width the book lies beside the writing. Matches Tailwind's `lg`. */
const WIDE = "(min-width: 1024px)";

const subscribeWide = (cb: () => void) => {
  const m = window.matchMedia(WIDE);
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
};

/** True when the book lies beside the writing (false while prerendering). */
export function useWideDesk(): boolean {
  return useSyncExternalStore(subscribeWide, () => window.matchMedia(WIDE).matches, () => false);
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

/**
 * The latest value, `ms` after it last changed (at once when `ms` is 0).
 * A change of `flush` shows the value at once: a click (a kind picked, a
 * person ticked) reprints straight away, typing waits for a pause.
 */
export function useDebounced<T>(value: T, ms: number, flush?: unknown): T {
  const [shown, setShown] = useState({ value, flush });
  const now = ms <= 0 || !Object.is(shown.flush, flush);
  if (now && (!Object.is(shown.value, value) || !Object.is(shown.flush, flush))) setShown({ value, flush });
  useEffect(() => {
    if (ms <= 0) return;
    const t = window.setTimeout(() => setShown((s) => (Object.is(s.value, value) ? s : { value, flush: s.flush })), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return now ? value : shown.value;
}

const TABLE = {
  background: `radial-gradient(80% 55% at 50% 46%, rgb(255 214 163 / 0.07), transparent 70%), ${TABLE_BG}`,
} as const;

export function BookDesk(props: {
  /** "write": the writing and the book side by side; "run": the book takes more of the desk. */
  mode: "write" | "run";
  /** The book, shown beside the page on a wide window; null while the page loads. */
  book: ReactNode;
  /** The aside's name for assistive tech ("Your book", "The book of traditions"). */
  label: string;
  /** Said on the table while the page loads (null: nothing). */
  loading?: ReactNode;
  children: ReactNode;
}) {
  const { mode, book, label, loading = "opening your book…", children } = props;
  const wide = useWideDesk();
  return (
    <div
      className={`lg:grid lg:min-h-dvh lg:transition-[grid-template-columns] lg:duration-700 lg:ease-paper motion-reduce:transition-none ${
        mode === "run" ? "lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]" : "lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
      }`}
    >
      <div className="min-w-0">{children}</div>
      {wide ? (
        <aside aria-label={label} className="sticky top-0 hidden h-dvh min-w-0 overflow-hidden lg:flex lg:items-center" style={TABLE}>
          <div className="mx-auto flex h-full w-full max-w-[min(100%,calc((100dvh-9rem)*1.05))] items-center px-2">
            {book ?? <p className="w-full text-center font-hand text-2xl text-[#f3e6cf]/50">{loading}</p>}
          </div>
        </aside>
      ) : null}
    </div>
  );
}

/** A quiet line on the table while a book is brought out. */
export function TableNote({ children }: { children: ReactNode }) {
  return (
    <div className="grid h-full w-full place-items-center" style={{ background: TABLE_BG }}>
      <p className="font-hand text-2xl text-[#f3e6cf]/55">{children}</p>
    </div>
  );
}

/** The keys Book3D's window listener turns and opens its pages with (Book3D.tsx, onKey). */
export const BOOK_KEYS: ReadonlySet<string> = new Set(["ArrowRight", "ArrowLeft", "PageDown", "PageUp", "Escape", "Enter", " "]);

/**
 * The page's keys stay the page's. Book3D listens on the window for the
 * arrows, PageUp/PageDown, Space, Enter and Escape and cancels their default
 * action; beside a form they must scroll the page and press the focused
 * button as anywhere else. This listener runs after React's own (at its
 * root) and before the window's, so it stops only the book's.
 */
export function useKeepPageKeys(): void {
  useEffect(() => {
    const keep = (e: KeyboardEvent) => {
      if (BOOK_KEYS.has(e.key)) e.stopPropagation();
    };
    document.addEventListener("keydown", keep);
    return () => document.removeEventListener("keydown", keep);
  }, []);
}

/**
 * The book in its box beside the page: a square of table (no taller than the
 * window leaves room for), the book filling it, the table running on past
 * its edges, and one handwritten line under it. As a "preview" (a phone) it
 * is a smaller picture with nothing in it to reach or press.
 */
export function BookPanel(props: {
  /**
   * Under the book: what is happening, in hand. While `live`, it is the
   * screen reader's news too (a polite live region, there from the start so
   * a change is heard); the book itself is hidden from assistive tech.
   */
  caption: ReactNode;
  /** The caption is a live region (default true); false for a hint that only sits there. */
  live?: boolean;
  /** Dims the caption like a hint (true while writing). */
  quiet?: boolean;
  /** A smaller plain line under the caption. */
  note?: ReactNode;
  /** Under the caption and note: a way on (/add's "Back to your book"). */
  after?: ReactNode;
  /** "side": beside the page (default). "preview": a small still picture in the page (a phone). */
  placement?: "side" | "preview";
  /** The book plays sound: its mute button shows (else it is hidden, nothing plays). */
  sound?: boolean;
  /** The book. */
  children: ReactNode;
  /** A data-* attribute for tests: what the book is showing ("writing", "saved", a run's stage). */
  stage?: string;
}) {
  const { caption, live = true, quiet = false, note, after, placement = "side", sound = false, children, stage } = props;
  const preview = placement === "preview";
  useKeepPageKeys();

  // The canvas says nothing to assistive tech (the caption does): hide each
  // one the book draws into, as it appears.
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const hide = () =>
      box.querySelectorAll("canvas:not([aria-hidden])").forEach((c) => {
        c.setAttribute("aria-hidden", "true");
        c.setAttribute("tabindex", "-1");
      });
    hide();
    const mo = new MutationObserver(hide);
    mo.observe(box, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, []);

  const size = quiet ? (preview ? "text-xl" : "text-2xl") : preview ? "text-[1.35rem]" : "text-[1.75rem]";
  return (
    <div
      className={`flex w-full flex-col ${preview ? "overflow-hidden rounded-paper shadow-lift" : "h-full justify-center"}`}
      style={preview ? { background: TABLE_BG } : undefined}
      data-book-stage={stage}
    >
      <div
        className={`relative w-full overflow-hidden ${preview ? "aspect-[4/3]" : "aspect-square max-h-[calc(100dvh-9rem)]"}`}
        style={{ background: TABLE_BG }}
      >
        <style href="stitch-book-panel" precedence="default">
          {BOX_CSS}
        </style>
        <div
          ref={boxRef}
          data-book-box={preview ? "preview" : "side"}
          data-book-quiet={sound ? undefined : ""}
          // A preview is a picture: nothing in it to reach or press.
          inert={preview}
          className="absolute inset-0"
        >
          {children}
        </div>
        {/* The table runs on past the picture's edges. */}
        <div aria-hidden className="pointer-events-none absolute inset-0" style={EDGES} />
      </div>
      <div className={`flex flex-col items-center px-6 text-center ${preview ? "min-h-[4.75rem] pt-1 pb-3" : "min-h-[7rem] pt-1"}`}>
        <p
          role={live ? "status" : undefined}
          aria-live={live ? "polite" : undefined}
          className={`font-hand leading-tight text-balance ${size} ${quiet ? "text-[#f3e6cf]/55" : "text-[#f3e6cf]/90"}`}
        >
          {caption}
        </p>
        {note ? <p className="mt-1 text-sm leading-snug text-[#f3e6cf]/65">{note}</p> : null}
        {after}
      </div>
    </div>
  );
}

/*
 * Book3D (and its loading table) size themselves to the viewport
 * (h-[100dvh]); inside the box they fill the box instead. One spread has no
 * pages to dot through, so its dots are left out, and until something plays
 * there is no mute button. A preview (a phone) has nothing to press, and the
 * page scrolls over it.
 */
const BOX_CSS = `
[data-book-box] > div,
[data-book-box] [class~="h-[100dvh]"],
[data-book-box] [class~="min-h-[100dvh]"] { height: 100% !important; min-height: 0 !important; }
[data-book-box] nav[aria-label="Spreads"] { display: none; }
[data-book-box="preview"] { pointer-events: none; }
[data-book-box="preview"] header { display: none; }
[data-book-quiet] header button[aria-label="Mute sound"],
[data-book-quiet] header button[aria-label="Turn sound on"] { display: none; }
`;

const EDGES = {
  background: `linear-gradient(to bottom, ${TABLE_BG} 0%, transparent 9%, transparent 90%, ${TABLE_BG} 100%), linear-gradient(to right, ${TABLE_BG} 0%, transparent 7%, transparent 93%, ${TABLE_BG} 100%)`,
} as const;
