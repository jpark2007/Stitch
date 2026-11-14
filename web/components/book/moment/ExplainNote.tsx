"use client";

/**
 * Click-to-explain on a moment's stage: click (or tap) a cast member or prop
 * that has an explanation and a small card of real paper rises on a paper
 * hinge beside it, like a pop-up's mechanism, with the "why it matters" in
 * the handwriting font, tied back to the cutout by a stitched thread. The
 * cutout lifts a few millimetres off its layer. Everything is in the
 * diorama's scene graph, lit and shadowed by the book's lamp.
 *
 *   const notes = useMemo(() => new ExplainSession(controller), [controller]);
 *   useEffect(() => notes.attach(), [notes]);
 *   <TimelineScene …>
 *     <ExplainLayer session={notes} fonts={fonts} reveal={reveal} caption={…} enabled={ready} />
 *   </TimelineScene>
 *
 * ExplainSession is the open note and what it did to the scene: opening
 * pauses the telling (narration too) and closing hands it back, in place,
 * if it was playing (../explain.ts resumesAfter). The stage's play gate
 * asks it before resuming (MomentStage). A telling switch closes the note.
 *
 * ExplainLayer (ExplainStage does the work), inside the stage, in stage space:
 *   - one invisible pointer target per explainable cutout, hit-tested on
 *     the cutout's paper (stage/play/pick), forgiving by a few px (more for
 *     a finger); marked as a book control so the book leaves those presses
 *     alone (no page turn, focus change or corner peel). Cast who are
 *     offstage right now are not targets;
 *   - while a note is open, the rest of the stage takes the press that
 *     closes it (so it does not also step the camera out of its shot); a
 *     press anywhere else on the book closes it too, and the book carries
 *     on with it as usual; so do Escape, a page turn and a telling;
 *   - the note (NoteRig): placed in screen space so it stays wholly on
 *     screen beside its target (../explain.ts placeNote), and re-placed
 *     only when the camera moves; it rises, settles, and then nothing draws;
 *   - an aria-live line with the note's words, and the test hook
 *     window.__stitchExplain (./explainProbe).
 *
 * Nothing re-renders React after mount; everything moves in useFrame, and
 * only while something moves. GPU resources are made per note and disposed
 * as it folds away.
 */

import { useFrame, useThree, type RootState, type ThreeEvent } from "@react-three/fiber";
import { useEffect, useMemo, useState, type RefObject } from "react";
import {
  Box3,
  BoxGeometry,
  CanvasTexture,
  CylinderGeometry,
  ExtrudeGeometry,
  Group,
  InstancedMesh,
  Matrix4,
  Mesh,
  MeshStandardMaterial,
  Object3D,
  Quaternion,
  Raycaster,
  SRGBColorSpace,
  Shape,
  SphereGeometry,
  Vector3,
  type Camera,
  type Intersection,
} from "three";
import { markShadowsDirty } from "@/components/stage/clock";
import { cutoutBox, LIFT_UP, pickCutout, pickNear, useScene, type StageRuntime } from "@/components/stage/play";
import type { PlaybackController } from "@/components/stage/playback";
import { getPaperTextures } from "@/components/paper/materials";
import { markBookControl } from "../controls";
import {
  FOLD_MS,
  HOVER_LIFT,
  LIFT,
  RISE_MS,
  explainItems,
  explainStep,
  layoutNote,
  noteFold,
  noteRise,
  onSet,
  placeNote,
  placement,
  rect,
  resumesAfter,
  sewAt,
  stepLift,
  type CloseReason,
  type ExplainEvent,
  type ExplainItem,
  type Measure,
  type NoteLayout,
  type PlaceInput,
  type Rect,
} from "../explain";
import { INK, INK_SOFT, canvas, drawStitchLine, paper, rng, type PageFonts } from "../paperArt";
import { paperLift } from "../sound";
import { registerExplainProbe, type ExplainNoteBox, type ExplainPoint } from "./explainProbe";

/* ------------------------------------------------------------------------ */
/* The session: which note is open, and the scene's pause                    */
/* ------------------------------------------------------------------------ */

type SessionListener = (open: string | null, prev: string | null, closed: CloseReason | null) => void;

export class ExplainSession {
  private current: string | null = null;
  /**
   * The telling was playing when the first note opened (or the play gate
   * wanted it back meanwhile): closing the note plays it on, in place.
   */
  private wasPlaying = false;
  private mayResume: () => boolean = () => true;
  private can: (target: string) => boolean = () => false;
  private narrator: string | null = null;
  /** Our own pause or play is under way: its status is not a telling switch. */
  private quiet = false;
  private readonly listeners = new Set<SessionListener>();

  constructor(readonly controller: PlaybackController) {}

  /** The open note's target, or null. */
  get open(): string | null {
    return this.current;
  }

  /** The stage says whether the telling may play on now (open, in view, mid-telling). */
  setMayResume(fn: () => boolean) {
    this.mayResume = fn;
  }

  /** The layer says whether a target can open now (explainable, on the set). */
  setCan(fn: (target: string) => boolean) {
    this.can = fn;
  }

  /** The play gate wanted the telling back while a note is open: it plays on once the note is put away. */
  resumeLater() {
    if (this.current !== null) this.wasPlaying = true;
  }

  /**
   * Follow the player (a telling switch closes the note). Returns the
   * detach, which also closes any open note without playing on (the stage
   * is going away). Call from an effect.
   */
  attach(): () => void {
    const off = this.controller.subscribe(() => this.onStatus());
    return () => {
      off();
      if (this.current !== null) this.dispatch({ type: "turn" });
    };
  }

  dispatch(ev: ExplainEvent): void {
    const prev = this.current;
    const step = explainStep(prev, ev, this.can);
    if (step.open === prev) return;
    this.current = step.open;
    const c = this.controller;
    if (prev === null) {
      const s = c.getStatus();
      this.wasPlaying = s.playing;
      this.narrator = s.narrator;
      if (s.playing) this.hush(() => c.pause());
    } else if (step.open === null) {
      const resume = this.wasPlaying && step.closed !== null && resumesAfter(step.closed) && this.mayResume();
      this.wasPlaying = false;
      if (resume && !c.getStatus().playing) this.hush(() => c.play());
    }
    for (const l of this.listeners) l(step.open, prev, step.closed);
  }

  subscribe(l: SessionListener): () => void {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }

  private hush(fn: () => void) {
    this.quiet = true;
    try {
      fn();
    } finally {
      this.quiet = false;
    }
  }

  /** Another telling chosen, or the scene started again from outside: the note goes. */
  private onStatus() {
    if (this.quiet || this.current === null) return;
    const s = this.controller.getStatus();
    if (s.narrator !== this.narrator || s.playing) this.dispatch({ type: "telling" });
  }
}

/* ------------------------------------------------------------------------ */
/* The note: a card on a paper hinge, and its thread                         */
/* ------------------------------------------------------------------------ */

/** Where the note stands: stage z (in front of the front layer, behind the printed words). */
const NOTE_Z = 0.8;
/** How high its hinge sits off the page, stage units. */
const BASE_Y = 0.004;
/** Card thickness, stage units (about a postcard's, at the book's scale). */
const CARD_T = 0.0055;
/** Canvas px per card px. */
const SUPER = 2.2;
/** Stitches in the thread. */
const STITCHES = 26;
const THREAD_R = 0.0052;
/** On-screen body type size the note is sized for (CSS px; the handwriting runs small), and the smallest it may shrink to. */
const READ_PX = 21;
const READ_PX_PHONE = 18;
const MIN_PX = 14;
/** The tallest a note may stand on the page, stage units (a puppet is 0.5). */
const MAX_H = 0.95;
/** Screen margins the note keeps clear of (the book's header, its bottom hints and buttons). */
const VIEW_PAD = { top: 58, bottom: 74, side: 12 };
/** Most a note turns toward the camera, and leans back to face it, radians. */
const MAX_YAW = 0.42;
const MAX_LEAN = 0.38;
/** Pointer slack round a cutout's edge, px: a mouse, and a finger. */
const SLACK_FINE = 5;
const SLACK_TOUCH = 14;

const UP = new Vector3(0, 1, 0);

function seedOf(s: string, k: number): number {
  let seed = k;
  for (let i = 0; i < s.length; i++) seed = (seed * 31 + s.charCodeAt(i)) >>> 0;
  return seed;
}

/** A card outline: straight along its hinge (the bottom), deckled on the other three sides. Unit height, `aspect` wide, origin at the hinge's middle. */
function deckledCard(aspect: number, seed: number): Shape {
  const r = rng(seed);
  const s = new Shape();
  const hw = aspect / 2;
  const amp = 0.009;
  let o = 0;
  const wob = () => {
    o = o * 0.55 + (r() - 0.5) * amp;
    return o;
  };
  s.moveTo(-hw, 0);
  s.lineTo(hw, 0);
  const side = (n: number, f: (t: number, d: number) => [number, number]) => {
    for (let i = 1; i <= n; i++) {
      const [x, y] = f(i / n, wob());
      s.lineTo(x, y);
    }
  };
  const nV = 34;
  const nH = Math.max(12, Math.round(aspect * 34));
  side(nV, (t, d) => [hw + d, t * 0.996]);
  side(nH, (t, d) => [hw - t * aspect, 1 + d]);
  side(nV, (t, d) => [-hw + d, 1 - t]);
  s.closePath();
  return s;
}

/** The note's face: paper, the heading in the handwriting font over a short running stitch, then the words. */
function drawNote(item: ExplainItem, l: NoteLayout, fonts: PageFonts, thread: string): HTMLCanvasElement {
  const { c, ctx } = canvas(Math.ceil(l.width * SUPER), Math.ceil(l.height * SUPER));
  ctx.scale(SUPER, SUPER);
  paper(ctx, l.width, l.height, { seed: seedOf(item.target, 7), tone: "#f8f2e4" });
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
  ctx.fillStyle = INK;
  ctx.font = `600 ${l.titleFont}px ${fonts.hand}`;
  const ty = l.pad + l.titleFont * 0.86;
  ctx.fillText(l.title, l.pad, ty);
  const tw = Math.min(ctx.measureText(l.title).width, l.width - 2 * l.pad);
  drawStitchLine(ctx, l.pad + 1, ty + 9, l.pad + Math.max(46, tw * 0.9), ty + 9, thread, 2.4, 9, 6);
  ctx.fillStyle = INK_SOFT;
  ctx.font = `500 ${l.font}px ${fonts.hand}`;
  for (let i = 0; i < l.lines.length; i++) ctx.fillText(l.lines[i], l.pad, l.bodyTop + l.font * 0.8 + i * l.lineHeight);
  return c;
}

type Phase = "rise" | "stand" | "fold" | "done";

interface Scratch {
  a: Vector3;
  b: Vector3;
  c: Vector3;
  p0: Vector3;
  p1: Vector3;
  mid: Vector3;
  dir: Vector3;
  scale: Vector3;
  q: Quaternion;
  m: Matrix4;
}

interface SharedGeometry {
  stitch: CylinderGeometry;
  knot: SphereGeometry;
  strut: BoxGeometry;
}

/** Quadratic Bézier from p0 through control c to p1, at t. */
function bezier(p0: Vector3, c: Vector3, p1: Vector3, t: number, out: Vector3): Vector3 {
  const u = 1 - t;
  return out.set(
    u * u * p0.x + 2 * u * t * c.x + t * t * p1.x,
    u * u * p0.y + 2 * u * t * c.y + t * t * p1.y,
    u * u * p0.z + 2 * u * t * c.z + t * t * p1.z,
  );
}

/**
 * One note: the card (deckled, a postcard thick, its face a canvas of
 * paper and handwriting), the little strut that props it, and the thread
 * of stitches from the cutout to the card's near top corner, with a knot at
 * each end. It stands at stage (x, 0, NOTE_Z), turned to the reader, and
 * rises on its hinge from flat on the page.
 */
class NoteRig {
  readonly root = new Group();
  private readonly hinge = new Group();
  private readonly sizer = new Group();
  private readonly attach = new Object3D();
  private readonly card: Mesh;
  private readonly strut: Mesh;
  private readonly thread: InstancedMesh;
  private readonly knotA: Mesh;
  private readonly knotB: Mesh;
  private readonly cardGeo: ExtrudeGeometry;
  private readonly texture: CanvasTexture;
  private readonly face: MeshStandardMaterial;
  private readonly edge: MeshStandardMaterial;
  private readonly threadMat: MeshStandardMaterial;

  phase: Phase = "rise";
  private t0 = performance.now();
  readonly aspect: number;
  /** Placement, eased toward its goal. */
  x = 0;
  h = 0.3;
  private yaw = 0;
  private lean = 0;
  private readonly goal = { x: 0, h: 0.3, yaw: 0, lean: 0 };
  /** The target's thread end in stage space, without its lift. */
  readonly anchor = new Vector3();
  /** The target's paper, world space, as it was when the note opened (for placement). */
  readonly box = new Box3();
  private placed = false;
  private lastSew = -1;

  constructor(
    readonly item: ExplainItem,
    readonly layout: NoteLayout,
    fonts: PageFonts,
    shared: SharedGeometry,
  ) {
    const color = item.color ?? "#b9573d";
    this.aspect = layout.width / layout.height;
    this.texture = new CanvasTexture(drawNote(item, layout, fonts, color));
    this.texture.colorSpace = SRGBColorSpace;
    this.texture.anisotropy = 8;
    // Shape x (-aspect/2 .. aspect/2) and y (0 .. 1) onto the canvas.
    this.texture.repeat.set(1 / this.aspect, 1);
    this.texture.offset.set(0.5, 0);
    const grain = getPaperTextures("paper", [2, 2]);
    this.face = new MeshStandardMaterial({ map: this.texture, roughness: 0.93, metalness: 0 });
    this.face.normalMap = grain.normalMap;
    this.face.normalScale.set(0.45, 0.45);
    this.edge = new MeshStandardMaterial({ color: "#d8c6a2", roughness: 1, metalness: 0 });
    this.threadMat = new MeshStandardMaterial({ color, roughness: 0.72, metalness: 0 });

    // Built at unit height and depth, and sized by its group.
    this.cardGeo = new ExtrudeGeometry(deckledCard(this.aspect, seedOf(item.target, 3)), {
      depth: 1,
      bevelEnabled: false,
      curveSegments: 1,
      steps: 1,
    });
    this.card = new Mesh(this.cardGeo, [this.face, this.edge]);
    this.card.castShadow = true;
    this.card.receiveShadow = true;
    this.sizer.add(this.card, this.attach);
    this.hinge.add(this.sizer);
    this.root.add(this.hinge);

    this.strut = new Mesh(shared.strut, this.edge);
    this.strut.castShadow = true;
    this.root.add(this.strut);

    this.thread = new InstancedMesh(shared.stitch, this.threadMat, STITCHES);
    this.thread.count = 0;
    this.thread.castShadow = true;
    this.thread.frustumCulled = false;
    this.knotA = new Mesh(shared.knot, this.threadMat);
    this.knotB = new Mesh(shared.knot, this.threadMat);
    this.knotA.visible = this.knotB.visible = false;
    this.knotA.castShadow = this.knotB.castShadow = true;
    this.root.name = `explain-note:${item.target}`;
  }

  /** Everything it adds to the stage (the thread lives in stage space, beside the card's group). */
  get parts(): Object3D[] {
    return [this.root, this.thread, this.knotA, this.knotB];
  }

  get cardMesh(): Mesh {
    return this.card;
  }

  /** It has been placed once (later placements glide it). */
  get isPlaced(): boolean {
    return this.placed;
  }

  get live(): boolean {
    return this.phase === "rise" || this.phase === "stand";
  }

  /** Where it should stand, and which edge the thread leaves from; the first placement is where it rises. */
  setGoal(x: number, h: number, yaw: number, lean: number, tie: -1 | 1) {
    const g = this.goal;
    g.x = x;
    g.h = h;
    g.yaw = yaw;
    g.lean = lean;
    this.attach.position.set(tie * (this.aspect / 2 - 0.07), 0.86, 1);
    if (!this.placed) {
      this.placed = true;
      this.x = x;
      this.h = h;
      this.yaw = yaw;
      this.lean = lean;
    }
  }

  fold() {
    if (this.phase === "fold" || this.phase === "done") return;
    const now = performance.now();
    const up = this.phase === "rise" ? Math.min(1, noteRise(now - this.t0)) : 1;
    this.phase = "fold";
    // Fold from wherever it has got to.
    let ms = 0;
    while (ms < FOLD_MS && noteFold(ms) > up) ms += 8;
    this.t0 = now - ms;
  }

  /**
   * Pose for this frame; true while anything still moves. `toStage` maps
   * world to stage space. `held` (ms into the rise) holds a rising note at
   * that moment, for screenshots of the animation (the probe's hold).
   */
  step(now: number, dt: number, lift: number, toStage: Matrix4, s: Scratch, held: number | null = null): boolean {
    let moving = false;
    const ms = held !== null && this.phase === "rise" ? held : now - this.t0;
    let up: number;
    let sew: number;
    if (this.phase === "rise") {
      up = noteRise(ms);
      sew = sewAt(ms, false);
      if (held === null && ms >= RISE_MS) this.phase = "stand";
      moving = held === null;
    } else if (this.phase === "stand") {
      up = 1;
      sew = 1;
    } else if (this.phase === "fold") {
      up = noteFold(ms);
      sew = sewAt(ms, true);
      if (ms >= FOLD_MS) this.phase = "done";
      moving = true;
    } else {
      return false;
    }

    // Glide to a new placement (the camera moved), quickly.
    const k = 1 - Math.exp(-dt / 90);
    const g = this.goal;
    if (Math.abs(g.x - this.x) + Math.abs(g.h - this.h) + Math.abs(g.yaw - this.yaw) + Math.abs(g.lean - this.lean) > 1e-4) {
      this.x += (g.x - this.x) * k;
      this.h += (g.h - this.h) * k;
      this.yaw += (g.yaw - this.yaw) * k;
      this.lean += (g.lean - this.lean) * k;
      moving = true;
    } else {
      this.x = g.x;
      this.h = g.h;
      this.yaw = g.yaw;
      this.lean = g.lean;
    }

    // The card on its hinge: flat back on the page at 0, leaning a little toward the reader at 1.
    this.root.position.set(this.x, BASE_Y, NOTE_Z);
    this.root.rotation.set(0, this.yaw, 0);
    this.hinge.rotation.x = -(Math.PI / 2) * (1 - up) - this.lean * up;
    this.sizer.scale.set(this.h, this.h, CARD_T);

    // The strut: from the page behind the card to its back, under half way up.
    const a = -this.hinge.rotation.x;
    const reach = 0.42 * this.h;
    const by = reach * Math.cos(a);
    const bz = -reach * Math.sin(a) - CARD_T;
    const foot = -0.16 * this.h;
    this.strut.visible = up > 0.08;
    this.strut.position.set(0, by / 2, (bz + foot) / 2);
    this.strut.rotation.set(Math.atan2(bz - foot, by), 0, 0);
    this.strut.scale.set(0.24 * this.h * this.aspect, Math.hypot(by, bz - foot), CARD_T * 0.8);

    // The thread: from the cutout (lifted with it) to the card's near top corner.
    this.root.updateMatrixWorld(true);
    const A = this.attach.getWorldPosition(s.a).applyMatrix4(toStage);
    const B = s.b.copy(this.anchor);
    B.z += lift * LIFT;
    B.y += lift * LIFT * LIFT_UP;
    const n = Math.round(sew * STITCHES);
    if (n !== this.lastSew || moving) {
      this.lastSew = n;
      this.sewThread(A, B, sew, s);
    }
    return moving;
  }

  private sewThread(A: Vector3, B: Vector3, sew: number, s: Scratch) {
    const len = A.distanceTo(B);
    // A little slack: it sags and bows toward the reader.
    const C = s.c.copy(A).add(B).multiplyScalar(0.5);
    C.y -= 0.16 * len;
    C.z += 0.05 * len;
    const shown = sew * STITCHES;
    const count = Math.min(STITCHES, Math.ceil(shown - 1e-6));
    const seg = 1 / STITCHES;
    for (let i = 0; i < count; i++) {
      // Sewn from the cutout out to the card; the newest stitch is still being drawn through.
      const t0 = i * seg;
      const t1 = t0 + seg * 0.64 * Math.min(1, shown - i);
      bezier(B, C, A, t0, s.p0);
      bezier(B, C, A, t1, s.p1);
      s.dir.subVectors(s.p1, s.p0).normalize();
      s.q.setFromUnitVectors(UP, s.dir);
      s.mid.addVectors(s.p0, s.p1).multiplyScalar(0.5);
      s.scale.set(THREAD_R, Math.max(1e-4, s.p1.distanceTo(s.p0)), THREAD_R);
      s.m.compose(s.mid, s.q, s.scale);
      this.thread.setMatrixAt(i, s.m);
    }
    this.thread.count = count;
    this.thread.instanceMatrix.needsUpdate = true;
    this.knotB.visible = sew > 0;
    this.knotB.position.copy(B);
    this.knotB.scale.setScalar(THREAD_R * 2.3);
    this.knotA.visible = sew >= 1;
    this.knotA.position.copy(A);
    this.knotA.scale.setScalar(THREAD_R * 1.9);
  }

  dispose() {
    for (const p of this.parts) p.removeFromParent();
    this.cardGeo.dispose();
    this.texture.dispose();
    this.face.dispose();
    this.edge.dispose();
    this.threadMat.dispose();
    this.thread.dispose();
  }
}

/* ------------------------------------------------------------------------ */
/* The stage's explain layer                                                 */
/* ------------------------------------------------------------------------ */

interface Target {
  item: ExplainItem;
  /** The invisible pointer target for this cutout. */
  proxy: Object3D;
  hit: Vector3;
  /** Eased lift, 0..1 (HOVER_LIFT under a hovering pointer, 1 with its note open). */
  lift: number;
}

interface Caption {
  at: RefObject<Group | null>;
  width: number;
  depth: number;
}

interface LayerInputs {
  fonts: PageFonts;
  reveal: { readonly current: number };
  caption: Caption | null;
  enabled: boolean;
}

const setCursor = (c: string) => {
  document.body.style.cursor = c;
};

/** Writes the screen rect (canvas CSS px) of a world box into `out`. */
function projectBox(b: Box3, camera: Camera, w: number, h: number, v: Vector3, out: Rect): Rect {
  out.x0 = out.y0 = Infinity;
  out.x1 = out.y1 = -Infinity;
  for (let i = 0; i < 8; i++) {
    v.set(i & 1 ? b.max.x : b.min.x, i & 2 ? b.max.y : b.min.y, i & 4 ? b.max.z : b.min.z).project(camera);
    const x = ((v.x + 1) / 2) * w;
    const y = ((1 - v.y) / 2) * h;
    if (x < out.x0) out.x0 = x;
    if (x > out.x1) out.x1 = x;
    if (y < out.y0) out.y0 = y;
    if (y > out.y1) out.y1 = y;
  }
  return out;
}

/** Grows `out` to take in a world point's screen position. */
function addPoint(v: Vector3, camera: Camera, w: number, h: number, out: Rect) {
  v.project(camera);
  const x = ((v.x + 1) / 2) * w;
  const y = ((1 - v.y) / 2) * h;
  if (x < out.x0) out.x0 = x;
  if (x > out.x1) out.x1 = x;
  if (y < out.y0) out.y0 = y;
  if (y > out.y1) out.y1 = y;
}

/**
 * The imperative half of <ExplainLayer>, one per stage (like the book's
 * controller): its pointer targets, notes, lifts and listeners. React only
 * calls its methods.
 */
class ExplainStage {
  readonly catcher = new Object3D();
  readonly noteHit = new Object3D();
  targets: Target[] = [];
  private items: ExplainItem[] = [];
  private rigs: NoteRig[] = [];
  private layer: Group | null = null;
  private inputs: LayerInputs | null = null;
  private hover: string | null = null;
  /** The press our own targets took, so the book-wide listener leaves it be. */
  private took: Event | null = null;
  private coarse = false;
  private lastT = 0;
  /** The probe holds rising notes at this many ms into their rise (null: they run). */
  private held: number | null = null;
  private readonly camKey = new Float64Array(34);
  private readonly objects = new Map<string, Object3D>();
  /** Geometry every note shares, made with the first note and freed with the layer. */
  private shared: SharedGeometry | null = null;
  private readonly s = {
    a: new Vector3(),
    b: new Vector3(),
    c: new Vector3(),
    p0: new Vector3(),
    p1: new Vector3(),
    mid: new Vector3(),
    dir: new Vector3(),
    scale: new Vector3(),
    q: new Quaternion(),
    m: new Matrix4(),
    ray: new Raycaster(),
    hits: [] as Intersection[],
    v: new Vector3(),
    w: new Vector3(),
    box: new Box3(),
    toStage: new Matrix4(),
    place: placement(),
    target: rect(),
    caption: rect(),
    view: rect(),
    cam: new Vector3(),
  };
  private measureFn: Measure | null = null;
  private liveRegion: HTMLDivElement | null = null;

  constructor(
    private readonly session: ExplainSession,
    private readonly runtime: StageRuntime,
    private readonly get: () => RootState,
  ) {
    this.catcher.name = "explain:stage";
    this.noteHit.name = "explain:note";
    markBookControl(this.catcher);
    markBookControl(this.noteHit);
    const box = new Box3(new Vector3(-1.04, 0, -0.02), new Vector3(1.04, 1.2, 0.9));
    const inv = new Matrix4();
    const ray = new Raycaster();
    const enter = new Vector3();
    this.catcher.raycast = (raycaster, intersects) => {
      const root = this.runtime.root;
      if (this.session.open === null || !root) return;
      inv.copy(root.matrixWorld).invert();
      ray.ray.copy(raycaster.ray).applyMatrix4(inv);
      if (!ray.ray.intersectBox(box, enter)) return;
      enter.applyMatrix4(root.matrixWorld);
      // Behind everything on the stage, so the cutouts and the note get their presses first.
      intersects.push({ distance: raycaster.ray.origin.distanceTo(enter) + 10, point: enter.clone(), object: this.catcher } as Intersection);
    };
    this.noteHit.raycast = (raycaster, intersects) => {
      const rig = this.rigs.find((r) => r.live);
      if (!rig || this.session.open === null) return;
      const hit = pickCutout(rig.cardMesh, raycaster, [], this.s.hits);
      if (hit) intersects.push({ distance: hit.distance, point: hit.point.clone(), object: this.noteHit } as Intersection);
    };
  }

  /* ---------- inputs ---------- */

  /** The explainable cutouts (from explainItems); returns the pointer targets to mount. */
  setItems(items: ExplainItem[]): Target[] {
    if (items === this.items) return this.targets;
    this.items = items;
    for (const t of this.targets) this.runtime.lifts.delete(t.item.target);
    this.targets = items.map((item) => {
      const proxy = new Object3D();
      proxy.name = `explain:${item.target}`;
      markBookControl(proxy);
      const t: Target = { item, proxy, hit: new Vector3(), lift: 0 };
      proxy.raycast = (raycaster, intersects) => this.raycastTarget(t, raycaster, intersects);
      return t;
    });
    return this.targets;
  }

  configure(inputs: LayerInputs) {
    const was = this.inputs;
    this.inputs = inputs;
    if (!was || was.fonts !== inputs.fonts) {
      const { ctx } = canvas(4, 4);
      const hand = inputs.fonts.hand;
      this.measureFn = (text, px) => {
        ctx.font = `500 ${px}px ${hand}`;
        return ctx.measureText(text).width;
      };
      // The heading's weight, ready for the canvas.
      document.fonts?.load(`600 34px ${hand}`).catch(() => {});
    }
    if (!inputs.enabled && this.session.open !== null) this.session.dispatch({ type: "gone" });
    this.get().invalidate();
  }

  setLayer(g: Group | null) {
    this.layer = g;
  }

  /* ---------- what can be explained ---------- */

  /** A cutout's object on the stage (the puppet's root, the prop's group), by target. */
  private objectOf(target: string): Object3D | null {
    const hit = this.objects.get(target);
    if (hit?.parent) return hit;
    const found = this.runtime.root?.getObjectByName(target) ?? null;
    if (found) this.objects.set(target, found);
    return found;
  }

  /** The cutout stands on the set now (inside the wings). */
  private standsOnSet(obj: Object3D): boolean {
    const root = this.runtime.root;
    if (!root) return false;
    const v = obj.getWorldPosition(this.s.w);
    root.worldToLocal(v);
    return onSet(v.x);
  }

  private get clickable(): boolean {
    const i = this.inputs;
    return !!i && i.enabled && i.reveal.current >= 0.98;
  }

  canOpen(target: string): boolean {
    if (!this.clickable || !this.items.some((i) => i.target === target)) return false;
    const obj = this.objectOf(target);
    return !!obj && this.standsOnSet(obj);
  }

  private raycastTarget(t: Target, raycaster: Raycaster, intersects: Intersection[]) {
    if (!this.clickable) return;
    const obj = this.objectOf(t.item.target);
    if (!obj || !this.standsOnSet(obj)) return;
    const st = this.get();
    const slack = this.coarse ? SLACK_TOUCH : SLACK_FINE;
    const hit = pickNear(obj, this.s.ray, raycaster.camera, st.pointer.x, st.pointer.y, st.size, slack, this.runtime.wings, this.s.hits);
    if (hit) intersects.push({ distance: hit.distance, point: t.hit.copy(hit.point), object: t.proxy } as Intersection);
  }

  /* ---------- pointer handlers (from the JSX) ---------- */

  over(t: Target, e: ThreeEvent<PointerEvent>) {
    e.stopPropagation();
    setCursor("pointer");
    if (e.pointerType === "touch") return;
    this.hover = t.item.target;
    this.get().invalidate();
  }

  out(t: Target) {
    if (this.hover === t.item.target) this.hover = null;
    setCursor("");
    this.get().invalidate();
  }

  /** A press our own targets take: the book leaves it alone, and so does the book-wide close. */
  take(e: ThreeEvent<PointerEvent>) {
    e.stopPropagation();
    this.took = e.nativeEvent;
  }

  /**
   * A press on a cutout: it opens (or closes, or swaps to) its note when the
   * pointer comes up within a few px, wherever the cutout has walked to by
   * then (the scene plays under the press).
   */
  press(t: Target, e: ThreeEvent<PointerEvent>) {
    this.take(e);
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const id = e.pointerId;
    const x0 = e.clientX;
    const y0 = e.clientY;
    const target = t.item.target;
    const up = (ev: PointerEvent) => {
      if (ev.pointerId !== id) return;
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      if (ev.type !== "pointerup" || Math.hypot(ev.clientX - x0, ev.clientY - y0) > 12) return;
      this.session.dispatch({ type: "target", target });
      this.get().invalidate();
    };
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }

  pressNote(e: ThreeEvent<PointerEvent>) {
    this.take(e);
    this.session.dispatch({ type: "note" });
    this.get().invalidate();
  }

  pressStage(e: ThreeEvent<PointerEvent>) {
    this.take(e);
    this.session.dispatch({ type: "elsewhere" });
    this.get().invalidate();
  }

  /* ---------- lifetime ---------- */

  /** Listeners, the aria-live line, the session and the probe. Returns the teardown. */
  mount(): () => void {
    const gl = this.get().gl;
    const el = gl.domElement;
    const coarse = window.matchMedia("(pointer: coarse)");
    this.coarse = coarse.matches;
    const down = (ev: PointerEvent) => {
      this.coarse = ev.pointerType === "touch" || coarse.matches;
      if (this.session.open === null || this.took === ev) return;
      if (!(ev.target instanceof Node) || !el.parentElement?.contains(ev.target)) return;
      this.session.dispatch({ type: "elsewhere" });
      this.get().invalidate();
    };
    const key = (ev: KeyboardEvent) => {
      if (ev.key !== "Escape" || this.session.open === null) return;
      // Before the book's own Escape (which steps back to the spread).
      ev.stopPropagation();
      ev.preventDefault();
      this.session.dispatch({ type: "escape" });
      this.get().invalidate();
    };
    // Bubbling, so the stage's own handlers have run (and marked their presses) first.
    window.addEventListener("pointerdown", down);
    window.addEventListener("keydown", key, true);

    // Screen readers hear the note's words.
    const region = document.createElement("div");
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    region.setAttribute("aria-atomic", "true");
    region.dataset.explainLive = "";
    Object.assign(region.style, {
      position: "fixed",
      width: "1px",
      height: "1px",
      margin: "-1px",
      overflow: "hidden",
      clip: "rect(0 0 0 0)",
      whiteSpace: "nowrap",
      border: "0",
      padding: "0",
    });
    document.body.appendChild(region);
    this.liveRegion = region;

    // The session asks this stage what may open (set here, not in the constructor: React may make one to throw away).
    const can = (t: string) => this.canOpen(t);
    this.session.setCan(can);
    const offSession = this.session.subscribe((open, prev) => this.onSession(open, prev));
    const offProbe = registerExplainProbe({
      targets: () => this.items.map((i) => this.where(i.target)).filter((p): p is ExplainPoint => p !== null),
      open: (target) => {
        this.session.dispatch({ type: "open", target });
        this.get().invalidate();
        return this.session.open === target;
      },
      close: () => {
        this.session.dispatch({ type: "open", target: null });
        this.get().invalidate();
      },
      current: () => this.session.open,
      notes: () => this.noteBoxes(),
      hold: (ms) => {
        this.held = ms === null || !Number.isFinite(ms) ? null : Math.max(0, ms);
        this.get().invalidate();
      },
    });

    return () => {
      window.removeEventListener("pointerdown", down);
      window.removeEventListener("keydown", key, true);
      offSession();
      offProbe();
      this.session.setCan(() => false);
      region.remove();
      this.liveRegion = null;
      for (const r of this.rigs) r.dispose();
      this.rigs = [];
      for (const t of this.targets) {
        t.lift = 0;
        this.runtime.lifts.delete(t.item.target);
      }
      if (this.hover) setCursor("");
      this.hover = null;
      const sh = this.shared;
      this.shared = null;
      sh?.stitch.dispose();
      sh?.knot.dispose();
      sh?.strut.dispose();
    };
  }

  private sharedGeometry(): SharedGeometry {
    this.shared ??= {
      // A unit stitch (radius 1, length 1), scaled per stitch.
      stitch: new CylinderGeometry(1, 1, 1, 6, 1, false),
      knot: new SphereGeometry(1, 10, 8),
      strut: new BoxGeometry(1, 1, 1),
    };
    return this.shared;
  }

  /* ---------- notes ---------- */

  private onSession(open: string | null, prev: string | null) {
    if (prev) for (const r of this.rigs) if (r.item.target === prev) r.fold();
    const region = this.liveRegion;
    if (open) {
      const item = this.items.find((i) => i.target === open);
      const obj = this.objectOf(open);
      const root = this.runtime.root;
      const inputs = this.inputs;
      if (item && obj && root && this.layer && inputs && this.measureFn) {
        const rig = new NoteRig(item, layoutNote(item.title, item.text, this.measureFn), inputs.fonts, this.sharedGeometry());
        // The thread's end on the cutout: its middle, two thirds up, on its front.
        cutoutBox(obj, rig.box);
        const t = this.targets.find((x) => x.item.target === open);
        const lifted = (t?.lift ?? 0) * LIFT;
        const b = rig.box;
        const v = this.s.v.set((b.min.x + b.max.x) / 2, b.min.y + 0.64 * (b.max.y - b.min.y), b.max.z);
        root.worldToLocal(v);
        rig.anchor.copy(v);
        rig.anchor.z -= lifted;
        rig.anchor.y -= lifted * LIFT_UP;
        this.layer.add(...rig.parts);
        this.rigs.push(rig);
        this.place(rig);
        paperLift();
      }
      if (region && item) region.textContent = `${item.title}: ${item.text}`;
    } else if (region) region.textContent = "";
    this.get().invalidate();
  }

  /** Where a note stands: screen-space placement (../explain.ts), then its turn toward the camera. */
  private place(rig: NoteRig) {
    const st = this.get();
    const root = this.runtime.root;
    if (!root) return;
    const s = this.s;
    const cam = st.camera;
    const W = st.size.width;
    const H = st.size.height;
    root.updateWorldMatrix(true, false);
    cam.updateMatrixWorld();
    const camStage = root.worldToLocal(s.cam.setFromMatrixPosition(cam.matrixWorld));
    const yawAt = (x: number) => Math.max(-MAX_YAW, Math.min(MAX_YAW, Math.atan2(camStage.x - x, camStage.z - NOTE_Z) * 0.75));
    const leanAt = (x: number, h: number) => {
      const el = Math.atan2(camStage.y - h * 0.5, Math.hypot(camStage.x - x, camStage.z - NOTE_Z));
      return Math.max(0, Math.min(MAX_LEAN, el * 0.5));
    };
    const rectAt: PlaceInput["rectAt"] = (x, h, out) => {
      const yaw = yawAt(x);
      const lean = leanAt(x, h);
      const cy = Math.cos(yaw);
      const sy = Math.sin(yaw);
      out.x0 = out.y0 = Infinity;
      out.x1 = out.y1 = -Infinity;
      for (let i = 0; i < 4; i++) {
        // Card-local (u across, v up) -> leaned back -> turned -> stood at (x, BASE_Y, NOTE_Z).
        const u = (i & 1 ? 0.5 : -0.5) * h * rig.aspect;
        const vUp = i & 2 ? h : 0;
        const ly = vUp * Math.cos(lean);
        const lz = -vUp * Math.sin(lean);
        s.v.set(x + u * cy + lz * sy, BASE_Y + ly, NOTE_Z - u * sy + lz * cy).applyMatrix4(root.matrixWorld);
        addPoint(s.v, cam, W, H, out);
      }
      return out;
    };
    projectBox(rig.box, cam, W, H, s.v, s.target);
    const avoid: Rect[] = [];
    const caption = this.inputs?.caption;
    const at = caption?.at.current;
    if (caption && at) {
      at.updateWorldMatrix(true, false);
      const c = s.caption;
      c.x0 = c.y0 = Infinity;
      c.x1 = c.y1 = -Infinity;
      for (let i = 0; i < 4; i++) {
        s.v.set(((i & 1) - 0.5) * caption.width, 0, (((i >> 1) & 1) - 0.5) * caption.depth);
        addPoint(at.localToWorld(s.v), cam, W, H, c);
      }
      avoid.push(c);
    }
    const view = s.view;
    view.x0 = VIEW_PAD.side;
    view.x1 = W - VIEW_PAD.side;
    view.y0 = VIEW_PAD.top;
    view.y1 = H - VIEW_PAD.bottom;
    const per = rig.layout.height / rig.layout.font;
    const p = placeNote(
      {
        target: s.target,
        view,
        avoid,
        rectAt,
        targetX: rig.anchor.x,
        xMin: -1.0,
        xMax: 1.0,
        wantPx: per * (W < 560 ? READ_PX_PHONE : READ_PX),
        minPx: per * MIN_PX,
        maxH: MAX_H,
        prevX: rig.isPlaced ? rig.x : null,
      },
      s.place,
    );
    rig.setGoal(p.x, p.h, yawAt(p.x), leanAt(p.x, p.h), p.side > 0 ? -1 : 1);
  }

  /** A point on a clickable cutout's paper (client CSS px), or null when it cannot be clicked now. */
  private where(target: string): ExplainPoint | null {
    const obj = this.objectOf(target);
    if (!obj || !this.canOpen(target)) return null;
    const st = this.get();
    const s = this.s;
    const cam = st.camera;
    const r = st.gl.domElement.getBoundingClientRect();
    cutoutBox(obj, s.box);
    const t = projectBox(s.box, cam, st.size.width, st.size.height, s.v, s.target);
    // The box's middle if the paper is there, else the nearest point that is.
    for (let ring = 0; ring < 6; ring++) {
      const n = ring === 0 ? 1 : ring * 8;
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2;
        const sx = t.x0 + (t.x1 - t.x0) * (0.5 + (ring / 6) * 0.45 * Math.cos(a));
        const sy = t.y0 + (t.y1 - t.y0) * (0.45 + (ring / 6) * 0.45 * Math.sin(a));
        const ndcX = (sx / st.size.width) * 2 - 1;
        const ndcY = 1 - (sy / st.size.height) * 2;
        if (pickNear(obj, s.ray, cam, ndcX, ndcY, st.size, 0, this.runtime.wings, s.hits)) return { target, x: r.left + sx, y: r.top + sy };
      }
    }
    return null;
  }

  private noteBoxes(): ExplainNoteBox[] {
    const st = this.get();
    const r = st.gl.domElement.getBoundingClientRect();
    return this.rigs
      .filter((rig) => rig.phase !== "done")
      .map((rig) => {
        const box = new Box3().setFromObject(rig.cardMesh);
        const sr = projectBox(box, st.camera, st.size.width, st.size.height, new Vector3(), rect());
        return { target: rig.item.target, phase: rig.phase, x0: r.left + sr.x0, y0: r.top + sr.y0, x1: r.left + sr.x1, y1: r.top + sr.y1 };
      });
  }

  /* ---------- per frame ---------- */

  frame(state: RootState) {
    const inputs = this.inputs;
    const now = performance.now();
    const dt = this.lastT > 0 ? Math.min(64, now - this.lastT) : 16;
    this.lastT = now;
    if (this.session.open !== null && inputs && inputs.reveal.current < 0.92) this.session.dispatch({ type: "turn" });
    let moving = false;

    // Lifts: the open note's cutout stands off its layer, a hovered one a little.
    const lifts = this.runtime.lifts;
    for (const t of this.targets) {
      const key = t.item.target;
      const goal = this.session.open === key ? 1 : this.hover === key && this.clickable ? HOVER_LIFT : 0;
      if (t.lift === goal) continue;
      t.lift = stepLift(t.lift, goal, dt);
      if (t.lift === 0) lifts.delete(key);
      else lifts.set(key, t.lift * LIFT);
      moving = true;
    }

    if (this.rigs.length > 0) {
      // Re-place the notes only when the camera (or the canvas) has moved.
      const cam = state.camera;
      const k = this.camKey;
      const me = cam.matrixWorld.elements;
      const pe = cam.projectionMatrix.elements;
      let changed = k[32] !== state.size.width || k[33] !== state.size.height;
      for (let i = 0; i < 16; i++) {
        if (Math.abs(k[i] - me[i]) > 1e-5 || Math.abs(k[16 + i] - pe[i]) > 1e-5) changed = true;
        k[i] = me[i];
        k[16 + i] = pe[i];
      }
      k[32] = state.size.width;
      k[33] = state.size.height;
      const root = this.runtime.root;
      if (root) this.s.toStage.copy(root.matrixWorld).invert();
      for (let i = this.rigs.length - 1; i >= 0; i--) {
        const r = this.rigs[i];
        if (changed && r.live) this.place(r);
        let lift = 0;
        for (const t of this.targets) if (t.item.target === r.item.target) lift = t.lift;
        if (r.step(now, dt, lift, this.s.toStage, this.s, this.held)) moving = true;
        if (r.phase === "done") {
          r.dispose();
          this.rigs.splice(i, 1);
        }
      }
    }
    if (moving) {
      state.invalidate();
      markShadowsDirty(state);
    }
  }
}

export interface ExplainLayerProps {
  session: ExplainSession;
  fonts: PageFonts;
  /** The stage's reveal: a turning page closes the note. */
  reveal: { readonly current: number };
  /** The printed caption, for the note to keep clear of: a group at its centre and its size (world units). */
  caption?: Caption | null;
  /** The scene is drawn and may be clicked. */
  enabled: boolean;
}

export function ExplainLayer({ session, fonts, reveal, caption = null, enabled }: ExplainLayerProps) {
  const { scene, cast, runtime } = useScene();
  const get = useThree((s) => s.get);
  const [stage] = useState(() => new ExplainStage(session, runtime, get));

  const items = useMemo(
    () =>
      explainItems(scene.timeline.explanations, scene, {
        puppets: cast.puppets.map((p) => p.id),
        props: cast.props.map((p) => p.prop.id),
      }),
    [scene, cast],
  );
  const targets = useMemo(() => stage.setItems(items), [stage, items]);

  useEffect(() => stage.configure({ fonts, reveal, caption, enabled }), [stage, fonts, reveal, caption, enabled]);
  useEffect(() => stage.mount(), [stage]);
  useFrame((state) => stage.frame(state));

  if (items.length === 0) return null;
  return (
    <group ref={(g) => stage.setLayer(g)} name="explain">
      {targets.map((t) => (
        <primitive
          key={t.item.target}
          object={t.proxy}
          onPointerOver={(e: ThreeEvent<PointerEvent>) => stage.over(t, e)}
          onPointerOut={() => stage.out(t)}
          onPointerMove={(e: ThreeEvent<PointerEvent>) => e.stopPropagation()}
          onPointerDown={(e: ThreeEvent<PointerEvent>) => stage.press(t, e)}
        />
      ))}
      <primitive
        object={stage.noteHit}
        onPointerOver={(e: ThreeEvent<PointerEvent>) => {
          e.stopPropagation();
          setCursor("pointer");
        }}
        onPointerOut={() => setCursor("")}
        onPointerMove={(e: ThreeEvent<PointerEvent>) => e.stopPropagation()}
        onPointerDown={(e: ThreeEvent<PointerEvent>) => stage.pressNote(e)}
      />
      <primitive
        object={stage.catcher}
        onPointerMove={(e: ThreeEvent<PointerEvent>) => e.stopPropagation()}
        onPointerDown={(e: ThreeEvent<PointerEvent>) => stage.pressStage(e)}
      />
    </group>
  );
}
