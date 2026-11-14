"use client";

/**
 * Where the threads come from, as one small state machine:
 *
 *   ?demo=1, or mock mode         -> the local demo family
 *   signed out (401)              -> "signed-out" (the screen offers sign in or the demo)
 *   signed in, no family          -> "no-family"
 *   API out of reach (status 0)   -> the local demo family, with a note
 *   signed in with a family       -> that family's threads (GET threads + tree)
 *   anything else                 -> "error", with retry
 *
 * The current family is ?family=<id> when given, else the one remembered in
 * this browser (the same key the rest of the app uses), else the first.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, isApiError, isMockMode } from "@/lib/api";
import type { FamilySummary, Person } from "@/lib/types";
import { pickFamily, recallFamily, rememberFamily } from "@/components/flow/session";
import { toThreadsData, type ThreadsData } from "./data";

export type DemoReason = "chosen" | "mock" | "offline";

export type ThreadsSource =
  | { kind: "loading" }
  | { kind: "signed-out" }
  | { kind: "no-family" }
  | {
      kind: "error";
      message: string;
      /** When one family failed to load: the user's families, to try another. */
      families?: FamilySummary[];
      familyId?: string;
    }
  | { kind: "demo"; why: DemoReason }
  | {
      kind: "family";
      family: FamilySummary;
      families: FamilySummary[];
      familyName: string;
      data: ThreadsData;
    };

const FRIENDLY_ERROR = "Something went wrong while gathering your family’s threads. Please try again.";

function message(e: unknown): string {
  return isApiError(e) && e.status >= 400 && e.status < 500 && e.status !== 401 ? e.detail : FRIENDLY_ERROR;
}

const offline = (e: unknown) => isApiError(e) && e.status === 0;
const signedOut = (e: unknown) => isApiError(e) && e.status === 401;

/** Replace query params on the current URL without a navigation. */
function setQuery(changes: Record<string, string | null>) {
  try {
    const url = new URL(window.location.href);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null) url.searchParams.delete(k);
      else url.searchParams.set(k, v);
    }
    window.history.replaceState(window.history.state, "", url);
  } catch {
    // The URL is a convenience; the screen works without it.
  }
}

export function useThreadsSource(opts: { family?: string; demo?: boolean }) {
  const initial: ThreadsSource = isMockMode()
    ? { kind: "demo", why: "mock" }
    : opts.demo
      ? { kind: "demo", why: "chosen" }
      : { kind: "loading" };
  const [source, setSource] = useState<ThreadsSource>(initial);
  const current = useRef(source);
  useEffect(() => {
    current.current = source;
  }, [source]);
  // Only the latest load may settle the screen.
  const ticket = useRef(0);
  const wanted = useRef<string | null>(opts.family ?? null);
  /** Let no load that is under way settle the screen. */
  const invalidate = useCallback(() => {
    ticket.current++;
  }, []);

  /** Ask the API. `quiet` keeps the family on screen while it refreshes. */
  const load = useCallback(async (quiet = false) => {
    const mine = ++ticket.current;
    const settle = (s: ThreadsSource) => {
      if (mine !== ticket.current) return;
      // A background refresh that fails (offline for a moment, a server
      // hiccup) keeps the family on screen; only signing out replaces it.
      if (quiet && (s.kind === "error" || s.kind === "demo")) return;
      // A refresh that changed nothing keeps the same objects, so the
      // threads are not sewn in again.
      const was = current.current;
      if (
        s.kind === "family" &&
        was.kind === "family" &&
        was.family.id === s.family.id &&
        was.familyName === s.familyName &&
        JSON.stringify(was.families) === JSON.stringify(s.families) &&
        JSON.stringify(was.data) === JSON.stringify(s.data)
      ) {
        return;
      }
      setSource(s);
    };
    if (!quiet) setSource((s) => (s.kind === "loading" ? s : { kind: "loading" }));

    let me;
    try {
      me = await api.me();
    } catch (e) {
      if (signedOut(e)) settle({ kind: "signed-out" });
      else if (offline(e)) settle({ kind: "demo", why: "offline" });
      else settle({ kind: "error", message: message(e) });
      return;
    }
    if (mine !== ticket.current) return;
    if (me.families.length === 0) {
      settle({ kind: "no-family" });
      return;
    }
    const family = pickFamily(me.families, wanted.current ?? recallFamily())!;
    // A family named in the link becomes this browser's current one too.
    if (family.id === wanted.current) rememberFamily(family.id);

    const [threads, tree] = await Promise.allSettled([api.getThreads(family.id), api.getTree(family.id)]);
    if (threads.status === "rejected") {
      const e = threads.reason;
      if (signedOut(e)) settle({ kind: "signed-out" });
      else if (offline(e)) settle({ kind: "demo", why: "offline" });
      else settle({ kind: "error", message: message(e), families: me.families, familyId: family.id });
      return;
    }
    if (tree.status === "rejected" && signedOut(tree.reason)) {
      settle({ kind: "signed-out" });
      return;
    }
    // Without the tree the threads still draw, just without family branches.
    const treePeople: { people: Person[] } | null = tree.status === "fulfilled" ? tree.value : null;
    settle({
      kind: "family",
      family,
      families: me.families,
      familyName: (tree.status === "fulfilled" && tree.value.familyName) || family.name,
      data: toThreadsData(threads.value, treePeople, new Date().getFullYear()),
    });
  }, []);

  // On mount, and again whenever the page is shown after being kept in the
  // background: load, or quietly refresh a family already on screen. A demo
  // the visitor chose (or mock mode) stays put.
  useEffect(() => {
    const s = current.current;
    if (s.kind === "demo" && s.why !== "offline") return;
    // Deferred so the first paint (the skeleton) never waits on it.
    const id = setTimeout(() => void load(s.kind === "family"), 0);
    return () => {
      clearTimeout(id);
      invalidate();
    };
  }, [load, invalidate]);

  /** Try the API again (after an error, or when it was out of reach). */
  const retry = useCallback(() => {
    setQuery({ demo: null });
    void load();
  }, [load]);

  /** Show the local demo family instead. */
  const exploreDemo = useCallback(() => {
    ticket.current++;
    setQuery({ demo: "1", family: null, highlight: null });
    setSource({ kind: "demo", why: "chosen" });
  }, []);

  /** True once the user has picked another family here (the URL's view seeds no longer apply). */
  const [switched, setSwitched] = useState(false);

  /** Open another of the user's families, and remember it in this browser. */
  const switchFamily = useCallback(
    (fid: string) => {
      wanted.current = fid;
      rememberFamily(fid);
      setSwitched(true);
      setQuery({ family: fid, highlight: null, focus: null, zoom: null });
      void load();
    },
    [load],
  );

  return { source, switched, retry, exploreDemo, switchFamily };
}
