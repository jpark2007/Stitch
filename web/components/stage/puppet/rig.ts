/**
 * The shared rig, poses and builds, typed. Read from shared/ (the contracts)
 * at build time through the `@shared/*` alias; never copied.
 */

import puppetJson from "@shared/rig/puppet.json";
import posesJson from "@shared/rig/poses.json";
import vocabularyJson from "@shared/vocabulary.json";
import type { CharacterSheet } from "@/lib/types";
import { boneOrder, puppetScale, type RigDef, type Rotations } from "./kinematics";
import { applyPose, compileCycle, sampleCycle, type CompiledCycle, type PoseLibrary } from "./poses";

export const RIG = puppetJson as unknown as RigDef;
export const POSES = posesJson as unknown as PoseLibrary;

/** Every bone name, parents before children. */
export const BONES: readonly string[] = boneOrder(RIG);

/** Pose names in poses.json order. */
export const POSE_NAMES: readonly string[] = Object.keys(POSES.poses);

export type Build = NonNullable<CharacterSheet["build"]>;
export const BUILDS = vocabularyJson.builds as Record<Build, number>;
export const SPEEDS = vocabularyJson.speeds as Record<"walk" | "run" | "drive", number>;

export type SocketName = "hand_l" | "hand_r" | "head";
export const SOCKETS = Object.keys(RIG.sockets) as SocketName[];

/** Stage units per rig unit for a build (average 0.5, tall 0.55). */
export function scaleForBuild(build: Build | null | undefined): number {
  return puppetScale(RIG, BUILDS[build ?? "average"] ?? 1);
}

/**
 * Every bone's angle for a named pose into `out` (unlisted bones 0). Returns
 * the pose's rootOffset y in rig units (sit and kneel lower the hip; the
 * compiler bakes it into root.position, so only standalone callers need it).
 */
export function poseRotations(name: string, out: Rotations): number {
  const pose = POSES.poses[name];
  applyPose(pose, BONES, out);
  return pose?.rootOffset?.[1] ?? 0;
}

/** A locomotion cycle's angles at `phase` (0..1, wraps) into `out`. */
export function cycleRotations(name: "walk" | "run", phase: number, out: Rotations, smooth = true): Rotations {
  return sampleCycle(getCycle(name), phase, out, smooth);
}

const cycles = new Map<string, CompiledCycle>();

/** A locomotion cycle ("walk", "run"), compiled once. */
export function getCycle(name: "walk" | "run"): CompiledCycle {
  let c = cycles.get(name);
  if (!c) {
    c = compileCycle(POSES.cycles[name], BONES);
    cycles.set(name, c);
  }
  return c;
}
