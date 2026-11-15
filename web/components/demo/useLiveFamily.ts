"use client";

// The family data the control page shows: its people (once, and again on
// request), and its moments and films, polled every few seconds so a story
// that arrives by text or call shows up (with a "New" mark, see arrivals.ts).
// A failed poll keeps the last list and says so; a 401 goes to sign-in.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { hrefs } from "@/components/flow/session";
import { isApiError, type StitchApi } from "@/lib/api";
import type { FilmSummary, MomentSummary, Person } from "@/lib/types";

import { NO_ARRIVALS, noteArrivals, type Arrivals } from "./arrivals";

export const POLL_MS = 5000;

type Loadable<T> = { kind: "loading" } | { kind: "ready"; value: T; error: string | null } | { kind: "error"; error: string };

function messageOf(e: unknown, fallback: string): string {
  return isApiError(e) && e.status !== 0 && e.detail ? e.detail : fallback;
}

export function useLiveFamily(api: StitchApi, fid: string | null) {
  const router = useRouter();
  const [people, setPeople] = useState<Loadable<Person[]>>({ kind: "loading" });
  const [moments, setMoments] = useState<Loadable<MomentSummary[]>>({ kind: "loading" });
  const [films, setFilms] = useState<Loadable<FilmSummary[]>>({ kind: "loading" });
  const [momentArrivals, setMomentArrivals] = useState<Arrivals>(NO_ARRIVALS);
  const [filmArrivals, setFilmArrivals] = useState<Arrivals>(NO_ARRIVALS);
  const [peopleTry, setPeopleTry] = useState(0);
  const [polledAt, setPolledAt] = useState<number | null>(null);

  const signedOut = useCallback(
    (e: unknown): boolean => {
      if (!(isApiError(e) && e.status === 401)) return false;
      router.replace(hrefs.login(`${window.location.pathname}${window.location.search}`));
      return true;
    },
    [router],
  );

  useEffect(() => {
    if (!fid) return;
    let live = true;
    api.getTree(fid).then(
      (t) => {
        if (live) setPeople({ kind: "ready", value: t.people, error: null });
      },
      (e: unknown) => {
        if (!live || signedOut(e)) return;
        setPeople({ kind: "error", error: messageOf(e, "The family’s people didn’t load.") });
      },
    );
    return () => {
      live = false;
    };
  }, [api, fid, peopleTry, signedOut]);

  useEffect(() => {
    if (!fid) return;
    let live = true;
    let timer: number | undefined;
    const poll = async () => {
      const [m, f] = await Promise.allSettled([api.getThreads(fid), api.listFilms(fid)]);
      if (!live) return;
      const now = Date.now();
      if (m.status === "fulfilled") {
        setMoments({ kind: "ready", value: m.value.moments, error: null });
        setMomentArrivals((a) => noteArrivals(a, m.value.moments.map((x) => x.id), now));
      } else if (!signedOut(m.reason)) {
        const error = messageOf(m.reason, "Couldn’t refresh the moments. Trying again.");
        setMoments((s) => (s.kind === "ready" ? { ...s, error } : { kind: "error", error }));
      }
      if (f.status === "fulfilled") {
        setFilms({ kind: "ready", value: f.value.films, error: null });
        setFilmArrivals((a) => noteArrivals(a, f.value.films.map((x) => x.id), now));
      } else if (!signedOut(f.reason)) {
        const error = messageOf(f.reason, "Couldn’t refresh the films. Trying again.");
        setFilms((s) => (s.kind === "ready" ? { ...s, error } : { kind: "error", error }));
      }
      setPolledAt(now);
      timer = window.setTimeout(poll, POLL_MS);
    };
    void poll();
    return () => {
      live = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [api, fid, signedOut]);

  return {
    people,
    moments,
    films,
    momentArrivals,
    filmArrivals,
    polledAt,
    reloadPeople: () => {
      setPeople({ kind: "loading" });
      setPeopleTry((n) => n + 1);
    },
  };
}

export type LiveFamily = ReturnType<typeof useLiveFamily>;
export type { Loadable };
