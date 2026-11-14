"use client";

// The add-a-memory screen. Reads ?family, ?memoryId and ?run, waits for the
// session, picks the family, then shows either the form (AddForm) or a run
// under way (AddRun). Sending the form swaps it for the run at once and puts
// the run in the URL, so a reload comes back to the same run.
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ButtonLink } from "@/components/flow/Button";
import { FlowPage } from "@/components/flow/FlowPage";
import { PaperCard } from "@/components/flow/PaperCard";
import { SessionGate, type SessionValue } from "@/components/flow/SessionGate";
import { loadTelling, readAddQuery, runHref } from "@/components/flow/addPage";
import type { MemoryRunTelling } from "@/components/flow/MemoryRun";
import { hrefs, pickFamily, recallFamily, rememberFamily } from "@/components/flow/session";

import { AddForm } from "./AddForm";
import { AddRun } from "./AddRun";
import { LoadingSheet, sessionStore } from "./parts";

const TITLE = "Add a memory";

/** The page shell with a quiet loading sheet: the prerendered HTML and the session wait. */
export function AddLoading() {
  return (
    <FlowPage title={TITLE} width="wide">
      <LoadingSheet label="Opening your family’s pages…" />
    </FlowPage>
  );
}

export function AddScreen() {
  const params = useSearchParams();
  const { familyId, memoryId, runId } = readAddQuery(params);
  return (
    <SessionGate requireFamily loading={<AddLoading />}>
      {(session) => (
        <AddFlow session={session} familyId={familyId} memoryId={memoryId} runId={runId} />
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
}) {
  const { session, familyId, memoryId, runId } = props;
  const { me, api } = session;
  const router = useRouter();

  // Only runs in the browser (SessionGate renders this after asking the
  // server who is signed in), so reading storage here is safe.
  const family = pickFamily(me.families, familyId ?? recallFamily());
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

  const onStarted = useCallback(
    (started: { runId: string; telling: MemoryRunTelling }) => {
      if (!fid) return;
      setRun({ runId: started.runId, telling: started.telling, fresh: true });
      router.replace(runHref({ familyId: fid, runId: started.runId }), { scroll: false });
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
    return <AddRun key={run.runId} api={api} fid={fid} run={run} />;
  }

  if (!family.personId) {
    return (
      <FlowPage title={TITLE} width="wide" back={{ href: hrefs.home(), label: "Family tree" }}>
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

  return (
    <AddForm
      key={`${fid}:${memoryId ?? ""}`}
      api={api}
      fid={fid}
      selfPersonId={family.personId}
      memoryId={memoryId}
      onStarted={onStarted}
    />
  );
}
