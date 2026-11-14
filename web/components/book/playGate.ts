/**
 * When a moment's pop-up plays in the book, and when it asks for a frame
 * (moment/MomentStage.tsx runs it once per drawn frame).
 *
 *   const gate = createPlayGate();
 *   useFrame((state) => {
 *     const step = stepPlayGate(gate, { reveal, view, ended, playing, now });
 *     if (shot.offer(step.offer) || step.redraw) state.invalidate();
 *     // step.action: "rearm" | "start" | "pauseAway" | "resume" | "none"
 *   });
 *
 *   - the set rises with the page: once the spread lies open (reveal) and in
 *     view (on a phone, the camera has reached the stage page) and the scene
 *     is drawn, the telling starts from 0;
 *   - it folds as the page turns away: the gate re-arms, to start from 0 the
 *     next time the spread opens;
 *   - on a phone it pauses while the reader is back on the story page, and
 *     resumes in place on the stage page;
 *   - the stage offers the book its camera while the telling is under way,
 *     and holds the last line a beat in close-up after it ends.
 *
 * The book draws on demand, so `redraw` asks for a frame only while
 * something here is changing (the page settling, the view panning, the end
 * hold running): a book at rest draws nothing. The book's camera reads the
 * offer before the stages' default-priority work in a frame, so a changed
 * offer also needs a frame (StageShot.offer says when).
 *
 * Pure, no React or three (playGate.test.ts).
 */

/** The spread must lie this open, and this much in view, before the scene starts. */
export const OPEN_TO_PLAY = 0.85;
export const VIEW_TO_PLAY = 0.6;
/** Below this the page has started to turn away: pause and re-arm. */
export const OPEN_TO_REARM = 0.4;
export const VIEW_TO_PAUSE = 0.3;
/** Once told, the camera holds on the last line this long (ms) before it pulls back to the spread. */
export const END_HOLD_MS = 900;

export interface PlayGate {
  /** The scene is fully drawn (set by the stage). */
  ready: boolean;
  /** Waiting to start from 0 the next time the spread lies open and in view. */
  armed: boolean;
  /** Paused because the reader went back to the story page. */
  awayPaused: boolean;
  /** When (ms) the telling was first seen ended; -1 while it is not. */
  endedAt: number;
  /** Last frame's view, to tell a panning camera from a still one. */
  lastView: number;
  /** This frame's result, reused (no allocation per frame). */
  readonly step: GateStep;
}

export type GateAction =
  /** Folded away: pause, and go back to 0 for next time. */
  | "rearm"
  /** Open, in view and drawn: start from 0 (or hold at a screenshot's time). */
  | "start"
  /** The reader went back to the story page: pause where it is. */
  | "pauseAway"
  /** Back on the stage page: carry on. */
  | "resume"
  | "none";

export interface GateStep {
  /** The stage wants the book's camera this frame. */
  offer: boolean;
  /** Ask the demand-driven canvas for another frame. */
  redraw: boolean;
  action: GateAction;
}

export interface GateInput {
  /** The book's reveal for this spread (0 folded .. 1 open and still). */
  reveal: number;
  /** The book's stage view (1 the stage page in view; a phone pans 0..1). */
  view: number;
  /** The player's status. */
  ended: boolean;
  playing: boolean;
  /** Wall time, ms. */
  now: number;
}

export function createPlayGate(): PlayGate {
  return { ready: false, armed: true, awayPaused: false, endedAt: -1, lastView: -1, step: { offer: false, redraw: false, action: "none" } };
}

/** One frame of the gate: what the stage offers, whether to draw again, and what the player should do. */
export function stepPlayGate(g: PlayGate, { reveal: r, view: v, ended, playing, now }: GateInput): GateStep {
  const step = g.step;
  step.action = "none";
  // The camera leans in while the telling is under way on an open page
  // (held mid-scene too), and comes back out a beat after it has been told.
  if (!ended) g.endedAt = -1;
  else if (g.endedAt < 0) g.endedAt = now;
  const holding = ended && now - g.endedAt < END_HOLD_MS;
  step.redraw = holding;
  step.offer = !g.armed && r >= OPEN_TO_PLAY && (!ended || holding);
  const viewMoved = v !== g.lastView;
  g.lastView = v;

  if (r < OPEN_TO_REARM) {
    if (!g.armed) {
      g.armed = true;
      g.awayPaused = false;
      step.action = "rearm";
    }
    return step;
  }
  if (g.armed) {
    if (!g.ready || r < OPEN_TO_PLAY || v < VIEW_TO_PLAY) {
      // Keep drawing while the page settles or the camera pans in (the book
      // draws those frames too); never while the reader rests on a phone's
      // story page, where nothing moves.
      if (g.ready && r >= OPEN_TO_PLAY && (r < 1 || viewMoved)) step.redraw = true;
      return step;
    }
    g.armed = false;
    step.action = "start";
    return step;
  }
  // Reading the story page on a phone: hold the scene there, then carry on.
  if (v < VIEW_TO_PAUSE && playing) {
    g.awayPaused = true;
    step.action = "pauseAway";
  } else if (v > VIEW_TO_PLAY && g.awayPaused) {
    g.awayPaused = false;
    step.action = "resume";
  }
  return step;
}
