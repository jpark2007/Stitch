/**
 * The dumb player's core: compile a Timeline once, then sample it at any time
 * into a reusable frame. Pure and framework-free (no DOM, no React, no three).
 *
 *   const compiled = compileTimeline(timeline, { bones: PUPPET_BONES });
 *   const frame = compiled.createFrame();
 *   compiled.sample(t, frame, { narrator: "mom" });   // overwrites `frame`
 *
 * Interpolation follows the table in shared/CONTRACTS.md "Timeline":
 *   - linear: root.position, <bone>.rot, prop position/rotation/part rot,
 *     set fold, camera fov; camera position/lookAt are linear with an ease
 *     in-out (smoothstep) across each key segment.
 *   - step: root.facing, prop parent, prop state.
 *   - Before a track's first key hold the first value, after its last key hold
 *     the last. A bone with no track is 0.
 *   - Prop position is a step at every parent key and never interpolates
 *     across a parent change.
 *
 * Sampling allocates nothing: tracks are packed into typed arrays, keys are
 * found by binary search (with a cached hint for sequential playback), bone
 * and part values live in Float64Array slots, and everything is written into
 * the frame in place.
 *
 * Node runs this file directly (type stripping) for the unit tests, so it may
 * only import types.
 */

import type { Caption, Interaction, NarrationLine, Timeline, TimelineShot, Track, WordTiming } from "@/lib/types";
import type {
  CameraFrame,
  CompiledInteraction,
  EffectFrame,
  InteractionInput,
  NarratorTrack,
  ParentRef,
  PlaybackFrame,
  PropFrame,
  PuppetFrame,
  SampleOptions,
  SetLayerFrame,
  Vec3,
} from "./types";

export interface CompileOptions {
  /**
   * Every bone in the puppet rig (keys of shared/rig/puppet.json `bones`).
   * Each puppet's frame lists all of them, at 0 unless a track moves it.
   * Bones that only appear in tracks are added after them.
   */
  bones?: readonly string[];
}

/** Camera used when the timeline has no camera tracks: the compiler's WIDE pose. */
export const DEFAULT_CAMERA = {
  position: [0, 0.6, 2.6] as const,
  lookAt: [0, 0.35, 0.5] as const,
  fov: 35,
};

/** Where a puppet stands when it has no root.position track: centre of the acting plane. */
export const DEFAULT_PUPPET_POSITION = [0, 0, 0.5] as const;

export const GROUND: ParentRef = Object.freeze({
  raw: "ground",
  kind: "ground",
  id: null,
  socket: null,
  target: null,
});

/** A bone's rotation in a puppet frame, by name (0 when the rig has no such bone). */
export function boneRotation(puppet: PuppetFrame, bone: string): number {
  const i = puppet.boneIndex.get(bone);
  return i === undefined ? 0 : puppet.rotations[i];
}

/** A part's rotation in a prop frame, by name (0 when the prop has no such part). */
export function partRotation(prop: PropFrame, part: string): number {
  const i = prop.partIndex.get(part);
  return i === undefined ? 0 : prop.partRotations[i];
}

/** A part's offset in a prop frame, by name (0 when the prop has no such part). */
export function partOffset(prop: PropFrame, part: string): number {
  const i = prop.partIndex.get(part);
  return i === undefined ? 0 : prop.partOffsets[i];
}

// ---------------------------------------------------------------- key search

/** A packed track of numeric keys. `width` numbers per key. */
interface NumericTrack {
  readonly times: Float64Array;
  readonly values: Float64Array;
  readonly width: 1 | 3;
  readonly eased: boolean;
  /** Segment i (key i -> key i+1) is a step when stepSegments[i] = 1. */
  readonly stepSegments: Uint8Array | null;
  hint: number;
}

/** A track of step keys (facing, parent, state). */
interface StepTrack<T> {
  readonly times: Float64Array;
  readonly values: readonly T[];
  hint: number;
}

// Why the samplers take (at, ai) instead of a time `t`: V8 boxes a double
// into a fresh heap object whenever it crosses a call that was not inlined
// (as an argument or a return value). Reading the time from a Float64Array
// slot, and handing results back through SCRATCH, keeps every call here
// allocation-free whether or not the JIT inlines it.

/** SCRATCH[0]: segment fraction; SCRATCH[1]: last scalar sample. */
const SCRATCH = new Float64Array(2);

/**
 * Index of the last key with time <= at[ai], or -1 when that is before the
 * first key. Checks the cached hint and its successor first (sequential
 * playback), then falls back to binary search.
 */
function findKey(track: { times: Float64Array; hint: number }, at: Float64Array, ai: number): number {
  const t = at[ai];
  const times = track.times;
  const n = times.length;
  if (n === 0 || t < times[0]) return -1;
  if (t >= times[n - 1]) return n - 1;
  const h = track.hint;
  if (h >= 0 && h < n - 1 && times[h] <= t) {
    if (t < times[h + 1]) return h;
    if (h + 2 < n && t < times[h + 2]) {
      track.hint = h + 1;
      return h + 1;
    }
  }
  // Invariant: times[lo] <= t < times[hi].
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >>> 1;
    if (times[mid] <= t) lo = mid;
    else hi = mid;
  }
  track.hint = lo;
  return lo;
}

/**
 * Key index at at[ai], with the fraction toward the next key (holds, steps
 * and easing applied) left in SCRATCH[0].
 */
function segment(track: NumericTrack, at: Float64Array, ai: number): number {
  const n = track.times.length;
  const i = findKey(track, at, ai);
  if (i < 0) {
    SCRATCH[0] = 0;
    return 0;
  }
  if (i >= n - 1 || (track.stepSegments !== null && track.stepSegments[i] === 1)) {
    SCRATCH[0] = 0;
    return i;
  }
  const t0 = track.times[i];
  let u = (at[ai] - t0) / (track.times[i + 1] - t0);
  if (track.eased) u = u * u * (3 - 2 * u);
  SCRATCH[0] = u;
  return i;
}

/** Samples a scalar track at at[ai] into SCRATCH[1]. */
function sampleScalar(track: NumericTrack, at: Float64Array, ai: number): void {
  const i = segment(track, at, ai);
  const u = SCRATCH[0];
  const v = track.values;
  SCRATCH[1] = u === 0 ? v[i] : v[i] + (v[i + 1] - v[i]) * u;
}

function sampleVec3(track: NumericTrack, at: Float64Array, ai: number, out: Vec3): void {
  const i = segment(track, at, ai);
  const u = SCRATCH[0];
  const v = track.values;
  const a = i * 3;
  if (u === 0) {
    out[0] = v[a];
    out[1] = v[a + 1];
    out[2] = v[a + 2];
  } else {
    const b = a + 3;
    out[0] = v[a] + (v[b] - v[a]) * u;
    out[1] = v[a + 1] + (v[b + 1] - v[a + 1]) * u;
    out[2] = v[a + 2] + (v[b + 2] - v[a + 2]) * u;
  }
}

function sampleStep<T>(track: StepTrack<T>, at: Float64Array, ai: number): T {
  const i = findKey(track, at, ai);
  return track.values[i < 0 ? 0 : i];
}

// ---------------------------------------------------------------- parsing

type Key = Track["keys"][number];

function sortedKeys(track: Track): readonly Key[] {
  const keys = track.keys;
  for (let i = 1; i < keys.length; i++) {
    if (keys[i][0] < keys[i - 1][0]) {
      // Stable sort keeps same-time keys in their written order.
      return keys.slice().sort((a, b) => a[0] - b[0]);
    }
  }
  return keys;
}

function isVec3(v: unknown): v is number[] {
  return Array.isArray(v) && v.length >= 3 && Number.isFinite(v[0]) && Number.isFinite(v[1]) && Number.isFinite(v[2]);
}

/**
 * Packs numeric keys into typed arrays, skipping malformed ones. With
 * `stepAt` (a prop's parent key times), every segment that reaches one of
 * those times becomes a step: the value after it is in another parent's space.
 */
function packNumeric(keys: readonly Key[], width: 1 | 3, eased: boolean, stepAt?: Float64Array): NumericTrack | null {
  const ok = keys.filter((k) => (width === 1 ? typeof k[1] === "number" && Number.isFinite(k[1]) : isVec3(k[1])));
  if (ok.length === 0) return null;
  const times = new Float64Array(ok.length);
  const values = new Float64Array(ok.length * width);
  ok.forEach((k, i) => {
    times[i] = k[0];
    if (width === 1) values[i] = k[1] as number;
    else {
      const v = k[1] as number[];
      values[i * 3] = v[0];
      values[i * 3 + 1] = v[1];
      values[i * 3 + 2] = v[2];
    }
  });
  let stepSegments: Uint8Array | null = null;
  if (stepAt && stepAt.length > 0) {
    stepSegments = new Uint8Array(Math.max(times.length - 1, 0));
    for (let i = 0; i < stepSegments.length; i++) {
      for (let j = 0; j < stepAt.length; j++) {
        if (stepAt[j] > times[i] && stepAt[j] <= times[i + 1]) {
          stepSegments[i] = 1;
          break;
        }
      }
    }
  }
  return { times, values, width, eased, stepSegments, hint: 0 };
}

function packStep<T>(keys: readonly Key[], parse: (v: Key[1]) => T | undefined): StepTrack<T> | null {
  const times: number[] = [];
  const values: T[] = [];
  for (const k of keys) {
    const v = parse(k[1]);
    if (v === undefined) continue;
    times.push(k[0]);
    values.push(v);
  }
  if (times.length === 0) return null;
  return { times: Float64Array.from(times), values, hint: 0 };
}

/** Parses "ground", "puppet:<id>.<socket>" or "prop:<id>.<socket>". */
export function parseParent(raw: string): ParentRef | null {
  if (raw === "ground") return GROUND;
  const m = /^(puppet|prop):(.+)\.([^.]+)$/.exec(raw);
  if (!m) return null;
  const kind = m[1] as "puppet" | "prop";
  return Object.freeze({ raw, kind, id: m[2], socket: m[3], target: `${kind}:${m[2]}` });
}

const SET_TARGET = /^set:(.+)\.layer(\d+)$/;
const BONE_CHANNEL = /^(.+)\.rot$/;
const PART_CHANNEL = /^part\.(.+)\.rot$/;

/** Does a drive's target (e.g. "puppet:mom" or "set:x") cover this track target? */
export function targetMatches(prefix: string, target: string): boolean {
  return target === prefix || (target.startsWith(prefix) && target.charCodeAt(prefix.length) === 46); // "."
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

// ---------------------------------------------------------------- bindings

// What a binding writes. A data-driven switch (rather than one closure per
// track) keeps the hot loop small and monomorphic, so V8 inlines the samplers
// and never boxes the doubles they pass around.
const PUPPET_POSITION = 0;
const PUPPET_FACING = 1;
const PUPPET_BONE = 2;
const PROP_PARENT = 3;
const PROP_POSITION = 4;
const PROP_ROTATION = 5;
const PROP_STATE = 6;
const PROP_PART = 7;
const LAYER_FOLD = 8;
const CAMERA_POSITION = 9;
const CAMERA_LOOK_AT = 10;
const CAMERA_FOV = 11;
const VECTOR_KINDS = new Set([PUPPET_POSITION, PROP_POSITION, CAMERA_POSITION, CAMERA_LOOK_AT]);

/** One compiled track, bound to the frame field it writes. */
interface Binding {
  readonly target: string;
  readonly kind: number;
  /** Index into frame.puppets / props / layers */
  readonly index: number;
  /** Bone or part slot */
  readonly slot: number;
  readonly numeric: NumericTrack | null;
  readonly step: StepTrack<unknown> | null;
}

/** The vector a [x, y, z] binding writes into. */
function vectorOf(out: PlaybackFrame, b: Binding): Vec3 {
  switch (b.kind) {
    case PUPPET_POSITION:
      return out.puppets[b.index].position;
    case PROP_POSITION:
      return out.props[b.index].position;
    case CAMERA_POSITION:
      return out.camera.position;
    default:
      return out.camera.lookAt;
  }
}

/** A list of names with stable slots. */
class Slots {
  readonly names: string[] = [];
  readonly index = new Map<string, number>();
  constructor(initial: readonly string[] = []) {
    for (const n of initial) this.slot(n);
  }
  slot(name: string): number {
    let i = this.index.get(name);
    if (i === undefined) {
      i = this.names.push(name) - 1;
      this.index.set(name, i);
    }
    return i;
  }
}

/** A drive's prop-side target, resolved to slots. */
interface DriveSlot {
  readonly prop: number;
  /** Part slot for prop_part, -1 otherwise */
  readonly part: number;
}

// ---------------------------------------------------------------- narration

function compileNarrator(
  id: string,
  audio: string | null | undefined,
  captions: readonly Caption[] | undefined,
  lines: readonly NarrationLine[] | undefined,
): NarratorTrack {
  const ls = (lines ?? []).slice().sort((a, b) => a.t - b.t);
  let caps = (captions ?? []).slice().sort((a, b) => a.t - b.t);
  if (caps.length === 0 && ls.length > 0) caps = ls.map((l) => ({ t: l.t, text: l.text }));
  const captionLines = caps.map((c, i) => {
    const sameTime = ls.filter((l) => Math.abs(l.t - c.t) < 1e-6);
    const exact = sameTime.find((l) => l.text === c.text);
    if (exact ?? sameTime[0]) return exact ?? sameTime[0];
    return ls.length === caps.length ? ls[i] : null;
  });
  const words: WordTiming[] = [];
  for (const l of ls) if (l.words) words.push(...l.words);
  words.sort((a, b) => a.t - b.t);
  return { id, audio: audio ?? null, captions: caps, captionLines, words };
}

/** Every telling, default narrator first, then the rest in the order the timeline lists them. */
function compileNarrators(timeline: Timeline): NarratorTrack[] {
  const narrations = timeline.narrations ?? {};
  const ids = Object.keys(narrations);
  if (ids.length === 0) {
    const hasTopLevel = (timeline.captions?.length ?? 0) > 0 || !!timeline.audio;
    return hasTopLevel
      ? [compileNarrator(timeline.defaultNarrator ?? "narrator", timeline.audio, timeline.captions, undefined)]
      : [];
  }
  const def = timeline.defaultNarrator;
  if (def && narrations[def]) ids.sort((a, b) => (a === def ? -1 : b === def ? 1 : 0));
  return ids.map((id) => compileNarrator(id, narrations[id].audio, narrations[id].captions, narrations[id].lines));
}

// ---------------------------------------------------------------- compiled

/** A timeline packed for fast sampling. Build once per timeline with compileTimeline. */
export class CompiledTimeline {
  readonly timeline: Timeline;
  readonly duration: number;
  readonly shots: readonly TimelineShot[];
  /** Puppet bone names, in slot order (shared by every puppet frame). */
  readonly bones: readonly string[];
  /** Tellers, default narrator first. Empty when the timeline has no narration. */
  readonly narrators: readonly NarratorTrack[];
  readonly defaultNarrator: string | null;
  readonly interactions: readonly CompiledInteraction[];
  /** Keys or channels that were skipped: unknown channel, malformed value, bad parent. */
  readonly issues: readonly string[];

  private readonly bindings: Binding[] = [];
  private readonly bindingTimes: Float64Array;
  private readonly boneSlots: Slots;
  private readonly puppetIds: string[] = [];
  private readonly propIds: string[] = [];
  private readonly propParts: Slots[] = [];
  private readonly trackedStates = new Set<number>();
  private readonly layers: { target: string; setId: string; layer: number }[] = [];
  /** Per interaction (same index): the bindings a scrub drives, or null. */
  private readonly scrubMasks: (Uint8Array | null)[] = [];
  /** Per interaction (same index): the prop and part a prop drive writes, or null. */
  private readonly driveSlots: (DriveSlot | null)[] = [];
  private readonly interactionIndex = new Map<string, number>();
  /** Untracked fields a drive may override; reset every sample. */
  private readonly resetRotations: DriveSlot[] = [];
  private readonly resetOffsets: DriveSlot[] = [];
  private readonly resetStates: number[] = [];
  private readonly captionEnds: Float64Array[];
  private readonly timesOfDay: { t: number; dur: number; value: string | null }[];

  constructor(timeline: Timeline, options: CompileOptions = {}) {
    this.timeline = timeline;
    this.duration = timeline.duration;
    this.shots = (timeline.shots ?? []).slice().sort((a, b) => a.start - b.start);
    this.boneSlots = new Slots(options.bones ?? []);
    this.bones = this.boneSlots.names;
    const issues: string[] = [];
    this.issues = issues;

    this.compileTracks(timeline.tracks, issues);
    this.bindingTimes = new Float64Array(this.bindings.length);

    this.narrators = compileNarrators(timeline);
    this.defaultNarrator = this.narrators[0]?.id ?? timeline.defaultNarrator ?? null;
    // A caption shows until the next one starts or its shot ends.
    this.captionEnds = this.narrators.map((n) =>
      Float64Array.from(n.captions, (c, i) => {
        let end = i + 1 < n.captions.length ? n.captions[i + 1].t : this.duration;
        const shot = this.shotAt(c.t);
        if (shot >= 0) end = Math.min(end, this.shots[shot].end);
        return Math.max(end, c.t);
      }),
    );

    this.timesOfDay = (timeline.effects ?? [])
      .filter((e) => e.kind === "time_of_day")
      .map((e) => ({ t: e.t, dur: e.dur ?? 0, value: e.value ?? null }))
      .sort((a, b) => a.t - b.t);

    const compiled: CompiledInteraction[] = [];
    for (const it of timeline.interactions ?? []) compiled.push(this.compileInteraction(it, compiled.length));
    this.interactions = compiled;
  }

  // ---- public API

  /** A fresh frame laid out for this timeline, at defaults. Sample into it. */
  createFrame(): PlaybackFrame {
    const bones = this.boneSlots.names;
    const boneIndex = this.boneSlots.index;
    const puppets: PuppetFrame[] = this.puppetIds.map((id) => ({
      id,
      target: `puppet:${id}`,
      position: [DEFAULT_PUPPET_POSITION[0], DEFAULT_PUPPET_POSITION[1], DEFAULT_PUPPET_POSITION[2]],
      facing: 1,
      bones,
      boneIndex,
      rotations: new Float64Array(bones.length),
    }));
    const props: PropFrame[] = this.propIds.map((id, i) => {
      const parts = this.propParts[i];
      return {
        id,
        target: `prop:${id}`,
        parent: GROUND,
        position: [0, 0, 0],
        rotation: 0,
        state: null,
        parts: parts.names,
        partIndex: parts.index,
        partRotations: new Float64Array(parts.names.length),
        partOffsets: new Float64Array(parts.names.length),
      };
    });
    const layers: SetLayerFrame[] = this.layers.map((l) => ({ ...l, fold: 0 }));
    const camera: CameraFrame = {
      position: [DEFAULT_CAMERA.position[0], DEFAULT_CAMERA.position[1], DEFAULT_CAMERA.position[2]],
      lookAt: [DEFAULT_CAMERA.lookAt[0], DEFAULT_CAMERA.lookAt[1], DEFAULT_CAMERA.lookAt[2]],
      fov: DEFAULT_CAMERA.fov,
    };
    const effects: EffectFrame[] = (this.timeline.effects ?? []).map((effect) => ({
      effect,
      active: false,
      progress: 0,
    }));
    return {
      t: 0,
      duration: this.duration,
      shot: null,
      shotIndex: -1,
      puppets,
      puppetsById: new Map(puppets.map((p) => [p.id, p])),
      props,
      propsById: new Map(props.map((p) => [p.id, p])),
      layers,
      layersByTarget: new Map(layers.map((l) => [l.target, l])),
      camera,
      effects,
      activeEffects: 0,
      timeOfDay: null,
      timeOfDayFrom: null,
      timeOfDayMix: 0,
      narration: { narrator: null, caption: null, captionIndex: -1, line: null, wordIndex: -1 },
    };
  }

  /**
   * Writes the scene at time t into `out` (a frame from this timeline's
   * createFrame) and returns it. Allocates nothing.
   *
   * Held drives in `options.interactions` apply on top: a scrub samples its
   * targets at its mapped time while every other track stays at t; prop_state
   * and prop_part drives override that prop's state or part.
   */
  sample(t: number, out: PlaybackFrame, options?: SampleOptions): PlaybackFrame {
    out.t = t;
    const times = this.bindingTimes;
    times.fill(t);
    const held = options?.interactions;
    if (held) {
      for (let h = 0; h < held.length; h++) {
        const k = this.interactionIndex.get(held[h].id);
        if (k === undefined) continue;
        const mask = this.scrubMasks[k];
        if (mask === null) continue;
        const ci = this.interactions[k];
        const st = ci.from + clamp01(held[h].value) * (ci.to - ci.from);
        for (let i = 0; i < mask.length; i++) if (mask[i] === 1) times[i] = st;
      }
    }
    // The helpers read the time back from out.t rather than taking it as an
    // argument (see the note above findKey).
    this.applyBindings(out);
    this.applyPropDrives(out, held);
    this.sampleShot(out);
    this.sampleEffects(out);
    this.sampleNarration(out, options?.narrator);
    return out;
  }

  /** Timeline time a scrub drive maps travel `value` (0..1) to. */
  scrubTime(interaction: CompiledInteraction, value: number): number {
    return interaction.from + clamp01(value) * (interaction.to - interaction.from);
  }

  interaction(id: string): CompiledInteraction | undefined {
    const k = this.interactionIndex.get(id);
    return k === undefined ? undefined : this.interactions[k];
  }

  /** Index of a teller in `narrators`, falling back to the default narrator (0), or -1 if none. */
  narratorIndex(id: string | null | undefined): number {
    if (this.narrators.length === 0) return -1;
    if (id) {
      for (let i = 0; i < this.narrators.length; i++) if (this.narrators[i].id === id) return i;
    }
    return 0;
  }

  narrator(id: string | null | undefined): NarratorTrack | null {
    const i = this.narratorIndex(id);
    return i < 0 ? null : this.narrators[i];
  }

  /** Index of the last shot with start <= t, or -1 before the first. */
  shotAt(t: number): number {
    let found = -1;
    for (let i = 0; i < this.shots.length; i++) {
      if (this.shots[i].start <= t) found = i;
      else break;
    }
    return found;
  }

  // ---- sampling helpers

  /**
   * Samples every track at its time in bindingTimes and writes the frame.
   * Kept small, with one call site per sampler, so V8 inlines them all: a
   * double passed to or returned from a call that is not inlined is boxed,
   * which would allocate on every frame.
   */
  private applyBindings(out: PlaybackFrame): void {
    const bindings = this.bindings;
    const times = this.bindingTimes;
    for (let i = 0; i < bindings.length; i++) {
      const b = bindings[i];
      const numeric = b.numeric;
      if (numeric === null) {
        const v = sampleStep(b.step!, times, i);
        if (b.kind === PUPPET_FACING) out.puppets[b.index].facing = v as 1 | -1;
        else if (b.kind === PROP_PARENT) out.props[b.index].parent = v as ParentRef;
        else out.props[b.index].state = v as string | null;
      } else if (numeric.width === 3) {
        sampleVec3(numeric, times, i, vectorOf(out, b));
      } else {
        sampleScalar(numeric, times, i);
        const v = SCRATCH[1];
        switch (b.kind) {
          case PUPPET_BONE:
            out.puppets[b.index].rotations[b.slot] = v;
            break;
          case PROP_ROTATION:
            out.props[b.index].rotation = v;
            break;
          case PROP_PART:
            out.props[b.index].partRotations[b.slot] = v;
            break;
          case LAYER_FOLD:
            out.layers[b.index].fold = v;
            break;
          case CAMERA_FOV:
            out.camera.fov = v;
            break;
        }
      }
    }
  }

  private applyPropDrives(out: PlaybackFrame, held: readonly InteractionInput[] | undefined): void {
    const props = out.props;
    for (let i = 0; i < this.resetRotations.length; i++) {
      const r = this.resetRotations[i];
      props[r.prop].partRotations[r.part] = 0;
    }
    for (let i = 0; i < this.resetOffsets.length; i++) {
      const r = this.resetOffsets[i];
      props[r.prop].partOffsets[r.part] = 0;
    }
    for (let i = 0; i < this.resetStates.length; i++) props[this.resetStates[i]].state = null;
    if (!held) return;
    for (let h = 0; h < held.length; h++) {
      const k = this.interactionIndex.get(held[h].id);
      if (k === undefined) continue;
      const slot = this.driveSlots[k];
      if (slot === null) continue;
      const ci = this.interactions[k];
      const prop = props[slot.prop];
      const v = clamp01(held[h].value);
      if (ci.type === "prop_state") {
        if (ci.states.length > 0) prop.state = ci.states[Math.round(v * (ci.states.length - 1))];
      } else if (slot.part >= 0) {
        const x = ci.range[0] + v * (ci.range[1] - ci.range[0]);
        if (ci.channel === "rot") prop.partRotations[slot.part] = x;
        else prop.partOffsets[slot.part] = x;
      }
    }
  }

  private sampleShot(out: PlaybackFrame): void {
    const t = out.t;
    let i = -1;
    for (let k = 0; k < this.shots.length; k++) {
      if (this.shots[k].start <= t) i = k;
      else break;
    }
    out.shotIndex = i;
    out.shot = i < 0 ? null : this.shots[i];
  }

  private sampleEffects(out: PlaybackFrame): void {
    const t = out.t;
    let active = 0;
    const effects = out.effects as EffectFrame[];
    for (let i = 0; i < effects.length; i++) {
      const fx = effects[i];
      const start = fx.effect.t;
      const dur = fx.effect.dur ?? 0;
      if (t < start) {
        fx.active = false;
        fx.progress = 0;
      } else if (t < start + dur) {
        fx.active = true;
        fx.progress = (t - start) / dur;
        active++;
      } else {
        fx.active = false;
        fx.progress = 1;
      }
    }
    out.activeEffects = active;

    const tod = this.timesOfDay;
    let k = -1;
    for (let i = 0; i < tod.length; i++) {
      if (tod[i].t <= t) k = i;
      else break;
    }
    if (k < 0) {
      out.timeOfDay = null;
      out.timeOfDayFrom = null;
      out.timeOfDayMix = 0;
    } else {
      const e = tod[k];
      out.timeOfDay = e.value;
      out.timeOfDayFrom = k > 0 ? tod[k - 1].value : null;
      out.timeOfDayMix = e.dur > 0 ? clamp01((t - e.t) / e.dur) : 1;
    }
  }

  private sampleNarration(out: PlaybackFrame, narrator: string | null | undefined): void {
    const t = out.t;
    const n = out.narration;
    const ni = this.narratorIndex(narrator);
    if (ni < 0) {
      n.narrator = null;
      n.caption = null;
      n.captionIndex = -1;
      n.line = null;
      n.wordIndex = -1;
      return;
    }
    const track = this.narrators[ni];
    const caps = track.captions;
    // Captions are few; a forward scan with early exit beats a search.
    let ci = -1;
    for (let i = 0; i < caps.length; i++) {
      if (caps[i].t <= t) ci = i;
      else break;
    }
    // Past its end the caption clears, except that the last one holds once playback is over.
    if (ci >= 0 && t >= this.captionEnds[ni][ci] && t < this.duration) ci = -1;
    n.narrator = track.id;
    n.captionIndex = ci;
    n.caption = ci < 0 ? null : caps[ci];
    const line = ci < 0 ? null : track.captionLines[ci];
    n.line = line;
    let wi = -1;
    const words = line?.words;
    if (words) {
      for (let i = 0; i < words.length; i++) {
        if (words[i].t <= t) wi = i;
        else break;
      }
    }
    n.wordIndex = wi;
  }

  // ---- compile helpers

  private puppetSlot(id: string): number {
    let i = this.puppetIds.indexOf(id);
    if (i < 0) i = this.puppetIds.push(id) - 1;
    return i;
  }

  private propSlot(id: string): number {
    let i = this.propIds.indexOf(id);
    if (i < 0) {
      i = this.propIds.push(id) - 1;
      this.propParts.push(new Slots());
    }
    return i;
  }

  private compileTracks(tracks: readonly Track[], issues: string[]): void {
    // Each prop's position track needs to know where its parent keys are.
    const parentTimes = new Map<string, Float64Array>();
    for (const tr of tracks) {
      if (tr.target.startsWith("prop:") && tr.channel === "parent") {
        parentTimes.set(tr.target, Float64Array.from(sortedKeys(tr), (k) => k[0]));
      }
    }

    const skip = (tr: Track, why: string) => issues.push(`${tr.target} ${tr.channel}: ${why}`);
    const bindNumeric = (tr: Track, kind: number, index: number, track: NumericTrack | null, slot = -1) => {
      if (!track) skip(tr, VECTOR_KINDS.has(kind) ? "no [x, y, z] keys" : "no numeric keys");
      else this.bindings.push({ target: tr.target, kind, index, slot, numeric: track, step: null });
    };
    const bindStep = (tr: Track, kind: number, index: number, track: StepTrack<unknown> | null, why: string) => {
      if (!track) skip(tr, why);
      else this.bindings.push({ target: tr.target, kind, index, slot: -1, numeric: null, step: track });
    };

    for (const tr of tracks) {
      const keys = sortedKeys(tr);
      const { target, channel } = tr;

      if (target.startsWith("puppet:")) {
        const p = this.puppetSlot(target.slice(7));
        if (channel === "root.position") {
          bindNumeric(tr, PUPPET_POSITION, p, packNumeric(keys, 3, false));
        } else if (channel === "root.facing") {
          const track = packStep(keys, (v) => (typeof v === "number" ? (v < 0 ? -1 : 1) : undefined));
          bindStep(tr, PUPPET_FACING, p, track, "no numeric keys");
        } else {
          const m = BONE_CHANNEL.exec(channel);
          if (!m) skip(tr, "unknown channel");
          else {
            const track = packNumeric(keys, 1, false);
            bindNumeric(tr, PUPPET_BONE, p, track, track ? this.boneSlots.slot(m[1]) : -1);
          }
        }
      } else if (target.startsWith("prop:")) {
        const p = this.propSlot(target.slice(5));
        if (channel === "parent") {
          const track = packStep(keys, (v) => {
            if (typeof v !== "string") return undefined;
            const ref = parseParent(v);
            if (!ref) skip(tr, `bad parent "${v}"`);
            return ref ?? undefined;
          });
          if (track) bindStep(tr, PROP_PARENT, p, track, "");
        } else if (channel === "position") {
          bindNumeric(tr, PROP_POSITION, p, packNumeric(keys, 3, false, parentTimes.get(target)));
        } else if (channel === "rotation") {
          bindNumeric(tr, PROP_ROTATION, p, packNumeric(keys, 1, false));
        } else if (channel === "state") {
          this.trackedStates.add(p);
          const track = packStep(keys, (v) => (typeof v === "string" ? v : v === null ? null : undefined));
          bindStep(tr, PROP_STATE, p, track, "no state keys");
        } else {
          const m = PART_CHANNEL.exec(channel);
          if (!m) skip(tr, "unknown channel");
          else {
            const track = packNumeric(keys, 1, false);
            bindNumeric(tr, PROP_PART, p, track, track ? this.propParts[p].slot(m[1]) : -1);
          }
        }
      } else if (target.startsWith("set:")) {
        const m = SET_TARGET.exec(target);
        if (!m || channel !== "fold") {
          skip(tr, m ? "unknown channel" : "bad set target");
          continue;
        }
        let li = this.layers.findIndex((l) => l.target === target);
        if (li < 0) li = this.layers.push({ target, setId: m[1], layer: Number(m[2]) }) - 1;
        bindNumeric(tr, LAYER_FOLD, li, packNumeric(keys, 1, false));
      } else if (target === "camera") {
        if (channel === "position") bindNumeric(tr, CAMERA_POSITION, 0, packNumeric(keys, 3, true));
        else if (channel === "lookAt") bindNumeric(tr, CAMERA_LOOK_AT, 0, packNumeric(keys, 3, true));
        else if (channel === "fov") bindNumeric(tr, CAMERA_FOV, 0, packNumeric(keys, 1, false));
        else skip(tr, "unknown channel");
      } else {
        skip(tr, "unknown target");
      }
    }
  }

  private compileInteraction(it: Interaction, index: number): CompiledInteraction {
    const d = it.drives;
    const ci: CompiledInteraction = {
      id: it.id,
      interaction: it,
      type: d.type,
      from: d.from ?? 0,
      to: d.to ?? this.duration,
      targets: d.targets && d.targets.length > 0 ? d.targets : null,
      prop: d.prop ?? null,
      states: d.states ?? [],
      part: d.part ?? null,
      channel: d.channel ?? "rot",
      range: d.range && d.range.length >= 2 ? [d.range[0], d.range[1]] : [0, 1],
    };
    this.interactionIndex.set(ci.id, index);

    let mask: Uint8Array | null = null;
    let drive: DriveSlot | null = null;
    if (ci.type === "scrub") {
      mask = Uint8Array.from(this.bindings, (b) =>
        ci.targets === null || ci.targets.some((p) => targetMatches(p, b.target)) ? 1 : 0,
      );
    } else if (ci.prop !== null) {
      const prop = this.propSlot(ci.prop);
      const parts = this.propParts[prop];
      if (ci.type === "prop_state") {
        if (!this.trackedStates.has(prop)) this.resetStates.push(prop);
        drive = { prop, part: -1 };
      } else if (ci.part !== null) {
        const tracked = parts.index.has(ci.part);
        drive = { prop, part: parts.slot(ci.part) };
        // Offsets never have tracks; rotations only reset when no track writes them.
        if (ci.channel === "offset") this.resetOffsets.push(drive);
        else if (!tracked) this.resetRotations.push(drive);
      }
    }
    this.scrubMasks[index] = mask;
    this.driveSlots[index] = drive;
    return ci;
  }
}

const compiledCache = new WeakMap<Timeline, CompiledTimeline>();

/** Compiles a timeline for sampling. Without options the result is cached per timeline object. */
export function compileTimeline(timeline: Timeline, options?: CompileOptions): CompiledTimeline {
  if (options) return new CompiledTimeline(timeline, options);
  let c = compiledCache.get(timeline);
  if (!c) {
    c = new CompiledTimeline(timeline);
    compiledCache.set(timeline, c);
  }
  return c;
}

/**
 * One-off convenience: the frame at time t, freshly allocated. Inside a render
 * loop use CompiledTimeline.sample with a reused frame instead.
 */
export function sampleTimeline(
  timeline: Timeline,
  t: number,
  options?: SampleOptions & CompileOptions,
): PlaybackFrame {
  const compiled = options?.bones ? compileTimeline(timeline, { bones: options.bones }) : compileTimeline(timeline);
  return compiled.sample(t, compiled.createFrame(), options);
}
