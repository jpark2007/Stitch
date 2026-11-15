// The book desk's look (BookDesk.tsx): the window is the book's wooden table,
// and the writing lies on it in light ink.
//
// - The writing column (data-desk-writing) re-points the kit's colour tokens
//   to light ink on wood, so every flow control (fields, pickers, the agent's
//   feed and cards) follows without a desk variant of its own; inside a book
//   (data-book-box) they are the paper's again.
// - Text fields are sunk panels of darker wood with a hairline paper edge;
//   focus turns the edge peach.
// - On a phone (no 3D book) the table is a tile of planks in the book's wood
//   colours under the same warm light; on a wide window BookDesk draws the
//   book's own table there instead (deskTable.ts).
//
// Unlayered CSS, so it wins over the flow pages' utility classes.

/** The table under everything: the book's TABLE_BG. */
const TABLE_BG = "#1a110b";

/** Light ink on wood (existing colours: the book's caption ink, the paper tones). */
const INK = "#f3e6cf";

// A phone's table: five planks in the book's wood colours, drawn once as an SVG tile.
const PLANK_H = 140;
const PLANKS: readonly { tone: string; seed: number; joint: number }[] = [
  { tone: "#56290e", seed: 3, joint: 212 },
  { tone: "#4d250d", seed: 8, joint: 538 },
  { tone: "#5c2c10", seed: 13, joint: 87 },
  { tone: "#51270e", seed: 21, joint: 401 },
  { tone: "#582a0f", seed: 34, joint: 655 },
];
const TILE_W = 720;
const TILE_H = PLANK_H * PLANKS.length;

function woodTile(): string {
  // Per plank: dark late-wood streaks, and thin ridges catching the lamp.
  const grain = PLANKS.map(
    (p, i) =>
      `<filter id='g${i}' x='0' y='0' width='1' height='1'><feTurbulence type='fractalNoise' baseFrequency='.0026 .07' numOctaves='3' seed='${p.seed}' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 .094 0 0 0 0 .035 0 0 0 0 .012 3 0 0 0 -1.05'/></filter>` +
      `<filter id='l${i}' x='0' y='0' width='1' height='1'><feTurbulence type='fractalNoise' baseFrequency='.004 .16' numOctaves='2' seed='${p.seed + 50}' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 .5 0 0 0 0 .24 0 0 0 0 .1 2.6 0 0 0 -1.42'/></filter>`,
  ).join("");
  const planks = PLANKS.map((p, i) => {
    const y = i * PLANK_H;
    return (
      `<rect y='${y}' width='${TILE_W}' height='${PLANK_H}' fill='${p.tone}'/>` +
      `<rect y='${y}' width='${TILE_W}' height='${PLANK_H}' filter='url(#g${i})'/>` +
      `<rect y='${y}' width='${TILE_W}' height='${PLANK_H}' filter='url(#l${i})'/>` +
      `<rect x='${p.joint}' y='${y}' width='1.5' height='${PLANK_H}' fill='#0d0603' fill-opacity='.75'/>`
    );
  }).join("");
  const seams = PLANKS.map(
    (_, i) => `<rect y='${(i + 1) * PLANK_H - 1.5}' width='${TILE_W}' height='1.5' fill='#0d0603' fill-opacity='.8'/>`,
  ).join("");
  const fine =
    "<filter id='f' x='0' y='0' width='1' height='1'><feTurbulence type='fractalNoise' baseFrequency='.035 .6' numOctaves='2' seed='5' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 .08 0 0 0 0 .035 0 0 0 0 .012 0 0 0 1.1 -.42'/></filter>";
  // sRGB filters: the colours above are the colours drawn (not taken as linear light).
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='${TILE_W}' height='${TILE_H}' color-interpolation-filters='sRGB'><defs>${grain}${fine}</defs>${planks}<rect width='${TILE_W}' height='${TILE_H}' filter='url(#f)'/>${seams}</svg>`;
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}") 0 0 / ${TILE_W}px ${TILE_H}px`;
}

/** A phone's light: the far end of the table in the dark, a warm pool, the corners in shadow. */
const PHONE_LIGHT =
  "linear-gradient(to bottom, rgb(16 9 5 / 0.6), transparent 18rem)," +
  "radial-gradient(90% 70% at 50% 45%, transparent 35%, rgb(18 10 5 / 0.45) 75%, rgb(14 8 4 / 0.8) 100%)";

/**
 * The lens over the whole desk on a wide window (Book3D's own vignette, which
 * would frame just the canvas, is hidden): the edges of the table fall into
 * shadow, as on the book's own page.
 */
export const DESK_VIGNETTE =
  "radial-gradient(115% 95% at 62% 46%, transparent 52%, rgb(16 9 5 / 0.38) 78%, rgb(12 7 4 / 0.72) 100%)";

/** The kit's own tokens (globals.css), for the paper inside a book that lies in the writing. */
const PAPER_TOKENS = `
  --ink: #2f2620; --ink-soft: #5c4e43; --ink-faint: #8f8173; --rule: rgb(47 38 32 / 0.14);
  --accent: #b9573d; --accent-deep: #8f3f2a;
  --paper: #f5eee1; --paper-light: #fbf8f1; --paper-deep: #ebe0cb;
  --shadow-paper: 0 1px 1px rgb(74 47 29 / 0.08), 0 2px 6px rgb(74 47 29 / 0.08);
  --shadow-inset: inset 0 1px 3px rgb(74 47 29 / 0.14);
  color: var(--ink);`;

/** A text field on the wood: a sunk panel of darker wood with a hairline paper edge. */
const FIELD = `:is(input:not([type=checkbox]):not([type=radio]):not([type=file]):not([type=range]), textarea, select)`;

export const DESK_CSS = `
html:has([data-desk]) { background-color: ${TABLE_BG}; }
html:has([data-desk]) body { background-color: transparent; }
[data-desk-table] { z-index: -1; background: ${PHONE_LIGHT}, ${woodTile()}, ${TABLE_BG}; }

[data-desk-writing] {
  --ink: ${INK};
  --ink-soft: #e4d3b6;
  --ink-faint: #d2bfa0;
  --rule: rgb(243 230 207 / 0.24);
  --accent: #e8957a;
  --accent-deep: #f2b394;
  --paper: transparent;
  --paper-light: rgb(20 10 5 / 0.55);
  --paper-deep: rgb(243 230 207 / 0.12);
  --shadow-paper: none;
  --shadow-inset: inset 0 1px 3px rgb(0 0 0 / 0.45);
  color: var(--ink);
}
[data-desk-writing] [data-book-box] { ${PAPER_TOKENS} }
[data-desk-writing] ::placeholder { color: rgb(228 211 182 / 0.8); }
/* The one terracotta button keeps its paper-coloured words. */
[data-desk-writing] .bg-accent-deep { background-color: #8f3f2a; }
[data-desk-writing] .bg-accent-deep:not(:disabled):hover { background-color: #7c3522; }
[data-desk-writing] .bg-accent-deep.text-paper-light { color: #fbf8f1; }
/* The form's error red, lightened for the wood. */
[data-desk-writing] .text-\\[\\#9c3b2a\\] { color: #ffab95; }

[data-desk-writing] ${FIELD} {
  background-color: rgb(14 7 3 / 0.5);
  border: 0;
  border-radius: 3px;
  padding-left: 0.75rem;
  padding-right: 0.75rem;
  box-shadow: inset 0 1px 3px rgb(0 0 0 / 0.55), 0 0 0 1px rgb(243 230 207 / 0.28);
}
[data-desk-writing] ${FIELD}:hover { box-shadow: inset 0 1px 3px rgb(0 0 0 / 0.55), 0 0 0 1px rgb(243 230 207 / 0.5); }
[data-desk-writing] ${FIELD}:focus { outline: none; box-shadow: inset 0 1px 3px rgb(0 0 0 / 0.55), 0 0 0 2px #f2b394; }
[data-desk-writing] ${FIELD}[aria-invalid="true"] { box-shadow: inset 0 1px 3px rgb(0 0 0 / 0.55), 0 0 0 1.5px #ffab95; }

[data-desk] [data-flow-page] { padding-left: 1rem; padding-right: 1rem; }
@media (min-width: 1024px) {
  /* The writing is a column of the window: it stacks its rows when narrow (Field.tsx's container rows). */
  [data-desk] [data-flow-page] { padding-left: clamp(1rem, 2.4vw, 2.5rem); padding-right: 0.75rem; }
  [data-desk] [data-flow-page] > div { max-width: none; container-type: inline-size; }
}
`;
