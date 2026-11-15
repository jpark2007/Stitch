"use client";

/**
 * /traditions: the Book of Family Traditions, one spread per tradition, in
 * the same pop-up book as a person's moments (Book3D): the closed book on
 * the table with its contents card, the page turn and its sounds, the mute
 * button, the dots and the contents tab. Each story page prints when the
 * tradition happens, its title and story, and a snapshot of the people who
 * keep it (traditionBook.ts); each right page stands the category's paper
 * kit, with a few of those people in front (TraditionStage.tsx).
 *
 *   /traditions                  the family remembered here, else the user's first
 *   /traditions?family=<fid>     that family
 *   /traditions?demo=1           the demo family's traditions, read-only
 *
 * The book routes' query works here too (readBookQuery): ?state, ?spread,
 * ?turn, ?focus, ?still, ?debug; and ?tradition=<id> (or ?memory=<id>)
 * opens it at that tradition's spread (openAt.ts). Signed out, no family yet, no traditions
 * yet, a family that is not the user's, Stitch out of reach (the demo, with
 * a note) and errors each get the book routes' paper sheet (BookSheet).
 */

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { BookControls, BookView } from "@/components/book/Book3D";
import { ContentsCard, ContentsTab } from "@/components/book/Contents";
import { BookLoading, BookSheet, readBookQuery, useBookLoad, type Search } from "@/components/book/moment/MomentBook";
import type { StageSlotArgs } from "@/components/book/StoryPage";
import { ButtonLink, Button } from "@/components/flow/Button";
import { hrefs, rememberFamily } from "@/components/flow/session";
import type { FamilySummary } from "@/lib/types";
import { kitFor, kitSpecs } from "./kitArt";
import { loadTraditionsPage, type TraditionsShown } from "./load";
import { openAtTradition, traditionParam } from "./openAt";
import type { TraditionBook } from "./traditionBook";

// three.js needs the browser: the book and its kits load on the client only.
const Book3D = dynamic(() => import("@/components/book/Book3D").then((m) => m.Book3D), {
  ssr: false,
  loading: () => <BookLoading />,
});
const TraditionStage = dynamic(() => import("./TraditionStage").then((m) => m.TraditionStage), { ssr: false });

const enc = encodeURIComponent;

/** Where the book's links go. */
export const traditionsHrefs = {
  book: (familyId?: string | null) => (familyId ? `/traditions?family=${enc(familyId)}` : "/traditions"),
  demo: () => "/traditions?demo=1",
  add: (familyId: string) => `/traditions/new?family=${enc(familyId)}`,
};

const TREE = { href: "/", label: "Family tree" } as const;
const DEMO_TREE = { href: "/?demo=1", label: "Family tree" } as const;

/** Reload when the page is shown again (a tradition may have just been added), except the fixed demo. */
const keepShown = (s: TraditionsShown) => s.kind === "book" && s.demo === "chosen";

export function TraditionsScreen({ search }: { search: Search }) {
  const query = readBookQuery(search);
  const { family, demo } = query;
  const run = useCallback((signal: AbortSignal) => loadTraditionsPage({ family, demo }, signal), [family, demo]);
  const { value: shown, retry } = useBookLoad(`traditions|${family ?? ""}|${demo ? 1 : 0}`, run, keepShown);
  // ?tradition=<id> (as /traditions/new lands here) opens the book at that tradition.
  const at = traditionParam({ tradition: search.tradition, memory: search.memory });

  if (!shown) return <BookLoading />;
  const back = demo ? DEMO_TREE : TREE;
  switch (shown.kind) {
    case "book":
      return (
        <TraditionsBookView
          key={shown.key}
          book={shown.book}
          familyId={shown.familyId}
          families={shown.families}
          canAdd={shown.canAdd}
          initial={query.initial}
          at={at}
          still={query.still}
          debug={query.debug}
          back={shown.demo === "chosen" ? DEMO_TREE : TREE}
          note={shown.demo ? <DemoNote why={shown.demo} onRetry={retry} /> : null}
        />
      );
    case "empty":
      return (
        <BookSheet kicker={shown.familyName} title="No traditions in the book yet" back={back}>
          <p>
            The things your family does again and again, the Sunday pancakes, the trip every July, each get a page of
            their own here, with a pop-up of it.
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-3">
            {shown.canAdd ? <ButtonLink href={traditionsHrefs.add(shown.familyId)}>Add a tradition</ButtonLink> : null}
            <Link href={back.href} className="text-accent-deep">
              Back to the family tree
            </Link>
          </div>
          <OtherFamilies families={shown.families} currentId={shown.familyId} />
        </BookSheet>
      );
    case "signed-out":
      return (
        <BookSheet title="This book is kept for the family" back={back}>
          <p>Every family on Stitch is private. Sign in, and the traditions your family keeps open here as a pop-up book.</p>
          <div className="mt-6">
            <ButtonLink href={hrefs.login(here())}>Sign in to open the book</ButtonLink>
          </div>
          <DemoLink />
        </BookSheet>
      );
    case "no-family":
      return (
        <BookSheet title="No family here yet" back={back}>
          <p>Start your family, or join one with an invite link, and the traditions you keep will gather here.</p>
          <div className="mt-6">
            <ButtonLink href={hrefs.newFamily()}>Start a family</ButtonLink>
          </div>
          <DemoLink />
        </BookSheet>
      );
    case "not-found":
      return (
        <BookSheet kicker="Traditions" title={<>This book isn&rsquo;t on your shelf</>} back={back}>
          <p>It belongs to a family you are not part of, or the link is out of date. Open your own from the family tree.</p>
          <div className="mt-6">
            <ButtonLink href={back.href} variant="secondary">
              Back to the family tree
            </ButtonLink>
          </div>
          <DemoLink />
        </BookSheet>
      );
    case "error":
      return (
        <BookSheet title="The book wouldn’t open" back={back}>
          <p role="alert">{shown.detail}</p>
          <div className="mt-6">
            <Button onClick={retry}>Try again</Button>
          </div>
          <DemoLink />
        </BookSheet>
      );
  }
}

/** This page's path and query, to come back to after signing in (the demo switch left out). */
function here(): string {
  if (typeof window === "undefined") return "/traditions";
  const url = new URL(window.location.href);
  url.searchParams.delete("demo");
  return `${url.pathname}${url.search}`;
}

function DemoLink() {
  return (
    <p className="mt-5 text-[0.95rem] text-ink-faint">
      or{" "}
      <Link
        href={traditionsHrefs.demo()}
        className="font-semibold text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2"
      >
        open the demo family&rsquo;s book
      </Link>
    </p>
  );
}

function DemoNote({ why, onRetry }: { why: "chosen" | "offline"; onRetry: () => void }) {
  if (why === "offline") {
    return (
      <>
        Stitch is out of reach, so this is a demo book &middot;{" "}
        <button type="button" onClick={onRetry} className="cursor-pointer text-[#f0b49a] underline decoration-1 underline-offset-[0.2em]">
          try again
        </button>
      </>
    );
  }
  return (
    <>
      a demo family &middot;{" "}
      <Link href={hrefs.login("/traditions")} className="text-[#f0b49a] underline decoration-1 underline-offset-[0.2em]">
        sign in to see yours
      </Link>
    </>
  );
}

/** On the empty sheet: the user's other families' books. */
function OtherFamilies({ families, currentId }: { families: FamilySummary[]; currentId: string }) {
  const others = families.filter((f) => f.id !== currentId);
  if (others.length === 0) return null;
  return (
    <div className="mt-6 border-t border-dashed border-rule pt-4">
      <p className="text-[0.95rem] text-ink-faint">Your other families</p>
      <ul className="mt-1 list-none p-0">
        {others.map((f) => (
          <li key={f.id}>
            <Link
              href={traditionsHrefs.book(f.id)}
              onClick={() => rememberFamily(f.id)}
              className="inline-flex min-h-10 items-center break-words font-display text-[1.05rem] text-ink no-underline decoration-rule"
            >
              {f.name}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------------ */
/* The book                                                                 */
/* ------------------------------------------------------------------------ */

interface BookViewProps {
  book: TraditionBook;
  familyId: string | null;
  families: FamilySummary[];
  canAdd: boolean;
  initial: ReturnType<typeof readBookQuery>["initial"];
  /** ?tradition=: the tradition to open at, when the book has it. */
  at: string | null;
  still: boolean;
  debug: boolean;
  back: { href: string; label: string };
  note: ReactNode;
}

function TraditionsBookView(props: BookViewProps) {
  // The book a mount opens with is the book it keeps: a reload that prints
  // the same pages (same key) must not reprint them.
  const [book] = useState(props.book);
  const { familyId, families, canAdd, still, debug, back, note } = props;
  const spreads = book.spreads;
  // ?tradition=<id>: open at that tradition's spread, when the book has it.
  const [initial] = useState(() => openAtTradition(props.initial, spreads, props.at));

  const [view, setView] = useState<BookView>(() => ({
    opened: initial.state === "open" || (initial.state !== "closed" && initial.spread != null),
    ready: false,
    spread: Math.min(Math.max(1, Math.round(initial.spread ?? 1)), Math.max(1, spreads.length)),
  }));

  const controls = useRef<BookControls | null>(null);
  const pending = useRef<number | null>(null);
  const [drawn, setDrawn] = useState(false);
  const onControls = useCallback((c: BookControls | null) => {
    controls.current = c;
    if (!c) return;
    setDrawn(true);
    if (pending.current !== null) {
      c.goTo(pending.current);
      pending.current = null;
    }
  }, []);
  const goTo = useCallback((n: number) => {
    if (controls.current) controls.current.goTo(n);
    else pending.current = n;
  }, []);

  useEffect(() => {
    const before = document.title;
    document.title = `${book.title} · Stitch`;
    return () => {
      document.title = before;
    };
  }, [book.title]);

  // Cut every kit's paper while the book lies closed, so a page opens without a hitch.
  useEffect(() => {
    if (!drawn) return;
    let live = true;
    const specs = [...new Map(
      [...book.byId.values()].flatMap(({ tradition, variant }) => kitSpecs(kitFor(tradition.category, variant))).map((s) => [s.key, s]),
    ).values()];
    import("@/components/stage/cutout")
      .then(async ({ preloadCutouts }) => {
        if (live) await preloadCutouts(specs);
      })
      .catch((e: unknown) => console.warn("[traditions] the kits were not cut ahead", e));
    return () => {
      live = false;
    };
  }, [drawn, book]);

  const renderStage = useCallback(
    (args: StageSlotArgs) => {
      const entry = book.byId.get(args.spread.id);
      if (!entry) return null;
      return (
        <TraditionStage
          category={entry.tradition.category}
          variant={entry.variant}
          keepers={entry.keepers}
          reveal={args.reveal}
          view={args.view}
          size={[args.width, args.depth]}
        />
      );
    },
    [book],
  );

  const actions = (
    <>
      {familyId ? <FamilySwitch families={families} currentId={familyId} /> : null}
      {canAdd && familyId ? <AddLink href={traditionsHrefs.add(familyId)} /> : null}
      <ContentsTab spreads={spreads} view={view} onPick={goTo} />
    </>
  );

  return (
    <div className="relative">
      <Book3D
        spreads={spreads}
        cover={book.cover}
        initial={initial}
        renderStage={renderStage}
        still={still}
        debug={debug}
        onView={setView}
        onControls={onControls}
        back={back}
        actions={actions}
      >
        {note ? <TableNote opened={view.opened}>{note}</TableNote> : null}
      </Book3D>
      <ContentsCard spreads={spreads} view={view} onPick={goTo} />
    </div>
  );
}

/** "Add a tradition", quiet by the contents tab: a plus on a phone, the words beside it on a wider screen. */
function AddLink({ href }: { href: string }) {
  return (
    <Link
      href={href}
      aria-label="Add a tradition"
      className="pointer-events-auto flex h-11 items-center gap-1.5 rounded-full px-2 font-display text-[0.95rem] text-[#f3e6cf]/80 no-underline transition-colors hover:text-[#f3e6cf] sm:px-3"
    >
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
        <path d="M8 3v10M3 8h10" />
      </svg>
      <span aria-hidden className="hidden sm:inline">
        Add a tradition
      </span>
    </Link>
  );
}

/** For someone in more than one family: which family's book this is. Hidden for one. */
function FamilySwitch({ families, currentId }: { families: FamilySummary[]; currentId: string }) {
  const router = useRouter();
  if (families.length < 2) return null;
  return (
    <label className="pointer-events-auto hidden sm:block">
      <span className="sr-only">Family</span>
      <select
        value={currentId}
        onChange={(e) => {
          rememberFamily(e.target.value);
          router.push(traditionsHrefs.book(e.target.value));
        }}
        className="h-11 max-w-[12rem] cursor-pointer truncate rounded-full bg-transparent px-3 font-display text-[0.95rem] text-[#f3e6cf]/80 transition-colors hover:text-[#f3e6cf] [&>option]:text-ink"
      >
        {families.map((f) => (
          <option key={f.id} value={f.id}>
            {f.name}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * A quiet line on the table (as the moment books carry it): in the header's
 * middle on a wide screen; on a phone at the foot while the book is closed,
 * under the header once it is open (the dots have the foot).
 */
function TableNote({ opened, children }: { opened: boolean; children: ReactNode }) {
  return (
    <p
      className={`pointer-events-auto absolute left-1/2 z-10 w-max max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-full bg-[#140c07]/55 px-4 py-1.5 text-center font-sans text-[0.8rem] text-[#f3e6cf]/75 sm:bottom-auto sm:top-[1.6rem] ${
        opened ? "top-[3.9rem]" : "bottom-[calc(0.9rem+env(safe-area-inset-bottom))]"
      }`}
    >
      {children}
    </p>
  );
}
