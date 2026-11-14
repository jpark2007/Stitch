/**
 * Forward kinematics for the shared puppet rig (shared/rig/puppet.json),
 * following shared/CONTRACTS.md "The puppet":
 *
 *   - every rotation is about z, radians, CCW positive, relative to the parent
 *   - a bone's pivot is in its parent's frame and turns with the parent
 *   - its tip is pivot + rotate(dir × length)
 *   - the local origin is the hip; root.position is the ground under the hip
 *
 * Pure and dependency-free (type imports only), so `node --test` runs it.
 * Nothing here allocates once the caller holds its own scratch objects.
 */

export interface BoneDef {
  parent: string;
  pivot: readonly number[];
  dir: readonly number[];
  length: number;
}

export interface SocketDef {
  bone: string;
  at: readonly number[];
}

export interface RigDef {
  height: number;
  stageHeight: number;
  hipHeight: number;
  root: string;
  bones: Record<string, BoneDef>;
  drawOrder: readonly string[];
  sockets: Record<string, SocketDef>;
  ik: Record<string, { upper: string; lower: string; socket: string }>;
  defaultHand: string;
}

/** Bone rotations by bone name. Missing bones are 0 (standing, arms down). */
export type Rotations = Record<string, number | undefined>;

/** A bone's frame in the hip (root) frame: origin at its pivot, absolute angle. */
export interface BoneFrame {
  x: number;
  y: number;
  angle: number;
}

/** Bone names with every parent before its children. */
export function boneOrder(rig: RigDef): string[] {
  const out: string[] = [];
  const seen = new Set<string>([rig.root]);
  const names = Object.keys(rig.bones);
  while (out.length < names.length) {
    const before = out.length;
    for (const n of names) {
      if (!seen.has(n) && seen.has(rig.bones[n].parent)) {
        seen.add(n);
        out.push(n);
      }
    }
    if (out.length === before) throw new Error("rig: bone parents form a cycle or name a missing bone");
  }
  return out;
}

/** Scratch frames for forwardKinematics, one per bone. */
export function createFrames(rig: RigDef): Record<string, BoneFrame> {
  const f: Record<string, BoneFrame> = {};
  for (const n of Object.keys(rig.bones)) f[n] = { x: 0, y: 0, angle: 0 };
  return f;
}

/**
 * Fill `frames` with every bone's pivot and absolute angle in the hip frame.
 * `order` is boneOrder(rig) (pass it to avoid recomputing).
 */
export function forwardKinematics(
  rig: RigDef,
  rot: Rotations,
  frames: Record<string, BoneFrame>,
  order: readonly string[] = boneOrder(rig),
): Record<string, BoneFrame> {
  for (const n of order) {
    const b = rig.bones[n];
    const f = frames[n];
    const p = b.parent === rig.root ? null : frames[b.parent];
    const pa = p ? p.angle : 0;
    const c = Math.cos(pa);
    const s = Math.sin(pa);
    const px = b.pivot[0];
    const py = b.pivot[1];
    f.x = (p ? p.x : 0) + px * c - py * s;
    f.y = (p ? p.y : 0) + px * s + py * c;
    f.angle = pa + (rot[n] ?? 0);
  }
  return frames;
}

/** A point given in a bone's frame, mapped into the hip frame. */
export function pointInHip(frame: BoneFrame, x: number, y: number, out: { x: number; y: number }) {
  const c = Math.cos(frame.angle);
  const s = Math.sin(frame.angle);
  out.x = frame.x + x * c - y * s;
  out.y = frame.y + x * s + y * c;
  return out;
}

/** A bone's tip (pivot + rotate(dir × length)) in the hip frame. */
export function boneTip(rig: RigDef, frames: Record<string, BoneFrame>, bone: string, out = { x: 0, y: 0 }) {
  const b = rig.bones[bone];
  return pointInHip(frames[bone], b.dir[0] * b.length, b.dir[1] * b.length, out);
}

/** A socket's position in the hip frame, rig units, unmirrored. */
export function socketInHip(
  rig: RigDef,
  frames: Record<string, BoneFrame>,
  socket: string,
  out = { x: 0, y: 0 },
) {
  const s = rig.sockets[socket];
  if (!s) throw new Error(`rig: unknown socket "${socket}"`);
  return pointInHip(frames[s.bone], s.at[0], s.at[1], out);
}

/** Stage units per rig unit for a build multiplier (contract: stageHeight × build). */
export function puppetScale(rig: RigDef, buildMultiplier = 1): number {
  return (rig.stageHeight * buildMultiplier) / rig.height;
}

/**
 * A point in the hip frame (rig units, unmirrored) placed on stage for a
 * puppet at `root.position` = (rx, ry, rz) with `facing` ±1 and `scale`.
 * The hip is hipHeight × scale above root.position; facing mirrors x.
 */
export function hipToStage(
  rig: RigDef,
  x: number,
  y: number,
  rx: number,
  ry: number,
  rz: number,
  facing: number,
  scale: number,
  out = { x: 0, y: 0, z: 0 },
) {
  out.x = rx + x * scale * (facing < 0 ? -1 : 1);
  out.y = ry + (rig.hipHeight + y) * scale;
  out.z = rz;
  return out;
}

/** The inverse of hipToStage for x and y: a stage point into the hip frame. */
export function stageToHip(
  rig: RigDef,
  sx: number,
  sy: number,
  rx: number,
  ry: number,
  facing: number,
  scale: number,
  out = { x: 0, y: 0 },
) {
  out.x = (sx - rx) / (scale * (facing < 0 ? -1 : 1));
  out.y = (sy - ry) / scale - rig.hipHeight;
  return out;
}

/**
 * How far below the hip the lower of the two feet is, in rig units, for the
 * leg chains ending in `feet` (default the two lower-leg bones). In the
 * standing pose this equals rig.hipHeight. The rig's walk and run cycles bend
 * the legs, so their feet sit above the ground at hip height; a caller that
 * wants planted feet lowers the hip by `hipHeight − lowestFoot(...)`.
 */
export function lowestFoot(
  rig: RigDef,
  frames: Record<string, BoneFrame>,
  feet: readonly string[] = ["leg_l.lower", "leg_r.lower"],
  scratch = { x: 0, y: 0 },
): number {
  let drop = 0;
  for (const f of feet) {
    boneTip(rig, frames, f, scratch);
    if (-scratch.y > drop) drop = -scratch.y;
  }
  return drop;
}
