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
 *
 * And the camera plays: while the telling is under way the stage offers the
 * book its timeline camera (the camera:position / lookAt / fov tracks, mapped
 * from stage space to the world as the stage stands in the page), and the
 * book's camera leans from its reading view into the pop-up and follows
 * those moves, scaled to keep the printed words in frame (../shot.ts). It
 * eases back out to the spread a beat after the telling ends (its tellers
 * there to choose again), as the page turns, or when the reader taps to step
 * back; choosing a telling leans back in.
 */

import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Vector3, type Group } from "three";
import type { StageSize } from "@/components/stage/CameraRig";
import { FpsProbe, PRIORITY, TimelineScene, stageLayout, useScene, type SceneData } from "@/components/stage/play";
import type { PlaybackController } from "@/components/stage/playback";
import { pullTabGeometry } from "../labelArt";
import { PAGE_H, PAGE_W } from "../leafShape";
import { PAGE_PX_W } from "../pageArt";
import type { PageFonts } from "../paperArt";
import type { StageShot } from "../shot";
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
  /** The camera this stage offers the book (StageSlotArgs.shot). */
  shot: StageShot;
  /** The slot, world units [width, depth]. */
  size: StageSize;
  /** Hold the scene at this time once it opens (screenshots), instead of playing. */
  freezeAt?: number | null;
  /** Called once the scene is fully drawn. */
  onReady?: () => void;
}

/**
 * The spread must lie this open (the leaf over it has all but landed), and
 * this much in view, before the scene starts.
 */
const OPEN_TO_PLAY = 0.85;
const VIEW_TO_PLAY = 0.6;
/** Below this the page has started to turn away: pause and re-arm. */
const OPEN_TO_REARM = 0.4;
const VIEW_TO_PAUSE = 0.3;
/** Once told, the camera holds on the last line this long (ms) before it pulls back to the spread. */
const END_HOLD_MS = 900;
/** The page's centre line in slot space. */
const CENTRE_X = PAGE_W / 2 - STAGE_SLOT.x;
/** World units per page px, and the pencilled label's reach below a pull tab (page px). */
const PX = PAGE_W / PAGE_PX_W;
const TAB_LABEL_PX = 60;

/**
 * The timeline's camera, offered to the book: each frame, once the player
 * has sampled it, the camera tracks mapped from stage space through the
 * stage's world matrix (as CameraBinding maps them for the standalone stage).
 */
function ShotFeed({ shot, keep }: { shot: StageShot; keep: RefObject<Group | null> }) {
  const { frame, runtime } = useScene();
  const [p, t] = useMemo(() => [new Vector3(), new Vector3()], []);
  useFrame(() => {
    const cam = frame.camera;
    runtime.toWorld(p.set(cam.position[0], cam.position[1], cam.position[2]));
    runtime.toWorld(t.set(cam.lookAt[0], cam.lookAt[1], cam.lookAt[2]));
    shot.frame(p.x, p.y, p.z, t.x, t.y, t.z, cam.fov);
    const k = keep.current;
    if (k) {
      k.getWorldPosition(p);
      shot.keepInFrame(p.x, p.y, p.z);
    }
  }, PRIORITY.drive);
  return null;
}

export function MomentStage({ controller, scene, spread, fonts, reveal, view, shot, size, freezeAt = null, onReady }: MomentStageProps) {
  const tellers: BookTeller[] = useMemo(() => spread.tellings ?? [], [spread]);
  const gate = useRef({ ready: false, armed: true, awayPaused: false, endedAt: 0 });
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

  // A stage folded away (unmounted) never plays on unseen, nor holds the camera.
  useEffect(
    () => () => {
      controller.pause();
      if (shot.offer(false)) invalidate();
    },
    [controller, shot, invalidate],
  );

  useFrame((state) => {
    const g = gate.current;
    const r = reveal.current;
    const v = view.current;
    // The camera leans in while the telling is under way on an open page
    // (held mid-scene too), and comes back out once it has been told.
    // The last line is held a beat in close-up before the camera pulls back.
    const ended = controller.getStatus().ended;
    const now = performance.now();
    if (!ended) g.endedAt = 0;
    else if (!g.endedAt) g.endedAt = now;
    const holding = ended && now - g.endedAt < END_HOLD_MS;
    if (holding) state.invalidate();
    // The camera read the offer earlier this frame (CAMERA_PRIORITY): a
    // change needs one more frame to be seen, or the camera, drawn on
    // demand, stays leaned in once the hold ends.
    if (shot.offer(!g.armed && r >= OPEN_TO_PLAY && (!ended || holding))) state.invalidate();
    if (r < OPEN_TO_REARM) {
      if (!g.armed) {
        // Folded away: next time the page opens, the set rises from the start.
        controller.pause();
        if (freezeAt === null) controller.seek(0);
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
    const stage: StageSize = [Math.min(PAGE_W * 0.92, size[0] * 1.2), size[1]];
    const { scale, origin } = stageLayout(stage);
    const width = Math.min(PAGE_W * 0.86, size[0] * 1.04);
    const front = origin[2] + 0.9 * scale;
    const depth = (width * 320) / 1024;
    return { stage, width, depth, z: Math.min(front + depth / 2, size[1] / 2 - depth / 2 - 0.02) };
  }, [size]);

  const tabs = useMemo(() => spread.stage?.pullTabs ?? [], [spread]);
  // The camera's shot keeps the words and the pull tabs (with their
  // pencilled labels) in frame: the nearest of them, in slot space.
  const keepZ = useMemo(() => {
    let z = layout.z + layout.depth / 2;
    for (const tab of tabs) {
      const g = pullTabGeometry(tab);
      z = Math.max(z, (g.y + g.width / 2 + TAB_LABEL_PX) * PX - PAGE_H / 2 - STAGE_SLOT.z);
    }
    return z;
  }, [layout, tabs]);
  const keep = useRef<Group>(null);
  const shots = useMemo(
    () => new Map((scene.timeline.interactions ?? []).map((i) => [i.id, i.shot ?? null])),
    [scene],
  );

  return (
    <>
      {/* Centred on the page itself, not the slot (which sits toward the fore-edge). */}
      <group position-x={CENTRE_X}>
        <TimelineScene controller={controller} scene={scene} size={layout.stage} reveal={reveal} onReady={onSceneReady}>
          <ShotFeed shot={shot} keep={keep} />
        </TimelineScene>
      </group>
      <group ref={keep} position={[CENTRE_X, 0, keepZ]} />
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
