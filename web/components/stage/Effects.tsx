"use client";

/**
 * Paper particles: confetti, steam puffs, rain strips and smoke.
 *
 * Each effect is one InstancedMesh (one draw call) of small paper shapes. A
 * particle's state is a pure function of stage time and its index, so effects
 * need no simulation state, play the same every time, and freeze exactly.
 * Puffs fade by shrinking, never by transparency, so nothing needs sorting.
 */

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import {
  CircleGeometry,
  Color,
  DoubleSide,
  Object3D,
  PlaneGeometry,
  Shape,
  ShapeGeometry,
  type BufferGeometry,
  type InstancedMesh,
} from "three";
import { createPaperMaterial } from "@/components/paper/materials";
import { useStageTime } from "./clock";
import { clamp, easeOutCubic, hash01, smoothstep, type Vec3 } from "./timeline";

export type EffectKind = "confetti" | "steam" | "rain" | "smoke";

export const EFFECT_KINDS: readonly EffectKind[] = ["confetti", "steam", "rain", "smoke"];

export interface PaperEffectProps {
  kind: EffectKind;
  /** Where it comes from: the emitter for steam/smoke, the centre above which confetti/rain fall. */
  origin?: Vec3;
  /** Half-extent of the fall area (x, z) for confetti and rain. */
  spread?: [number, number];
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
  rain: { count: 220, colors: ["#9fbdd6", "#bcd0e0", "#8aaecb"] },
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

/** A long paper raindrop: a hair-thin tail swelling to a round foot. Unit height. */
function dropGeometry() {
  const s = new Shape();
  const r = 0.028;
  s.moveTo(0, 0.5);
  s.bezierCurveTo(0.004, 0.2, r, -0.36, r, -0.44);
  s.absarc(0, -0.44, r, 0, Math.PI, true);
  s.bezierCurveTo(-r, -0.36, -0.004, 0.2, 0, 0.5);
  return new ShapeGeometry(s, 8);
}

function geometryFor(kind: EffectKind, seed: number): BufferGeometry {
  switch (kind) {
    case "confetti":
      return new PlaneGeometry(1, 0.62);
    case "rain":
      return dropGeometry();
    default:
      return puffGeometry(seed);
  }
}

const tmpColor = new Color();

export function PaperEffect({
  kind,
  origin = [0, 0, 0],
  spread,
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

  const [sx, sz] = spread ?? (kind === "rain" ? [1.1, 1.35] : [0.85, 0.5]);

  useFrame((state) => {
    const m = mesh.current;
    if (!m) return;
    const t = now(state) - start;
    const camQ = state.camera.quaternion;
    const camYaw = Math.atan2(
      state.camera.position.x - origin[0],
      state.camera.position.z - origin[2],
    );
    for (let i = 0; i < n; i++) {
      const h1 = hash01(i, seed);
      const h2 = hash01(i, seed + 1);
      const h3 = hash01(i, seed + 2);
      const h4 = hash01(i, seed + 3);
      let visible = true;
      if (kind === "confetti") {
        // Thrown in a few handfuls, flutters down with sway, then rests on the page.
        const delay = Math.floor(h4 * 4) * 0.55 + h1 * 0.35;
        const age = t - delay;
        const life = 5.2;
        if (age < 0 || t > end - start + life) visible = false;
        const fall = 0.32 + h2 * 0.18;
        const top = origin[1] + 1.05 + h3 * 0.5;
        const y = top - fall * Math.max(0, age);
        const ground = 0.004 + h1 * 0.003;
        const landed = y <= ground;
        const ax = age * (1.1 + h3);
        dummy.position.set(
          origin[0] + (h1 - 0.5) * 2 * sx + Math.sin(ax * 1.3 + h2 * 6) * 0.08 * (landed ? 0 : 1),
          Math.max(ground, y),
          origin[2] + (h2 - 0.5) * 2 * sz + Math.cos(ax + h1 * 6) * 0.05 * (landed ? 0 : 1),
        );
        if (landed) {
          dummy.rotation.set(-Math.PI / 2, 0, h3 * 6.28);
        } else {
          dummy.rotation.set(ax * (2 + h1 * 3), ax * (1.5 + h2 * 2), ax * (1 + h3));
        }
        // Lift-in at the start of each particle's flight.
        const s = 0.045 * scale * smoothstep(age / 0.25);
        dummy.scale.set(s, s, s);
      } else if (kind === "rain") {
        const life = 0.72 + h3 * 0.18;
        const phase = h4 * life;
        const age = ((t + phase) % life + life) % life;
        const cycleStart = t - age;
        if (t < 0 || cycleStart > end - start) visible = false;
        const k = age / life;
        const top = origin[1] + 2.2;
        dummy.position.set(
          origin[0] + (h1 - 0.5) * 2 * sx - k * 0.18,
          top * (1 - k) + 0.02,
          origin[2] + (h2 - 0.5) * 2 * sz,
        );
        dummy.rotation.set(0, camYaw, 0.08);
        const s = 0.11 * scale * (0.7 + h3 * 0.6) * smoothstep(Math.min(k / 0.05, (1 - k) / 0.04));
        dummy.scale.set(s, s, s);
      } else {
        // Steam and smoke: puffs rise, grow, drift, then shrink away.
        const smoke = kind === "smoke";
        const life = smoke ? 4.6 : 2.4;
        const phase = (i / n) * life + h1 * 0.2;
        const born = Math.floor((t - phase) / life) * life + phase;
        const age = t - born;
        if (t < phase || born > end - start) visible = false;
        const k = clamp(age / life);
        const rise = (smoke ? 1.35 : 0.75) * easeOutCubic(k) + k * (smoke ? 0.25 : 0.1);
        const drift = smoke ? 0.55 * k * k + Math.sin(age * 1.4 + h2 * 6) * 0.06 : Math.sin(age * 2.2 + h2 * 6) * 0.05 * k;
        dummy.position.set(
          origin[0] + drift + (h3 - 0.5) * 0.06,
          origin[1] + rise,
          origin[2] + (h4 - 0.5) * 0.06 + i * 0.0004,
        );
        dummy.quaternion.copy(camQ);
        dummy.rotateZ(h2 * 6.28 + age * (h3 - 0.5) * 0.6);
        const grow = smoke ? 0.07 + 0.2 * Math.sqrt(k) : 0.025 + 0.075 * Math.sqrt(k);
        const s = grow * scale * smoothstep(k / 0.08) * (1 - smoothstep((k - 0.62) / 0.38)) * (0.8 + h4 * 0.4);
        dummy.scale.set(s, s, s);
      }
      if (!visible) dummy.scale.set(0, 0, 0);
      dummy.updateMatrix();
      m.setMatrixAt(i, dummy.matrix);
    }
    m.instanceMatrix.needsUpdate = true;
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
