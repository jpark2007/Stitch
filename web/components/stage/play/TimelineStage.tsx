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

import { memo, type ReactNode } from "react";
import type { PlaybackController } from "../playback";
import { Stage } from "../Stage";
import { TimelineScene } from "./TimelineScene";
import type { SceneData } from "./scene";

export interface TimelineStageProps {
  controller: PlaybackController;
  scene: SceneData;
  className?: string;
  /** Allow the small drag-orbit. Default true. */
  orbit?: boolean;
  /** Soft depth of field focused on the camera target. Default false. */
  dof?: boolean;
  /** Extra things in the Canvas outside the stage (frame probes). */
  overlay?: ReactNode;
}

export const TimelineStage = memo(function TimelineStage({ controller, scene, className, orbit = true, dof = false, overlay }: TimelineStageProps) {
  return (
    <Stage className={className} camera={null} dof={dof} overlay={overlay}>
      <TimelineScene controller={controller} scene={scene} camera orbit={orbit} />
    </Stage>
  );
});
