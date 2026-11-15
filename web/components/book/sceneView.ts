/**
 * Scene view or book view, while a moment plays on the open spread.
 *
 * In scene view (the default) the book's camera leans into the pop-up and
 * follows the timeline's camera tracks, like a cut scene. In book view it
 * stays on the whole open spread, story page and pop-up together, while the
 * scene carries on playing in the pop-up: same timeline, audio and captions.
 * Only the book's camera changes; nothing pauses, seeks or restarts.
 *
 *   const view = new SceneView(readSceneViewPref(), writeSceneViewPref);
 *   view.offer(true);       // the book's camera: a moment plays on this spread
 *   view.toggle();          // the reader: Escape, the paper toggle, a tap
 *   stepViewShot(s, want, reading, turning, view.mode, calm, dt); // per frame
 *
 * The reader's choice is kept per browser (localStorage, guarded), so the
 * next moment they open plays the way they last chose.
 *
 * Pure: no React or three, and no DOM but the guarded storage (sceneView.test.ts).
 */

import { stepShotWeight } from "./shot";

export type SceneViewMode = "scene" | "book";

export const SCENE_VIEW_KEY = "stitch:book-view";

/** Seconds to lean into a playing stage's shot; to come back out when the telling ends; when a page lifts. */
export const SHOT_IN = 1.8;
export const SHOT_OUT = 1.5;
export const SHOT_TURN = 0.5;
/** Seconds for the reader's own switch between the scene and the whole book, either way. */
export const SHOT_TOGGLE = 0.75;

type ReadStore = Pick<Storage, "getItem">;
type WriteStore = Pick<Storage, "setItem">;

function safeLocal(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** The reader's stored choice; "scene" when there is none, it is unreadable, or storage is blocked. */
export function readSceneViewPref(store: ReadStore | null = safeLocal()): SceneViewMode {
  try {
    return store?.getItem(SCENE_VIEW_KEY) === "book" ? "book" : "scene";
  } catch {
    return "scene";
  }
}

/** Keep the reader's choice; silent when storage is blocked (it lasts the visit only). */
export function writeSceneViewPref(mode: SceneViewMode, store: WriteStore | null = safeLocal()): void {
  try {
    store?.setItem(SCENE_VIEW_KEY, mode);
  } catch {
    // Private mode or full storage: the choice holds until the page goes.
  }
}

export interface SceneViewState {
  readonly mode: SceneViewMode;
  /** A moment plays on the open spread: the reader may switch views. */
  readonly available: boolean;
}

/**
 * The view mode, one per book: the reader changes it, the book's camera
 * reads it each frame and says whether a moment is playing. `get` and
 * `subscribe` suit useSyncExternalStore; the snapshot is replaced only when
 * something changes.
 */
export class SceneView {
  private snap: SceneViewState;
  private readonly listeners = new Set<() => void>();
  private readonly persist: ((mode: SceneViewMode) => void) | undefined;

  constructor(mode: SceneViewMode = "scene", persist?: (mode: SceneViewMode) => void) {
    this.snap = { mode, available: false };
    this.persist = persist;
  }

  get mode(): SceneViewMode {
    return this.snap.mode;
  }

  get available(): boolean {
    return this.snap.available;
  }

  get = (): SceneViewState => this.snap;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** From the book's camera, every frame: whether a moment plays here. Allocates and emits only on a change. */
  offer(available: boolean): void {
    if (available === this.snap.available) return;
    this.snap = { mode: this.snap.mode, available };
    this.emit();
  }

  /** The reader chooses a view. Only while a moment plays; returns whether it changed. */
  choose(mode: SceneViewMode): boolean {
    if (!this.snap.available || mode === this.snap.mode) return false;
    this.snap = { mode, available: true };
    this.persist?.(mode);
    this.emit();
    return true;
  }

  toggle(): boolean {
    return this.choose(this.snap.mode === "scene" ? "book" : "scene");
  }

  private emit() {
    for (const l of this.listeners) l();
  }
}

/** The reader's switch, named for the view it goes to: the label shown, the action it says aloud, its tooltip. */
export interface ViewSwitch {
  readonly to: SceneViewMode;
  readonly label: string;
  readonly action: string;
  readonly title: string;
}

const TO_BOOK: ViewSwitch = { to: "book", label: "Whole book", action: "See the whole book", title: "See the whole book (Esc)" };
const TO_SCENE: ViewSwitch = { to: "scene", label: "Cutscene", action: "Back to the cutscene", title: "Back to the cutscene (Esc)" };

/** From the cutscene the switch offers the whole book; from the whole book, the cutscene. */
export function viewSwitch(mode: SceneViewMode): ViewSwitch {
  return mode === "scene" ? TO_BOOK : TO_SCENE;
}

/** What the book's Escape does (after an open explain note or contents list has taken it). */
export function escapeAction(available: boolean): "toggle" | "focus" {
  return available ? "toggle" : "focus";
}

/** The camera's weight toward the stage shot, and the goal a reader's toggle is easing to (-1 for none). */
export interface ViewShot {
  w: number;
  quickTo: number;
}

/** The reader switched views: the next ease toward that goal takes SHOT_TOGGLE. */
export function chooseViewShot(s: ViewShot, mode: SceneViewMode): void {
  s.quickTo = mode === "scene" ? 1 : 0;
}

/**
 * One frame of the camera's weight toward the stage shot. Mutates `s` and
 * returns the goal. Leaning in needs a moment playing (`want`), the stage
 * page read (`reading`), no page lifting (`turning`) and scene view. A
 * reader's switch eases in SHOT_TOGGLE either way; anything else (a telling
 * starting or ending, a turn) keeps its own pace. Reduced motion (`calm`)
 * cuts. Positional arguments, so nothing is allocated per frame.
 */
export function stepViewShot(
  s: ViewShot,
  want: boolean,
  reading: boolean,
  turning: boolean,
  mode: SceneViewMode,
  calm: boolean,
  dt: number,
): 0 | 1 {
  const goal = want && reading && !turning && mode === "scene" ? 1 : 0;
  // The goal moved for another reason (the telling ended mid-switch): the usual pace again.
  if (s.quickTo !== goal) s.quickTo = -1;
  const quick = s.quickTo >= 0;
  s.w = calm ? goal : stepShotWeight(s.w, goal, dt, quick ? SHOT_TOGGLE : SHOT_IN, turning ? SHOT_TURN : quick ? SHOT_TOGGLE : SHOT_OUT);
  if (s.w === goal) s.quickTo = -1;
  return goal;
}
