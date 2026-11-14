"use client";

// The add-a-memory screen. Reads ?family, ?memoryId, ?run and ?retry, waits
// for the session, picks the family, then shows either the form (AddForm) or a
// run under way (AddRun). Sending the form swaps it for the run at once and
// puts the run in the URL, so a reload comes back to the same run. A run that
// fails with its own error offers "Tell it again": ?retry=<run> brings the
// form back filled in with what that run was sent. ?added=<person> comes back
// from the add-relative page: the form ticks that person, then drops it.
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ButtonLink } from "@/components/flow/Button";
import { FlowPage } from "@/components/flow/FlowPage";
import { PaperCard } from "@/components/flow/PaperCard";
import { SessionGate, type SessionValue } from "@/components/flow/SessionGate";
import {
  addedFromQuery,
  familyChoice,
  loadSentDraft,
  loadTelling,
  questionIdFromQuery,
  readAddQuery,
  retryHref,
  runHref,
} from "@/components/flow/addPage";
import type { MemoryRunTelling } from "@/components/flow/MemoryRun";
import { hrefs, recallFamily, rememberFamily } from "@/components/flow/session";

import { AddForm, type StartedRun } from "./AddForm";
import { AddRun } from "./AddRun";
import {
  ADD_BACK,
  FamilyLine,
  LoadingSheet,
  NEW_MEMORY_LEDE,
  SECOND_TELLING_LEDE,
  sessionStore,
} from "./parts";

const TITLE = "Add a memory";

/**
 * The page shell with a quiet loading sheet: the prerendered HTML and the
 * session wait. It has the form's back link, title and lede (none for a run,
 * whose page has none), so nothing jumps when the page arrives.
 */
export function AddLoading(props: { kind?: "new" | "second" | "run" }) {
  const { kind = "new" } = props;
  const title = kind === "second" ? "Add your telling" : kind === "run" ? "Your memory" : TITLE;
  const lede = kind === "second" ? SECOND_TELLING_LEDE : kind === "run" ? undefined : NEW_MEMORY_LEDE;
  return (
    <FlowPage title={title} lede={lede} back={ADD_BACK} width="wide">
      <LoadingSheet label="Opening your family’s pages…" />
    </FlowPage>
  );
}

export function AddScreen() {
  const params = useSearchParams();
  const { familyId, memoryId, runId, retryOf } = readAddQuery(params);
  const added = addedFromQuery(params);
  const questionId = questionIdFromQuery(params);
  const loadingKind = runId ? "run" : memoryId ? "second" : "new";
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

  // Only runs in the browser (SessionGate renders this after asking the
  // server who is signed in), so reading storage here is safe. Someone in two
  // or more families sees which one this goes into, with links to the others
  // (each to that family's plain form: no moment, no run).
  const choice = familyChoice(me.families, familyId ?? recallFamily());
  const family = choice.current;
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

  const onStarted = useCallback(
    (started: StartedRun) => {
      if (!fid) return;
      photosRef.current.set(started.runId, started.photos);
      setRun({ runId: started.runId, telling: started.telling, fresh: true });
      router.replace(runHref({ familyId: fid, runId: started.runId }), { scroll: false });
    },
    [fid, router],
  );

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

  if (!family || !fid) {
    // SessionGate's requireFamily sends people without a family away; this
    // is only reached if that changes under us.
    return (
      <FlowPage title={TITLE} width="wide">
        <PaperCard className="flex flex-col items-start gap-4">
          <p className="text-[1.0625rem] leading-relaxed text-ink">
            Memories live in a family. Start one first, then come back to add yours.
          </p>
          <ButtonLink href={hrefs.newFamily()}>Start a family</ButtonLink>
        </PaperCard>
      </FlowPage>
    );
  }

  if (run) {
    return (
      <AddRun
        key={run.runId}
        api={api}
        fid={fid}
        run={run}
        lede={choice.show ? <FamilyLine choice={choice} switchable={false} /> : undefined}
        onTellAgain={() => onTellAgain(run.runId)}
      />
    );
  }

  if (!family.personId) {
    return (
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
  }

  // ?retry=<run>: the form again with what that run was sent. Without the
  // saved draft (another tab, blocked storage) it is the plain form.
  const draft = retryId ? loadSentDraft(sessionStore(), retryId) : null;
  if (retryId && draft) {
    return (
      <AddForm
        key={`${fid}:retry:${retryId}`}
        api={api}
        fid={fid}
        selfPersonId={family.personId}
        memoryId={memoryId ?? draft.memoryId}
        choice={choice}
        onStarted={onStarted}
        restore={{ runId: retryId, draft, photos: backPhotos?.runId === retryId ? backPhotos.photos : null }}
        added={added}
      />
    );
  }

  return (
    <AddForm
      key={`${fid}:${memoryId ?? ""}`}
      api={api}
      fid={fid}
      selfPersonId={family.personId}
      memoryId={memoryId}
      choice={choice}
      onStarted={onStarted}
      added={added}
      questionId={questionId}
    />
  );
}
