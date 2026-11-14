import type { CSSProperties, ReactNode } from "react";
import { Tape } from "./Tape";

/**
 * A photo taped into the scrapbook: white border, a slight tilt, a soft shadow
 * and tape holding it down. Server-safe (no hooks).
 *
 *   <Photo src={url} alt="The green sedan" caption="Summer '68" rotate={-3} />
 *   <Photo alt="Placeholder"><Snapshot scene="hills" /></Photo>
 *
 * `src` can be any URL (API, blob:, data:); pass `children` instead to draw
 * your own content inside the frame.
 */

export type PhotoTape = "top" | "corners" | "none";

export interface PhotoProps {
  src?: string;
  alt: string;
  /** Handwritten caption under the photo. */
  caption?: ReactNode;
  /** Degrees. Default -2. */
  rotate?: number;
  /** Frame width in px. Default 220. */
  width?: number;
  /** Image width / height. Default 4/3. */
  aspect?: number;
  /** How it's stuck down. Default "top". */
  tape?: PhotoTape;
  tapeColor?: string;
  /** "polaroid" leaves a deeper bottom margin (where the caption goes). Default: polaroid if captioned. */
  frame?: "border" | "polaroid";
  /** Custom content instead of an <img>. */
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export function Photo({
  src,
  alt,
  caption,
  rotate = -2,
  width = 220,
  aspect = 4 / 3,
  tape = "top",
  tapeColor,
  frame,
  children,
  className,
  style,
}: PhotoProps) {
  const polaroid = (frame ?? (caption ? "polaroid" : "border")) === "polaroid";
  const pad = Math.max(8, Math.round(width * 0.045));
  const tapeW = Math.round(width * 0.36);
  const seed = Math.round(Math.abs(rotate) * 13 + width) % 97;

  return (
    <figure
      className={className}
      style={{
        position: "relative",
        display: "inline-block",
        width,
        margin: 0,
        padding: `${pad}px ${pad}px ${polaroid ? pad * 3.4 : pad}px`,
        background: "var(--paper-light)",
        borderRadius: 1.5,
        boxShadow: "var(--shadow-lift)",
        transform: `rotate(${rotate}deg)`,
        ...style,
      }}
    >
      <div
        style={{
          position: "relative",
          aspectRatio: String(aspect),
          overflow: "hidden",
          background: "var(--paper-deep)",
        }}
      >
        {children ??
          (src ? (
            // Arbitrary user/API URLs (incl. blob:), so not next/image.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={src}
              alt={alt}
              loading="lazy"
              decoding="async"
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                display: "block",
                filter: "saturate(0.9) sepia(0.12) contrast(0.97)",
              }}
            />
          ) : (
            <Snapshot scene="hills" />
          ))}
        {/* Print sheen + a faint inner edge so it reads as photo paper. */}
        <div
          aria-hidden
          style={{
            position: "absolute",
            inset: 0,
            background:
              "linear-gradient(155deg, rgb(255 255 255 / 0.16), transparent 38%, transparent 72%, rgb(60 35 15 / 0.08))",
            boxShadow: "inset 0 0 0 1px rgb(60 35 15 / 0.08), inset 0 0 18px rgb(60 35 15 / 0.1)",
          }}
        />
      </div>
      {caption ? (
        <figcaption
          className="font-hand"
          style={{
            position: "absolute",
            left: pad,
            right: pad,
            bottom: pad * 0.55,
            textAlign: "center",
            fontSize: Math.max(16, Math.round(width * 0.085)),
            lineHeight: 1.1,
            color: "var(--ink-soft)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {caption}
        </figcaption>
      ) : null}

      {tape === "top" && (
        <Tape
          color={tapeColor}
          width={tapeW}
          height={Math.max(18, Math.round(width * 0.1))}
          rotate={-rotate * 0.8 + 3}
          seed={seed}
          style={{ position: "absolute", top: -pad * 1.1, left: `calc(50% - ${tapeW / 2}px)` }}
        />
      )}
      {tape === "corners" && (
        <>
          <Tape
            color={tapeColor}
            width={tapeW * 0.75}
            height={Math.max(16, Math.round(width * 0.085))}
            rotate={-38}
            seed={seed}
            style={{ position: "absolute", top: -pad * 0.4, left: -tapeW * 0.2 }}
          />
          <Tape
            color={tapeColor}
            width={tapeW * 0.75}
            height={Math.max(16, Math.round(width * 0.085))}
            rotate={-38}
            seed={seed + 3}
            style={{ position: "absolute", bottom: -pad * 0.4, right: -tapeW * 0.2 }}
          />
        </>
      )}
    </figure>
  );
}

/* ------------------------------------------------------------------------ */

export type SnapshotScene = "hills" | "sea" | "house";

/**
 * A small CSS-painted stand-in photo (no image assets), in faded print colours.
 * Use for empty states and demos.
 */
export function Snapshot({ scene = "hills" }: { scene?: SnapshotScene }) {
  const fill: CSSProperties = { position: "absolute", inset: 0 };
  if (scene === "sea") {
    return (
      <div aria-hidden style={{ ...fill, background: "linear-gradient(#e8d6b3 0%, #d9c39c 44%, #7d9aa0 45%, #5d7f88 70%, #c9b28a 71%, #d8c39b 100%)" }}>
        <div style={{ position: "absolute", left: "62%", top: "18%", width: "13%", aspectRatio: "1", borderRadius: "50%", background: "#f3e2bd", boxShadow: "0 0 24px 8px rgb(243 226 189 / 0.6)" }} />
      </div>
    );
  }
  if (scene === "house") {
    return (
      <div aria-hidden style={{ ...fill, background: "linear-gradient(#cfd8cf, #e6dcc4 60%, #8d9a6e 61%, #76845b)" }}>
        <div style={{ position: "absolute", left: "30%", bottom: "30%", width: "40%", height: "34%", background: "#c98f6a" }} />
        <div style={{ position: "absolute", left: "26%", bottom: "63%", width: "48%", height: "22%", background: "#7a4f3a", clipPath: "polygon(50% 0, 100% 100%, 0 100%)" }} />
        <div style={{ position: "absolute", left: "46%", bottom: "30%", width: "9%", height: "17%", background: "#5a3b2b" }} />
        <div style={{ position: "absolute", left: "34%", bottom: "48%", width: "8%", height: "8%", background: "#f2e3b8" }} />
        <div style={{ position: "absolute", left: "58%", bottom: "48%", width: "8%", height: "8%", background: "#f2e3b8" }} />
      </div>
    );
  }
  return (
    <div
      aria-hidden
      style={{
        ...fill,
        background:
          "radial-gradient(120% 70% at 20% 120%, #6f8458 0 40%, transparent 41%), radial-gradient(110% 60% at 85% 115%, #8a9a64 0 45%, transparent 46%), linear-gradient(#e3cfa6, #d9bf93 70%, #c4ac7f)",
      }}
    >
      <div style={{ position: "absolute", left: "66%", top: "20%", width: "12%", aspectRatio: "1", borderRadius: "50%", background: "#f6e6bf", boxShadow: "0 0 22px 6px rgb(246 230 191 / 0.55)" }} />
    </div>
  );
}

export default Photo;
