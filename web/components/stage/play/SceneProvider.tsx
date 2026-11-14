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
import { Matrix4, Plane, Vector3, type Group, type Material, type Mesh, type Object3D } from "three";
import { api, bareMediaId } from "@/lib/api";
import type { Person } from "@/lib/types";
import { markShadowsDirty, StageClock } from "../clock";
import type { PlaybackController, PlaybackFrame } from "../playback";
import type { PuppetHandle, SocketName } from "../Puppet";
import { PropRegistry, type SocketResolver } from "../props/sockets";
import { RIG } from "../puppet/rig";
import type { SceneData } from "./scene";

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

  private readonly localWings = this.wings.map((p) => p.clone());
  private readonly puppets = new Map<string, RefObject<PuppetHandle | null>>();
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
  /** `media/<id>` -> URL (null when it cannot be resolved, e.g. a local sample). */
  resolveMedia(mediaId: string): string | null;
  /** A person by id (with a fallback sheet for cast missing from the data). */
  person(id: string): Person;
}

const SceneContext = createContext<SceneContextValue | null>(null);

/** The enclosing SceneProvider. Throws outside one. */
export function useScene(): SceneContextValue {
  const v = useContext(SceneContext);
  if (!v) throw new Error("useScene: no <SceneProvider> above this component");
  return v;
}

/** Warm stand-in colours for cast the scene data does not describe. */
const FALLBACK_COLORS = ["#8b5a7c", "#5b82a6", "#7f9873", "#d3a03d", "#b9573d"];

function personLookup(scene: SceneData): (id: string) => Person {
  const people = new Map(scene.people.map((p) => [p.id, p]));
  const fallback = new Map<string, Person>();
  return (id) => {
    const hit = people.get(id) ?? fallback.get(id);
    if (hit) return hit;
    const made: Person = {
      id,
      familyId: scene.familyId ?? "",
      name: id,
      character: { color: FALLBACK_COLORS[fallback.size % FALLBACK_COLORS.length], build: "average" },
    };
    fallback.set(id, made);
    return made;
  };
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
   */
  tick?: boolean;
  children?: ReactNode;
}

const ORIGIN = [0, 0, 0] as const;

export function SceneProvider({ controller, scene, scale = 1, origin = ORIGIN, tick = true, children }: SceneProviderProps) {
  const [runtime] = useState(() => new StageRuntime());
  const get = useThree((s) => s.get);
  const rootRef = useRef<Group>(null);
  // Before any child's effects or frames ask for sockets.
  useLayoutEffect(() => {
    runtime.setRoot(rootRef.current);
    return () => runtime.setRoot(null);
  }, [runtime]);

  // Local clipping is off by default in three; the wings need it.
  useEffect(() => {
    const gl = get().gl;
    const was = gl.localClippingEnabled;
    gl.localClippingEnabled = true;
    return () => {
      gl.localClippingEnabled = was;
    };
  }, [get]);

  const value = useMemo<SceneContextValue>(() => {
    const familyId = scene.familyId;
    return {
      controller,
      frame: controller.frame,
      scene,
      feetPlanted: scene.timeline.feetPlanted === true,
      runtime,
      resolveMedia: (mediaId) => (familyId ? api.mediaUrl(familyId, bareMediaId(mediaId)) : null),
      person: personLookup(scene),
    };
  }, [controller, scene, runtime]);

  const time = useMemo(() => () => controller.time, [controller]);
  const lastVersion = useRef(-1);

  useFrame((state) => {
    if (tick) controller.tick(performance.now());
    if (controller.frameVersion !== lastVersion.current) {
      lastVersion.current = controller.frameVersion;
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
