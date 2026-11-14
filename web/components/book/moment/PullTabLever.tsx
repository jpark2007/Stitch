"use client";

/**
 * <PullTabLever>: a pull tab on the stage page, like a real pop-up book's: a
 * strip of folded card coming out of a slit cut in the page (the slit and
 * its pencilled label are printed on the page, labelArt.drawPullTabPrint),
 * with a pencil arrow along it and its end folded back for a grip.
 *
 * Drag it out and it drives its interaction (CONTRACTS.md Clarifications 7):
 * the travel, 0..1, is held on the player (holdInteraction), so a scrub plays
 * its targets while the rest of the scene holds. Let go and it springs back
 * into the slit while the targets return to the scene's time ("return").
 * After the scene ends, it nudges out once to show it can be pulled.
 *
 * The lever's meshes carry userData.pageControl, so the book leaves pointer
 * events over it alone. Everything is posed in useFrame from refs; nothing
 * re-renders while it is pulled.
 */

import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import {
  CanvasTexture,
  ExtrudeGeometry,
  Matrix4,
  MeshStandardMaterial,
  Plane,
  Raycaster,
  SRGBColorSpace,
  Shape,
  Vector2,
  Vector3,
  type Group,
} from "three";
import type { PlaybackController } from "@/components/stage/playback";
import { drawLeverFace, pullTabGeometry } from "../labelArt";
import { PAGE_H, PAGE_W } from "../leafShape";
import { PAGE_PX_W } from "../pageArt";
import { holdPageControl, pageHeightAt, usePageSurface } from "../pagePrint";
import { paperLift } from "../sound";
import { STAGE_SLOT } from "../StoryPage";
import type { BookPullTab } from "../types";

/** World units per page pixel. */
const PX = PAGE_W / PAGE_PX_W;
const CARD_T = 0.0022;
/** How far (of the travel) the tab eases out under a hovering pointer. */
const HOVER_OUT = 0.07;
/** Length of the folded-back grip, world units. */
const FOLD = 0.024;

export interface PullTabLeverProps {
  controller: PlaybackController;
  tab: BookPullTab;
  /** The interaction's shot (null: every shot). */
  shot?: string | null;
  /** The stage's reveal: the lever goes back into its slit as the page turns away. */
  reveal: { readonly current: number };
}

/** A strip `len` × `w` with its far end rounded, lying in x (length) and y (width), extruded `t`. */
function stripGeometry(len: number, w: number, t: number, round: number) {
  const s = new Shape();
  const r = Math.min(round, w / 2);
  s.moveTo(0, -w / 2);
  s.lineTo(len - r, -w / 2);
  s.quadraticCurveTo(len, -w / 2, len, -w / 2 + r);
  s.lineTo(len, w / 2 - r);
  s.quadraticCurveTo(len, w / 2, len - r, w / 2);
  s.lineTo(0, w / 2);
  s.closePath();
  const g = new ExtrudeGeometry(s, { depth: t, bevelEnabled: false, curveSegments: 4 });
  // Lie flat: shape y -> -z, extrusion -> up.
  g.rotateX(-Math.PI / 2);
  return g;
}

export function PullTabLever({ controller, tab, shot = null, reveal }: PullTabLeverProps) {
  const rt = usePageSurface();
  const camera = useThree((s) => s.camera);
  const gl = useThree((s) => s.gl);
  const invalidate = useThree((s) => s.invalidate);

  const geo = useMemo(() => {
    const g = pullTabGeometry(tab);
    const length = g.length * PX;
    const width = g.width * PX;
    const travel = g.travel * PX;
    // The strip runs from inside the slit (hidden by the clip) to the grip.
    const hidden = travel + 0.01;
    return {
      slitX: g.slitX * PX - STAGE_SLOT.x,
      z: g.y * PX - PAGE_H / 2 - STAGE_SLOT.z,
      length,
      width,
      travel,
      hidden,
    };
  }, [tab]);

  const parts = useMemo(() => {
    const total = geo.hidden + geo.length;
    const body = stripGeometry(total, geo.width, CARD_T, 0.007);
    const flap = stripGeometry(FOLD, geo.width * 0.98, CARD_T * 0.9, 0.007);
    const shadow = stripGeometry(total + 0.004, geo.width + 0.008, 0.0001, 0.01);
    // The face: pencil arrow and fold, mapped over the visible length only.
    const pxW = 512;
    const pxH = Math.round((pxW * geo.width) / geo.length);
    const face = new CanvasTexture(drawLeverFace(pxW, pxH, (FOLD / geo.length) * pxW, 7));
    face.colorSpace = SRGBColorSpace;
    face.anisotropy = 8;
    // Shape coords -> uv: u along the length from the slit, v across.
    face.repeat.set(1 / geo.length, 1 / geo.width);
    face.offset.set(-geo.hidden / geo.length, 0.5);
    const card = new MeshStandardMaterial({ map: face, roughness: 0.9, color: "#ffffff" });
    const edge = new MeshStandardMaterial({ color: "#e8dcc4", roughness: 0.95 });
    const back = new MeshStandardMaterial({ color: "#b9573d", roughness: 0.92 });
    const backEdge = new MeshStandardMaterial({ color: "#9a4530", roughness: 0.95 });
    const shade = new MeshStandardMaterial({ color: "#2a170c", transparent: true, opacity: 0.22, depthWrite: false });
    return { body, flap, shadow, face, card, edge, back, backEdge, shade };
  }, [geo]);
  useEffect(
    () => () => {
      const p = parts;
      [p.body, p.flap, p.shadow, p.face, p.card, p.edge, p.back, p.backEdge, p.shade].forEach((x) => x.dispose());
    },
    [parts],
  );

  // The slit: everything left of it is under the page. One world-space plane,
  // kept on the slit as the book moves.
  const clip = useMemo(() => [new Plane(new Vector3(1, 0, 0), 0)], []);
  useEffect(() => {
    for (const m of [parts.card, parts.edge, parts.back, parts.backEdge, parts.shade]) {
      m.clippingPlanes = clip;
      m.clipShadows = true;
      m.needsUpdate = true;
    }
  }, [parts, clip]);

  const root = useRef<Group>(null);
  const slider = useRef<Group>(null);
  const flapPivot = useRef<Group>(null);

  /** Lever state: how far out (0..1 of travel), held or springing back. */
  const st = useRef({
    value: 0,
    vel: 0,
    held: false,
    hover: false,
    nudgeT0: -1,
    nudged: false,
    lastY: Number.NaN,
    /** Lets the book have the pointer back (set while held). */
    release: () => {},
  });

  // The scene ending invites a pull: one small nudge out of the slit.
  useEffect(
    () =>
      controller.subscribe(() => {
        const s = st.current;
        if (controller.getStatus().ended && !s.nudged && !s.held) {
          s.nudged = true;
          s.nudgeT0 = performance.now();
          invalidate();
        }
      }),
    [controller, invalidate],
  );

  const tmp = useMemo(
    () => ({ m: new Matrix4(), v: new Vector3(), n: new Vector3(1, 0, 0), ray: new Raycaster(), ndc: new Vector2(), plane: new Plane(), hit: new Vector3() }),
    [],
  );

  useFrame((_, delta) => {
    const g = root.current;
    const sl = slider.current;
    if (!g || !sl) return;
    const s = st.current;
    const dt = Math.min(delta, 0.05);
    // Back into the slit as the page lifts to turn.
    const r = reveal.current;
    g.visible = r > 0.02;
    const y = pageHeightAt(rt, geo.slitX + geo.length / 2);
    if (y !== s.lastY) {
      s.lastY = y;
      g.position.y = y + 0.0004;
    }
    let moving = false;
    // Under the pointer, the tab eases out a touch, as if lifted by a fingernail.
    const rest = s.hover ? HOVER_OUT : 0;
    if (!s.held && (s.value !== rest || s.vel !== 0)) {
      // A paper spring: quick, one soft overshoot.
      const k = 260;
      const c = 2 * Math.sqrt(k) * 0.62;
      s.vel += (-k * (s.value - rest) - c * s.vel) * dt;
      s.value += s.vel * dt;
      if (Math.abs(s.value - rest) < 1e-4 && Math.abs(s.vel) < 1e-3) {
        s.value = rest;
        s.vel = 0;
      }
      moving = true;
    }
    let nudge = 0;
    if (s.nudgeT0 > 0) {
      const t = (performance.now() - s.nudgeT0) / 1000;
      if (t > 1.6) s.nudgeT0 = -1;
      else {
        nudge = 0.22 * Math.sin(Math.min(1, t / 1.6) * Math.PI) * (1 + 0.25 * Math.sin(t * 9) * (1 - t / 1.6));
        moving = true;
      }
    }
    const out = Math.max(-0.04, s.value + nudge) * geo.travel;
    const folded = r < 1 ? (1 - r) * (geo.length + geo.travel) : 0;
    sl.position.x = out - folded;
    // The folded grip lifts a touch while held.
    if (flapPivot.current) flapPivot.current.rotation.z = s.held ? -0.16 : -0.07;
    // The slit plane, in world space (the book only moves while it opens).
    g.updateWorldMatrix(true, false);
    // The root sits on the slit, so the slit is its origin.
    tmp.v.set(0, 0, 0).applyMatrix4(g.matrixWorld);
    tmp.n.set(1, 0, 0).transformDirection(g.matrixWorld);
    clip[0].setFromNormalAndCoplanarPoint(tmp.n, tmp.v);
    if (moving) invalidate();
  });

  // Dragging: the pointer's ray meets the page, its slot x drives the travel.
  const pageX = (clientX: number, clientY: number): number | null => {
    const g = root.current;
    if (!g) return null;
    const rect = gl.domElement.getBoundingClientRect();
    tmp.ndc.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    tmp.ray.setFromCamera(tmp.ndc, camera);
    tmp.v.set(0, 0, 0).applyMatrix4(g.matrixWorld);
    tmp.plane.setFromNormalAndCoplanarPoint(tmp.n.set(0, 1, 0), tmp.v);
    if (!tmp.ray.ray.intersectPlane(tmp.plane, tmp.hit)) return null;
    tmp.m.copy(g.matrixWorld).invert();
    return tmp.hit.applyMatrix4(tmp.m).x;
  };

  const inShot = () => !shot || controller.frame.shot?.id == null || controller.frame.shot.id === shot;

  const onDown = (e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    if (e.button !== 0 || !inShot() || reveal.current < 0.9) return;
    const x0 = pageX(e.clientX, e.clientY);
    if (x0 === null) return;
    const s = st.current;
    const v0 = Math.max(0, s.value);
    s.held = true;
    s.vel = 0;
    s.nudgeT0 = -1;
    s.nudged = true;
    paperLift();
    s.release();
    s.release = holdPageControl();
    document.body.style.cursor = "grabbing";
    const id = e.pointerId;
    const hold = (v: number) => {
      s.value = v;
      controller.holdInteraction(tab.id, v);
      invalidate();
    };
    hold(v0);
    const move = (ev: PointerEvent) => {
      if (ev.pointerId !== id) return;
      const x = pageX(ev.clientX, ev.clientY);
      if (x === null) return;
      hold(Math.min(1, Math.max(0, v0 + (x - x0) / geo.travel)));
    };
    const up = (ev: PointerEvent) => {
      if (ev.pointerId !== id) return;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      s.held = false;
      s.release();
      document.body.style.cursor = "";
      // Let go: the targets return to the scene's time; the lever springs home.
      controller.releaseInteraction(tab.id, { settle: "return" });
      invalidate();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  };

  // Test hook, like the book's window.__stitchBook: where the grip is on
  // screen (CSS px) and how far a full pull travels there.
  useEffect(() => {
    const w = window as Window & { __stitchPullTabs?: Record<string, () => { x: number; y: number; travel: number } | null> };
    const tabs = (w.__stitchPullTabs ??= {});
    const at = new Vector3();
    const project = (x: number) => {
      const g = root.current;
      if (!g) return null;
      at.set(x, 0, 0).applyMatrix4(g.matrixWorld).project(camera);
      const r = gl.domElement.getBoundingClientRect();
      return { x: r.left + ((at.x + 1) / 2) * r.width, y: r.top + ((1 - at.y) / 2) * r.height };
    };
    tabs[tab.id] = () => {
      const a = project(geo.length - FOLD / 2);
      const b = project(geo.length - FOLD / 2 + geo.travel);
      return a && b ? { x: a.x, y: a.y, travel: Math.hypot(b.x - a.x, b.y - a.y) } : null;
    };
    return () => {
      delete tabs[tab.id];
    };
  }, [tab.id, geo, camera, gl]);

  // A held lever let go when the stage unmounts (the page turned mid-pull).
  useEffect(
    () => () => {
      if (st.current.held) controller.releaseInteraction(tab.id, { settle: "return" });
      st.current.release();
      document.body.style.cursor = "";
    },
    [controller, tab.id],
  );

  const handlers = {
    onPointerDown: onDown,
    onPointerOver: (e: ThreeEvent<PointerEvent>) => {
      e.stopPropagation();
      st.current.hover = e.pointerType === "mouse";
      invalidate();
      if (!st.current.held) document.body.style.cursor = "grab";
    },
    onPointerOut: () => {
      st.current.hover = false;
      invalidate();
      if (!st.current.held) document.body.style.cursor = "";
    },
    onPointerMove: (e: ThreeEvent<PointerEvent>) => e.stopPropagation(),
  };
  const control = { pageControl: true };

  return (
    <group ref={root} position={[geo.slitX, 0, geo.z]}>
      <group ref={slider}>
        {/* A soft shade under the card on the page. */}
        <mesh geometry={parts.shadow} material={parts.shade} position={[-geo.hidden + 0.0015, 0.00005, 0.0025]} renderOrder={2} />
        <mesh
          geometry={parts.body}
          material={[parts.card, parts.edge]}
          position={[-geo.hidden, 0.0004, 0]}
          castShadow
          userData={control}
          {...handlers}
        />
        {/* The grip: the card's end folded back over itself. */}
        <group ref={flapPivot} position={[geo.length, 0.0004 + CARD_T, 0]}>
          <mesh
            geometry={parts.flap}
            material={[parts.back, parts.backEdge]}
            position={[-FOLD, 0, 0]}
            castShadow
            userData={control}
            {...handlers}
          />
        </group>
      </group>
    </group>
  );
}
