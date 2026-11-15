"use client";

/**
 * <TraditionStage>: a tradition's pop-up on the right page of the book
 * (Book3D renderStage). The category's paper kit (kitArt.ts) stands up out
 * of the page with the book's `reveal`, back pieces first, as a moment's set
 * does, and folds flat as the page turns; a few of the people who keep the
 * tradition stand in front of it as their paper puppets, in their colours.
 *
 *   <Book3D renderStage={({ spread, reveal, view, width, depth }) => (
 *     <TraditionStage category="food" keepers={keepers} reveal={reveal} view={view} size={[width, depth]} />
 *   )} />
 *
 * Once it stands, small things keep it alive: steam off the pot, lights on
 * the tree, the campfire's flames, notes over the piano, bunting in a
 * breeze. Each is one instanced draw, pinned to its piece's hinge
 * (piecePoint), and the book (drawn on demand) is asked for frames only
 * while the stage stands and its page is in view.
 */

import { useFrame } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import {
  BoxGeometry,
  CircleGeometry,
  Color,
  DoubleSide,
  MeshBasicMaterial,
  MeshStandardMaterial,
  Object3D,
  PlaneGeometry,
  Shape,
  ShapeGeometry,
  type BufferGeometry,
  type Group,
  type InstancedMesh,
  type Material,
} from "three";
import { PAGE_W } from "@/components/book/leafShape";
import { STAGE_SLOT } from "@/components/book/StoryPage";
import { createPaperMaterial } from "@/components/paper/materials";
import { useStageTime } from "@/components/stage/clock";
import { getCutout } from "@/components/stage/cutout";
import { PaperEffect } from "@/components/stage/Effects";
import { Puppet, type PuppetInputs, type Rotations } from "@/components/stage/Puppet";
import { PagePatch, PopUp, SetPiece, piecePoint, usePieceEmitter, usePopUp, type PiecePlacement } from "@/components/stage/SetPiece";
import type { Vec3 } from "@/components/stage/timeline";
import { flagCard, kitFor, noteCard, type Kit, type KitEffect, type KitLife } from "./kitArt";
import type { TraditionPerson } from "./traditionBook";

export interface TraditionStageProps {
  category: string;
  /** Its place among the book's traditions of this category: picks the kit's variant (kitFor). */
  variant?: number;
  /** The people who keep it: the first few stand in the scene. */
  keepers: readonly TraditionPerson[];
  /** The book's reveal and stage view (StageSlotArgs). */
  reveal: { readonly current: number };
  view: { readonly current: number };
  /** The slot, world units [width, depth]. */
  size: readonly [number, number];
  /**
   * Hide each person while they lie folded flat (before they rise, after
   * they fold): seen from above, a puppet folded toward the reader reads as
   * a figure upside down. For a kit raised and folded in front of the reader
   * (the book beside the add-a-tradition form); off by default.
   */
  hideFolded?: boolean;
}

/** How far a person has risen before they show, with hideFolded. */
const SHOW_FROM = 0.55;

/** The page's centre line in slot space (the slot sits toward the fore-edge). */
const CENTRE_X = PAGE_W / 2 - STAGE_SLOT.x;
/**
 * The kit's 2 stage units fill the stand's width (a little wider than the
 * slot, as a moment's set does: a real pop-up uses the page), and it needs
 * about 1.45 units of the page's depth, back fold to the people's feet.
 */
const STAGE_WIDTH = 2;
const STAGE_DEPTH = 1.45;
/** Where the back fold sits, in stage units behind the slot's centre line. */
const BACK_FOLD = 0.2;
/** How many of the keepers stand in the scene. */
const MAX_STANDING = 3;
/** Something pinned to a piece shows once that piece has nearly stood up. */
const MIN_RISE = 0.92;

function placementOf(kit: Kit, i: number, count: number): PiecePlacement {
  const p = kit.pieces[i];
  return { height: p.height, x: p.x, z: p.z, yaw: p.yaw ?? 0, order: i, count, vfold: p.vfold ?? 0 };
}

export function TraditionStage({ category, variant = 0, keepers, reveal, view, size, hideFolded = false }: TraditionStageProps) {
  const kit = kitFor(category, variant);
  const standing = keepers.slice(0, Math.min(MAX_STANDING, kit.stand.length));
  // Every piece and puppet rises in turn: the kit back to front, then the people.
  const count = kit.pieces.length + standing.length;
  const layout = useMemo(() => {
    const stage: [number, number] = [Math.min(PAGE_W * 0.92, size[0] * 1.2), size[1]];
    const scale = Math.min(stage[0] / STAGE_WIDTH, stage[1] / STAGE_DEPTH);
    return { scale, z: -BACK_FOLD * scale };
  }, [size]);
  const cutouts = useMemo(() => kit.pieces.map((p) => getCutout(p.spec)), [kit]);
  const patches = useMemo(() => kit.patches.map((p) => getCutout(p.spec)), [kit]);

  return (
    <group position={[CENTRE_X, 0, layout.z]} scale={layout.scale}>
      <PopUp open={reveal}>
        {kit.patches.map((p, i) => (
          <PagePatch key={p.spec.key} cutout={patches[i]} width={p.width} x={p.x} z={p.z} layer={i} />
        ))}
        {kit.pieces.map((p, i) => (
          <SetPiece
            key={p.spec.key}
            cutout={cutouts[i]}
            height={p.height}
            x={p.x}
            z={p.z}
            yaw={p.yaw}
            vfold={p.vfold}
            order={i}
            count={count}
          />
        ))}
        {kit.effects.map((e, i) => (
          <PieceEffect key={`fx${i}`} effect={e} placement={placementOf(kit, e.piece, count)} />
        ))}
        {kit.life.map((l, i) => (
          <PieceLife key={`life${i}`} life={l} placement={placementOf(kit, l.piece, count)} />
        ))}
        {standing.map((person, i) => (
          <Keeper
            key={person.id}
            person={person}
            at={kit.stand[i]}
            order={kit.pieces.length + i}
            count={count}
            phase={i * 2.1}
            hideFolded={hideFolded}
          />
        ))}
      </PopUp>
      <KeepDrawing reveal={reveal} view={view} />
    </group>
  );
}

/**
 * The book draws on demand: while the kit stands and its page is in view,
 * ask for the next frame so its small things keep moving.
 */
function KeepDrawing({ reveal, view }: { reveal: { readonly current: number }; view: { readonly current: number } }) {
  useFrame((state) => {
    if (reveal.current > 0.02 && view.current > 0.05) state.invalidate();
  });
  return null;
}

/* ------------------------------------------------------------------------ */
/* People                                                                    */
/* ------------------------------------------------------------------------ */

/**
 * One keeper as their paper puppet, hinged at the feet like every piece:
 * flat on the page toward the reader, standing once the kit has stood.
 * Facing the middle of the scene, with a slow breath of a sway.
 */
function Keeper({
  person,
  at,
  order,
  count,
  phase,
  hideFolded,
}: {
  person: TraditionPerson;
  at: readonly [number, number];
  order: number;
  count: number;
  phase: number;
  hideFolded: boolean;
}) {
  const { rise } = usePopUp();
  const now = useStageTime();
  const hinge = useRef<Group>(null);
  const facing = at[0] < 0 ? 1 : -1;
  // Read by the puppet every frame (its driver), so the sway needs no render.
  const driver = useRef<PuppetInputs>({ position: [0, 0, 0], facing, rot: {} });
  const character = useMemo(
    () => ({ color: person.color, item: person.item ?? undefined, build: person.build }),
    [person.color, person.item, person.build],
  );
  useFrame((state) => {
    const t = now(state);
    const r = rise(order, count, t, 0.8);
    const g = hinge.current;
    if (g) {
      if (hideFolded) g.visible = r >= SHOW_FROM;
      g.rotation.x = Math.min(1, 1 - r) * (Math.PI / 2);
      g.position.y = 0.004 * (1 - Math.min(1, r));
    }
    const d = driver.current;
    d.facing = facing;
    const rot: Rotations = d.rot;
    rot.head = 0.07 * Math.sin(t * 0.9 + phase);
    rot.torso = 0.025 * Math.sin(t * 0.7 + phase + 1);
    rot["arm_l.upper"] = 0.06 + 0.05 * Math.sin(t * 0.8 + phase);
    rot["arm_r.upper"] = -0.06 - 0.05 * Math.sin(t * 0.8 + phase + 0.5);
  });
  return (
    <group position={[at[0], 0, at[1]]}>
      <group ref={hinge}>
        <Puppet character={character} personId={person.id} driver={driver} />
      </group>
    </group>
  );
}

/* ------------------------------------------------------------------------ */
/* Steam and smoke                                                           */
/* ------------------------------------------------------------------------ */

function PieceEffect({ effect, placement }: { effect: KitEffect; placement: PiecePlacement }) {
  const emitter = usePieceEmitter(placement, effect.at[0], effect.at[1]);
  const group = useRef<Group>(null);
  // Vapour throws no shadow: on the kit's back wall a puff's shadow reads as soot.
  useLayoutEffect(() => {
    group.current?.traverse((o) => {
      o.castShadow = false;
    });
  });
  return (
    <group ref={group}>
      <PaperEffect kind={effect.kind} emitter={emitter} scale={effect.scale} count={effect.count} wind={effect.wind} seed={7} />
    </group>
  );
}

/* ------------------------------------------------------------------------ */
/* Small things that move                                                    */
/* ------------------------------------------------------------------------ */

/** A flame: a teardrop with its base at the origin, one unit tall. */
function flameGeometry(): BufferGeometry {
  const s = new Shape();
  s.moveTo(0, 0);
  s.bezierCurveTo(0.42, 0.02, 0.36, 0.5, 0, 1);
  s.bezierCurveTo(-0.36, 0.5, -0.42, 0.02, 0, 0);
  return new ShapeGeometry(s, 8);
}

/** A flag or a note: a unit card hanging from its top edge (flag) or standing on its foot (note). */
function cardGeometry(aspect: number, hang: boolean): BufferGeometry {
  const g = new PlaneGeometry(aspect, 1);
  g.translate(0, hang ? -0.5 : 0.5, 0);
  return g;
}

function lifeParts(kind: KitLife["kind"]): { geometry: BufferGeometry; material: Material } {
  switch (kind) {
    case "twinkle": {
      const material = new MeshBasicMaterial({ color: "#ffffff", toneMapped: false });
      return { geometry: new CircleGeometry(0.5, 14), material };
    }
    case "flicker": {
      const material = new MeshBasicMaterial({ color: "#ffffff", toneMapped: false, side: DoubleSide });
      return { geometry: flameGeometry(), material };
    }
    case "float": {
      const note = getCutout(noteCard);
      const material = new MeshStandardMaterial({
        map: note.map,
        alphaMap: note.alphaMap,
        alphaTest: 0.5,
        roughness: 0.9,
        side: DoubleSide,
      });
      return { geometry: cardGeometry(noteCard.aspect, false), material };
    }
    case "sway": {
      const flag = getCutout(flagCard);
      const material = createPaperMaterial({ color: "#ffffff", kind: "paper", side: DoubleSide, grain: 0.5 });
      material.alphaMap = flag.alphaMap;
      material.alphaTest = 0.5;
      return { geometry: cardGeometry(flagCard.aspect, true), material };
    }
    case "hop": {
      const material = createPaperMaterial({ color: "#ffffff", kind: "card", grain: 0.5 });
      return { geometry: new BoxGeometry(1, 1, 1), material };
    }
  }
}

const tmpColor = new Color();
const BLACK = new Color(0, 0, 0);
/** Scratch for the instance matrices (used synchronously within one frame). */
const dummy = new Object3D();

/**
 * Small things pinned to a piece (KitLife), one instanced mesh: each point
 * follows the piece's hinge and shows once it has stood, then moves by kind.
 */
function PieceLife({ life, placement }: { life: KitLife; placement: PiecePlacement }) {
  const { rise } = usePopUp();
  const now = useStageTime();
  const mesh = useRef<InstancedMesh>(null);
  const n = life.points.length;
  const { geometry, material } = useMemo(() => lifeParts(life.kind), [life.kind]);
  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );
  const base = useMemo(() => life.points.map((_, i) => new Color(life.colors[i % life.colors.length])), [life]);
  const at = useRef<Vec3>([0, 0, 0]);

  useLayoutEffect(() => {
    const m = mesh.current;
    if (!m) return;
    for (let i = 0; i < n; i++) m.setColorAt(i, base[i]);
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  }, [base, n]);

  useFrame((state) => {
    const m = mesh.current;
    if (!m) return;
    const t = now(state);
    const yaw = placement.yaw ?? 0;
    const fx = Math.sin(yaw);
    const fz = Math.cos(yaw);
    let recolor = false;
    for (let i = 0; i < n; i++) {
      const [u, v] = life.points[i];
      const r = piecePoint(rise, placement, u, v, t, at.current);
      const [x, y, z] = at.current;
      // Just in front of the card's printed face.
      dummy.position.set(x + fx * 0.012, y, z + fz * 0.012);
      dummy.rotation.set(0, yaw, 0);
      const shown = r >= MIN_RISE;
      const size = life.size;
      const p = i * 1.7;
      switch (life.kind) {
        case "twinkle": {
          const glow = 0.5 + 0.5 * Math.sin(t * 2.3 + p) * Math.sin(t * 0.9 + p * 0.6);
          dummy.scale.setScalar(size * (0.78 + 0.3 * glow));
          tmpColor.copy(base[i]).lerp(BLACK, 0.45 * (1 - glow));
          m.setColorAt(i, tmpColor);
          recolor = true;
          break;
        }
        case "flicker": {
          const k = 1 + 0.16 * Math.sin(t * 11 + p * 2) + 0.08 * Math.sin(t * 23 + p);
          dummy.scale.set(size * 0.62 * (1 + 0.07 * Math.sin(t * 17 + p)), size * k, 1);
          dummy.rotation.z = 0.09 * Math.sin(t * 6.5 + p);
          break;
        }
        case "float": {
          const life_ = 3.4;
          const k = (((t + (i * life_) / n) % life_) + life_) % life_ / life_;
          dummy.position.x += 0.1 * Math.sin(k * Math.PI * 1.4 + p);
          dummy.position.y += 0.5 * k;
          dummy.rotation.z = 0.25 * Math.sin(t * 1.1 + p);
          dummy.scale.setScalar(size * Math.pow(Math.sin(Math.PI * k), 0.6));
          break;
        }
        case "sway": {
          dummy.rotation.z = 0.2 * Math.sin(t * 1.5 + i * 0.75);
          dummy.rotation.x = 0.12 * Math.sin(t * 1.1 + i * 0.5);
          dummy.scale.setScalar(size);
          break;
        }
        case "hop": {
          const k = Math.abs(Math.sin(t * 2.4 + p));
          dummy.position.y += size * 0.5 + 0.1 * k;
          dummy.position.z += 0.04;
          dummy.rotation.set(t * 1.3 + p, t * 0.9 + p, 0);
          dummy.scale.setScalar(size);
          break;
        }
      }
      if (!shown) dummy.scale.setScalar(0);
      dummy.updateMatrix();
      m.setMatrixAt(i, dummy.matrix);
    }
    m.instanceMatrix.needsUpdate = true;
    if (recolor && m.instanceColor) m.instanceColor.needsUpdate = true;
  });

  return (
    <instancedMesh
      ref={mesh}
      args={[geometry, material, n]}
      frustumCulled={false}
      castShadow={life.kind === "hop" || life.kind === "sway"}
    />
  );
}
