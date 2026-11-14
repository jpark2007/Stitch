"use client";

/**
 * <PullTabLever>: a pull tab on the stage page, like a real pop-up book's: a
 * strip of card coming out of a slit cut in the page (the slit and its
 * pencilled label are printed on the page, labelArt.drawPullTabPrint),
 * stitched round its edge, chevrons pointing the way it pulls, its end
 * folded back for a grip. The cut page's lip shades it just out of the slit.
 *
 * Drag it out and it drives its interaction (CONTRACTS.md Clarifications 7):
 * the travel, 0..1, is held on the player (holdInteraction), so a scrub plays
 * its targets while the rest of the scene holds. Let go and it springs back
 * into the slit while the targets return to the scene's time ("return").
 * It grabs anywhere on the strip (a hit area wider than the card), and any
 * drag away from the slit pulls: out along the tab, or down or up the page.
 * A hold from elsewhere (the keyboard's slider) moves it too.
 *
 * A beat after the scene starts it nudges out once, so the reader sees it
 * can be pulled, and again when the scene ends if it has not been pulled.
 *
 * The lever is marked as a book control (controls.ts), so the book leaves
 * pointer events over it alone. Everything is posed in useFrame from refs;
 * nothing re-renders while it is pulled.
 */

import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import {
  CanvasTexture,
  ExtrudeGeometry,
  Matrix4,
  MeshBasicMaterial,
  MeshStandardMaterial,
  Plane,
  PlaneGeometry,
  Raycaster,
  SRGBColorSpace,
  Shape,
  Vector2,
  Vector3,
  type Group,
  type Mesh,
} from "three";
import type { PlaybackController } from "@/components/stage/playback";
import { holdBookControl, markBookControl } from "../controls";
import { drawLeverFace, drawLipShade, pullTabGeometry } from "../labelArt";
import { PAGE_H, PAGE_W } from "../leafShape";
import { PAGE_PX_W } from "../pageArt";
import { pageHeightAt, usePageSurface } from "../pagePrint";
import { pageLand, paperLift } from "../sound";
import { STAGE_SLOT } from "../StoryPage";
import type { BookPullTab } from "../types";
import { registerTabProbe } from "./probe";

/** World units per page pixel. */
const PX = PAGE_W / PAGE_PX_W;
/** Card thickness: enough to show as a darker edge along the strip and round its tip. */
const CARD_T = 0.003;
/** How far (of the travel) the tab eases out under a hovering pointer. */
const HOVER_OUT = 0.07;
/** Length of the folded-back grip, world units. */
const FOLD = 0.03;
/** How far down the card the cut page's lip shades it, world units. */
const LIP = 0.016;
/** The grab area reaches this far past the card on every side, world units. */
const HIT_PAD = 0.026;
/** A drag across the tab (down or up the page) pulls this much as one along it. */
const ACROSS = 0.7;
/** Scene time (s) at which the tab nudges once, while the scene plays. */
const NUDGE_AT = 2.2;
const NUDGE_DUR = 1.4;
const NUDGE_OUT = 0.3;

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
    const travel = g.travel * PX;
    // The strip runs from inside the slit (hidden by the clip) to the grip.
    const hidden = travel + 0.012;
    return {
      slitX: g.slitX * PX - STAGE_SLOT.x,
      z: g.y * PX - PAGE_H / 2 - STAGE_SLOT.z,
      length,
      width: g.width * PX,
      travel,
      hidden,
    };
  }, [tab]);

  const parts = useMemo(() => {
    const total = geo.hidden + geo.length;
    const body = stripGeometry(total, geo.width, CARD_T, 0.008);
    const flap = stripGeometry(FOLD, geo.width * 0.98, CARD_T * 0.8, 0.008);
    const shadow = stripGeometry(total + 0.005, geo.width + 0.01, 0.0001, 0.012);
    // The grab area, invisible: the card and a margin round it.
    const hit = new PlaneGeometry(geo.length + 2 * HIT_PAD, geo.width + 2 * HIT_PAD);
    hit.rotateX(-Math.PI / 2);
    hit.translate(geo.length / 2, 0, 0);
    // The lip's shade, fixed at the slit.
    const lip = new PlaneGeometry(LIP, geo.width + 0.002);
    lip.rotateX(-Math.PI / 2);
    lip.translate(LIP / 2, 0, 0);
    // The face covers the whole strip, so a pulled card shows card, not a smear.
    const pxPerUnit = 1024 / total;
    const face = new CanvasTexture(
      drawLeverFace(1024, Math.round(geo.width * pxPerUnit), FOLD * pxPerUnit, 7, geo.width / PX),
    );
    face.colorSpace = SRGBColorSpace;
    face.anisotropy = 8;
    // Shape coords -> uv: u along the strip, v across.
    face.repeat.set(1 / total, 1 / geo.width);
    face.offset.set(0, 0.5);
    const lipMap = new CanvasTexture(drawLipShade());
    const card = new MeshStandardMaterial({ map: face, roughness: 0.88, color: "#ffffff" });
    const edge = new MeshStandardMaterial({ color: "#c9b48e", roughness: 0.95 });
    const back = new MeshStandardMaterial({ color: "#b9573d", roughness: 0.92 });
    const backEdge = new MeshStandardMaterial({ color: "#8e3f2b", roughness: 0.95 });
    const shade = new MeshStandardMaterial({ color: "#2a170c", transparent: true, opacity: 0.24, depthWrite: false });
    const lipShade = new MeshBasicMaterial({ color: "#2a170c", alphaMap: lipMap, transparent: true, opacity: 0.5, depthWrite: false });
    const grab = new MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false, colorWrite: false });
    return { body, flap, shadow, hit, lip, face, lipMap, card, edge, back, backEdge, shade, lipShade, grab };
  }, [geo]);
  useEffect(
    () => () => {
      const p = parts;
      [p.body, p.flap, p.shadow, p.hit, p.lip, p.face, p.lipMap, p.card, p.edge, p.back, p.backEdge, p.shade, p.lipShade, p.grab].forEach(
        (x) => x.dispose(),
      );
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
  const lipMesh = useRef<Mesh>(null);

  /** Lever state: how far out (0..1 of travel), held or springing back. */
  const st = useRef({
    value: 0,
    vel: 0,
    /** Under the pointer's hand. */
    held: false,
    hover: false,
    /** Pulled at least once (by hand or key): no more nudges. */
    pulled: false,
    nudgeT0: -1,
    nudgedEarly: false,
    nudgedEnd: false,
    /** The value a hold from elsewhere (the keyboard) has it at, or null; kept from the player's status. */
    external: null as number | null,
    lastY: Number.NaN,
    /** Lets the book have the pointer back (set while held). */
    release: () => {},
  });

  const inShot = () => !shot || controller.frame.shot?.id == null || controller.frame.shot.id === shot;

  // Every hold emits a status, so the player's status says when the
  // keyboard holds the tab (read here, not every frame). And the scene
  // ending invites a pull, if it has not been pulled yet.
  useEffect(
    () =>
      controller.subscribe(() => {
        const s = st.current;
        const external = controller.heldValue(tab.id);
        if (external !== s.external) {
          s.external = external;
          invalidate();
        }
        if (controller.getStatus().ended && !s.nudgedEnd && !s.pulled && !s.held) {
          s.nudgedEnd = true;
          s.nudgeT0 = performance.now();
          invalidate();
        }
      }),
    [controller, invalidate, tab.id],
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
    // The lip shades card, not bare page: it fades as the card slides home.
    const lipMat = lipMesh.current?.material as MeshBasicMaterial | undefined;
    const lip = 0.5 * Math.min(1, Math.max(0, (r - 0.6) / 0.4));
    if (lipMat && lipMat.opacity !== lip) lipMat.opacity = lip;
    const y = pageHeightAt(rt, geo.slitX + geo.length / 2);
    if (y !== s.lastY) {
      s.lastY = y;
      g.position.y = y + 0.0004;
    }
    let moving = false;

    // A hold from elsewhere (the keyboard's slider): follow it.
    const external = s.held ? null : s.external;
    if (external !== null) {
      if (external !== s.value) moving = true;
      s.value = external;
      s.vel = 0;
      s.pulled = true;
      s.nudgeT0 = -1;
    } else if (!s.held) {
      // Under the pointer, the tab eases out a touch, as if lifted by a fingernail.
      const rest = s.hover ? HOVER_OUT : 0;
      if (s.value !== rest || s.vel !== 0) {
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
    }

    // Once, a beat into the scene: a nudge out of the slit.
    if (!s.nudgedEarly && !s.pulled && !s.held && r > 0.98 && controller.time >= NUDGE_AT && controller.getStatus().playing && inShot()) {
      s.nudgedEarly = true;
      s.nudgeT0 = performance.now();
    }
    let out = 0;
    if (s.nudgeT0 > 0) {
      const t = (performance.now() - s.nudgeT0) / 1000;
      if (t > NUDGE_DUR || s.held) s.nudgeT0 = -1;
      else {
        const u = t / NUDGE_DUR;
        out = NUDGE_OUT * Math.sin(u * Math.PI) * (1 + 0.25 * Math.sin(t * 9) * (1 - u));
        moving = true;
      }
    }
    const pos = Math.max(-0.04, s.value + out) * geo.travel;
    const folded = r < 1 ? (1 - r) * (geo.length + geo.travel) : 0;
    sl.position.x = pos - folded;
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

  /** Where the pointer's ray meets the page, in the lever's frame (x along the tab, z across), or false. */
  const pagePoint = (clientX: number, clientY: number, out: Vector2): boolean => {
    const g = root.current;
    if (!g) return false;
    const rect = gl.domElement.getBoundingClientRect();
    tmp.ndc.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    tmp.ray.setFromCamera(tmp.ndc, camera);
    tmp.v.set(0, 0, 0).applyMatrix4(g.matrixWorld);
    tmp.plane.setFromNormalAndCoplanarPoint(tmp.n.set(0, 1, 0), tmp.v);
    if (!tmp.ray.ray.intersectPlane(tmp.plane, tmp.hit)) return false;
    tmp.m.copy(g.matrixWorld).invert();
    tmp.hit.applyMatrix4(tmp.m);
    out.set(tmp.hit.x, tmp.hit.z);
    return true;
  };

  const drag = useMemo(() => ({ p0: new Vector2(), p: new Vector2() }), []);

  const onDown = (e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    if ((e.pointerType === "mouse" && e.button !== 0) || !inShot() || reveal.current < 0.9) return;
    if (!pagePoint(e.clientX, e.clientY, drag.p0)) return;
    const s = st.current;
    const v0 = Math.max(0, s.value);
    s.held = true;
    s.pulled = true;
    s.vel = 0;
    s.nudgeT0 = -1;
    paperLift();
    s.release();
    s.release = holdBookControl(rt);
    document.body.style.cursor = "grabbing";
    const id = e.pointerId;
    const hold = (v: number) => {
      s.value = v;
      controller.holdInteraction(tab.id, v);
      invalidate();
    };
    hold(v0);
    const move = (ev: PointerEvent) => {
      if (ev.pointerId !== id || !pagePoint(ev.clientX, ev.clientY, drag.p)) return;
      // Out along the tab, or across it: either way away from the slit pulls.
      const along = drag.p.x - drag.p0.x + ACROSS * Math.abs(drag.p.y - drag.p0.y);
      hold(Math.min(1, Math.max(0, v0 + along / geo.travel)));
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
      if (s.value > 0.15) pageLand(1);
      controller.releaseInteraction(tab.id, { settle: "return" });
      invalidate();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  };

  // Test hook (window.__stitchMoment.tab): where the grip is on screen now,
  // and how far and which way a full pull travels there.
  useEffect(() => {
    const at = new Vector3();
    const project = (x: number) => {
      const g = root.current;
      if (!g) return null;
      at.set(x, 0, 0).applyMatrix4(g.matrixWorld).project(camera);
      const r = gl.domElement.getBoundingClientRect();
      return { x: r.left + ((at.x + 1) / 2) * r.width, y: r.top + ((1 - at.y) / 2) * r.height };
    };
    return registerTabProbe(tab.id, () => {
      const x = (slider.current?.position.x ?? 0) + geo.length - FOLD / 2;
      const a = project(x);
      const b = project(x + geo.travel);
      if (!a || !b) return null;
      const travel = Math.hypot(b.x - a.x, b.y - a.y);
      return { x: a.x, y: a.y, travel, dx: (b.x - a.x) / travel, dy: (b.y - a.y) / travel };
    });
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

  return (
    <group
      ref={(g) => {
        root.current = g;
        markBookControl(g);
      }}
      position={[geo.slitX, 0, geo.z]}
      onPointerDown={onDown}
      onPointerOver={(e) => {
        e.stopPropagation();
        st.current.hover = e.pointerType === "mouse";
        invalidate();
        if (!st.current.held) document.body.style.cursor = "grab";
      }}
      onPointerOut={() => {
        st.current.hover = false;
        invalidate();
        if (!st.current.held) document.body.style.cursor = "";
      }}
      onPointerMove={(e) => e.stopPropagation()}
    >
      {/* The cut page's lip, shading the card just out of the slit. */}
      <mesh ref={lipMesh} geometry={parts.lip} material={parts.lipShade} position-y={CARD_T + 0.0007} renderOrder={3} />
      <group ref={slider}>
        {/* A soft shade under the card on the page. */}
        <mesh geometry={parts.shadow} material={parts.shade} position={[-geo.hidden + 0.002, 0.00005, 0.003]} renderOrder={2} />
        <mesh geometry={parts.body} material={[parts.card, parts.edge]} position={[-geo.hidden, 0.0004, 0]} castShadow />
        {/* The grip: the card's end folded back over itself. */}
        <group ref={flapPivot} position={[geo.length, 0.0004 + CARD_T, 0]}>
          <mesh geometry={parts.flap} material={[parts.back, parts.backEdge]} position={[-FOLD, 0, 0]} castShadow />
        </group>
        {/* Grab anywhere on the strip, or just off it. */}
        <mesh geometry={parts.hit} material={parts.grab} position-y={CARD_T + 0.002} />
      </group>
    </group>
  );
}
