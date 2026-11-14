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

/**
 * A teller of a moment with more than one telling: a small taped label on
 * the story page ("Hear Ana's version") with their puppet's portrait.
 */
export interface BookTeller {
  /** Person id (the telling's author). */
  id: string;
  name: string;
  /** Thread colour (character.color): the puppet and the label's tape. */
  color: string;
  /** Their telling in their own words: the page's text when the spread has none of its own. */
  text?: string;
}

/** A pull tab printed on the stage page: a paper lever in a slit. */
export interface BookPullTab {
  /** Interaction id. */
  id: string;
  /** Pencilled beside the tab, e.g. "Pull to drop the books". */
  label: string;
  /** [u, v] on the stage page, fractions from its top-left (Interaction.at). */
  at: readonly [number, number];
}

/** One moment = one spread: story on the left, the pop-up stage on the right. */
export interface BookSpread {
  /** The memory id. */
  id: string;
  title: string;
  year: number;
  /**
   * Short storybook text printed on the page. Empty: the playing telling's
   * own words (BookTeller.text) are printed instead.
   */
  text: string;
  /** Names of the people who told it ("David", "Ana"). */
  tellers?: string[];
  /**
   * One taped label per telling, to switch between them. When set they
   * replace the "as told by" line; Book3D's `tellings` says which is playing.
   */
  tellings?: BookTeller[];
  photos: BookPhoto[];
  /**
   * A real pop-up stands on the right page: it is printed plain (no "the
   * pop-up rises here" marks), with its pull tabs' slits and pencil notes.
   */
  stage?: { pullTabs?: BookPullTab[] };
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
