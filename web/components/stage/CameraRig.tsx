"use client";

/**
 * The stage camera.
 *
 * Poses are plain data: where the camera sits, what it looks at, and its lens.
 * Presets and moves are pure functions over poses (reading view, wide, dolly
 * to a target, pan, close-up, pull back), so a player can build any camera
 * track from them without this file knowing the timeline format.
 *
 * <CameraRig pose={...}> drives the default camera:
 *   - a pose object: the camera glides to it with critically damped smoothing
 *     whenever it changes;
 *   - a function of stage time: followed exactly (deterministic frames).
 * With `orbit`, dragging swings the camera a little around its target (soft
 * limits, rubber-banded) and it eases back to the pose on release.
 */

import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useRef } from "react";
import { PerspectiveCamera, Vector3 } from "three";
import { useStageTime } from "./clock";
import { clamp, easeInOutSine, lerp, smoothDamp, type Ease, type Vec3 } from "./timeline";

export interface CameraPose {
  position: Vec3;
  target: Vec3;
  /** Vertical field of view in degrees. Default 30. */
  fov?: number;
}

export type CameraPath = (t: number) => CameraPose;

/** Page size of the stage: width (x) by depth (z), world units. */
export type StageSize = [number, number];

export const DEFAULT_STAGE_SIZE: StageSize = [2.4, 3.2];

/* ------------------------------------------------------------------------ */
/* Presets and moves                                                         */
/* ------------------------------------------------------------------------ */

/** The two resting framings of a stage page. */
export function stagePoses(size: StageSize = DEFAULT_STAGE_SIZE) {
  const [w, d] = size;
  const s = Math.max(w / 2.4, d / 3.2);
  return {
    /** Reading view: the whole diorama, from just above the reader's eye line. */
    reading: { position: [0, 1.55 * s, 4.6 * s], target: [0, 0.52 * s, -0.25 * s], fov: 30 } as CameraPose,
    /** Wide: higher and further back, the page and some of the table. */
    wide: { position: [1.1 * s, 2.75 * s, 6.3 * s], target: [-0.05 * s, 0.38 * s, -0.25 * s], fov: 30 } as CameraPose,
  };
}

const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a: Vec3, k: number): Vec3 => [a[0] * k, a[1] * k, a[2] * k];
const len = (a: Vec3) => Math.hypot(a[0], a[1], a[2]);

/** Look at `target` from `distance` away, `height` above it, `yaw` radians off-axis. */
export function closeUp(target: Vec3, { distance = 1.6, height = 0.35, yaw = 0, fov = 30 } = {}): CameraPose {
  const flat = Math.sqrt(Math.max(0, distance * distance - height * height));
  return {
    position: [target[0] + Math.sin(yaw) * flat, target[1] + height, target[2] + Math.cos(yaw) * flat],
    target,
    fov,
  };
}

/**
 * Dolly towards a new target: the camera keeps its viewing direction's height
 * ratio, moves closer by `amount` (0 = no move, 1 = all the way to `minDistance`)
 * and re-aims at `target`.
 */
export function dollyTo(from: CameraPose, target: Vec3, amount: number, minDistance = 1.1): CameraPose {
  const off = sub(from.position, from.target);
  const dist = len(off);
  const next = lerp(dist, minDistance, clamp(amount));
  return { position: add(target, scale(off, next / dist)), target, fov: from.fov };
}

/** Slide camera and target together (a truck/pedestal move). */
export function pan(from: CameraPose, delta: Vec3): CameraPose {
  return { position: add(from.position, delta), target: add(from.target, delta), fov: from.fov };
}

/** Back away from the target along the view line (`factor` > 1). */
export function pullBack(from: CameraPose, factor = 1.5): CameraPose {
  const off = sub(from.position, from.target);
  return { position: add(from.target, scale(off, factor)), target: from.target, fov: from.fov };
}

/** Blend two poses. Position and target move in straight lines. */
export function blendPose(a: CameraPose, b: CameraPose, k: number): CameraPose {
  return {
    position: [lerp(a.position[0], b.position[0], k), lerp(a.position[1], b.position[1], k), lerp(a.position[2], b.position[2], k)],
    target: [lerp(a.target[0], b.target[0], k), lerp(a.target[1], b.target[1], k), lerp(a.target[2], b.target[2], k)],
    fov: lerp(a.fov ?? 30, b.fov ?? 30, k),
  };
}

/** A camera stop: be at `pose` at time `at`, arriving with `ease`. */
export interface CameraStop {
  at: number;
  pose: CameraPose;
  ease?: Ease;
}

/** Holds each pose and eases between consecutive stops. Stops must be sorted. */
export function cameraPath(stops: readonly CameraStop[]): CameraPath {
  return (t) => {
    if (t <= stops[0].at) return stops[0].pose;
    for (let i = 1; i < stops.length; i++) {
      const b = stops[i];
      if (t < b.at) {
        const a = stops[i - 1];
        const k = (b.ease ?? easeInOutSine)((t - a.at) / (b.at - a.at));
        return blendPose(a.pose, b.pose, k);
      }
    }
    return stops[stops.length - 1].pose;
  };
}

/* ------------------------------------------------------------------------ */
/* Rig                                                                       */
/* ------------------------------------------------------------------------ */

export interface CameraRigProps {
  pose: CameraPose | CameraPath;
  /** Glide time when `pose` (an object) changes, seconds. Default 1.1. */
  smoothTime?: number;
  /** Allow a small drag-orbit that eases back to the pose. Default true. */
  orbit?: boolean;
  /** Orbit limits in radians. */
  maxYaw?: number;
  maxPitch?: number;
}

type Spring = [number, number];

export function CameraRig({ pose, smoothTime = 1.1, orbit = true, maxYaw = 0.42, maxPitch = 0.16 }: CameraRigProps) {
  const now = useStageTime();
  // r3f state is mutable three.js objects: read it through get() in effects
  // and from the frame state in useFrame, never as React values.
  const get = useThree((s) => s.get);

  // Smoothed pose (for object poses): 7 springs (position, target, fov).
  const smooth = useRef<Spring[] | null>(null);
  // Orbit offsets: current springs and the drag target.
  const yaw = useRef<Spring>([0, 0]);
  const pitch = useRef<Spring>([0, 0]);
  const drag = useRef({ active: false, x: 0, y: 0, yaw: 0, pitch: 0 });
  const tmp = useRef({ off: new Vector3(), right: new Vector3(), up: new Vector3(0, 1, 0), target: new Vector3() });

  useEffect(() => {
    if (!orbit) return;
    const el = get().gl.domElement;
    const d = drag.current;
    const soft = (raw: number, max: number) => max * Math.tanh(raw / max);
    // Down on the canvas, move/up on the window: a drag that leaves the
    // canvas (or the window) still tracks and still lets go.
    let id = -1;
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0 || d.active) return;
      d.active = true;
      id = e.pointerId;
      d.x = e.clientX;
      d.y = e.clientY;
      el.style.cursor = "grabbing";
    };
    const onMove = (e: PointerEvent) => {
      if (!d.active || e.pointerId !== id) return;
      const sx = 2.4 / Math.max(320, el.clientWidth);
      d.yaw = soft(-(e.clientX - d.x) * sx, maxYaw);
      d.pitch = soft((e.clientY - d.y) * sx * 0.6, maxPitch);
    };
    const onUp = (e: PointerEvent) => {
      if (!d.active || e.pointerId !== id) return;
      d.active = false;
      id = -1;
      d.yaw = 0;
      d.pitch = 0;
      el.style.cursor = "grab";
    };
    const onBlur = () => {
      if (!d.active) return;
      d.active = false;
      id = -1;
      d.yaw = 0;
      d.pitch = 0;
      el.style.cursor = "grab";
    };
    el.style.cursor = "grab";
    el.style.touchAction = "none";
    el.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    window.addEventListener("blur", onBlur);
    return () => {
      el.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      window.removeEventListener("blur", onBlur);
      el.style.cursor = "";
    };
  }, [get, orbit, maxYaw, maxPitch]);

  useFrame((state, dt) => {
    let p: CameraPose;
    if (typeof pose === "function") {
      p = pose(now(state));
    } else {
      const goal = [...pose.position, ...pose.target, pose.fov ?? 30];
      if (!smooth.current) smooth.current = goal.map((v) => [v, 0] as Spring);
      const s = smooth.current;
      for (let i = 0; i < 7; i++) smoothDamp(s[i], goal[i], smoothTime, dt);
      p = { position: [s[0][0], s[1][0], s[2][0]], target: [s[3][0], s[4][0], s[5][0]], fov: s[6][0] };
    }

    const d = drag.current;
    const st = d.active ? 0.09 : 0.5;
    const yw = smoothDamp(yaw.current, d.yaw, st, dt);
    const pt = smoothDamp(pitch.current, d.pitch, st, dt);

    const { off, right, up, target } = tmp.current;
    const camera = state.camera;
    target.set(p.target[0], p.target[1], p.target[2]);
    off.set(p.position[0] - p.target[0], p.position[1] - p.target[1], p.position[2] - p.target[2]);
    if (yw !== 0 || pt !== 0) {
      off.applyAxisAngle(up, yw);
      right.crossVectors(up, off).normalize();
      // Keep the camera above the page whatever the drag does.
      const elev = Math.asin(clamp(off.y / off.length(), -1, 1));
      const pitchClamped = clamp(pt, 0.08 - elev, 1.25 - elev);
      off.applyAxisAngle(right, -pitchClamped);
    }
    camera.position.copy(target).add(off);
    camera.lookAt(target);
    if (camera instanceof PerspectiveCamera && Math.abs(camera.fov - (p.fov ?? 30)) > 1e-3) {
      camera.fov = p.fov ?? 30;
      camera.updateProjectionMatrix();
    }
  });

  return null;
}
