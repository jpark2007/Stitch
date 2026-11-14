"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { use } from "react";
import { demoCover, demoSpreads } from "@/components/book/demoSpreads";

// three.js needs the browser: load the book on the client only.
const Book3D = dynamic(() => import("@/components/book/Book3D").then((m) => m.Book3D), {
  ssr: false,
  loading: () => <BookLoading />,
});

type Search = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

/** -1|left, 1|right; anything else is the whole spread. */
function readFocus(v: string | undefined): -1 | 0 | 1 {
  if (v === "-1" || v === "left") return -1;
  if (v === "1" || v === "right") return 1;
  return 0;
}

/**
 * Reads ?state=closed|open, ?spread=<n>, ?turn=<0..1> (freeze mid-turn) and
 * ?focus=left|right (close-up on one page) for deterministic views, and the
 * test switches ?still=1 (no parallax, peels or hints) and ?debug=1
 * (`window.__book` is the controller; a probe card stands in each stage slot).
 */
function readInitial(q: Search) {
  const state = one(q.state);
  const spread = Number(one(q.spread));
  const turn = Number(one(q.turn));
  return {
    state: state === "open" || state === "closed" ? state : undefined,
    spread: Number.isFinite(spread) && spread > 0 ? spread : undefined,
    turn: one(q.turn) != null && Number.isFinite(turn) ? turn : null,
    focus: readFocus(one(q.focus)),
  } as const;
}

export function MemoryBook({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Search> }) {
  const { id } = use(params);
  const q = use(searchParams);

  if (id !== "demo") {
    return (
      <div className="grid min-h-[100dvh] place-items-center px-6 text-center">
        <div>
          <p className="kicker">Moment</p>
          <h1 className="mt-3 text-4xl">This page isn&rsquo;t in the book yet</h1>
          <p className="mt-4 text-ink-soft">
            Real moments load from the family&rsquo;s book soon. Meanwhile, <Link href="/memories/demo">open the demo book</Link>.
          </p>
        </div>
      </div>
    );
  }

  return (
    <Book3D
      spreads={demoSpreads}
      cover={demoCover}
      initial={readInitial(q)}
      still={one(q.still) === "1"}
      debug={one(q.debug) === "1"}
    />
  );
}

export function BookLoading() {
  return (
    <div className="grid h-[100dvh] place-items-center" style={{ background: "#1a110b" }}>
      <p className="font-hand text-2xl text-[#f3e6cf]/60">opening the book&hellip;</p>
    </div>
  );
}
