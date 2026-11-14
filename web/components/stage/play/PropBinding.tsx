"use client";

/**
 * <PropBinding>: one prop, driven by its `prop:<propId>` tracks.
 *
 * Binds the prop's frame slot once, then every frame copies parent, position,
 * rotation, state and part rotations into a mutable PropPose that a
 * <PropActor> places: on the ground by its anchor, in a hand by its grip, on
 * a head or another prop's socket by its anchor (CONTRACTS.md "Props" and
 * Clarifications 3 and 8). The parent string is the frame's pre-parsed
 * ParentRef `raw`, so a parent swap is a pointer change, not a new string.
 */

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import { Matrix4, Vector3, type Group } from "three";
import type { Prop } from "@/lib/types";
import {
  PropActor,
  createPropPose,
  setParent,
  setPartRotation,
  setPosition,
  setRotation,
  setState,
  type PropActorHandle,
} from "../PropActor";
import { PRIORITY, standStill, standUp, tiltAbout, useScene, useWingClip } from "./SceneProvider";
import type { Mount } from "./standing";

export interface PropBindingProps {
  prop: Prop;
}

export function PropBinding({ prop }: PropBindingProps) {
  const { frame, runtime, standing, resolveMedia } = useScene();
  const pf = useMemo(() => frame.propsById.get(prop.id) ?? null, [frame, prop.id]);
  const pose = useMemo(() => createPropPose({ parent: "ground", position: [0, 0, 0] }), []);
  const actor = useRef<PropActorHandle>(null);
  const outer = useRef<Group>(null);
  const inner = useRef<Group>(null);
  const mount = useMemo<Mount>(() => ({ angle: 0, dy: 0, dz: 0 }), []);
  const scratch = useMemo(() => ({ m: new Matrix4(), v: new Vector3() }), []);

  // A missing image resolves to an empty string: the actor draws its
  // placeholder card in prop.color instead.
  const resolveArt = useMemo(() => (mediaId: string) => resolveMedia(mediaId) ?? "", [resolveMedia]);

  useFrame(() => {
    if (!pf) return;
    const p = pf.position;
    if (pose.parent !== pf.parent.raw) setParent(pose, pf.parent.raw, p[0], p[1], p[2]);
    else setPosition(pose, p[0], p[1], p[2]);
    setRotation(pose, pf.rotation);
    if (pose.state !== pf.state) setState(pose, pf.state);
    const parts = pf.parts;
    for (let i = 0; i < parts.length; i++) setPartRotation(pose, parts[i], pf.partRotations[i]);
    // On the page it stands up with the set. On a puppet it already follows
    // the socket's position; while the holder lies folded it also tilts flat
    // with them (a held prop otherwise keeps its own upright orientation).
    const parent = pf.parent;
    if (parent.kind === "ground") {
      standUp(standing, outer.current, inner.current, p[0], p[1], p[2], mount);
      return;
    }
    const holder = parent.kind === "puppet" && parent.id ? frame.puppetsById.get(parent.id) : undefined;
    if (holder && parent.target && parent.socket) {
      const h = holder.position;
      standing.mount(h[0], h[1], h[2], mount);
      if (mount.angle !== 0 && runtime.resolvePuppetSocket(parent.target, parent.socket, scratch.m)) {
        scratch.v.setFromMatrixPosition(scratch.m);
        tiltAbout(outer.current, inner.current, scratch.v.x, scratch.v.y, scratch.v.z, mount.angle);
        return;
      }
    }
    standStill(outer.current, inner.current);
  }, PRIORITY.pose);

  useWingClip(() => actor.current?.object);
  // Its art (or placeholder) must be drawable before the scene is ready.
  useEffect(() => (pf ? runtime.addReadyCheck(() => actor.current?.ready ?? false) : undefined), [runtime, pf]);

  if (!pf) return null;
  return (
    <group ref={outer}>
      <group ref={inner}>
        <PropActor
          ref={actor}
          prop={prop}
          pose={pose}
          resolveSocket={runtime.resolvePuppetSocket}
          registry={runtime.registry}
          resolveArt={resolveArt}
        />
      </group>
    </group>
  );
}
