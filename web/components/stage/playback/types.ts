/**
 * What the timeline player hands the stage each frame.
 *
 * A frame is one reusable, mutable object: `CompiledTimeline.sample(t, frame)`
 * overwrites its fields in place, so nothing is allocated per frame. Treat it
 * as read-only and do not keep references to its vectors across frames; copy
 * the numbers out if you need history.
 *
 * Conventions (axes, units, channels) are in shared/CONTRACTS.md.
 */

import type {
  Caption,
  Interaction,
  NarrationLine,
  TimelineEffect,
  TimelineShot,
  WordTiming,
} from "@/lib/types";

/** [x, y, z] in stage units. Mutated in place by the player. */
export type Vec3 = [number, number, number];

/** Where a prop hangs: parsed once from a `parent` key and shared by every frame. */
export interface ParentRef {
  /** The key exactly as written, e.g. "puppet:dad.hand_r" or "ground" */
  readonly raw: string;
  readonly kind: "ground" | "puppet" | "prop";
  /** Person id or prop id; null for ground */
  readonly id: string | null;
  /** Socket name, e.g. "hand_r"; null for ground */
  readonly socket: string | null;
  /** Track target of the parent, e.g. "puppet:dad"; null for ground */
  readonly target: string | null;
}

export interface PuppetFrame {
  /** Person id */
  readonly id: string;
  /** "puppet:<id>" */
  readonly target: string;
  /** Point on the ground under the hips, stage units */
  readonly position: Vec3;
  /** 1 faces right (+x), -1 faces left */
  facing: 1 | -1;
  /**
   * Bone names: every rig bone, then any extra bone a track names. Shared by
   * every puppet of a timeline, so resolve slots once (boneIndex) and read
   * `rotations[slot]` per frame.
   */
  readonly bones: readonly string[];
  readonly boneIndex: ReadonlyMap<string, number>;
  /**
   * rotations[i] is bones[i]'s rotation about z in radians, relative to its
   * parent, in the puppet's unmirrored local frame. Bones with no track are 0.
   */
  readonly rotations: Float64Array;
}

export interface PropFrame {
  /** Prop id */
  readonly id: string;
  /** "prop:<id>" */
  readonly target: string;
  /** Current parent. A shared, pre-parsed object: compare by reference or `raw`. */
  parent: ParentRef;
  /**
   * In the current parent's space: world anchor position when the parent is
   * ground, an offset from the socket when held (normally [0, 0, 0]).
   */
  readonly position: Vec3;
  /** Radians about z */
  rotation: number;
  /** Current state name, or null when the prop has no state track */
  state: string | null;
  /** Names of the parts that move: from `part.<name>.rot` tracks and prop_part drives */
  readonly parts: readonly string[];
  readonly partIndex: ReadonlyMap<string, number>;
  /** partRotations[i] is parts[i]'s rotation in radians (track or prop_part drive) */
  readonly partRotations: Float64Array;
  /** partOffsets[i] is parts[i]'s offset (only a prop_part drive with channel "offset" sets it) */
  readonly partOffsets: Float64Array;
}

export interface SetLayerFrame {
  /** "set:<setId>.layer<n>" */
  readonly target: string;
  readonly setId: string;
  /** Index into SetDef.layers */
  readonly layer: number;
  /** 0 flat on the page, 1 risen */
  fold: number;
}

export interface CameraFrame {
  readonly position: Vec3;
  readonly lookAt: Vec3;
  /** Vertical field of view, degrees */
  fov: number;
}

export interface EffectFrame {
  /** The timeline entry, by reference */
  readonly effect: TimelineEffect;
  /** t <= now < t + dur */
  active: boolean;
  /** 0..1 through the effect while active; 0 before, 1 after */
  progress: number;
}

export interface NarrationFrame {
  /** Teller (author person id) whose captions these are; null when the timeline has none */
  narrator: string | null;
  /** The caption showing now, or null */
  caption: Caption | null;
  /** Index into the teller's captions, or -1 */
  captionIndex: number;
  /** The narration line matching the caption (for word timings), or null */
  line: NarrationLine | null;
  /** Index of the last word spoken so far in `line.words`, or -1 */
  wordIndex: number;
}

/** Everything the stage draws at one moment. Reused and overwritten every frame. */
export interface PlaybackFrame {
  /** Scene time, seconds from the timeline start */
  t: number;
  readonly duration: number;
  /** The shot whose [start, end) holds t (the last shot after the end), or null */
  shot: TimelineShot | null;
  shotIndex: number;
  readonly puppets: readonly PuppetFrame[];
  readonly puppetsById: ReadonlyMap<string, PuppetFrame>;
  readonly props: readonly PropFrame[];
  readonly propsById: ReadonlyMap<string, PropFrame>;
  readonly layers: readonly SetLayerFrame[];
  readonly layersByTarget: ReadonlyMap<string, SetLayerFrame>;
  readonly camera: CameraFrame;
  /** One entry per Timeline.effects item, in order; check `active` */
  readonly effects: readonly EffectFrame[];
  /** Number of entries in `effects` with active = true */
  activeEffects: number;
  /**
   * The most recent time_of_day value started at or before t (null if none),
   * the value before it, and how far the change has blended (0..1).
   */
  timeOfDay: string | null;
  timeOfDayFrom: string | null;
  timeOfDayMix: number;
  readonly narration: NarrationFrame;
}

/** One telling's narration, ready to play. */
export interface NarratorTrack {
  /** Author person id */
  readonly id: string;
  /** Media id of the narration audio, or null when there is none yet */
  readonly audio: string | null;
  /** Sorted by t */
  readonly captions: readonly Caption[];
  /** Line matching each caption (same index), or null */
  readonly captionLines: readonly (NarrationLine | null)[];
  /** Every word timing across the telling, sorted by t */
  readonly words: readonly WordTiming[];
}

/** A pull tab or slider, resolved against the tracks it drives. */
export interface CompiledInteraction {
  readonly id: string;
  readonly interaction: Interaction;
  readonly type: "scrub" | "prop_state" | "prop_part";
  /** scrub: timeline time at travel 0 and travel 1 */
  readonly from: number;
  readonly to: number;
  /** scrub: the target prefixes it drives; null means every track */
  readonly targets: readonly string[] | null;
  /** prop_state / prop_part: the prop id */
  readonly prop: string | null;
  /** prop_state: the states travel steps through */
  readonly states: readonly string[];
  /** prop_part: part name, channel and [min, max] */
  readonly part: string | null;
  readonly channel: "rot" | "offset";
  readonly range: readonly [number, number];
}

/** A drive being held at some travel. `value` is 0..1 along the tab or slider. */
export interface InteractionInput {
  id: string;
  value: number;
}

export interface SampleOptions {
  /** Teller whose captions to resolve; default the timeline's default narrator */
  narrator?: string | null;
  /** Drives being held right now (pull tabs, sliders) */
  interactions?: readonly InteractionInput[];
}
