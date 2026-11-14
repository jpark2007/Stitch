"use client";

/**
 * /memories/<id>: one moment, opened as a pop-up book (the one-moment case
 * of components/book/moment/MomentBook.tsx).
 *
 *   /memories/demo                   the hand-made demo book; its first spread plays the reference sample
 *   /memories/hymn_books             the reference sample: its scene plays as the pop-up
 *   /memories/<id>?demo=1            a moment of the demo family (m-steps-1987 plays the sample)
 *   /memories/<id>?family=<fid>      a saved moment from that family
 *   /memories/<id>                   a saved moment from the family remembered here, else the user's first
 *
 * A moment is one spread: the story page on the left (year, title, the
 * storybook text, a taped label per telling, photos) and its scene on the
 * right, rising from the page when the spread opens and playing once it lies
 * still. Choosing a teller's label replays the scene from the start with
 * their words. The mute button silences the narration with the paper sounds.
 *
 * Signed out, without a family, a moment that is not there, Stitch out of
 * reach (the demo, with a note) and errors each get their own sheet
 * (BookScreen). Only a true 404 says "This page isn't in the book yet".
 *
 * Query, for deterministic views and tests: ?state=closed|open, ?spread=<n>,
 * ?turn=<0..1>, ?focus=left|right, ?still=1, ?debug=1, ?narrator=<person id
 * or name> (start with that telling), ?t=<seconds> (hold the scene at that
 * time once it opens, instead of playing). See readBookQuery.
 */

import { use, useCallback, useMemo } from "react";
import { BookScreen, readBookQuery, useBookLoad, type BookShown, type Search } from "@/components/book/moment/MomentBook";
import { loadMomentPage } from "./moment";

export { BookLoading } from "@/components/book/moment/MomentBook";

const keepBook = (s: BookShown) => s.kind === "book" || s.kind === "not-found";

export function MemoryBook({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Search> }) {
  const { id } = use(params);
  const q = use(searchParams);
  const query = useMemo(() => readBookQuery(q), [q]);
  const { family, demo } = query;
  const run = useCallback((signal: AbortSignal) => loadMomentPage(id, { family, demo }, signal), [id, family, demo]);
  const { value, retry } = useBookLoad(`${id}|${family ?? ""}|${demo ? 1 : 0}`, run, keepBook);
  return <BookScreen shown={value} query={query} retry={retry} contents={false} />;
}
