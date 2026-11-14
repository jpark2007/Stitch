"use client";

/**
 * <TimelineScene>: a compiled timeline played as a pop-up scene, inside an
 * existing <Canvas>. The stage stays a dumb player: everything on it comes
 * from the Timeline's tracks and the scene data the tracks name.
 *
 *   const { controller } = useTimelinePlayer(scene.timeline, { narrator, autoplay: true });
 *   <Canvas> … <TimelineScene controller={controller} scene={scene} size={[w, d]} /> … </Canvas>
 *
 * Composition (each binding reads its own slots of the shared frame):
 *   SetLayers       set layers rising and folding on their fold tracks,
 *                   and paper risers under raised marks
 *   PuppetActor     one per puppet:* target, feet planted when the timeline
 *                   is not
 *   PropBinding     one per prop:* target, held by grip / resting by anchor
 *   EffectBindings  Timeline.effects (paper particles, time of day)
 *   CameraBinding   the camera tracks, with the drag-orbit that snaps back
 *                   (only with `camera`; a host with its own camera, like
 *                   the book, leaves it off)
 *
 * Layout: the scene stands centred on a page `size` = [width, depth] world
 * units (x across, z towards the reader, y up off the paper), scaled so the
 * stage's two units of width fill most of the page. It adds no lights and no
 * page: the host's light it (the standalone <Stage> or the book).
 *
 * In the book's right-page slot (Book3D renderStage):
 *
 *   renderStage={({ spread, reveal, width, depth }) => (
 *     <SpreadStage scene={scenes[spread.id]} reveal={reveal} size={[width, depth]} />
 *   )}
 *   function SpreadStage({ scene, reveal, size }) {
 *     const { controller } = useTimelinePlayer(scene.timeline, { autoplay: true });
 *     return <TimelineScene controller={controller} scene={scene} size={size} reveal={reveal} />;
 *   }
 *
 * The book keeps its own camera (leave `camera` off) and can show the words
 * with <CaptionStrip controller> or print them from controller.getStatus().
 */

import { useMemo, type ReactNode } from "react";
import { DEFAULT_STAGE_SIZE, type StageSize } from "../CameraRig";
import type { PlaybackController } from "../playback";
import type { OpenSource } from "../SetPiece";
import { CameraBinding } from "./CameraBinding";
import { EffectBindings } from "./EffectBindings";
import type { StageGrade } from "./grade";
import { SetLayers } from "./LayerBinding";
import { PropBinding } from "./PropBinding";
import { PuppetActor } from "./PuppetActor";
import { SceneProvider, useScene } from "./SceneProvider";
import type { SceneData } from "./scene";

/** Page width per stage width: the stage's 2 units fill 2 / 2.4 of the page. */
const PAGE_PER_STAGE = 2.4;
/** Page depth the stage needs, in stage units (back fold to just past the front layer). */
const STAGE_DEPTH = 1.6;

/**
 * One actor per puppet target and one binding per prop target (./cast); a
 * tracked prop the scene data does not describe still plays, as a
 * hand-sized placeholder card.
 */
function Cast() {
  const { cast } = useScene();
  return (
    <>
      {cast.puppets.map((p) => (
        <PuppetActor key={p.id} id={p.id} index={p.index} person={p.person} />
      ))}
      {cast.props.map(({ prop }) => (
        <PropBinding key={prop.id} prop={prop} />
      ))}
    </>
  );
}

/** World units per stage unit, and where stage (0, 0, 0) sits, on a page of `size`. */
export function stageLayout(size: StageSize = DEFAULT_STAGE_SIZE) {
  const scale = Math.min(size[0] / PAGE_PER_STAGE, size[1] / STAGE_DEPTH);
  // Centre the set (back fold z = 0 to the front layer, ~0.8) on the page.
  const origin = [0, 0, -0.4 * scale] as const;
  return { scale, origin };
}

export interface TimelineSceneProps {
  controller: PlaybackController;
  scene: SceneData;
  /** The page the stage stands on, world units [width, depth]. Default the standalone stage page. */
  size?: StageSize;
  /** Drive the camera from the timeline's camera tracks. Default false (the host owns the camera). */
  camera?: boolean;
  /** With `camera`: allow the small drag-orbit that eases back. Default true. */
  orbit?: boolean;
  /** With `camera`: lens-shift the picture up by this share of the screen (room for captions). Default 0. */
  lift?: number;
  /** With `camera`: lens zoom; below 1 steps the picture back a little (room for title and captions). Default 1. */
  zoom?: number;
  /** Tick the controller from the render loop. Default true (see SceneProvider). */
  tick?: boolean;
  /**
   * Cap every fold at this (0..1; a number, a function of scene time, or a
   * ref): pass the book's `reveal` so the scene folds with the page turn.
   */
  reveal?: OpenSource;
  /** Called once the scene is fully drawn (all art in, shadows settled). */
  onReady?: () => void;
  /**
   * The host lights' time-of-day grade (createGrade(), applied by <StageLights
   * grade>): time_of_day effects re-light the lamp through it. Without one
   * they add a soft tinted fill.
   */
  grade?: StageGrade | null;
  /** Extra things in stage space (debug markers, probes). */
  children?: ReactNode;
}

export function TimelineScene({
  controller,
  scene,
  size,
  camera = false,
  orbit = true,
  lift = 0,
  zoom = 1,
  tick = true,
  reveal,
  onReady,
  grade = null,
  children,
}: TimelineSceneProps) {
  const { scale, origin } = useMemo(() => stageLayout(size), [size]);
  return (
    <SceneProvider controller={controller} scene={scene} scale={scale} origin={origin} tick={tick} reveal={reveal} onReady={onReady}>
      <SetLayers />
      <Cast />
      <EffectBindings grade={grade} />
      {camera && <CameraBinding orbit={orbit} lift={lift} zoom={zoom} />}
      {children}
    </SceneProvider>
  );
}
