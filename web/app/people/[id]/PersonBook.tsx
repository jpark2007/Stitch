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
 *
 * On the viewer's own book in a real family, a small paper "Add a memory"
 * tag lies on the table too (AddToMyBook).
 */

import Link from "next/link";
import { use, useCallback, useMemo } from "react";
import { BookScreen, readBookQuery, useBookLoad, type Search } from "@/components/book/moment/MomentBook";
import { hrefs } from "@/components/flow/session";
import { loadPersonPage, type PersonPage } from "./load";

const keepBook = (s: PersonPage) => s.kind === "book" || s.kind === "not-found";

export function PersonBook({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Search> }) {
  const { id } = use(params);
  const q = use(searchParams);
  const query = useMemo(() => readBookQuery(q), [q]);
  const { family, demo } = query;
  const run = useCallback((signal: AbortSignal) => loadPersonPage(id, { family, demo }, signal), [id, family, demo]);
  const { value, retry } = useBookLoad(`${id}|${family ?? ""}|${demo ? 1 : 0}`, run, keepBook);
  return (
    <>
      <BookScreen shown={value} query={query} retry={retry} contents />
      {value?.kind === "book" && value.ownFamilyId ? <AddToMyBook familyId={value.ownFamilyId} /> : null}
    </>
  );
}

/**
 * "Add a memory", as a paper tag in the book's own chrome (the mute button's
 * paper and shadow). It sits where none of the book's controls ever go:
 *
 *   wide screens   the top bar, just right of "← Family tree" (the right end
 *                  holds Contents and the mute button; the middle, a demo
 *                  note, which this page never has alongside it)
 *   phones         centred on the table's foot, above the hint that opens the
 *                  book and the spread dots, below the book (the top holds
 *                  the contents strip and the Contents menu)
 */
function AddToMyBook({ familyId }: { familyId: string }) {
  return (
    <Link
      href={hrefs.add({ familyId })}
      className={
        // Phones: a paper tag (the mute button's paper and shadow). Wide
        // screens: words like "← Family tree" beside it, in its style.
        "fixed bottom-[calc(7rem+env(safe-area-inset-bottom))] left-1/2 z-10 inline-flex h-11 -translate-x-1/2 items-center gap-1.5 whitespace-nowrap rounded-full bg-paper-light/95 px-4 font-display text-[1.02rem] text-ink-soft no-underline shadow-[0_2px_3px_rgb(0_0_0/0.25),0_8px_20px_-6px_rgb(0_0_0/0.5)] transition-colors hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper-light " +
        "sm:bottom-auto sm:left-[10.25rem] sm:top-5 sm:translate-x-0 sm:rounded-sm sm:bg-transparent sm:px-1 sm:text-lg sm:tracking-tight sm:text-[#f3e6cf]/80 sm:shadow-none sm:hover:text-[#f3e6cf]"
      }
    >
      <span aria-hidden className="text-lg leading-none text-accent-deep sm:text-inherit">
        +
      </span>
      Add a memory
    </Link>
  );
}
