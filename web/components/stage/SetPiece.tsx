"use client";

/**
 * Hinged paper set pieces.
 *
 * A <SetPiece> is one card cutout standing on its bottom fold: an alpha-masked
 * front, a plain card back, and thin walls tracing the outline so the card has
 * visible thickness. It hangs off nested groups:
 *
 *   base (on the page, at the fold)
 *     hinge   rotation.x: flat (folded towards the reader) -> upright
 *       half  rotation.y: V-fold panels swing about the centre crease
 *         front / back / edge meshes
 *
 * How far each piece has risen comes from the nearest <PopUp>:
 *   open: OpenSource  scrubbed: a number, a function of stage time, or a ref
 *                  the host writes (the book's page turn drives the set
 *                  through a ref with no React re-renders). Pieces rise in a
 *                  depth stagger (back first), no springs, so any value is a
 *                  pose.
 *   open: boolean  played. Each flip schedules, per piece (back first when
 *                  opening, front first when closing), an accelerating ramp
 *                  that a critically damped hinge spring follows: it carries
 *                  the ramp's final speed into one small overshoot and settles.
 *                  Evaluated in closed form from stage time, so frozen frames
 *                  are exact.
 *
 * Folded flat, back pieces lie on top of front ones (they fold forwards over
 * them), which is why they must rise first.
 */

import { useFrame, useThree } from "@react-three/fiber";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  BackSide,
  DoubleSide,
  Float32BufferAttribute,
  FrontSide,
  MeshStandardMaterial,
  PlaneGeometry,
  type BufferGeometry,
  type Group,
} from "three";
import { createCardEdgeMaterial, createPaperMaterial, getPaperTextures } from "@/components/paper/materials";
import { markShadowsDirty, useStageTime } from "./clock";
import type { Cutout } from "./cutout";
import { clamp, followAt, linearAt, smootherstep, smoothstep, type LinearKeys, type Vec3 } from "./timeline";

/* ------------------------------------------------------------------------ */
/* Pop-up controller                                                         */
/* ------------------------------------------------------------------------ */

export interface PopUpTiming {
  /** Seconds between pieces when opening (back first). */
  openStagger: number;
  /** Seconds between pieces when closing (front first). */
  closeStagger: number;
  /** Seconds a unit-height piece takes to rise / fold down (taller is slower). */
  openTime: number;
  closeTime: number;
  /** Hinge spring speed (rad/s): lower is looser, with a bigger pop. */
  omega: number;
  /** For scrubbed `open`: share of the 0..1 range between piece starts. */
  scrubStagger: number;
}

const DEFAULT_TIMING: PopUpTiming = {
  openStagger: 0.2,
  closeStagger: 0.1,
  openTime: 0.72,
  closeTime: 0.55,
  omega: 11,
  scrubStagger: 0.16,
};

export interface PopUpValue {
  /**
   * Rise of a piece, 0 flat .. 1 upright (briefly above 1 on the pop).
   * `size` stretches the motion: ~sqrt(height), so tall cards move slower.
   */
  rise(order: number, count: number, t: number, size: number): number;
}

/**
 * The hinge's target path for one piece: for each flip, a ramp from wherever
 * the target is towards 1 (open) or 0 (closed) that starts slowly and speeds
 * up, as a page opening drives a real mechanism. The spring following it
 * carries the final speed into one small overshoot (opening) or a soft landing
 * (closing, clamped at the page).
 */
function hingePath(flips: readonly Flip[], order: number, count: number, size: number, t: PopUpTiming): LinearKeys {
  const timed = flips
    .map((f) => ({ ...f, at: f.at + (f.open ? order * t.openStagger : (count - 1 - order) * t.closeStagger) }))
    .sort((a, b) => a.at - b.at);
  let keys: LinearKeys = [];
  for (const f of timed) {
    const from = linearAt(keys, f.at, 0);
    keys = keys.filter((k) => k[0] < f.at);
    keys.push([f.at, from]);
    const to = f.open ? 1 : 0;
    const dist = Math.abs(to - from);
    if (dist < 1e-3) continue;
    const dur = (f.open ? t.openTime : t.closeTime) * size * (0.35 + 0.65 * dist);
    keys.push([f.at + dur * 0.6, from + (to - from) * 0.3]);
    keys.push([f.at + dur, to]);
  }
  return keys;
}

const PopUpContext = createContext<PopUpValue>({ rise: () => 1 });

/** The enclosing <PopUp>'s rise function (1 everywhere outside a PopUp). */
export function usePopUp(): PopUpValue {
  return useContext(PopUpContext);
}

/** Scrubbed open progress (0 flat .. 1 risen): a fixed value, a function of
 *  stage time, or a ref the host updates every frame. */
export type OpenSource = number | ((t: number) => number) | { readonly current: number };

/** Read an OpenSource at stage time `t`, clamped to 0..1. */
export function readOpen(src: OpenSource, t: number): number {
  const v = typeof src === "number" ? src : typeof src === "function" ? src(t) : src.current;
  return clamp(v, 0, 1);
}

interface Flip {
  at: number;
  open: boolean;
}

export interface PopUpProps {
  /** Scrub (an OpenSource, 0..1) or play (boolean). Default true. */
  open?: OpenSource | boolean;
  /** When `open` starts true: stage time at which the set begins to rise. */
  openAt?: number;
  /** Optional scheduled fold-down, in stage time (e.g. before a page turn). */
  closeAt?: number | null;
  timing?: Partial<PopUpTiming>;
  children?: ReactNode;
}

export function PopUp({ open = true, openAt = 0.4, closeAt = null, timing, children }: PopUpProps) {
  const { openStagger, closeStagger, openTime, closeTime, omega, scrubStagger } = { ...DEFAULT_TIMING, ...timing };
  const timingRef = useMemo<PopUpTiming>(
    () => ({ openStagger, closeStagger, openTime, closeTime, omega, scrubStagger }),
    [openStagger, closeStagger, openTime, closeTime, omega, scrubStagger],
  );
  const now = useStageTime();
  const get = useThree((s) => s.get);

  // Boolean mode: flips in stage time. The scheduled ones come from props;
  // live ones are appended when `open` changes after mount.
  const [startsOpen] = useState(open === true);
  const scheduled = useMemo<Flip[]>(() => {
    const list: Flip[] = startsOpen ? [{ at: openAt, open: true }] : [];
    if (closeAt !== null) list.push({ at: closeAt, open: false });
    return list;
  }, [startsOpen, openAt, closeAt]);
  const live = useRef<Flip[]>([]);
  const version = useRef(0);
  const last = useRef(open);
  useEffect(() => {
    if (typeof open !== "boolean" || open === last.current) return;
    last.current = open;
    live.current = [...live.current, { at: now(get()), open }];
    version.current += 1;
  }, [open, now, get]);

  const value = useMemo<PopUpValue>(() => {
    if (typeof open !== "boolean") {
      return {
        rise(order, count, time) {
          // Overlap the pieces so mid-scrub shows a cascade, not one card at a time.
          const s = Math.min(scrubStagger, 0.5 / Math.max(1, count - 1));
          const span = 1 - s * (count - 1);
          return smootherstep((readOpen(open, time) - order * s) / span);
        },
      };
    }
    // Each piece's hinge path, rebuilt only when the flips change.
    const cache = new Map<string, { v: number; keys: LinearKeys }>();
    return {
      rise(order, count, time, size) {
        const key = `${order}|${count}|${size}`;
        let hit = cache.get(key);
        if (!hit || hit.v !== version.current) {
          hit = { v: version.current, keys: hingePath([...scheduled, ...live.current], order, count, size, timingRef) };
          cache.set(key, hit);
        }
        return Math.max(0, followAt(hit.keys, time, timingRef.omega / Math.sqrt(size), 0));
      },
    };
  }, [open, scheduled, timingRef, scrubStagger]);

  return <PopUpContext.Provider value={value}>{children}</PopUpContext.Provider>;
}

/* ------------------------------------------------------------------------ */
/* Set piece                                                                 */
/* ------------------------------------------------------------------------ */

export interface SetPieceProps {
  cutout: Cutout;
  /** Height of the card in world units (width follows the cutout's aspect). */
  height: number;
  /** Fold line centre on the page. */
  x?: number;
  z?: number;
  /** Turn about the vertical axis (radians), for pieces set at an angle. */
  yaw?: number;
  /** Stagger slot: 0 rises first. Pieces further back should come first. */
  order?: number;
  count?: number;
  /** V-fold: the angle each panel swings back from the crease at full open. 0 = flat piece. */
  vfold?: number;
  /** Card thickness. Default 0.007. */
  thickness?: number;
  /** Colour of the card's back and cut edge. */
  backColor?: string;
  /** Motion scale: >1 is slower. Default sqrt(height), so tall cards are slower. */
  size?: number;
}

const TAB_DEPTH = 0.06;

/**
 * Shared glue-tab geometry: a unit strip lying on the page, from the fold
 * (z = 0) back to z = -1, shaded in vertex colours so it reads as folded card:
 * a crease shadow along the fold, flat card behind it, a faint cut edge.
 */
let tabGeometry: BufferGeometry | null = null;
function getTabGeometry() {
  if (tabGeometry) return tabGeometry;
  const g = new PlaneGeometry(1, 1, 1, 10);
  g.rotateX(-Math.PI / 2);
  g.translate(0, 0, -0.5);
  const pos = g.getAttribute("position");
  const col = new Float32Array(pos.count * 3);
  for (let i = 0; i < pos.count; i++) {
    const v = -pos.getZ(i); // 0 at the fold, 1 at the far edge
    const shade = 1 - 0.3 * Math.exp(-v / 0.1) - 0.07 * smoothstep((v - 0.85) / 0.15);
    col[i * 3] = shade;
    col[i * 3 + 1] = shade * 0.99;
    col[i * 3 + 2] = shade * 0.97;
  }
  g.setAttribute("color", new Float32BufferAttribute(col, 3));
  tabGeometry = g;
  return g;
}

type Half = { key: string; plane: BufferGeometry; edge: BufferGeometry; sign: number };

function halfPlane(aspect: number, side: -1 | 0 | 1) {
  const w = side === 0 ? aspect : aspect / 2;
  const g = new PlaneGeometry(w, 1);
  g.translate(side * (aspect / 4), 0.5, 0);
  if (side !== 0) {
    const uv = g.getAttribute("uv");
    for (let i = 0; i < uv.count; i++) uv.setX(i, (side < 0 ? 0 : 0.5) + uv.getX(i) * 0.5);
    uv.needsUpdate = true;
  }
  return g;
}

export function SetPiece({
  cutout,
  height,
  x = 0,
  z = 0,
  yaw = 0,
  order = 0,
  count = 1,
  vfold = 0,
  thickness = 0.007,
  backColor = "#e9dfcb",
  size,
}: SetPieceProps) {
  const { rise } = useContext(PopUpContext);
  const now = useStageTime();
  const hinge = useRef<Group>(null);
  const halvesRef = useRef<(Group | null)[]>([]);
  const w = cutout.aspect * height;
  const motion = size ?? Math.sqrt(Math.max(0.25, height));

  const halves = useMemo<Half[]>(
    () =>
      vfold > 0
        ? [
            { key: "l", plane: halfPlane(cutout.aspect, -1), edge: cutout.edgeLeft, sign: -1 },
            { key: "r", plane: halfPlane(cutout.aspect, 1), edge: cutout.edgeRight, sign: 1 },
          ]
        : [{ key: "f", plane: halfPlane(cutout.aspect, 0), edge: cutout.edge, sign: 0 }],
    [cutout, vfold],
  );
  useEffect(() => () => halves.forEach((h) => h.plane.dispose()), [halves]);

  const materials = useMemo(() => {
    const rep: [number, number] = [Math.max(0.5, w * 1.1), Math.max(0.5, height * 1.1)];
    const grain = getPaperTextures("card", rep);
    const front = new MeshStandardMaterial({
      map: cutout.map,
      alphaMap: cutout.alphaMap,
      alphaTest: 0.5,
      roughness: 0.93,
      metalness: 0,
      side: FrontSide,
    });
    front.alphaToCoverage = true;
    // Cast from both faces: the default (back faces only) loses the shadow
    // whenever the card faces the light.
    front.shadowSide = DoubleSide;
    front.normalMap = grain.normalMap;
    front.normalScale.set(0.4, 0.4);
    front.roughnessMap = grain.roughnessMap;
    front.name = "stage:cutout-front";
    const back = createPaperMaterial({ color: backColor, kind: "card", repeat: rep, side: BackSide, grain: 0.7 });
    back.alphaMap = cutout.alphaMap;
    back.alphaTest = 0.5;
    back.alphaToCoverage = true;
    const edge = createCardEdgeMaterial({ color: backColor, darken: 0.12 });
    const tab = createPaperMaterial({ color: backColor, kind: "card", grain: 0.6 });
    tab.vertexColors = true;
    return { front, back, edge, tab };
  }, [cutout, w, height, backColor]);
  useEffect(() => () => Object.values(materials).forEach((m) => m.dispose()), [materials]);

  // Folded flat, a piece lies on the ones in front of it: stack them so they
  // never intersect, and let each sink to the page as it stands up.
  const stackY = thickness / 2 + 0.0012 + (count - 1 - order) * (thickness + 0.0018);

  const lastRise = useRef(NaN);
  useFrame((state) => {
    const r = rise(order, count, now(state), motion);
    const g = hinge.current;
    if (!g) return;
    if (Math.abs(r - lastRise.current) > 1e-5 || Number.isNaN(lastRise.current)) markShadowsDirty(state);
    lastRise.current = r;
    g.rotation.x = Math.min(1, 1 - r) * (Math.PI / 2);
    g.position.y = stackY * (1 - smoothstep(r));
    if (vfold > 0) {
      const v = vfold * smoothstep(clamp((r - 0.45) / 0.55, 0, 1.2));
      for (let i = 0; i < halves.length; i++) {
        const h = halvesRef.current[i];
        if (h) h.rotation.y = halves[i].sign * v;
      }
    }
  });

  // Glue tabs: card strips stuck to the page behind each foot of the piece,
  // the visible half of the hinge. V-folds get one per panel, set at the
  // panel's open angle.
  const tabs = useMemo(() => {
    const out: { key: string; x: number; w: number; yaw: number; pivot: number }[] = [];
    for (const [a, b] of cutout.feet) {
      const parts: [number, number, number][] =
        vfold > 0 ? ([[a, Math.min(b, 0), -1], [Math.max(a, 0), b, 1]] as [number, number, number][]) : [[a, b, 0]];
      for (const [p, q, side] of parts) {
        const width = (q - p) * height - 0.01;
        if (width <= 0.01) continue;
        out.push({ key: `${p}:${side}`, x: ((p + q) / 2) * height, w: width, yaw: side * vfold, pivot: side });
      }
    }
    return out;
  }, [cutout, vfold, height]);

  return (
    <group position={[x, 0, z]} rotation-y={yaw}>
      {tabs.map((t) => (
        <group key={t.key} rotation-y={t.yaw}>
          <mesh
            geometry={getTabGeometry()}
            material={materials.tab}
            position={[t.x, 0.0012, -thickness * 0.5]}
            scale={[t.w, 1, TAB_DEPTH]}
            receiveShadow
          />
        </group>
      ))}
      <group ref={hinge}>
        {halves.map((h, i) => (
          // V-fold panels pivot on the crease line of the printed face, so
          // the two faces meet cleanly and the card backs tuck behind.
          <group
            key={h.key}
            position-z={thickness / 2}
            ref={(el) => {
              halvesRef.current[i] = el;
            }}
          >
            <group position-z={-thickness / 2}>
              <mesh
                geometry={h.plane}
                material={materials.front}
                position-z={thickness / 2}
                scale={[height, height, 1]}
                castShadow
                receiveShadow
              />
              <mesh
                geometry={h.plane}
                material={materials.back}
                position-z={-thickness / 2}
                scale={[height, height, 1]}
                receiveShadow
              />
              <mesh geometry={h.edge} material={materials.edge} scale={[height, height, thickness]} />
            </group>
          </group>
        ))}
      </group>
    </group>
  );
}

/* ------------------------------------------------------------------------ */
/* Page patch                                                                */
/* ------------------------------------------------------------------------ */

export interface PagePatchProps {
  cutout: Cutout;
  /** Width on the page (depth follows the cutout's aspect). */
  width: number;
  /** Centre on the page. */
  x?: number;
  z?: number;
  yaw?: number;
  /** Stacking slot for overlapping patches (higher lies on top). */
  layer?: number;
}

/**
 * A paper cutout glued flat to the page (a lawn, a path, a pond). It never
 * rises; it receives the set's shadows. The cutout's top edge points away
 * from the reader.
 */
export function PagePatch({ cutout, width, x = 0, z = 0, yaw = 0, layer = 0 }: PagePatchProps) {
  const depth = width / cutout.aspect;
  const material = useMemo(() => {
    const grain = getPaperTextures("paper", [Math.max(0.5, width), Math.max(0.5, depth)]);
    const m = new MeshStandardMaterial({
      map: cutout.map,
      alphaMap: cutout.alphaMap,
      alphaTest: 0.5,
      roughness: 0.95,
      metalness: 0,
    });
    m.alphaToCoverage = true;
    m.normalMap = grain.normalMap;
    m.normalScale.set(0.5, 0.5);
    m.polygonOffset = true;
    m.polygonOffsetFactor = -1 - layer;
    m.polygonOffsetUnits = -2 - 2 * layer;
    return m;
  }, [cutout, width, depth, layer]);
  useEffect(() => () => material.dispose(), [material]);
  return (
    <mesh position={[x, 0.0006 + layer * 0.0004, z]} rotation={[-Math.PI / 2, 0, yaw]} material={material} receiveShadow>
      <planeGeometry args={[width, depth]} />
    </mesh>
  );
}

/* ------------------------------------------------------------------------ */
/* Floating layer (parallel fold)                                            */
/* ------------------------------------------------------------------------ */

export interface FloatingLayerProps {
  /** Centre of the back wall's fold on the page. */
  x?: number;
  z?: number;
  /** Platform width (x), wall height and top depth (z). */
  width: number;
  height: number;
  depth: number;
  /** Stagger slot, as for SetPiece. Give it a slot before what stands on it. */
  order?: number;
  count?: number;
  /** Paint for the front wall (stretched over it), e.g. steps or a stone base. */
  front?: Cutout;
  /** Card colour of the walls and top. */
  color?: string;
  thickness?: number;
  /** Pieces standing on the platform, in coordinates whose origin is the
   *  top's back edge centre (+z towards the front edge). */
  children?: ReactNode;
}

/**
 * A raised platform made the way a pop-up book makes one: a back wall, a
 * front wall and a centre support strip, all hinged on the page, carrying a
 * top. Every quad is a parallelogram, so as the walls tilt the top stays
 * parallel to the page and simply sinks towards the reader; folded, the whole
 * thing lies flat. Pieces mounted on it (`children`) ride on the top and can
 * rise on their own hinges.
 */
export function FloatingLayer({
  x = 0,
  z = 0,
  width,
  height,
  depth,
  order = 0,
  count = 1,
  front,
  color = "#e4d6bd",
  thickness = 0.007,
  children,
}: FloatingLayerProps) {
  const { rise } = useContext(PopUpContext);
  const now = useStageTime();
  const walls = useRef<(Group | null)[]>([]);
  const top = useRef<Group>(null);
  const motion = Math.sqrt(Math.max(0.25, height * 2));

  const mats = useMemo(() => {
    const card = createPaperMaterial({ color, kind: "card", grain: 0.7, repeat: [width, height * 2] });
    const edge = createCardEdgeMaterial({ color, darken: 0.14 });
    let face = card;
    if (front) {
      face = new MeshStandardMaterial({ map: front.map, roughness: 0.93, metalness: 0 });
      const grain = getPaperTextures("card", [width, height * 2]);
      face.normalMap = grain.normalMap;
      face.normalScale.set(0.4, 0.4);
    }
    // Box faces: [+x, -x, +y, -y, +z, -z]. Walls stand in local XY (+z faces the reader).
    const wall = [edge, edge, edge, edge, face, card];
    const plain = [edge, edge, edge, edge, card, card];
    // The top lies in XZ: +y is its upper face.
    const plate = [edge, edge, card, card, edge, edge];
    return { card, edge, face, wall, plain, plate };
  }, [color, width, height, front]);
  useEffect(
    () => () => {
      mats.card.dispose();
      mats.edge.dispose();
      if (mats.face !== mats.card) mats.face.dispose();
    },
    [mats],
  );

  // Wall bases along the platform's depth: back, centre support, front.
  const bases = [0, depth / 2, depth];
  const lastRise = useRef(NaN);
  useFrame((state) => {
    const r = rise(order, count, now(state), motion);
    if (r === lastRise.current) return;
    lastRise.current = r;
    markShadowsDirty(state);
    // Walls tilt towards the reader, like every hinge on the stage.
    const a = Math.min(1, 1 - r) * (Math.PI / 2);
    const lift = (1 - smoothstep(r)) * thickness * 1.6;
    for (const g of walls.current) if (g) g.rotation.x = a;
    const t = top.current;
    if (t) t.position.set(0, height * Math.cos(a) + lift, height * Math.sin(a));
  });

  return (
    <group position={[x, 0, z]}>
      {bases.map((bz, i) => (
        <group
          key={bz}
          position-z={bz}
          ref={(el) => {
            walls.current[i] = el;
          }}
        >
          {/* The centre support is a narrow strip, hidden under the top. */}
          <mesh
            position={[0, height / 2, -thickness / 2]}
            material={i === 2 ? mats.wall : mats.plain}
            castShadow={i !== 1}
            receiveShadow
          >
            <boxGeometry args={[i === 1 ? width * 0.3 : width, height, thickness]} />
          </mesh>
        </group>
      ))}
      <group ref={top}>
        <mesh position={[0, thickness / 2, depth / 2]} material={mats.plate} castShadow receiveShadow>
          <boxGeometry args={[width, thickness, depth + thickness]} />
        </mesh>
        <group position-y={thickness}>{children}</group>
      </group>
    </group>
  );
}

/* ------------------------------------------------------------------------ */
/* Anchors on pieces                                                         */
/* ------------------------------------------------------------------------ */

/** Where a SetPiece stands: the same placement props it was given. */
export interface PiecePlacement {
  height: number;
  x?: number;
  z?: number;
  yaw?: number;
  order?: number;
  count?: number;
  vfold?: number;
  size?: number;
  /** Extra offset, e.g. the top of a FloatingLayer it stands on (when risen). */
  base?: Vec3;
}

/**
 * The stage position of a point on a set piece's art at stage time `t`,
 * following its hinge, and whether the piece is standing then. `u` runs
 * across the art from its centre and `v` up from the fold, both in piece
 * units (height 1). Write into `out`; returns the piece's rise.
 */
export function piecePoint(rise: PopUpValue["rise"], p: PiecePlacement, u: number, v: number, t: number, out: Vec3): number {
  const { height, x = 0, z = 0, yaw = 0, order = 0, count = 1, vfold = 0 } = p;
  const r = rise(order, count, t, p.size ?? Math.sqrt(Math.max(0.25, height)));
  // On a V-fold panel, the panel swings back about the crease.
  const pv = vfold > 0 ? Math.sign(u) * vfold : 0;
  let lx = u * height * Math.cos(pv);
  let ly = v * height;
  let lz = -Math.abs(u) * height * Math.sin(Math.abs(pv)) * (vfold > 0 ? 1 : 0);
  // Hinge: tipped towards the reader by `a` about the fold.
  const a = Math.min(1, 1 - r) * (Math.PI / 2);
  const y2 = ly * Math.cos(a) - lz * Math.sin(a);
  const z2 = ly * Math.sin(a) + lz * Math.cos(a);
  ly = y2;
  lz = z2;
  // Base yaw, then placement.
  const c = Math.cos(yaw);
  const s = Math.sin(yaw);
  const wx = lx * c + lz * s;
  const wz = -lx * s + lz * c;
  lx = wx;
  const b = p.base ?? [0, 0, 0];
  out[0] = x + lx + b[0];
  out[1] = ly + b[1];
  out[2] = z + wz + b[2];
  return r;
}

/**
 * An effect emitter (see Effects) pinned to a point on a set piece: it follows
 * the piece and only emits while the piece stands (rise >= `minRise`), so
 * smoke never rises from a flat house and stops before the set folds.
 */
export function usePieceEmitter(p: PiecePlacement, u: number, v: number, minRise = 0.96) {
  const { rise } = usePopUp();
  const { height, x, z, yaw, order, count, vfold, size, base } = p;
  return useMemo(() => {
    const place: PiecePlacement = { height, x, z, yaw, order, count, vfold, size, base };
    return (t: number, out: Vec3) => piecePoint(rise, place, u, v, t, out) >= minRise;
  }, [rise, height, x, z, yaw, order, count, vfold, size, base, u, v, minRise]);
}
