"use client";

/**
 * <SceneProvider>: the shared state of one timeline stage, for the entity
 * bindings below it (PuppetActor, PropBinding, LayerBinding, CameraBinding,
 * EffectBindings).
 *
 * Declarative composition: the provider owns no entities. It exposes the
 * player's live frame by reference (a PlaybackFrame mutated in place, never
 * React state) and a few shared services; each binding resolves its own
 * tracks once (puppetsById, boneIndex, layersByTarget, ...) and reads its
 * slots from the shared frame in its own useFrame. Nothing re-renders React
 * while the scene plays.
 *
 * Per animation frame, in useFrame priority order (lower runs first):
 *   PRIORITY.tick     the provider ticks the controller (fresh sample this
 *                     frame), marks shadow maps dirty when the frame changed,
 *                     and moves the wing clipping planes with the stage
 *   PRIORITY.drive    puppets copy root and bone slots into their drivers
 *   PRIORITY.pose     props copy parent / position / rotation / state / parts
 *   0                 Puppet, PropActor and SetPiece apply themselves (a prop
 *                     asks the puppet registry for its socket, which re-applies
 *                     the puppet's latest inputs first, so order is safe)
 *
 * Space: children are mounted in a group in CONTRACTS.md stage space (x -1..1
 * across, y up from the page, z towards the viewer from the back fold),
 * scaled by `scale` and moved to `origin` inside whatever group the provider
 * sits in: the standalone stage's page or the book's right-page slot.
 *
 * Stage time: everything below reads the player's scene time through
 * <StageClock source>, so set hinges, paper particles and the camera follow
 * play, pause, scrub and ?t freezes exactly.
 */

import { useFrame, useThree } from "@react-three/fiber";
import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import {
  Matrix4,
  Plane,
  Vector3,
  type Camera,
  type Group,
  type Material,
  type Mesh,
  type Object3D,
  type Scene,
  type WebGLRenderer,
} from "three";
import { api, bareMediaId } from "@/lib/api";
import type { Person } from "@/lib/types";
import { markShadowsDirty, StageClock } from "../clock";
import { readOpen, type OpenSource } from "../SetPiece";
import type { PlaybackController, PlaybackFrame } from "../playback";
import type { PuppetHandle, SocketName } from "../Puppet";
import { PropRegistry, type SocketResolver } from "../props/sockets";
import { RIG } from "../puppet/rig";
import { castScene, personLookup, type SceneCast } from "./cast";
import type { SceneData } from "./scene";
import { Standing, type Mount } from "./standing";

/** useFrame priorities of the timeline stage (all <= 0: r3f keeps rendering). */
export const PRIORITY = { tick: -4, drive: -3, pose: -2 } as const;

/** Half-width of the stage's wings: puppets and props are cut off beyond it. */
export const WING_X = 1.0;

/**
 * The imperative half of a stage: its stage-space root, the puppet handles,
 * socket resolution and the wing planes. One per provider, never re-created.
 */
export class StageRuntime {
  /** The stage-space group every entity is mounted in (set by the provider). */
  root: Group | null = null;
  /** The stage's props, for "prop:<id>.<socket>" parents (falls back to puppets). */
  readonly registry: PropRegistry;
  /** World-space clipping planes at the wings, kept current every frame. */
  readonly wings: readonly Plane[] = [new Plane(new Vector3(-1, 0, 0), WING_X), new Plane(new Vector3(1, 0, 0), WING_X)];
  /**
   * Opt-in lifts, by target ("puppet:<id>" / "prop:<id>"): the offset (stage
   * units) a cutout stands off its place, for a host that picks one up (the
   * book's click-to-explain lifts it toward its camera, so the cutout stays
   * under the pointer and its shadow lengthens). The host owns the vectors
   * and may change them in place. Empty by default, so a stage no one lifts
   * plays exactly as authored. The host invalidates while it changes a lift.
   */
  readonly lifts = new Map<string, Readonly<Vector3>>();
  /**
   * How each cutout stands up with the page now, by target: the mount its
   * binding writes every frame (angle 0 upright .. π/2 lying flat with a
   * folded layer). For a host to read (the book's click-to-explain leaves a
   * cutout lying flat be); the stage never reads it.
   */
  readonly mounts = new Map<string, Readonly<Mount>>();

  private readonly localWings = this.wings.map((p) => p.clone());
  private readonly puppets = new Map<string, RefObject<PuppetHandle | null>>();
  private readonly readyChecks = new Set<() => boolean>();
  private readonly tmp = new Matrix4();
  private readonly inv = new Matrix4();

  constructor() {
    this.registry = new PropRegistry(this.resolvePuppetSocket);
  }

  readonly setRoot = (g: Group | null) => {
    this.root = g;
  };

  /** Puppet handles by person id, for socket lookups. Returns the unregister. */
  registerPuppet(id: string, handle: RefObject<PuppetHandle | null>): () => void {
    this.puppets.set(id, handle);
    return () => {
      if (this.puppets.get(id) === handle) this.puppets.delete(id);
    };
  }

  /** A puppet socket in stage space ("puppet:<id>", "hand_r", out). */
  readonly resolvePuppetSocket: SocketResolver = (target, socket, out) => {
    if (!target.startsWith("puppet:") || !Object.hasOwn(RIG.sockets, socket)) return false;
    const handle = this.puppets.get(target.slice(7))?.current;
    const stage = this.root;
    if (!handle || !stage) return false;
    handle.socketWorldMatrix(socket as SocketName, this.tmp);
    stage.updateWorldMatrix(true, false);
    this.inv.copy(stage.matrixWorld).invert();
    out.multiplyMatrices(this.inv, this.tmp);
    return true;
  };

  /**
   * Something still loading (layer art, prop art) registers a check that
   * returns true once it is drawable (or has given up). Returns the unregister.
   */
  addReadyCheck(check: () => boolean): () => void {
    this.readyChecks.add(check);
    return () => this.readyChecks.delete(check);
  }

  /** Every registered check passes. */
  get loaded(): boolean {
    for (const c of this.readyChecks) if (!c()) return false;
    return true;
  }

  /** A stage-space point into world space, in place. */
  toWorld(v: Vector3): Vector3 {
    return this.root ? v.applyMatrix4(this.root.matrixWorld) : v;
  }

  /** Refresh the root's world matrix and move the wing planes with it. */
  update(): void {
    const g = this.root;
    if (!g) return;
    g.updateWorldMatrix(true, false);
    for (let i = 0; i < this.wings.length; i++) this.wings[i].copy(this.localWings[i]).applyMatrix4(g.matrixWorld);
  }
}

export interface SceneContextValue {
  controller: PlaybackController;
  /** The controller's live frame. Mutated in place; read it in useFrame. */
  frame: PlaybackFrame;
  scene: SceneData;
  /** The timeline already plants feet (Timeline.feetPlanted). */
  feetPlanted: boolean;
  runtime: StageRuntime;
  /** How puppets and grounded props stand up with the page (./standing). */
  standing: Standing;
  /** `media/<id>` -> URL (null when it cannot be resolved, e.g. a local sample). */
  resolveMedia(mediaId: string): string | null;
  /** A person by id (with a fallback sheet for cast missing from the data). */
  person(id: string): Person;
  /** Who and what the timeline binds: puppets, props, sets, and any issues (./cast). */
  cast: SceneCast;
}

const SceneContext = createContext<SceneContextValue | null>(null);

/** The enclosing SceneProvider. Throws outside one. */
export function useScene(): SceneContextValue {
  const v = useContext(SceneContext);
  if (!v) throw new Error("useScene: no <SceneProvider> above this component");
  return v;
}

export interface SceneProviderProps {
  controller: PlaybackController;
  scene: SceneData;
  /** World units per stage unit. Default 1. */
  scale?: number;
  /** Where stage-space (0, 0, 0) sits in the parent group. Default the origin. */
  origin?: readonly [number, number, number];
  /**
   * Tick the controller from the render loop (default true), so every frame
   * is sampled right before it is drawn. Harmless alongside
   * useTimelinePlayer's own loop: a second tick in the same frame is a no-op.
   * With useTimelinePlayer({ clock: "manual" }) this is the only clock, and
   * a frameloop="demand" Canvas idles while the scene is paused: the
   * provider invalidates on every player change and every frame it plays.
   */
  tick?: boolean;
  /**
   * An outer cap on every fold, 0..1: a number, a function of scene time, or
   * a ref the host writes every frame (the book's `reveal`, so the scene folds
   * flat as a leaf turns over it and rises again when it lies open).
   */
  reveal?: OpenSource;
  /**
   * Called once, when the scene is fully drawn: every piece of art has loaded
   * (or given up) and a few frames have rendered, so shadows have settled.
   * Headless captures wait for it.
   */
  onReady?: () => void;
  children?: ReactNode;
}

/** Frames to draw before calling a scene ready (shadow maps and first uploads settle). */
const READY_FRAMES = 3;
/** Give up waiting for slow art after this long (ms) and call it ready anyway. */
const READY_TIMEOUT = 10000;

const ORIGIN = [0, 0, 0] as const;

/** The reveal as a plain reader, or null when there is none. */
function revealReader(reveal: OpenSource | undefined, controller: PlaybackController): (() => number) | null {
  if (reveal === undefined) return null;
  return () => readOpen(reveal, controller.time);
}

export function SceneProvider({
  controller,
  scene,
  scale = 1,
  origin = ORIGIN,
  tick = true,
  reveal,
  onReady,
  children,
}: SceneProviderProps) {
  const [runtime] = useState(() => new StageRuntime());
  const get = useThree((s) => s.get);
  const rootRef = useRef<Group>(null);
  // Before any child's effects or frames ask for sockets.
  useLayoutEffect(() => {
    runtime.setRoot(rootRef.current);
    return () => runtime.setRoot(null);
  }, [runtime]);

  // Local clipping is off by default in three; the wings need it. It is left
  // on at unmount: it only affects materials that carry clipping planes, and
  // another stage in the same Canvas (the book mid-turn) may still need it.
  useEffect(() => {
    get().gl.localClippingEnabled = true;
  }, [get]);

  const value = useMemo<SceneContextValue>(() => {
    const familyId = scene.familyId;
    const person = personLookup(scene);
    return {
      controller,
      frame: controller.frame,
      scene,
      feetPlanted: scene.timeline.feetPlanted === true,
      runtime,
      standing: new Standing(scene.sets, controller.frame, revealReader(reveal, controller)),
      resolveMedia: (mediaId) => (familyId ? api.mediaUrl(familyId, bareMediaId(mediaId)) : null),
      person,
      cast: castScene(scene, controller.frame, person),
    };
  }, [controller, scene, runtime, reveal]);

  // Scene data the timeline names but does not describe: say so once.
  const { cast } = value;
  useEffect(() => {
    if (cast.issues.length) console.warn(`[stage] ${scene.id}: the scene data is missing pieces\n  ${cast.issues.join("\n  ")}`);
  }, [cast, scene.id]);

  const time = useMemo(() => () => controller.time, [controller]);
  const lastVersion = useRef(-1);
  const lastReveal = useRef(Number.NaN);
  const readyRef = useRef(onReady);
  useEffect(() => {
    readyRef.current = onReady;
  }, [onReady]);
  const ready = useRef({ frames: 0, since: 0, done: false, compiled: false });

  // A frameloop="demand" Canvas (the book) draws only when asked: ask on
  // every status change (play, pause, seek, teller) and every new frame.
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => {
    const offStatus = controller.subscribe(() => invalidate());
    const offFrame = controller.onFrame(() => invalidate());
    invalidate();
    return () => {
      offStatus();
      offFrame();
    };
  }, [controller, invalidate]);

  // Readiness: a few frames drawn with every piece of art in. After the
  // first such frame (materials have their wing planes by then) every
  // program the scene will need is compiled, so nothing hitches mid-scene
  // the moment a hidden set, puppet or prop first shows.
  useFrame((state) => {
    const r = ready.current;
    if (r.done) return;
    if (r.since === 0) r.since = performance.now();
    if (!runtime.loaded && performance.now() - r.since < READY_TIMEOUT) {
      r.frames = 0;
      state.invalidate();
      return;
    }
    if (!r.compiled && r.frames > 0) {
      r.compiled = true;
      precompile(state.gl, state.scene, state.camera, rootRef.current);
    }
    state.invalidate();
    if (++r.frames < READY_FRAMES) return;
    r.done = true;
    readyRef.current?.();
  });

  useFrame((state) => {
    if (tick) controller.tick(performance.now());
    // Keep a demand-driven Canvas drawing while the scene plays.
    if (controller.getStatus().playing) state.invalidate();
    const r = reveal === undefined ? 1 : readOpen(reveal, controller.time);
    if (controller.frameVersion !== lastVersion.current || r !== lastReveal.current) {
      lastVersion.current = controller.frameVersion;
      lastReveal.current = r;
      // Puppets, props and layers move with the frame: redraw shadow maps.
      markShadowsDirty(state);
    }
    runtime.update();
  }, PRIORITY.tick);

  return (
    <SceneContext.Provider value={value}>
      <StageClock source={time}>
        <group ref={rootRef} position={origin as [number, number, number]} scale={scale} name="timeline-stage">
          {children}
        </group>
      </StageClock>
    </SceneContext.Provider>
  );
}

/**
 * Compiles every shader program `scene` needs, with everything under `root`
 * shown for the moment (sets of later shots, cast waiting offstage, folded
 * pieces), then puts their visibility back. Nothing is drawn in between.
 */
function precompile(gl: WebGLRenderer, scene: Scene, camera: Camera, root: Object3D | null): void {
  const hidden: Object3D[] = [];
  root?.traverse((o) => {
    if (o.visible) return;
    hidden.push(o);
    o.visible = true;
  });
  try {
    gl.compile(scene, camera);
  } finally {
    for (const o of hidden) o.visible = false;
  }
}

/**
 * Cut an entity off at the stage's wings: every mesh material under `object`
 * clips (and casts shadows clipped) against `planes`. Materials can be
 * swapped later (prop states, recoloured puppets), so callers re-run it now
 * and then; it only touches a material whose planes differ.
 */
export function clipToWings(object: Object3D | null | undefined, planes: readonly Plane[]): void {
  if (!object) return;
  object.traverse((o) => {
    const mesh = o as Mesh;
    if (!mesh.isMesh) return;
    const mats = mesh.material as Material | Material[];
    if (Array.isArray(mats)) for (const m of mats) clipMaterial(m, planes);
    else clipMaterial(mats, planes);
  });
}

function clipMaterial(m: Material | undefined, planes: readonly Plane[]) {
  if (!m || m.clippingPlanes === planes) return;
  m.clippingPlanes = planes as Plane[];
  m.clipShadows = true;
  m.needsUpdate = true;
}

/**
 * Stand something at stage (x, y, z) up with the page: `outer` and `inner`
 * are two nested groups around it (outer > inner > thing). With the set
 * risen they are the identity; while it folds they hinge the thing about
 * (x, y, z) and carry it with the riser it stands on. `m` is scratch.
 */
export function standUp(standing: Standing, outer: Group | null, inner: Group | null, x: number, y: number, z: number, m: Mount): void {
  if (!outer || !inner) return;
  standing.mount(x, y, z, m);
  if (m.angle === 0 && m.dy === 0 && m.dz === 0) {
    outer.position.set(0, 0, 0);
    outer.rotation.x = 0;
    inner.position.set(0, 0, 0);
    return;
  }
  outer.position.set(x, y + m.dy, z + m.dz);
  outer.rotation.x = m.angle;
  inner.position.set(-x, -y, -z);
}

/** Lists a cutout's mount on the runtime (StageRuntime.mounts). Returns the unregister. */
export function registerMount(runtime: StageRuntime, target: string, m: Readonly<Mount>): () => void {
  runtime.mounts.set(target, m);
  return () => {
    if (runtime.mounts.get(target) === m) runtime.mounts.delete(target);
  };
}

/** Turns the two stand-up groups into a tilt of `angle` (about x) around stage point (x, y, z). */
export function tiltAbout(outer: Group | null, inner: Group | null, x: number, y: number, z: number, angle: number): void {
  if (!outer || !inner) return;
  outer.position.set(x, y, z);
  outer.rotation.x = angle;
  inner.position.set(-x, -y, -z);
}

/** Puts the two stand-up groups back to the identity (for something held in a hand). */
export function standStill(outer: Group | null, inner: Group | null): void {
  if (outer) {
    outer.position.set(0, 0, 0);
    outer.rotation.x = 0;
  }
  if (inner) inner.position.set(0, 0, 0);
}

/** Re-applies clipToWings to `target()` every `every` frames (and on the first few). */
export function useWingClip(target: () => Object3D | null | undefined, every = 30) {
  const { runtime } = useScene();
  const n = useRef(0);
  useFrame(() => {
    const k = n.current++;
    if (k > 3 && k % every !== 0) return;
    clipToWings(target(), runtime.wings);
  });
}
