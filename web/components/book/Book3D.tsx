"use client";

/**
 * The hero object: a handmade scrapbook lying on a wooden table under a warm
 * spotlight. Kraft card covers with a stitched cloth spine, a page block with
 * visible thickness that dips into the gutter, and skinned paper leaves that
 * bend as they turn (Leaf.tsx). The logic lives in controller.ts.
 *
 *   <Book3D spreads={spreads} cover={cover} renderStage={({ spread, reveal }) => <Stage … />} />
 *
 * Closed, the book sits on the table; a click opens it. Turn a page by
 * clicking or dragging its outer edge or corner, with the arrow keys, or with
 * the dots. `initial` sets the starting view, or freezes a leaf mid-turn, for
 * deterministic screenshots.
 */

import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { motion } from "framer-motion";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import {
  BoxGeometry,
  BufferAttribute,
  BufferGeometry,
  DoubleSide,
  type Group,
  type Material,
  type Mesh,
  MeshStandardMaterial,
  type PerspectiveCamera,
  Vector2,
} from "three";
import {
  createCardEdgeMaterial,
  createCardMaterial,
  createPaperMaterial,
  createWoodMaterial,
  getDeckledMask,
  getPaperTextures,
  preloadPaperTextures,
  type DeckledMaskOptions,
} from "@/components/paper/materials";
import { createBlockGeometry } from "./blockGeometry";
import { BOARD_D, BOARD_W, BookController, COVER_PIVOT_Y, type BookStart, type BookView } from "./controller";
import { Leaf, type LeafTextures } from "./Leaf";
import { BLOCK_T, COVER_T, PAGE_H, PAGE_W } from "./leafShape";
import { isMuted, setMuted, subscribeMuted } from "./sound";
import { StageSlot, buildBookArt, useBookTextures, type BookArt, type StageSlotArgs } from "./StoryPage";
import type { BookCover, BookSpread } from "./types";

const LEAF_MASK: DeckledMaskOptions = { aspect: PAGE_W / PAGE_H, style: "deckled", amount: 0.7, seed: 11, resolution: 1024 };
const KRAFT = "#b58c62";
const TABLE_BG = "#1a110b";

export interface BookInitial {
  state?: "closed" | "open";
  /** 1-based spread to open at. */
  spread?: number;
  /** Freeze the next leaf mid-turn at this progress (0..1). */
  turn?: number | null;
}

export interface Book3DProps {
  spreads: BookSpread[];
  cover: BookCover;
  initial?: BookInitial;
  /** The pop-up stage for a spread, mounted in the right page's slot. */
  renderStage?: (args: StageSlotArgs) => ReactNode;
}

export interface BookControls {
  open(): void;
  next(): void;
  prev(): void;
  goTo(spread: number): void;
}

function resolveStart(initial: BookInitial | undefined, n: number): BookStart {
  const opened = initial?.state === "open" || (initial?.state !== "closed" && initial?.spread != null);
  const spread = Math.min(Math.max(1, Math.round(initial?.spread ?? 1)), Math.max(1, n));
  const t = initial?.turn;
  const turn = opened && t != null && Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : null;
  return { opened, spread, turn };
}

/* ------------------------------------------------------------------------ */
/* Shell: loading, overlay UI                                                */
/* ------------------------------------------------------------------------ */

export function Book3D({ spreads, cover, initial, renderStage }: Book3DProps) {
  // The starting view is read once; later prop changes do not reset the book.
  const [start] = useState(() => resolveStart(initial, spreads.length));
  const [art, setArt] = useState<BookArt | null>(null);
  const [view, setView] = useState<BookView>({ opened: start.opened, spread: start.spread });
  const controls = useRef<BookControls | null>(null);
  const onControls = useCallback((api: BookControls | null) => {
    controls.current = api;
  }, []);
  const muted = useSyncExternalStore(subscribeMuted, isMuted, () => false);

  useEffect(() => {
    let live = true;
    Promise.all([
      buildBookArt(spreads, cover),
      preloadPaperTextures({ kinds: ["paper", "card"], wood: true, masks: [LEAF_MASK] }),
    ]).then(([a]) => {
      if (live) setArt(a);
    });
    return () => {
      live = false;
    };
  }, [spreads, cover]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && e.target.closest("input, textarea, select")) return;
      const c = controls.current;
      if (!c) return;
      if (e.key === "ArrowRight" || e.key === "PageDown") c.next();
      else if (e.key === "ArrowLeft" || e.key === "PageUp") c.prev();
      else if ((e.key === "Enter" || e.key === " ") && e.target === document.body) c.open();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="relative h-[100dvh] w-full touch-none overflow-hidden select-none" style={{ background: TABLE_BG }}>
      {art ? (
        <motion.div
          className="absolute inset-0"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
        >
          <Canvas
            shadows="percentage"
            dpr={[1, 1.75]}
            frameloop="demand"
            camera={{ fov: 30, near: 0.4, far: 40, position: [0, 3, 3] }}
            gl={{ antialias: true, powerPreference: "high-performance" }}
            aria-label="A handmade storybook on a wooden table"
          >
            <BookScene
              art={art}
              spreads={spreads}
              start={start}
              onControls={onControls}
              onView={setView}
              renderStage={renderStage}
            />
          </Canvas>
        </motion.div>
      ) : null}

      {/* Lens: soft focus toward the frame edges, and a warm vignette. */}
      <div aria-hidden className="pointer-events-none absolute inset-0" style={LENS_BLUR} />
      <div aria-hidden className="pointer-events-none absolute inset-0" style={VIGNETTE} />

      <header className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between px-4 pt-4 sm:px-6 sm:pt-5">
        <Link
          href="/"
          className="pointer-events-auto font-display text-lg tracking-tight text-[#f3e6cf]/80 no-underline transition-colors hover:text-[#f3e6cf]"
        >
          Stitch
        </Link>
        <MuteButton muted={muted} />
      </header>

      <NavDots spreads={spreads} view={view} onPick={(i) => controls.current?.goTo(i)} />

      <motion.p
        className="pointer-events-none absolute inset-x-0 bottom-[9vh] text-center font-hand text-2xl text-[#f3e6cf]/75"
        initial={false}
        animate={{ opacity: art && !view.opened ? 1 : 0, y: art && !view.opened ? 0 : 8 }}
        transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
      >
        tap the book to open it
      </motion.p>
    </div>
  );
}

const VIGNETTE = {
  background:
    "radial-gradient(115% 95% at 50% 46%, transparent 52%, rgb(16 9 5 / 0.38) 78%, rgb(12 7 4 / 0.72) 100%)",
} as const;

const LENS_BLUR = {
  backdropFilter: "blur(3px)",
  WebkitBackdropFilter: "blur(3px)",
  maskImage: "radial-gradient(85% 80% at 50% 48%, transparent 58%, black 92%)",
  WebkitMaskImage: "radial-gradient(85% 80% at 50% 48%, transparent 58%, black 92%)",
} as const;

function MuteButton({ muted }: { muted: boolean }) {
  return (
    <button
      type="button"
      onClick={() => setMuted(!muted)}
      aria-pressed={muted}
      aria-label={muted ? "Turn sound on" : "Mute sound"}
      title={muted ? "Sound off" : "Sound on"}
      className="pointer-events-auto grid size-11 place-items-center rounded-full bg-[#f5eee1]/90 text-[#5c4e43] shadow-[0_2px_3px_rgb(0_0_0/0.25),0_8px_20px_-6px_rgb(0_0_0/0.5)] backdrop-blur-sm transition-transform duration-200 hover:scale-105 active:scale-95"
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M4 9.5h3.2L12 5.5v13l-4.8-4H4z" fill="currentColor" fillOpacity="0.14" />
        {muted ? (
          <path d="M16 9.5l5 5M21 9.5l-5 5" />
        ) : (
          <>
            <path d="M15.5 9.2a4 4 0 0 1 0 5.6" />
            <path d="M18.2 6.8a7.4 7.4 0 0 1 0 10.4" />
          </>
        )}
      </svg>
    </button>
  );
}

function NavDots({ spreads, view, onPick }: { spreads: BookSpread[]; view: BookView; onPick: (i: number) => void }) {
  return (
    <motion.nav
      aria-label="Spreads"
      className="absolute inset-x-0 bottom-0 flex justify-center pb-[max(1rem,env(safe-area-inset-bottom))]"
      initial={false}
      animate={{ opacity: view.opened ? 1 : 0, y: view.opened ? 0 : 10 }}
      transition={{ duration: 0.6, delay: view.opened ? 0.8 : 0, ease: [0.22, 1, 0.36, 1] }}
      style={{ pointerEvents: view.opened ? "auto" : "none" }}
    >
      <ol className="flex items-start gap-1 rounded-full bg-[#140c07]/35 px-3 py-1.5 backdrop-blur-[2px]">
        {spreads.map((s, i) => {
          const active = view.spread === i + 1;
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onPick(i + 1)}
                aria-label={`${s.title}, ${s.year}`}
                aria-current={active ? "page" : undefined}
                title={s.title}
                className="group flex w-14 flex-col items-center gap-1 py-1 outline-offset-2"
              >
                <span
                  className="block rounded-full transition-all duration-300"
                  style={{
                    width: active ? 11 : 8,
                    height: active ? 11 : 8,
                    background: active ? "#d77a5a" : "rgb(243 230 207 / 0.45)",
                    boxShadow: active ? "0 0 0 3px rgb(215 122 90 / 0.25)" : "none",
                  }}
                />
                <span
                  className="font-sans text-[0.68rem] font-bold tracking-[0.14em] transition-colors duration-300"
                  style={{ color: active ? "rgb(243 230 207 / 0.92)" : "rgb(243 230 207 / 0.5)" }}
                >
                  {s.year}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </motion.nav>
  );
}

/* ------------------------------------------------------------------------ */
/* Scene                                                                     */
/* ------------------------------------------------------------------------ */

interface SceneProps {
  art: BookArt;
  spreads: BookSpread[];
  start: BookStart;
  onControls: (api: BookControls | null) => void;
  onView: (v: BookView) => void;
  renderStage?: (args: StageSlotArgs) => ReactNode;
}

function BookScene(props: SceneProps) {
  const wood = useMemo(() => createWoodMaterial({ repeat: [7, 7], color: "#f2dcc4" }), []);
  useEffect(() => () => wood.dispose(), [wood]);
  return (
    <>
      <color attach="background" args={[TABLE_BG]} />
      <fog attach="fog" args={[TABLE_BG, 4, 11]} />
      <hemisphereLight args={["#ffe7c9", "#3a2415", 0.5]} />
      <spotLight
        castShadow
        color="#ffd7a6"
        intensity={52}
        position={[-1.7, 4.3, 2.1]}
        angle={0.62}
        penumbra={0.95}
        decay={2}
        distance={0}
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.00025}
        shadow-normalBias={0.012}
        shadow-radius={5}
        shadow-camera-near={1.5}
        shadow-camera-far={9}
      />
      {/* Fill from the reader's side, and a warm rim from behind. */}
      <directionalLight color="#ffe4c4" intensity={0.38} position={[2.2, 2.6, 3.4]} />
      <directionalLight color="#ffc78f" intensity={0.55} position={[0.6, 2.2, -3.5]} />

      <mesh rotation-x={-Math.PI / 2} receiveShadow material={wood}>
        <planeGeometry args={[30, 30]} />
      </mesh>

      <BookModel {...props} />
    </>
  );
}

/* ------------------------------------------------------------------------ */
/* The book                                                                  */
/* ------------------------------------------------------------------------ */

function useBookMaterials(textures: ReturnType<typeof useBookTextures>) {
  const mats = useMemo(() => {
    const card = getPaperTextures("card", [2, 2.6]);
    const grain = getPaperTextures("paper", [2, 2.6]);
    const edge = createCardEdgeMaterial({ color: KRAFT, darken: 0.22 });
    const outside = new MeshStandardMaterial({
      map: textures.coverOutside,
      normalMap: card.normalMap,
      normalScale: new Vector2(0.9, 0.9),
      roughnessMap: card.roughnessMap,
      roughness: 0.92,
    });
    const inside = new MeshStandardMaterial({
      map: textures.endpaper,
      normalMap: grain.normalMap,
      normalScale: new Vector2(0.6, 0.6),
      roughness: 0.9,
    });
    const backOutside = createCardMaterial({ color: KRAFT });
    const blockTop = createPaperMaterial({ color: "#efe5d0", repeat: [2, 2.6], grain: 0.6 });
    const blockEdge = new MeshStandardMaterial({ map: textures.edges, roughness: 1, color: "#f4ead8" });
    const spine = new MeshStandardMaterial({ map: textures.spine, roughness: 0.95, side: DoubleSide });
    const shadow = new MeshStandardMaterial({
      color: "#000000",
      alphaMap: textures.shadow,
      transparent: true,
      opacity: 0.55,
      depthWrite: false,
    });
    const hit = new MeshStandardMaterial({ transparent: true, opacity: 0, depthWrite: false, colorWrite: false });
    return {
      front: [edge, edge, inside, outside, edge, edge] as Material[],
      back: [edge, edge, inside, backOutside, edge, edge] as Material[],
      block: [blockTop, blockEdge] as Material[],
      spine,
      shadow,
      hit,
      all: [edge, outside, inside, backOutside, blockTop, blockEdge, spine, shadow, hit],
    };
  }, [textures]);
  useEffect(() => () => mats.all.forEach((m) => m.dispose()), [mats]);
  return mats;
}

function useBookGeometry() {
  const geo = useMemo(() => {
    const board = new BoxGeometry(BOARD_W, COVER_T, BOARD_D);
    const blocks = { right: createBlockGeometry(1, 3), left: createBlockGeometry(-1, 5) };
    // The spine ribbon: two rows of points along a curve, posed each frame.
    const segs = 14;
    const spine = new BufferGeometry();
    const uv = new Float32Array((segs + 1) * 4);
    const idx: number[] = [];
    for (let i = 0; i <= segs; i++) {
      uv.set([i / segs, 0, i / segs, 1], i * 4);
      if (i < segs) idx.push(i * 2, i * 2 + 2, i * 2 + 1, i * 2 + 1, i * 2 + 2, i * 2 + 3);
    }
    spine.setAttribute("position", new BufferAttribute(new Float32Array((segs + 1) * 6), 3));
    spine.setAttribute("uv", new BufferAttribute(uv, 2));
    spine.setIndex(idx);
    return { board, blocks, spine };
  }, []);
  useEffect(
    () => () => {
      geo.board.dispose();
      geo.blocks.right.geometry.dispose();
      geo.blocks.left.geometry.dispose();
      geo.spine.dispose();
    },
    [geo],
  );
  return geo;
}

function BookModel({ art, spreads, start, onControls, onView, renderStage }: SceneProps) {
  const camera = useThree((s) => s.camera) as PerspectiveCamera;
  const gl = useThree((s) => s.gl);
  const invalidate = useThree((s) => s.invalidate);
  const textures = useBookTextures(art, gl.capabilities.getMaxAnisotropy());
  const mats = useBookMaterials(textures);
  const geo = useBookGeometry();

  const [stageSpread, setStageSpread] = useState(start.opened && start.turn == null ? start.spread : 0);
  const [ctl] = useState(() => new BookController(spreads.length, start, invalidate, onView, setStageSpread));

  const leafTextures = useMemo<LeafTextures[]>(() => {
    const grain = getPaperTextures("paper", [2.2, 2.8]);
    const mask = getDeckledMask(LEAF_MASK);
    return textures.leaves.map((l) => ({
      front: l.front,
      back: l.back,
      alphaMap: mask.alphaMap,
      normalMap: grain.normalMap,
      roughnessMap: grain.roughnessMap,
    }));
  }, [textures]);

  useEffect(() => {
    ctl.attach(camera, () => gl.domElement.getBoundingClientRect());
    const api: BookControls = {
      open: () => ctl.openBook(),
      next: () => ctl.next(),
      prev: () => ctl.prev(),
      goTo: (s) => ctl.goTo(s),
    };
    onControls(api);
    // Test hook for the self-testing loop (FPS probe, scripted turns).
    const w = window as Window & { __stitchBook?: unknown };
    w.__stitchBook = { ...api, frames: () => ctl.rt.frames };
    invalidate();
    return () => {
      onControls(null);
      delete w.__stitchBook;
    };
  }, [ctl, camera, gl, onControls, invalidate]);

  const book = useRef<Group>(null);
  const coverPivot = useRef<Group>(null);
  const leftBlock = useRef<Mesh>(null);
  const spine = useRef<Mesh>(null);
  const shadow = useRef<Mesh>(null);
  const stage = useRef<Group>(null);
  const closedHit = useRef<Mesh>(null);

  useFrame((state) => {
    const moving = ctl.frame({
      camera: state.camera as PerspectiveCamera,
      scene: state.scene,
      width: state.size.width,
      height: state.size.height,
      parts: {
        book: book.current,
        coverPivot: coverPivot.current,
        leftBlock: leftBlock.current,
        spine: spine.current,
        shadow: shadow.current,
        stage: stage.current,
        closedHit: closedHit.current,
        blocks: geo.blocks,
        spineGeometry: geo.spine,
      },
    });
    if (moving) state.invalidate();
    if (ctl.rt.frames === 4) document.documentElement.dataset.bookReady = "1";
  });

  const toBook = (e: ThreeEvent<PointerEvent>) => ({ x: e.point.x - (book.current?.position.x ?? 0), z: e.point.z });
  const setCursor = (c: string) => {
    document.body.style.cursor = c;
  };

  const onPageDown = (e: ThreeEvent<PointerEvent>) => {
    if (!ctl.pageDown(toBook(e), e.clientX, e.clientY)) return;
    e.stopPropagation();
    const id = e.pointerId;
    const move = (ev: PointerEvent) => {
      if (ev.pointerId !== id) return;
      ctl.dragMove(ev.clientX, ev.clientY);
      setCursor("grabbing");
    };
    const up = (ev: PointerEvent) => {
      if (ev.pointerId !== id) return;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      ctl.dragEnd();
      setCursor("");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  };

  const spreadIndex = stageSpread - 1;
  const spread = spreadIndex >= 0 ? spreads[spreadIndex] : undefined;

  return (
    <group ref={book}>
      {/* Contact shadow: soft darkening right under the book. */}
      <mesh ref={shadow} rotation-x={-Math.PI / 2} position={[0, 0.0015, 0]} material={mats.shadow} renderOrder={-1}>
        <planeGeometry args={[1, 1]} />
      </mesh>

      {/* Back board (right) and the front board, hinged at the spine. */}
      <mesh position={[BOARD_W / 2, COVER_T / 2, 0]} geometry={geo.board} material={mats.back} castShadow receiveShadow />
      <group ref={coverPivot} position={[0, COVER_PIVOT_Y, 0]}>
        <mesh
          position={[-BOARD_W / 2, COVER_T / 2 - COVER_PIVOT_Y, 0]}
          geometry={geo.board}
          material={mats.front}
          castShadow
          receiveShadow
        />
      </group>
      <mesh ref={spine} geometry={geo.spine} material={mats.spine} castShadow />

      <mesh geometry={geo.blocks.right.geometry} material={mats.block} castShadow receiveShadow />
      <mesh ref={leftBlock} geometry={geo.blocks.left.geometry} material={mats.block} castShadow receiveShadow />

      {leafTextures.map((t, i) => (
        <Leaf key={i} index={i} runtime={ctl.rt} textures={t} />
      ))}

      {/* Pointer targets (invisible). */}
      <mesh
        position={[0, COVER_T + BLOCK_T * 0.6, 0]}
        rotation-x={-Math.PI / 2}
        material={mats.hit}
        onPointerMove={(e) => setCursor(ctl.pageMove(toBook(e)))}
        onPointerLeave={() => {
          ctl.pageLeave();
          setCursor("");
        }}
        onPointerDown={onPageDown}
      >
        <planeGeometry args={[2 * PAGE_W, PAGE_H]} />
      </mesh>
      <mesh
        ref={closedHit}
        position={[BOARD_W / 2, (COVER_T * 2 + BLOCK_T) / 2, 0]}
        material={mats.hit}
        onPointerOver={(e) => {
          if (!ctl.hoverCover(true)) return;
          e.stopPropagation();
          setCursor("pointer");
        }}
        onPointerOut={() => {
          ctl.hoverCover(false);
          setCursor("");
        }}
        onClick={(e) => {
          if (ctl.isOpen) return;
          e.stopPropagation();
          setCursor("");
          ctl.openBook();
        }}
      >
        <boxGeometry args={[BOARD_W, COVER_T * 2 + BLOCK_T + 0.02, BOARD_D]} />
      </mesh>

      <group ref={stage}>
        {renderStage && spread ? (
          <StageSlot y={0}>{renderStage({ spread, index: spreadIndex, reveal: ctl.reveal })}</StageSlot>
        ) : null}
      </group>
    </group>
  );
}

export default Book3D;
