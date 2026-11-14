"use client";

import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { Plane, Raycaster, Vector2, Vector3, type Mesh } from "three";
import type { DeckledMaskOptions } from "@/components/paper/materials";
import { useCardBoxMaterials, useCutoutMaterial, usePaperMaterial } from "@/components/paper/usePaper";
import { Puppet } from "@/components/stage/Puppet";
import { RIG, scaleForBuild } from "@/components/stage/puppet/rig";
import type { CharacterSheet } from "@/lib/types";
import { ACTING_Z, CrowdDirector, FpsCounter, LineupDirector, SLOT_FACING, SLOT_X } from "./director";

export interface LabSettings {
  pose: string;
  walk: boolean;
  reach: boolean;
  /** Frozen time in seconds, or null to run. */
  freeze: number | null;
  /** >0: that many puppets walking instead of the lineup. */
  crowd: number;
  /** Close camera on lineup puppet i. */
  focus: number | null;
}

interface CastMember {
  id: string;
  character: Pick<CharacterSheet, "color" | "item" | "build">;
}

/** The lineup (from the seeded demo family) and extra walkers for the crowd. */
const CAST: CastMember[] = [
  { id: "david", character: { color: "#3f6fb5", item: "blue flat cap", build: "tall" } },
  { id: "ana", character: { color: "#c0504d", item: "red scarf", build: "average" } },
  { id: "june", character: { color: "#d39a2c", item: "pearl reading glasses", build: "short" } },
  { id: "walter", character: { color: "#4e8a5b", item: "fishing hat with a lure", build: "average" } },
  { id: "rosa", character: { color: "#8b5a7c", item: "yellow hair bow", build: "small" } },
  { id: "sam", character: { color: "#5b82a6", item: "green wool beanie", build: "short" } },
  { id: "lena", character: { color: "#b9573d", item: "striped headband", build: "average" } },
  { id: "joe", character: { color: "#7a7a42", item: "a lucky coin", build: "tall" } },
];
const LINEUP = CAST.slice(0, 4);

export const BACKDROP_MASK = { style: "deckled", aspect: 2.6 / 1.2, seed: 12 } satisfies DeckledMaskOptions;

/* ------------------------------------------------------------------------ */

export function LabScene({ settings }: { settings: LabSettings }) {
  return (
    <>
      <CameraFrame focus={settings.crowd > 0 ? null : settings.focus} />
      <hemisphereLight args={["#fff3e0", "#7a5a3c", 0.95]} />
      <directionalLight
        castShadow
        color="#ffdcb0"
        intensity={2.3}
        position={[-1.7, 2.7, 2.5]}
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.0002}
        shadow-normalBias={0.006}
        shadow-radius={4}
        shadow-intensity={0.7}
        shadow-camera-left={-1.8}
        shadow-camera-right={1.8}
        shadow-camera-top={1.6}
        shadow-camera-bottom={-1.2}
        shadow-camera-near={0.5}
        shadow-camera-far={8}
      />
      <directionalLight color="#ffe9cf" intensity={0.6} position={[2.2, 1.2, 3]} />
      <PaperStage />
      {settings.crowd > 0 ? <Crowd n={settings.crowd} freeze={settings.freeze} /> : <Lineup settings={settings} />}
      <FpsMeter />
    </>
  );
}

/** A sheet of paper for the ground and one deckled card standing behind. */
function PaperStage() {
  const page = usePaperMaterial({ color: "#f1e7d3", repeat: [5, 3.5] });
  const back = useCutoutMaterial({ color: "#e9dfc8", grain: 0.3, repeat: [5, 2.3], mask: BACKDROP_MASK });
  return (
    <>
      <mesh rotation-x={-Math.PI / 2} position={[0, 0, 0.6]} receiveShadow material={page}>
        <planeGeometry args={[7, 3.6]} />
      </mesh>
      <mesh position={[0, 0.6, -0.02]} receiveShadow material={back}>
        <planeGeometry args={[2.6, 1.2]} />
      </mesh>
    </>
  );
}

function CameraFrame({ focus }: { focus: number | null }) {
  const get = useThree((s) => s.get);
  const portrait = useThree((s) => s.size.width < s.size.height * 1.05);
  useLayoutEffect(() => {
    const { camera, invalidate } = get();
    if (!("fov" in camera)) return;
    const i = focus === null ? -1 : Math.max(0, Math.min(3, Math.round(focus)));
    if (i >= 0) {
      const x = SLOT_X[i];
      camera.position.set(x + 0.06, 0.36, ACTING_Z + 1.55);
      camera.fov = 22;
      camera.updateProjectionMatrix();
      camera.lookAt(x, 0.28, ACTING_Z);
    } else {
      camera.position.set(0, portrait ? 0.55 : 0.5, portrait ? 4.6 : 3.05);
      camera.fov = portrait ? 34 : 27;
      camera.updateProjectionMatrix();
      camera.lookAt(0, 0.31, 0.45);
    }
    invalidate();
  }, [get, focus, portrait]);
  return null;
}

/* ------------------------------------------------------------------------ */
/* Lineup: poses, walk, reach                                                */
/* ------------------------------------------------------------------------ */

function Lineup({ settings }: { settings: LabSettings }) {
  const scales = useMemo(() => LINEUP.map((c) => scaleForBuild(c.character.build)), []);
  const director = useMemo(() => new LineupDirector(scales), [scales]);
  const latest = useRef(settings);
  useLayoutEffect(() => {
    latest.current = settings;
  });
  const ballHit = useRef<Mesh>(null);
  useYarnDrag(ballHit, director);
  useFrame((_, delta) => director.step(delta, latest.current));

  return (
    <>
      {LINEUP.map((c, i) => (
        <Puppet key={c.id} personId={c.id} character={c.character} driver={director.drivers[i]} />
      ))}
      {settings.pose === "sit" &&
        LINEUP.map((c, i) =>
          i === 0 && settings.walk ? null : (
            <Stool key={c.id} x={SLOT_X[i]} facing={SLOT_FACING[i]} scale={scales[i]} />
          ),
        )}
      <group ref={(g) => director.attachBall(g)} visible={settings.reach}>
        <YarnBall hitRef={ballHit} />
      </group>
    </>
  );
}

/** A little card stool under a sitting puppet's thighs. */
function Stool({ x, facing, scale }: { x: number; facing: number; scale: number }) {
  const mats = useCardBoxMaterials({ color: "#c4a37a", repeat: [1.2, 1] });
  const seat = (RIG.hipHeight - 0.24 - 0.034) * scale;
  const w = 0.2 * scale;
  return (
    <mesh position={[x + facing * 0.1 * scale, seat / 2, ACTING_Z - 0.075]} material={mats} castShadow receiveShadow>
      <boxGeometry args={[w, seat, 0.1]} />
    </mesh>
  );
}

/** The draggable IK target: a small ball of red yarn, cut from paper. */
function YarnBall({ hitRef }: { hitRef: React.RefObject<Mesh | null> }) {
  const mat = useCutoutMaterial({ color: "#b9573d", grain: 0.8, mask: { style: "deckled", aspect: 1, seed: 4, cornerRadius: 0.5 } });
  const wind = useCutoutMaterial({ color: "#d9826a", grain: 0.6, mask: { style: "deckled", aspect: 2.6, seed: 9, cornerRadius: 0.5 } });
  return (
    <>
      <mesh ref={hitRef} material={mat} castShadow>
        <planeGeometry args={[0.05, 0.05]} />
      </mesh>
      <mesh material={wind} position={[0.002, 0.003, 0.002]} rotation-z={0.5}>
        <planeGeometry args={[0.036, 0.0075]} />
      </mesh>
      <mesh material={wind} position={[-0.004, -0.006, 0.002]} rotation-z={0.25}>
        <planeGeometry args={[0.03, 0.006]} />
      </mesh>
    </>
  );
}

/** Pointer dragging of the yarn ball in the acting plane (DOM events, no re-renders). */
function useYarnDrag(hit: React.RefObject<Mesh | null>, director: LineupDirector) {
  const get = useThree((s) => s.get);
  useEffect(() => {
    const el = get().gl.domElement;
    const ray = new Raycaster();
    const ndc = new Vector2();
    const plane = new Plane(new Vector3(0, 0, 1), -(ACTING_Z + 0.02));
    const point = new Vector3();
    let dragging = false;
    const aim = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, get().camera);
    };
    const overBall = () => {
      const m = hit.current;
      return !!m && m.parent?.visible !== false && ray.intersectObject(m, false).length > 0;
    };
    const drag = () => {
      if (!ray.ray.intersectPlane(plane, point)) return;
      point.x = Math.max(-1.1, Math.min(1.1, point.x));
      point.y = Math.max(0.02, Math.min(1.0, point.y));
      director.hold(point);
    };
    const down = (e: PointerEvent) => {
      aim(e);
      if (!overBall()) return;
      dragging = true;
      el.setPointerCapture(e.pointerId);
      el.style.cursor = "grabbing";
      drag();
    };
    const move = (e: PointerEvent) => {
      aim(e);
      if (dragging) drag();
      else el.style.cursor = overBall() ? "grab" : "";
    };
    const up = (e: PointerEvent) => {
      if (!dragging) return;
      dragging = false;
      el.style.cursor = "grab";
      if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
    };
    el.addEventListener("pointerdown", down);
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
    return () => {
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
      el.style.cursor = "";
    };
  }, [get, hit, director]);
}

/* ------------------------------------------------------------------------ */
/* Crowd: n puppets walking, for the frame-rate check                        */
/* ------------------------------------------------------------------------ */

function Crowd({ n, freeze }: { n: number; freeze: number | null }) {
  const director = useMemo(
    () =>
      new CrowdDirector(
        n,
        (i) => scaleForBuild(CAST[i % CAST.length].character.build),
        (i) => `${CAST[i % CAST.length].id}-${i}`,
      ),
    [n],
  );
  const latestFreeze = useRef(freeze);
  useLayoutEffect(() => {
    latestFreeze.current = freeze;
  });
  useFrame((_, delta) => director.step(delta, latestFreeze.current));
  return (
    <>
      {director.walkers.map((w, i) => (
        <Puppet key={w.id} personId={w.id} character={CAST[i % CAST.length].character} driver={w.driver} />
      ))}
    </>
  );
}

/* ------------------------------------------------------------------------ */

function FpsMeter() {
  const counter = useMemo(() => new FpsCounter(), []);
  useFrame(() => counter.tick());
  return null;
}
