"use client";

// The form half of /add: loads the tree, the questions waiting for this person
// and (for `?memoryId=`) the moment a second telling joins, then shows one
// quiet question area (questionAreaFor) and the memory form with the story
// near the top. A second telling shows only its moment and that moment's own
// question, so nothing on the page can switch the moment it joins. Sending
// starts a run and hands it up.
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
  momentFromMemory,
  questionForMemory,
  saveTelling,
  tellingFromSubmit,
  type FamilyChoice,
} from "@/components/flow/addPage";
import { draftFromQuestion, questionAreaFor, type MemoryDraft } from "@/components/flow/memoryForm";
import type { MemoryRunTelling } from "@/components/flow/MemoryRun";
import { hrefs } from "@/components/flow/session";
import { isApiError, type StitchApi } from "@/lib/api";
import type { Question, SubmitMemoryFields } from "@/lib/types";

import { currentPath, ledeWithFamily, LoadingSheet, Notice, sessionStore } from "./parts";

type Moment = ReturnType<typeof momentFromMemory>;

type PickablePerson = { id: string; name: string; born?: number | null; color?: string | null };

/** Where the form starts before any question is picked. */
type Base =
  | { kind: "new"; note: string | null }
  | { kind: "second"; memoryId: string; moment: Moment; question: Question | null };

interface Loaded {
  people: PickablePerson[];
  questions: Question[];
  base: Base;
}

type Status = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: Loaded };

/** A question the person chose to answer, with its moment for a second telling. */
interface Picked {
  question: Question;
  moment: Moment | null;
  note: string | null;
}

/** What was typed before a pick changed the form, carried into the new one. */
interface Carried {
  text: string;
  year: string;
}

const NOTHING_CARRIED: Carried = { text: "", year: "" };

const LOAD_ERROR = "We couldn't open the page for adding a memory. Check your connection and try again.";
const PICK_ERROR = "That question didn't open. Check your connection and try again.";
const MISSING_MOMENT = "We couldn't find that moment any more, so this will start a new memory.";
const MISSING_QUESTION_MOMENT =
  "We couldn't find the moment this question is about, so your answer will start a new memory.";

function messageOf(e: unknown, fallback: string): string {
  return isApiError(e) && e.status !== 0 && e.detail ? e.detail : fallback;
}

export function AddForm(props: {
  api: StitchApi;
  fid: string;
  selfPersonId: string;
  memoryId: string | null;
  /** Which family this goes into; named on the page only for someone in two or more. */
  choice: FamilyChoice;
  onStarted: (run: { runId: string; telling: MemoryRunTelling }) => void;
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
  onStarted: (run: { runId: string; telling: MemoryRunTelling }) => void;
  onRetry: () => void;
}) {
  const { api, fid, selfPersonId, memoryId, choice, onStarted, onRetry } = props;
  const router = useRouter();
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  useEffect(() => {
    let live = true;
    const moment = memoryId
      ? api.getMemory(fid, memoryId).then(
          (m) => momentFromMemory(m),
          (e: unknown) => {
            // A moment that is gone becomes a new memory; anything else fails the load.
            if (isApiError(e) && e.status === 404) return null;
            throw e;
          },
        )
      : Promise.resolve(null);

    Promise.all([api.getTree(fid), api.getQuestions(fid), moment]).then(
      ([tree, qs, m]) => {
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
        setStatus({ kind: "ready", data: { people, questions, base } });
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
  onStarted: (run: { runId: string; telling: MemoryRunTelling }) => void;
}) {
  const { api, fid, selfPersonId, data, choice, onStarted } = props;
  const { people, questions, base } = data;

  // Only a new memory picks questions: a second telling shows none to pick.
  const [picked, setPicked] = useState<Picked | null>(null);
  const [pickingId, setPickingId] = useState<string | null>(null);
  const [pickError, setPickError] = useState<string | null>(null);
  // The story and year typed so far, handed to the form a pick remounts.
  const [carried, setCarried] = useState<Carried>(NOTHING_CARRIED);
  const formWrapRef = useRef<HTMLDivElement>(null);
  // Moments fetched for picked questions, so picking one twice asks once.
  const moments = useRef(new Map<string, Moment | null>());
  // Only the latest pick may land.
  const pickToken = useRef(0);

  // The question note shown pressed.
  const currentId = picked ? picked.question.id : null;

  /** Reads what is in the form now, so a remount does not lose it. */
  const carryTyped = useCallback(() => {
    const root = formWrapRef.current;
    setCarried({
      text: root?.querySelector<HTMLTextAreaElement>('textarea[name="text"]')?.value ?? "",
      year: root?.querySelector<HTMLInputElement>('input[name="year"]')?.value ?? "",
    });
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
      const known = moments.current.get(mid);
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
    [api, fid, currentId, pickingId, carryTyped],
  );

  // What the form starts from: the picked question, else the moment from
  // ?memoryId=, else a blank memory. The key changes only when that does.
  let initial: Partial<MemoryDraft> | undefined;
  let secondTellingOf: { title: string; year: number } | null = null;
  let formKey: string;
  let note: string | null = null;
  if (picked) {
    const q = picked.moment ? picked.question : { ...picked.question, memoryId: null };
    initial = draftFromQuestion(q, picked.moment);
    if (picked.moment) secondTellingOf = { title: picked.moment.title, year: picked.moment.year };
    formKey = `q:${picked.question.id}`;
    note = picked.note;
  } else if (base.kind === "second") {
    initial = draftFromQuestion(
      { text: base.question?.text ?? "", memoryId: base.memoryId },
      { year: base.moment.year, participantIds: base.moment.participantIds },
    );
    secondTellingOf = { title: base.moment.title, year: base.moment.year };
    formKey = `m:${base.memoryId}:${base.question?.id ?? ""}`;
  } else {
    formKey = "new";
    note = base.note;
  }
  // Whatever was typed before the pick goes on into the new form. A second
  // telling keeps the moment's year.
  if (carried.text || (carried.year && !secondTellingOf && !initial?.year)) {
    initial = {
      ...initial,
      ...(carried.text ? { text: carried.text } : {}),
      ...(carried.year && !secondTellingOf && !initial?.year ? { year: carried.year } : {}),
    };
  }

  const second = secondTellingOf !== null;
  const secondTitle = secondTellingOf?.title ?? null;
  // One question area: the family's notes for a new memory, nothing to pick
  // for a second telling (its own question rides along read-only).
  const area = questionAreaFor({
    mode: base.kind === "second" && !picked ? "second" : "new",
    openQuestions: questions.length,
    prefilledQuestion: (initial?.question ?? "").trim() !== "",
  });
  const askedQuestion = base.kind === "second" && !picked ? (base.question?.text ?? null) : null;

  const onSubmit = useCallback(
    async (fields: SubmitMemoryFields, photos: File[]) => {
      // ApiErrors reject from here, so the form shows them where they belong.
      const res = await api.submitMemory(fid, fields, photos);
      const telling = tellingFromSubmit(fields, photos.length, secondTitle);
      saveTelling(sessionStore(), res.runId, telling);
      onStarted({ runId: res.runId, telling });
    },
    [api, fid, secondTitle, onStarted],
  );

  return (
    <FlowPage
      title={second ? "Add your telling" : "Add a memory"}
      lede={ledeWithFamily(
        choice,
        second ? (
          <>
            Tell it as you remember it. It doesn&rsquo;t have to match anyone else&rsquo;s.
            {base.kind === "second" && !picked ? (
              <>
                {" "}
                <Link
                  href={hrefs.add({ familyId: fid })}
                  className={`rounded-paper font-semibold text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2 ${FOCUS_RING}`}
                >
                  Tell a different story instead
                </Link>
              </>
            ) : null}
          </>
        ) : (
          "Tell it the way you would at the kitchen table. The studio turns it into a scene for the family book."
        ),
      )}
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

        <div ref={formWrapRef} className="flex flex-col gap-4">
          {note ? <Notice>{note}</Notice> : null}
          <AddMemoryForm
            key={formKey}
            people={people}
            selfPersonId={selfPersonId}
            initial={initial}
            secondTellingOf={secondTellingOf}
            askedQuestion={askedQuestion}
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
