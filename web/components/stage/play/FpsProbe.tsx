"use client";

/**
 * <FpsProbe>: frames per second, measured in the render loop and written to
 * `<html data-fps>` twice a second, so capture scripts can read it during
 * playback without anything on screen. Pass `target` (an element id) to also
 * print it there, e.g. the render route's ?fps readout.
 *
 * Mount it inside the Canvas (a Stage's `overlay`). It allocates nothing per
 * frame and draws nothing.
 */

import { useFrame } from "@react-three/fiber";
import { useEffect, useRef } from "react";

const WINDOW_MS = 500;

export function FpsProbe({ target }: { target?: string }) {
  const s = useRef({ frames: 0, since: 0, el: null as HTMLElement | null });
  useEffect(() => {
    const st = s.current;
    return () => {
      st.el = null;
      delete document.documentElement.dataset.fps;
    };
  }, [target]);
  useFrame(() => {
    const st = s.current;
    const now = performance.now();
    if (st.since === 0) st.since = now;
    st.frames++;
    const dt = now - st.since;
    if (dt < WINDOW_MS) return;
    const fps = (st.frames * 1000) / dt;
    document.documentElement.dataset.fps = fps.toFixed(1);
    // Looked up here (twice a second), so a readout mounted after the Canvas is found.
    if (target && !st.el?.isConnected) st.el = document.getElementById(target);
    if (st.el) st.el.textContent = `${Math.round(fps)} fps`;
    st.frames = 0;
    st.since = now;
  });
  return null;
}
