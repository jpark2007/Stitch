"use client";

// The add-a-memory screen. Reads ?family, ?memoryId, ?run and ?retry, waits
// for the session, picks the family, then shows either the form (AddForm) or a
// run under way (AddRun). Sending the form swaps it for the run at once and
// puts the run in the URL, so a reload comes back to the same run. A run that
// fails with its own error offers "Tell it again": ?retry=<run> brings the
// form back filled in with what that run was sent. ?added=<person> comes back
// from the add-relative page: the form ticks that person, then drops it.
// ?memoryId= without ?family= for someone in two or more families: the page
// first finds which family holds the moment (the remembered one first) and
// opens its second telling there.
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { loadMomentPage } from "@/app/memories/[id]/moment";
import { BookDesk, useWideDesk } from "@/components/flow/BookDesk";
import { ButtonLink } from "@/components/flow/Button";
import { CraftingBook, EMPTY_PAGE, type CraftInput, type DraftPage } from "@/components/flow/CraftingBook";
import {
  bookOf,
  castKey,
  castOf,
  keepCast,
  keepList,
  createWatch,
  pageTitle,
  pageYear,
  readCast,
  type CastMember,
} from "@/components/flow/crafting";
import { FlowPage } from "@/components/flow/FlowPage";
import { PaperCard } from "@/components/flow/PaperCard";
import { SessionGate, type SessionValue } from "@/components/flow/SessionGate";
import {
  addedFromQuery,
  demoDraftCarry,
  dropDemoDraft,
  familyChoice,
  localStore,
  findMomentFamily,
  loadSentDraft,
  loadTelling,
  questionIdFromQuery,
  readAddQuery,
  retryHref,
  runHref,
  saveCarry,
} from "@/components/flow/addPage";
import type { MemoryRunTelling } from "@/components/flow/MemoryRun";
import { hrefs, recallFamily, rememberFamily } from "@/components/flow/session";
import { isApiError, isMockMode } from "@/lib/api";

import { AddForm, AddLoadFailed, LOAD_ERROR, messageOf, type FormPreview, type StartedRun } from "./AddForm";
import { AddRun } from "./AddRun";
import { useCallRun } from "./callRun";
import {
  ADD_BACK,
  currentPath,
  FamilyLine,
  LoadingSheet,
  sessionStore,
  type MomentHome,
} from "./parts";

const TITLE = "Add a memory";
/** The aside the teller's book lies in, for assistive tech. */
const BOOK_LABEL = "Your book";

/**
 * The page shell with a quiet loading sheet: the prerendered HTML and the
 * session wait. It has the form's back link, title and lede (none for a run,
 * whose page has none), so nothing jumps when the page arrives.
 */
export function AddLoading(props: { kind?: "new" | "second" | "run" }) {
  const { kind = "new" } = props;
  return (
    <BookDesk mode={kind === "run" ? "run" : "write"} book={null} label={BOOK_LABEL}>
      <AddLoadingPage kind={kind} />
    </BookDesk>
  );
}

/** AddLoading's page alone, for a screen already on its desk. */
function AddLoadingPage(props: { kind: "new" | "second" | "run" }) {
  const { kind } = props;
  const title = kind === "second" ? "Add your telling" : kind === "run" ? "Your memory" : TITLE;
  // No lede: the form has none either (the story's placeholder invites).
  return (
    <FlowPage title={title} back={ADD_BACK} width="wide">
      <LoadingSheet label="Opening your family’s pages…" />
    </FlowPage>
  );
}

/**
 * The story page of the teller's book, from the form as it is now. The
 * cast, people and photos come back as the same arrays as `prev` when they
 * did not change, so a keystroke changes only the text and the book does not
 * redraw for it.
 */
function pageFromForm(p: FormPreview, prev: DraftPage): DraftPage {
  return {
    title: pageTitle({ question: p.draft.question, secondTellingOf: p.secondTellingOf }),
    year: pageYear(p.draft.year),
    text: p.draft.text,
    cast: keepCast(prev.cast, castOf(p.people, p.selfPersonId, p.draft.participantIds)),
    participantIds: keepList(prev.participantIds, p.draft.participantIds.filter((id) => id !== p.selfPersonId)),
    photos: keepList(prev.photos, p.photos),
  };
}

/** The story page of a run reattached after a reload: what was sent, and the cast kept for it. */
function pageFromTelling(telling: MemoryRunTelling | null, cast: CastMember[]): DraftPage {
  if (!telling) return { ...EMPTY_PAGE, title: "Your memory", cast };
  return {
    title: pageTitle({ question: telling.question, secondTellingOf: telling.secondTellingOf }),
    year: pageYear(telling.year),
    text: telling.text,
    cast,
    participantIds: cast.slice(1).map((c) => c.id),
    photos: [],
  };
}

function readStoredCast(runId: string): CastMember[] {
  try {
    return readCast(sessionStore()?.getItem(castKey(runId)) ?? null);
  } catch {
    return [];
  }
}

function storeCast(runId: string, cast: readonly CastMember[]): void {
  try {
    sessionStore()?.setItem(castKey(runId), JSON.stringify(cast));
  } catch {
    // Not kept: a reload stands the run up without its puppets.
  }
}

export function AddScreen() {
  const params = useSearchParams();
  const { familyId, memoryId, runId, retryOf } = readAddQuery(params);
  const added = addedFromQuery(params);
  const questionId = questionIdFromQuery(params);
  const loadingKind = runId ? "run" : memoryId ? "second" : "new";
  // ?demoDraft=1 (the /demo control page): its prepared telling becomes the form's carry, then its key goes.
  const [demo] = useState(() => {
    const carry = demoDraftCarry(params, localStore(), familyId);
    if (carry) saveCarry(sessionStore(), carry);
    return carry !== null;
  });
  useEffect(() => {
    if (demo) dropDemoDraft(localStore());
  }, [demo]);
  return (
    <SessionGate requireFamily loading={<AddLoading kind={loadingKind} />}>
      {(session) => (
        <AddFlow
          session={session}
          familyId={familyId}
          memoryId={memoryId}
          runId={runId}
          retryOf={retryOf}
          added={added}
          questionId={questionId}
        />
      )}
    </SessionGate>
  );
}

interface ShownRun {
  runId: string;
  /** The slip of what was sent; null after a reload that lost it. */
  telling: MemoryRunTelling | null;
  /** Started on this page just now (not reattached from the URL). */
  fresh: boolean;
}

function reattach(runId: string): ShownRun {
  return { runId, telling: loadTelling(sessionStore(), runId), fresh: false };
}

/** Where the lookup for `?memoryId=`'s family ended: a family, none, or a failed load. */
type MomentLookup = { kind: "found"; familyId: string } | { kind: "none" } | { kind: "error"; message: string };

function AddFlow(props: {
  session: SessionValue;
  familyId: string | null;
  memoryId: string | null;
  runId: string | null;
  retryOf: string | null;
  /** `?added=`: someone just added on the add-relative page (AddForm ticks them). */
  added: string | null;
  /** `?questionId=`: the family question picked before a reload (AddForm picks it again). */
  questionId: string | null;
}) {
  const { session, familyId, memoryId, runId, retryOf, added, questionId } = props;
  const { me, api } = session;
  const router = useRouter();

  // ?memoryId= with no ?family=, for someone in two or more families: ask each
  // family for the moment, the remembered one first. A 404 moves on; the first
  // that has it is where the second telling opens. Any other failure is the
  // page's load error, with a retry.
  const lookFor = memoryId && !familyId && !runId && me.families.length >= 2 ? memoryId : null;
  const [lookupTry, setLookupTry] = useState(0);
  const lookupKey = lookFor ? `${lookFor}#${lookupTry}` : null;
  const [lookup, setLookup] = useState<{ key: string; result: MomentLookup } | null>(null);
  useEffect(() => {
    if (!lookFor || !lookupKey) return;
    let live = true;
    const order = familyChoice(me.families, recallFamily());
    const ids = (order.current ? [order.current, ...order.others] : []).map((f) => f.id);
    findMomentFamily(ids, (fid) => api.getMemory(fid, lookFor)).then(
      (found) => {
        if (!live) return;
        const result: MomentLookup = found ? { kind: "found", familyId: found.familyId } : { kind: "none" };
        setLookup({ key: lookupKey, result });
      },
      (e: unknown) => {
        if (!live) return;
        if (isApiError(e) && e.status === 401) {
          router.replace(hrefs.login(currentPath()));
          return;
        }
        setLookup({ key: lookupKey, result: { kind: "error", message: messageOf(e, LOAD_ERROR) } });
      },
    );
    return () => {
      live = false;
    };
  }, [api, me.families, lookFor, lookupKey, router]);
  const looked = lookupKey && lookup?.key === lookupKey ? lookup.result : null;
  const foundFid = looked?.kind === "found" ? looked.familyId : null;

  // Only runs in the browser (SessionGate renders this after asking the
  // server who is signed in), so reading storage here is safe. Someone in two
  // or more families sees which one this goes into, with links to the others
  // (each to that family's plain form: no run, and no moment except in the
  // family found holding it).
  const wanted = familyId ?? foundFid ?? recallFamily();
  const choice = useMemo(() => familyChoice(me.families, wanted), [me.families, wanted]);
  const family = choice.current;
  const momentHome = useMemo<MomentHome | null>(
    () => (memoryId && foundFid ? { memoryId, familyId: foundFid } : null),
    [memoryId, foundFid],
  );
  const fid = family?.id ?? null;

  useEffect(() => {
    if (fid) rememberFamily(fid);
  }, [fid]);

  // The run on screen follows ?run=. Sending the form shows its run straight
  // away, before the URL catches up; leaving ?run= (via "Add another memory")
  // goes back to the form.
  const [seenRunId, setSeenRunId] = useState(runId);
  const [run, setRun] = useState<ShownRun | null>(() => (runId ? reattach(runId) : null));
  if (seenRunId !== runId) {
    setSeenRunId(runId);
    if (runId === null) setRun(null);
    else if (run?.runId !== runId) setRun(reattach(runId));
  }

  // The photo files sent with each run on this page, so "Tell it again" can
  // put them back. Files cannot go in storage: after a reload they are gone.
  const photosRef = useRef(new Map<string, File[]>());

  // The teller's book beside the page: its story page follows the form as it
  // is typed, then shows what was sent while the run makes the pop-up. Both
  // are small values the book reads itself, so the page does not re-render
  // for a keystroke or a run event.
  const [pageWatch] = useState(() => createWatch<DraftPage>(EMPTY_PAGE));
  const onPreview = useCallback((p: FormPreview) => pageWatch.set(pageFromForm(p, pageWatch.get())), [pageWatch]);
  const shownRunId = run?.runId ?? null;
  const craftWatch = useMemo(() => (shownRunId ? createWatch<CraftInput | null>(null) : null), [shownRunId]);
  const reattached = run !== null && !run.fresh;
  const reattachedTelling = run?.telling ?? null;
  useEffect(() => {
    // A run come back to after a reload: the page is what was sent, with the cast kept for it.
    if (shownRunId && reattached) pageWatch.set(pageFromTelling(reattachedTelling, readStoredCast(shownRunId)));
  }, [shownRunId, reattached, reattachedTelling, pageWatch]);

  const onStarted = useCallback(
    (started: StartedRun) => {
      if (!fid) return;
      photosRef.current.set(started.runId, started.photos);
      storeCast(started.runId, pageWatch.get().cast);
      setRun({ runId: started.runId, telling: started.telling, fresh: true });
      router.replace(runHref({ familyId: fid, runId: started.runId }), { scroll: false });
    },
    [fid, router, pageWatch],
  );

  // A phone call that starts its own memory opens here, as if it had been sent
  // from this form. Only the plain form waits for one: not a run, a retry or
  // a second telling.
  const waitsForCall = run === null && !runId && !retryOf && !memoryId ? fid : null;
  const onCallRun = useCallback(
    (callRunId: string) => {
      if (!fid) return;
      setRun(reattach(callRunId));
      router.replace(runHref({ familyId: fid, runId: callRunId }), { scroll: false });
    },
    [fid, router],
  );
  useCallRun(waitsForCall, onCallRun);

  // After the run's own error: back to the form, filled in with what it was
  // sent. The form shows at once (tellingAgain), before ?retry= catches up.
  const [tellingAgain, setTellingAgain] = useState<string | null>(null);
  // The photos handed back to the form, read from photosRef when "Tell it
  // again" is pressed (a ref is not read while rendering).
  const [backPhotos, setBackPhotos] = useState<{ runId: string; photos: File[] } | null>(null);
  const [seenRetryOf, setSeenRetryOf] = useState(retryOf);
  if (seenRetryOf !== retryOf) {
    setSeenRetryOf(retryOf);
    setTellingAgain(null);
  }
  const retryId = retryOf ?? tellingAgain;

  const onTellAgain = useCallback(
    (failedRunId: string) => {
      if (!fid) return;
      setTellingAgain(failedRunId);
      const photos = photosRef.current.get(failedRunId);
      setBackPhotos(photos ? { runId: failedRunId, photos } : null);
      setRun(null);
      router.replace(retryHref({ familyId: fid, runId: failedRunId }), { scroll: false });
    },
    [fid, router],
  );

  // The book: whose it is, and the run making its new page.
  const wide = useWideDesk();
  const selfPersonId = family?.personId ?? null;
  const owner = useMemo(
    () => ({ title: bookOf(me.user.name), personId: selfPersonId, familyId: fid }),
    [me.user.name, selfPersonId, fid],
  );
  const second = Boolean(run?.telling?.secondTellingOf);
  const fresh = run?.fresh ?? false;
  const runBook = useMemo(
    () =>
      craftWatch && fid
        ? {
            watch: craftWatch,
            fresh,
            familyId: fid,
            second,
            // The stage plays saved moments from the real API only.
            loadMoment: isMockMode()
              ? null
              : (id: string, signal: AbortSignal) => loadMomentPage(id, { family: fid, demo: false }, signal),
          }
        : null,
    [craftWatch, fid, fresh, second],
  );
  const book = <CraftingBook owner={owner} page={pageWatch} run={runBook} placement="side" />;
  const preview = runBook ? <CraftingBook owner={owner} page={pageWatch} run={runBook} placement="preview" /> : null;

  // Every screen of the flow lies on the same desk, so the book beside it
  // stays put (and keeps its one canvas) as the form turns into the run.
  let content: ReactNode;
  // ?retry=<run>: the form again with what that run was sent. Without the
  // saved draft (another tab, blocked storage) it is the plain form.
  const draft = retryId ? loadSentDraft(sessionStore(), retryId) : null;
  if (!family || !fid) {
    // SessionGate's requireFamily sends people without a family away; this
    // is only reached if that changes under us.
    content = (
      <FlowPage title={TITLE} width="wide">
        <PaperCard className="flex flex-col items-start gap-4">
          <p className="text-[1.0625rem] leading-relaxed text-ink">
            Memories live in a family. Start one first, then come back to add yours.
          </p>
          <ButtonLink href={hrefs.newFamily()}>Start a family</ButtonLink>
        </PaperCard>
      </FlowPage>
    );
  } else if (run) {
    content = (
      <AddRun
        key={run.runId}
        api={api}
        fid={fid}
        run={run}
        lede={choice.show ? <FamilyLine choice={choice} switchable={false} /> : undefined}
        onTellAgain={() => onTellAgain(run.runId)}
        craft={craftWatch}
        book={wide ? null : preview}
      />
    );
  } else if (lookFor && !looked) {
    content = <AddLoadingPage kind="second" />;
  } else if (looked?.kind === "error") {
    content = (
      <AddLoadFailed
        choice={choice}
        message={looked.message}
        onRetry={() => setLookupTry((n) => n + 1)}
      />
    );
  } else if (!family.personId) {
    content = (
      <FlowPage
        title={TITLE}
        lede={choice.show ? <FamilyLine choice={choice} /> : undefined}
        width="wide"
        back={{ href: hrefs.home(), label: "Family tree" }}
      >
        <PaperCard className="flex flex-col items-start gap-4">
          <h2 className="font-display text-[1.375rem] leading-tight text-ink">
            You&rsquo;re not in the tree yet
          </h2>
          <p className="text-[1.0625rem] leading-relaxed text-pretty text-ink-soft">
            Memories are told by someone in {family.name}&rsquo;s tree. Find yourself in the family
            settings first, then come back to tell your story.
          </p>
          <ButtonLink variant="secondary" href={hrefs.settings(fid)}>
            Open family settings
          </ButtonLink>
        </PaperCard>
      </FlowPage>
    );
  } else if (retryId && draft) {
    content = (
      <AddForm
        key={`${fid}:retry:${retryId}`}
        api={api}
        fid={fid}
        selfPersonId={family.personId}
        memoryId={memoryId ?? draft.memoryId}
        choice={choice}
        onStarted={onStarted}
        onPreview={onPreview}
        restore={{ runId: retryId, draft, photos: backPhotos?.runId === retryId ? backPhotos.photos : null }}
        added={added}
        momentHome={momentHome}
      />
    );
  } else {
    content = (
      <AddForm
        key={`${fid}:${memoryId ?? ""}`}
        api={api}
        fid={fid}
        selfPersonId={family.personId}
        memoryId={memoryId}
        choice={choice}
        onStarted={onStarted}
        onPreview={onPreview}
        added={added}
        questionId={questionId}
        momentHome={momentHome}
      />
    );
  }

  return (
    <BookDesk mode={run ? "run" : "write"} book={family && fid ? book : null} label={BOOK_LABEL}>
      {content}
    </BookDesk>
  );
}
