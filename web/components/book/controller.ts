/**
 * The book's brain, outside React: opening, the turn queue, hover peels,
 * click and drag turns, block thickness, visibility, the cover, the spine
 * and the camera. Book3D owns one instance and calls `frame()` from useFrame
 * and the pointer handlers from r3f events. Nothing here re-renders React
 * except the rare `onView` / `onStages` callbacks.
 */

import { BufferAttribute, type BufferGeometry, type Group, type Mesh, type PerspectiveCamera, type Scene, Vector3 } from "three";
import type { BlockGeometry } from "./blockGeometry";
import {
  BLOCK_T,
  BOW,
  COVER_OVERHANG,
  COVER_T,
  HINGE_LIFT,
  LEAF_REST_GAP,
  PAGE_H,
  PAGE_W,
  pageProfile,
  releaseCompletes,
  smoothstep,
  spineCrossing,
  tipOf,
  turnTargets,
} from "./leafShape";
import { createRuntime, setDepths, type BookRuntime, type LeafState } from "./runtime";
import { coverOpen, pageLand, pageTurn, paperLift } from "./sound";
import { layStacks } from "./stacking";

export const BOARD_W = PAGE_W + COVER_OVERHANG;
export const BOARD_D = PAGE_H + 2 * COVER_OVERHANG;
/** Height of the front board's hinge pivot. */
export const COVER_PIVOT_Y = COVER_T + (BLOCK_T + 2 * LEAF_REST_GAP) / 2;
/**
 * The pointer target reaches this far past the pages: mid-turn the camera
 * eases back, so a second click on the same spot lands just off the page.
 */
export const HIT_MARGIN = 0.45;

const TURN_DUR = 1.15;
const FAST_TURN_DUR = 0.78;
const OPEN_DUR = 1.6;
/** Reduced motion: quicker, plainer turns and opening; no parallax. */
const CALM = 0.5;
/** Pointer parallax at the frame's edge (radians). */
const PARALLAX_YAW = 2.2 * (Math.PI / 180);
const PARALLAX_PITCH = 1.3 * (Math.PI / 180);
const DEG = Math.PI / 180;

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const clamp01 = (t: number) => (t < 0 ? 0 : t > 1 ? 1 : t);
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const now = () => performance.now() / 1000;

export interface BookView {
  opened: boolean;
  /** The cover has finished opening. */
  ready: boolean;
  /** 1-based. */
  spread: number;
}

export interface BookStart {
  opened: boolean;
  spread: number;
  turn: number | null;
  /** Page to look at closely: -1 left, 1 right, 0 the whole spread. */
  focus: -1 | 0 | 1;
}

/** Below this aspect (portrait phones) the camera reads one page at a time. */
const PHONE_ASPECT = 0.9;

/** Scene objects the controller poses each frame. */
export interface BookParts {
  book: Group | null;
  coverPivot: Group | null;
  leftBlock: Mesh | null;
  spine: Mesh | null;
  shadow: Mesh | null;
  stage: Group | null;
  closedHit: Mesh | null;
  blocks: { left: BlockGeometry; right: BlockGeometry };
  spineGeometry: BufferGeometry;
}

export interface FrameContext {
  camera: PerspectiveCamera;
  scene: Scene;
  width: number;
  height: number;
  parts: BookParts;
}

/** A press on the page away from the turn zones: a tap or a swipe. */
interface Tap {
  side: -1 | 1;
  startX: number;
  startY: number;
  /** Pressed on a turn edge while a leaf was still moving: queue this turn. */
  queued?: 1 | -1;
  /** Pressed while a leaf was moving: it never zooms the camera. */
  busy: boolean;
}

interface Drag {
  leaf: number;
  dir: 1 | -1;
  startX: number;
  startY: number;
  corner: number;
  active: boolean;
  lastP: number;
  lastT: number;
  /** Pointer at the last applied move. */
  lastX: number;
  vel: number;
}

/** A point on the page under the pointer, in book space. */
export interface PagePoint {
  x: number;
  z: number;
}

const onPage = (pt: PagePoint) => Math.abs(pt.x) <= PAGE_W && Math.abs(pt.z) <= PAGE_H / 2;

export class BookController {
  readonly rt: BookRuntime;
  readonly n: number;
  /**
   * Per spread (index 0-based): 0..1, how far its pop-up should stand. It
   * rises once the spread's stage page lies uncovered and still, and falls
   * as a leaf lifts off or comes down over it. Read `.current` in useFrame.
   */
  readonly reveals: { current: number }[];

  private open: number;
  private opening = false;
  private openT0 = 0;
  private peek = 0;
  private peekTarget = 0;
  /** Settled spread (1-based) = leaves on the left. */
  private pos: number;
  private queue: (1 | -1)[] = [];
  private drag: Drag | null = null;
  private hoverLeaf = -1;
  private frozen = false;
  private camKey = "";
  private viewKey = "";
  /** 0-based spreads whose stage is mounted (rising, standing or folding). */
  private stages: number[] = [];
  private camPos = new Vector3();
  private camTarget = new Vector3();
  /** Eased 0..1: how far a leaf stands up; the camera eases back for it. */
  private lift = 0;
  private lastFrameT = 0;
  /** Page the camera reads: -1 left, 1 right, 0 the whole spread. */
  private focusGoal: -1 | 0 | 1;
  private focusE: number;
  /** Eased 0..1: how much the camera frames a single page. */
  private single: number;
  private phone = false;
  private tap: Tap | null = null;
  private calm = false;
  private parallaxOn = false;
  /** Corner peels under the hovering pointer (off for still screenshots). */
  private peels = true;
  /** Pointer parallax: goal and eased, -1..1 across the canvas. */
  private parGoal = { x: 0, y: 0 };
  private par = { x: 0, y: 0 };
  private tmpAngles: Float32Array;
  private tmpV = new Vector3();
  private camera: PerspectiveCamera | null = null;
  private canvasRect: () => DOMRect = () => new DOMRect();

  constructor(
    spreads: number,
    start: BookStart,
    private readonly invalidate: () => void,
    private readonly onView: (v: BookView) => void,
    private readonly onStages: (indices: number[]) => void,
  ) {
    this.n = spreads;
    this.rt = createRuntime(spreads + 1, start.opened ? start.spread : 0);
    this.tmpAngles = new Float32Array(this.rt.restR.length);
    this.open = start.opened ? 1 : 0;
    this.pos = start.opened ? start.spread : 0;
    this.reveals = Array.from({ length: spreads }, () => ({ current: 0 }));
    if (start.opened && start.turn == null) {
      this.reveals[start.spread - 1].current = 1;
      this.stages = [start.spread - 1];
    }
    this.focusGoal = start.focus;
    this.focusE = start.focus;
    this.single = Math.abs(start.focus);
    if (start.opened && start.turn != null) {
      const j = start.spread;
      if (j < spreads) this.freeze(this.rt.leaves[j], start.turn, 1);
      else if (j > 1) this.freeze(this.rt.leaves[j - 1], 1 - start.turn, -1);
    }
  }

  private freeze(leaf: LeafState, p: number, dir: 1 | -1) {
    leaf.mode = "frozen";
    leaf.p = p;
    leaf.dir = dir;
    leaf.corner = 0.55;
    this.frozen = true;
  }

  /** 0-based spreads whose stage is mounted at the start. */
  get initialStages() {
    return this.stages;
  }

  /** Wire the camera and canvas used to map drags to the page. */
  attach(camera: PerspectiveCamera, canvasRect: () => DOMRect) {
    this.camera = camera;
    this.canvasRect = canvasRect;
  }

  /* ---------- Commands ---------- */

  get isOpen() {
    return this.open > 0 || this.opening;
  }

  openBook() {
    if (this.isOpen) return;
    this.opening = true;
    this.openT0 = now();
    this.peekTarget = 0;
    coverOpen();
    // The title leaf follows the cover over, a beat behind.
    const leaf = this.rt.leaves[0];
    const k = this.calm ? CALM : 1;
    Object.assign(leaf, { mode: "auto", dir: 1, from: 0, to: 1, t0: now() + 0.32 * k, dur: 1.35 * k, curl: 0.3, corner: 0.2, stiff: 1 });
    this.pos = 1;
    this.invalidate();
  }

  next() {
    if (!this.isOpen) return this.openBook();
    this.queue.push(1);
    this.invalidate();
  }

  prev() {
    if (!this.isOpen) return;
    this.queue.push(-1);
    this.invalidate();
  }

  goTo(spread: number) {
    if (!this.isOpen) this.openBook();
    if (this.phone) this.setFocus(-1);
    const target = Math.min(Math.max(1, Math.round(spread)), this.n);
    const pending = this.queue.reduce((s, d) => s + d, this.pos);
    const diff = target - pending;
    for (let i = 0; i < Math.abs(diff); i++) this.queue.push(diff > 0 ? 1 : -1);
    this.invalidate();
  }

  /** prefers-reduced-motion: shorter turns and opening, no parallax. */
  setCalm(calm: boolean) {
    this.calm = calm;
    this.invalidate();
  }

  /** Let the camera drift a touch with the pointer (fine pointers only). */
  setParallax(on: boolean) {
    this.parallaxOn = on;
    this.invalidate();
  }

  setPeels(on: boolean) {
    this.peels = on;
    if (!on) this.clearHover();
  }

  /** Pointer position over the canvas, -1..1 on each axis (y down). */
  pointer(x: number, y: number) {
    this.parGoal.x = x;
    this.parGoal.y = y;
    if (this.parallaxOn && !this.calm) this.invalidate();
  }

  /** Look closely at one page (-1 left, 1 right), or at the whole spread (0). */
  setFocus(side: -1 | 0 | 1) {
    // A phone always reads one page; it pans between them.
    const goal = this.phone && side === 0 ? this.focusGoal || -1 : side;
    if (goal === this.focusGoal) return;
    this.focusGoal = goal;
    this.invalidate();
  }

  get focus() {
    return this.focusGoal;
  }

  /**
   * One step through the book, as a reader would page it: on a phone the
   * camera pans from the story page to the stage page before a leaf turns.
   */
  step(dir: 1 | -1) {
    if (!this.isOpen) return dir > 0 ? this.openBook() : undefined;
    if (this.phone && !this.busy()) {
      if (dir > 0 && this.focusGoal < 0) return this.setFocus(1);
      if (dir < 0 && this.focusGoal > 0) return this.setFocus(-1);
    }
    if (dir > 0) this.next();
    else this.prev();
  }

  private startTurn(dir: 1 | -1, dur: number) {
    if (dir > 0 && this.pos >= this.n) return false;
    if (dir < 0 && this.pos <= 1) return false;
    const k = dir > 0 ? this.pos : this.pos - 1;
    const leaf = this.rt.leaves[k];
    if (this.hoverLeaf === k) this.hoverLeaf = -1;
    // Calm turns flip more plainly: less curl, less corner lift.
    const curl = this.calm ? 0.25 : 0.42;
    Object.assign(leaf, { mode: "auto", dir, from: leaf.p, to: dir > 0 ? 1 : 0, t0: now(), dur, curl, corner: this.calm ? 0.3 : 0.55, stiff: 1 });
    this.pos += dir;
    this.afterTurn(dir);
    pageTurn(dur, dir);
    this.invalidate();
    return true;
  }

  /** On a phone a new spread opens on its story page (or, paging back, its stage). */
  private afterTurn(dir: 1 | -1) {
    if (this.phone) this.focusGoal = dir > 0 ? -1 : 1;
    else if (this.focusGoal !== 0) this.focusGoal = 0;
  }

  private busy() {
    return this.queue.length > 0 || this.rt.leaves.some((l) => l.mode === "auto" || l.mode === "drag");
  }

  /* ---------- Closed book ---------- */

  hoverCover(on: boolean) {
    if (this.isOpen) return false;
    this.peekTarget = on ? 0.035 : 0;
    this.invalidate();
    return true;
  }

  /* ---------- Pages: hover peel, click, drag ---------- */

  /** Which outer edge band the point is in, whether or not a leaf is left to turn there. */
  private band(pt: PagePoint): 1 | -1 | 0 {
    // On a phone the page fills the screen: only its outer edge turns it.
    const edge = PAGE_W * (this.phone ? 0.72 : 0.55);
    return pt.x > edge ? 1 : pt.x < -edge ? -1 : 0;
  }

  private zone(pt: PagePoint) {
    const corner = 0.3 + 0.7 * clamp01((pt.z + PAGE_H / 2) / PAGE_H);
    const b = this.band(pt);
    if (b > 0 && this.pos < this.n) return { dir: 1 as const, leaf: this.pos, corner };
    if (b < 0 && this.pos > 1) return { dir: -1 as const, leaf: this.pos - 1, corner };
    return null;
  }

  /** Pointer over the open pages. Returns the cursor to show. */
  pageMove(pt: PagePoint): string {
    if (this.drag) return "grabbing";
    if (this.frozen || this.open < 1) return "";
    const busy = this.busy();
    // Mid-turn pos has already moved on, so the edge is tested by geometry alone.
    const b = busy ? this.band(pt) : 0;
    const z = busy ? null : this.zone(pt);
    // Off the page only counts mid-turn, to queue another turn.
    if (!onPage(pt) && !b) {
      this.clearHover();
      return "";
    }
    if (z && this.peels) this.setHover(z.leaf, z.dir, z.corner);
    else this.clearHover();
    // Mid-turn the edge can't be held, only clicked to queue another turn.
    if (b) return "pointer";
    if (z) return "grab";
    if (busy) return "";
    if (this.phone) return "";
    return this.focusGoal === (pt.x < 0 ? -1 : 1) ? "zoom-out" : "zoom-in";
  }

  pageLeave() {
    if (!this.drag) this.clearHover();
  }

  private setHover(leaf: number, dir: 1 | -1, corner: number) {
    if (this.hoverLeaf === leaf) return;
    this.clearHover();
    const l = this.rt.leaves[leaf];
    if (l.mode !== "rest") return;
    Object.assign(l, { mode: "hover", dir, p: dir > 0 ? 0.045 : 0.955, curl: 0.55, corner: Math.max(0.6, corner), stiff: 1 });
    this.hoverLeaf = leaf;
    this.invalidate();
  }

  private clearHover() {
    if (this.hoverLeaf < 0) return;
    const l = this.rt.leaves[this.hoverLeaf];
    if (l.mode === "hover") {
      l.mode = "rest";
      l.p = l.dir > 0 ? 0 : 1;
    }
    this.hoverLeaf = -1;
    this.invalidate();
  }

  /**
   * Pointer down on the pages. Returns true if it starts a gesture (a turn
   * from the edge, or a tap or swipe elsewhere on the page); the caller then
   * forwards moves to `dragMove` and the release to `dragEnd`.
   */
  pageDown(pt: PagePoint, clientX: number, clientY: number): boolean {
    if (this.frozen || this.open < 1 || this.drag || this.tap) return false;
    const busy = this.busy();
    // Mid-turn pos has already moved on, so the edge is tested by geometry
    // alone; a turn past either end is dropped when the queue reaches it.
    const b = busy ? this.band(pt) : 0;
    const z = busy ? null : this.zone(pt);
    if (!onPage(pt) && !b) return false;
    if (!z) {
      // Mid-turn the leaf can't be grabbed; a press on its edge queues one more turn.
      this.tap = { side: pt.x < 0 ? -1 : 1, startX: clientX, startY: clientY, queued: b || undefined, busy };
      return true;
    }
    this.drag = {
      leaf: z.leaf,
      dir: z.dir,
      startX: clientX,
      startY: clientY,
      corner: z.corner,
      active: false,
      lastP: z.dir > 0 ? 0 : 1,
      lastT: now(),
      lastX: clientX,
      vel: 0,
    };
    return true;
  }

  /** A page is held by the pointer (not just pressed). */
  get dragging() {
    return !!this.drag?.active;
  }

  dragMove(clientX: number, clientY: number) {
    const d = this.drag;
    if (!d) return;
    const leaf = this.rt.leaves[d.leaf];
    if (!d.active) {
      if (Math.hypot(clientX - d.startX, clientY - d.startY) < 6) return;
      d.active = true;
      if (this.hoverLeaf === d.leaf) this.hoverLeaf = -1;
      Object.assign(leaf, { mode: "drag", dir: d.dir, curl: 0.5, corner: Math.max(0.45, d.corner), stiff: 1.7 });
      paperLift();
    }
    const p = this.solveDragP(leaf, clientX);
    const t = now();
    const dt = Math.max(1e-3, t - d.lastT);
    d.vel = d.vel * 0.6 + ((p - d.lastP) / dt) * 0.4;
    d.lastP = p;
    d.lastT = t;
    d.lastX = clientX;
    leaf.p = p;
    this.invalidate();
  }

  /** `final` is false for a cancelled pointer, whose coordinates mean nothing. */
  dragEnd(clientX = 0, clientY = 0, final = true) {
    const tap = this.tap;
    if (tap) {
      this.tap = null;
      if (tap.queued) {
        this.queue.push(tap.queued);
        this.invalidate();
        return;
      }
      const dx = clientX - tap.startX;
      const dy = clientY - tap.startY;
      if (Math.abs(dx) > 40 && Math.abs(dx) > 1.3 * Math.abs(dy)) {
        // A swipe: the page follows the finger, so a swipe left reads on.
        this.step(dx < 0 ? 1 : -1);
      } else if (Math.hypot(dx, dy) < 8 && !tap.busy) {
        // A tap: look closer at that page (a phone pans to it), or back out.
        // Not mid-turn: the turn in flight already chose where to look.
        if (this.phone) this.setFocus(tap.side);
        else this.setFocus(this.focusGoal === tap.side ? 0 : tap.side);
      }
      return;
    }
    const d = this.drag;
    if (!d) return;
    if (d.active) {
      // A held corner sends no moves, so its speed fades with the time since
      // the last one: a pause before letting go is no flick.
      d.vel *= Math.exp(-(now() - d.lastT) / 0.06);
      // The release point counts too, when it isn't where the last move was.
      if (final && clientX !== d.lastX) this.dragMove(clientX, clientY);
    }
    this.drag = null;
    const leaf = this.rt.leaves[d.leaf];
    if (!d.active) {
      // A click on the edge or corner: turn the page.
      this.clearHover();
      this.queue.push(d.dir);
      this.invalidate();
      return;
    }
    // Released: complete or spring back, from where the corner is headed.
    const along = d.dir > 0 ? leaf.p : 1 - leaf.p;
    const speed = d.dir > 0 ? d.vel : -d.vel;
    const complete = releaseCompletes(along, speed, spineCrossing(d.dir, leaf.curl, this.rt.restR, this.rt.restL));
    const to = complete ? (d.dir > 0 ? 1 : 0) : d.dir > 0 ? 0 : 1;
    Object.assign(leaf, { mode: "auto", from: leaf.p, to, t0: now(), dur: 0.3 + 0.75 * Math.abs(to - leaf.p), stiff: 1.15 });
    if (complete) {
      this.pos += d.dir;
      this.afterTurn(d.dir);
      pageLand(d.dir);
    } else if (along > 0.04) pageLand(d.dir);
    this.invalidate();
  }

  /** The progress whose bottom corner sits under the pointer (in screen x). */
  private solveDragP(leaf: LeafState, clientX: number) {
    const camera = this.camera;
    if (!camera) return leaf.p;
    const rect = this.canvasRect();
    const ndcX = ((clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1;
    const input = { p: 0, dir: leaf.dir, curl: leaf.curl, corner: leaf.corner };
    const screenX = (p: number) => {
      input.p = p;
      turnTargets(input, this.rt.restR, this.rt.restL, this.tmpAngles);
      const [tx, ty] = tipOf(this.tmpAngles);
      this.tmpV.set(tx + this.bookX, this.rt.hinge + ty, PAGE_H / 2);
      return this.tmpV.project(camera).x;
    };
    let lo = 0;
    let hi = 1;
    for (let i = 0; i < 18; i++) {
      const mid = (lo + hi) / 2;
      // Screen x falls as the page swings from right to left.
      if (screenX(mid) > ndcX) lo = mid;
      else hi = mid;
    }
    return (lo + hi) / 2;
  }

  private bookX = 0;

  /* ---------- Per frame ---------- */

  /** Advance one frame. Returns true while anything is still moving. */
  frame(ctx: FrameContext): boolean {
    const { rt } = this;
    const { parts } = ctx;
    const t = now();
    const dt = this.lastFrameT ? Math.min(0.05, t - this.lastFrameT) : 0;
    this.lastFrameT = t;
    let moving = false;
    rt.frames++;

    // Cover.
    if (this.opening) {
      const k = clamp01((t - this.openT0) / (OPEN_DUR * (this.calm ? CALM : 1)));
      this.open = easeInOut(k);
      if (k >= 1) {
        this.opening = false;
        this.open = 1;
      }
      moving = true;
    }
    if (Math.abs(this.peekTarget - this.peek) > 1e-4) {
      this.peek += (this.peekTarget - this.peek) * 0.16;
      moving = true;
    } else this.peek = this.peekTarget;
    const cover = Math.max(this.open, this.peek);

    // Queue: start the next turn once the one in flight is well under way.
    if (this.queue.length && this.open >= 1 && !this.drag) {
      const inFlight = rt.leaves.filter((l) => l.mode === "auto");
      const progress = inFlight.length ? Math.min(...inFlight.map((l) => (t - l.t0) / l.dur)) : 1;
      if (progress > 0.32) {
        const dir = this.queue.shift()!;
        const dur = (this.queue.length > 0 || inFlight.length > 0 ? FAST_TURN_DUR : TURN_DUR) * (this.calm ? CALM : 1);
        this.clearHover();
        // A turn past either end is simply dropped; the rest of the queue stays.
        this.startTurn(dir, dur);
      }
      moving = true;
    }

    // Leaves on the clock.
    for (const leaf of rt.leaves) {
      if (leaf.mode !== "auto") continue;
      const k = clamp01((t - leaf.t0) / leaf.dur);
      leaf.p = lerp(leaf.from, leaf.to, k);
      if (k >= 1) {
        leaf.mode = "rest";
        leaf.p = leaf.to;
      }
      moving = true;
    }

    // Block thickness: a sheet leaves its stack as it lifts and joins the
    // other as it lands.
    let cntL = 0;
    let cntR = 0;
    for (const leaf of rt.leaves) {
      cntL += smoothstep(0.82, 1, leaf.p);
      cntR += 1 - smoothstep(0, 0.18, leaf.p);
    }
    const per = BLOCK_T / rt.leaves.length;
    const topL = COVER_T + cntL * per;
    const topR = COVER_T + cntR * per;
    const o = easeInOut(clamp01(cover));
    // Closed, the block is flat; open, the pages dip into the gutter.
    rt.hinge = lerp(topR + LEAF_REST_GAP, COVER_T + HINGE_LIFT, clamp01(this.open * 1.4));
    // Open pages swell a little across their width; a closed book lies flat.
    const bow = BOW * smoothstep(0.55, 1, this.open);
    setDepths(rt, Math.max(0, topL + LEAF_REST_GAP - rt.hinge), Math.max(0, topR + LEAF_REST_GAP - rt.hinge), bow);
    parts.blocks.right.update(topR, Math.min(topR, rt.hinge - LEAF_REST_GAP), rt.bow);
    parts.blocks.left.update(topL, Math.min(topL, rt.hinge - LEAF_REST_GAP), rt.bow);
    if (parts.leftBlock) parts.leftBlock.visible = cntL * per > 0.0015;

    // Visible sheets: anything off its stack, plus the top of each stack,
    // unless a sheet is coming down on it (see stacking.ts). A moving sheet
    // lying on a stack's top sheet is drawn in front of it. The first frames
    // draw every sheet once (under the fade-in), so each page's texture is on
    // the GPU before it is first turned to.
    const warm = rt.frames <= 2;
    layStacks(rt.leaves, warm);
    if (warm) moving = true;

    // Cover, spine, the book's place on the table.
    this.bookX = (-BOARD_W / 2) * (1 - o);
    if (parts.book) parts.book.position.x = this.bookX;
    const angle = -Math.PI * (1 - cover);
    if (parts.coverPivot) {
      parts.coverPivot.position.set(0, COVER_PIVOT_Y, 0);
      parts.coverPivot.rotation.z = angle;
    }
    if (parts.spine) {
      const vis = cover < 0.985;
      parts.spine.visible = vis;
      if (vis) writeSpine(parts.spineGeometry, angle, COVER_PIVOT_Y);
    }
    if (parts.shadow) {
      parts.shadow.scale.set(lerp(BOARD_W, 2 * BOARD_W, o) + 0.45, BOARD_D + 0.45, 1);
      parts.shadow.position.x = lerp(BOARD_W / 2, 0, o);
    }
    if (parts.closedHit) parts.closedHit.visible = this.open < 1;

    // Stage slots. Spread j's stage stands on leaf j's recto (the right
    // page) once the leaf before it has turned clear, and folds down the
    // moment leaf j itself starts to lift. During a turn the outgoing stage
    // folds while the incoming one rises, so both are mounted for a beat.
    const coverUp = smoothstep(0.9, 1, this.open);
    let mounted = "";
    for (let j = 0; j < this.n; j++) {
      const before = rt.leaves[j];
      const page = rt.leaves[j + 1];
      // A corner peel (hover) does not fold the stage; a real lift does.
      const pageDown = page.mode === "hover" ? 1 : 1 - smoothstep(0.01, 0.06, page.p);
      const goal = coverUp * smoothstep(0.62, 0.97, before.p) * pageDown;
      const r = this.reveals[j];
      if (Math.abs(goal - r.current) > 1e-3) {
        // Rise gently, fold quickly (ahead of the lifting leaf).
        const rate = goal > r.current ? 3.2 : 14;
        r.current += (goal - r.current) * (dt === 0 || this.frozen ? 1 : 1 - Math.exp(-dt * rate));
        moving = true;
      } else r.current = goal;
      if (goal > 0 || r.current > 1e-3) mounted += `${j},`;
    }
    if (mounted !== this.stages.join(",") + (this.stages.length ? "," : "")) {
      this.stages = mounted ? mounted.slice(0, -1).split(",").map(Number) : [];
      this.onStages(this.stages);
    }
    // The slot sits on the right page's surface, swell included.
    if (parts.stage) parts.stage.position.y = rt.hinge + pageProfile(PAGE_W / 2, rt.depthR, rt.bow) + LEAF_REST_GAP;

    // Camera. While a leaf stands up the camera eases back a little, so the
    // upright page never clips the top of the frame.
    let liftGoal = 0;
    let liftFrozen = false;
    for (const leaf of rt.leaves) {
      if (leaf.mode === "auto" || leaf.mode === "drag" || leaf.mode === "frozen") {
        liftGoal = Math.max(liftGoal, Math.sin(Math.PI * clamp01(leaf.p)));
        if (leaf.mode === "frozen") liftFrozen = true;
      }
    }
    if (liftFrozen || dt === 0) this.lift = liftGoal;
    else this.lift += (liftGoal - this.lift) * (1 - Math.exp(-dt * 5));
    if (Math.abs(liftGoal - this.lift) > 1e-3) moving = true;
    else this.lift = liftGoal;

    const aspect = ctx.width / Math.max(1, ctx.height);
    const phone = aspect < PHONE_ASPECT;
    if (phone !== this.phone) {
      this.phone = phone;
      // A phone reads one page at a time, starting with the story.
      if (phone && this.focusGoal === 0) this.focusGoal = -1;
    }
    const singleGoal = this.phone ? 1 : Math.abs(this.focusGoal);
    // On a phone the camera travels with a turning leaf, over the spine.
    let focusGoal: number = this.focusGoal;
    if (this.phone) {
      for (const leaf of rt.leaves) {
        if (leaf.mode === "auto" || leaf.mode === "drag") focusGoal = Math.cos(Math.PI * clamp01(leaf.p));
      }
    }
    const ease = dt === 0 ? 1 : 1 - Math.exp(-dt * 4.5);
    this.focusE += (focusGoal - this.focusE) * ease;
    this.single += (singleGoal - this.single) * ease;
    if (Math.abs(focusGoal - this.focusE) > 1e-3 || Math.abs(singleGoal - this.single) > 1e-3) moving = true;
    else {
      this.focusE = focusGoal;
      this.single = singleGoal;
    }
    // Parallax holds still while a page is held, so the drag maps cleanly.
    if (!this.drag?.active) {
      const on = this.parallaxOn && !this.calm && !this.frozen;
      const gx = on ? this.parGoal.x : 0;
      const gy = on ? this.parGoal.y : 0;
      const pe = dt === 0 ? 1 : 1 - Math.exp(-dt * 3);
      this.par.x += (gx - this.par.x) * pe;
      this.par.y += (gy - this.par.y) * pe;
      if (Math.abs(gx - this.par.x) > 2e-3 || Math.abs(gy - this.par.y) > 2e-3) moving = true;
    }
    const key = `${o.toFixed(4)}|${aspect.toFixed(3)}|${this.lift.toFixed(4)}|${this.focusE.toFixed(4)}|${this.single.toFixed(4)}|${this.par.x.toFixed(3)}|${this.par.y.toFixed(3)}`;
    if (key !== this.camKey) {
      this.camKey = key;
      const pose = { open: o, lift: this.lift, focus: this.focusE, single: this.single, px: this.par.x, py: this.par.y };
      frameCamera(pose, aspect, ctx.camera.fov, this.camPos, this.camTarget);
      ctx.camera.position.copy(this.camPos);
      ctx.camera.lookAt(this.camTarget);
      const fog = ctx.scene.fog;
      if (fog && "near" in fog) {
        const d = this.camPos.distanceTo(this.camTarget);
        fog.near = d * 1.05;
        fog.far = d * 2.7;
      }
    }

    // React hears about spread changes only.
    const view = { opened: this.isOpen, ready: this.open >= 1 && !this.opening, spread: Math.max(1, this.pos) };
    const vk = `${view.opened}|${view.ready}|${view.spread}`;
    if (vk !== this.viewKey) {
      this.viewKey = vk;
      this.onView(view);
    }
    return moving;
  }
}

/** The cloth spine: a curve from the back board's edge to the front board's. */
function writeSpine(g: BufferGeometry, angle: number, py: number) {
  const pos = g.getAttribute("position") as BufferAttribute;
  const segs = pos.count / 2 - 1;
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  // The front board's outside edge at the hinge, rotated about the pivot.
  const fx = py * s;
  const fy = py * (1 - c);
  // Both ends leave their boards heading out past the spine.
  const k = Math.hypot(fx, fy) * 0.42;
  const z0 = -BOARD_D / 2;
  const z1 = BOARD_D / 2;
  for (let i = 0; i <= segs; i++) {
    const u = i / segs;
    // Cubic Bezier from the back board's edge (0, 0), leaving toward -x.
    const b = 3 * u * (1 - u) ** 2;
    const d = 3 * u * u * (1 - u);
    const e = u ** 3;
    const x = b * -k + d * (fx + c * k) + e * fx;
    const y = d * (fy + s * k) + e * fy;
    pos.setXYZ(i * 2, x, y, z0);
    pos.setXYZ(i * 2 + 1, x, y, z1);
  }
  pos.needsUpdate = true;
  g.computeVertexNormals();
  g.computeBoundingSphere();
}

export interface CameraPose {
  /** 0 closed .. 1 open (eased). */
  open: number;
  /** 0..1: a leaf standing up mid-turn. */
  lift: number;
  /** -1..1: which page the camera looks at (eased). */
  focus: number;
  /** 0..1: framing one page rather than the spread (eased). */
  single: number;
  /** Pointer parallax, -1..1 (optional). */
  px?: number;
  py?: number;
}

/** Camera: a 3/4 view of the closed book, a reading view when open. */
export function frameCamera(pose: CameraPose, aspect: number, fov: number, pos: Vector3, target: Vector3) {
  const { open, lift } = pose;
  // A standing leaf needs more of the spread in view. A portrait frame has
  // the height for it already, so it widens only a little there (a phone
  // pans across the spine with the leaf rather than zooming right out).
  const one = pose.single * (1 - lift * (aspect < 1 ? 0.3 : 1));
  const tanV = Math.tan((fov * DEG) / 2);
  const tanH = tanV * aspect;
  const portrait = aspect < 1;
  // Open: the spread fills the frame, leaving room for the dots below.
  const eO = (portrait ? 66 : 58) * DEG;
  const halfDO = BOARD_D / 2 + 0.05;
  // (The near edge is closer than the spine, so it reads wider: pad for it.)
  // One page: that page, with a sliver of the other to tap.
  const halfW = lerp(BOARD_W + (portrait ? 0.06 : 0.33), BOARD_W * (portrait ? 0.6 : 0.62), one);
  const dO = Math.max(halfW / tanH, (halfDO * Math.sin(eO) + lerp(0.16, portrait ? 0.16 : 0.07, one)) / tanV);
  // Closed: a 3/4 view from the left so the stitched spine shows.
  const eC = (portrait ? 62 : 50) * DEG;
  const halfDC = BOARD_D / 2 + 0.2;
  // Phones see the closed book large; the spine still shows.
  const halfWC = BOARD_W / 2 + (portrait ? 0.17 : 0.85);
  const dC = Math.max(halfWC / tanH, (halfDC * Math.sin(eC) + 0.42) / tanV);
  // A standing leaf (about a page tall over the spine): back off and look up a little.
  const l = lift * open;
  const d = lerp(dC, dO, open) * (1 + 0.2 * l);
  // The camera drifts a little toward the pointer, as a head would.
  const e = lerp(eC, eO, open) + (pose.py ?? 0) * PARALLAX_PITCH;
  const az = lerp(portrait ? -10 : -16, 0, open) * DEG + (pose.px ?? 0) * PARALLAX_YAW;
  // Aim a touch nearer the reader than the spine, so the dots have room below.
  const focusX = pose.focus * (BOARD_W / 2) * one * open;
  target.set(focusX, lerp(0.1, 0.03, open) + 0.26 * l, lerp(0.04, portrait ? 0 : 0.07, open) - 0.1 * l);
  pos.set(
    target.x + d * Math.cos(e) * Math.sin(az),
    target.y + d * Math.sin(e),
    target.z + d * Math.cos(e) * Math.cos(az),
  );
}
