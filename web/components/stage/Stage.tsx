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
import { Canvas, useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  BufferAttribute,
  BufferGeometry,
  CanvasTexture,
  Float32BufferAttribute,
  MeshStandardMaterial,
  Object3D,
  PlaneGeometry,
  SRGBColorSpace,
  Vector2,
  type Group,
  type Mesh,
} from "three";
import { useCardBoxMaterials, usePaperMaterial, useWoodMaterial } from "@/components/paper/usePaper";
import { createCardEdgeMaterial, createPaperMaterial, getPaperTextures } from "@/components/paper/materials";
import { markShadowsDirty, StageClock, StaticShadows, useStageTime } from "./clock";
import { CameraRig, DEFAULT_STAGE_SIZE, stagePoses, type CameraPath, type CameraPose, type StageSize } from "./CameraRig";
import { DepthOfField } from "./DepthOfField";
import { PopUp, readOpen, type OpenSource, type PopUpTiming } from "./SetPiece";
import { rng, smootherstep } from "./timeline";

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
      {/* Ground colour = light bouncing off the page: it keeps card faces
          that tip towards the page (mid-fold) reading as paper, not black. */}
      <hemisphereLight args={["#fff0da", "#d6b68e", 0.66]} />
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
      {/* Bounce up off the page towards the reader: lights only faces that
          look down (a card mid-fold, tipped towards the reader), which the
          lamp cannot reach. The page and table face away from it. */}
      <directionalLight target={target} color="#f0d2a8" intensity={0.9} position={[0.4, -2.5, 3.2]} />
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

/** Width of the gutter valley at the spine, and how deep the pages sink there. */
const GUTTER = 0.36;
const DIP = 0.05;

/** How far into the gutter valley stage x is: 0 on the open page, 1 at the spine. */
function gutterK(x: number, w: number) {
  return Math.max(0, 1 - (x + w / 2) / GUTTER);
}

/** Height of the stage page's surface at stage x (it sinks towards the spine). */
export function pageHeightAt(x: number, size: StageSize = DEFAULT_STAGE_SIZE) {
  const k = gutterK(x, size[0]);
  return -DIP * k * k * (3 - 2 * k);
}

/**
 * A page that curves down into the gutter at its spine edge and darkens there
 * (vertex colours), so the open spread reads as a bound book, not a board.
 * `spine` is -1 when the spine is on the left (a right-hand page), 1 on the
 * right.
 */
function usePageGeometry(size: StageSize, spine: -1 | 1) {
  const geo = useMemo(() => {
    const [w, d] = size;
    const g = new PlaneGeometry(w, d, 64, 2);
    g.rotateX(-Math.PI / 2);
    const pos = g.getAttribute("position");
    const colors = new Float32Array(pos.count * 3);
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i);
      const k = gutterK(spine < 0 ? x : -x, w);
      pos.setY(i, -DIP * k * k * (3 - 2 * k));
      // Shade the valley: deepest at the spine, a soft falloff onto the page.
      const shade = 1 - 0.3 * Math.pow(k, 2.2);
      colors[i * 3] = shade;
      colors[i * 3 + 1] = shade * 0.985;
      colors[i * 3 + 2] = shade * 0.96;
    }
    g.setAttribute("color", new Float32BufferAttribute(colors, 3));
    pos.needsUpdate = true;
    g.computeVertexNormals();
    return g;
  }, [size, spine]);
  useEffect(() => () => geo.dispose(), [geo]);
  return geo;
}

/** Page block thickness, cover board thickness, and how far the boards overhang. */
const BLOCK = 0.07;
const BOARD = 0.03;
const OVERHANG = 0.08;
/**
 * Height of the book's hinge above the page. Closed, the left page lies
 * 2 x HINGE_Y above the right one, leaving room for the folded-flat set.
 */
const HINGE_Y = 0.036;
const SPINE_SEGMENTS = 16;

/** Book leather with a stitched border and a blind-tooled panel: the outside
 *  of the cover boards, seen when the book is closed. Built once. */
let leatherTexture: CanvasTexture | null = null;
function getLeatherTexture(aspect: number) {
  if (leatherTexture) return leatherTexture;
  const W = 512;
  const H = Math.round(W * aspect);
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const g = c.getContext("2d");
  if (g) {
    g.fillStyle = STAGE_COLORS.cover;
    g.fillRect(0, 0, W, H);
    // Mottled grain: soft blotches, then fine pores.
    const rand = rng(7);
    g.filter = "blur(10px)";
    for (let i = 0; i < 140; i++) {
      const r = 10 + rand() * 30;
      g.fillStyle = rand() < 0.5 ? `rgba(40, 16, 8, ${0.04 + rand() * 0.04})` : `rgba(255, 200, 160, ${0.02 + rand() * 0.03})`;
      g.beginPath();
      g.arc(rand() * W, rand() * H, r, 0, Math.PI * 2);
      g.fill();
    }
    g.filter = "none";
    for (let i = 0; i < 5000; i++) {
      g.fillStyle = `rgba(30, 10, 4, ${0.08 + rand() * 0.12})`;
      g.fillRect(rand() * W, rand() * H, 1, 1);
    }
    // Rubbed, lighter edges.
    const edge = g.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.35, W / 2, H / 2, Math.max(W, H) * 0.72);
    edge.addColorStop(0, "rgba(255, 210, 170, 0)");
    edge.addColorStop(1, "rgba(255, 210, 170, 0.14)");
    g.fillStyle = edge;
    g.fillRect(0, 0, W, H);
    // Blind-tooled panel: a pressed line with a highlight under it.
    const panel = (inset: number, dark: string, light: string) => {
      g.lineWidth = 2;
      g.strokeStyle = light;
      g.strokeRect(inset + 1, inset + 1, W - 2 * inset, H - 2 * inset);
      g.strokeStyle = dark;
      g.strokeRect(inset, inset, W - 2 * inset, H - 2 * inset);
    };
    panel(58, "rgba(30, 10, 4, 0.4)", "rgba(255, 210, 170, 0.16)");
    // Stitched border: thread dashes with a shadow under each.
    const inset = 26;
    g.setLineDash([11, 8]);
    g.lineCap = "round";
    g.lineWidth = 3;
    g.strokeStyle = "rgba(25, 8, 2, 0.45)";
    g.strokeRect(inset + 1, inset + 2, W - 2 * inset, H - 2 * inset);
    g.strokeStyle = "#e9d2ad";
    g.strokeRect(inset, inset, W - 2 * inset, H - 2 * inset);
    g.setLineDash([]);
  }
  leatherTexture = new CanvasTexture(c);
  leatherTexture.colorSpace = SRGBColorSpace;
  leatherTexture.anisotropy = 8;
  return leatherTexture;
}

export interface OpenBookProps {
  size?: StageSize;
  /**
   * How closed the book is: 0 lies open, 1 is closed on the table (the intro
   * state), in between the left half swings over on the spine. A number, a
   * function of stage time or a ref. The book owner can drop this; it is
   * what lets the standalone stage show a closed book at ?open=0.
   */
  closed?: OpenSource;
}

/** The open book under the stage: two pages, page blocks, cover boards and a
 *  spine. The right page is the stage page (centred on the origin); the left
 *  half can swing shut over it (`closed`). */
export function OpenBook({ size = DEFAULT_STAGE_SIZE, closed = 0 }: OpenBookProps) {
  const [w, d] = size;
  const rightGeo = usePageGeometry(size, -1);
  const leftGeo = usePageGeometry(size, 1);
  const page = useMemo(() => {
    const m = createPaperMaterial({ color: STAGE_COLORS.page, repeat: [w * 1.2, d * 1.2] });
    m.vertexColors = true;
    return m;
  }, [w, d]);
  useEffect(() => () => page.dispose(), [page]);
  const box = useCardBoxMaterials({ color: STAGE_COLORS.cover, repeat: [2, 1.4], edgeColor: "#4f281c" });
  const leather = useMemo(() => {
    const grain = getPaperTextures("card", [2, 2.6]);
    return new MeshStandardMaterial({
      map: getLeatherTexture((d + 2 * OVERHANG) / (w + OVERHANG)),
      normalMap: grain.normalMap,
      normalScale: new Vector2(0.55, 0.55),
      roughness: 0.72,
      metalness: 0,
    });
  }, [w, d]);
  useEffect(() => () => leather.dispose(), [leather]);
  // Box faces: [+x, -x, +y, -y, +z, -z]; the outside (-y) is leather.
  const coverMats = useMemo(() => [box[0], box[1], box[2], leather, box[4], box[5]], [box, leather]);
  const edge = useMemo(() => createCardEdgeMaterial({ color: STAGE_COLORS.pageEdge, darken: 0.04 }), []);
  useEffect(() => () => edge.dispose(), [edge]);

  // The spine: a band of cover leather around the hinge, from the right
  // board's inner edge to the left board's, rebuilt only while it swings.
  const spineGeo = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute("position", new BufferAttribute(new Float32Array((SPINE_SEGMENTS + 1) * 4 * 3), 3));
    const idx: number[] = [];
    for (let i = 0; i < SPINE_SEGMENTS; i++) {
      const a = i * 4;
      const b = (i + 1) * 4;
      idx.push(a, b, a + 1, b, b + 1, a + 1); // outside
      idx.push(a + 2, a + 3, b + 2, b + 2, a + 3, b + 3); // inside
    }
    g.setIndex(idx);
    return g;
  }, []);
  useEffect(() => () => spineGeo.dispose(), [spineGeo]);

  const now = useStageTime();
  const left = useRef<Group>(null);
  const spine = useRef<Mesh>(null);
  const last = useRef(NaN);
  useFrame((state) => {
    const k = readOpen(closed, now(state));
    if (k === last.current) return;
    last.current = k;
    markShadowsDirty(state);
    // Ease the swing so it lifts gently and lands softly.
    const phi = Math.PI * smootherstep(k);
    if (left.current) left.current.rotation.z = -phi;
    const sp = spine.current;
    if (!sp) return;
    sp.visible = phi > 0.01;
    if (!sp.visible) return;
    const pos = sp.geometry.getAttribute("position") as BufferAttribute;
    const ro = HINGE_Y + BLOCK + BOARD + 0.001;
    const ri = HINGE_Y + BLOCK + 0.001;
    const z0 = -d / 2 - OVERHANG;
    const z1 = d / 2 + OVERHANG;
    for (let i = 0; i <= SPINE_SEGMENTS; i++) {
      const b = -Math.PI / 2 - (phi * i) / SPINE_SEGMENTS;
      const cx = Math.cos(b);
      const cy = Math.sin(b);
      pos.setXYZ(i * 4, cx * ro, cy * ro, z0);
      pos.setXYZ(i * 4 + 1, cx * ro, cy * ro, z1);
      pos.setXYZ(i * 4 + 2, cx * ri, cy * ri, z0);
      pos.setXYZ(i * 4 + 3, cx * ri, cy * ri, z1);
    }
    pos.needsUpdate = true;
    sp.geometry.computeVertexNormals();
    sp.geometry.computeBoundingSphere();
  });

  // One half of the book, drawn for the right page (side 1) or the left (-1),
  // in coordinates centred on that page.
  const half = (side: 1 | -1, geo: BufferGeometry) => (
    <>
      <mesh geometry={geo} material={page} receiveShadow />
      {/* Page block: the stacked page edges under the sheet, lower under
          the gutter, where the pages dip into the spine. */}
      <mesh position={[side * (GUTTER / 2), -BLOCK / 2 - 0.001, 0]} material={edge} castShadow receiveShadow>
        <boxGeometry args={[w - GUTTER - 0.004, BLOCK, d - 0.01]} />
      </mesh>
      <mesh position={[-side * (w / 2 - GUTTER / 2), -BLOCK / 2 - DIP / 2 - 0.002, 0]} material={edge} receiveShadow>
        <boxGeometry args={[GUTTER, BLOCK - DIP, d - 0.01]} />
      </mesh>
      {/* Cover board, overhanging the pages on the three open sides. */}
      <mesh position={[side * (OVERHANG / 2), -BLOCK - BOARD / 2 - 0.001, 0]} material={coverMats} castShadow receiveShadow>
        <boxGeometry args={[w + OVERHANG, BOARD, d + 2 * OVERHANG]} />
      </mesh>
    </>
  );

  return (
    <group>
      {half(1, rightGeo)}
      {/* The left half hangs off the hinge, so it can swing shut. */}
      <group position={[-w / 2, HINGE_Y, 0]}>
        <group ref={left}>
          <group position={[-w / 2, -HINGE_Y, 0]}>{half(-1, leftGeo)}</group>
        </group>
        <mesh ref={spine} geometry={spineGeo} material={box[0]} castShadow receiveShadow frustumCulled={false} visible={false} />
      </group>
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
  /** Scrub (0..1: a number, a function of stage time, or a ref) or play
   *  (boolean) the pop-up. Default true. */
  open?: OpenSource | boolean;
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
  /** Soft depth of field focused on the camera target. Default false. */
  dof?: boolean;
  /**
   * When `open` is scrubbed, start from the closed book: the first part of
   * the range swings the cover open, the rest raises the set. Default true.
   */
  cover?: boolean;
}

/** Share of a scrubbed `open` (0..1) the cover takes to swing open; the set
 *  starts rising a little before the cover lands. */
export const COVER_SPAN = 0.22;
const SET_FROM = COVER_SPAN * 0.75;

export function Stage({
  time,
  camera,
  orbit = true,
  className,
  overlay,
  staticShadows = true,
  dof = false,
  cover = true,
  open = true,
  size = DEFAULT_STAGE_SIZE,
  ...scene
}: StageProps) {
  // Scrubbed: split the range between the cover and the set.
  const scrub = typeof open !== "boolean" && cover;
  const setOpen = useMemo<OpenSource | boolean>(
    () => (scrub ? (t: number) => (readOpen(open, t) - SET_FROM) / (1 - SET_FROM) : open),
    [scrub, open],
  );
  const closed = useMemo<OpenSource>(() => (scrub ? (t: number) => 1 - readOpen(open, t) / COVER_SPAN : 0), [scrub, open]);
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
        <OpenBook size={size} closed={closed} />
        <StageScene {...scene} open={setOpen} size={size} surface={false} />
        <CameraRig pose={pose} orbit={orbit} />
        {dof && <DepthOfField />}
        {overlay}
      </StageClock>
    </Canvas>
  );
}
