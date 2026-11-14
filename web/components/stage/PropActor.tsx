"use client";

/**
 * PropActor: one prop on the stage, as an actor (ARCHITECTURE.md section 1
 * "Props", CONTRACTS.md "Props").
 *
 * It draws the prop as an alpha-cut paper card, `sizes[size]` stage units tall
 * and `aspect` times as wide: the art from its URL, or, when the art is null,
 * a placeholder card in `prop.color` with a silhouette for its size class.
 * Parts (wheels, lids) are separate cards that turn about their `at` point;
 * states swap the art.
 *
 * Driving it. Pass a mutable PropPose and change it in place every frame
 * (the timeline player writes it; see props/poseTrack for a sampler). The
 * actor reads it in useFrame, so moving a prop never re-renders React:
 *
 *   const pose = useMemo(() => createPropPose({ parent: "ground", position: [0, 0, 0.5] }), []);
 *   useFrame(() => samplePropTracks(tracks, time(), pose));
 *   <PropActor prop={prop} pose={pose} registry={registry} resolveSocket={puppetSockets} />
 *
 * Placement, from `pose.parent`:
 *   "ground"                 the anchor sits on `pose.position` (stage space)
 *   "puppet:<id>.<socket>"   the grip sits on that socket, offset by
 *   "prop:<id>.<socket>"     `pose.position` in the socket's space
 * `pose.rotation` (radians about z) turns the card about the anchor or grip.
 * `pose.state` picks the art; `pose.parts[name]` turns each part.
 *
 * Mount it in stage space (the group whose units are CONTRACTS.md stage
 * units); it sets its own matrix there. Socket matrices come from
 * `resolveSocket` (puppets; see props/sockets for the exact contract) and from
 * `registry` (other props). The actor registers its own sockets (car seats, a
 * tabletop) with `registry` under `prop.id`.
 */

import { useEffect, useImperativeHandle, useMemo, useRef, type Ref } from "react";
import { useFrame } from "@react-three/fiber";
import type { Group, Matrix4, Vector3 } from "three";
import vocabulary from "@shared/vocabulary.json";
import type { Prop } from "@/lib/types";
import { PropRig } from "./props/propRig";
import type { HeldOrientation, PropPose } from "./props/rig";
import type { PropRegistry, SocketResolver } from "./props/sockets";

export type { PropPose, HeldOrientation } from "./props/rig";
export type { SocketResolver } from "./props/sockets";
export {
  createPropPose,
  setParent,
  setPartRotation,
  setPosition,
  setRotation,
  setState,
} from "./props/rig";
export { compilePropTracks, samplePropTracks, type PropTracks } from "./props/poseTrack";
export { PropRegistry, toStageSpace } from "./props/sockets";

const SIZES: Readonly<Record<string, number>> = vocabulary.sizes;

export interface PropActorHandle {
  /** The prop's root group (its matrix is the placement, in stage space). */
  readonly object: Group | null;
  /** True once the art (or placeholder) is drawable. */
  readonly ready: boolean;
  /** Place the prop from its pose now (useFrame does this every frame; call
   *  it after changing the pose outside a frame, e.g. before a still). */
  update(): boolean;
  /** A socket of this prop (`prop.sockets`) in stage space. */
  socketMatrix(socket: string, out: Matrix4): boolean;
  /** Where an image fraction of the prop is in stage space. `pointAt(0.5, 1)`
   *  with the default anchor is where its bottom touches down. */
  pointAt(u: number, v: number, out: Vector3): Vector3;
}

export interface PropActorProps {
  prop: Prop;
  /** Mutable placement, read every frame. Mutate it; do not replace it. */
  pose: PropPose;
  /** Puppet sockets (and anything else that is not a registered prop). */
  resolveSocket?: SocketResolver;
  /** The stage's props, for "prop:<id>.<socket>" parents and to publish this
   *  prop's own sockets. */
  registry?: PropRegistry;
  /** Media id -> URL. Default: the art value is already a URL. Keep it stable. */
  resolveArt?: (mediaId: string) => string;
  /** "upright" (default) keeps a held prop level; "socket" turns it with the
   *  hand. */
  heldOrientation?: HeldOrientation;
  /** Held props sit this far toward the viewer from the socket, so they are in
   *  front of the hand. Default 0.012 stage units. */
  heldDepthBias?: number;
  /** On a "prop:<id>.<socket>" parent (a seat, a tabletop, a roof) the prop
   *  rests on the socket by its anchor (default), or hangs by its grip. A
   *  puppet's socket always takes the grip. */
  propSocketPivot?: "anchor" | "grip";
  castShadow?: boolean;
  receiveShadow?: boolean;
  /** useFrame priority; keep it <= 0 (positive takes over rendering). Puppets
   *  should update first (a lower number). Default 0. */
  priority?: number;
  ref?: Ref<PropActorHandle>;
}

export function PropActor({
  prop,
  pose,
  resolveSocket,
  registry,
  resolveArt,
  heldOrientation = "upright",
  heldDepthBias = 0.012,
  propSocketPivot = "anchor",
  castShadow = true,
  receiveShadow = false,
  priority = 0,
  ref,
}: PropActorProps) {
  const groupRef = useRef<Group>(null);
  const rigRef = useRef<PropRig | null>(null);
  const artRef = useRef(resolveArt);

  // Rebuild only when the prop's data changes, not when a parent re-creates
  // an equal object.
  const propKey = useMemo(() => JSON.stringify(prop), [prop]);

  useEffect(() => {
    artRef.current = resolveArt;
  }, [resolveArt]);

  // The rig is built in an effect (balanced mount/unmount, also under
  // StrictMode) and owns every GPU resource it creates.
  useEffect(() => {
    const root = groupRef.current;
    if (!root) return;
    const data = JSON.parse(propKey) as Prop;
    const rig = new PropRig(root, data, {
      sizes: SIZES,
      resolveArt: artRef.current,
      castShadow,
      receiveShadow,
    });
    rigRef.current = rig;
    return () => {
      rigRef.current = null;
      rig.dispose();
    };
  }, [propKey, castShadow, receiveShadow]);

  // Inputs that change without rebuilding.
  useEffect(() => {
    const rig = rigRef.current;
    if (!rig) return;
    rig.pose = pose;
    rig.orientation = heldOrientation;
    rig.depthBias = heldDepthBias;
    rig.propSocketPivot = propSocketPivot;
  });

  // Prop sockets come from the registry; everything else from resolveSocket
  // (or the registry's own fallback when none is given).
  const resolver = useMemo<SocketResolver | null>(() => {
    if (!registry) return resolveSocket ?? null;
    if (!resolveSocket) return registry.resolve;
    return (target, socket, out) =>
      target.startsWith("prop:") && registry.has(target.slice(5))
        ? registry.resolve(target, socket, out)
        : resolveSocket(target, socket, out);
  }, [registry, resolveSocket]);

  useEffect(() => {
    const rig = rigRef.current;
    if (rig) rig.resolver = resolver;
  });

  useEffect(() => {
    const rig = rigRef.current;
    if (!registry || !rig) return;
    return registry.register(rig.prop.id, rig);
  }, [registry, propKey]);

  useImperativeHandle(
    ref,
    () => ({
      get object() {
        return groupRef.current;
      },
      get ready() {
        return rigRef.current?.ready ?? false;
      },
      update: () => rigRef.current?.update() ?? false,
      socketMatrix: (socket, out) => rigRef.current?.socketMatrix(socket, out) ?? false,
      pointAt: (u, v, out) => (rigRef.current ? rigRef.current.pointAt(u, v, out) : out.set(0, 0, 0)),
    }),
    [],
  );

  useFrame(() => {
    rigRef.current?.update();
  }, Math.min(0, priority));

  return <group ref={groupRef} name={`prop:${prop.id}`} />;
}
