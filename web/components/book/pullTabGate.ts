/**
 * When a pull tab is out of its slit, and when it takes a pull
 * (moment/PullTabLever.tsx runs it once per drawn frame).
 *
 *   const g = tabGate({ tabShot, frameShot, reveal, openAt: OPEN_TO_PLAY, held, away });
 *   away = stepTabAway(away, g.out, dt);   // 0 out of the slit .. 1 home in it
 *   if (g.grabbable) // take presses, hover, the grab cursor
 *   if (endNudge({ ended, grabbable: g.grabbable, pulled, held, nudged })) // wiggle once
 *
 * The rule: a tab is shown out of its slit only when it can be pulled; the
 * scene is never seeked by a tab (CONTRACTS 7).
 *
 *   - a tab bound to a shot is out only while that shot is on stage; after
 *     the telling the frame keeps the last shot, so the last shot's tabs stay
 *     out in the end hold and at rest;
 *   - a tab with no shot, or any tab before the first shot, is out;
 *   - a held tab stays out, so a shot changing under the hand never yanks it;
 *   - an out tab takes a pull from the moment the scene may play (the play
 *     gate's OPEN_TO_PLAY), once it has slid most of the way out.
 *
 * Pure, no imports (pullTabGate.test.ts).
 */

export interface TabGateInput {
  /** The tab's interaction's shot (null: every shot). */
  tabShot: string | null | undefined;
  /** The shot on stage now (null: before the first). */
  frameShot: string | null | undefined;
  /** The book's reveal for this spread (0 folded .. 1 open and still). */
  reveal: number;
  /** The reveal a pull is taken from (the play gate's OPEN_TO_PLAY). */
  openAt: number;
  /** Under the pointer's hand. */
  held: boolean;
  /** How far home in its slit the card is (0 out .. 1 home). */
  away: number;
}

export interface TabGate {
  /** The card's target: out of its slit, or home in it. */
  out: boolean;
  /** It takes a press (and hover, the grab cursor and nudges). */
  grabbable: boolean;
}

/** The tab belongs to the shot on stage (or to every shot). */
export function tabOnShot(tabShot: string | null | undefined, frameShot: string | null | undefined): boolean {
  return !tabShot || frameShot == null || frameShot === tabShot;
}

export function tabGate(i: TabGateInput): TabGate {
  const out = i.held || tabOnShot(i.tabShot, i.frameShot);
  return { out, grabbable: i.held || (out && i.reveal >= i.openAt && i.away < 0.5) };
}

/** One frame of the card sliding toward out (away 0) or home (away 1), `rate` per second, eased. */
export function stepTabAway(away: number, out: boolean, dt: number, rate = 4): number {
  const target = out ? 0 : 1;
  if (away === target) return away;
  const step = Math.min(Math.max(dt, 0), 0.05) * rate;
  // Ease: quick while far, gentle as it seats, with a floor so it arrives.
  const gap = target - away;
  const move = Math.sign(gap) * Math.min(Math.abs(gap), step * Math.max(0.35, Math.min(1, 2 * Math.abs(gap))));
  const next = away + move;
  return Math.abs(target - next) < 1e-3 ? target : next;
}

/** The end-of-telling nudge: once, for a tab that can be pulled and has not been. */
export function endNudge(i: { ended: boolean; grabbable: boolean; pulled: boolean; held: boolean; nudged: boolean }): boolean {
  return i.ended && i.grabbable && !i.pulled && !i.held && !i.nudged;
}
