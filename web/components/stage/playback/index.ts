/**
 * The timeline player: the "dumb player" of shared/CONTRACTS.md "Timeline".
 *
 * - sampler.ts     compileTimeline / CompiledTimeline.sample: tracks -> frame, allocation-free
 * - controller.ts  PlaybackController: clock, teller, audio sync, held pull tabs and sliders
 * - useTimelinePlayer.ts  the React hook that drives it with requestAnimationFrame
 * - types.ts       the frame the stage renders
 */

export {
  CompiledTimeline,
  DEFAULT_CAMERA,
  DEFAULT_PUPPET_POSITION,
  GROUND,
  boneRotation,
  compileTimeline,
  parseParent,
  partOffset,
  partRotation,
  sampleTimeline,
  targetMatches,
  type CompileOptions,
} from "./sampler";
export {
  PlaybackController,
  type AudioLike,
  type AudioState,
  type ControllerOptions,
  type PlayerStatus,
  type ReleaseOptions,
} from "./controller";
export { usePlayerFrame, useTimelinePlayer, type TimelinePlayer, type UseTimelinePlayerOptions } from "./useTimelinePlayer";
export { PUPPET_BONES } from "./rig";
export type * from "./types";
