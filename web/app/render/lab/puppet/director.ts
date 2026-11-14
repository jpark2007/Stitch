/**
 * The lab's tiny "timeline player": plain classes that own the mutable
 * per-puppet inputs and update them once a frame (no React state, no
 * allocations in step()). Each <Puppet> reads its driver in its own frame.
 */

import { Vector3, type Object3D } from "three";
import type { PuppetInputs } from "@/components/stage/Puppet";
import { solveArmIK } from "@/components/stage/puppet/ik";
import { createFrames, forwardKinematics, lowestFoot, stageToHip, type Rotations } from "@/components/stage/puppet/kinematics";
import { applyPose, cyclePhase, dampRotations, sampleCycle } from "@/components/stage/puppet/poses";
import { BONES, POSES, RIG, SPEEDS, getCycle } from "@/components/stage/puppet/rig";

export type Driver = { current: PuppetInputs & { position: [number, number, number] } };

const makeDriver = (x: number, z: number, facing: number): Driver => ({
  current: { position: [x, 0, z], facing, rot: {} },
});

export const ACTING_Z = 0.5;
export const SLOT_X = [-0.72, -0.24, 0.24, 0.72];
export const SLOT_FACING = [1, 1, 1, -1];
export const IK_PUPPET = 3;
const WALK_Z = 0.68;

type Frames = ReturnType<typeof createFrames>;

/** Walk a puppet back and forth between xa and xb, feet planted on the page. */
function walkBetween(d: Driver["current"], scale: number, dist: number, xa: number, xb: number, z: number, frames: Frames) {
  const cycle = getCycle("walk");
  const span = xb - xa;
  const u = ((dist % (2 * span)) + 2 * span) % (2 * span);
  const out = u < span;
  d.position[0] = out ? xa + u : xb - (u - span);
  d.facing = out ? 1 : -1;
  sampleCycle(cycle, cyclePhase(dist, cycle.strideLength, scale), d.rot);
  // The cycle bends the legs, which lifts the feet: lower the hip onto them.
  forwardKinematics(RIG, d.rot, frames, BONES);
  d.position[1] = -(RIG.hipHeight - lowestFoot(RIG, frames)) * scale;
  d.position[2] = z;
}

export interface LineupSettings {
  pose: string;
  walk: boolean;
  reach: boolean;
  freeze: number | null;
}

export class LineupDirector {
  readonly drivers: Driver[];
  /** The yarn ball (IK target) object, positioned each frame. */
  private ball: Object3D | null = null;
  private held: Vector3 | null = null;
  private time = 0;
  private started = false;
  private readonly target: Rotations = {};
  private readonly yOff: number[];
  private readonly frames = createFrames(RIG);
  private readonly hip = { x: 0, y: 0 };
  private readonly ik = { upper: 0, lower: 0, reachable: true };

  constructor(private readonly scales: readonly number[]) {
    this.drivers = scales.map((_, i) => makeDriver(SLOT_X[i], ACTING_Z, SLOT_FACING[i]));
    this.yOff = scales.map(() => 0);
  }

  attachBall(o: Object3D | null) {
    this.ball = o;
  }

  /** Pin the yarn ball where it was dragged to. */
  hold(p: Vector3) {
    this.held = (this.held ?? new Vector3()).copy(p);
  }

  step(delta: number, s: LineupSettings) {
    const dt = Math.min(delta, 1 / 20);
    this.time = s.freeze ?? this.time + dt;
    const snap = s.freeze !== null || !this.started;
    this.started = true;
    const k = snap ? 1 : 1 - Math.exp(-dt * 8);
    const pose = POSES.poses[s.pose];
    applyPose(pose, BONES, this.target);
    const offY = pose?.rootOffset ? (pose.rootOffset[1] ?? 0) : 0;

    for (let i = 0; i < this.drivers.length; i++) {
      const d = this.drivers[i].current;
      const sc = this.scales[i];
      if (i === 0 && s.walk) {
        walkBetween(d, sc, this.time * SPEEDS.walk + 0.35, -1.02, 1.02, WALK_Z, this.frames);
        this.yOff[i] = d.position[1];
        continue;
      }
      dampRotations(d.rot, this.target, BONES, k);
      this.yOff[i] += (offY * sc - this.yOff[i]) * k;
      d.position[0] = SLOT_X[i];
      d.position[1] = this.yOff[i];
      d.position[2] = ACTING_Z;
      d.facing = SLOT_FACING[i];
    }

    // Reach: the IK puppet's right hand follows the yarn ball.
    const g = this.ball;
    if (!g) return;
    g.visible = s.reach;
    if (!s.reach) return;
    const d = this.drivers[IK_PUPPET].current;
    const t = this.time;
    if (this.held) g.position.copy(this.held);
    else {
      g.position.set(
        SLOT_X[IK_PUPPET] - 0.19 + 0.075 * Math.sin(t * 0.8),
        0.37 + 0.1 * Math.sin(t * 1.15 + 0.6),
        ACTING_Z + 0.02,
      );
    }
    const hip = stageToHip(RIG, g.position.x, g.position.y, d.position[0], d.position[1], d.facing, this.scales[IK_PUPPET], this.hip);
    solveArmIK(RIG, "arm_r", hip.x, hip.y, d.rot.torso ?? 0, this.ik);
    d.rot["arm_r.upper"] = this.ik.upper;
    d.rot["arm_r.lower"] = this.ik.lower;
  }
}

export interface Walker {
  id: string;
  scale: number;
  z: number;
  start: number;
  speed: number;
  driver: Driver;
}

export class CrowdDirector {
  readonly walkers: Walker[];
  private time = 0;
  private readonly frames = createFrames(RIG);

  constructor(n: number, scaleOf: (i: number) => number, idOf: (i: number) => string) {
    this.walkers = Array.from({ length: n }, (_, i) => ({
      id: idOf(i),
      scale: scaleOf(i),
      z: 0.22 + (0.6 * i) / Math.max(1, n - 1),
      start: (i * 0.73) % 1,
      speed: SPEEDS.walk * (0.85 + ((i * 37) % 10) / 30),
      driver: makeDriver(0, 0.5, 1),
    }));
  }

  step(delta: number, freeze: number | null) {
    this.time = freeze ?? this.time + Math.min(delta, 1 / 20);
    for (const w of this.walkers) {
      const span = 2.3;
      walkBetween(w.driver.current, w.scale, this.time * w.speed + w.start * 2 * span, -1.15, 1.15, w.z, this.frames);
    }
  }
}

/** Frames per second into #fps (and <html data-fps>), twice a second. */
export class FpsCounter {
  private frames = 0;
  private since = 0;
  private total = 0;

  tick() {
    const now = performance.now();
    if (!this.since) this.since = now;
    this.frames++;
    this.total++;
    if (this.total === 30) document.documentElement.dataset.labReady = "1";
    if (now - this.since >= 500) {
      const fps = (this.frames * 1000) / (now - this.since);
      const el = document.getElementById("fps");
      if (el) el.textContent = `${fps.toFixed(0)} fps`;
      document.documentElement.dataset.fps = fps.toFixed(1);
      this.frames = 0;
      this.since = now;
    }
  }
}
