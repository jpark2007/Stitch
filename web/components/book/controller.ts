/**
 * The book's brain, outside React: opening, the turn queue, hover peels,
 * click and drag turns, block thickness, visibility, the cover, the spine
 * and the camera. Book3D owns one instance and calls `frame()` from useFrame
 * and the pointer handlers from r3f events. Nothing here re-renders React
 * except the rare `onView` / `onStage` callbacks.
 */

import { BufferAttribute, type BufferGeometry, type Group, type Mesh, type PerspectiveCamera, type Scene, Vector3 } from "three";
import type { BlockGeometry } from "./blockGeometry";
import {
  BLOCK_T,
  COVER_OVERHANG,
  COVER_T,
  HINGE_LIFT,
  LEAF_REST_GAP,
  PAGE_H,
  PAGE_W,
  smoothstep,
  tipOf,
  turnTargets,
} from "./leafShape";
import { createRuntime, setDepths, type BookRuntime, type LeafState } from "./runtime";
import { coverOpen, pageLand, pageTurn, paperLift } from "./sound";

export const BOARD_W = PAGE_W + COVER_OVERHANG;
export const BOARD_D = PAGE_H + 2 * COVER_OVERHANG;
/** Height of the front board's hinge pivot. */
export const COVER_PIVOT_Y = COVER_T + (BLOCK_T + 2 * LEAF_REST_GAP) / 2;

const TURN_DUR = 1.15;
const FAST_TURN_DUR = 0.78;
const OPEN_DUR = 1.6;
const DEG = Math.PI / 180;

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const clamp01 = (t: number) => (t < 0 ? 0 : t > 1 ? 1 : t);
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const now = () => performance.now() / 1000;

export interface BookView {
  opened: boolean;
  /** 1-based. */
  spread: number;
}

export interface BookStart {
  opened: boolean;
  spread: number;
  turn: number | null;
}

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

interface Drag {
  leaf: number;
  dir: 1 | -1;
  startX: number;
  startY: number;
  corner: number;
  active: boolean;
  lastP: number;
  lastT: number;
  vel: number;
}

/** A point on the page under the pointer, in book space. */
export interface PagePoint {
  x: number;
  z: number;
}

export class BookController {
  readonly rt: BookRuntime;
  readonly n: number;
  /** Rises to 1 once a spread is open and still; read by the stage. */
  readonly reveal = { current: 0 };

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
  private stageSpread: number;
  private camPos = new Vector3();
  private camTarget = new Vector3();
  private tmpAngles: Float32Array;
  private tmpV = new Vector3();
  private camera: PerspectiveCamera | null = null;
  private canvasRect: () => DOMRect = () => new DOMRect();

  constructor(
    spreads: number,
    start: BookStart,
    private readonly invalidate: () => void,
    private readonly onView: (v: BookView) => void,
    private readonly onStage: (spread: number) => void,
  ) {
    this.n = spreads;
    this.rt = createRuntime(spreads + 1, start.opened ? start.spread : 0);
    this.tmpAngles = new Float32Array(this.rt.restR.length);
    this.open = start.opened ? 1 : 0;
    this.pos = start.opened ? start.spread : 0;
    this.stageSpread = start.opened && start.turn == null ? start.spread : 0;
    this.reveal.current = this.stageSpread ? 1 : 0;
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

  get initialStageSpread() {
    return this.stageSpread;
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
    Object.assign(leaf, { mode: "auto", dir: 1, from: 0, to: 1, t0: now() + 0.32, dur: 1.35, curl: 0.3, corner: 0.2, stiff: 1 });
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
    const target = Math.min(Math.max(1, Math.round(spread)), this.n);
    const pending = this.queue.reduce((s, d) => s + d, this.pos);
    const diff = target - pending;
    for (let i = 0; i < Math.abs(diff); i++) this.queue.push(diff > 0 ? 1 : -1);
    this.invalidate();
  }

  private startTurn(dir: 1 | -1, dur: number) {
    if (dir > 0 && this.pos >= this.n) return false;
    if (dir < 0 && this.pos <= 1) return false;
    const k = dir > 0 ? this.pos : this.pos - 1;
    const leaf = this.rt.leaves[k];
    if (this.hoverLeaf === k) this.hoverLeaf = -1;
    Object.assign(leaf, { mode: "auto", dir, from: leaf.p, to: dir > 0 ? 1 : 0, t0: now(), dur, curl: 0.42, corner: 0.55, stiff: 1 });
    this.pos += dir;
    pageTurn(dur, dir);
    this.invalidate();
    return true;
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

  private zone(pt: PagePoint) {
    const corner = 0.3 + 0.7 * clamp01((pt.z + PAGE_H / 2) / PAGE_H);
    if (pt.x > PAGE_W * 0.55 && this.pos < this.n) return { dir: 1 as const, leaf: this.pos, corner };
    if (pt.x < -PAGE_W * 0.55 && this.pos > 1) return { dir: -1 as const, leaf: this.pos - 1, corner };
    return null;
  }

  /** Pointer over the open pages. Returns the cursor to show. */
  pageMove(pt: PagePoint): string {
    if (this.drag) return "grabbing";
    if (this.frozen || this.open < 1) return "";
    const z = this.zone(pt);
    if (z && !this.busy()) this.setHover(z.leaf, z.dir, z.corner);
    else this.clearHover();
    return z ? "grab" : "";
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

  /** Pointer down on the pages. Returns true if it starts a turn gesture. */
  pageDown(pt: PagePoint, clientX: number, clientY: number): boolean {
    if (this.frozen || this.open < 1 || this.drag || this.busy()) return false;
    const z = this.zone(pt);
    if (!z) return false;
    this.drag = {
      leaf: z.leaf,
      dir: z.dir,
      startX: clientX,
      startY: clientY,
      corner: z.corner,
      active: false,
      lastP: z.dir > 0 ? 0 : 1,
      lastT: now(),
      vel: 0,
    };
    return true;
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
    leaf.p = p;
    this.invalidate();
  }

  dragEnd() {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    const leaf = this.rt.leaves[d.leaf];
    if (!d.active) {
      // A click on the edge or corner: turn the page.
      this.clearHover();
      this.queue.push(d.dir);
      this.invalidate();
      return;
    }
    // Released: complete or spring back, from how far and how fast it went.
    const along = d.dir > 0 ? leaf.p : 1 - leaf.p;
    const speed = d.dir > 0 ? d.vel : -d.vel;
    const complete = along > 0.5 || (speed > 1.1 && along > 0.08);
    const to = complete ? (d.dir > 0 ? 1 : 0) : d.dir > 0 ? 0 : 1;
    Object.assign(leaf, { mode: "auto", from: leaf.p, to, t0: now(), dur: 0.3 + 0.75 * Math.abs(to - leaf.p), stiff: 1.15 });
    if (complete) {
      this.pos += d.dir;
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
    let moving = false;
    rt.frames++;

    // Cover.
    if (this.opening) {
      const k = clamp01((t - this.openT0) / OPEN_DUR);
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
        const dur = this.queue.length > 0 || inFlight.length > 0 ? FAST_TURN_DUR : TURN_DUR;
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
    setDepths(rt, Math.max(0, topL + LEAF_REST_GAP - rt.hinge), Math.max(0, topR + LEAF_REST_GAP - rt.hinge));
    parts.blocks.right.update(topR, Math.min(topR, rt.hinge - LEAF_REST_GAP));
    parts.blocks.left.update(topL, Math.min(topL, rt.hinge - LEAF_REST_GAP));
    if (parts.leftBlock) parts.leftBlock.visible = cntL * per > 0.0015;

    // Visible sheets: anything off its stack, plus the top of each stack.
    let leftTop = -1;
    let rightTop = -1;
    rt.leaves.forEach((leaf, i) => {
      if (leaf.mode === "rest" && leaf.p >= 1) leftTop = i;
      if (leaf.mode === "rest" && leaf.p <= 0 && rightTop < 0) rightTop = i;
    });
    // The first frames draw every sheet once (under the fade-in), so each
    // page's texture is on the GPU before it is first turned to.
    const warm = rt.frames <= 2;
    rt.leaves.forEach((leaf, i) => {
      leaf.visible = warm || leaf.mode !== "rest" || i === leftTop || i === rightTop;
    });
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

    // Stage slot: up once the spread lies open and still.
    const still = this.open >= 1 && !rt.leaves.some((l) => l.mode === "auto" || l.mode === "drag" || l.mode === "frozen");
    const rv = this.reveal.current;
    const target = still ? 1 : 0;
    if (Math.abs(target - rv) > 1e-3) {
      this.reveal.current = rv + (target - rv) * (still ? 0.06 : 0.25);
      moving = true;
    } else this.reveal.current = target;
    if (parts.stage) parts.stage.position.y = topR + LEAF_REST_GAP * 2;
    const stage = still ? this.pos : this.reveal.current > 0 ? this.stageSpread : 0;
    if (stage !== this.stageSpread) {
      this.stageSpread = stage;
      this.onStage(stage);
    }

    // Camera.
    const aspect = ctx.width / Math.max(1, ctx.height);
    const key = `${o.toFixed(4)}|${aspect.toFixed(3)}`;
    if (key !== this.camKey) {
      this.camKey = key;
      frameCamera(o, aspect, ctx.camera.fov, this.camPos, this.camTarget);
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
    const view = { opened: this.isOpen, spread: Math.max(1, this.pos) };
    const vk = `${view.opened}|${view.spread}`;
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

/** Camera: a 3/4 view of the closed book, a reading view when open. */
function frameCamera(open: number, aspect: number, fov: number, pos: Vector3, target: Vector3) {
  const tanV = Math.tan((fov * DEG) / 2);
  const tanH = tanV * aspect;
  const portrait = aspect < 1;
  const halfD = BOARD_D / 2 + 0.2;
  // Open: fit the whole spread with a little table around it.
  // Steep enough that a page standing up mid-turn stays in frame.
  const eO = (portrait ? 66 : 60) * DEG;
  const halfW = BOARD_W + (portrait ? 0.06 : 0.26);
  const dO = Math.max(halfW / tanH, (halfD * Math.sin(eO) + 0.27) / tanV);
  // Closed: a 3/4 view from the left so the stitched spine shows.
  const eC = (portrait ? 62 : 50) * DEG;
  const halfWC = BOARD_W / 2 + (portrait ? 0.24 : 0.85);
  const dC = Math.max(halfWC / tanH, (halfD * Math.sin(eC) + 0.42) / tanV);
  const d = lerp(dC, dO, open);
  const e = lerp(eC, eO, open);
  const az = lerp(portrait ? -10 : -16, 0, open) * DEG;
  // Open, aim a little beyond the book so it sits low, leaving headroom for a
  // page standing up mid-turn.
  target.set(0, lerp(0.1, 0.06, open), lerp(0.04, portrait ? -0.02 : -0.15, open));
  pos.set(
    target.x + d * Math.cos(e) * Math.sin(az),
    target.y + d * Math.sin(e),
    target.z + d * Math.cos(e) * Math.cos(az),
  );
}
