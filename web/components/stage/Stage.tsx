"use client";

/**
 * The pop-up stage.
 *
 *   <StageScene>  the diorama on one page: warm key + fill light with soft
 *                 shadows, the page surface, and whatever pieces you put in
 *                 it. It lives inside an existing <Canvas>, so the book can
 *                 mount it in its right-page slot (pass lights={false} and
 *                 surface={false} when the book provides them).
 *   <Stage>       a standalone Canvas: an open book on a table under warm
 *                 light, with the StageScene on its right page and the camera
 *                 rig. Used by /render/[memoryId].
 *
 * Coordinates: the stage page lies in the XZ plane at y = 0, centred on the
 * origin, x to the right, z towards the reader. Set pieces stand on it.
 */

import { PerformanceMonitor } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { CanvasTexture, Object3D, PlaneGeometry, SRGBColorSpace } from "three";
import { useCardBoxMaterials, usePaperMaterial, useWoodMaterial } from "@/components/paper/usePaper";
import { createCardEdgeMaterial } from "@/components/paper/materials";
import { StageClock, StaticShadows } from "./clock";
import { CameraRig, DEFAULT_STAGE_SIZE, stagePoses, type CameraPath, type CameraPose, type StageSize } from "./CameraRig";
import { PopUp, type PopUpTiming } from "./SetPiece";

export const STAGE_COLORS = {
  page: "#f4ecdd",
  pageEdge: "#e6dac4",
  cover: "#6f3a2a",
  backdrop: "#24180f",
  fog: "#2c1e14",
} as const;

/* ------------------------------------------------------------------------ */
/* Lights                                                                    */
/* ------------------------------------------------------------------------ */

/** A reading lamp up and to the reader's left (a soft-edged warm pool, so
 *  the set glows and the book falls off into the room), a cool-neutral fill
 *  from the right, a warm rim from behind that makes cut card edges glow, and
 *  a sky/bounce hemisphere. The lamp is a spotlight so tall pieces throw
 *  crisp, readable silhouettes (the spire) onto the layers behind them. */
export function StageLights({ size = DEFAULT_STAGE_SIZE }: { size?: StageSize }) {
  const s = Math.max(size[0] / 2.4, size[1] / 3.2);
  // Aim at the stage's own origin, so the lights still land on the set when
  // the stage is mounted inside a moved group (the book's right page).
  const target = useMemo(() => {
    const o = new Object3D();
    o.position.set(0, 0.35 * s, -0.2 * s);
    return o;
  }, [s]);
  return (
    <>
      <primitive object={target} />
      <hemisphereLight args={["#fff0da", "#a07a5a", 0.62]} />
      <spotLight
        castShadow
        target={target}
        color="#ffd6a0"
        intensity={3.3}
        decay={0}
        angle={0.44}
        penumbra={0.8}
        position={[-3.1 * s, 5.6 * s, 4.4 * s]}
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.0002}
        shadow-normalBias={0.014}
        shadow-radius={5}
        shadow-intensity={0.78}
        shadow-camera-near={3 * s}
        shadow-camera-far={14 * s}
      />
      <directionalLight target={target} color="#ece6ee" intensity={0.5} position={[4.2, 2.2, 4.6]} />
      {/* Rim from behind: catches the top edges of the cards. */}
      <directionalLight target={target} color="#ffc68e" intensity={0.85} position={[1.6, 3, -4.5]} />
    </>
  );
}

/* ------------------------------------------------------------------------ */
/* Surfaces                                                                  */
/* ------------------------------------------------------------------------ */

/** A plain flat page for the stage (when nothing else draws one). */
export function PageSurface({ size = DEFAULT_STAGE_SIZE }: { size?: StageSize }) {
  const paper = usePaperMaterial({ color: STAGE_COLORS.page, repeat: [size[0] * 1.2, size[1] * 1.2] });
  return (
    <mesh rotation-x={-Math.PI / 2} receiveShadow material={paper}>
      <planeGeometry args={size} />
    </mesh>
  );
}

/** Width of the gutter dip at the spine, and how deep the pages sink there. */
const GUTTER = 0.16;
const DIP = 0.026;

/**
 * A page that dips gently into the gutter at its spine edge. `spine` is -1
 * when the spine is on the left (a right-hand page), 1 on the right.
 */
function usePageGeometry(size: StageSize, spine: -1 | 1) {
  const geo = useMemo(() => {
    const [w, d] = size;
    const g = new PlaneGeometry(w, d, 48, 2);
    g.rotateX(-Math.PI / 2);
    const pos = g.getAttribute("position");
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i);
      const fromSpine = spine < 0 ? x + w / 2 : w / 2 - x;
      const k = Math.max(0, 1 - fromSpine / GUTTER);
      pos.setY(i, -DIP * k * k * (3 - 2 * k));
    }
    pos.needsUpdate = true;
    g.computeVertexNormals();
    return g;
  }, [size, spine]);
  useEffect(() => () => geo.dispose(), [geo]);
  return geo;
}

/** The open book under the stage: two pages, page blocks and the cover board.
 *  The right page is the stage page (centred on the origin). */
export function OpenBook({ size = DEFAULT_STAGE_SIZE }: { size?: StageSize }) {
  const [w, d] = size;
  const block = 0.07;
  const rightGeo = usePageGeometry(size, -1);
  const leftGeo = usePageGeometry(size, 1);
  const page = usePaperMaterial({ color: STAGE_COLORS.page, repeat: [w * 1.2, d * 1.2] });
  const cover = useCardBoxMaterials({ color: STAGE_COLORS.cover, repeat: [2, 1.4], edgeColor: "#4f281c" });
  const edge = useMemo(() => createCardEdgeMaterial({ color: STAGE_COLORS.pageEdge, darken: 0.04 }), []);
  useEffect(() => () => edge.dispose(), [edge]);
  const leftX = -w;
  return (
    <group>
      <mesh geometry={rightGeo} material={page} receiveShadow />
      <mesh geometry={leftGeo} material={page} position-x={leftX} receiveShadow />
      {/* Page blocks: the stacked page edges under each sheet. Lower under
          the gutter, where the pages dip into the spine. */}
      {[0, leftX].map((cx, i) => {
        const outer = i === 0 ? 1 : -1;
        const gx = cx - outer * (w / 2 - GUTTER / 2);
        return (
          <group key={cx}>
            <mesh position={[cx + outer * (GUTTER / 2), -block / 2 - 0.001, 0]} material={edge} castShadow receiveShadow>
              <boxGeometry args={[w - GUTTER - 0.004, block, d - 0.01]} />
            </mesh>
            <mesh position={[gx, -block / 2 - DIP / 2 - 0.002, 0]} material={edge} receiveShadow>
              <boxGeometry args={[GUTTER, block - DIP, d - 0.01]} />
            </mesh>
          </group>
        );
      })}
      {/* Cover board, a touch larger than the pages. */}
      <mesh position={[-w / 2, -block - 0.016, 0]} material={cover} castShadow receiveShadow>
        <boxGeometry args={[w * 2 + 0.16, 0.03, d + 0.14]} />
      </mesh>
    </group>
  );
}

/** A lamp-lit wall behind the table: a warm glow above the book, falling off
 *  into dark corners. Screen-space background, so it never moves. */
function Room() {
  const tex = useMemo(() => {
    const c = document.createElement("canvas");
    c.width = 256;
    c.height = 256;
    const g = c.getContext("2d");
    if (g) {
      const grad = g.createRadialGradient(128, 64, 8, 128, 96, 200);
      grad.addColorStop(0, "#5a3d27");
      grad.addColorStop(0.5, "#352418");
      grad.addColorStop(1, STAGE_COLORS.backdrop);
      g.fillStyle = grad;
      g.fillRect(0, 0, 256, 256);
    }
    const t = new CanvasTexture(c);
    t.colorSpace = SRGBColorSpace;
    return t;
  }, []);
  useEffect(() => () => tex.dispose(), [tex]);
  return (
    <>
      <primitive attach="background" object={tex} />
      <fog attach="fog" args={[STAGE_COLORS.fog, 9, 20]} />
    </>
  );
}

/** The table, fading into the dark room. */
export function Table({ y = -0.102 }: { y?: number }) {
  const wood = useWoodMaterial({ repeat: [3, 3] });
  return (
    <mesh rotation-x={-Math.PI / 2} position-y={y} receiveShadow material={wood}>
      <planeGeometry args={[24, 24]} />
    </mesh>
  );
}

/* ------------------------------------------------------------------------ */
/* Scene                                                                     */
/* ------------------------------------------------------------------------ */

export interface StageSceneProps {
  /** Scrub (0..1) or play (boolean) the pop-up. Default true. */
  open?: number | boolean;
  /** When `open` starts true: stage time the set begins to rise. */
  openAt?: number;
  /** Optional scheduled fold-down, in stage time. */
  closeAt?: number | null;
  timing?: Partial<PopUpTiming>;
  size?: StageSize;
  /** Draw a plain page under the set. Default true. */
  surface?: boolean;
  /** Add the stage's key/fill lights. Default true. */
  lights?: boolean;
  children?: ReactNode;
}

export function StageScene({
  open = true,
  openAt,
  closeAt,
  timing,
  size = DEFAULT_STAGE_SIZE,
  surface = true,
  lights = true,
  children,
}: StageSceneProps) {
  return (
    <PopUp open={open} openAt={openAt} closeAt={closeAt} timing={timing}>
      {lights && <StageLights size={size} />}
      {surface && <PageSurface size={size} />}
      {children}
    </PopUp>
  );
}

export interface StageProps extends Omit<StageSceneProps, "surface"> {
  /** Freeze stage time (seconds). Omit to play. */
  time?: number | null;
  /** Camera pose or a path over stage time. Default: the reading view. */
  camera?: CameraPose | CameraPath;
  /** Drag to orbit a little, easing back on release. Default true. */
  orbit?: boolean;
  className?: string;
  /** Extra things in the Canvas outside the stage (helpers, frame probes). */
  overlay?: ReactNode;
  /** Redraw shadow maps only while something moves (see StaticShadows). Default true. */
  staticShadows?: boolean;
}

export function Stage({
  time,
  camera,
  orbit = true,
  className,
  overlay,
  staticShadows = true,
  size = DEFAULT_STAGE_SIZE,
  ...scene
}: StageProps) {
  const pose = camera ?? stagePoses(size).reading;
  const initial = typeof pose === "function" ? pose(time ?? 0) : pose;
  // Render at up to 1.5x on retina screens and step down if frames run long:
  // paper reads fine at 1.25x, a dropped frame does not.
  const [dpr, setDpr] = useState(1.5);
  return (
    <Canvas
      className={className}
      shadows="percentage"
      dpr={[1, dpr]}
      camera={{ position: initial.position, fov: initial.fov ?? 30, near: 0.1, far: 40 }}
      gl={{ antialias: true }}
    >
      <Room />
      <PerformanceMonitor
        flipflops={3}
        onDecline={() => setDpr((d) => Math.max(1, d - 0.25))}
        onIncline={() => setDpr((d) => Math.min(1.5, d + 0.25))}
        onFallback={() => setDpr(1)}
      />
      {staticShadows && <StaticShadows />}
      <StageClock time={time}>
        <Table />
        <OpenBook size={size} />
        <StageScene {...scene} size={size} surface={false} />
        <CameraRig pose={pose} orbit={orbit} />
        {overlay}
      </StageClock>
    </Canvas>
  );
}
