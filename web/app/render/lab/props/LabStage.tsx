"use client";

/**
 * The lab's paper stage: an open pop-up book page (a floor sheet and the
 * facing page standing up at the fold, z = 0) on a wooden table, under warm
 * light. Stage units are CONTRACTS.md stage space, so props placed here use
 * the same numbers as on the real stage.
 */

import { useEffect } from "react";
import { useThree } from "@react-three/fiber";
import { useCardBoxMaterials, usePaperMaterial, useWoodMaterial } from "@/components/paper/usePaper";

export interface Shot {
  position: [number, number, number];
  lookAt: [number, number, number];
  fov: number;
}

export function Framing({ shot }: { shot: Shot }) {
  // r3f state is mutable three.js objects: read it through get() in effects.
  const get = useThree((s) => s.get);
  const portrait = useThree((s) => s.size.width < s.size.height);
  useEffect(() => {
    const { camera, invalidate } = get();
    camera.position.set(...shot.position);
    if ("fov" in camera) {
      camera.fov = portrait ? shot.fov * 1.6 : shot.fov;
      camera.updateProjectionMatrix();
    }
    camera.lookAt(...shot.lookAt);
    invalidate();
  }, [get, portrait, shot]);
  return null;
}

/** Faint pencil ticks along the acting plane at u = 0, 0.25 ... 1. */
function Marks() {
  const pencil = usePaperMaterial({ color: "#cbbb9f", roughness: 1, grain: 0.2 });
  return (
    <group position={[0, 0.0008, 0.5]}>
      {[-1, -0.5, 0, 0.5, 1].map((x) => (
        <mesh key={x} position={[x, 0, 0]} rotation-x={-Math.PI / 2} material={pencil}>
          <planeGeometry args={[0.0025, 0.022]} />
        </mesh>
      ))}
    </group>
  );
}

export function LabStage() {
  const wood = useWoodMaterial({ repeat: [2, 2] });
  // Fine grain at the props' scale: about 2.5 tiles per stage unit.
  const floor = useCardBoxMaterials({ color: "#f3ead8", repeat: [8, 4], grain: 0.55 });
  const page = useCardBoxMaterials({ color: "#efe5d1", repeat: [8, 5], grain: 0.5 });

  return (
    <>
      <hemisphereLight args={["#fff1dc", "#6a4a30", 1.05]} />
      <directionalLight
        castShadow
        color="#ffd9aa"
        intensity={2.4}
        position={[-2.2, 3.4, 3.2]}
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.0003}
        shadow-normalBias={0.012}
        shadow-radius={4}
        shadow-intensity={0.75}
        shadow-camera-left={-2.2}
        shadow-camera-right={2.2}
        shadow-camera-top={2.2}
        shadow-camera-bottom={-1.2}
        shadow-camera-near={0.5}
        shadow-camera-far={9}
      />
      {/* Soft fill from the camera side so standing cards are not muddy. */}
      <directionalLight color="#ffe6c8" intensity={0.8} position={[1.2, 1.6, 5]} />

      <mesh position={[0, -0.03, 0.6]} rotation-x={-Math.PI / 2} receiveShadow material={wood}>
        <planeGeometry args={[12, 8]} />
      </mesh>
      {/* The floor sheet: the lower page, from the fold toward the viewer. */}
      <mesh position={[0, -0.006, 0.78]} receiveShadow castShadow material={floor}>
        <boxGeometry args={[3.2, 0.012, 1.56]} />
      </mesh>
      {/* The facing page, standing at the fold. */}
      <mesh position={[0, 1.0, -0.006]} rotation-x={Math.PI / 2} receiveShadow castShadow material={page}>
        <boxGeometry args={[3.2, 0.012, 2.0]} />
      </mesh>
      <Marks />
    </>
  );
}
