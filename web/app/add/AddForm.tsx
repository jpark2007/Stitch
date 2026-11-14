"use client";

// The form half of /add: loads the tree, the questions waiting for this person
// and the family's moments (GET /threads: enough to open a second telling from
// `?memoryId=` or a picked question without loading a scene), then shows one
// quiet question area (questionAreaFor) and the memory form with the story
// near the top. A second telling shows only its moment and that moment's own
// question, so nothing on the page can switch the moment it joins. Sending
// starts a run and hands it up. Whatever was typed (and the photos) carries
// into the form a pick remounts, and the story and year carry into another
// family's form when the person switches family.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AddMemoryForm } from "@/components/flow/AddMemoryForm";
import { Button, FOCUS_RING } from "@/components/flow/Button";
import { FlowPage } from "@/components/flow/FlowPage";
import { FormError } from "@/components/flow/FormError";
import { PaperCard } from "@/components/flow/PaperCard";
import { QuestionPrompts } from "@/components/flow/QuestionPrompts";
import {
  carryInto,
  clearCarry,
  draftFromSent,
  hasTold,
  momentFromMemory,
  momentsFromThreads,
  questionForMemory,
  readCarry,
  restoreNotice,
  saveCarry,
  saveSentDraft,
  saveTelling,
  sentDraftFromSubmit,
  tellingFromSubmit,
  type FamilyChoice,
  type MomentInfo,
  type SentDraft,
} from "@/components/flow/addPage";
import { draftFromQuestion, questionAreaFor, type MemoryDraft } from "@/components/flow/memoryForm";
import type { MemoryRunTelling } from "@/components/flow/MemoryRun";
import { hrefs } from "@/components/flow/session";
import { isApiError, type StitchApi } from "@/lib/api";
import type { Question, SubmitMemoryFields } from "@/lib/types";

import { currentPath, FamilyLede, ledeWithFamily, LoadingSheet, Notice, sessionStore } from "./parts";

type Moment = MomentInfo;

type PickablePerson = { id: string; name: string; born?: number | null; color?: string | null };

/** Where the form starts before any question is picked. */
type Base =
  | { kind: "new"; note: string | null }
  | { kind: "second"; memoryId: string; moment: Moment; question: Question | null };

interface Loaded {
  people: PickablePerson[];
  questions: Question[];
  base: Base;
  /** Every moment of the family by id; null when GET /threads failed. */
  moments: Map<string, Moment> | null;
}

type Status = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: Loaded };

/** A question the person chose to answer, with its moment for a second telling. */
interface Picked {
  question: Question;
  moment: Moment | null;
  note: string | null;
}

/**
 * What was typed before a pick changed the form, carried into the new one.
 * `participantIds` only come from a new memory's picker (a second telling's
 * people are its moment's, never chosen); null carries none.
 */
interface Carried {
  text: string;
  year: string;
  participantIds: string[] | null;
  photos: File[];
}

const NOTHING_CARRIED: Carried = { text: "", year: "", participantIds: null, photos: [] };

/** The latest draft in the form, and whether it was a second telling. */
interface Latest {
  draft: MemoryDraft | null;
  photos: File[];
  second: boolean;
}

const LOAD_ERROR = "We couldn't open the page for adding a memory. Check your connection and try again.";
const PICK_ERROR = "That question didn't open. Check your connection and try again.";
const MISSING_MOMENT =
  "We couldn't find that moment in this family, so this will start a new memory instead.";
const MISSING_QUESTION_MOMENT =
  "We couldn't find the moment this question is about, so your answer will start a new memory.";

function messageOf(e: unknown, fallback: string): string {
  return isApiError(e) && e.status !== 0 && e.detail ? e.detail : fallback;
}

/** A run that started: its id, the slip of what was sent and the photo files that went with it. */
export type StartedRun = { runId: string; telling: MemoryRunTelling; photos: File[] };

/**
 * What a failed run was sent, to fill the form in again ("Tell it again").
 * `photos` are the files themselves when this page still has them (null after
 * a reload); `runId` names the failed run.
 */
export interface Restore {
  runId: string;
  draft: SentDraft;
  photos: File[] | null;
}

export function AddForm(props: {
  api: StitchApi;
  fid: string;
  selfPersonId: string;
  memoryId: string | null;
  /** Which family this goes into; named on the page only for someone in two or more. */
  choice: FamilyChoice;
  onStarted: (run: StartedRun) => void;
  /** Fill the form in with what a failed run was sent. */
  restore?: Restore;
}) {
  const [attempt, setAttempt] = useState(0);
  return <AddFormLoader key={attempt} {...props} onRetry={() => setAttempt((n) => n + 1)} />;
}

function AddFormLoader(props: {
  api: StitchApi;
  fid: string;
  selfPersonId: string;
  memoryId: string | null;
  choice: FamilyChoice;
  onStarted: (run: StartedRun) => void;
  restore?: Restore;
  onRetry: () => void;
}) {
  const { api, fid, selfPersonId, memoryId, choice, onStarted, restore, onRetry } = props;
  const router = useRouter();
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  useEffect(() => {
    let live = true;
    // The family's moments, from the threads: a moment that is not there is
    // simply missing (no 404 to log). Without them, ask for the one moment.
    const known = api.getThreads(fid).then(momentsFromThreads, () => null);
    const moment = known.then((all): Promise<Moment | null> | Moment | null => {
      if (!memoryId) return null;
      if (all) return all.get(memoryId) ?? null;
      return api.getMemory(fid, memoryId).then(momentFromMemory, (e: unknown) => {
        // A moment that is gone becomes a new memory; anything else fails the load.
        if (isApiError(e) && e.status === 404) return null;
        throw e;
      });
    });

    Promise.all([api.getTree(fid), api.getQuestions(fid), moment, known]).then(
      ([tree, qs, m, all]) => {
        if (!live) return;
        const people = tree.people.map((p) => ({
          id: p.id,
          name: p.name,
          born: p.born ?? null,
          color: p.character?.color ?? null,
        }));
        const questions = qs.questions;
        const base: Base =
          memoryId && m
            ? { kind: "second", memoryId, moment: m, question: questionForMemory(questions, memoryId) }
            : { kind: "new", note: memoryId ? MISSING_MOMENT : null };
        setStatus({ kind: "ready", data: { people, questions, base, moments: all } });
      },
      (e: unknown) => {
        if (!live) return;
        if (isApiError(e) && e.status === 401) {
          router.replace(hrefs.login(currentPath()));
          return;
        }
        setStatus({ kind: "error", message: messageOf(e, LOAD_ERROR) });
      },
    );
    return () => {
      live = false;
    };
  }, [api, fid, memoryId, router]);

  if (status.kind === "loading") {
    return (
      <FlowPage
        title={memoryId ? "Add your telling" : "Add a memory"}
        lede={ledeWithFamily(choice)}
        width="wide"
        back={BACK}
      >
        <LoadingSheet label="Getting the page ready…" />
      </FlowPage>
    );
  }

  if (status.kind === "error") {
    return (
      <FlowPage title="Add a memory" lede={ledeWithFamily(choice)} width="wide" back={BACK}>
        <PaperCard className="flex flex-col gap-5">
          <FormError message={status.message} />
          <div>
            <Button onClick={onRetry}>Try again</Button>
          </div>
        </PaperCard>
      </FlowPage>
    );
  }

  return (
    <AddFormReady
      api={api}
      fid={fid}
      selfPersonId={selfPersonId}
      data={status.data}
      choice={choice}
      onStarted={onStarted}
      restore={restore}
    />
  );
}

const BACK = { href: hrefs.home(), label: "Family tree" };

function AddFormReady(props: {
  api: StitchApi;
  fid: string;
  selfPersonId: string;
  data: Loaded;
  choice: FamilyChoice;
  onStarted: (run: StartedRun) => void;
  restore?: Restore;
}) {
  const { api, fid, selfPersonId, data, choice, onStarted, restore } = props;
  const { people, questions, base } = data;

  // Only a new memory picks questions: a second telling shows none to pick.
  const [picked, setPicked] = useState<Picked | null>(null);
  const [pickingId, setPickingId] = useState<string | null>(null);
  const [pickError, setPickError] = useState<string | null>(null);
  // What was typed so far, handed to the form a pick remounts. A new memory
  // also starts with the story carried from another family's form.
  const [carried, setCarried] = useState<Carried>(() => {
    if (base.kind !== "new" || restore) return NOTHING_CARRIED;
    const c = readCarry(sessionStore(), fid);
    return c ? { ...NOTHING_CARRIED, text: c.text, year: c.year } : NOTHING_CARRIED;
  });
  useEffect(() => clearCarry(sessionStore()), []);
  // The form's latest draft and photos (told by onDraftChange).
  const latest = useRef<Latest>({ draft: null, photos: [], second: false });
  // Moments by id: the family's from the threads, plus any fetched for a
  // picked question when the threads could not be read (null: not there).
  const moments = useRef(new Map<string, Moment | null>(data.moments ?? []));
  // Only the latest pick may land.
  const pickToken = useRef(0);

  // The question note shown pressed.
  const currentId = picked ? picked.question.id : null;

  // The year and people of the last new-memory draft, so a moment's question
  // picked and let go again gives them back.
  const lastNew = useRef<{ year: string; participantIds: string[] } | null>(null);

  /** Keeps what is in the form now, so a remount does not lose it. */
  const carryTyped = useCallback(() => {
    const { draft, photos, second } = latest.current;
    if (draft && !second) lastNew.current = { year: draft.year, participantIds: [...draft.participantIds] };
    const own = lastNew.current;
    setCarried({
      text: draft?.text ?? "",
      year: own?.year ?? "",
      participantIds: own ? [...own.participantIds] : null,
      photos: [...photos],
    });
  }, []);

  const onDraftChange = useCallback((draft: MemoryDraft, photos: File[]) => {
    latest.current = { draft, photos, second: Boolean(draft.memoryId) };
  }, []);

  // Switching family: the story and year go along to that family's form.
  const onSwitch = useCallback((familyId: string) => {
    const { draft, second } = latest.current;
    saveCarry(sessionStore(), { familyId, text: draft?.text ?? "", year: draft && !second ? draft.year : "" });
  }, []);

  const onPick = useCallback(
    (q: Question) => {
      const token = ++pickToken.current;
      setPickError(null);
      if (pickingId === q.id) {
        // Pressed again while its moment is still opening: never mind.
        setPickingId(null);
        return;
      }
      setPickingId(null);
      if (currentId === q.id) {
        // Pressing the pressed note lets it go: the form keeps the story and
        // drops the question.
        carryTyped();
        setPicked(null);
        return;
      }
      const mid = q.memoryId ?? null;
      if (!mid) {
        carryTyped();
        setPicked({ question: q, moment: null, note: null });
        return;
      }
      // With the family's moments loaded, one that is not among them is gone.
      const known = moments.current.has(mid) ? moments.current.get(mid) : data.moments ? null : undefined;
      if (known !== undefined) {
        carryTyped();
        setPicked({ question: q, moment: known, note: known ? null : MISSING_QUESTION_MOMENT });
        return;
      }
      setPickingId(q.id);
      api.getMemory(fid, mid).then(
        (m) => {
          const moment = momentFromMemory(m);
          moments.current.set(mid, moment);
          if (token !== pickToken.current) return;
          carryTyped();
          setPicked({ question: q, moment, note: null });
          setPickingId(null);
        },
        (e: unknown) => {
          if (token !== pickToken.current) return;
          setPickingId(null);
          if (isApiError(e) && e.status === 404) {
            moments.current.set(mid, null);
            carryTyped();
            setPicked({ question: q, moment: null, note: MISSING_QUESTION_MOMENT });
          } else {
            setPickError(messageOf(e, PICK_ERROR));
          }
        },
      );
    },
    [api, fid, currentId, pickingId, carryTyped, data.moments],
  );

  // What the form starts from: the picked question, else the moment from
  // ?memoryId=, else a blank memory. The key changes only when that does.
  let initial: Partial<MemoryDraft> | undefined;
  let secondTellingOf: { title: string; year: number } | null = null;
  let formKey: string;
  let note: string | null = null;
  let alreadyTold = false;
  // A restored question was typed, not picked: it stays in the typed field.
  let typedQuestion = false;
  if (picked) {
    const q = picked.moment ? picked.question : { ...picked.question, memoryId: null };
    initial = draftFromQuestion(q, picked.moment);
    if (picked.moment) {
      secondTellingOf = { title: picked.moment.title, year: picked.moment.year };
      alreadyTold = hasTold(picked.moment, selfPersonId);
    }
    formKey = `q:${picked.question.id}`;
    note = picked.note;
  } else if (base.kind === "second") {
    initial = draftFromQuestion(
      { text: base.question?.text ?? "", memoryId: base.memoryId },
      { year: base.moment.year, participantIds: base.moment.participantIds },
    );
    secondTellingOf = { title: base.moment.title, year: base.moment.year };
    alreadyTold = hasTold(base.moment, selfPersonId);
    formKey = `m:${base.memoryId}:${base.question?.id ?? ""}`;
    if (restore) {
      // The story and question come back; the moment keeps its year and people.
      const sent = draftFromSent(restore.draft);
      initial = { ...initial, text: sent.text, question: sent.question || initial.question };
      formKey += `:r:${restore.runId}`;
    }
  } else {
    formKey = "new";
    note = base.note;
    if (restore) {
      // A new memory: everything sent comes back. A moment that has gone
      // since (base.note says so) starts a new memory instead.
      initial = { ...draftFromSent(restore.draft), memoryId: null };
      formKey += `:r:${restore.runId}`;
      typedQuestion = true;
    }
  }
  // Whatever was typed before the pick goes on into the new form. A second
  // telling keeps the moment's year and people.
  initial = carryInto(initial, carried, secondTellingOf !== null);
  const startPhotos = carried.photos.length > 0 ? carried.photos : (restore?.photos ?? undefined);

  const second = secondTellingOf !== null;
  const secondTitle = secondTellingOf?.title ?? null;
  // One question area: the family's notes for a new memory, nothing to pick
  // for a second telling (its own question rides along read-only).
  const area = questionAreaFor({
    mode: base.kind === "second" && !picked ? "second" : "new",
    openQuestions: questions.length,
    prefilledQuestion: !typedQuestion && (initial?.question ?? "").trim() !== "",
  });
  const askedQuestion = base.kind === "second" && !picked ? (base.question?.text ?? null) : null;

  const onSubmit = useCallback(
    async (fields: SubmitMemoryFields, photos: File[]) => {
      // ApiErrors reject from here, so the form shows them where they belong.
      const res = await api.submitMemory(fid, fields, photos);
      const telling = tellingFromSubmit(fields, photos.length, secondTitle);
      saveTelling(sessionStore(), res.runId, telling);
      // Kept so a run that fails can bring the form back filled in.
      saveSentDraft(sessionStore(), res.runId, sentDraftFromSubmit(fields, photos.length));
      onStarted({ runId: res.runId, telling, photos });
    },
    [api, fid, secondTitle, onStarted],
  );

  return (
    <FlowPage
      title={second ? "Add your telling" : "Add a memory"}
      lede={
        <FamilyLede choice={choice} onSwitch={onSwitch}>
          {second ? (
            <>
              Tell it as you remember it. It doesn&rsquo;t have to match anyone else&rsquo;s.
              {base.kind === "second" && !picked ? (
                <>
                  {" "}
                  <Link
                    href={hrefs.add({ familyId: fid })}
                    // The story typed so far goes along to the new memory.
                    onClick={() => onSwitch(fid)}
                    className={`rounded-paper font-semibold text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2 ${FOCUS_RING}`}
                  >
                    Tell a different story instead
                  </Link>
                </>
              ) : null}
            </>
          ) : (
            "Tell it the way you would at the kitchen table. The studio turns it into a scene for the family book."
          )}
        </FamilyLede>
      }
      back={BACK}
      width="wide"
    >
      <div className="flex flex-col gap-7">
        {area.showFamilyQuestions ? (
          <div className="flex flex-col gap-3">
            <QuestionPrompts
              questions={questions}
              pickedId={pickingId ?? currentId}
              onPick={onPick}
            />
            {pickingId ? (
              <p role="status" className="text-sm text-ink-soft">
                Opening that moment…
              </p>
            ) : null}
            <FormError message={pickError} />
          </div>
        ) : null}

        <div className="flex flex-col gap-4">
          {restore ? (
            <Notice>{restoreNotice(restore.draft.photoCount, restore.photos?.length ?? null)}</Notice>
          ) : null}
          {note ? <Notice>{note}</Notice> : null}
          <AddMemoryForm
            key={formKey}
            people={people}
            selfPersonId={selfPersonId}
            initial={initial}
            initialPhotos={startPhotos}
            secondTellingOf={secondTellingOf}
            askedQuestion={askedQuestion}
            alreadyTold={alreadyTold}
            onDraftChange={onDraftChange}
            questionArea={area}
            disabled={pickingId !== null}
            submitLabel={second ? "Save my telling" : "Save this memory"}
            onSubmit={onSubmit}
          />
        </div>
      </div>
    </FlowPage>
  );
}
