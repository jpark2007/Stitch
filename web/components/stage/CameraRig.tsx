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
 * Poses are framed for a landscape screen (REF_ASPECT); on narrower screens
 * the rig widens the lens (fitFov) and looks at a pose's `narrowTarget`.
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
  /** Vertical field of view in degrees, as framed on a landscape screen
   *  (REF_ASPECT). Narrower screens widen it to keep the sides in view. Default 30. */
  fov?: number;
  /** Where to look instead on a narrow (portrait) screen, which cannot fit
   *  everything `target` centres. Default: `target`. */
  narrowTarget?: Vec3;
  /** The share of the landscape framing's width the narrow framing needs to
   *  keep in view (0..1]. Default 1. */
  narrowWidth?: number;
}

export type CameraPath = (t: number) => CameraPose;

/** Page size of the stage: width (x) by depth (z), world units. */
export type StageSize = [number, number];

export const DEFAULT_STAGE_SIZE: StageSize = [2.4, 3.2];

/** The screen shape poses are framed for (1440x900). */
export const REF_ASPECT = 1.6;
/** The widest vertical FOV a narrow screen may widen a pose to, degrees. */
const MAX_FOV = 65;
/** At or below this aspect a pose looks fully at its `narrowTarget`. */
const NARROW_ASPECT = 0.75;

const DEG = Math.PI / 180;

/**
 * The vertical FOV that shows a landscape-framed pose's horizontal extent on a
 * screen of `aspect` (width / height): unchanged at REF_ASPECT and wider,
 * widened on narrower screens, capped at MAX_FOV. `width` < 1 asks to keep only
 * that share of the landscape width in view.
 */
export function fitFov(fov: number, aspect: number, width = 1): number {
  const ref = REF_ASPECT * width;
  if (!(aspect > 0) || aspect >= ref) return fov;
  const wide = (2 * Math.atan((Math.tan((fov * DEG) / 2) * ref) / aspect)) / DEG;
  return Math.min(Math.max(fov, MAX_FOV), wide);
}

/** How far a screen of `aspect` is towards portrait: 0 at REF_ASPECT, 1 at NARROW_ASPECT. */
export function narrowness(aspect: number): number {
  const k = clamp((REF_ASPECT - aspect) / (REF_ASPECT - NARROW_ASPECT));
  return k * k * (3 - 2 * k);
}

/* ------------------------------------------------------------------------ */
/* Presets and moves                                                         */
/* ------------------------------------------------------------------------ */

/**
 * The resting framings of a stage page. The stage page is the right-hand page
 * of the open book; the left page (and the spine at x = -w/2) lie to its left.
 */
export function stagePoses(size: StageSize = DEFAULT_STAGE_SIZE) {
  const [w, d] = size;
  const s = Math.max(w / 2.4, d / 3.2);
  return {
    /** Reading view: the whole diorama, from just above the reader's eye line. */
    reading: { position: [0.1 * s, 1.65 * s, 4.7 * s], target: [0, 0.5 * s, -0.25 * s], fov: 30 } as CameraPose,
    /** Wide: a three-quarter view from above of the whole open book on the
     *  table, centred between the spread and the set. */
    wide: {
      position: [0.7 * s, 4.7 * s, 6.5 * s],
      target: [-0.36 * w, 0.2 * s, 0.1 * s],
      // A phone cannot fit the spread: frame the set on the stage page.
      narrowTarget: [0, 0.2 * s, 0.1 * s],
      narrowWidth: 0.55,
      fov: 30,
    } as CameraPose,
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
  return {
    position: add(from.position, delta),
    target: add(from.target, delta),
    narrowTarget: from.narrowTarget && add(from.narrowTarget, delta),
    narrowWidth: from.narrowWidth,
    fov: from.fov,
  };
}

/** Back away from the target along the view line (`factor` > 1). */
export function pullBack(from: CameraPose, factor = 1.5): CameraPose {
  const off = sub(from.position, from.target);
  return { position: add(from.target, scale(off, factor)), target: from.target, narrowTarget: from.narrowTarget, narrowWidth: from.narrowWidth, fov: from.fov };
}

const mix = (a: Vec3, b: Vec3, k: number): Vec3 => [lerp(a[0], b[0], k), lerp(a[1], b[1], k), lerp(a[2], b[2], k)];

/** Blend two poses. Position and target move in straight lines. */
export function blendPose(a: CameraPose, b: CameraPose, k: number): CameraPose {
  const pose: CameraPose = {
    position: mix(a.position, b.position, k),
    target: mix(a.target, b.target, k),
    fov: lerp(a.fov ?? 30, b.fov ?? 30, k),
  };
  if (a.narrowTarget || b.narrowTarget) pose.narrowTarget = mix(a.narrowTarget ?? a.target, b.narrowTarget ?? b.target, k);
  if (a.narrowWidth !== undefined || b.narrowWidth !== undefined) pose.narrowWidth = lerp(a.narrowWidth ?? 1, b.narrowWidth ?? 1, k);
  return pose;
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

  // Smoothed pose (for object poses): 11 springs (position, target, fov,
  // narrow target and width).
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
      const goal = [...pose.position, ...pose.target, pose.fov ?? 30, ...(pose.narrowTarget ?? pose.target), pose.narrowWidth ?? 1];
      if (!smooth.current) smooth.current = goal.map((v) => [v, 0] as Spring);
      const s = smooth.current;
      for (let i = 0; i < goal.length; i++) smoothDamp(s[i], goal[i], smoothTime, dt);
      p = {
        position: [s[0][0], s[1][0], s[2][0]],
        target: [s[3][0], s[4][0], s[5][0]],
        fov: s[6][0],
        narrowTarget: [s[7][0], s[8][0], s[9][0]],
        narrowWidth: s[10][0],
      };
    }

    const d = drag.current;
    const st = d.active ? 0.09 : 0.5;
    const yw = smoothDamp(yaw.current, d.yaw, st, dt);
    const pt = smoothDamp(pitch.current, d.pitch, st, dt);

    const { off, right, up, target } = tmp.current;
    const camera = state.camera;
    // Fit the pose to the screen: on narrow screens slide the camera over to
    // the pose's narrow target (keeping its angle) and widen the lens.
    const aspect = state.size.width / Math.max(1, state.size.height);
    const k = p.narrowTarget ? narrowness(aspect) : 0;
    const look = k > 0 && p.narrowTarget ? mix(p.target, p.narrowTarget, k) : p.target;
    target.set(look[0], look[1], look[2]);
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
    // What the camera looks at is what stays sharp (see DepthOfField).
    camera.userData.focus = off.length();
    const fov = fitFov(p.fov ?? 30, aspect, lerp(1, p.narrowWidth ?? 1, narrowness(aspect)));
    if (camera instanceof PerspectiveCamera && Math.abs(camera.fov - fov) > 1e-3) {
      camera.fov = fov;
      camera.updateProjectionMatrix();
    }
  });

  return null;
}
