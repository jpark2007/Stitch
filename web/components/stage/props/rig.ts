/**
 * Prop rig maths: sizes, image-fraction points and placement matrices.
 *
 * Pure and allocation-free on the hot path (everything writes into `out`), so
 * it is safe to call from useFrame. No React, no DOM, no path aliases: Node's
 * type stripping runs it directly in the unit tests.
 *
 * Conventions (shared/CONTRACTS.md, "Props"):
 * - World height is `sizes[size]` stage units; width is `height * aspect`.
 * - `anchor`, `grip`, `sockets` and part `at` are fractions of the prop image,
 *   [0, 0] top-left, [1, 1] bottom-right.
 * - The prop's local frame has its origin at the image centre, +x right, +y up,
 *   in stage units. It is a flat card in its local XY plane.
 *
 * Placement (the prop's transform in stage space):
 *   ground:  M = T(position) * Rz(rotation) * T(-anchor)
 *            The anchor sits on `position`; rotation pivots about the anchor.
 *   socket:  M = S * T(offset) * Rz(rotation) * T(-pivot)
 *            The pivot sits on the socket (plus `offset`, normally 0, in the
 *            socket's space); rotation turns about it. The pivot is the grip
 *            on a hand socket (hand_l, hand_r: CONTRACTS.md, a held prop sits
 *            on its grip) and the anchor on every other socket: a puppet's
 *            head (a worn cap rests on it, as the compiler's put_on_keys
 *            places it) and a prop's socket (a seat, a tabletop). See
 *            socketPivot. S is the socket
 *            frame reduced to its position (and, in "socket" orientation, its
 *            visible rotation): its scale is dropped, so a prop keeps its world
 *            size however big the puppet is, and so is its mirror, so a prop
 *            held by a puppet facing left is NOT mirrored. The contract has no
 *            facing for props, and the compiler's grip/anchor maths
 *            (api/screenplay/body.py anchor_from_grip) assumes an unmirrored
 *            prop, so the anchor lands where the drop key says it does.
 */

import { Matrix4, Quaternion, Vector3 } from "three";
import type { Prop } from "@/lib/types";

export type Vec3 = [number, number, number];
export type PropSize = Prop["size"];

/** Mutable per-frame placement of one prop, as the timeline player writes it
 *  (CONTRACTS.md "Timeline", target `prop:<id>`). */
export interface PropPose {
  /** "ground", "puppet:<id>.<socket>" or "prop:<id>.<socket>". */
  parent: string;
  /** Anchor in stage space (ground), or offset in the socket's space (held). */
  position: Vec3;
  /** Radians about z, counter-clockwise positive. */
  rotation: number;
  /** Current state name, or null for the base art. */
  state: string | null;
  /** Part name -> radians about z (`part.<name>.rot`). Missing = 0. */
  parts: Record<string, number>;
}

export function createPropPose(init: Partial<PropPose> = {}): PropPose {
  return {
    parent: init.parent ?? "ground",
    position: init.position ? [init.position[0], init.position[1], init.position[2]] : [0, 0, 0.5],
    rotation: init.rotation ?? 0,
    state: init.state ?? null,
    parts: { ...(init.parts ?? {}) },
  };
}

/*
 * Setters. A pose is a mutable per-frame object (like a three.js Vector3), but
 * React's lint forbids assigning fields of a value that was passed to a hook
 * or as a prop. Mutate through these instead; none of them allocate.
 */

export function setPosition(pose: PropPose, x: number, y: number, z: number): PropPose {
  pose.position[0] = x;
  pose.position[1] = y;
  pose.position[2] = z;
  return pose;
}

/** Change parent. Per the contract a parent change always comes with its
 *  position (an offset when held, normally 0), applied as a step. */
export function setParent(pose: PropPose, parent: string, x = 0, y = 0, z = 0): PropPose {
  pose.parent = parent;
  return setPosition(pose, x, y, z);
}

export function setRotation(pose: PropPose, radians: number): PropPose {
  pose.rotation = radians;
  return pose;
}

export function setState(pose: PropPose, state: string | null): PropPose {
  pose.state = state;
  return pose;
}

export function setPartRotation(pose: PropPose, part: string, radians: number): PropPose {
  pose.parts[part] = radians;
  return pose;
}

/* ------------------------------------------------------------------------ */
/* Sizes and points                                                          */
/* ------------------------------------------------------------------------ */

export const DEFAULT_ANCHOR: readonly [number, number] = [0.5, 1];
/** Where a prop with `grip: null` is held: its centre. */
export const DEFAULT_GRIP: readonly [number, number] = [0.5, 0.5];

export interface PropDims {
  height: number;
  width: number;
  aspect: number;
}

/** World size of a prop. `sizes` is `vocabulary.sizes`; an unknown size class
 *  falls back to "carried". `aspect` falls back to `fallbackAspect` (e.g. the
 *  loaded image's), then 1. */
export function propDims(
  prop: Pick<Prop, "size" | "aspect">,
  sizes: Readonly<Record<string, number>>,
  fallbackAspect?: number,
): PropDims {
  const height = sizes[prop.size] ?? sizes.carried ?? 0.2;
  const raw = prop.aspect ?? fallbackAspect ?? 1;
  const aspect = Number.isFinite(raw) && raw > 0 ? raw : 1;
  return { height, width: height * aspect, aspect };
}

/** A 2-number image fraction, or the fallback when missing or malformed. */
export function fractionOr(
  value: readonly number[] | null | undefined,
  fallback: readonly [number, number],
): readonly [number, number] {
  if (!value || value.length < 2 || !Number.isFinite(value[0]) || !Number.isFinite(value[1])) return fallback;
  return value as unknown as readonly [number, number];
}

/** Image fraction [u, v] (v down) -> prop-local point (origin at the image
 *  centre, y up) in stage units. Writes x, y into `out` and returns it. */
export function imageToLocal(
  out: { x: number; y: number },
  u: number,
  v: number,
  width: number,
  height: number,
): { x: number; y: number } {
  out.x = (u - 0.5) * width;
  out.y = (0.5 - v) * height;
  return out;
}

/* ------------------------------------------------------------------------ */
/* Parents                                                                   */
/* ------------------------------------------------------------------------ */

export interface ParsedParent {
  kind: "ground" | "puppet" | "prop" | "invalid";
  /** Target id as used by tracks: "puppet:mom", "prop:car". Empty for ground. */
  target: string;
  /** The bare id ("mom", "car"). */
  id: string;
  /** The socket name ("hand_r", "driver"). */
  socket: string;
}

const parentCache = new Map<string, ParsedParent>();

/** Parse a `parent` value. Cached: parents come from a small fixed set, so the
 *  per-frame call allocates nothing after the first sight of each value. */
export function parseParent(parent: string): ParsedParent {
  const hit = parentCache.get(parent);
  if (hit) return hit;
  let parsed: ParsedParent;
  if (parent === "ground") {
    parsed = { kind: "ground", target: "", id: "", socket: "" };
  } else {
    const m = /^(puppet|prop):([^.]+)\.(.+)$/.exec(parent);
    parsed = m
      ? { kind: m[1] as "puppet" | "prop", target: `${m[1]}:${m[2]}`, id: m[2], socket: m[3] }
      : { kind: "invalid", target: "", id: "", socket: "" };
  }
  if (parentCache.size > 512) parentCache.clear();
  parentCache.set(parent, parsed);
  return parsed;
}

/* ------------------------------------------------------------------------ */
/* Placement matrices                                                        */
/* ------------------------------------------------------------------------ */

/**
 * out = T(t) * Rz(rot) * T(-pivot). The prop-local point `pivot` lands on `t`
 * and the card turns about it.
 */
export function pivotMatrix(
  out: Matrix4,
  tx: number,
  ty: number,
  tz: number,
  rot: number,
  pivotX: number,
  pivotY: number,
): Matrix4 {
  const c = Math.cos(rot);
  const s = Math.sin(rot);
  // Rz * (-pivot) = (-(c*px - s*py), -(s*px + c*py))
  const ox = tx - (c * pivotX - s * pivotY);
  const oy = ty - (s * pivotX + c * pivotY);
  // Matrix4.set takes row-major arguments.
  out.set(c, -s, 0, ox, s, c, 0, oy, 0, 0, 1, tz, 0, 0, 0, 1);
  return out;
}

/** Placement on the ground: the anchor sits on `position`. */
export function groundMatrix(
  out: Matrix4,
  position: Readonly<Vec3>,
  rotation: number,
  anchorX: number,
  anchorY: number,
): Matrix4 {
  return pivotMatrix(out, position[0], position[1], position[2], rotation, anchorX, anchorY);
}

/** How a held prop is oriented: "upright" keeps it level with the stage
 *  (only the socket's position applies), "socket" turns it with the socket's
 *  visible rotation (a prop pinned to the forearm). Neither mirrors it. */
export type HeldOrientation = "upright" | "socket";

const _pos = new Vector3();
const _quat = new Quaternion();
const _scale = new Vector3();
const _unit = new Vector3(1, 1, 1);
const _local = new Matrix4();

/**
 * Normalise a socket's frame: keep its position, keep (or drop) its rotation,
 * drop its scale and its mirror. Writes into `out`.
 *
 * decompose() reports a mirrored matrix as a negative scale.x and returns the
 * rotation that is left, which is the rotation as seen on screen (a puppet
 * facing left with its forearm at +a in its own frame shows it at -a). That
 * is the rotation "socket" orientation follows; the mirror itself is dropped.
 */
export function socketFrame(out: Matrix4, socket: Matrix4, orientation: HeldOrientation): Matrix4 {
  socket.decompose(_pos, _quat, _scale);
  if (orientation === "upright") _quat.identity();
  return out.compose(_pos, _quat, _unit);
}

/** The puppet sockets a hand holds by: the ones the arms end in
 *  (shared/rig/puppet.json, `arms.*.socket`). */
export const HAND_SOCKETS: ReadonlySet<string> = new Set(["hand_l", "hand_r"]);

/** Which point of a prop sits on a socket. */
export type SocketPivot = "anchor" | "grip";

/**
 * The point of the prop that sits on a socket parent, matching the compiler
 * (api/screenplay/body.py): a hand holds by the grip (pick_up, hand_to);
 * everything else carries the prop by its anchor, a puppet's head for a worn
 * prop (put_on: anchor on `puppet:<id>.head`) and a prop's socket for a prop
 * set on it (place_on). `propSocketPivot` lets a prop socket take the grip
 * instead (a lamp hanging from a hook); the compiler never asks for that.
 */
export function socketPivot(parent: ParsedParent, propSocketPivot: SocketPivot = "anchor"): SocketPivot {
  if (parent.kind === "puppet") return HAND_SOCKETS.has(parent.socket) ? "grip" : "anchor";
  if (parent.kind === "prop") return propSocketPivot;
  return "anchor";
}

/**
 * Placement in a socket: the prop-local point `pivot` (its grip or anchor, per
 * socketPivot) sits on the socket, plus `offset` in the (normalised) socket
 * space; rotation turns about it. `socket` is the socket's raw matrix in stage
 * space, as a SocketResolver returns it.
 */
export function heldMatrix(
  out: Matrix4,
  socket: Matrix4,
  orientation: HeldOrientation,
  offset: Readonly<Vec3>,
  rotation: number,
  pivotX: number,
  pivotY: number,
): Matrix4 {
  socketFrame(out, socket, orientation);
  pivotMatrix(_local, offset[0], offset[1], offset[2], rotation, pivotX, pivotY);
  return out.multiply(_local);
}

/** A socket on a prop: the prop's matrix, translated to the socket point. */
export function propSocketMatrix(out: Matrix4, propMatrix: Matrix4, localX: number, localY: number): Matrix4 {
  _local.makeTranslation(localX, localY, 0);
  return out.multiplyMatrices(propMatrix, _local);
}

/* ------------------------------------------------------------------------ */
/* Art and states                                                            */
/* ------------------------------------------------------------------------ */

/** The art key drawn as the main card: "body" if present, else the first key
 *  no part uses, else the first key. Null when the prop has no art keys. */
export function bodyArtKey(prop: Pick<Prop, "art" | "parts">): string | null {
  const keys = Object.keys(prop.art ?? {});
  if (keys.includes("body")) return "body";
  const used = new Set(Object.values(prop.parts ?? {}).map((p) => p.art));
  return keys.find((k) => !used.has(k)) ?? keys[0] ?? null;
}

/**
 * A state override's media id. The contract says it is a media id; an
 * override naming one of the prop's own art keys instead ({"ringing":
 * {"body": "ringing"}}, as seeded data has it) means that key's art (null:
 * the placeholder), never a fetch of media "ringing".
 */
function overrideArt(prop: Pick<Prop, "art">, value: string): string | null {
  return prop.art && Object.hasOwn(prop.art, value) ? (prop.art[value] ?? null) : value;
}

/** The media id drawn for an art key in a state: the state's override if it
 *  has one, else the base art. null = placeholder. */
export function artFor(prop: Pick<Prop, "art" | "states">, key: string, state: string | null): string | null {
  if (state) {
    const override = prop.states?.[state]?.[key];
    if (override) return overrideArt(prop, override);
  }
  return prop.art?.[key] ?? null;
}

/** Every media id a prop may show, across all states (to preload). */
export function allArtIds(prop: Pick<Prop, "art" | "states">): string[] {
  const ids = new Set<string>();
  for (const v of Object.values(prop.art ?? {})) if (v) ids.add(v);
  for (const s of Object.values(prop.states ?? {}))
    for (const v of Object.values(s)) {
      const id = v ? overrideArt(prop, v) : null;
      if (id) ids.add(id);
    }
  return [...ids];
}

/** Wheel rotation for rolling `distance` stage units (positive = to the
 *  right) on a wheel of `radius`: clockwise seen from the front, so negative. */
export function rollAngle(distance: number, radius: number): number {
  return radius > 0 ? -distance / radius : 0;
}
