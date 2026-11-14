import type { CSSProperties } from "react";

/**
 * A strip of washi or masking tape, for sticking DOM scrapbook pieces down.
 * Translucent (multiplies over whatever is under it), with torn ends.
 * Server-safe: no hooks, deterministic from `seed`.
 *
 *   <Tape color="#d9a6a0" pattern="stripes" rotate={-6} />
 */

export type TapePattern = "plain" | "stripes" | "dots" | "grid";

export interface TapeProps {
  /** Tape colour (any CSS colour). Default: cream masking tape. */
  color?: string;
  pattern?: TapePattern;
  /** CSS length or px. Default 96. */
  width?: number | string;
  /** CSS length or px. Default 24. */
  height?: number | string;
  /** Degrees. Default 0. */
  rotate?: number;
  /** Changes the torn ends. Default 1. */
  seed?: number;
  className?: string;
  style?: CSSProperties;
}

const MASKING = "#e9dcbd";

function tornEnds(seed: number): string {
  // Small deterministic zigzag at each end, like a hand-torn strip.
  let s = seed * 9301 + 49297;
  const rnd = () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
  const teeth = 7;
  const left: string[] = [];
  const right: string[] = [];
  for (let i = 0; i <= teeth; i++) {
    const y = (i / teeth) * 100;
    left.push(`${(rnd() * 3.2).toFixed(2)}% ${y.toFixed(1)}%`);
    right.push(`${(100 - rnd() * 3.2).toFixed(2)}% ${(100 - y).toFixed(1)}%`);
  }
  return `polygon(${left.join(",")},${right.join(",")})`;
}

function patternLayer(pattern: TapePattern): string | undefined {
  switch (pattern) {
    case "stripes":
      return "repeating-linear-gradient(115deg, rgb(255 255 255 / 0.32) 0 5px, transparent 5px 11px)";
    case "dots":
      return "radial-gradient(circle at 50% 50%, rgb(255 255 255 / 0.5) 0 1.6px, transparent 2.2px) 0 0 / 9px 9px";
    case "grid":
      return "linear-gradient(rgb(255 255 255 / 0.28) 1px, transparent 1px) 0 0 / 8px 8px, linear-gradient(90deg, rgb(255 255 255 / 0.28) 1px, transparent 1px) 0 0 / 8px 8px";
    default:
      return undefined;
  }
}

const len = (v: number | string) => (typeof v === "number" ? `${v}px` : v);

export function Tape({
  color = MASKING,
  pattern = "plain",
  width = 96,
  height = 24,
  rotate = 0,
  seed = 1,
  className,
  style,
}: TapeProps) {
  const layers = [
    // Fibre sheen and slight crinkle along the strip.
    "linear-gradient(180deg, rgb(255 255 255 / 0.28), transparent 35%, transparent 70%, rgb(0 0 0 / 0.05))",
    "repeating-linear-gradient(90deg, rgb(255 255 255 / 0.06) 0 2px, transparent 2px 7px)",
    patternLayer(pattern),
  ].filter(Boolean);

  return (
    <span
      aria-hidden
      className={className}
      style={{
        display: "inline-block",
        width: len(width),
        height: len(height),
        transform: rotate ? `rotate(${rotate}deg)` : undefined,
        filter: "drop-shadow(0 1px 0.5px rgb(74 47 29 / 0.18))",
        // On the outer element: a filter isolates its children, so the
        // multiply must happen here to blend with the page underneath.
        mixBlendMode: "multiply",
        pointerEvents: "none",
        ...style,
      }}
    >
      <span
        style={{
          display: "block",
          width: "100%",
          height: "100%",
          clipPath: tornEnds(seed),
          background: `${layers.join(", ")}, ${color}`,
          opacity: 0.86,
        }}
      />
    </span>
  );
}

export default Tape;
