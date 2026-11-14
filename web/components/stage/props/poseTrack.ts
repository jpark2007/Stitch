/**
 * Sample a prop's timeline tracks into a PropPose.
 *
 * A small, prop-only player for the channels in CONTRACTS.md "Timeline"
 * (target `prop:<id>`): parent (step), position (linear, never across a
 * parent change), rotation (linear), state (step), part.<name>.rot (linear).
 * Before a track's first key it holds the first value; after the last, the
 * last. Compile once per timeline, then sample every frame: sampling does not
 * allocate.
 *
 * Pure: no React, no DOM, no path aliases (the unit tests run it in Node).
 */

import type { Track } from "@/lib/types";
import type { PropPose, Vec3 } from "./rig";

interface Keys<T> {
  times: Float64Array;
  values: T[];
}

export interface PropTracks {
  id: string;
  parent: Keys<string> | null;
  position: Keys<Vec3> | null;
  rotation: Keys<number> | null;
  state: Keys<string | null> | null;
  parts: { name: string; keys: Keys<number> }[];
}

function keysOf<T>(track: Track, read: (v: unknown) => T | undefined): Keys<T> | null {
  const times: number[] = [];
  const values: T[] = [];
  for (const [t, raw] of track.keys) {
    const v = read(raw);
    if (v === undefined || !Number.isFinite(t)) continue;
    times.push(t);
    values.push(v);
  }
  return times.length ? { times: Float64Array.from(times), values } : null;
}

const readString = (v: unknown) => (typeof v === "string" ? v : undefined);
const readStateName = (v: unknown) => (typeof v === "string" ? v : v === null ? null : undefined);
const readNumber = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const readVec3 = (v: unknown): Vec3 | undefined =>
  Array.isArray(v) && v.length >= 3 && v.every((x) => typeof x === "number" && Number.isFinite(x))
    ? [v[0], v[1], v[2]]
    : undefined;

/** Pick out one prop's tracks from a timeline. */
export function compilePropTracks(tracks: readonly Track[], propId: string): PropTracks {
  const target = `prop:${propId}`;
  const out: PropTracks = { id: propId, parent: null, position: null, rotation: null, state: null, parts: [] };
  for (const tr of tracks) {
    if (tr.target !== target) continue;
    const ch = tr.channel;
    if (ch === "parent") out.parent = keysOf(tr, readString);
    else if (ch === "position") out.position = keysOf(tr, readVec3);
    else if (ch === "rotation") out.rotation = keysOf(tr, readNumber);
    else if (ch === "state") out.state = keysOf(tr, readStateName);
    else if (ch.startsWith("part.") && ch.endsWith(".rot")) {
      const keys = keysOf(tr, readNumber);
      if (keys) out.parts.push({ name: ch.slice(5, -4), keys });
    }
  }
  return out;
}

/** Index of the last key with time <= t, or -1 when t is before every key. */
export function lastAtOrBefore(times: Float64Array, t: number): number {
  let lo = 0;
  let hi = times.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

function stepAt<T>(keys: Keys<T>, t: number): T {
  const i = lastAtOrBefore(keys.times, t);
  return keys.values[i < 0 ? 0 : i];
}

function linearAt(keys: Keys<number>, t: number): number {
  const { times, values } = keys;
  const i = lastAtOrBefore(times, t);
  if (i < 0) return values[0];
  if (i >= times.length - 1) return values[i];
  const k = (t - times[i]) / (times[i + 1] - times[i] || 1);
  return values[i] + (values[i + 1] - values[i]) * k;
}

/**
 * Position, interpolating only between keys inside the current parent's
 * segment [segStart, segEnd). Writes into `out`.
 */
function positionAt(keys: Keys<Vec3>, t: number, segStart: number, segEnd: number, out: Vec3): Vec3 {
  const { times, values } = keys;
  // The last key at or before t; if it falls before the segment, the
  // segment's first key holds (it is a step at the parent change).
  let i = lastAtOrBefore(times, t);
  if (i < 0 || times[i] < segStart) {
    let j = i + 1;
    while (j < times.length && times[j] < segStart) j++;
    if (j < times.length && times[j] < segEnd) i = j;
    else if (i < 0) i = 0;
    const v = values[i];
    out[0] = v[0];
    out[1] = v[1];
    out[2] = v[2];
    return out;
  }
  const a = values[i];
  const next = i + 1 < times.length && times[i + 1] < segEnd ? i + 1 : -1;
  if (next < 0) {
    out[0] = a[0];
    out[1] = a[1];
    out[2] = a[2];
    return out;
  }
  const b = values[next];
  const k = (t - times[i]) / (times[next] - times[i] || 1);
  out[0] = a[0] + (b[0] - a[0]) * k;
  out[1] = a[1] + (b[1] - a[1]) * k;
  out[2] = a[2] + (b[2] - a[2]) * k;
  return out;
}

/** Sample every channel at time `t` into `out` (mutated, also returned).
 *  A channel with no track leaves its field in `out` untouched, so seed `out`
 *  with createPropPose() and the prop's starting values. */
export function samplePropTracks(tracks: PropTracks, t: number, out: PropPose): PropPose {
  let segStart = -Infinity;
  let segEnd = Infinity;
  if (tracks.parent) {
    const { times, values } = tracks.parent;
    const i = lastAtOrBefore(times, t);
    out.parent = values[i < 0 ? 0 : i];
    // Before the first parent key the first parent holds, so its segment
    // reaches back to -Infinity.
    segStart = i <= 0 ? -Infinity : times[i];
    const nextIdx = i < 0 ? 1 : i + 1;
    segEnd = nextIdx < times.length ? times[nextIdx] : Infinity;
  }
  if (tracks.position) positionAt(tracks.position, t, segStart, segEnd, out.position);
  if (tracks.rotation) out.rotation = linearAt(tracks.rotation, t);
  if (tracks.state) out.state = stepAt(tracks.state, t);
  for (const p of tracks.parts) out.parts[p.name] = linearAt(p.keys, t);
  return out;
}
