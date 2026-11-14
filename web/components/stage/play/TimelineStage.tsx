"use client";

/**
 * <TimelineStage>: a standalone Canvas that plays a timeline on the open
 * book on the table (the <Stage> look: warm lamp, paper, soft shadows), with
 * the camera from the timeline's camera tracks and the drag-orbit that eases
 * back. For the render route; the book mounts <TimelineScene> in its own
 * Canvas instead.
 *
 * Memoised: it renders once per controller and scene, so caption and
 * control updates around it never touch the 3D tree.
 */

import { memo, useMemo, type ReactNode } from "react";
import type { PlaybackController } from "../playback";
import { Stage } from "../Stage";
import { createGrade } from "./grade";
import { TimelineScene } from "./TimelineScene";
import type { SceneData } from "./scene";

export interface TimelineStageProps {
  controller: PlaybackController;
  scene: SceneData;
  className?: string;
  /** Allow the small drag-orbit. Default true. */
  orbit?: boolean;
  /** Lens-shift the picture up by this share of the screen, leaving room for captions. Default 0. */
  lift?: number;
  /** Soft depth of field focused on the camera target. Default false. */
  dof?: boolean;
  /** Called once the scene is fully drawn (all art in, shadows settled). Keep it stable. */
  onReady?: () => void;
  /** Extra things in the Canvas outside the stage (frame probes). */
  overlay?: ReactNode;
}

export const TimelineStage = memo(function TimelineStage({
  controller,
  scene,
  className,
  orbit = true,
  lift = 0,
  dof = false,
  onReady,
  overlay,
}: TimelineStageProps) {
  // The timeline's time of day re-lights the lamp through this shared grade.
  const grade = useMemo(() => createGrade(), []);
  return (
    <Stage className={className} camera={null} dof={dof} overlay={overlay} grade={grade}>
      <TimelineScene controller={controller} scene={scene} camera orbit={orbit} lift={lift} onReady={onReady} grade={grade} />
    </Stage>
  );
});
