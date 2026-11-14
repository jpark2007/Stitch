"use client";

/**
 * <MomentStage>: a moment's compiled timeline, played as the pop-up on the
 * right page of the book (Book3D renderStage), with its words printed under
 * it and its pull tabs in the page.
 *
 *   const { controller } = useTimelinePlayer(scene.timeline, { clock: "manual", audio: false });
 *   <Book3D renderStage={({ spread, reveal, view, fonts, width, depth }) => (
 *     <MomentStage controller={controller} scene={scene} spread={spread} fonts={fonts}
 *                  reveal={reveal} view={view} size={[width, depth]} />
 *   )} />
 *
 * The player lives with the page (one per moment, outside the Canvas) so the
 * story page's teller labels can switch the telling. This component decides
 * only when it plays:
 *
 *   - the set rises with the page: the book's `reveal` caps every fold, and
 *     the timeline (whose t = 0 is the set beginning to rise) starts from 0
 *     once the spread lies open and still and the scene is drawn;
 *   - it folds as the page turns away: the player pauses, and plays from the
 *     start again the next time the spread opens;
 *   - on a phone, which reads one page at a time, it waits for the camera to
 *     reach the stage page, and pauses (resuming in place) while the reader
 *     is back on the story page.
 */

import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import type { StageSize } from "@/components/stage/CameraRig";
import { FpsProbe, TimelineScene, stageLayout, type SceneData } from "@/components/stage/play";
import type { PlaybackController } from "@/components/stage/playback";
import { PAGE_W } from "../leafShape";
import type { PageFonts } from "../paperArt";
import { STAGE_SLOT } from "../StoryPage";
import type { BookSpread, BookTeller } from "../types";
import { PrintedCaption } from "./PrintedCaption";
import { PullTabLever } from "./PullTabLever";

export interface MomentStageProps {
  controller: PlaybackController;
  scene: SceneData;
  spread: BookSpread;
  fonts: PageFonts;
  /** The book's reveal and stage view (StageSlotArgs). */
  reveal: { readonly current: number };
  view: { readonly current: number };
  /** The slot, world units [width, depth]. */
  size: StageSize;
  /** Hold the scene at this time once it opens (screenshots), instead of playing. */
  freezeAt?: number | null;
  /** Called once the scene is fully drawn. */
  onReady?: () => void;
}

/** The spread must lie this open, and this much in view, before the scene starts. */
const OPEN_TO_PLAY = 0.97;
const VIEW_TO_PLAY = 0.6;
/** Below this the page has started to turn away: pause and re-arm. */
const OPEN_TO_REARM = 0.4;
const VIEW_TO_PAUSE = 0.3;
/** The page's centre line in slot space. */
const CENTRE_X = PAGE_W / 2 - STAGE_SLOT.x;

export function MomentStage({ controller, scene, spread, fonts, reveal, view, size, freezeAt = null, onReady }: MomentStageProps) {
  const tellers: BookTeller[] = useMemo(() => spread.tellings ?? [], [spread]);
  const gate = useRef({ ready: false, armed: true, awayPaused: false });
  const [ready, setReady] = useState(false);
  const readyRef = useRef(onReady);
  useEffect(() => {
    readyRef.current = onReady;
  }, [onReady]);
  const invalidate = useThree((s) => s.invalidate);
  const onSceneReady = useMemo(
    () => () => {
      gate.current.ready = true;
      setReady(true);
      readyRef.current?.();
      // The book draws on demand: one more frame, for the gate to open on.
      invalidate();
    },
    [invalidate],
  );

  // A stage folded away (unmounted) never plays on unseen.
  useEffect(() => () => controller.pause(), [controller]);

  useFrame((state) => {
    const g = gate.current;
    const r = reveal.current;
    const v = view.current;
    if (r < OPEN_TO_REARM) {
      if (!g.armed) {
        controller.pause();
        g.armed = true;
        g.awayPaused = false;
      }
      return;
    }
    if (g.armed) {
      if (!g.ready || r < OPEN_TO_PLAY || v < VIEW_TO_PLAY) {
        // Keep drawing while the page settles or the camera pans in.
        if (g.ready && r >= OPEN_TO_PLAY) state.invalidate();
        return;
      }
      g.armed = false;
      if (freezeAt !== null) {
        controller.pause();
        controller.seek(freezeAt);
      } else {
        controller.seek(0);
        controller.play();
      }
      return;
    }
    // Reading the story page on a phone: hold the scene there, then carry on.
    const playing = controller.getStatus().playing;
    if (v < VIEW_TO_PAUSE && playing) {
      controller.pause();
      g.awayPaused = true;
    } else if (v > VIEW_TO_PLAY && g.awayPaused) {
      g.awayPaused = false;
      controller.play();
    }
  });

  // The pop-up stands a little wider than the marked slot: a real pop-up
  // uses the page. The words go on the paper in front of it.
  const layout = useMemo(() => {
    const stage: StageSize = [Math.min(PAGE_W * 0.86, size[0] * 1.12), size[1]];
    const { scale, origin } = stageLayout(stage);
    const width = Math.min(PAGE_W * 0.86, size[0] * 1.04);
    const front = origin[2] + 0.9 * scale;
    const depth = (width * 320) / 1024;
    return { stage, width, z: Math.min(front + depth / 2, size[1] / 2 - depth / 2 - 0.02) };
  }, [size]);

  const tabs = spread.stage?.pullTabs ?? [];
  const shots = useMemo(
    () => new Map((scene.timeline.interactions ?? []).map((i) => [i.id, i.shot ?? null])),
    [scene],
  );

  return (
    <>
      {/* Centred on the page itself, not the slot (which sits toward the fore-edge). */}
      <group position-x={CENTRE_X}>
        <TimelineScene controller={controller} scene={scene} size={layout.stage} reveal={reveal} onReady={onSceneReady} />
      </group>
      {ready && (
        <PrintedCaption
          controller={controller}
          tellers={tellers}
          fonts={fonts}
          width={layout.width}
          x={CENTRE_X}
          z={layout.z}
          reveal={reveal}
        />
      )}
      {tabs.map((tab) => (
        <PullTabLever key={tab.id} controller={controller} tab={tab} shot={shots.get(tab.id) ?? null} reveal={reveal} />
      ))}
      <FpsProbe />
    </>
  );
}
