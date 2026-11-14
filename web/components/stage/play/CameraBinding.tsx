"use client";

/**
 * <CameraBinding>: the camera from the timeline's `camera` tracks
 * (position, lookAt, fov; the player already eases position and lookAt with
 * smoothstep per key segment and keeps fov linear, CONTRACTS.md
 * Clarifications 4).
 *
 * The tracks are in stage space; the binding maps them through the stage
 * group's world matrix, so the shot frames the set wherever (and at whatever
 * scale) the stage is mounted. It feeds the existing <CameraRig> as a camera
 * path, which keeps everything the rig already does: fitting the lens to
 * narrow screens, depth-of-field focus, and the limited drag-orbit that
 * eases back to the shot on release (DESIGN.md "orbit a little ... then snap
 * back").
 */

import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import { Vector3, type PerspectiveCamera } from "three";
import { CameraRig, narrowness, type CameraPath } from "../CameraRig";
import type { PlaybackFrame } from "../playback";
import { useScene, type StageRuntime } from "./SceneProvider";

/**
 * A camera path that reads the frame's camera and maps it to world space.
 * It writes into one reused pose object, so following the camera allocates
 * nothing per frame.
 */
export function timelineCameraPath(frame: PlaybackFrame, runtime: StageRuntime): CameraPath {
  const position: [number, number, number] = [0, 0, 0];
  const target: [number, number, number] = [0, 0, 0];
  const pose = { position, target, fov: 30 };
  const v = new Vector3();
  return () => {
    const cam = frame.camera;
    runtime.toWorld(v.set(cam.position[0], cam.position[1], cam.position[2]));
    position[0] = v.x;
    position[1] = v.y;
    position[2] = v.z;
    runtime.toWorld(v.set(cam.lookAt[0], cam.lookAt[1], cam.lookAt[2]));
    target[0] = v.x;
    target[1] = v.y;
    target[2] = v.z;
    pose.fov = cam.fov;
    return pose;
  };
}

export interface CameraBindingProps {
  /** Drag to orbit a little, easing back on release. Default true. */
  orbit?: boolean;
  /**
   * Shift the picture up by this share of the screen height (a lens shift,
   * not a tilt: perspective is unchanged), leaving the bottom of the frame
   * for captions laid over it. Default 0.
   */
  lift?: number;
  /**
   * Lens zoom (below 1 widens the picture a little around its centre, like
   * stepping back), so the shot sits inside the room the title and captions
   * leave. Eases back to 1 on portrait screens, which have that room
   * already. Default 1.
   */
  zoom?: number;
}

export function CameraBinding({ orbit = true, lift = 0, zoom = 1 }: CameraBindingProps) {
  const { frame, runtime } = useScene();
  const path = useMemo(() => timelineCameraPath(frame, runtime), [frame, runtime]);
  const camera = useThree((s) => s.camera);
  const last = useRef({ w: 0, h: 0, lift: Number.NaN, zoom: Number.NaN });

  useFrame((state) => {
    const cam = state.camera as PerspectiveCamera;
    if (!cam.isPerspectiveCamera) return;
    const { width: w, height: h } = state.size;
    const l = last.current;
    if (l.w === w && l.h === h && l.lift === lift && l.zoom === zoom) return;
    l.w = w;
    l.h = h;
    l.lift = lift;
    l.zoom = zoom;
    // Narrow screens already widen the lens to hold the shot's width (the
    // rig's fitLens), leaving room above and below: no step back there.
    const k = narrowness(w / Math.max(1, h));
    cam.zoom = zoom + (1 - zoom) * k;
    if (lift) cam.setViewOffset(w, h, 0, Math.round(lift * h), w, h);
    else cam.clearViewOffset();
    cam.updateProjectionMatrix();
  });
  useEffect(
    () => () => {
      const cam = camera as PerspectiveCamera;
      if (!cam.isPerspectiveCamera) return;
      cam.zoom = 1;
      cam.clearViewOffset();
    },
    [camera],
  );

  return <CameraRig pose={path} orbit={orbit} />;
}
