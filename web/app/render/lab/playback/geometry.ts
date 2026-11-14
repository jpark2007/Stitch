/**
 * Stage-space geometry for the 2D debug views: puppet forward kinematics from
 * the shared rig, sockets, and where a prop's card sits. Lab-only; the 3D stage
 * builds the same thing out of three.js groups.
 */

import puppet from "@shared/rig/puppet.json";
import vocabulary from "@shared/vocabulary.json";
import { boneRotation } from "@/components/stage/playback";
import type { PlaybackFrame, PropFrame, PuppetFrame } from "@/components/stage/playback";
import type { Prop } from "@/lib/types";

type Bone = { parent: string; pivot: number[]; dir: number[]; length: number };
const BONES = puppet.bones as Record<string, Bone>;
const SOCKETS = puppet.sockets as Record<string, { bone: string; at: number[] }>;
const SIZES = vocabulary.sizes as Record<string, number>;
const BUILDS = vocabulary.builds as Record<string, number>;

export const DRAW_ORDER = puppet.drawOrder as string[];
export const HIP_HEIGHT = puppet.hipHeight;
export const STAGE_HEIGHT = puppet.stageHeight;

/** Stage units per rig unit for a build. */
export function puppetScale(build: string | null | undefined): number {
  return STAGE_HEIGHT * (BUILDS[build ?? "average"] ?? 1);
}

export interface BonePose {
  /** Stage-space start and end of the bone (x, y) */
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  /** The bone's frame in unmirrored rig units, relative to the hip: origin and angle */
  ox: number;
  oy: number;
  a: number;
}

/**
 * Forward kinematics for one puppet: every bone's stage-space segment.
 * Rotations are in the unmirrored local frame; facing -1 mirrors local x.
 */
export function solvePuppet(p: PuppetFrame, scale: number, out: Map<string, BonePose> = new Map()) {
  const hipX = p.position[0];
  const hipY = p.position[1] + HIP_HEIGHT * scale;
  const f = p.facing;
  // Local frames: origin and accumulated angle per bone, unmirrored rig units.
  const local = new Map<string, { ox: number; oy: number; a: number }>();
  const solve = (name: string): { ox: number; oy: number; a: number } => {
    const hit = local.get(name);
    if (hit) return hit;
    const b = BONES[name];
    const parent = b.parent === puppet.root ? { ox: 0, oy: 0, a: 0 } : solve(b.parent);
    const c = Math.cos(parent.a);
    const s = Math.sin(parent.a);
    const ox = parent.ox + c * b.pivot[0] - s * b.pivot[1];
    const oy = parent.oy + s * b.pivot[0] + c * b.pivot[1];
    const r = { ox, oy, a: parent.a + boneRotation(p, name) };
    local.set(name, r);
    return r;
  };
  for (const name of Object.keys(BONES)) {
    const b = BONES[name];
    const fr = solve(name);
    const c = Math.cos(fr.a);
    const s = Math.sin(fr.a);
    const tx = fr.ox + (c * b.dir[0] - s * b.dir[1]) * b.length;
    const ty = fr.oy + (s * b.dir[0] + c * b.dir[1]) * b.length;
    out.set(name, {
      x0: hipX + f * fr.ox * scale,
      y0: hipY + fr.oy * scale,
      x1: hipX + f * tx * scale,
      y1: hipY + ty * scale,
      ox: fr.ox,
      oy: fr.oy,
      a: fr.a,
    });
  }
  return out;
}

/** Stage-space position of a puppet socket (hand_l, hand_r, head). */
export function puppetSocket(p: PuppetFrame, scale: number, socket: string, poses = solvePuppet(p, scale)) {
  const s = SOCKETS[socket] ?? SOCKETS[puppet.defaultHand];
  const b = poses.get(s.bone)!;
  // `at` is in the bone's own frame, from its pivot; then mirror and scale onto the stage.
  const c = Math.cos(b.a);
  const sn = Math.sin(b.a);
  const lx = b.ox + c * s.at[0] - sn * s.at[1];
  const ly = b.oy + sn * s.at[0] + c * s.at[1];
  return { x: p.position[0] + p.facing * lx * scale, y: p.position[1] + HIP_HEIGHT * scale + ly * scale };
}

export interface PropCard {
  /** Rectangle in stage space: left, bottom, width, height */
  x: number;
  y: number;
  w: number;
  h: number;
  /** The point it hangs from (grip on a hand, anchor on the ground) */
  px: number;
  py: number;
  /** Where its parent is, for the parent line */
  parentX: number;
  parentY: number;
  rotation: number;
}

/**
 * Where a prop's card sits this frame. Held: its grip on the socket, plus the
 * position offset. On the ground: its anchor at the position.
 */
export function placeProp(
  prop: PropFrame,
  def: Prop | undefined,
  frame: PlaybackFrame,
  scaleOf: (personId: string) => number,
  depth = 0,
): PropCard {
  const h = SIZES[def?.size ?? "hand"] ?? 0.08;
  const w = h * (def?.aspect ?? 1);
  const parent = prop.parent;
  let px = prop.position[0];
  let py = prop.position[1];
  let parentX = px;
  let parentY = 0;
  let fu = def?.anchor?.[0] ?? 0.5;
  let fv = def?.anchor?.[1] ?? 1;

  if (parent.kind === "puppet" && parent.id) {
    const who = frame.puppetsById.get(parent.id);
    if (who) {
      const sc = scaleOf(who.id);
      const sock = puppetSocket(who, sc, parent.socket ?? "hand_r");
      px = sock.x + prop.position[0];
      py = sock.y + prop.position[1];
      parentX = who.position[0];
      parentY = who.position[1] + HIP_HEIGHT * sc;
      fu = def?.grip?.[0] ?? 0.5;
      fv = def?.grip?.[1] ?? 0.5;
    }
  } else if (parent.kind === "prop" && parent.id && depth < 4) {
    const host = frame.propsById.get(parent.id);
    if (host) {
      const card = placeProp(host, undefined, frame, scaleOf, depth + 1);
      px = card.x + card.w / 2 + prop.position[0];
      py = card.y + card.h + prop.position[1];
      parentX = card.x + card.w / 2;
      parentY = card.y + card.h / 2;
    }
  }
  return { x: px - fu * w, y: py - (1 - fv) * h, w, h, px, py, parentX, parentY, rotation: prop.rotation };
}
