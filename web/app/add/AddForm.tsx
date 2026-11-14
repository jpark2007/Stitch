"use client";

// The form half of /add: loads the tree, the questions waiting for this person
// and the family's moments (GET /threads: enough to open a second telling from
// `?memoryId=` or a picked question without loading a scene), then shows one
// quiet question area (questionAreaFor) and the memory form with the story
// near the top. A second telling shows only its moment and that moment's own
// question, so nothing on the page can switch the moment it joins. Sending
// starts a run and hands it up. Whatever was typed (and the photos) carries
// into the form a pick remounts, and the story and year carry into another
// family's form when the person switches family. "Someone missing?" under
// the people goes to the add-relative page with the whole draft kept (but the
// photo files); coming back with ?added= ticks the new person. What is typed
// is also kept for the tab (saveTypedDraft) and a picked question rides in
// the URL (?questionId=), so a reload gives the same form back; sending it
// drops the draft.
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
  addRelativeHref,
  carriedPhotosNote,
  carryInto,
  clearCarry,
  clearTypedDraft,
  draftForForm,
  draftFromSent,
  familyAsking,
  hasTold,
  knownMoment,
  loadTelling,
  mergeRefreshed,
  momentFromMemory,
  momentsFromThreads,
  openRunNotice,
  openRunsFor,
  preselectAdded,
  questionForMemory,
  readCarry,
  readTypedDraft,
  rememberOpenRun,
  restoreNotice,
  runHref,
  runToResume,
  saveCarry,
  saveSentDraft,
  saveTelling,
  saveTypedDraft,
  sentDraftFromSubmit,
  shouldRefreshOnShow,
  tellingFromSubmit,
  withQuestionId,
  withoutAdded,
  type FamilyChoice,
  type MomentInfo,
  type OpenRun,
  type SentDraft,
} from "@/components/flow/addPage";
import { draftFromQuestion, questionAreaFor, type MemoryDraft } from "@/components/flow/memoryForm";
import type { MemoryRunTelling } from "@/components/flow/MemoryRun";
import { hrefs } from "@/components/flow/session";
import { isApiError, type StitchApi } from "@/lib/api";
import type { Question, SubmitMemoryFields } from "@/lib/types";

import {
  ADD_BACK,
  currentPath,
  FamilyLede,
  ledeWithFamily,
  LoadingSheet,
  Notice,
  sessionStore,
  type AskingFamily,
  type MomentHome,
} from "./parts";

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
  /**
   * The `?added=` person this load started after (null for none): once it
   * matches the URL's, `people` is fresh enough to have them, if they are
   * in this family at all.
   */
  addedFor: string | null;
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
  /** A typed question (no pick), back from the add-relative page; "" for none. */
  question: string;
}

const NOTHING_CARRIED: Carried = { text: "", year: "", participantIds: null, photos: [], question: "" };

/** Where a new memory's form starts when the page opens: what came back to it. */
interface Start {
  carried: Carried;
  picked: Picked | null;
  /** Photos that were attached when the draft left (they cannot come back). */
  photosLeft: number;
}

const BLANK_START: Start = { carried: NOTHING_CARRIED, picked: null, photosLeft: 0 };

/** The latest draft in the form, and whether it was a second telling. */
interface Latest {
  draft: MemoryDraft | null;
  photos: File[];
  second: boolean;
}

export const LOAD_ERROR = "We couldn't open the page for adding a memory. Check your connection and try again.";
const PICK_ERROR = "That question didn't open. Check your connection and try again.";
const MISSING_MOMENT =
  "We couldn't find that moment in this family, so this will start a new memory instead.";
const MISSING_QUESTION_MOMENT =
  "We couldn't find the moment this question is about, so your answer will start a new memory.";

export function messageOf(e: unknown, fallback: string): string {
  return isApiError(e) && e.status !== 0 && e.detail ? e.detail : fallback;
}

/**
 * The family question a carried draft had picked, picked again. A question
 * that is no longer open is dropped. One about a moment is only re-picked
 * when the family's moments are known (else the form starts without it).
 */
function repick(
  questionId: string | null,
  questions: readonly Question[],
  moments: Map<string, Moment> | null,
): Picked | null {
  const question = questionId ? questions.find((q) => q.id === questionId) : undefined;
  if (!question) return null;
  const mid = question.memoryId ?? null;
  if (!mid) return { question, moment: null, note: null };
  if (!moments) return null;
  const moment = moments.get(mid) ?? null;
  return { question, moment, note: moment ? null : MISSING_QUESTION_MOMENT };
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
  /** `?added=`: someone just added on the add-relative page, to tick under "Who was there?". */
  added?: string | null;
  /** `?questionId=`: the family question to open picked (read once, when the form appears). */
  questionId?: string | null;
  /** The moment from `?memoryId=` and the family found holding it, for the switch links. */
  momentHome?: MomentHome | null;
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
  added?: string | null;
  questionId?: string | null;
  momentHome?: MomentHome | null;
  onRetry: () => void;
}) {
  const {
    api,
    fid,
    selfPersonId,
    memoryId,
    choice,
    onStarted,
    restore,
    added = null,
    questionId = null,
    momentHome = null,
    onRetry,
  } = props;
  const router = useRouter();
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  // The last `?added=` person seen. A new one loads the family again: this
  // page can be kept alive (hidden) while the person is added elsewhere, so
  // the people it has may not include them yet. Going back to null (once the
  // form has ticked them and the URL drops it) loads nothing.
  const [loadFor, setLoadFor] = useState<string | null>(added);
  if (added && added !== loadFor) setLoadFor(added);
  // Bumped when the window comes back (see below): loads the page again
  // under the form, so a question asked or a relative who joined meanwhile
  // shows up without a reload.
  const [refresh, setRefresh] = useState(0);
  // When the last load landed (or failed), and whether one is running: the
  // window coming back reloads at most every ADD_REFRESH_MIN_GAP_MS.
  const lastLoadedAt = useRef<number | null>(null);
  const inFlight = useRef(false);

  useEffect(() => {
    let live = true;
    const addedFor = loadFor;
    inFlight.current = true;
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
        inFlight.current = false;
        lastLoadedAt.current = Date.now();
        const people = tree.people.map((p) => ({
          id: p.id,
          name: p.name,
          born: p.born ?? null,
          color: p.character?.color ?? null,
        }));
        const questions = qs.questions;
        setStatus((s) => {
          // Under a form already on screen, its base stays as it is, so the
          // form is not remounted and nothing typed is lost.
          if (s.kind === "ready") {
            return { kind: "ready", data: mergeRefreshed(s.data, { people, questions, moments: all }, addedFor) };
          }
          const base: Base =
            memoryId && m
              ? { kind: "second", memoryId, moment: m, question: questionForMemory(questions, memoryId) }
              : { kind: "new", note: memoryId ? MISSING_MOMENT : null };
          return { kind: "ready", data: { people, questions, base, moments: all, addedFor } };
        });
      },
      (e: unknown) => {
        if (!live) return;
        inFlight.current = false;
        lastLoadedAt.current = Date.now();
        if (isApiError(e) && e.status === 401) {
          router.replace(hrefs.login(currentPath()));
          return;
        }
        // Loading again under a form already on screen (the page shown
        // again, or someone just added): keep the form and what is typed in
        // it, with the people it had. Only a first load shows the error.
        setStatus((s) =>
          s.kind === "ready"
            ? { kind: "ready", data: { ...s.data, addedFor } }
            : { kind: "error", message: messageOf(e, LOAD_ERROR) },
        );
      },
    );
    return () => {
      live = false;
      inFlight.current = false;
    };
  }, [api, fid, memoryId, router, loadFor, refresh]);

  // The window coming back (the tab shown again, or the window focused):
  // load again, quietly. Only once the form is up, and the page stays as it
  // is (no loading sheet) until the fresh data lands.
  const ready = status.kind === "ready";
  useEffect(() => {
    if (!ready) return;
    const onShow = () => {
      const go = shouldRefreshOnShow({
        visible: document.visibilityState === "visible",
        ready: true,
        inFlight: inFlight.current,
        lastLoadedAt: lastLoadedAt.current,
        now: Date.now(),
      });
      if (!go) return;
      // Marked now: focus and visibilitychange often fire back to back.
      inFlight.current = true;
      setRefresh((n) => n + 1);
    };
    document.addEventListener("visibilitychange", onShow);
    window.addEventListener("focus", onShow);
    return () => {
      document.removeEventListener("visibilitychange", onShow);
      window.removeEventListener("focus", onShow);
    };
  }, [ready]);

  if (status.kind === "loading") {
    return (
      <FlowPage
        title={memoryId ? "Add your telling" : "Add a memory"}
        lede={ledeWithFamily(choice, undefined, true, momentHome)}
        width="wide"
        back={BACK}
      >
        <LoadingSheet label="Getting the page ready…" />
      </FlowPage>
    );
  }

  if (status.kind === "error") {
    return <AddLoadFailed choice={choice} message={status.message} onRetry={onRetry} momentHome={momentHome} />;
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
      added={added}
      questionId={questionId}
      momentHome={momentHome}
    />
  );
}

const BACK = ADD_BACK;

/** The page when it could not load: what went wrong, and a way to try again. */
export function AddLoadFailed(props: {
  choice: FamilyChoice;
  message: string;
  onRetry: () => void;
  momentHome?: MomentHome | null;
}) {
  const { choice, message, onRetry, momentHome = null } = props;
  return (
    <FlowPage title="Add a memory" lede={ledeWithFamily(choice, undefined, true, momentHome)} width="wide" back={BACK}>
      <PaperCard className="flex flex-col gap-5">
        <FormError message={message} />
        <div>
          <Button onClick={onRetry}>Try again</Button>
        </div>
      </PaperCard>
    </FlowPage>
  );
}

function AddFormReady(props: {
  api: StitchApi;
  fid: string;
  selfPersonId: string;
  data: Loaded;
  choice: FamilyChoice;
  onStarted: (run: StartedRun) => void;
  restore?: Restore;
  added: string | null;
  questionId: string | null;
  momentHome: MomentHome | null;
}) {
  const { api, fid, selfPersonId, data, choice, onStarted, restore, added, questionId, momentHome } = props;
  const { people, questions, base } = data;
  const router = useRouter();
  const baseMemoryId = base.kind === "second" ? base.memoryId : null;

  // A new memory starts from what came back to this page: the draft carried
  // from another family's form, or from this one before a trip to the
  // add-relative page (its story, year, people and picked question), else
  // the draft kept for this tab (a reload), with whoever was just added on
  // the add-relative page (?added=) ticked and the question in ?questionId=
  // picked. A second telling from ?memoryId= gets its kept story back. Read
  // once, on mount.
  const [start] = useState<Start>(() => {
    if (restore) return BLANK_START;
    const store = sessionStore();
    if (base.kind === "second") {
      const kept = draftForForm(readTypedDraft(store, fid), { memoryId: base.memoryId, questionId: null });
      if (!kept) return BLANK_START;
      return { carried: { ...NOTHING_CARRIED, text: kept.text }, picked: null, photosLeft: kept.photoCount };
    }
    const c = readCarry(store, fid);
    const kept = c ? null : draftForForm(readTypedDraft(store, fid), { memoryId: null, questionId });
    const participantIds = preselectAdded(
      c ? c.participantIds : (kept?.participantIds ?? null),
      added,
      people.map((p) => p.id),
    );
    const pickId = questionId ?? c?.questionId ?? kept?.questionId ?? null;
    if (!c && !kept && !participantIds && !pickId) return BLANK_START;
    return {
      carried: {
        ...NOTHING_CARRIED,
        text: c?.text ?? kept?.text ?? "",
        year: c?.year ?? kept?.year ?? "",
        participantIds,
        question: c?.question ?? kept?.question ?? "",
      },
      picked: repick(pickId, questions, data.moments),
      photosLeft: c?.photoCount ?? kept?.photoCount ?? 0,
    };
  });

  // Only a new memory picks questions: a second telling shows none to pick.
  const [picked, setPicked] = useState<Picked | null>(start.picked);
  const [pickingId, setPickingId] = useState<string | null>(null);
  const [pickError, setPickError] = useState<string | null>(null);
  // What was typed so far, handed to the form a pick remounts.
  const [carried, setCarried] = useState<Carried>(start.carried);
  // Photos left behind on the way to the add-relative page: said once, until
  // a photo is added again.
  const [photosLeft, setPhotosLeft] = useState(start.photosLeft);
  useEffect(() => clearCarry(sessionStore()), []);

  // A run of this family started in this tab and left before it was over
  // (waiting on the person, or still being made): one calm note with the way
  // back to it. Read once; the form below stays free to use.
  const plain = base.kind === "new" && !restore;
  const [openRun] = useState<{ run: OpenRun; notice: string } | null>(() => {
    if (!plain) return null;
    const store = sessionStore();
    const run = runToResume(openRunsFor(store, fid));
    return run ? { run, notice: openRunNotice(run, loadTelling(store, run.runId)?.text) } : null;
  });

  // Someone in another family asked this person something: one quiet line
  // under the family line, pointing there. Asked of every other family at
  // once; one that fails is left out.
  const [asking, setAsking] = useState<AskingFamily | null>(null);
  const others = choice.others;
  const askOthers = plain && choice.show && others.length > 0;
  useEffect(() => {
    if (!askOthers) return;
    let live = true;
    Promise.all(others.map((f) => api.getQuestions(f.id).then((r) => r.questions, () => null))).then((qs) => {
      if (!live) return;
      const f = familyAsking(others, qs);
      setAsking(f ? { id: f.id, name: f.name } : null);
    });
    return () => {
      live = false;
    };
  }, [api, askOthers, others]);

  // ?added= on a form that was already here (the page kept alive while the
  // person was added): once the people are loaded fresh for them, the form
  // ticks them in place, keeping everything else, photos included. Someone
  // not in this family is ignored.
  const freshAdded = added && data.addedFor === added ? added : null;
  const tickPersonId =
    freshAdded && base.kind === "new" && people.some((p) => p.id === freshAdded) ? freshAdded : null;
  // Then ?added= leaves the URL (the rest stays), so a reload does not tick
  // that person again after they were unticked.
  useEffect(() => {
    if (freshAdded) router.replace(withoutAdded(window.location.search), { scroll: false });
  }, [freshAdded, router]);
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
      question: "",
    });
  }, []);

  // The picked question rides in the URL (?questionId=), every other param
  // kept, so a reload picks it again. A second telling from ?memoryId= picks
  // nothing, so its URL is left alone. Only on /add itself: this page can be
  // kept alive, hidden, while another route shows.
  const syncQuestion = base.kind === "new";
  useEffect(() => {
    if (!syncQuestion || window.location.pathname !== "/add") return;
    const next = withQuestionId(window.location.search, currentId);
    if (next !== `${window.location.pathname}${window.location.search}`) {
      window.history.replaceState(null, "", next);
    }
  }, [currentId, syncQuestion]);

  const onDraftChange = useCallback(
    (draft: MemoryDraft, photos: File[]) => {
      const second = Boolean(draft.memoryId);
      latest.current = { draft, photos, second };
      if (photos.length > 0) setPhotosLeft(0);
      // Kept for a reload of this tab. A picked question's text, a moment's
      // year and people are not typed: they come back with the pick.
      saveTypedDraft(sessionStore(), {
        familyId: fid,
        memoryId: baseMemoryId,
        questionId: currentId,
        text: draft.text,
        year: second ? "" : draft.year,
        participantIds: second ? [] : draft.participantIds.filter((id) => id !== selfPersonId),
        question: currentId || second ? "" : draft.question,
        photoCount: photos.length > 0 ? photos.length : photosLeft,
      });
    },
    [fid, baseMemoryId, currentId, selfPersonId, photosLeft],
  );

  // Off to the add-relative page: keep the whole draft (all but the photo
  // files) for the form that comes back with the new person ticked.
  const pickedId = picked?.question.id ?? null;
  const onAddPerson = useCallback(() => {
    const { draft, photos, second } = latest.current;
    if (!draft || second) return;
    saveCarry(sessionStore(), {
      familyId: fid,
      text: draft.text,
      year: draft.year,
      participantIds: draft.participantIds.filter((id) => id !== selfPersonId),
      questionId: pickedId,
      // A picked question comes back by id; only a typed one travels as text.
      question: pickedId ? null : draft.question,
      photoCount: photos.length,
    });
  }, [fid, selfPersonId, pickedId]);

  // Switching family: the story and year go along to that family's form,
  // and leave this one (else a later visit here would find them again).
  const onSwitch = useCallback(
    (familyId: string) => {
      const { draft, second } = latest.current;
      saveCarry(sessionStore(), { familyId, text: draft?.text ?? "", year: draft && !second ? draft.year : "" });
      if (familyId !== fid) clearTypedDraft(sessionStore(), fid);
    },
    [fid],
  );

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
      // The freshest moments first (one made since the page opened is there
      // now), then any fetched one by one. With the family's moments loaded,
      // one that is not among them is gone.
      const known = knownMoment(mid, moments.current, data.moments);
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
    } else if (carried.question.trim()) {
      // A question typed before the add-relative trip comes back typed.
      typedQuestion = true;
    }
  }
  // Whatever was typed before the pick goes on into the new form. A second
  // telling keeps the moment's year and people.
  initial = carryInto(initial, carried, secondTellingOf !== null);
  const startPhotos = carried.photos.length > 0 ? carried.photos : (restore?.photos ?? undefined);

  const second = secondTellingOf !== null;
  const secondTitle = secondTellingOf?.title ?? null;
  const photosNote = carriedPhotosNote(photosLeft);
  // One question area: the family's notes for a new memory, nothing to pick
  // for a second telling (its own question rides along read-only).
  const area = questionAreaFor({
    mode: base.kind === "second" && !picked ? "second" : "new",
    openQuestions: questions.length,
    prefilledQuestion: !typedQuestion && (initial?.question ?? "").trim() !== "",
  });
  // The question a second telling from ?memoryId= answers, or the family
  // question picked for a new memory (shown as what is being answered).
  // A picked note already shows itself pressed ("Answering this") in the
  // list above the story, so the form repeats it only when that list is not
  // on the page.
  let askedQuestion: string | null = null;
  if (base.kind === "second" && !picked) askedQuestion = base.question?.text ?? null;
  else if (picked && !picked.moment && !area.showFamilyQuestions) askedQuestion = picked.question.text;

  const onSubmit = useCallback(
    async (fields: SubmitMemoryFields, photos: File[]) => {
      // ApiErrors reject from here, so the form shows them where they belong.
      const res = await api.submitMemory(fid, fields, photos);
      // Sent: the draft kept for a reload has done its job.
      clearTypedDraft(sessionStore(), fid);
      const telling = tellingFromSubmit(fields, photos.length, secondTitle);
      saveTelling(sessionStore(), res.runId, telling);
      // Kept so a run that fails can bring the form back filled in.
      saveSentDraft(sessionStore(), res.runId, sentDraftFromSubmit(fields, photos.length));
      // An open run of this tab: /add offers the way back to it until it is over.
      rememberOpenRun(sessionStore(), { familyId: fid, runId: res.runId, title: secondTitle, startedAt: Date.now() });
      onStarted({ runId: res.runId, telling, photos });
    },
    [api, fid, secondTitle, onStarted],
  );

  return (
    <FlowPage
      title={second ? "Add your telling" : "Add a memory"}
      lede={
        // No lede: the story's placeholder carries the invitation. Only the
        // family line (two or more families) and, for a second telling from a
        // link, the way out to a new memory.
        <FamilyLede choice={choice} onSwitch={onSwitch} moment={momentHome} asking={askOthers ? asking : null}>
          {second && base.kind === "second" && !picked ? (
            <Link
              href={hrefs.add({ familyId: fid })}
              // The story typed so far goes along to the new memory.
              onClick={() => onSwitch(fid)}
              className={`rounded-paper text-[0.9375rem] font-medium text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2 ${FOCUS_RING}`}
            >
              Tell a different story instead
            </Link>
          ) : null}
        </FamilyLede>
      }
      back={BACK}
      width="wide"
    >
      <div className="flex flex-col gap-9">
        {openRun ? (
          <Notice>
            {openRun.notice}{" "}
            <Link
              href={runHref({ familyId: fid, runId: openRun.run.runId })}
              className={`rounded-paper font-semibold whitespace-nowrap text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2 ${FOCUS_RING}`}
            >
              Go back to it
            </Link>
          </Notice>
        ) : null}
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
          {photosNote ? <Notice>{photosNote}</Notice> : null}
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
            addPersonHref={second ? null : addRelativeHref(fid)}
            onAddPerson={onAddPerson}
            tickPersonId={second ? null : tickPersonId}
          />
        </div>
      </div>
    </FlowPage>
  );
}
