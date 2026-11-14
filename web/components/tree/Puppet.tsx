import type { ReactNode } from "react";
import { shade, tint } from "./sketch";

/**
 * A faceless split-pin paper puppet, drawn flat in SVG: the static avatar
 * version of the shared rig (head, torso, arms, legs joined at pins). Body in
 * the person's colour, with their signature item hinted from its description
 * ("blue flat cap", "film camera on a strap", ...). Server-safe.
 *
 * Render <PuppetDefs /> once per page; puppets reference its filters.
 */

export type Build = "short" | "average" | "tall";

export interface PuppetProps {
  /** Unique per page (used for pattern ids). */
  id: string;
  color: string;
  item?: string;
  build?: Build;
  className?: string;
  title?: string;
}

const LAYER = "tree-puppet-layer";
const GRAIN = "tree-puppet-grain";

/** Shared SVG filters: a soft drop shadow between paper layers, and grain. */
export function PuppetDefs() {
  return (
    <svg width="0" height="0" aria-hidden focusable="false" style={{ position: "absolute" }}>
      <defs>
        <filter id={LAYER} x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0.35" dy="0.9" stdDeviation="0.55" floodColor="#4a2f1d" floodOpacity="0.32" />
        </filter>
        <filter id={GRAIN} x="0" y="0" width="100%" height="100%">
          <feTurbulence type="fractalNoise" baseFrequency="1.6" numOctaves="2" seed="4" result="noise" />
          <feColorMatrix
            in="noise"
            values="0 0 0 0 0.18  0 0 0 0 0.12  0 0 0 0 0.08  0 0 0 0.5 -0.12"
            result="speck"
          />
          <feComposite in="speck" in2="SourceAlpha" operator="in" result="grain" />
          <feMerge>
            <feMergeNode in="SourceGraphic" />
            <feMergeNode in="grain" />
          </feMerge>
        </filter>
      </defs>
    </svg>
  );
}

/** A rounded capsule from p to q, `w` wide, as a closed path. */
function capsule(p: [number, number], q: [number, number], w: number): string {
  const dx = q[0] - p[0];
  const dy = q[1] - p[1];
  const len = Math.hypot(dx, dy) || 1;
  const nx = (-dy / len) * (w / 2);
  const ny = (dx / len) * (w / 2);
  const r = w / 2;
  const f = (n: number) => n.toFixed(2);
  return (
    `M${f(p[0] + nx)} ${f(p[1] + ny)} L${f(q[0] + nx)} ${f(q[1] + ny)} ` +
    `A${r} ${r} 0 0 1 ${f(q[0] - nx)} ${f(q[1] - ny)} L${f(p[0] - nx)} ${f(p[1] - ny)} ` +
    `A${r} ${r} 0 0 1 ${f(p[0] + nx)} ${f(p[1] + ny)} Z`
  );
}

/* ------------------------------------------------------------------ items */

const WORD_COLORS: [RegExp, string][] = [
  [/\bblue\b/, "#2f4f7f"],
  [/\bred\b/, "#9c3727"],
  [/\bgreen\b/, "#5f8a4c"],
  [/\byellow|sunflower|gold\b/, "#e7b631"],
  [/\bbrown|leather\b/, "#7a4f2e"],
  [/\bpearl\b/, "#f4eee2"],
  [/\bblack\b/, "#2f2620"],
  [/\bwhite\b/, "#f7f2e8"],
  [/\bpink\b/, "#e59aa8"],
  [/\bpurple\b/, "#6c4a7e"],
  [/\borange\b/, "#e07a3a"],
];

function itemColor(item: string, fallback: string): string {
  const s = item.toLowerCase();
  for (const [re, c] of WORD_COLORS) if (re.test(s)) return c;
  return fallback;
}

type ItemKind =
  | "cap"
  | "hat"
  | "shawl"
  | "glasses"
  | "satchel"
  | "flower"
  | "camera"
  | "backpack"
  | "scarf";

const KINDS: [RegExp, ItemKind][] = [
  [/backpack|rucksack/, "backpack"],
  [/camera/, "camera"],
  [/glasses|spectacles/, "glasses"],
  [/scarf/, "scarf"],
  [/shawl|cardigan|blanket/, "shawl"],
  [/satchel|bag|purse/, "satchel"],
  [/flower|clip|bow|hair/, "flower"],
  [/\bcap\b|beret/, "cap"],
  [/hat|bonnet/, "hat"],
];

export function itemKind(item?: string): ItemKind | undefined {
  if (!item) return undefined;
  const s = item.toLowerCase();
  return KINDS.find(([re]) => re.test(s))?.[1];
}

interface Parts {
  /** Drawn behind the body. */
  back?: ReactNode;
  /** Drawn over the body. */
  front?: ReactNode;
}

const EDGE = "rgb(251 248 241 / 0.55)";

function itemParts(kind: ItemKind | undefined, item: string, body: string, pid: string): Parts {
  const c = itemColor(item, shade(body, 0.35));
  const edge = { stroke: EDGE, strokeWidth: 0.7 };
  switch (kind) {
    case "cap":
      return {
        front: (
          <g filter={`url(#${LAYER})`}>
            <path d="M36.5 24 Q37 11.5 50.5 11 Q63.5 11 64 21.5 Q70.5 22.5 72 25.5 Q60 27.6 36.5 25.6 Z" fill={c} {...edge} />
            <path d="M40 20 Q50 16.5 61.5 19.5" fill="none" stroke={tint(c, 0.25)} strokeWidth="0.8" strokeDasharray="1.6 1.4" />
          </g>
        ),
      };
    case "hat":
      return {
        front: (
          <g filter={`url(#${LAYER})`}>
            <path d="M39 20 Q39.5 9.5 50 9.5 Q60.5 9.5 61 20 Z" fill={c} {...edge} />
            <path d="M31 22.5 Q50 15.5 69 22.5 Q50 26.5 31 22.5 Z" fill={shade(c, 0.12)} {...edge} />
            <path d="M39.5 17 Q50 14.5 60.5 17" fill="none" stroke={tint(c, 0.45)} strokeWidth="1.6" />
            {/* the lure, hanging off the brim */}
            <path d="M66 22.5 Q67.5 27 66.6 30" fill="none" stroke="#5c4e43" strokeWidth="0.5" />
            <ellipse cx="66.6" cy="31.6" rx="1.4" ry="2" fill="#e07a3a" stroke="#fbf8f1" strokeWidth="0.4" />
          </g>
        ),
      };
    case "shawl":
      return {
        front: (
          <g filter={`url(#${LAYER})`}>
            <path d="M31.5 45 Q50 36.5 68.5 45 L67 55 Q50 71 33 55 Z" fill={c} {...edge} />
            <path d="M35 49 Q50 61 65 49" fill="none" stroke={tint(c, 0.3)} strokeWidth="0.9" strokeDasharray="0.1 2.2" strokeLinecap="round" />
            <path d="M37 53.5 Q50 64 63 53.5" fill="none" stroke={tint(c, 0.3)} strokeWidth="0.9" strokeDasharray="0.1 2.2" strokeLinecap="round" />
            <path d="M45 66 L44 70 M48 67.5 L47.6 71.5 M52 67.5 L52.4 71.5 M55 66 L56 70" stroke={c} strokeWidth="1" strokeLinecap="round" />
          </g>
        ),
      };
    case "glasses":
      return {
        front: (
          <g>
            <path d="M39 27 Q42 37 50 41 Q58 37 61 27" fill="none" stroke={c} strokeWidth="1" strokeDasharray="0.1 1.9" strokeLinecap="round" />
            <g fill="rgb(251 248 241 / 0.35)" stroke="#6b4e2e" strokeWidth="1.1">
              <circle cx="44.5" cy="26" r="3.9" />
              <circle cx="55.5" cy="26" r="3.9" />
            </g>
            <path d="M48.4 25.6 Q50 24.6 51.6 25.6" fill="none" stroke="#6b4e2e" strokeWidth="1" />
          </g>
        ),
      };
    case "satchel":
      return {
        front: (
          <g filter={`url(#${LAYER})`}>
            <path d="M37 44 L64.5 74" stroke={shade(c, 0.15)} strokeWidth="2.4" strokeLinecap="round" />
            <path d="M58 71 h15 a2 2 0 0 1 2 2 v11 a2 2 0 0 1 -2 2 h-15 a2 2 0 0 1 -2 -2 v-11 a2 2 0 0 1 2 -2 Z" fill={c} {...edge} />
            <path d="M56 71.5 h19 v4.5 Q65.5 80 56 76 Z" fill={shade(c, 0.12)} />
            <circle cx="65.5" cy="77.2" r="0.9" fill="#d3a03d" />
          </g>
        ),
      };
    case "flower":
      return {
        front: (
          <g filter={`url(#${LAYER})`} transform="translate(60.5 15.5)">
            {Array.from({ length: 8 }, (_, i) => (
              <ellipse key={i} cx="0" cy="-3.6" rx="1.7" ry="3.2" fill={c} transform={`rotate(${i * 45})`} />
            ))}
            <circle r="2.2" fill="#6a4320" />
          </g>
        ),
      };
    case "camera":
      return {
        front: (
          <g filter={`url(#${LAYER})`}>
            <path d="M37.5 45 L44 59 M62.5 45 L56 59" stroke="#3a2e26" strokeWidth="1.2" strokeLinecap="round" />
            <rect x="41" y="57" width="18" height="11" rx="1.6" fill="#3a2e26" stroke={EDGE} strokeWidth="0.6" />
            <rect x="43" y="55.4" width="5" height="2.6" rx="0.6" fill="#3a2e26" />
            <circle cx="50" cy="62.6" r="3.6" fill="#6b5b4d" stroke="#d8cbb4" strokeWidth="0.8" />
            <circle cx="49" cy="61.6" r="0.9" fill="#d8cbb4" />
          </g>
        ),
      };
    case "backpack":
      return {
        back: (
          <path d="M57 46 h12 a4 4 0 0 1 4 4 v24 a4 4 0 0 1 -4 4 h-12 Z" fill={c} filter={`url(#${LAYER})`} {...edge} />
        ),
        front: (
          <g>
            <path d="M38.5 44.5 Q39 56 40.5 66" fill="none" stroke={shade(c, 0.2)} strokeWidth="2.2" strokeLinecap="round" />
            <path d="M61.5 44.5 Q61 56 59.5 66" fill="none" stroke={shade(c, 0.2)} strokeWidth="2.2" strokeLinecap="round" />
          </g>
        ),
      };
    case "scarf": {
      const stripeA = /striped|stripe/.test(item) ? "#f4ead8" : tint(c, 0.4);
      const pat = `tree-stripes-${pid}`;
      return {
        front: (
          <g filter={`url(#${LAYER})`}>
            <defs>
              <pattern id={pat} width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(10)">
                <rect width="4" height="4" fill="#b23a2e" />
                <rect width="4" height="2" fill={stripeA} />
              </pattern>
            </defs>
            <path d="M37 41 Q50 47.5 63 41 L63.5 47 Q50 54 36.5 47 Z" fill={`url(#${pat})`} stroke={EDGE} strokeWidth="0.6" />
            <path d="M54.5 47.5 L61.5 48 L61 70 L55 69.5 Z" fill={`url(#${pat})`} stroke={EDGE} strokeWidth="0.6" />
          </g>
        ),
      };
    }
    default:
      return {};
  }
}

/* ----------------------------------------------------------------- puppet */

const BUILD_SCALE: Record<Build, number> = { short: 0.9, average: 0.97, tall: 1.04 };

export function Puppet({ id, color, item = "", build = "average", className, title }: PuppetProps) {
  const kind = itemKind(item);
  const parts = itemParts(kind, item.toLowerCase(), color, id);
  const torso = color;
  const arm = shade(color, 0.14);
  const leg = shade(color, 0.3);
  const head = tint(color, 0.58);
  const edge = { stroke: tint(color, 0.5), strokeWidth: 0.7 };
  const s = BUILD_SCALE[build];
  const pin = { r: 1.25, fill: "#d9b46a", stroke: "#7a5a2e", strokeWidth: 0.35 };

  return (
    <svg
      viewBox="0 0 100 130"
      className={className}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      {/* Scale the figure from its feet so builds stand on the same ground. */}
      <g transform={`translate(50 124) scale(${s}) translate(-50 -124)`} filter={`url(#${GRAIN})`}>
        {parts.back}
        <g filter={`url(#${LAYER})`}>
          <path d={capsule([44.5, 82], [43.5, 120], 10)} fill={leg} {...edge} />
          <path d={capsule([55.5, 82], [56.5, 120], 10)} fill={leg} {...edge} />
        </g>
        <g filter={`url(#${LAYER})`}>
          <rect x="46" y="35" width="8" height="9" rx="2" fill={head} />
          <path d="M35.5 45 Q50 38.5 64.5 45 Q66.5 64 67 85 Q50 89 33 85 Q33.5 64 35.5 45 Z" fill={torso} {...edge} />
        </g>
        <g filter={`url(#${LAYER})`}>
          <path d={capsule([37.5, 47.5], [29, 81], 8.6)} fill={arm} {...edge} />
          <path d={capsule([62.5, 47.5], [71, 81], 8.6)} fill={arm} {...edge} />
        </g>
        <circle cx="50" cy="25" r="13.2" fill={head} {...edge} filter={`url(#${LAYER})`} />
        <circle cx="37.5" cy="48" {...pin} />
        <circle cx="62.5" cy="48" {...pin} />
        <circle cx="45" cy="84" {...pin} />
        <circle cx="55" cy="84" {...pin} />
        {parts.front}
      </g>
    </svg>
  );
}
