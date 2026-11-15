"use client";

// The audience window: one browser window, named AUDIENCE_WINDOW, that the
// operator drags to the projector. The control page keeps its handle and
// sends it to pages with location.assign (no focus change, so the operator's
// keyboard stays here). Without a live handle (not opened yet, or this page
// was reloaded) it uses window.open with the same name, which reuses that
// window when it is still open. Once a second it reads whether the window is
// still open and, being same-origin, which page it shows.
import { useCallback, useEffect, useRef, useState } from "react";

import { AUDIENCE_WINDOW } from "./audience";

export type AudienceStatus =
  /** No handle: never opened from this page load (it may still be open from before a reload). */
  | { kind: "unknown" }
  | { kind: "open"; location: string | null }
  | { kind: "closed" }
  /** The browser refused to open a window (a popup blocker). */
  | { kind: "blocked" };

/** A window, not a tab, so it can be dragged to the projector and made full screen. */
const FEATURES = "popup=yes,width=1280,height=800";

function readLocation(w: Window): string | null {
  try {
    const { pathname, search, href } = w.location;
    if (href.startsWith("about:")) return href;
    return `${pathname}${search}`;
  } catch {
    return null; // not readable (another origin)
  }
}

export function useAudience() {
  const handle = useRef<Window | null>(null);
  const [status, setStatus] = useState<AudienceStatus>({ kind: "unknown" });

  const read = useCallback(() => {
    const w = handle.current;
    if (!w) return;
    if (w.closed) {
      handle.current = null;
      setStatus((s) => (s.kind === "closed" ? s : { kind: "closed" }));
      return;
    }
    const location = readLocation(w);
    setStatus((s) => (s.kind === "open" && s.location === location ? s : { kind: "open", location }));
  }, []);

  useEffect(() => {
    const t = window.setInterval(read, 1000);
    return () => window.clearInterval(t);
  }, [read]);

  /** Send the audience window to `url`, opening it if need be. False when the browser refused. */
  const go = useCallback(
    (url: string): boolean => {
      const w = handle.current;
      if (w && !w.closed) {
        try {
          w.location.assign(url);
          setStatus({ kind: "open", location: url });
          return true;
        } catch {
          // Fall through to window.open, which navigates it by name.
        }
      }
      const opened = window.open(url, AUDIENCE_WINDOW, FEATURES);
      if (!opened) {
        setStatus({ kind: "blocked" });
        return false;
      }
      handle.current = opened;
      setStatus({ kind: "open", location: url });
      return true;
    },
    [],
  );

  /**
   * "Open audience window": find the named window (left open before a reload
   * of this page) and keep what it shows, or open it fresh at `homeUrl`.
   */
  const open = useCallback(
    (homeUrl: string): "opened" | "reconnected" | "blocked" => {
      const w = handle.current;
      if (w && !w.closed) {
        w.focus();
        return "reconnected";
      }
      const found = window.open("", AUDIENCE_WINDOW, FEATURES);
      if (!found) {
        setStatus({ kind: "blocked" });
        return "blocked";
      }
      handle.current = found;
      const location = readLocation(found);
      if (location === null || location.startsWith("about:")) {
        found.location.assign(homeUrl);
        setStatus({ kind: "open", location: homeUrl });
        return "opened";
      }
      setStatus({ kind: "open", location });
      return "reconnected";
    },
    [],
  );

  const close = useCallback(() => {
    handle.current?.close();
    handle.current = null;
    setStatus({ kind: "closed" });
  }, []);

  return { status, go, open, close };
}
