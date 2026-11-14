"use client";

/**
 * <PrintedCaption>: the teller's words printed on the stage page, in front
 * of the pop-up, the way a storybook sets a line under its picture. Not a
 * subtitle bar: ink on the paper, lit by the same lamp, bent with the page.
 *
 * Words already spoken are in full ink and the rest wait in pencil, so the
 * line reads along with the narration. A new caption fades out the old line
 * and inks in the next; switching teller swaps the words over the same
 * scene. Follows the player's discrete status (controller.subscribe) and
 * redraws a small canvas only when the caption or spoken word changes:
 * nothing re-renders React while the scene plays.
 */

import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import { CanvasTexture, LinearFilter, SRGBColorSpace } from "three";
import type { PlaybackController } from "@/components/stage/playback";
import { PagePrint } from "../pagePrint";
import { INK, INK_FAINT, canvas, setSpacing, wrap, type PageFonts } from "../paperArt";
import type { BookTeller } from "../types";

const TEX_W = 1024;
const TEX_H = 320;
/** Caption type size in canvas px (the print is ~0.63 world wide: about the page's body text). */
const SIZE = 74;
const LONG = 52;
const OUT_S = 0.2;
const IN_S = 0.55;

export interface PrintedCaptionProps {
  controller: PlaybackController;
  tellers: readonly BookTeller[];
  fonts: PageFonts;
  /** World size of the print and its centre in slot space. */
  width: number;
  x: number;
  z: number;
  /** The stage's reveal: the print fades as the page turns away. */
  reveal: { readonly current: number };
}

/** Caption tokens with how many are spoken, proportional when the timings and tokens differ. */
function spokenTokens(text: string, words: number, wordIndex: number): { tokens: string[]; spoken: number } {
  const tokens = text.split(/\s+/).filter(Boolean);
  if (words <= 0) return { tokens, spoken: tokens.length };
  const k = Math.max(0, Math.min(1, (wordIndex + 1) / words));
  return { tokens, spoken: words === tokens.length ? wordIndex + 1 : Math.round(k * tokens.length) };
}

interface CaptionWords {
  key: string;
  spoken: number;
  text: string;
  teller: BookTeller | null;
}

/** The caption's canvas and texture; `print` redraws the words. */
class CaptionPrinter {
  readonly texture: CanvasTexture;
  private readonly ctx: CanvasRenderingContext2D;

  constructor() {
    const { c, ctx } = canvas(TEX_W, TEX_H);
    this.ctx = ctx;
    this.texture = new CanvasTexture(c);
    this.texture.colorSpace = SRGBColorSpace;
    this.texture.anisotropy = 8;
    // Text stays crisp; no mip chain to rebuild on every word.
    this.texture.generateMipmaps = false;
    this.texture.minFilter = LinearFilter;
  }

  print(w: CaptionWords, fonts: PageFonts) {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, TEX_W, TEX_H);
    this.texture.needsUpdate = true;
    if (!w.key) return;
    const size = w.text.length > LONG ? SIZE * 0.86 : SIZE;
    ctx.font = `500 ${size}px ${fonts.hand}`;
    ctx.textBaseline = "alphabetic";
    ctx.textAlign = "left";
    const lines = wrap(ctx, w.text, TEX_W - 80).slice(0, 3);
    const lineH = size * 1.08;
    const attribution = w.teller ? 50 : 0;
    const blockH = lines.length * lineH + attribution;
    let y = (TEX_H - blockH) / 2 + size * 0.82;
    let k = 0;
    const space = ctx.measureText(" ").width;
    for (const line of lines) {
      const words = line.split(" ");
      const lw = ctx.measureText(line).width;
      let cx = (TEX_W - lw) / 2;
      for (const word of words) {
        // Spoken words in ink, the rest waiting in pencil.
        ctx.fillStyle = k < w.spoken ? INK : "rgba(78,72,68,0.4)";
        ctx.fillText(word, cx, y);
        cx += ctx.measureText(word).width + space;
        k++;
      }
      y += lineH;
    }
    if (w.teller) {
      // Who is telling: a short stitch in their thread, and the name.
      const label = w.teller.name.toUpperCase();
      ctx.font = `700 24px ${fonts.sans}`;
      setSpacing(ctx, 5);
      const lw = ctx.measureText(label).width;
      const total = 34 + 12 + lw;
      const x0 = (TEX_W - total) / 2;
      const ly = y - lineH + 50;
      ctx.strokeStyle = w.teller.color;
      ctx.lineWidth = 3;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(x0, ly - 7);
      ctx.lineTo(x0 + 12, ly - 7);
      ctx.moveTo(x0 + 20, ly - 7);
      ctx.lineTo(x0 + 32, ly - 7);
      ctx.stroke();
      ctx.fillStyle = INK_FAINT;
      ctx.fillText(label, x0 + 46, ly);
      setSpacing(ctx, 0);
    }
  }

  dispose() {
    this.texture.dispose();
  }
}

interface Printed {
  key: string;
  spoken: number;
}

export function PrintedCaption({ controller, tellers, fonts, width, x, z, reveal }: PrintedCaptionProps) {
  const invalidate = useThree((s) => s.invalidate);
  const printer = useMemo(() => new CaptionPrinter(), []);
  useEffect(() => () => printer.dispose(), [printer]);

  /** What is on the canvas, what should be, and the fade between them. */
  const state = useRef({ shown: { key: "", spoken: -1 } as Printed, fade: 0, phase: "idle" as "idle" | "out" | "in", t: 0 });
  const opacityRef = useRef(0);

  // The words to print now, from the player's status.
  const want = useRef<CaptionWords>({ key: "", spoken: 0, text: "", teller: null });
  useEffect(() => {
    const read = () => {
      const st = controller.getStatus();
      const track = controller.compiled.narrator(st.narrator);
      const i = st.captionIndex;
      const caption = track && i >= 0 ? track.captions[i] : null;
      const line = track && i >= 0 ? track.captionLines[i] : null;
      if (!caption) {
        want.current = { key: "", spoken: 0, text: "", teller: null };
      } else {
        const { spoken } = spokenTokens(caption.text, line?.words?.length ?? 0, st.wordIndex);
        want.current = {
          key: `${st.narrator}:${i}`,
          spoken,
          text: caption.text,
          teller: tellers.find((t) => t.id === st.narrator) ?? null,
        };
      }
      invalidate();
    };
    read();
    return controller.subscribe(read);
  }, [controller, tellers, invalidate]);

  const draw = () => {
    const w = want.current;
    state.current.shown = { key: w.key, spoken: w.spoken };
    printer.print(w, fonts);
  };

  useFrame((frame) => {
    const s = state.current;
    const w = want.current;
    // Wall time, so a slow frame never stretches the fade.
    const now = performance.now() / 1000;
    const dt = s.t > 0 ? Math.min(now - s.t, 0.5) : 0;
    s.t = now;
    if (w.key !== s.shown.key) {
      // A new line: fade the old one out first (nothing shown: print at once).
      if (s.fade <= 0.001 || !s.shown.key) {
        draw();
        s.phase = w.key ? "in" : "idle";
        s.fade = 0;
      } else s.phase = "out";
    } else if (w.spoken !== s.shown.spoken) draw();

    if (s.phase === "out") {
      s.fade = Math.max(0, s.fade - dt / OUT_S);
      if (s.fade === 0) {
        draw();
        s.phase = w.key ? "in" : "idle";
      }
    } else if (s.phase === "in") {
      s.fade = Math.min(1, s.fade + dt / IN_S);
      if (s.fade === 1) s.phase = "idle";
    }
    if (s.phase !== "idle") frame.invalidate();
    // Printed with the pop-up: it goes as the page lifts to turn.
    const r = reveal.current;
    const settle = r >= 1 ? 1 : r <= 0.7 ? 0 : (r - 0.7) / 0.3;
    opacityRef.current = s.fade * s.fade * (3 - 2 * s.fade) * settle;
  });

  const depth = (width * TEX_H) / TEX_W;
  return <PagePrint map={printer.texture} width={width} depth={depth} x={x} z={z} opacity={opacityRef} />;
}
