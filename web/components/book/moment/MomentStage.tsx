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
 * only when it plays (../playGate.ts):
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
 *
 * And it explains (./ExplainNote.tsx): click a cast member or prop that has
 * an explanation (Timeline.explanations) and a paper note rises beside it,
 * tied on by a stitched thread. The telling pauses while a note is open and
 * plays on, in place, when it is put away; the play gate holds off its own
 * resume meanwhile. On by default; `explain={false}` leaves the stage as
 * it was.
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
import { OPEN_TO_PLAY, createPlayGate, stepPlayGate, type PlayGate } from "../playGate";
import type { StageShot } from "../shot";
import { STAGE_SLOT } from "../StoryPage";
import type { BookSpread, BookTeller } from "../types";
import { ExplainLayer, ExplainSession } from "./ExplainNote";
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
  /** Click-to-explain on the stage's cast and props. Default true. */
  explain?: boolean;
}

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

export function MomentStage({
  controller,
  scene,
  spread,
  fonts,
  reveal,
  view,
  shot,
  size,
  freezeAt = null,
  onReady,
  explain = true,
}: MomentStageProps) {
  const tellers: BookTeller[] = useMemo(() => spread.tellings ?? [], [spread]);
  const gate = useRef<PlayGate>(null);
  // The open note, and the pause it holds on the telling.
  const notes = useMemo(() => new ExplainSession(controller), [controller]);
  useEffect(() => {
    // The telling plays on after a note only on an open page, in view, mid-telling.
    notes.setMayResume(() => {
      const g = gate.current;
      return !!g && !g.armed && !g.awayPaused && reveal.current >= OPEN_TO_PLAY;
    });
    return notes.attach();
  }, [notes, reveal]);
  const [ready, setReady] = useState(false);
  const readyRef = useRef(onReady);
  useEffect(() => {
    readyRef.current = onReady;
  }, [onReady]);
  const invalidate = useThree((s) => s.invalidate);
  const onSceneReady = useMemo(
    () => () => {
      (gate.current ??= createPlayGate()).ready = true;
      setReady(true);
      readyRef.current?.();
      // The book draws on demand: one more frame, for the gate to open on.
      invalidate();
    },
    [invalidate],
  );

  // One timer for the end hold's wake (stepPlayGate's wakeAt).
  const wake = useRef<{ at: number; id: ReturnType<typeof setTimeout> | null }>({ at: 0, id: null });

  // A stage folded away (unmounted) never plays on unseen, nor holds the camera.
  useEffect(() => {
    const w = wake.current;
    return () => {
      if (w.id !== null) clearTimeout(w.id);
      w.id = null;
      w.at = 0;
      controller.pause();
      if (shot.offer(false)) invalidate();
    };
  }, [controller, shot, invalidate]);

  useFrame((state) => {
    gate.current ??= createPlayGate();
    const status = controller.getStatus();
    const now = performance.now();
    const step = stepPlayGate(gate.current, {
      reveal: reveal.current,
      view: view.current,
      ended: status.ended,
      playing: status.playing,
      now,
    });
    // The camera read the offer earlier this frame (CAMERA_PRIORITY): a
    // change needs one more frame to be seen, or the camera, drawn on
    // demand, stays leaned in once the hold ends.
    if (shot.offer(step.offer) || step.redraw) state.invalidate();
    // The last line holds still: no frames till the hold is up, then one
    // (on which the offer goes, and that change wakes the camera).
    const w = wake.current;
    if (step.wakeAt && step.wakeAt !== w.at) {
      if (w.id !== null) clearTimeout(w.id);
      w.at = step.wakeAt;
      w.id = setTimeout(() => {
        w.id = null;
        w.at = 0;
        invalidate();
      }, step.wakeAt - now + 1);
    }
    switch (step.action) {
      case "rearm":
        // Folded away: next time the page opens, the set rises from the start.
        notes.dispatch({ type: "turn" });
        controller.pause();
        if (freezeAt === null) controller.seek(0);
        break;
      case "start":
        if (freezeAt !== null) {
          controller.pause();
          controller.seek(freezeAt);
        } else {
          controller.seek(0);
          controller.play();
        }
        break;
      case "pauseAway":
        controller.pause();
        break;
      case "resume":
        // Back on the stage page with a note open: play on once it is put away.
        if (notes.open !== null) notes.resumeLater();
        else controller.play();
        break;
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
  const captionAt = useRef<Group>(null);
  const caption = useMemo(() => ({ at: captionAt, width: layout.width, depth: layout.depth }), [layout]);
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
          {explain && <ExplainLayer session={notes} fonts={fonts} reveal={reveal} caption={caption} enabled={ready} gate={gate} />}
        </TimelineScene>
      </group>
      <group ref={keep} position={[CENTRE_X, 0, keepZ]} />
      <group ref={captionAt} position={[CENTRE_X, 0, layout.z]} />
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
