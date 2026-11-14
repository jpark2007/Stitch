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

import { useMemo } from "react";
import { Vector3 } from "three";
import { CameraRig, type CameraPath } from "../CameraRig";
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
}

export function CameraBinding({ orbit = true }: CameraBindingProps) {
  const { frame, runtime } = useScene();
  const path = useMemo(() => timelineCameraPath(frame, runtime), [frame, runtime]);
  return <CameraRig pose={path} orbit={orbit} />;
}
