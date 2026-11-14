"use client";

// TEMPORARY style-guide showcase for the global look and the paper toolkit.
// The family-tree home replaces this page.

import { Canvas, useThree } from "@react-three/fiber";
import { useEffect } from "react";
import { Photo, Snapshot } from "@/components/paper/Photo";
import { Tape } from "@/components/paper/Tape";
import {
  useCardBoxMaterials,
  useCutoutMaterial,
  usePaperMaterial,
  useWoodMaterial,
} from "@/components/paper/usePaper";
import { demoPeople } from "@/components/tree/demoFamily";

const PAPER_TOKENS = [
  ["paper", "#f5eee1"],
  ["paper-light", "#fbf8f1"],
  ["paper-deep", "#ebe0cb"],
  ["card", "#e2d2b4"],
  ["kraft", "#c4a37a"],
  ["wood", "#7a4f31"],
] as const;

const INK_TOKENS = [
  ["ink", "#2f2620"],
  ["ink-soft", "#5c4e43"],
  ["ink-faint", "#8f8173"],
  ["accent", "#b9573d"],
  ["mustard", "#d3a03d"],
  ["sage", "#7f9873"],
  ["sky", "#5b82a6"],
  ["plum", "#8b5a7c"],
] as const;

export default function StyleGuidePage() {
  return (
    <main className="mx-auto max-w-6xl px-[var(--gutter)] pb-24 pt-16 sm:pt-24">
      <header className="max-w-3xl">
        <p className="kicker">Stitch · the look</p>
        <h1 className="mt-4">A pop-up storybook of your family</h1>
        <p className="mt-5 max-w-[var(--measure)] text-lg text-ink-soft">
          Warm paper, soft shadows, a storybook serif and a little handwriting. Everything below is
          drawn in code: the grain, the tape, the photos and the paper in the 3D scene.
        </p>
      </header>

      <Section kicker="01" title="Type">
        <div className="grid gap-10 md:grid-cols-[1.4fr_1fr]">
          <div className="space-y-5">
            <h1 className="!text-5xl">The green sedan</h1>
            <h2>How I met your mother</h2>
            <h3>Grandma&rsquo;s kitchen, 1968</h3>
            <p className="max-w-[var(--measure)] text-ink-soft">
              Dad says they met on the church steps, when Mom dropped an armful of hymn books and he
              picked up every single one. Mom says he dropped them.
            </p>
          </div>
          <div className="space-y-4 self-end">
            <p className="caption">&ldquo;we drove it all the way to the coast&rdquo;</p>
            <p className="kicker">Summer · 1968</p>
            <hr className="stitch-rule" />
            <p className="text-sm text-ink-faint">
              Fraunces for headings, Caveat for captions, Nunito Sans for interface text.
            </p>
          </div>
        </div>
      </Section>

      <Section kicker="02" title="Colour">
        <div className="grid gap-8 lg:grid-cols-2">
          <Swatches tokens={PAPER_TOKENS} />
          <Swatches tokens={INK_TOKENS} />
        </div>
        <div className="mt-10">
          <p className="kicker mb-4">Thread colours · demo family</p>
          <ul className="flex flex-wrap gap-x-6 gap-y-3">
            {demoPeople.map((p) => (
              <li key={p.id} className="flex items-center gap-2 text-sm text-ink-soft">
                <svg width="38" height="10" aria-hidden className="shrink-0">
                  <path
                    d="M1 5 Q 10 1 19 5 T 37 5"
                    fill="none"
                    stroke={p.character.color}
                    strokeWidth="2.5"
                    strokeDasharray="5 3"
                    strokeLinecap="round"
                  />
                </svg>
                {p.name}
              </li>
            ))}
          </ul>
        </div>
      </Section>

      <Section kicker="03" title="Scrapbook">
        <div className="relative rounded-[var(--radius-paper)] bg-paper-light px-6 py-14 shadow-paper sm:px-12">
          <div className="flex flex-wrap items-start justify-center gap-x-12 gap-y-14">
            <Photo alt="Hills at sunset" caption="the farm, 1961" rotate={-3} width={220}>
              <Snapshot scene="hills" />
            </Photo>
            <Photo alt="The coast" rotate={2.5} width={240} tape="corners" tapeColor="#cfb6c9" aspect={1}>
              <Snapshot scene="sea" />
            </Photo>
            <Photo alt="Maple Street house" caption="Maple St." rotate={-1.5} width={200} tapeColor="#b9cdb8">
              <Snapshot scene="house" />
            </Photo>
          </div>
          <div className="mt-12 flex flex-wrap items-center justify-center gap-6">
            <Tape rotate={-4} />
            <Tape color="#d9a6a0" pattern="stripes" rotate={3} seed={4} />
            <Tape color="#a9c0d3" pattern="dots" rotate={-2} seed={7} />
            <Tape color="#e4c477" pattern="grid" rotate={5} seed={9} />
          </div>
        </div>
      </Section>

      <Section kicker="04" title="Paper in 3D">
        <div className="relative aspect-[4/5] overflow-hidden rounded-[var(--radius-card)] bg-wood-dark shadow-float sm:aspect-[16/9]">
          <Canvas
            shadows="percentage"
            dpr={[1, 2]}
            frameloop="demand"
            camera={{ position: [0, 5.2, 5.4], fov: 30, near: 0.1, far: 60 }}
            gl={{ antialias: true }}
          >
            <SwatchScene />
          </Canvas>
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0"
            style={{ background: "radial-gradient(120% 90% at 50% 45%, transparent 55%, rgb(30 18 10 / 0.45))" }}
          />
        </div>
        <p className="caption mt-4 text-center">paper, card, and a few torn cutouts on the table</p>
      </Section>
    </main>
  );
}

function Section({ kicker, title, children }: { kicker: string; title: string; children: React.ReactNode }) {
  return (
    <section className="mt-20 sm:mt-28">
      <div className="mb-8 flex items-baseline gap-4">
        <span className="kicker">{kicker}</span>
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}

function Swatches({ tokens }: { tokens: readonly (readonly [string, string])[] }) {
  return (
    <ul className="grid grid-cols-3 gap-4 sm:grid-cols-4">
      {tokens.map(([name, hex]) => (
        <li key={name}>
          <div
            className="aspect-[4/3] rounded-[var(--radius-paper)] shadow-paper"
            style={{ background: `var(--${name})` }}
          />
          <p className="mt-2 text-sm font-semibold text-ink">{name}</p>
          <p className="text-xs text-ink-faint">{hex}</p>
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------------------ */
/* 3D swatches                                                               */
/* ------------------------------------------------------------------------ */

function Framing() {
  // Read the camera through get() inside the effect: r3f state is mutable
  // three.js objects, not React values.
  const get = useThree((s) => s.get);
  const portrait = useThree((s) => s.size.width < s.size.height);
  useEffect(() => {
    const { camera, invalidate } = get();
    if ("fov" in camera) {
      camera.fov = portrait ? 42 : 30;
      camera.updateProjectionMatrix();
    }
    camera.lookAt(0.45, 0, 0.1);
    invalidate();
  }, [get, portrait]);
  return null;
}

function SwatchScene() {
  const wood = useWoodMaterial({ repeat: [1.6, 1.6] });
  const sheet = usePaperMaterial({ color: "#f3ead8", repeat: [3, 3.6] });
  const terracotta = useCardBoxMaterials({ color: "#c46a49", repeat: [2, 1.4] });
  const kraft = useCardBoxMaterials({ color: "#cdae84", repeat: [1.8, 1.3] });
  const sage = useCutoutMaterial({ color: "#9db58a", grain: 0.6, repeat: 2, mask: { style: "torn", aspect: 1.5, seed: 3 } });
  const mustard = useCutoutMaterial({ color: "#e2b04c", grain: 0.6, repeat: 1, mask: { style: "deckled", aspect: 1, seed: 5 } });
  const sky = useCutoutMaterial({ color: "#8fb0cc", grain: 0.6, repeat: 1.2, mask: { style: "torn", aspect: 1.2, seed: 8 } });

  return (
    <>
      <Framing />
      <hemisphereLight args={["#fff1dc", "#6a4a30", 1.1]} />
      <directionalLight
        castShadow
        color="#ffd9aa"
        intensity={2.6}
        position={[-3.5, 6, 2.5]}
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.0004}
        shadow-normalBias={0.02}
        shadow-camera-left={-4}
        shadow-camera-right={4}
        shadow-camera-top={4}
        shadow-camera-bottom={-4}
      />
      {/* Soft fill from the camera side so standing cutouts aren't muddy. */}
      <directionalLight color="#ffe6c8" intensity={0.9} position={[1.5, 2.5, 6]} />

      {/* Table */}
      <mesh rotation-x={-Math.PI / 2} receiveShadow material={wood}>
        <planeGeometry args={[14, 14]} />
      </mesh>

      {/* A sheet of paper */}
      <mesh position={[-0.4, 0.006, 0.1]} rotation-y={0.08} castShadow receiveShadow material={sheet}>
        <boxGeometry args={[2.4, 0.012, 3.0]} />
      </mesh>

      {/* Card stock */}
      <mesh position={[1.75, 0.03, 0.55]} rotation-y={-0.22} castShadow receiveShadow material={terracotta}>
        <boxGeometry args={[1.5, 0.05, 1.05]} />
      </mesh>
      <mesh position={[1.9, 0.02, -0.75]} rotation-y={0.18} castShadow receiveShadow material={kraft}>
        <boxGeometry args={[1.3, 0.04, 0.95]} />
      </mesh>

      {/* Pop-up cutouts standing on the sheet */}
      <mesh position={[-0.55, 0.42, -0.55]} rotation-y={0.08} castShadow receiveShadow material={sage}>
        <planeGeometry args={[1.5 * 1.1, 1.1]} />
      </mesh>
      <mesh position={[-0.75, 0.32, 0.25]} rotation={[-0.12, 0.25, 0]} castShadow receiveShadow material={sky}>
        <planeGeometry args={[1.2 * 0.75, 0.75]} />
      </mesh>
      <mesh position={[0.15, 0.25, 0.65]} rotation={[-0.1, -0.35, 0]} castShadow receiveShadow material={mustard}>
        <planeGeometry args={[0.6, 0.6]} />
      </mesh>
    </>
  );
}
