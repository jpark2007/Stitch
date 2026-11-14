"use client";

/**
 * Paper particles: confetti, steam puffs, rain strips and smoke.
 *
 * Each effect is one InstancedMesh (one draw call) of small paper shapes. A
 * particle's state is a pure function of stage time and its index, so effects
 * need no simulation state, play the same every time, and freeze exactly.
 * Puffs fade by shrinking, never by transparency, so nothing needs sorting.
 *
 * Effects belong to the scene, not the screen:
 *  - an `emitter` anchors them to a point on a set piece (smoke from the
 *    chimney) and switches them off while that piece is not standing, so
 *    nothing smokes over a flat house or keeps puffing as the set folds;
 *  - `start` / `end` bound them in stage time (particles in flight finish);
 *  - `ground` is the surface confetti settles on and rain lands on;
 *  - confetti and rain spawn inside `spread` around `origin`, kept over the
 *    page, so nothing falls onto the table.
 */

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import {
  CircleGeometry,
  Color,
  DoubleSide,
  Object3D,
  PlaneGeometry,
  type BufferGeometry,
  type InstancedMesh,
} from "three";
import { createPaperMaterial } from "@/components/paper/materials";
import { markShadowsDirty, useStageTime } from "./clock";
import { clamp, easeOutCubic, hash01, smoothstep, type Vec3 } from "./timeline";

export type EffectKind = "confetti" | "steam" | "rain" | "smoke";

export const EFFECT_KINDS: readonly EffectKind[] = ["confetti", "steam", "rain", "smoke"];

/**
 * Where an effect comes from at stage time `t`: write the point into `out`
 * and return whether it is emitting then. Particles sample it when they are
 * born, so the answer for a given `t` must not change.
 */
export type Emitter = (t: number, out: Vec3) => boolean;

export interface PaperEffectProps {
  kind: EffectKind;
  /** Where it comes from: the emitter for steam/smoke, the centre above which confetti/rain fall. */
  origin?: Vec3;
  /** A moving or switchable origin (overrides `origin`), e.g. a chimney top. */
  emitter?: Emitter;
  /** Half-extent of the fall area (x, z) for confetti and rain. */
  spread?: [number, number];
  /** Height confetti settles at and rain lands on. Default 0 (the page). */
  ground?: number;
  /** Particle count (defaults per kind). */
  count?: number;
  /** Stage time the effect starts. */
  start?: number;
  /** Stage time it stops emitting (particles in flight finish). */
  end?: number;
  /** Overall size multiplier. */
  scale?: number;
  colors?: string[];
  seed?: number;
}

const DEFAULTS: Record<EffectKind, { count: number; colors: string[] }> = {
  confetti: { count: 110, colors: ["#b9573d", "#e6b45c", "#f6efe1", "#7f9873", "#5b82a6", "#d98d6b", "#8b5a7c"] },
  steam: { count: 11, colors: ["#fbf7ef", "#f4eee3", "#efe7d9"] },
  rain: { count: 200, colors: ["#9fbdd6", "#bcd0e0", "#8aaecb", "#d4e0ea"] },
  smoke: { count: 18, colors: ["#d8d0c3", "#c9bfb1", "#bdb2a3", "#e3dcd0"] },
};

/** A paper puff: a disc with a hand-cut, wobbly rim. */
function puffGeometry(seed: number) {
  const g = new CircleGeometry(0.5, 22);
  const pos = g.getAttribute("position");
  for (let i = 1; i < pos.count; i++) {
    const a = Math.atan2(pos.getY(i), pos.getX(i));
    const r = 0.5 * (1 + 0.09 * Math.sin(a * 3 + seed) + 0.05 * Math.sin(a * 7 + seed * 2.3) + 0.04 * (hash01(i, seed) - 0.5));
    pos.setXY(i, Math.cos(a) * r, Math.sin(a) * r);
  }
  pos.needsUpdate = true;
  g.computeVertexNormals();
  return g;
}

/** A rain strip: a narrow sliver of paper, unit length, slightly tapered. */
function stripGeometry() {
  const g = new PlaneGeometry(0.06, 1, 1, 4);
  const pos = g.getAttribute("position");
  for (let i = 0; i < pos.count; i++) {
    const y = pos.getY(i);
    // Narrower at the top, so it reads as falling.
    pos.setX(i, pos.getX(i) * (0.55 + 0.45 * (0.5 - y)));
  }
  pos.needsUpdate = true;
  return g;
}

function geometryFor(kind: EffectKind, seed: number): BufferGeometry {
  switch (kind) {
    case "confetti":
      return new PlaneGeometry(1, 0.62);
    case "rain":
      return stripGeometry();
    default:
      return puffGeometry(seed);
  }
}

const tmpColor = new Color();

export function PaperEffect({
  kind,
  origin = [0, 0, 0],
  emitter,
  spread,
  ground = 0,
  count,
  start = 0,
  end = Infinity,
  scale = 1,
  colors,
  seed = 1,
}: PaperEffectProps) {
  const n = count ?? DEFAULTS[kind].count;
  const palette = colors ?? DEFAULTS[kind].colors;
  const now = useStageTime();
  const mesh = useRef<InstancedMesh>(null);
  const dummy = useMemo(() => new Object3D(), []);
  const scratch = useRef<Vec3>([0, 0, 0]);
  const geometry = useMemo(() => geometryFor(kind, seed), [kind, seed]);
  const material = useMemo(() => {
    const m = createPaperMaterial({ color: "#ffffff", kind: "paper", side: DoubleSide, grain: 0.5, roughness: 0.95 });
    // A warm floor of light, so paper turned away from the key never goes black.
    m.emissive = new Color("#3b2c1e");
    return m;
  }, []);
  useEffect(() => () => geometry.dispose(), [geometry]);
  useEffect(() => () => material.dispose(), [material]);

  const paletteKey = palette.join(",");
  useEffect(() => {
    const m = mesh.current;
    if (!m) return;
    const cols = paletteKey.split(",");
    for (let i = 0; i < n; i++) m.setColorAt(i, tmpColor.set(cols[Math.floor(hash01(i, seed + 7) * cols.length)]));
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  }, [n, paletteKey, seed]);

  const [sx, sz] = spread ?? (kind === "rain" ? [1.05, 1.3] : [0.85, 0.5]);

  useFrame((state) => {
    const m = mesh.current;
    if (!m) return;
    const at = scratch.current;
    /** Origin at stage time `t` into `at`; false when not emitting then. */
    const source = (t: number) => {
      if (t < start || t > end) return false;
      if (!emitter) {
        at[0] = origin[0];
        at[1] = origin[1];
        at[2] = origin[2];
        return true;
      }
      return emitter(t, at);
    };
    const time = now(state);
    const t = time - start;
    const camQ = state.camera.quaternion;
    const camYaw = Math.atan2(state.camera.position.x - origin[0], state.camera.position.z - origin[2]);
    let moving = false;
    for (let i = 0; i < n; i++) {
      const h1 = hash01(i, seed);
      const h2 = hash01(i, seed + 1);
      const h3 = hash01(i, seed + 2);
      const h4 = hash01(i, seed + 3);
      let visible = true;
      if (kind === "confetti") {
        // Thrown in a few handfuls, flutters down with sway, then rests.
        const delay = Math.floor(h4 * 4) * 0.55 + h1 * 0.35;
        const age = t - delay;
        if (age < 0 || !source(start + delay)) visible = false;
        const fall = 0.32 + h2 * 0.18;
        const top = at[1] + 1.05 + h3 * 0.5;
        const y = top - fall * Math.max(0, age);
        const rest = ground + 0.004 + h1 * 0.003;
        const landed = y <= rest;
        const ax = age * (1.1 + h3);
        dummy.position.set(
          at[0] + (h1 - 0.5) * 2 * sx + Math.sin(ax * 1.3 + h2 * 6) * 0.08 * (landed ? 0 : 1),
          Math.max(rest, y),
          at[2] + (h2 - 0.5) * 2 * sz + Math.cos(ax + h1 * 6) * 0.05 * (landed ? 0 : 1),
        );
        if (landed) {
          dummy.rotation.set(-Math.PI / 2, 0, h3 * 6.28);
        } else {
          dummy.rotation.set(ax * (2 + h1 * 3), ax * (1.5 + h2 * 2), ax * (1 + h3));
          if (visible) moving = true;
        }
        // Lift-in at the start of each particle's flight.
        const s = 0.045 * scale * smoothstep(age / 0.25);
        dummy.scale.set(s, s, s);
      } else if (kind === "rain") {
        // Paper strips fall from a little above the set, twisting as they go,
        // and shrink into the page where they land.
        const life = 0.85 + h3 * 0.2;
        const age = ((t + h4 * life) % life + life) % life;
        const born = time - age;
        if (t < 0 || !source(born)) visible = false;
        const k = age / life;
        const top = at[1] + 1.85;
        const drift = 0.16;
        // Slanted fall, centred on the spawn column so strips stay over the page.
        const x0 = at[0] + (h1 - 0.5) * 2 * (sx - drift / 2) + drift / 2;
        dummy.position.set(x0 - k * drift, ground + 0.02 + (top - ground) * (1 - k), at[2] + (h2 - 0.5) * 2 * sz);
        dummy.rotation.set(0, camYaw + h2 * 6.28 + age * (3 + h1 * 5), 0.09);
        const s = 0.12 * scale * (0.7 + h3 * 0.6) * smoothstep(Math.min(k / 0.08, (1 - k) / 0.05));
        dummy.scale.set(s, s, s);
        if (visible) moving = true;
      } else {
        // Steam and smoke: puffs rise, grow, drift, then shrink away.
        const smoke = kind === "smoke";
        const life = smoke ? 4.6 : 2.4;
        const phase = (i / n) * life + h1 * 0.2;
        const bornRel = Math.floor((t - phase) / life) * life + phase;
        const age = t - bornRel;
        if (t < phase || !source(start + bornRel)) visible = false;
        const k = clamp(age / life);
        const rise = (smoke ? 1.35 : 0.75) * easeOutCubic(k) + k * (smoke ? 0.25 : 0.1);
        const drift = smoke ? 0.55 * k * k + Math.sin(age * 1.4 + h2 * 6) * 0.06 : Math.sin(age * 2.2 + h2 * 6) * 0.05 * k;
        dummy.position.set(at[0] + drift + (h3 - 0.5) * 0.04, at[1] + rise, at[2] + (h4 - 0.5) * 0.04 + i * 0.0004);
        dummy.quaternion.copy(camQ);
        dummy.rotateZ(h2 * 6.28 + age * (h3 - 0.5) * 0.6);
        const grow = smoke ? 0.06 + 0.2 * Math.sqrt(k) : 0.025 + 0.075 * Math.sqrt(k);
        const s = grow * scale * smoothstep(k / 0.08) * (1 - smoothstep((k - 0.62) / 0.38)) * (0.8 + h4 * 0.4);
        dummy.scale.set(s, s, s);
        if (visible) moving = true;
      }
      if (!visible) dummy.scale.set(0, 0, 0);
      dummy.updateMatrix();
      m.setMatrixAt(i, dummy.matrix);
    }
    m.instanceMatrix.needsUpdate = true;
    if (moving && kind !== "rain") markShadowsDirty(state);
  });

  return (
    <instancedMesh
      ref={mesh}
      args={[geometry, material, n]}
      frustumCulled={false}
      castShadow={kind !== "rain"}
      receiveShadow={kind !== "rain"}
    />
  );
}
