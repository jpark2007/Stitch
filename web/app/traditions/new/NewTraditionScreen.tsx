"use client";

// The "Add a tradition" screen, set as /add is: the writing on the left and,
// from 1024px, the family's Book of Traditions lying open beside it at its
// next blank page, the tradition printing into it as it is written
// (TraditionDraftBook; the desk is BookDesk, shared with /add's book).
// Reads ?family=, waits for the session (signed out goes to log in and comes
// back; no family goes to start one), picks the family as /add does (the
// asked-for one if the person belongs to it, else the remembered one, else
// their first), loads its tree for "Who takes part?" and its traditions (for
// which kit the new one gets), then shows the form. Saving uploads the
// photos (each once, even across retries) and creates the tradition; the
// book beside the form shows it saved a moment, then the page goes to the
// book, opened at the new tradition's spread.
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { LoadingSheet } from "@/app/add/parts";
import { BookDesk, usePrefersReducedMotion, useWideDesk } from "@/components/flow/BookDesk";
import { Button, ButtonLink, FOCUS_RING } from "@/components/flow/Button";
import { createWatch } from "@/components/flow/crafting";
import { FlowPage } from "@/components/flow/FlowPage";
import { FormError } from "@/components/flow/FormError";
import { PaperCard } from "@/components/flow/PaperCard";
import { SessionGate, type SessionValue } from "@/components/flow/SessionGate";
import { AddTraditionForm } from "@/components/flow/AddTraditionForm";
import { familyChoice, type FamilyChoice } from "@/components/flow/addPage";
import { hrefs, recallFamily, rememberFamily } from "@/components/flow/session";
import { toCreateTraditionRequest, type TraditionDraft } from "@/components/flow/traditionForm";
import { traditionHref } from "@/components/traditions/openAt";
import { traditionsCover, traditionsTitle } from "@/components/traditions/traditionBook";
import { isApiError } from "@/lib/api";
import type { Person, Tradition } from "@/lib/types";

import { TraditionDraftBook, type TraditionInput } from "./TraditionDraftBook";

const TITLE = "Add a tradition";
// No lede, as on /add: the form's placeholders carry the invitation. Only
// someone in two or more families gets a line (which family, and a switch).
const LOAD_ERROR = "We couldn't open the page for adding a tradition. Check your connection and try again.";
/** The aside the book lies in, for assistive tech. */
const BOOK_LABEL = "The book of traditions";
/** How long the book shows the tradition saved before the page goes to the book, ms. */
const SAVED_BEAT_MS = 950;

type PickablePerson = { id: string; name: string; born?: number | null; color?: string | null };

/** The quiet link back to the book, for the family on this page ("The Rivera Book of Traditions"). */
function backTo(familyId: string | null, familyName: string | null) {
  return { href: hrefs.traditions({ familyId }), label: familyName ? traditionsTitle(familyName) : "The Book of Traditions" };
}

/** The path and query of this page, for coming back after logging in. */
function currentPath(): string {
  return `${window.location.pathname}${window.location.search}`;
}

/** The page shell with a quiet loading sheet, on the desk, so nothing jumps when the form arrives. */
export function NewTraditionLoading(props: { familyId: string | null }) {
  return (
    <BookDesk mode="write" book={null} label={BOOK_LABEL} loading="opening the book…">
      <LoadingPage back={backTo(props.familyId, null)} />
    </BookDesk>
  );
}

function LoadingPage(props: { back: { href: string; label: string }; lede?: ReactNode }) {
  return (
    <FlowPage title={TITLE} lede={props.lede} back={props.back} width="wide" heading="quiet">
      <LoadingSheet label="Opening your family’s pages…" />
    </FlowPage>
  );
}

export function NewTraditionScreen() {
  const params = useSearchParams();
  const familyId = params.get("family") || null;
  return (
    <SessionGate requireFamily width="wide" loading={<NewTraditionLoading familyId={familyId} />}>
      {(session) => <NewTraditionFlow session={session} familyId={familyId} />}
    </SessionGate>
  );
}

type Status =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | {
      kind: "ready";
      /** For "Who takes part?". */
      people: PickablePerson[];
      /** The same people as the book draws them. */
      persons: Person[];
      /** The family's traditions already in the book. */
      traditions: Tradition[];
    };

function NewTraditionFlow(props: { session: SessionValue; familyId: string | null }) {
  const { session, familyId } = props;
  const { me, api } = session;
  const router = useRouter();

  // Only runs in the browser (SessionGate renders this after asking the
  // server who is signed in), so reading storage here is safe.
  const wanted = familyId ?? recallFamily();
  const choice = useMemo(() => familyChoice(me.families, wanted), [me.families, wanted]);
  const family = choice.current;
  const fid = family?.id ?? null;

  useEffect(() => {
    if (fid) rememberFamily(fid);
  }, [fid]);

  // The family's people, for "Who takes part?", and its traditions, for the
  // book (without them the page still works: the new one gets its kind's
  // first kit). Keyed by family and try, so a switch or a retry shows the
  // loading sheet again, never stale people.
  const [loadTry, setLoadTry] = useState(0);
  const loadKey = fid ? `${fid}#${loadTry}` : null;
  const [loaded, setLoaded] = useState<{ key: string; status: Status } | null>(null);
  useEffect(() => {
    if (!fid || !loadKey) return;
    let live = true;
    const traditions = api.listTraditions(fid).then(
      (r) => (Array.isArray(r?.traditions) ? r.traditions : []),
      () => [] as Tradition[],
    );
    Promise.all([api.getTree(fid), traditions]).then(
      ([tree, list]) => {
        if (!live) return;
        const persons = Array.isArray(tree.people) ? tree.people : [];
        const people = persons.map((p) => ({
          id: p.id,
          name: p.name,
          born: p.born ?? null,
          color: p.character?.color ?? null,
        }));
        setLoaded({ key: loadKey, status: { kind: "ready", people, persons, traditions: list } });
      },
      (e: unknown) => {
        if (!live) return;
        if (isApiError(e) && e.status === 401) {
          router.replace(hrefs.login(currentPath()));
          return;
        }
        const message = isApiError(e) && e.status !== 0 && e.detail ? e.detail : LOAD_ERROR;
        setLoaded({ key: loadKey, status: { kind: "error", message } });
      },
    );
    return () => {
      live = false;
    };
  }, [api, fid, loadKey, router]);
  const status: Status = loadKey && loaded?.key === loadKey ? loaded.status : { kind: "loading" };

  // Photos already uploaded by an earlier try, so saving again after an
  // error does not upload them twice.
  const uploaded = useRef(new Map<File, string>());
  // Set once the tradition is saved: the form on this page is spent.
  const spent = useRef(false);

  // The book beside the form reads what the form holds, and the tradition
  // once saved, itself: a keystroke re-renders the book alone, not the page.
  const [input] = useState(() => createWatch<TraditionInput | null>(null));
  const [saved] = useState(() => createWatch<Tradition | null>(null));
  // The form tells it once as it mounts (a switch of family remounts it).
  const onPreview = useCallback((draft: TraditionDraft, photos: File[]) => input.set({ draft, photos }), [input]);

  const wide = useWideDesk();
  const reduced = usePrefersReducedMotion();
  const onSubmit = useCallback(
    async (draft: TraditionDraft, photos: File[]) => {
      if (!fid) return;
      const photoIds: string[] = [];
      for (const file of photos) {
        let id = uploaded.current.get(file);
        if (!id) {
          id = (await api.uploadMedia(fid, file)).id;
          uploaded.current.set(file, id);
        }
        photoIds.push(id);
      }
      const created = await api.createTradition(fid, toCreateTraditionRequest(draft, photoIds));
      spent.current = true;
      // Beside the form, the book shows the page as saved a moment first
      // (not on a phone, which has no book, nor under reduced motion).
      if (wide && !reduced) {
        saved.set(created);
        await new Promise((resolve) => window.setTimeout(resolve, SAVED_BEAT_MS));
      }
      // Replaced, not pushed: Back from the book goes to wherever the person
      // was before the form, not to a form already sent.
      router.replace(traditionHref(fid, created.id));
    },
    [api, fid, router, wide, reduced, saved],
  );

  // A form sent is spent. Should this page be shown again all the same (the
  // router keeps a page alive while it is away; the browser's back-forward
  // cache keeps a whole one), it comes back as a fresh form with a blank page
  // in the book, not "Saving…" and "It's in the book." for ever.
  const [round, setRound] = useState(0);
  const fresh = useCallback(() => {
    if (!spent.current) return;
    spent.current = false;
    uploaded.current.clear();
    saved.set(null);
    input.set(null);
    setRound((n) => n + 1);
  }, [saved, input]);
  useEffect(() => {
    // Shown again from the back-forward cache.
    const onShow = (e: PageTransitionEvent) => {
      if (e.persisted) fresh();
    };
    window.addEventListener("pageshow", onShow);
    // Put away (the page left, kept alive or not): ready for its return.
    return () => {
      window.removeEventListener("pageshow", onShow);
      fresh();
    };
  }, [fresh]);

  const familyName = family?.name ?? null;
  const owner = useMemo(
    () => ({ title: traditionsTitle(familyName), href: hrefs.traditions({ familyId: fid }) }),
    [familyName, fid],
  );
  const existing = status.kind === "ready" ? status.traditions : NO_TRADITIONS;
  const cover = useMemo(() => traditionsCover(familyName, existing), [familyName, existing]);

  if (!family || !fid) {
    // SessionGate's requireFamily sends people without a family away; this
    // is only reached if that changes under us.
    return (
      <FlowPage title={TITLE} width="wide" heading="quiet">
        <PaperCard className="flex flex-col items-start gap-4">
          <p className="text-[1.0625rem] leading-relaxed text-ink">
            Traditions live in a family. Start one first, then come back to add yours.
          </p>
          <ButtonLink href={hrefs.newFamily()}>Start a family</ButtonLink>
        </PaperCard>
      </FlowPage>
    );
  }

  const lede = choice.show ? <FamilyLede choice={choice} /> : undefined;
  const back = backTo(fid, family.name);

  if (status.kind === "loading") {
    return (
      <BookDesk mode="write" book={null} label={BOOK_LABEL} loading="opening the book…">
        <LoadingPage back={back} lede={lede} />
      </BookDesk>
    );
  }

  if (status.kind === "error") {
    return (
      <BookDesk mode="write" book={null} label={BOOK_LABEL} loading={null}>
        <FlowPage title={TITLE} lede={lede} back={back} width="wide" heading="quiet">
          <PaperCard className="flex flex-col gap-5">
            <FormError message={status.message} />
            <div>
              <Button onClick={() => setLoadTry((n) => n + 1)}>Try again</Button>
            </div>
          </PaperCard>
        </FlowPage>
      </BookDesk>
    );
  }

  const book = (
    <TraditionDraftBook
      key={`${fid}#${round}`}
      owner={owner}
      cover={cover}
      people={status.persons}
      existing={status.traditions}
      selfPersonId={family.personId ?? null}
      input={input}
      saved={saved}
    />
  );
  return (
    <BookDesk mode="write" book={book} label={BOOK_LABEL}>
      <FlowPage title={TITLE} lede={lede} back={back} width="wide" heading="quiet">
        <AddTraditionForm
          key={`${fid}#${round}`}
          people={status.people}
          selfPersonId={family.personId ?? null}
          onSubmit={onSubmit}
          onPreview={onPreview}
        />
      </FlowPage>
    </BookDesk>
  );
}

const NO_TRADITIONS: Tradition[] = [];

const SWITCH_LINK = `rounded-paper font-medium text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2 ${FOCUS_RING}`;

/** "A", "A or B", "A, B or C": what goes before link `i` of `n`. */
function joiner(i: number, n: number): string {
  if (i === 0) return "";
  return i === n - 1 ? " or " : ", ";
}

/**
 * One quiet line for someone in two or more families: which family the
 * tradition goes into, and a link to each other family's form (the same line
 * /add shows).
 */
function FamilyLede(props: { choice: FamilyChoice }) {
  const { current, others, show } = props.choice;
  if (!show || !current) return null;
  return (
    <>
      <div className="flex min-w-0 flex-col gap-y-1 text-[0.9375rem] leading-snug text-ink-soft sm:flex-row sm:flex-wrap sm:items-baseline sm:gap-x-2">
        <p className="min-w-0 break-words">
          Adding to <span className="font-semibold text-ink">{current.name}</span>
        </p>
        {others.length ? (
          <>
            <span aria-hidden="true" className="hidden text-ink-soft/50 sm:inline">
              &middot;
            </span>
            <nav aria-label="Add to another family" className="min-w-0 break-words">
              Switch to{" "}
              {others.map((f, i) => (
                <Fragment key={f.id}>
                  {joiner(i, others.length)}
                  <Link href={hrefs.newTradition({ familyId: f.id })} className={SWITCH_LINK}>
                    {f.name}
                  </Link>
                </Fragment>
              ))}
            </nav>
          </>
        ) : null}
      </div>
    </>
  );
}
