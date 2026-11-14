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
import { useMemo, useRef } from "react";
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
import { PRIORITY, useScene, useWingClip } from "./SceneProvider";

export interface PropBindingProps {
  prop: Prop;
}

export function PropBinding({ prop }: PropBindingProps) {
  const { frame, runtime, resolveMedia } = useScene();
  const pf = useMemo(() => frame.propsById.get(prop.id) ?? null, [frame, prop.id]);
  const pose = useMemo(() => createPropPose({ parent: "ground", position: [0, 0, 0] }), []);
  const actor = useRef<PropActorHandle>(null);

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
  }, PRIORITY.pose);

  useWingClip(() => actor.current?.object);

  if (!pf) return null;
  return (
    <PropActor
      ref={actor}
      prop={prop}
      pose={pose}
      resolveSocket={runtime.resolvePuppetSocket}
      registry={runtime.registry}
      resolveArt={resolveArt}
    />
  );
}
