"use client";

// The writing desk shared by the "add" pages (add a memory, add a tradition):
// the whole window is the book's wooden table. The writing lies on it in
// light ink on the left; from 1024px the book lies open on the right, large,
// the page being written showing in it as it is written.
//
//   <BookDesk mode="write" label="Your book" book={<BookPanel caption={…}>{book3d}</BookPanel>}>
//     <FlowPage …>…form…</FlowPage>
//   </BookDesk>
//
// - BookDesk: the two columns (30/70 from 1280px, 40/60 from 1024; the book's
//   side sticky, full height) and the table under both. The book's canvas is
//   wider than its side (deskTable.ts) and runs on under the writing, where
//   the page draws the same table: the same wood, plank for plank where the
//   book's camera puts them, under the same light (DeskTable), so the two are
//   one table. Narrower than 1024px the page is one column on a tiled table
//   (deskStyle.ts) and the desk mounts no book at all, so a resize across the
//   line never leaves two canvases. The look of the writing on the wood
//   (light ink, sunk fields) is deskStyle.ts.
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
import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from "react";

import { deskLayout, tableBands, type DeskLayout } from "./deskTable";
import { DESK_CSS, DESK_VIGNETTE } from "./deskStyle";

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

const subscribeResize = (cb: () => void) => {
  window.addEventListener("resize", cb);
  return () => window.removeEventListener("resize", cb);
};
const windowSize = () => `${window.innerWidth}x${window.innerHeight}`;

/** The window's size as "WxH" (one string, so an unchanged size is the same snapshot). */
function useWindowSize(): string {
  return useSyncExternalStore(subscribeResize, windowSize, () => "0x0");
}

export function BookDesk(props: {
  /**
   * "write" or "run" (the page being made). The desk is the same for both:
   * the book has most of the window from the start.
   */
  mode: "write" | "run";
  /** The book, shown beside the page on a wide window; null while the page loads. */
  book: ReactNode;
  /** The aside's name for assistive tech ("Your book", "The book of traditions"). */
  label: string;
  /** Said on the table while the page loads (null: nothing). */
  loading?: ReactNode;
  children: ReactNode;
}) {
  const { book, label, loading = "opening your book…", children } = props;
  const wide = useWideDesk();
  const size = useWindowSize();
  // Where the book's canvas lies: wider than its area, so the spread fills
  // most of it (deskTable.ts). Its box reads these (BookPanel, "side").
  const layout = useMemo(() => {
    if (!wide) return null;
    const [w, h] = size.split("x").map(Number);
    return w > 0 ? deskLayout(w, h) : null;
  }, [wide, size]);
  const vars = layout
    ? ({
        "--canvas-x": `${layout.canvas.x}px`,
        "--canvas-y": `${layout.canvas.y}px`,
        "--canvas-w": `${layout.canvas.width}px`,
        "--canvas-h": `${layout.canvas.height}px`,
        "--canvas-bleed": `${-layout.canvas.x}px`,
      } as CSSProperties)
    : undefined;
  return (
    <div
      data-desk=""
      style={vars}
      className="lg:grid lg:min-h-dvh lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:overflow-x-clip xl:grid-cols-[minmax(0,3fr)_minmax(0,7fr)]"
    >
      <style href="stitch-desk" precedence="default">
        {DESK_CSS}
      </style>
      {/* The table: the whole window, under everything, still as the page scrolls. */}
      {layout ? <DeskTable layout={layout} /> : <div aria-hidden data-desk-table="" className="pointer-events-none fixed inset-0" />}
      {/* The lens, over the table and the book but under the writing. */}
      {layout ? (
        <div aria-hidden className="pointer-events-none fixed inset-0 z-[1]" style={{ background: DESK_VIGNETTE }} />
      ) : null}
      <div data-desk-writing="" className="relative z-[2] min-w-0">
        {children}
      </div>
      {wide ? (
        <aside aria-label={label} className="sticky top-0 hidden h-dvh min-w-0 lg:block">
          {book ?? (
            <p className="flex h-full w-full items-center justify-center font-hand text-2xl text-[#f3e6cf]/80">
              {loading}
            </p>
          )}
        </aside>
      ) : null}
    </div>
  );
}

/**
 * The book's own table beyond its canvas: the same wood texture, plank by
 * plank where the book's camera puts each one (deskTable.ts), under the same
 * lamp. The canvas lies over it on the right; under the writing it runs on.
 * Drawn once per window size, as still images on a fixed layer: nothing moves
 * or redraws as the page scrolls or the form is typed in.
 */
function DeskTable({ layout }: { layout: DeskLayout }) {
  const wood = useWoodImage();
  const bands = useMemo(() => tableBands(layout, layout.area.height), [layout]);
  const { light, falloff } = useMemo(() => tableLight(layout), [layout]);
  return (
    <div
      aria-hidden
      data-desk-table=""
      className="pointer-events-none fixed inset-0 isolate overflow-hidden"
      style={{ background: TABLE_BG }}
    >
      {wood
        ? bands.map((b) => (
            <div
              key={`${b.top.toFixed(1)}`}
              className="absolute inset-x-0"
              style={{
                top: b.top,
                height: b.height,
                backgroundImage: `url(${wood})`,
                backgroundRepeat: "repeat-x",
                backgroundSize: `${TILE * b.sx}px ${TILE * b.sy}px`,
                backgroundPosition: `${b.x0}px ${-b.row * b.sy}px`,
              }}
            />
          ))
        : null}
      {/* The book's lamp on its wood: the wood's tint and light, far to near, then falling away from the book. */}
      <div className="absolute inset-0 mix-blend-multiply" style={{ background: light }} />
      <div className="absolute inset-0 mix-blend-multiply" style={{ background: falloff }} />
    </div>
  );
}

/** The wood texture's pixel size (paper/materials.ts). */
const TILE = 512;

/**
 * The book's wood (paper/materials.ts getWoodTextures: the same images its
 * table is drawn with), its grain's relief lit from the book's lamp (up and
 * to the left, toward the reader), as one image URL, made once per page.
 */
let woodUrl: Promise<string | null> | null = null;
function useWoodImage(): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    woodUrl ??= import("@/components/paper/materials")
      .then((m) => {
        const set = m.getWoodTextures();
        return litWood(set.map.image as HTMLCanvasElement, set.normalMap.image as HTMLCanvasElement);
      })
      .catch(() => null);
    woodUrl.then((u) => {
      if (live) setUrl(u);
    });
    return () => {
      live = false;
    };
  }, []);
  return url;
}

/**
 * The wood's colour times its relief under the lamp: each texel's normal
 * (the book's normal map, at the table's normal scale 0.45) against the
 * lamp's direction (Book3D: up at (-1.3, 6.2, 2.1) from the spread), a flat
 * texel unchanged. The same 512 px tile; drawn once, off screen.
 */
async function litWood(albedo: HTMLCanvasElement, normals: HTMLCanvasElement): Promise<string | null> {
  const size = albedo.width;
  const c = document.createElement("canvas");
  c.width = size;
  c.height = size;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  ctx.drawImage(normals, 0, 0, size, size);
  const n = ctx.getImageData(0, 0, size, size).data;
  ctx.drawImage(albedo, 0, 0, size, size);
  const img = ctx.getImageData(0, 0, size, size);
  const a = img.data;
  // The lamp in the texture's own frame: u along the table's x, v away from the reader (-z), up out of the wood.
  const len = Math.hypot(1.3, 6.2, 2.1);
  const lu = -1.3 / len;
  const lv = -2.1 / len;
  const lw = 6.2 / len;
  const k = RELIEF;
  const shade = new Float32Array(a.length / 4);
  let sum = 0;
  for (let i = 0, j = 0; i < a.length; i += 4, j++) {
    const nu = ((n[i] / 255) * 2 - 1) * k;
    const nv = ((n[i + 1] / 255) * 2 - 1) * k;
    const nw = (n[i + 2] / 255) * 2 - 1;
    shade[j] = Math.min(1.7, Math.max(0.35, (nu * lu + nv * lv + nw * lw) / (Math.hypot(nu, nv, nw) * lw)));
    sum += shade[j];
  }
  // On average the wood is as light as before: the relief only moves light about.
  const mean = sum / shade.length;
  for (let i = 0, j = 0; i < a.length; i += 4, j++) {
    const s = shade[j] / mean;
    a[i] = Math.min(255, a[i] * s);
    a[i + 1] = Math.min(255, a[i + 1] * s);
    a[i + 2] = Math.min(255, a[i + 2] * s);
  }
  ctx.putImageData(img, 0, 0);
  return new Promise((resolve) => c.toBlob((b) => resolve(b ? URL.createObjectURL(b) : null), "image/png"));
}

/**
 * How strongly the grain's relief shows. Book3D's table has normal scale 0.45,
 * but its sheen and tone mapping lift the grain further: this matches the
 * grain's contrast at the join (measured from the canvas's pixels).
 */
const RELIEF = 1.5;

/**
 * The light the book's table is drawn under (Book3D: its wood tinted
 * #b39478, a warm lamp over the spread, the far table fading into fog), as
 * multiplies over the wood.
 *
 * - `light`: far to near, as the canvas lights the table at its edge beside
 *   the writing. Measured there from the canvas's own pixels (its colour over
 *   the bare wood), by height on the canvas: the same at 1024, 1440 and 1920.
 * - `falloff`: from that edge leftward, the table falls away from the lamp,
 *   a little faster at first, as the canvas's light does toward its edge.
 */
function tableLight(layout: DeskLayout): { light: string; falloff: string } {
  const { area, canvas } = layout;
  const at = (f: number) => `${(canvas.y + f * canvas.height).toFixed(1)}px`;
  const light =
    `linear-gradient(to bottom, ` +
    LIGHT_BY_HEIGHT.map(([f, rgb]) => `rgb(${rgb}) ${at(f)}`).join(", ") +
    `)`;
  const edge = area.x;
  const falloff =
    `linear-gradient(to left, #fff ${(area.x + area.width - edge).toFixed(0)}px, ` +
    `rgb(226 226 226) ${(area.x + area.width - edge + 100).toFixed(0)}px, ` +
    `rgb(200 200 200) ${(area.x + area.width - edge + 250).toFixed(0)}px, ` +
    `rgb(184 184 184) ${(area.x + area.width).toFixed(0)}px)`;
  return { light, falloff };
}

/** The canvas's light on its table beside the writing, by height on the canvas (0 top, 1 bottom). */
const LIGHT_BY_HEIGHT: readonly [number, string][] = [
  [0, "92 42 15"],
  [0.12, "102 47 17"],
  [0.25, "133 69 29"],
  [0.38, "160 91 45"],
  [0.5, "171 104 57"],
  [0.65, "180 112 66"],
  [0.8, "186 122 76"],
  [1, "190 135 90"],
];

/** A quiet line on the table while a book is brought out. */
export function TableNote({ children }: { children: ReactNode }) {
  return (
    <div className="grid h-full w-full place-items-center" style={{ background: TABLE_BG }}>
      <p className="font-hand text-2xl text-[#f3e6cf]/80">{children}</p>
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
 * The book beside the page: its canvas where BookDesk lays it (the spread
 * filling most of the book's side), and one handwritten line under it. As a
 * "preview" (a phone) it is a smaller picture with nothing in it to reach or
 * press.
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
    <div className={`flex w-full flex-col ${preview ? "" : "relative h-full"}`} data-book-stage={stage}>
      <style href="stitch-book-panel" precedence="default">
        {BOX_CSS}
      </style>
      {/*
        Beside the page the canvas is the table itself: wider than the book's
        area (BookDesk's --canvas-*), running on under the writing, where it
        melts into the same table drawn on the page. As a preview it is a
        picture whose edges melt into the wood around it.
      */}
      <div
        data-book-frame={preview ? "preview" : "side"}
        className={`overflow-hidden ${preview ? "relative aspect-[4/3] w-full" : "absolute"}`}
        style={
          preview
            ? undefined
            : { left: "var(--canvas-x)", top: "var(--canvas-y)", width: "var(--canvas-w)", height: "var(--canvas-h)" }
        }
      >
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
      </div>
      <div
        className={`flex flex-col items-center px-6 text-center ${
          preview ? "min-h-[4.75rem] pt-1 pb-3" : "pointer-events-none absolute inset-x-0 bottom-0 pt-10 pb-[5vh] *:pointer-events-auto"
        }`}
        // On the book's lit table the caption sits in a little shade of its own, so it stays legible.
        style={preview ? undefined : { background: CAPTION_SHADE }}
      >
        <p
          role={live ? "status" : undefined}
          aria-live={live ? "polite" : undefined}
          // Light ink on the wood, at least 4.5:1 on its lightest grain; the hint a little quieter.
          className={`font-hand leading-tight text-balance ${size} ${quiet ? "text-[#f3e6cf]/80" : "text-[#f3e6cf]/95"}`}
        >
          {caption}
        </p>
        {note ? <p className="mt-1 text-sm leading-snug text-[#f3e6cf]/85">{note}</p> : null}
        {after}
      </div>
    </div>
  );
}

/** A soft shade behind the caption under the book (not a band: it fades out all round). */
const CAPTION_SHADE = "radial-gradient(42% 62% at 50% 64%, rgb(14 7 3 / 0.55), rgb(14 7 3 / 0.25) 55%, transparent)";

/*
 * Book3D (and its loading table) size themselves to the viewport
 * (h-[100dvh]); inside the box they fill the box instead. One spread has no
 * pages to dot through, so its dots are left out, and until something plays
 * there is no mute button. The book lies open and is not to be clicked: its
 * "click the book to open it" line (faded out, but still read aloud) goes. A
 * preview (a phone) has nothing to press, and the page scrolls over it.
 */
const BOX_CSS = `
[data-book-box] > div,
[data-book-box] [class~="h-[100dvh]"],
[data-book-box] [class~="min-h-[100dvh]"] { height: 100% !important; min-height: 0 !important; }
[data-book-box] nav[aria-label="Spreads"] { display: none; }
[data-book-box] p[class~="bottom-[9vh]"] { display: none; }
[data-book-box="preview"] { pointer-events: none; }
[data-book-box="preview"] header { display: none; }
[data-book-quiet] header button[aria-label="Mute sound"],
[data-book-quiet] header button[aria-label="Turn sound on"] { display: none; }

/* The book's own lens vignette would frame just its canvas: the desk has one over the whole window. */
[data-book-box] > div > div[aria-hidden="true"][style*="radial-gradient"] { display: none; }
/* Until the book has drawn, its box shows the page's table (the same table), not a dark square. */
[data-book-frame="side"] [data-book-box] [style*="background: rgb(26, 17, 11)"],
[data-book-frame="side"] [data-book-box] [style*="background-color: rgb(26, 17, 11)"] { background: transparent !important; }
/* The canvas runs past its area on both sides: the header's links and buttons stay inside it. */
[data-book-frame="side"] [data-book-box] header { left: var(--canvas-bleed) !important; right: var(--canvas-bleed) !important; }

[data-book-frame] {
  -webkit-mask-image: var(--book-mask-x), var(--book-mask-y);
  mask-image: var(--book-mask-x), var(--book-mask-y);
  -webkit-mask-composite: source-in;
  mask-composite: intersect;
}
/* Under the writing the canvas melts into the page's table (the same planks). */
[data-book-frame="side"] {
  --book-mask-x: linear-gradient(to right, transparent, #000 var(--canvas-bleed));
  --book-mask-y: linear-gradient(to bottom, transparent, #000 0.5rem, #000 calc(100% - 0.5rem), transparent);
}
[data-book-frame="preview"] {
  --book-mask-x: linear-gradient(to right, transparent, #000 9%, #000 91%, transparent);
  --book-mask-y: linear-gradient(to bottom, transparent, #000 10%, #000 88%, transparent);
}
`;
