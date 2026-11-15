"use client";

// /films/new: the photos (as many as you like, up to a 1,000-photo album,
// dropped or chosen), the story in the teller's words, the year and an
// optional title. Stitch looks through the photos in the browser
// (components/film/analyze.ts) and keeps a shortlist of at most 40, no two
// alike, spread over the day (components/film/shortlist.ts); the person can
// take any out (the next best takes its place) or have Stitch choose again.
// Nothing of the album leaves the browser until the form is sent, and then
// only the shortlist: each photo through POST /families/{fid}/media, at most
// three at a time, each thumbnail showing its own progress; a photo that
// fails can be tried again (or removed) and, once every photo is up, the
// film starts by itself. Then POST /families/{fid}/films, and on to the
// film's page, which waits for it. Photos already sent are not sent again
// when the form is sent a second time.
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";

import { Button, ButtonLink } from "@/components/flow/Button";
import {
  FIELD_ERROR_CLASS,
  FIELD_ROW_CLASS,
  FIELD_ROW_LABEL_CLASS,
  OptionalMark,
  TextAreaField,
  fieldControlClass,
} from "@/components/flow/Field";
import { FlowPage } from "@/components/flow/FlowPage";
import { FormError, focusFirstError } from "@/components/flow/FormError";
import { PaperCard } from "@/components/flow/PaperCard";
import { SessionGate, type SessionValue } from "@/components/flow/SessionGate";
import { hrefs } from "@/components/flow/session";
import { CHECK_FIELDS_MESSAGE, type FieldErrors } from "@/components/flow/validation";
import { YearInput } from "@/components/flow/YearInput";
import { analyzePhotos, type PhotoLook } from "@/components/film/analyze";
import { FilmPhotoPicker, type Looking, type PickedPhoto } from "@/components/film/FilmPhotoPicker";
import {
  FILM_YEAR_MIN,
  MAX_ALBUM_PHOTOS,
  MAX_FILM_PHOTOS,
  MIN_FILM_PHOTOS,
  UPLOADS_IN_FLIGHT,
  acceptFilmPhotos,
  albumLine,
  filmRequest,
  filmServerErrors,
  runQueue,
  sentIds,
  tallyUploads,
  unreadableLine,
  uploadLine,
  validateFilmDraft,
  type FilmDraft,
  type FilmField,
} from "@/components/film/filmForm";
import { familyFromQuery, filmHref, filmsHref } from "@/components/film/filmLinks";
import { chooseAgain, planAlbumAsync, replaceTaken, signalsFromLooks, type AlbumPlan } from "@/components/film/shortlist";
import { useFilmFamily } from "@/components/film/useFilmFamily";
import { isApiError, type StitchApi } from "@/lib/api";

const TITLE = "Make a film";
const LEDE =
  "Pick the photos from one day or trip, the whole album if you like, then tell what happened. Stitch chooses the best of them, puts them in the order of your story and reads it in your words.";
const STILL_LOOKING = "Stitch is still looking through your photos. It won’t be long.";
/** Progress redraws at most this often while Stitch looks. */
const LOOKING_REDRAW_MS = 120;

/**
 * The pause between slices of choosing: a plain timer, which queues behind
 * React's own work, so the page redraws (and the story box keeps up) while a
 * big album is chosen from. scheduler.yield() would resume first and starve
 * React's renders.
 */
function pauseForPage(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}
const STORY_PLACEHOLDER =
  "Tell it the way you would at the kitchen table: where you went, who was there, what happened first and how it ended…";
// Large, comfortable serif for writing in, as on the add-a-memory page.
const STORY_CLASS = "font-serif! text-[1.1875rem]! leading-[1.65]! sm:text-[1.3125rem]! pb-3!";
const CREATE_ERROR = "The film didn’t start. Your photos and story are still here: try again.";

export function NewFilmLoading() {
  return (
    <FlowPage title={TITLE} lede={LEDE} width="wide" back={{ href: filmsHref(), label: "Films" }}>
      <div role="status" aria-busy="true" className="text-ink-soft">
        <span
          aria-hidden="true"
          className="block h-0 w-24 border-t-[1.5px] border-dashed border-ink-faint motion-safe:animate-pulse"
        />
        <p className="mt-3">Opening your family’s pages…</p>
      </div>
    </FlowPage>
  );
}

export function NewFilmScreen() {
  const familyId = familyFromQuery(useSearchParams());
  return (
    <SessionGate requireFamily width="wide" loading={<NewFilmLoading />}>
      {(session) => <NewFilm session={session} familyId={familyId} />}
    </SessionGate>
  );
}

function NewFilm(props: { session: SessionValue; familyId: string | null }) {
  const { session } = props;
  const { family, fid, api } = useFilmFamily(session, props.familyId);
  const back = { href: filmsHref(fid), label: "Films" };
  const lede =
    family && session.me.families.length > 1 ? (
      <>
        <p>
          For <span className="font-semibold text-ink">{family.name}</span>
        </p>
        <p className="mt-3 text-pretty">{LEDE}</p>
      </>
    ) : (
      LEDE
    );

  if (!family || !fid) return <NewFilmLoading />;

  if (!family.personId) {
    return (
      <FlowPage title={TITLE} width="wide" back={back}>
        <PaperCard className="flex flex-col items-start gap-4">
          <h2 className="font-display text-[1.375rem]! leading-tight text-ink">You&rsquo;re not in the tree yet</h2>
          <p className="text-[1.0625rem] leading-relaxed text-pretty text-ink-soft">
            Films are told by someone in {family.name}&rsquo;s tree. Find yourself in the family settings first, then
            come back to make yours.
          </p>
          <ButtonLink variant="secondary" href={hrefs.settings(fid)}>
            Open family settings
          </ButtonLink>
        </PaperCard>
      </FlowPage>
    );
  }

  return (
    <FlowPage title={TITLE} lede={lede} width="wide" back={back}>
      <FilmForm key={fid} api={api} fid={fid} />
    </FlowPage>
  );
}

type Phase = "editing" | "uploading" | "creating";

let keySeq = 0;

/** A friendly reason a photo did not send. */
function uploadError(e: unknown): string {
  if (!isApiError(e)) return "it didn’t send";
  if (e.status === 413) return "it’s too big";
  if (e.status === 415) return "it isn’t a kind we can use";
  if (e.status === 0) return "the connection dropped";
  return "it didn’t send";
}

function FilmForm(props: { api: StitchApi; fid: string }) {
  const { api, fid } = props;
  const router = useRouter();
  const formRef = useRef<HTMLFormElement>(null);
  const titleId = useId();

  // The photos live in state for rendering and in a ref for the upload
  // workers, which need the latest list between renders. Every change goes
  // through `update`, so the two never disagree.
  const [items, setItems] = useState<PickedPhoto[]>([]);
  const itemsRef = useRef<PickedPhoto[]>([]);
  const update = useCallback((fn: (list: PickedPhoto[]) => PickedPhoto[]) => {
    itemsRef.current = fn(itemsRef.current);
    setItems(itemsRef.current);
  }, []);

  const [draft, setDraft] = useState<FilmDraft>({ text: "", year: "", title: "" });
  const [errors, setErrors] = useState<FieldErrors<FilmField>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [phase, setPhase] = useState<Phase>("editing");
  const locked = phase !== "editing";

  const setField = (field: keyof FilmDraft, value: string) => {
    setDraft((d) => ({ ...d, [field]: value }));
    if (errors[field]) {
      setErrors((e) => {
        const next = { ...e };
        delete next[field];
        return next;
      });
    }
  };

  const signedOut = useCallback(
    (e: unknown): boolean => {
      if (!(isApiError(e) && e.status === 401)) return false;
      router.replace(hrefs.login(`${window.location.pathname}${window.location.search}`));
      return true;
    },
    [router],
  );

  // The album: every file picked so far, in pick order, and how each looked
  // (null: it could not be read). Only the shortlist is ever in `items`.
  const album = useRef<{ files: File[]; looks: (PhotoLook | null)[]; removed: number[]; plan: AlbumPlan | null }>({
    files: [],
    looks: [],
    removed: [],
    plan: null,
  });
  const [looking, setLooking] = useState<Looking | null>(null);
  const [albumSize, setAlbumSize] = useState(0);
  const lookingRef = useRef<AbortController | null>(null);
  const [summary, setSummary] = useState<{ considered: number; setAside: number; chose: boolean } | null>(null);
  const [canChooseAgain, setCanChooseAgain] = useState(false);
  // Leaving the page stops the looking.
  useEffect(() => () => lookingRef.current?.abort(), []);

  const clearPhotosError = useCallback(() => {
    if (itemsRef.current.length < MIN_FILM_PHOTOS) return;
    setErrors((e) => {
      if (!e.photos) return e;
      const next = { ...e };
      delete next.photos;
      return next;
    });
  }, []);

  /** The film's photos become `chosen` (album indices); photos staying in keep their upload state. */
  const show = useCallback(
    (chosen: readonly number[]) => {
      const files = album.current.files;
      update((list) => {
        const kept = new Map(list.map((i) => [i.index, i]));
        return chosen.map(
          (index) =>
            kept.get(index) ?? { key: `p${++keySeq}`, index, file: files[index], status: "waiting" as const, mediaId: null, error: null },
        );
      });
      clearPhotosError();
    },
    [update, clearPhotosError],
  );

  /** Show a plan for the whole album. */
  const applyPlan = useCallback(
    (plan: AlbumPlan) => {
      album.current.plan = plan;
      show(plan.chosen);
      setSummary({ considered: plan.album.photos.length, setAside: plan.setAside, chose: plan.mode === "album" });
      setCanChooseAgain(false);
    },
    [show],
  );

  /** The "Choose again" action: a fresh choice from the album already looked through, removed photos still out. */
  const rechoose = useCallback(() => {
    const plan = album.current.plan;
    if (!plan) return;
    applyPlan({ ...plan, ...chooseAgain(plan.album, plan.mode, album.current.removed, MAX_FILM_PHOTOS, MIN_FILM_PHOTOS) });
  }, [applyPlan]);

  const addFiles = (files: File[]) => {
    if (lookingRef.current) return;
    const a = album.current;
    const { accepted, errors: left } = acceptFilmPhotos(a.files, files, MAX_ALBUM_PHOTOS);
    setNotes(left);
    if (!accepted.length) return;
    const fresh = accepted.map((i) => files[i]);
    const control = new AbortController();
    lookingRef.current = control;
    setLooking({ done: 0, total: fresh.length });
    let drawn = 0;
    void analyzePhotos(fresh, {
      signal: control.signal,
      onProgress: (done, total) => {
        const now = performance.now();
        if (done < total && now - drawn < LOOKING_REDRAW_MS) return;
        drawn = now;
        setLooking({ done, total });
      },
    })
      .then(async (looks) => {
        if (control.signal.aborted) return;
        // Choosing can take a moment for a big album: it pauses for the page
        // between slices, and the panel says what it is doing.
        setLooking({ done: fresh.length, total: fresh.length, choosing: true });
        const files = [...a.files, ...fresh];
        const allLooks = [...a.looks, ...looks];
        const { photos, exactTimes } = signalsFromLooks(allLooks);
        const plan = await planAlbumAsync(
          photos,
          { exactTimes, removed: a.removed, max: MAX_FILM_PHOTOS, min: MIN_FILM_PHOTOS },
          pauseForPage,
        );
        if (control.signal.aborted) return;
        a.files = files;
        a.looks = allLooks;
        const unreadable = unreadableLine(fresh.filter((_, k) => !looks[k]).map((f) => f.name));
        if (unreadable) setNotes([...left, unreadable]);
        applyPlan(plan);
      })
      .catch(() => {
        // Stopped: the album stays as it was before this pick.
      })
      .finally(() => {
        if (lookingRef.current !== control) return;
        lookingRef.current = null;
        doneLooking();
      });
  };

  /** The "still looking" line goes with the looking. */
  const doneLooking = () => {
    setLooking(null);
    setAlbumSize(album.current.files.length);
    setErrors((e) => {
      if (e.photos !== STILL_LOOKING) return e;
      const next = { ...e };
      delete next.photos;
      return next;
    });
  };

  const stopLooking = () => {
    lookingRef.current?.abort();
    lookingRef.current = null;
    doneLooking();
  };

  const remove = (key: string) => {
    setNotes([]);
    const a = album.current;
    const item = itemsRef.current.find((i) => i.key === key);
    if (item && a.plan) {
      // Out it goes, with its look-alikes; the next best that is unlike
      // everything else takes its place.
      a.removed = [...a.removed, item.index];
      const next = replaceTaken(a.plan.album, itemsRef.current.map((i) => i.index), a.removed, item.index);
      show(next);
      if (a.plan.mode === "album") {
        const fresh = chooseAgain(a.plan.album, a.plan.mode, a.removed, MAX_FILM_PHOTOS, MIN_FILM_PHOTOS).chosen;
        setCanChooseAgain(fresh.join() !== next.join());
      }
    } else {
      update((list) => list.filter((i) => i.key !== key));
    }
    // The last failure removed: the line about it goes too.
    if (tallyUploads(itemsRef.current).failed === 0) setFormError(null);
  };

  /** Send one photo (unless it already went), recording how it went. */
  const uploadOne = useCallback(
    async (key: string) => {
      const item = itemsRef.current.find((i) => i.key === key);
      if (!item || (item.status === "sent" && item.mediaId)) return;
      update((list) => list.map((i) => (i.key === key ? { ...i, status: "sending", error: null } : i)));
      try {
        const { id } = await api.uploadMedia(fid, item.file);
        update((list) => list.map((i) => (i.key === key ? { ...i, status: "sent", mediaId: id, error: null } : i)));
      } catch (e) {
        if (signedOut(e)) return;
        update((list) => list.map((i) => (i.key === key ? { ...i, status: "failed", error: uploadError(e) } : i)));
      }
    },
    [api, fid, update, signedOut],
  );

  /** Every photo is up: start the film and go to its page. */
  const create = useCallback(
    async (ids: string[], current: FilmDraft) => {
      setPhase("creating");
      try {
        const film = await api.createFilm(fid, filmRequest(current, ids));
        router.push(filmHref(film.id, fid));
      } catch (e) {
        setPhase("editing");
        if (signedOut(e)) return;
        if (isApiError(e) && e.status === 422) {
          const { fields, rest } = filmServerErrors(e.fieldErrors);
          setErrors(fields);
          setFormError(rest ?? (Object.keys(fields).length ? null : e.detail || CHECK_FIELDS_MESSAGE));
        } else {
          setFormError(isApiError(e) && e.status !== 0 ? e.detail : CREATE_ERROR);
        }
        focusFirstError(formRef.current);
      }
    },
    [api, fid, router, signedOut],
  );

  /** Upload what is not up yet, then start the film; or stop and say which photos did not send. */
  const send = useCallback(
    async (current: FilmDraft) => {
      setFormError(null);
      setPhase("uploading");
      update((list) => list.map((i) => (i.status === "sent" ? i : { ...i, status: "waiting", error: null })));
      const keys = itemsRef.current.filter((i) => i.status !== "sent").map((i) => i.key);
      await runQueue(keys, uploadOne, UPLOADS_IN_FLIGHT);
      const ids = sentIds(itemsRef.current);
      if (ids) {
        await create(ids, current);
        return;
      }
      setPhase("editing");
      setFormError(uploadLine(tallyUploads(itemsRef.current)));
    },
    [create, update, uploadOne],
  );

  const check = (): boolean => {
    const found = validateFilmDraft(draft, itemsRef.current.length);
    setErrors(found);
    if (Object.keys(found).length) {
      setFormError(null);
      focusFirstError(formRef.current);
      return false;
    }
    return true;
  };

  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (locked) return;
    if (lookingRef.current) {
      setErrors((found) => ({ ...found, photos: STILL_LOOKING }));
      setFormError(null);
      return;
    }
    if (!check()) return;
    void send(draft);
  };

  /** One failed photo tried again; if that was the last one missing, the film starts. */
  const retry = (key: string) => {
    if (locked) return;
    setFormError(null);
    setPhase("uploading");
    void uploadOne(key).then(() => {
      const ids = sentIds(itemsRef.current);
      if (ids && check()) {
        void create(ids, draft);
        return;
      }
      setPhase("editing");
      const t = tallyUploads(itemsRef.current);
      if (t.failed) setFormError(uploadLine(t));
    });
  };

  const tally = tallyUploads(items);
  const progress = phase === "uploading" ? uploadLine(tally) : phase === "creating" ? "Starting the film…" : "";

  return (
    <form ref={formRef} onSubmit={onSubmit} noValidate aria-busy={locked || undefined} className="flex flex-col">
      <FilmPhotoPicker
        items={items}
        onAdd={addFiles}
        onRemove={remove}
        onRetry={retry}
        locked={locked}
        notes={notes}
        error={errors.photos}
        looking={looking}
        onStopLooking={stopLooking}
        summary={summary ? albumLine({ ...summary, chosen: items.length }) : null}
        onChooseAgain={canChooseAgain ? rechoose : null}
        albumSize={albumSize}
      />

      <fieldset disabled={locked} className="m-0 mt-12 min-w-0 border-0 p-0">
        <TextAreaField
          label="Your story"
          labelHidden
          placeholder={STORY_PLACEHOLDER}
          required
          name="text"
          rows={6}
          value={draft.text}
          onChange={(e) => setField("text", e.target.value)}
          error={errors.text}
          className={STORY_CLASS}
        />

        <div className="mt-10 space-y-5 sm:space-y-3">
          <YearInput
            layout="row"
            label="When was this?"
            required
            name="year"
            min={FILM_YEAR_MIN}
            value={draft.year}
            onChange={(v) => setField("year", v)}
            error={errors.year}
          />
          <div className={FIELD_ROW_CLASS}>
            <label htmlFor={titleId} className={FIELD_ROW_LABEL_CLASS}>
              {/* One inline run: the label is a flex row from sm up, which would drop the space. */}
              <span>
                Title
                <OptionalMark />
              </span>
            </label>
            <div className="min-w-0">
              <input
                id={titleId}
                name="title"
                type="text"
                autoComplete="off"
                placeholder="Stitch names it if you don’t"
                value={draft.title}
                onChange={(e) => setField("title", e.target.value)}
                aria-invalid={errors.title ? true : undefined}
                aria-describedby={errors.title ? `${titleId}-error` : undefined}
                className={fieldControlClass(!!errors.title)}
              />
              {errors.title ? (
                <p id={`${titleId}-error`} className={`mt-1.5 ${FIELD_ERROR_CLASS}`}>
                  {errors.title}
                </p>
              ) : null}
            </div>
          </div>
        </div>
      </fieldset>

      <div className="mt-12 space-y-4">
        <FormError message={formError} />
        <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:gap-5">
          <Button
            type="submit"
            busy={locked}
            busyLabel={phase === "creating" ? "Starting the film" : "Sending photos"}
          >
            Make the film
          </Button>
          <p role="status" className="text-[0.9375rem] text-ink-soft tabular-nums">
            {progress}
          </p>
        </div>
      </div>
    </form>
  );
}
