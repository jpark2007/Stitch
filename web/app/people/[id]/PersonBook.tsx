"use client";

/**
 * /people/<id>: one person's pop-up storybook. Its spreads are the moments
 * they are in, in order by year, then "The end"; every moment's scene plays
 * on its own spread (components/book/moment/MomentBook.tsx). With the book
 * closed, a small contents card lies beside it; once open, a quiet
 * "Contents" tab by the mute button turns to any moment.
 *
 *   /people/<id>                   from the family remembered here, else the user's first
 *   /people/<id>?family=<fid>      from that family
 *   /people/<id>?demo=1            the demo family's person (david, rosa, sofia, ...)
 *
 * The same query as /memories/<id> (readBookQuery): ?state, ?spread, ?turn,
 * ?focus, ?still, ?debug, ?narrator (the opening spread's telling), ?t.
 */

import { use, useCallback, useMemo } from "react";
import { BookScreen, readBookQuery, useBookLoad, type BookShown, type Search } from "@/components/book/moment/MomentBook";
import { loadPersonPage } from "./load";

const keepBook = (s: BookShown) => s.kind === "book" || s.kind === "not-found";

export function PersonBook({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Search> }) {
  const { id } = use(params);
  const q = use(searchParams);
  const query = useMemo(() => readBookQuery(q), [q]);
  const { family, demo } = query;
  const run = useCallback((signal: AbortSignal) => loadPersonPage(id, { family, demo }, signal), [id, family, demo]);
  const { value, retry } = useBookLoad(`${id}|${family ?? ""}|${demo ? 1 : 0}`, run, keepBook);
  return <BookScreen shown={value} query={query} retry={retry} contents />;
}
