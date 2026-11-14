"use client";

// The home screen around the family tree: the signed-in family's real tree,
// or a warm way in for visitors, or the local demo family when the API is out
// of reach. Everything is fetched in the browser (the session cookie belongs
// to the API), so the prerendered page is the paper placeholder below.
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Button, ButtonLink } from "@/components/flow/Button";
import { PaperCard } from "@/components/flow/PaperCard";
import { hrefs, recallFamily, rememberFamily } from "@/components/flow/session";
import { api, isMockMode } from "@/lib/api";
import type { FamilySummary } from "@/lib/types";
import { demoFamily, demoPeople } from "./demoFamily";
import { FamilyTree } from "./FamilyTree";
import { loadHome, personHref, threadsHref, type HomeView } from "./homeData";
import { Puppet, PuppetDefs } from "./Puppet";
import { toTreePeople } from "./treeData";

/** Fit the tree to the viewport's height on big screens. */
const TREE_MAX_HEIGHT = "calc(100dvh - 17rem)";
/** Below this scale (px per layout unit) names get too small; wide trees scroll instead. */
const TREE_MIN_SCALE = 0.42;
/**
 * Above this scale a small family's puppets would fill the page (a new
 * family starts with one person); it stays near the demo family's size.
 */
const TREE_MAX_SCALE = 1.05;

// No ?next=: the sign-in pages already come home, or go on to start a family
// for someone who has none yet (a next of "/" would skip that step).
const LOGIN_HREF = "/login";
const SIGNUP_HREF = "/signup";
const DEMO_HREF = "/?demo=1";
// Links out of the demo tree say so, so the next screen shows the demo too.
const DEMO_SCOPE = { demo: true } as const;
const demoPersonHref = (id: string) => personHref(id, null, DEMO_SCOPE);

export function TreeHome() {
  const wantsDemo = useSearchParams().has("demo");
  // Inlined at build time, so the server and browser renders agree.
  const mock = isMockMode();
  const live = !wantsDemo && !mock;

  const [view, setView] = useState<HomeView>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [chosen, setChosen] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);

  // With cacheComponents this page can come back from a hidden Activity (say,
  // right after signing in). Before paint, swap a stale "please sign in" or
  // error sheet for the placeholder while we ask again; a tree stays up.
  useLayoutEffect(() => {
    if (!live) return;
    // Syncing with the server's session, once per arrival.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setView((v) => (v.kind === "tree" || v.kind === "loading" ? v : { kind: "loading" }));
  }, [live]);

  useEffect(() => {
    if (!live) return;
    let current = true;
    loadOnce(`${attempt}:${chosen ?? ""}`, () => loadHome(api, chosen ?? recallFamily()))
      .then((next) => {
        if (!current) return;
        if (next.kind === "tree") rememberFamily(next.familyId);
        setView(next);
        setSwitching(false);
      });
    return () => {
      current = false;
    };
  }, [live, attempt, chosen]);

  const retry = useCallback(() => {
    setView({ kind: "loading" });
    setAttempt((a) => a + 1);
  }, []);

  const switchTo = useCallback((fid: string) => {
    rememberFamily(fid);
    setSwitching(true);
    setChosen(fid);
  }, []);

  if (!live) return <DemoHome reason={mock ? "mock" : "chosen"} />;

  switch (view.kind) {
    case "loading":
      return <TreeHomeLoading />;
    case "offline":
      return <DemoHome reason="offline" onRetry={retry} />;
    case "signed-out":
      return <SignedOutHome />;
    case "no-family":
      return <NoFamilyHome />;
    case "error":
      return <ErrorHome message={view.message} onRetry={retry} />;
    case "tree":
      return <FamilyHome view={view} switching={switching} onSwitch={switchTo} />;
  }
}

/**
 * One request per load: strict mode's second effect run (and a remount while
 * the first is still out) joins the request in flight instead of sending
 * another. Once it settles the next arrival asks afresh.
 */
let inflight: { key: string; promise: Promise<HomeView> } | null = null;

function loadOnce(key: string, load: () => Promise<HomeView>): Promise<HomeView> {
  if (inflight?.key === key) return inflight.promise;
  const promise = load()
    .catch((): HomeView => ({ kind: "error", message: "" }))
    .finally(() => {
      if (inflight?.promise === promise) inflight = null;
    });
  inflight = { key, promise };
  return promise;
}

/* ------------------------------------------------------------------ views */

function FamilyHome(props: {
  view: Extract<HomeView, { kind: "tree" }>;
  switching: boolean;
  onSwitch: (fid: string) => void;
}) {
  const { view, switching, onSwitch } = props;
  const { familyId, familyName, myPersonId } = view;
  const people = useMemo(() => toTreePeople(view.people), [view.people]);
  const hrefFor = useCallback((id: string) => personHref(id, familyId), [familyId]);
  const treeRef = useRef<HTMLDivElement>(null);

  useDocumentTitle(`${familyName} · Stitch`);

  // A tree wider than the screen opens scrolled to the viewer (or its middle).
  useEffect(() => {
    const scroller = treeRef.current?.querySelector<HTMLElement>(".ft-scroll");
    if (!scroller || scroller.scrollWidth <= scroller.clientWidth + 1) return;
    const box = scroller.getBoundingClientRect();
    const mine = myPersonId
      ? scroller.querySelector<HTMLElement>(`.ft-node[data-id="${CSS.escape(myPersonId)}"]`)
      : null;
    const centre = mine
      ? mine.getBoundingClientRect().left - box.left + scroller.scrollLeft + mine.offsetWidth / 2
      : scroller.scrollWidth / 2;
    scroller.scrollLeft = Math.max(0, centre - scroller.clientWidth / 2);
  }, [people, myPersonId]);

  return (
    <HomeShell
      title={familyName}
      caption={people.length ? "pick anyone to open their book" : undefined}
      threads={threadsHref(familyId)}
      add={hrefs.add({ familyId })}
      switcher={
        <FamilySwitch families={view.families} currentId={familyId} busy={switching} onChange={onSwitch} />
      }
    >
      {people.length ? (
        <div ref={treeRef} className={`ft-home-tree ${switching ? "is-switching" : ""}`} aria-busy={switching || undefined}>
          <FamilyTree people={people} hrefFor={hrefFor} maxHeight={TREE_MAX_HEIGHT} minScale={TREE_MIN_SCALE} maxScale={TREE_MAX_SCALE} />
        </div>
      ) : (
        <PromptCard title="No one is in this tree yet">
          <p className="text-ink-soft">Add the first relatives, and their paper puppets will stand here.</p>
          <div className="mt-6">
            <ButtonLink href={`/families/${encodeURIComponent(familyId)}/people/new`}>Add someone</ButtonLink>
          </div>
        </PromptCard>
      )}
    </HomeShell>
  );
}

function DemoHome(props: { reason: "chosen" | "offline" | "mock"; onRetry?: () => void }) {
  const { reason, onRetry } = props;
  let note: ReactNode;
  if (reason === "offline") {
    note = (
      <>
        We can&rsquo;t reach Stitch right now, so this is a demo family.{" "}
        <button type="button" onClick={onRetry} className="ft-note-link">
          Try again
        </button>
      </>
    );
  } else if (reason === "chosen") {
    note = (
      <>
        This is a demo family.{" "}
        <Link href="/" className="ft-note-link">
          See your own
        </Link>
      </>
    );
  } else {
    note = <>This is a demo family.</>;
  }
  // Chosen or mock: the demo all the way through, so threads opens the demo
  // family too. Offline: plain /threads, which falls back by itself and shows
  // the real family once Stitch answers again. Demo people are always marked.
  const threads = reason === "offline" ? threadsHref(null) : threadsHref(null, DEMO_SCOPE);
  return (
    <HomeShell title={demoFamily.name} caption="pick anyone to open their book" threads={threads} note={note}>
      <FamilyTree
        people={demoPeople}
        hrefFor={demoPersonHref}
        maxHeight={TREE_MAX_HEIGHT}
        minScale={TREE_MIN_SCALE}
        maxScale={TREE_MAX_SCALE}
      />
    </HomeShell>
  );
}

function SignedOutHome() {
  return (
    <HomeShell threads={threadsHref(null)}>
      <PromptCard title="Your family’s storybook" puppets>
        <p className="text-ink-soft">
          Sign in to see your family&rsquo;s tree and open anyone&rsquo;s book of moments.
        </p>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-x-2 gap-y-3">
          <ButtonLink href={LOGIN_HREF}>Sign in</ButtonLink>
          <ButtonLink href={SIGNUP_HREF} variant="quiet">
            New here? Make an account
          </ButtonLink>
        </div>
        <DemoLink />
      </PromptCard>
    </HomeShell>
  );
}

function NoFamilyHome() {
  return (
    <HomeShell threads={threadsHref(null)}>
      <PromptCard title="You’re not in a family yet" puppets>
        <p className="text-ink-soft">
          Start your family&rsquo;s tree, or open the invite link a relative sent you to join theirs.
        </p>
        <div className="mt-6">
          <ButtonLink href="/families/new">Start a family</ButtonLink>
        </div>
        <DemoLink />
      </PromptCard>
    </HomeShell>
  );
}

function ErrorHome(props: { message: string; onRetry: () => void }) {
  return (
    <HomeShell threads={threadsHref(null)}>
      <PromptCard title="We couldn’t open your family’s tree">
        <p className="text-ink-soft" role="alert">
          {props.message || "Something went wrong on our side. Please try again."}
        </p>
        <div className="mt-6">
          <Button onClick={props.onRetry}>Try again</Button>
        </div>
        <DemoLink />
      </PromptCard>
    </HomeShell>
  );
}

/** The prerendered page and the wait for the API: a blank paper tree that breathes. */
export function TreeHomeLoading() {
  return (
    <HomeShell
      threads={threadsHref(null)}
      title={<span className="ft-bar ft-bar-title" />}
      caption={<span className="ft-bar ft-bar-caption" />}
      busy
    >
      <FamilyTree people={demoPeople} maxHeight={TREE_MAX_HEIGHT} maxScale={TREE_MAX_SCALE} ghost />
    </HomeShell>
  );
}

/* ------------------------------------------------------------------ parts */

function HomeShell(props: {
  title?: ReactNode;
  caption?: ReactNode;
  threads: string;
  switcher?: ReactNode;
  /** "Add a memory" link: only for a signed-in viewer on a real family. */
  add?: string;
  note?: ReactNode;
  busy?: boolean;
  children: ReactNode;
}) {
  const { title, caption, threads, add, switcher, note, busy, children } = props;
  const placeholder = busy === true;
  return (
    <main className="mx-auto flex min-h-dvh max-w-6xl flex-col px-[var(--gutter)] pb-10 pt-8 sm:pt-12">
      <header className="relative flex flex-col items-center text-center">
        <nav className="flex w-full items-center justify-between gap-4">
          <span className="kicker">Stitch</span>
          <div className="flex min-w-0 items-center gap-x-5">
            {switcher}
            <Link
              href={threads}
              className="group inline-flex shrink-0 items-center gap-2 text-sm text-ink-faint no-underline transition-colors hover:text-ink"
            >
              <svg width="26" height="10" viewBox="0 0 26 10" aria-hidden className="shrink-0">
                <path
                  d="M1 6 Q 7 1 13 5 T 25 4"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeDasharray="3.5 2.5"
                  strokeLinecap="round"
                />
              </svg>
              Family threads
            </Link>
            {add && (
              <Link
                href={add}
                aria-label="Add a memory"
                className="-my-2 inline-flex shrink-0 items-center gap-1.5 py-2 text-sm text-ink-faint no-underline transition-colors hover:text-ink"
              >
                <span aria-hidden className="text-base leading-none">
                  +
                </span>
                <span aria-hidden>
                  Add<span className="hidden sm:inline"> a memory</span>
                </span>
              </Link>
            )}
          </div>
        </nav>
        {title !== undefined &&
          (placeholder ? (
            <div aria-hidden className="ft-title mt-8 sm:mt-6">
              {title}
            </div>
          ) : (
            <h1 className="ft-title mt-8 sm:mt-6">{typeof title === "string" ? keepDashedTogether(title) : title}</h1>
          ))}
        {caption !== undefined &&
          (placeholder ? (
            <div aria-hidden className="caption mt-2">
              {caption}
            </div>
          ) : (
            <p className="caption mt-2">{caption}</p>
          ))}
      </header>

      <section
        aria-label="Family tree"
        aria-busy={placeholder || undefined}
        className="mt-6 flex flex-1 items-center justify-center pb-[6vh] sm:mt-10 sm:pb-0"
      >
        {placeholder && (
          <span role="status" className="sr-only">
            Opening your family&rsquo;s tree…
          </span>
        )}
        <div className="w-full max-w-[62rem]">{children}</div>
      </section>

      {note && <p className="ft-note">{note}</p>}
    </main>
  );
}

/** A centred sheet of paper for every "not a tree yet" state. */
function PromptCard(props: { title: string; puppets?: boolean; children: ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-md text-center">
      {props.puppets && <PromptPuppets />}
      <PaperCard className="flex flex-col items-center">
        <h1 className="font-display text-[clamp(1.6rem,3.2vw,2.1rem)] text-ink">{props.title}</h1>
        <div className="mt-3 flex w-full flex-col items-center">{props.children}</div>
      </PaperCard>
    </div>
  );
}

/** Three demo puppets standing on the top edge of the card. */
function PromptPuppets() {
  const cast = ["rosa", "david", "sofia"]
    .map((id) => demoPeople.find((p) => p.id === id))
    .filter((p): p is (typeof demoPeople)[number] => p !== undefined);
  return (
    <div className="ft-prompt-cast" aria-hidden>
      <PuppetDefs />
      {cast.map((p) => (
        <Puppet
          key={p.id}
          id={`prompt-${p.id}`}
          color={p.character.color}
          item={p.character.item}
          build={p.character.build}
          className="ft-prompt-puppet"
        />
      ))}
    </div>
  );
}

function DemoLink() {
  return (
    <>
      <hr className="stitch-rule mt-7 w-full" />
      <Link href={DEMO_HREF} className="caption mt-4 inline-block text-accent-deep">
        or explore the demo family &rarr;
      </Link>
    </>
  );
}

/** A quiet picker for people in more than one family. Hidden for one family. */
function FamilySwitch(props: {
  families: FamilySummary[];
  currentId: string;
  busy: boolean;
  onChange: (fid: string) => void;
}) {
  const { families, currentId, busy, onChange } = props;
  if (families.length < 2) return null;
  return (
    <label className="ft-switch">
      <span className="sr-only">Family</span>
      <select value={currentId} disabled={busy} onChange={(e) => onChange(e.target.value)}>
        {families.map((f) => (
          <option key={f.id} value={f.id}>
            {f.name}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Name the tab after the family while this page is up. */
function useDocumentTitle(title: string) {
  useEffect(() => {
    const before = document.title;
    document.title = title;
    return () => {
      document.title = before;
    };
  }, [title]);
}

/** Word joiners around dashes, so "Alvarez–Moore" never breaks at the dash. */
function keepDashedTogether(text: string): string {
  return text.replace(/\s*([–-])\s*/g, "⁠$1⁠");
}
