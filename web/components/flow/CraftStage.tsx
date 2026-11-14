"use client";

// The pop-up being made: what the right page of the crafting book holds while
// the studio works (CraftingBook renders it through Book3D's renderStage).
// Every piece is paper from the stage's own kit (SetPiece cut-outs, Puppet),
// placed in stage units like a real scene (stageLayout), so when the real
// scene takes over it stands where these stood.
//
//   storyboard  frames laid along the front of the page, a pencil sketching
//   props       cut-outs that slide in and stand up, each over its name slip
//   set         backdrop layers rising from the fold, back first
//   staging     the cast's puppets stand up and walk to their marks
//   checking    a red tick on each frame the checker looked at
//   a fix       the cast and props shuffle to new marks (nothing restarts)
//   waiting     the pencil lies down and a folded "?" note stands up
//   settle      at the end every piece folds down, for the real scene
//
// Nothing re-renders per frame: entrances are scheduled in the frame loop
// from the latest scene (crafting.ts scheduleEntrances) and every piece reads
// its progress there. The book draws on demand; this keeps drawing only while
// something moves, and the pencil works in bursts with rests between.
import { useFrame, useThree } from "@react-three/fiber";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { BoxGeometry, ConeGeometry, CylinderGeometry, MeshStandardMaterial, type Group, type Mesh } from "three";

import type { PageFonts } from "@/components/book/paperArt";
import { PAGE_H, PAGE_W } from "@/components/book/leafShape";
import { STAGE_SLOT } from "@/components/book/StoryPage";
import { createPaperMaterial, getPaperTextures } from "@/components/paper/materials";
import { Puppet } from "@/components/stage/Puppet";
import { RiseProvider, SetPiece, type PopUpValue } from "@/components/stage/SetPiece";
import { stageLayout } from "@/components/stage/play";

import {
  FRAME_ASPECT,
  LAYERS,
  PROP_SHAPES,
  SLIP_ASPECT,
  disposeCraftPrints,
  frameTexture,
  layerCutout,
  propCutout,
  slipTexture,
  tickTexture,
  waitCutout,
} from "./craftPieces";
import {
  ENTRANCE,
  MAX_CHECKS,
  SETTLED,
  entranceProgress,
  scheduleEntrances,
  sceneKeys,
  type CastMember,
  type CraftScene,
} from "./crafting";

/** How long one piece takes to move in, ms. */
const ENTER_MS = 1100;
/** How long the pieces take to fold down at the end, ms. */
const SETTLE_MS = 900;
/** How long a fix takes to shuffle the pieces, ms. */
const FIX_MS = 900;
/** A resumed run's first events arrive in one replay: everything in it is simply there. */
const REPLAY_MS = 1500;

/** The page's centre line in slot space (as MomentStage centres a scene). */
const CENTRE_X = PAGE_W / 2 - STAGE_SLOT.x;

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
const smooth = (t: number) => {
  const x = clamp01(t);
  return x * x * (3 - 2 * x);
};
/** A rise with one small pop past upright, like a card coming off its fold. */
const pop = (t: number) => {
  const x = clamp01(t);
  const c = 1.15;
  return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2);
};

/**
 * The stage's live animation state, read by every piece in the frame loop and
 * brought up to date with each new scene (CraftStage's effect). One per stage.
 */
class CraftClock {
  /** Entrance start per piece key (ms, performance.now()). */
  entrances = new Map<string, number>();
  /** When each prop was folded away in the review (ms). */
  removedAt = new Map<string, number>();
  /** When the last fix began (ms), and how many there have been. */
  fixAt = -Infinity;
  fixes: number;
  /** When the pieces began to fold down for the real scene (ms), or null. */
  settleAt: number | null = null;
  /** When the run began waiting on the teller (ms), or null. */
  waitAt: number | null = null;
  reduced: boolean;
  /** When the stage first saw a scene (ms). */
  private since: number | null = null;

  constructor(fixes: number, reduced: boolean) {
    this.fixes = fixes;
    this.reduced = reduced;
  }

  /** A new scene: schedule the pieces it brings, note props folded away, a fix, a pause, the settle. */
  update(scene: CraftScene, keys: readonly string[], o: { resumed: boolean; reduced: boolean; settle: boolean }, now: number) {
    this.since ??= now;
    this.reduced = o.reduced;
    const replaying = o.resumed && now - this.since < REPLAY_MS;
    this.entrances = scheduleEntrances(this.entrances, keys, now, { ...ENTRANCE, instant: o.reduced || replaying });
    for (const p of scene.props) {
      if (p.removed && !this.removedAt.has(p.id)) this.removedAt.set(p.id, replaying ? SETTLED : now);
      if (!p.removed) this.removedAt.delete(p.id);
    }
    if (scene.fixes > this.fixes) this.fixAt = replaying ? -Infinity : now;
    this.fixes = scene.fixes;
    if (scene.waiting && this.waitAt === null) this.waitAt = replaying ? SETTLED : now;
    if (!scene.waiting) this.waitAt = null;
    if (o.settle && this.settleAt === null) this.settleAt = now;
    if (!o.settle) this.settleAt = null;
  }

  /** 0..1, how far piece `key` has come in at `now`, folded back down as the page settles. */
  presence(key: string, now: number, order = 0, count = 1): number {
    const at = this.entrances.get(key);
    const p = this.reduced ? (at === undefined ? 0 : 1) : entranceProgress(at, now, ENTER_MS);
    if (this.settleAt === null) return p;
    const s = this.reduced ? 1 : clamp01((now - this.settleAt - (count - 1 - order) * 70) / SETTLE_MS);
    return p * (1 - smooth(s));
  }

  /** 1 for a prop still in the scene, falling to 0 as one left out in the review folds away. */
  kept(id: string, now: number): number {
    const at = this.removedAt.get(id);
    if (at === undefined) return 1;
    return 1 - smooth(this.reduced || at <= SETTLED ? 1 : (now - at) / 700);
  }

  /** 0..1, how far the waiting note has stood up. */
  waiting(now: number): number {
    const at = this.waitAt;
    if (at === null) return 0;
    return this.reduced ? 1 : entranceProgress(at, now, ENTER_MS);
  }

  /** Anything still moving at `now` (the book draws on demand). */
  moving(now: number): boolean {
    for (const at of this.entrances.values()) if (at > SETTLED && now - at < ENTER_MS + 80) return true;
    for (const at of this.removedAt.values()) if (at > SETTLED && now - at < ENTER_MS) return true;
    if (now - this.fixAt < FIX_MS + 60) return true;
    if (this.waitAt !== null && this.waitAt > SETTLED && now - this.waitAt < ENTER_MS) return true;
    return this.settleAt !== null && now - this.settleAt < SETTLE_MS + 400;
  }
}

const ClockContext = createContext<CraftClock | null>(null);

function useClock(): CraftClock {
  const c = useContext(ClockContext);
  if (!c) throw new Error("CraftStage pieces need the stage's clock");
  return c;
}

export interface CraftStageProps {
  scene: CraftScene;
  cast: readonly CastMember[];
  fonts: PageFonts;
  /** The slot, world units. */
  width: number;
  depth: number;
  /** The book's reveal (StageSlotArgs.reveal): nothing stands on a page still turning. */
  reveal: { readonly current: number };
  /** A run reattached after a reload: what the replay brings is simply there. */
  resumed: boolean;
  reduced: boolean;
  /** Fold every piece down (the real scene is about to take the page). */
  settle: boolean;
}

export function CraftStage({ scene, cast, fonts, width, depth, reveal, resumed, reduced, settle }: CraftStageProps) {
  const invalidate = useThree((s) => s.invalidate);
  const layout = useMemo(() => {
    const stage: [number, number] = [Math.min(PAGE_W * 0.92, width * 1.2), depth];
    return stageLayout(stage);
  }, [width, depth]);

  const keys = useMemo(() => sceneKeys(scene, cast.length), [scene, cast.length]);
  const keySig = keys.join("|");

  const [clock] = useState(() => new CraftClock(scene.fixes, reduced));
  // The prints are this stage's alone (the cut-outs are the stage kit's shared cache).
  useEffect(() => () => disposeCraftPrints(), []);

  // A new scene: bring the clock up to date (in an effect, never while rendering).
  useEffect(() => {
    clock.update(scene, keySig ? keySig.split("|") : [], { resumed, reduced, settle }, performance.now());
    invalidate();
  }, [clock, keySig, scene, resumed, reduced, settle, invalidate]);

  // Keep drawing while anything moves; the pencil works in bursts.
  const working = !scene.waiting && !["done", "stopped", "ready", "opening"].includes(scene.stage) && !settle;
  useFrame((state) => {
    const moving = clock.moving(performance.now()) || reveal.current < 1;
    if (moving || (working && !clock.reduced)) state.invalidate();
  });

  const props = scene.props.slice(0, 5);
  const frames = Math.max(scene.frames, scene.checked > 0 && scene.frames === 0 ? Math.min(scene.checked, MAX_CHECKS) : 0);

  return (
    <ClockContext.Provider value={clock}>
      <group position-x={CENTRE_X}>
        <group position={[0, 0, layout.origin[2]]} scale={layout.scale}>
          {Array.from({ length: scene.layers }, (_, i) => (
            <Layer key={`layer:${i}`} index={i} count={LAYERS.length} reveal={reveal} />
          ))}
          {props.map((p, i) => (
            <PropPiece
              key={p.id}
              id={p.id}
              name={p.name}
              size={p.size}
              color={p.color}
              index={i}
              count={props.length}
              fonts={fonts}
              reveal={reveal}
            />
          ))}
          {scene.cast &&
            cast.map((m, i) => <CastPiece key={m.id} member={m} index={i} count={cast.length} reveal={reveal} />)}
          {Array.from({ length: frames }, (_, i) => (
            <FramePiece key={`frame:${i}`} index={i} count={frames} fonts={fonts} ticked={i < scene.checked} />
          ))}
          {scene.waiting ? <WaitNote reveal={reveal} /> : null}
          {scene.linked ? <Ribbon /> : null}
        </group>
        <Pencil scene={scene} layout={layout} frames={frames} settle={settle} />
      </group>
    </ClockContext.Provider>
  );
}

/** A rise source for one SetPiece, read from the clock each frame. */
function useRise(key: string, reveal: { readonly current: number }, order: number, count: number, extra?: (now: number) => number): PopUpValue {
  const clock = useClock();
  return useMemo<PopUpValue>(
    () => ({
      rise() {
        const now = performance.now();
        const p = clock.presence(key, now, order, count);
        const r = clock.reduced || p >= 1 ? p : pop(p);
        return Math.min(r, reveal.current * 1.05) * (extra ? extra(now) : 1);
      },
    }),
    [clock, key, reveal, order, count, extra],
  );
}

function Layer({ index, count, reveal }: { index: number; count: number; reveal: { readonly current: number } }) {
  const cutout = useMemo(() => layerCutout(index), [index]);
  const rise = useRise(`layer:${index}`, reveal, index, count);
  const spec = LAYERS[index];
  return (
    <RiseProvider value={rise}>
      <SetPiece cutout={cutout} height={spec.height} z={spec.z} order={index} count={count} />
    </RiseProvider>
  );
}

/** Where prop `i` of `n` stands (stage units). */
function propMark(i: number, n: number): number {
  const step = Math.min(0.7, 2.0 / Math.max(1, n));
  return (i - (n - 1) / 2) * step;
}

const PROP_Z = 0.86;

function PropPiece(props: {
  id: string;
  name: string;
  size: CraftScene["props"][number]["size"];
  color: string;
  index: number;
  count: number;
  fonts: PageFonts;
  reveal: { readonly current: number };
}) {
  const { id, name, size, color, index, count, fonts, reveal } = props;
  const clock = useClock();
  const cutout = useMemo(() => propCutout(size, color), [size, color]);
  const shape = PROP_SHAPES[size] ?? PROP_SHAPES.hand;
  const key = `prop:${id}`;
  // Folded away in the review: back down to the page.
  const fold = useCallback((now: number) => clock.kept(id, now), [clock, id]);
  const rise = useRise(key, reveal, index, count, fold);
  const slip = useMemo(() => slipTexture(name, fonts), [name, fonts]);
  const slipMat = useMemo(() => {
    const grain = getPaperTextures("paper", [1, 0.4]);
    const m = new MeshStandardMaterial({ map: slip, roughness: 0.95, transparent: true, alphaTest: 0.02 });
    m.normalMap = grain.normalMap;
    m.normalScale.set(0.4, 0.4);
    m.polygonOffset = true;
    m.polygonOffsetFactor = -2;
    m.polygonOffsetUnits = -4;
    return m;
  }, [slip]);
  useEffect(() => () => slipMat.dispose(), [slipMat]);

  const group = useRef<Group>(null);
  const slipMesh = useRef<Mesh>(null);
  const x = propMark(index, count);
  useFrame(() => {
    const now = performance.now();
    const c = clock;
    const p = c.presence(key, now, index, count);
    const g = group.current;
    if (g) {
      // Slides in from the fore-edge while still flat, then stands.
      const slide = 1 - smooth(p / 0.45);
      g.position.x = x + slide * 1.4;
      // A fix: everything shuffles a little to a new mark.
      const f = c.reduced ? 1 : clamp01((now - c.fixAt) / FIX_MS);
      const nudge = Math.sin(Math.PI * f) * 0.05 * (index % 2 ? 1 : -1);
      g.position.x += (c.fixes % 2 ? 0.04 : 0) * (index % 2 ? -1 : 1) + nudge;
      g.rotation.y = nudge * 2;
    }
    const s = slipMesh.current;
    if (s) {
      // The name slip comes with its cut-out and goes with it when it is left out.
      const fade = clamp01((p - 0.35) / 0.4) * c.kept(id, now);
      (s.material as MeshStandardMaterial).opacity = fade;
      s.visible = fade > 0.01;
    }
  });
  const slipW = Math.min(0.62, Math.max(0.44, shape.height * shape.aspect * 1.1));
  return (
    <group ref={group} position={[x, 0, PROP_Z]}>
      <RiseProvider value={rise}>
        <SetPiece cutout={cutout} height={shape.height} order={index} count={count} />
      </RiseProvider>
      <mesh ref={slipMesh} material={slipMat} rotation-x={-Math.PI / 2} position={[0.02, 0.003, 0.2]} receiveShadow>
        <planeGeometry args={[slipW, slipW / SLIP_ASPECT]} />
      </mesh>
    </group>
  );
}

/** Where cast member `i` of `n` takes their mark (stage units). */
function castMark(i: number, n: number, fixes: number): number {
  const step = Math.min(0.42, 1.3 / Math.max(1, n));
  const base = (i - (n - 1) / 2) * step;
  // Each fix moves everyone a little: the director re-blocks, nothing restarts.
  const shift = fixes > 0 ? (fixes % 2 ? 0.07 : -0.05) * (i % 2 ? -1 : 1) : 0;
  return base + shift;
}

const CAST_Z = 0.52;

function CastPiece({ member, index, count, reveal }: { member: CastMember; index: number; count: number; reveal: { readonly current: number } }) {
  const clock = useClock();
  const key = `cast:${index}`;
  const group = useRef<Group>(null);
  const lean = useRef<Group>(null);
  const character = useMemo(
    () => ({ color: member.color, item: member.item ?? null, build: member.build ?? undefined }),
    [member.color, member.item, member.build],
  );
  const from = useRef(castMark(index, count, 0));
  const to = useRef(castMark(index, count, 0));
  useFrame(() => {
    const now = performance.now();
    const c = clock;
    const p = c.presence(key, now, index, count) * clamp01(reveal.current * 1.05);
    const g = group.current;
    const l = lean.current;
    if (!g || !l) return;
    const target = castMark(index, count, c.fixes);
    if (target !== to.current) {
      from.current = g.position.x;
      to.current = target;
    }
    const f = c.reduced ? 1 : clamp01((now - c.fixAt) / FIX_MS);
    // Walks on from the wing on its side, then stands at its mark.
    const side = index < (count - 1) / 2 ? -1 : 1;
    const walk = 1 - smooth(p / 0.55);
    const mark = from.current + (to.current - from.current) * smooth(f);
    g.position.x = mark + side * walk * 0.9;
    g.position.y = c.reduced ? 0 : Math.abs(Math.sin(Math.PI * f * 2)) * 0.03 * (f < 1 ? 1 : 0);
    // Stands up off the page, hinged at the feet.
    const up = c.reduced || p >= 1 ? p : pop(clamp01((p - 0.2) / 0.8));
    l.rotation.x = -(Math.PI / 2) * (1 - Math.min(1, up)) + (up > 1 ? (up - 1) * 0.5 : 0);
    g.visible = p > 0.001;
  });
  const facing = index < (count - 1) / 2 ? 1 : index > (count - 1) / 2 ? -1 : 1;
  return (
    <group ref={group} position={[castMark(index, count, 0), 0, CAST_Z]}>
      <group ref={lean}>
        <Puppet character={character} personId={member.id} position={[0, 0, 0]} facing={facing} />
      </group>
    </group>
  );
}

const FRAME_W = 0.6;
const FRAME_Z = 1.5;

/** Where storyboard frame `i` of `n` lies (stage units). */
function frameMark(i: number, n: number): [number, number, number] {
  const step = Math.min(0.66, 2.1 / Math.max(1, n));
  return [(i - (n - 1) / 2) * step, FRAME_Z + (i % 2 ? 0.03 : -0.02), (i % 2 ? 1 : -1) * 0.05];
}

function FramePiece({ index, count, fonts, ticked }: { index: number; count: number; fonts: PageFonts; ticked: boolean }) {
  const clock = useClock();
  const key = `frame:${index}`;
  const tickKey = `check:${index}`;
  const map = useMemo(() => frameTexture(index, fonts), [index, fonts]);
  const mats = useMemo(() => {
    const grain = getPaperTextures("paper", [0.6, 0.4]);
    const card = new MeshStandardMaterial({ map, roughness: 0.95 });
    card.normalMap = grain.normalMap;
    card.normalScale.set(0.4, 0.4);
    const tick = new MeshStandardMaterial({ map: tickTexture(), transparent: true, roughness: 0.9, depthWrite: false });
    tick.polygonOffset = true;
    tick.polygonOffsetFactor = -3;
    tick.polygonOffsetUnits = -6;
    return { card, tick };
  }, [map]);
  useEffect(() => () => Object.values(mats).forEach((m) => m.dispose()), [mats]);
  const group = useRef<Group>(null);
  const tick = useRef<Mesh>(null);
  const [x, z, yaw] = frameMark(index, count);
  useFrame(() => {
    const now = performance.now();
    const c = clock;
    const p = c.presence(key, now);
    const g = group.current;
    if (g) {
      // Dropped onto the page from a little above, turning as it falls.
      const fall = 1 - smooth(p);
      g.position.set(x + fall * 0.25, 0.004 + fall * 0.35, z - fall * 0.2);
      g.rotation.set(-Math.PI / 2 + fall * 0.5, 0, yaw + fall * 0.6);
      g.visible = p > 0.001;
    }
    const t = tick.current;
    if (t) {
      const q = c.presence(tickKey, now);
      const s = Math.max(0.001, c.reduced ? q : pop(q));
      t.scale.setScalar(s);
      t.visible = ticked && q > 0.001;
    }
  });
  return (
    <group ref={group} position={[x, 0.004, z]} rotation={[-Math.PI / 2, 0, yaw]}>
      <mesh material={mats.card} receiveShadow castShadow>
        <boxGeometry args={[FRAME_W, FRAME_W / FRAME_ASPECT, 0.006]} />
      </mesh>
      <mesh ref={tick} material={mats.tick} position={[FRAME_W * 0.36, FRAME_W * 0.18, 0.0045]} visible={false}>
        <planeGeometry args={[0.18, 0.18]} />
      </mesh>
    </group>
  );
}

/**
 * The linker puts the page in everyone's books: a cloth bookmark ribbon is
 * drawn down the page from its head, beside the set.
 */
function Ribbon() {
  const clock = useClock();
  const parts = useMemo(() => {
    const geo = new BoxGeometry(0.09, 0.004, RIBBON_LEN);
    geo.translate(0, 0.002, RIBBON_LEN / 2);
    const mat = createPaperMaterial({ color: "#9c3b2c", roughness: 0.75, grain: 0.5 });
    return { geo, mat };
  }, []);
  useEffect(
    () => () => {
      parts.geo.dispose();
      parts.mat.dispose();
    },
    [parts],
  );
  const mesh = useRef<Mesh>(null);
  useFrame(() => {
    const m = mesh.current;
    if (!m) return;
    const p = clock.presence("ribbon", performance.now());
    m.scale.z = Math.max(0.001, smooth(p));
    m.visible = p > 0.001;
  });
  return <mesh ref={mesh} geometry={parts.geo} material={parts.mat} position={[1.17, 0, -1.3]} rotation-y={0.04} castShadow receiveShadow />;
}

const RIBBON_LEN = 1.25;

/** The folded "?" note that stands while the run waits on the teller. */
function WaitNote({ reveal }: { reveal: { readonly current: number } }) {
  const clock = useClock();
  const cutout = useMemo(() => waitCutout(), []);
  const rise = useMemo<PopUpValue>(
    () => ({
      rise() {
        const p = clock.waiting(performance.now());
        return Math.min(clock.reduced || p >= 1 ? p : pop(p), reveal.current);
      },
    }),
    [clock, reveal],
  );
  return (
    // Off to the side, clear of the props and of the pencil laid down by the fore-edge.
    <group position={[-0.95, 0, 1.12]} rotation-y={0.3}>
      <RiseProvider value={rise}>
        <SetPiece cutout={cutout} height={0.42} />
      </RiseProvider>
    </group>
  );
}

// ---------------------------------------------------------------------------
// The pencil: where the work is, sketching in bursts; lying down while the
// run waits, when it stops, and once the page is made.

const PENCIL_LEN = 0.62;
const PENCIL_R = 0.022;

function usePencilParts() {
  const parts = useMemo(() => {
    const body = new CylinderGeometry(PENCIL_R, PENCIL_R, PENCIL_LEN * 0.8, 6);
    body.translate(0, PENCIL_LEN * 0.2 + PENCIL_LEN * 0.4, 0);
    const wood = new ConeGeometry(PENCIL_R, PENCIL_LEN * 0.14, 6);
    wood.rotateX(Math.PI);
    wood.translate(0, PENCIL_LEN * 0.13, 0);
    const lead = new ConeGeometry(PENCIL_R * 0.36, PENCIL_LEN * 0.05, 6);
    lead.rotateX(Math.PI);
    lead.translate(0, PENCIL_LEN * 0.03, 0);
    const ferrule = new CylinderGeometry(PENCIL_R * 1.04, PENCIL_R * 1.04, PENCIL_LEN * 0.06, 10);
    ferrule.translate(0, PENCIL_LEN * 1.03, 0);
    const eraser = new CylinderGeometry(PENCIL_R, PENCIL_R, PENCIL_LEN * 0.07, 10);
    eraser.translate(0, PENCIL_LEN * 1.095, 0);
    const mats = {
      paint: new MeshStandardMaterial({ color: "#d9a441", roughness: 0.55 }),
      wood: new MeshStandardMaterial({ color: "#e8c9a0", roughness: 0.9 }),
      lead: new MeshStandardMaterial({ color: "#3a332d", roughness: 0.5 }),
      metal: new MeshStandardMaterial({ color: "#b8b2a6", roughness: 0.35, metalness: 0.6 }),
      eraser: new MeshStandardMaterial({ color: "#d98d6b", roughness: 0.85 }),
    };
    return { geos: { body, wood, lead, ferrule, eraser }, mats };
  }, []);
  useEffect(
    () => () => {
      Object.values(parts.geos).forEach((g) => g.dispose());
      Object.values(parts.mats).forEach((m) => m.dispose());
    },
    [parts],
  );
  return parts;
}

/** Where the pencil works for a scene (stage units, before the layout's scale), or null to rest. */
function pencilTarget(scene: CraftScene, frames: number): [number, number] | null {
  switch (scene.stage) {
    case "storyboard":
      return frames > 0 ? [frameMark(frames - 1, frames)[0], FRAME_Z] : [0, FRAME_Z];
    case "props":
      return [0.5, PROP_Z + 0.25];
    case "set":
      return [-0.6, 0.3];
    case "staging":
      return [0.2, CAST_Z + 0.3];
    case "narration":
      return null;
    case "checking":
      return frames > 0 ? [frameMark(Math.min(frames - 1, scene.checked), frames)[0], FRAME_Z] : [0, FRAME_Z];
    case "reading":
    case "linking":
    case "finishing":
      return [0.6, FRAME_Z - 0.2];
    default:
      return null;
  }
}

function Pencil({ scene, layout, frames, settle }: { scene: CraftScene; layout: { scale: number; origin: readonly number[] }; frames: number; settle: boolean }) {
  const clock = useClock();
  const { geos, mats } = usePencilParts();
  const group = useRef<Group>(null);
  const k = layout.scale;
  // Reading the story and inking it happen on the story page: the pencil
  // goes over to its lines (page px ~330 down, left of the page's middle).
  const leftPage = useMemo<[number, number]>(() => {
    const x = -PAGE_W / 2 - STAGE_SLOT.x - CENTRE_X + 0.05;
    const z = 0.33 - PAGE_H / 2 - STAGE_SLOT.z - layout.origin[2];
    return [x / k, z / k];
  }, [k, layout]);
  const target = useMemo(() => {
    if (scene.waiting || settle || ["done", "stopped", "ready"].includes(scene.stage)) return null;
    if (scene.stage === "narration" || scene.stage === "reading" || scene.stage === "opening") return leftPage;
    return pencilTarget(scene, frames);
  }, [scene, frames, settle, leftPage]);
  const targetRef = useRef(target);
  useEffect(() => {
    targetRef.current = target;
  }, [target]);
  const pos = useRef({ x: 1.05, z: 1.15, lift: 0, tilt: 0 });
  const last = useRef(0);

  useFrame((state) => {
    const g = group.current;
    if (!g) return;
    const now = performance.now();
    const dt = last.current ? Math.min(0.05, (now - last.current) / 1000) : 1;
    last.current = now;
    const c = clock;
    const t = targetRef.current;
    const p = pos.current;
    // At rest it lies on the page by the fore-edge; at work it stands over the work.
    const want = t ?? [1.05, 1.15];
    const up = t ? 1 : 0;
    const ease = c.reduced ? 1 : 1 - Math.exp(-dt * 3.2);
    p.x += (want[0] - p.x) * ease;
    p.z += (want[1] - p.z) * ease;
    p.tilt += (up - p.tilt) * ease;
    // Still on its way: one more frame (the book draws on demand).
    if (Math.abs(want[0] - p.x) + Math.abs(want[1] - p.z) + Math.abs(up - p.tilt) > 1e-3) state.invalidate();
    // Bursts of sketching with rests between, so the page is never busy.
    let dx = 0;
    let dz = 0;
    let lift = 0.06;
    if (t && !c.reduced) {
      const s = now / 1000;
      const cycle = s % 4.2;
      if (t === leftPage) {
        // Along the lines of the story, back and forth, a line lower each pass.
        const tri = Math.abs(((s * 0.22) % 2) - 1) * 2 - 1;
        dx = tri * 0.75 + Math.sin(s * 11) * 0.02;
        dz = (Math.floor((s * 0.22) % 6) - 2) * 0.09;
        lift = 0.006 + Math.max(0, Math.sin(s * 7)) * 0.01;
      } else if (cycle < 2.6) {
        dx = Math.sin(s * 9.1) * 0.05 + Math.sin(s * 2.3) * 0.04;
        dz = Math.sin(s * 6.7 + 1) * 0.025;
        lift = 0.005 + Math.max(0, Math.sin(s * 4.5)) * 0.018;
      } else {
        lift = 0.07;
      }
    }
    p.lift += (lift - p.lift) * (c.reduced ? 1 : 1 - Math.exp(-dt * 10));
    // At rest it lies on its side on the page; at work it stands on its tip,
    // leaning right and away from the reader, as a hand holds it.
    const w = smooth(p.tilt);
    g.position.set(p.x + dx * w, PENCIL_R * (1 - w) + p.lift * w, p.z + dz * w);
    g.rotation.set(-0.38 * w, 0.55 - 0.3 * w, (Math.PI / 2) * (1 - w) - 0.5 * w);
  });

  return (
    <group position={[0, 0, layout.origin[2]]} scale={k}>
      <group ref={group}>
        <mesh geometry={geos.body} material={mats.paint} castShadow />
        <mesh geometry={geos.wood} material={mats.wood} castShadow />
        <mesh geometry={geos.lead} material={mats.lead} />
        <mesh geometry={geos.ferrule} material={mats.metal} castShadow />
        <mesh geometry={geos.eraser} material={mats.eraser} castShadow />
      </group>
    </group>
  );
}
