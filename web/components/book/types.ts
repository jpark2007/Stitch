/**
 * Local data shapes for the 3D book. They follow ARCHITECTURE.md section 5
 * (`memories`: title, year, tellings, photoIds) trimmed to what a spread
 * draws, so they can be swapped for `web/lib/types.ts` once the contracts
 * land. In the UI a memory is a "moment"; each one is one spread.
 */

/** Built-in painted stand-ins for photos that are not uploaded yet. */
export type PlaceholderScene = "steps" | "wedding" | "house" | "sea" | "hills";

export interface BookPhoto {
  id: string;
  /** Handwritten caption under the print. */
  caption?: string;
  /** Image URL (API, blob:, data:). Same-origin or CORS-enabled, since it is drawn to a canvas. */
  src?: string;
  /** Painted stand-in used when there is no `src`. */
  scene?: PlaceholderScene;
  /** Width / height of the print. Default 4/3. */
  aspect?: number;
}

/** One moment = one spread: story on the left, the pop-up stage on the right. */
export interface BookSpread {
  /** The memory id. */
  id: string;
  title: string;
  year: number;
  /** Short storybook text printed on the page. */
  text: string;
  /** Names of the people who told it ("David", "Ana"). */
  tellers?: string[];
  photos: BookPhoto[];
}

/** What is printed on the cover and the title page. */
export interface BookCover {
  /** "David's book" */
  title: string;
  /** Small line above the title, e.g. the family name. */
  kicker?: string;
  /** Small line under the title, e.g. "1987 – 1996". */
  subtitle?: string;
}
