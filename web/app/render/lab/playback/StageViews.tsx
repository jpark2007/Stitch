"use client";

// Two flat views of stage space for checking the player by eye.
// Front: x across, y up (what the camera sees, minus perspective).
// Top: x across, z down the page toward the viewer (where the camera is).

import type { ReactNode } from "react";
import type { PlaybackFrame } from "@/components/stage/playback";
import type { Person, Prop, SetDef } from "@/lib/types";

import { DRAW_ORDER, placeProp, puppetScale, solvePuppet } from "./geometry";

/** The stage page's aspect for the camera framing; the real canvas may differ. */
export const FRAMING_ASPECT = 16 / 10;

export interface SceneDefs {
  people: Map<string, Person>;
  props: Map<string, Prop>;
  sets: Map<string, SetDef>;
}

const INK = "var(--ink)";
const INK_SOFT = "var(--ink-soft)";
const INK_FAINT = "var(--ink-faint)";
const ACCENT = "var(--accent)";
const LAYER_NAMES = ["back", "middle", "front"];

type Tone = "soft" | "faint" | "accent";
const TONES: Record<Tone, string> = { soft: INK_SOFT, faint: INK_FAINT, accent: ACCENT };

/** SVG text sized in stage units. */
function Label({
  x,
  y,
  size,
  tone = "soft",
  anchor = "start",
  mono = false,
  children,
}: {
  x: number;
  y: number;
  size: number;
  tone?: Tone;
  anchor?: "start" | "middle" | "end";
  mono?: boolean;
  children: ReactNode;
}) {
  return (
    <text
      x={x}
      y={y}
      fontSize={size}
      fill={TONES[tone]}
      textAnchor={anchor}
      fontFamily={mono ? "ui-monospace, SFMono-Regular, Menlo, monospace" : "var(--font-sans)"}
      fontWeight={mono ? 500 : 650}
      letterSpacing={mono ? 0 : size * 0.06}
      style={{ fontVariantNumeric: "tabular-nums" }}
    >
      {children}
    </text>
  );
}

function scaleFor(defs: SceneDefs) {
  return (id: string) => puppetScale(defs.people.get(id)?.character.build);
}

// ---------------------------------------------------------------- front

const FX0 = -1.5;
const FX1 = 1.5;
const FY0 = -0.12;
const FY1 = 1.16;
const FRONT_LABEL = 0.034;

export function FrontView({ frame, defs }: { frame: PlaybackFrame; defs: SceneDefs }) {
  const scaleOf = scaleFor(defs);
  const shotSet = frame.shot ? defs.sets.get(frame.shot.set) : undefined;

  // Everything that stands at a depth, drawn back to front.
  const items: { z: number; node: ReactNode }[] = [];

  for (const layer of frame.layers) {
    const def = defs.sets.get(layer.setId)?.layers[layer.layer];
    const h = (def?.height ?? 0.5) * layer.fold;
    const color = def?.color ?? "var(--card)";
    items.push({
      z: (def?.depth ?? layer.layer) * 0.35,
      node: (
        <g key={layer.target} opacity={layer.fold > 0.001 ? 1 : 0}>
          <rect x={-1} y={-h} width={2} height={h} fill={color} opacity={0.18 + 0.08 * layer.layer} />
          <line x1={-1} x2={1} y1={-h} y2={-h} stroke={color} strokeWidth={0.008} />
        </g>
      ),
    });
  }

  for (const p of frame.puppets) {
    const person = defs.people.get(p.id);
    const color = person?.character.color ?? INK_SOFT;
    const sc = scaleOf(p.id);
    const poses = solvePuppet(p, sc);
    const head = poses.get("head");
    const headR = 0.11 * sc;
    const hx = head ? (head.x0 + head.x1) / 2 : p.position[0];
    const hy = head ? (head.y0 + head.y1) / 2 : p.position[1] + sc;
    const limb = (name: string) => {
      const b = poses.get(name);
      if (!b) return null;
      const w = name === "torso" ? 0.13 * sc : name.startsWith("hand") ? 0.055 * sc : 0.06 * sc;
      return <line key={name} x1={b.x0} y1={-b.y0} x2={b.x1} y2={-b.y1} strokeWidth={w} />;
    };
    // Far-side limbs (the rig's _l bones) sit behind as one faded layer, so
    // their overlapping joints do not darken.
    const far = DRAW_ORDER.filter((n) => n.includes("_l"));
    const near = DRAW_ORDER.filter((n) => !n.includes("_l") && n !== "head");
    items.push({
      z: p.position[2],
      node: (
        <g key={p.target}>
          <g stroke={color} strokeLinecap="round" opacity={0.45}>
            {far.map(limb)}
          </g>
          <g stroke={color} strokeLinecap="round">
            {near.map(limb)}
          </g>
          <circle cx={hx} cy={-hy} r={headR} fill={color} />
          {/* facing: a chevron beside the head */}
          <path
            d={`M ${hx + p.facing * (headR + 0.022)} ${-hy - 0.018} l ${p.facing * 0.02} 0.018 l ${-p.facing * 0.02} 0.018`}
            fill="none"
            stroke={INK_SOFT}
            strokeWidth={0.006}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {/* root: the point on the ground under the hips */}
          <circle cx={p.position[0]} cy={-p.position[1]} r={0.014} fill="var(--paper-light)" stroke={INK} strokeWidth={0.005} />
          <Label x={hx} y={-(hy + headR) - 0.032} size={FRONT_LABEL} anchor="middle">
            {person?.name ?? p.id}
          </Label>
        </g>
      ),
    });
  }

  for (const prop of frame.props) {
    const def = defs.props.get(prop.id);
    const card = placeProp(prop, def, frame, scaleOf);
    const cx = card.x + card.w / 2;
    const cy = card.y + card.h / 2;
    const held = prop.parent.kind !== "ground";
    items.push({
      z: held ? 0.51 : prop.position[2],
      node: (
        <g key={prop.target}>
          <line
            x1={cx}
            y1={-cy}
            x2={card.parentX}
            y2={-card.parentY}
            stroke={INK_FAINT}
            strokeWidth={0.004}
            strokeDasharray="0.012 0.01"
          />
          <circle cx={card.parentX} cy={-card.parentY} r={0.008} fill={INK_FAINT} />
          <g transform={`rotate(${(-card.rotation * 180) / Math.PI} ${card.px} ${-card.py})`}>
            <rect
              x={card.x}
              y={-(card.y + card.h)}
              width={card.w}
              height={card.h}
              rx={0.006}
              fill={def?.color ?? "var(--kraft)"}
              stroke="var(--paper-light)"
              strokeWidth={0.005}
            />
          </g>
          {/* parent, under the card and away from the arm that holds it */}
          <Label x={cx} y={-card.y + 0.04} size={0.026} anchor="middle" tone="faint" mono>
            {held ? prop.parent.raw.replace(/^puppet:/, "") : "ground"}
          </Label>
        </g>
      ),
    });
  }
  items.sort((a, b) => a.z - b.z);

  // Camera framing at the look-at point, as viewfinder corners.
  const cam = frame.camera;
  const dist = Math.hypot(
    cam.lookAt[0] - cam.position[0],
    cam.lookAt[1] - cam.position[1],
    cam.lookAt[2] - cam.position[2],
  );
  const halfH = dist * Math.tan(((cam.fov / 2) * Math.PI) / 180);
  const halfW = halfH * FRAMING_ASPECT;
  const L = cam.lookAt[0] - halfW;
  const R = cam.lookAt[0] + halfW;
  const T = -(cam.lookAt[1] + halfH);
  const B = -(cam.lookAt[1] - halfH);
  const c = Math.min(halfW, halfH) * 0.16;

  const marks = Object.entries(shotSet?.marks ?? {});

  return (
    <svg
      viewBox={`${FX0} ${-FY1} ${FX1 - FX0} ${FY1 - FY0}`}
      className="block h-full w-full"
      role="img"
      aria-label="Front view of the stage"
    >
      <defs>
        <pattern id="lab-offstage" width="0.035" height="0.035" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="0.035" stroke="var(--rule)" strokeWidth="0.005" />
        </pattern>
      </defs>
      <rect x={FX0} y={-FY1} width={-1 - FX0} height={FY1 - FY0} fill="url(#lab-offstage)" />
      <rect x={1} y={-FY1} width={FX1 - 1} height={FY1 - FY0} fill="url(#lab-offstage)" />
      {items.map((i) => i.node)}
      <line x1={FX0} x2={FX1} y1={0} y2={0} stroke={INK_SOFT} strokeWidth={0.005} />
      {marks.map(([name, m]) => {
        const x = 2 * m[0] - 1;
        return (
          <g key={name}>
            <line x1={x} x2={x} y1={-m[1]} y2={-m[1] + 0.022} stroke={INK_FAINT} strokeWidth={0.004} />
            {m[1] > 0 ? (
              // a raised standing surface (steps, a stage): where feet go before the set rises
              <line
                x1={x - 0.12}
                x2={x + 0.12}
                y1={-m[1]}
                y2={-m[1]}
                stroke={INK_FAINT}
                strokeWidth={0.004}
                strokeDasharray="0.014 0.01"
              />
            ) : null}
            <Label x={x} y={0.07} size={0.028} anchor="middle" tone="faint">
              {name.replace(/_/g, " ")}
            </Label>
          </g>
        );
      })}
      <Label x={-1.25} y={0.07} size={0.028} anchor="middle" tone="faint">
        offstage
      </Label>
      <Label x={1.25} y={0.07} size={0.028} anchor="middle" tone="faint">
        offstage
      </Label>
      <g stroke={ACCENT} strokeWidth={0.006} fill="none" strokeLinecap="round">
        <path d={`M ${L} ${T + c} V ${T} H ${L + c}`} />
        <path d={`M ${R - c} ${T} H ${R} V ${T + c}`} />
        <path d={`M ${R} ${B - c} V ${B} H ${R - c}`} />
        <path d={`M ${L + c} ${B} H ${L} V ${B - c}`} />
        <path d={`M ${cam.lookAt[0] - 0.015} ${-cam.lookAt[1]} h 0.03 M ${cam.lookAt[0]} ${-cam.lookAt[1] - 0.015} v 0.03`} />
      </g>
      {/* kept inside the view when the framing is wider than it (the fly-in) */}
      <Label x={Math.max(L, FX0 + 0.03) + 0.004} y={Math.max(T - 0.02, -FY1 + 0.04)} size={0.03} tone="accent">
        camera · {cam.fov.toFixed(0)}°
      </Label>
    </svg>
  );
}

// ---------------------------------------------------------------- top

const TX0 = -1.5;
const TX1 = 1.5;
const TZ0 = -0.2;
const TZ1 = 3.6;
const TOP_LABEL = 0.1;

export function TopView({ frame, defs }: { frame: PlaybackFrame; defs: SceneDefs }) {
  const cam = frame.camera;
  const dx = cam.lookAt[0] - cam.position[0];
  const dz = cam.lookAt[2] - cam.position[2];
  const heading = Math.atan2(dz, dx);
  const halfFovX = Math.atan(Math.tan(((cam.fov / 2) * Math.PI) / 180) * FRAMING_ASPECT);
  const reach = Math.hypot(dx, dz) * 1.12;
  const ax = cam.position[0] + Math.cos(heading - halfFovX) * reach;
  const az = cam.position[2] + Math.sin(heading - halfFovX) * reach;
  const bx = cam.position[0] + Math.cos(heading + halfFovX) * reach;
  const bz = cam.position[2] + Math.sin(heading + halfFovX) * reach;

  return (
    <svg
      viewBox={`${TX0} ${TZ0} ${TX1 - TX0} ${TZ1 - TZ0}`}
      className="block h-full w-full"
      role="img"
      aria-label="Top view of the stage and camera"
    >
      {/* the open page the set stands on, from the fold toward the reader */}
      <rect x={-1} y={0} width={2} height={0.95} fill="var(--paper-light)" stroke="var(--rule)" strokeWidth={0.008} />
      <line x1={-1} x2={1} y1={0} y2={0} stroke={INK_SOFT} strokeWidth={0.01} />
      <Label x={-1} y={-0.06} size={TOP_LABEL} tone="faint">
        fold
      </Label>
      <line x1={-1} x2={1} y1={0.5} y2={0.5} stroke={INK_FAINT} strokeWidth={0.008} strokeDasharray="0.035 0.03" />
      <Label x={1.05} y={0.525} size={TOP_LABEL} tone="faint">
        acting
      </Label>
      {frame.layers.map((layer) => {
        const def = defs.sets.get(layer.setId)?.layers[layer.layer];
        const z = (def?.depth ?? layer.layer) * 0.35;
        return (
          <g key={layer.target}>
            <line x1={-1} x2={1} y1={z} y2={z} stroke={def?.color ?? INK_SOFT} strokeWidth={0.04} opacity={0.14} />
            <line
              x1={-1}
              x2={-1 + 2 * layer.fold}
              y1={z}
              y2={z}
              stroke={def?.color ?? INK_SOFT}
              strokeWidth={0.04}
              opacity={layer.fold > 0.001 ? 0.9 : 0}
            />
            <Label x={1.05} y={z + 0.025} size={TOP_LABEL} tone="faint">
              {LAYER_NAMES[layer.layer] ?? `layer ${layer.layer}`}
            </Label>
          </g>
        );
      })}

      {/* camera frustum, horizontal */}
      <path
        d={`M ${cam.position[0]} ${cam.position[2]} L ${ax} ${az} L ${bx} ${bz} Z`}
        fill={ACCENT}
        fillOpacity={0.07}
        stroke={ACCENT}
        strokeOpacity={0.55}
        strokeWidth={0.01}
        strokeLinejoin="round"
      />
      <line
        x1={cam.position[0]}
        y1={cam.position[2]}
        x2={cam.lookAt[0]}
        y2={cam.lookAt[2]}
        stroke={ACCENT}
        strokeWidth={0.008}
        strokeDasharray="0.035 0.03"
      />
      <circle cx={cam.lookAt[0]} cy={cam.lookAt[2]} r={0.03} fill="none" stroke={ACCENT} strokeWidth={0.01} />
      <rect x={cam.position[0] - 0.07} y={cam.position[2] - 0.045} width={0.14} height={0.09} rx={0.018} fill={ACCENT} />
      <Label x={cam.position[0] + 0.12} y={cam.position[2] + 0.028} size={TOP_LABEL} tone="accent">
        camera
      </Label>

      {frame.props.map((prop) => {
        const def = defs.props.get(prop.id);
        let x = prop.position[0];
        let z = prop.position[2];
        if (prop.parent.kind === "puppet" && prop.parent.id) {
          const who = frame.puppetsById.get(prop.parent.id);
          if (who) {
            x = who.position[0] + who.facing * 0.07 + prop.position[0];
            z = who.position[2] + 0.07 + prop.position[2];
          }
        }
        return (
          <rect
            key={prop.target}
            x={x - 0.032}
            y={z - 0.032}
            width={0.064}
            height={0.064}
            rx={0.01}
            fill={def?.color ?? "var(--kraft)"}
          />
        );
      })}
      {frame.puppets.map((p) => {
        const color = defs.people.get(p.id)?.character.color ?? INK_SOFT;
        const x = p.position[0];
        const z = p.position[2];
        return (
          <g key={p.target}>
            <line x1={x} y1={z} x2={x + p.facing * 0.12} y2={z} stroke={color} strokeWidth={0.016} strokeLinecap="round" />
            <circle cx={x} cy={z} r={0.048} fill={color} stroke="var(--paper-light)" strokeWidth={0.012} />
          </g>
        );
      })}
    </svg>
  );
}
