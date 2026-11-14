/**
 * The timeline stage: a compiled Timeline played as a pop-up scene.
 *
 * Inside your own Canvas (the book's right-page slot):
 *
 *   const { controller } = useTimelinePlayer(scene.timeline, { narrator, autoplay: true });
 *   <TimelineScene controller={controller} scene={scene} size={[slot.width, slot.depth]} />
 *
 * Standalone (the render route): <TimelineStage controller={…} scene={…} />,
 * a Canvas with the book, table, lamp and the timeline's camera.
 *
 * DOM pieces to put around it: <CaptionStrip>, <TellerTabs>, <PlayerControls>.
 *
 * Scene data: sceneFromMemory(memoryResponse) or loadSampleScene("hymn_books").
 * Build cutouts and the puppet kit before mounting: preloadScene(scene).
 *
 * Lower level, for custom compositions: SceneProvider + the bindings
 * (SetLayers / LayerBinding, PuppetActor, PropBinding, CameraBinding,
 * EffectBindings), each reading the shared frame in its own useFrame.
 */

export { TimelineScene, stageLayout, type TimelineSceneProps } from "./TimelineScene";
export { TimelineStage, type TimelineStageProps } from "./TimelineStage";
export {
  SceneProvider,
  StageRuntime,
  useScene,
  standUp,
  PRIORITY,
  WING_X,
  type SceneContextValue,
  type SceneProviderProps,
} from "./SceneProvider";
export { PuppetActor, type PuppetActorProps } from "./PuppetActor";
export { PropBinding, type PropBindingProps } from "./PropBinding";
export { LayerBinding, SetLayers, sceneCutoutSpecs, type LayerBindingProps } from "./LayerBinding";
export { Standing, foldToRise, layerZ, risersFor, type Mount, type Riser } from "./standing";
export { CameraBinding, type CameraBindingProps } from "./CameraBinding";
export { EffectBindings } from "./EffectBindings";
export { CaptionStrip, type CaptionStripProps, type Teller } from "./CaptionStrip";
export { PlayerControls, SoundHint, TellerTabs, type PlayerControlsProps, type TellerTabsProps } from "./PlayerControls";
export { SAMPLE_SCENES, loadSampleScene, personName, sceneFromMemory, type SceneData } from "./scene";
export { preloadScene, sceneTellers } from "./preload";
