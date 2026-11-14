"use client";

/**
 * The lab's demo scenes. Each one drives its props the way the timeline
 * player will: by mutating a PropPose in useFrame, never by re-rendering.
 * `time` (seconds) is frozen when the URL gives ?t=, for stills.
 */

import { useMemo, useRef, useState } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { Matrix4, Plane, Vector3, type Group } from "three";
import {
  createPropPose,
  PropActor,
  PropRegistry,
  setParent,
  setPartRotation,
  setPosition,
  setRotation,
  setState,
  type PropActorHandle,
  type SocketResolver,
} from "@/components/stage/PropActor";
import { usePaperMaterial } from "@/components/paper/usePaper";
import { rollAngle } from "@/components/stage/props/rig";
import type { Prop } from "@/lib/types";
import { resolveLabArt, WHEEL_PX, CAR_H } from "./labArt";
import { greenSedan, hymnBooks, placeholders, suitcase } from "./labProps";

export type DemoName = "ground" | "drive" | "states" | "held" | "closeup";

/** Every PropActor handle in a demo, so the page can tell when art is in. */
export type ReadyList = React.RefObject<(PropActorHandle | null)[]>;

function useHandleSlot(list: ReadyList, index: number) {
  return (h: PropActorHandle | null) => {
    list.current[index] = h;
  };
}

const clockOf = (fixed: number | null, elapsed: number) => (fixed ?? elapsed);

/* ------------------------------------------------------------------------ */
/* All props on the ground                                                   */
/* ------------------------------------------------------------------------ */

const LINEUP: { prop: Prop; at: [number, number, number] }[] = [
  { prop: placeholders[4], at: [0.62, 0, 0.14] }, // corner store, at the back
  { prop: placeholders[3], at: [-0.52, 0, 0.32] }, // blue Buick
  { prop: placeholders[2], at: [0.36, 0, 0.62] }, // kitchen table
  { prop: placeholders[1], at: [-0.2, 0, 0.7] }, // picnic basket
  { prop: hymnBooks, at: [0.03, 0, 0.78] },
  { prop: placeholders[0], at: [0.13, 0, 0.84] }, // ring
];

export function GroundDemo({ ready }: { ready: ReadyList }) {
  const poses = useMemo(() => LINEUP.map((l) => createPropPose({ parent: "ground", position: l.at })), []);
  return (
    <>
      {LINEUP.map((l, i) => (
        <GroundProp key={l.prop.id} index={i} prop={l.prop} pose={poses[i]} ready={ready} />
      ))}
    </>
  );
}

function GroundProp({ index, ready, ...rest }: { index: number; ready: ReadyList } & React.ComponentProps<typeof PropActor>) {
  const slot = useHandleSlot(ready, index);
  return <PropActor ref={slot} {...rest} />;
}

/* ------------------------------------------------------------------------ */
/* Drive: wheel parts spin as the car rolls; a basket rides on its roof      */
/* ------------------------------------------------------------------------ */

const DRIVE = { from: -1.55, to: 1.55, cruise: 0.9, accel: 0.7, pause: 0.8 };
const WHEEL_RADIUS = ((WHEEL_PX / CAR_H) * 0.9) / 2; // vehicle height 0.9

/** Distance along the drive at time t: ease out of rest, cruise, ease in. */
function driveDistance(t: number): number {
  const L = DRIVE.to - DRIVE.from;
  const { cruise: v, accel: a } = DRIVE;
  const tRamp = v / a;
  const dRamp = 0.5 * a * tRamp * tRamp;
  const tCruise = (L - 2 * dRamp) / v;
  const period = 2 * tRamp + tCruise + DRIVE.pause;
  const k = ((t % period) + period) % period;
  if (k < tRamp) return 0.5 * a * k * k;
  if (k < tRamp + tCruise) return dRamp + v * (k - tRamp);
  if (k < 2 * tRamp + tCruise) {
    const r = k - tRamp - tCruise;
    return dRamp + v * tCruise + v * r - 0.5 * a * r * r;
  }
  return L;
}

export function DriveDemo({ ready, fixedT }: { ready: ReadyList; fixedT: number | null }) {
  const registry = useMemo(() => new PropRegistry(), []);
  const car = useMemo(() => createPropPose({ parent: "ground", position: [DRIVE.from, 0, 0.5], state: "driving" }), []);
  const basket = useMemo(() => createPropPose({ parent: `prop:${greenSedan.id}.roof`, position: [0, 0, 0] }), []);
  const slotCar = useHandleSlot(ready, 0);
  const slotBasket = useHandleSlot(ready, 1);

  useFrame((s) => {
    const t = clockOf(fixedT, s.clock.elapsedTime);
    const d = driveDistance(t);
    setPosition(car, DRIVE.from + d, 0, 0.5);
    // A paper car on a pull tab bobs a little as it goes.
    setRotation(car, Math.sin(d * 9) * 0.006);
    const spin = rollAngle(d, WHEEL_RADIUS);
    setPartRotation(car, "wheel_back", spin);
    setPartRotation(car, "wheel_front", spin);
    setRotation(basket, Math.sin(d * 9 + 0.8) * 0.02);
  }, -1);

  return (
    <>
      <PropActor ref={slotCar} prop={greenSedan} pose={car} registry={registry} resolveArt={resolveLabArt} />
      <PropActor ref={slotBasket} prop={placeholders[1]} pose={basket} registry={registry} />
    </>
  );
}

/* ------------------------------------------------------------------------ */
/* States: the suitcase opens and closes                                     */
/* ------------------------------------------------------------------------ */

export function StatesDemo({
  ready,
  fixedState,
  onState,
}: {
  ready: ReadyList;
  fixedState: string | null;
  onState: (s: string) => void;
}) {
  const pose = useMemo(() => createPropPose({ parent: "ground", position: [0, 0, 0.5], state: fixedState ?? "closed" }), [fixedState]);
  const shown = useRef<string | null>(null);
  const slot = useHandleSlot(ready, 0);

  useFrame((s) => {
    if (!fixedState) setState(pose, Math.floor(s.clock.elapsedTime / 1.6) % 2 ? "open" : "closed");
    if (pose.state !== shown.current) {
      shown.current = pose.state;
      onState(pose.state ?? "");
    }
  });

  return <PropActor ref={slot} prop={suitcase} pose={pose} resolveArt={resolveLabArt} />;
}

/* ------------------------------------------------------------------------ */
/* Held: a prop follows a draggable hand by its grip, and can be dropped     */
/* ------------------------------------------------------------------------ */

const G = 2.8; // gravity in stage units / s^2 (a 0.5-unit puppet is ~1.75 m)

const HAND_TARGET = "puppet:lab_hand";
const HAND_PARENT = `${HAND_TARGET}.hand_r`;

/**
 * The held demo's state, shared by the DOM buttons and the scene: a stand-in
 * puppet with one socket (the draggable hand), and the prop's pose. The
 * buttons only request a drop or pick-up; `step` applies it inside a frame,
 * where the prop's current placement is known.
 */
export class HeldHand {
  readonly hand = new Vector3(0.08, 0.36, 0.5);
  readonly pose = createPropPose({ parent: HAND_PARENT, position: [0, 0, 0] });
  private facing: 1 | -1;
  private request: "drop" | "pickUp" | null;
  private fall: { t0: number; y0: number } | null = null;
  private readonly flip = new Matrix4();
  private readonly at = new Vector3();

  constructor(dropped: boolean, facing: 1 | -1) {
    this.request = dropped ? "drop" : null;
    this.facing = facing;
  }

  drop() {
    this.request = "drop";
  }
  pickUp() {
    this.request = "pickUp";
  }
  turn() {
    this.facing = this.facing === 1 ? -1 : 1;
  }
  moveHand(x: number, y: number) {
    this.hand.set(Math.max(-1.2, Math.min(1.2, x)), Math.max(0.03, Math.min(1.0, y)), 0.5);
  }

  /** The stand-in puppet's sockets: the hand, mirrored when facing left. */
  readonly resolve: SocketResolver = (target, socket, out) => {
    if (target !== HAND_TARGET || socket !== "hand_r") return false;
    out.makeTranslation(this.hand.x, this.hand.y, this.hand.z);
    if (this.facing < 0) out.multiply(this.flip.makeScale(-1, 1, 1));
    return true;
  };

  /** Apply requests and advance a fall. `still` lands a drop at once. */
  step(t: number, still: boolean, prop: PropActorHandle | null) {
    const pose = this.pose;
    if (this.request === "drop" && prop?.ready) {
      this.request = null;
      // Let go where it is: the anchor's current point becomes its ground
      // position, and from there it falls to the floor.
      prop.update();
      const [u, v] = suitcase.anchor ?? [0.5, 1];
      prop.pointAt(u, v, this.at);
      setParent(pose, "ground", this.at.x, still ? 0 : this.at.y, 0.5);
      setRotation(pose, 0);
      this.fall = still ? null : { t0: t, y0: this.at.y };
    } else if (this.request === "pickUp") {
      this.request = null;
      this.fall = null;
      setParent(pose, HAND_PARENT);
      setRotation(pose, 0);
    }
    if (!this.fall) return;
    const dt = t - this.fall.t0;
    const tLand = Math.sqrt((2 * Math.max(0, this.fall.y0)) / G);
    if (dt < tLand) {
      setPosition(pose, pose.position[0], this.fall.y0 - 0.5 * G * dt * dt, 0.5);
      setRotation(pose, 0.05 * (dt / tLand));
      return;
    }
    // Landed: a small settling rock, then still.
    setPosition(pose, pose.position[0], 0, 0.5);
    const after = dt - tLand;
    setRotation(pose, after > 0.6 ? 0 : 0.06 * Math.exp(-after * 9) * Math.sin(after * 30));
    if (after > 0.6) this.fall = null;
  }
}

export function HeldDemo({ ready, control, fixedT }: { ready: ReadyList; control: HeldHand; fixedT: number | null }) {
  const handle = useRef<PropActorHandle | null>(null);
  const slot = useHandleSlot(ready, 0);
  const marker = useRef<Group>(null);

  useFrame((s) => {
    marker.current?.position.copy(control.hand);
    control.step(clockOf(fixedT, s.clock.elapsedTime), fixedT !== null, handle.current);
  }, -1);

  const setHandle = (h: PropActorHandle | null) => {
    handle.current = h;
    slot(h);
  };

  return (
    <>
      <PropActor
        ref={setHandle}
        prop={suitcase}
        pose={control.pose}
        resolveSocket={control.resolve}
        resolveArt={resolveLabArt}
      />
      <group ref={marker}>
        <HandMarker control={control} />
      </group>
    </>
  );
}

/** The draggable hand: a small paper disc on the acting plane. */
function HandMarker({ control }: { control: HeldHand }) {
  const paper = usePaperMaterial({ color: "#b9573d", grain: 0.5 });
  const dot = usePaperMaterial({ color: "#f4ecdb", grain: 0.3 });
  const [hover, setHover] = useState(false);
  const dragging = useRef(false);
  const plane = useMemo(() => new Plane(new Vector3(0, 0, 1), -0.5), []);
  const hit = useMemo(() => new Vector3(), []);

  const move = (e: ThreeEvent<PointerEvent>) => {
    if (!dragging.current) return;
    if (e.ray.intersectPlane(plane, hit)) control.moveHand(hit.x, hit.y);
  };

  return (
    <group scale={hover ? 1.15 : 1}>
      <mesh material={paper} castShadow>
        <circleGeometry args={[0.022, 40]} />
      </mesh>
      <mesh position={[0, 0, 0.001]} material={dot}>
        <circleGeometry args={[0.007, 24]} />
      </mesh>
      {/* A generous invisible hit area so the hand is easy to grab. */}
      <mesh
        visible={false}
        onPointerOver={() => {
          setHover(true);
          document.body.style.cursor = "grab";
        }}
        onPointerOut={() => {
          setHover(false);
          document.body.style.cursor = "";
        }}
        onPointerDown={(e) => {
          e.stopPropagation();
          (e.target as Element).setPointerCapture(e.pointerId);
          dragging.current = true;
        }}
        onPointerUp={(e) => {
          (e.target as Element).releasePointerCapture(e.pointerId);
          dragging.current = false;
        }}
        onPointerMove={move}
      >
        <circleGeometry args={[0.06, 16]} />
      </mesh>
    </group>
  );
}

/* ------------------------------------------------------------------------ */
/* Close-up of a placeholder card                                            */
/* ------------------------------------------------------------------------ */

export function CloseupDemo({ ready }: { ready: ReadyList }) {
  const books = useMemo(() => createPropPose({ parent: "ground", position: [-0.012, 0, 0.5], rotation: 0 }), []);
  const ring = useMemo(() => createPropPose({ parent: "ground", position: [0.062, 0, 0.53] }), []);
  const a = useHandleSlot(ready, 0);
  const b = useHandleSlot(ready, 1);
  return (
    <>
      <PropActor ref={a} prop={hymnBooks} pose={books} />
      <PropActor ref={b} prop={placeholders[0]} pose={ring} />
    </>
  );
}
