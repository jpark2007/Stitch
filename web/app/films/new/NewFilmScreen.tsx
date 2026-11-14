"use client";

// /films/new: the photos (8 to 40, dropped or chosen), the story in the
// teller's words, the year and an optional title. Sending first uploads each
// photo through POST /families/{fid}/media, at most three at a time, each
// thumbnail showing its own progress; a photo that fails can be tried again
// (or removed) and, once every photo is up, the film starts by itself. Then
// POST /families/{fid}/films, and on to the film's page, which waits for it.
// Photos already sent are not sent again when the form is sent a second time.
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useId, useRef, useState, type FormEvent } from "react";

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
import { FilmPhotoPicker, type PickedPhoto } from "@/components/film/FilmPhotoPicker";
import {
  FILM_YEAR_MIN,
  MIN_FILM_PHOTOS,
  UPLOADS_IN_FLIGHT,
  acceptFilmPhotos,
  filmRequest,
  filmServerErrors,
  runQueue,
  sentIds,
  tallyUploads,
  uploadLine,
  validateFilmDraft,
  type FilmDraft,
  type FilmField,
} from "@/components/film/filmForm";
import { familyFromQuery, filmHref, filmsHref } from "@/components/film/filmLinks";
import { useFilmFamily } from "@/components/film/useFilmFamily";
import { isApiError, type StitchApi } from "@/lib/api";

const TITLE = "Make a film";
const LEDE =
  "Pick the photos from one day or trip, then tell what happened. Stitch chooses the best of them, puts them in the order of your story and reads it in your words.";
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

  const addFiles = (files: File[]) => {
    const { accepted, errors: left } = acceptFilmPhotos(
      itemsRef.current.map((i) => i.file),
      files,
    );
    setNotes(left);
    if (!accepted.length) return;
    update((list) => [
      ...list,
      ...accepted.map((i) => ({ key: `p${++keySeq}`, file: files[i], status: "waiting" as const, mediaId: null, error: null })),
    ]);
    if (errors.photos && itemsRef.current.length >= MIN_FILM_PHOTOS) {
      setErrors((e) => {
        const next = { ...e };
        delete next.photos;
        return next;
      });
    }
  };

  const remove = (key: string) => {
    setNotes([]);
    update((list) => list.filter((i) => i.key !== key));
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
    if (locked || !check()) return;
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
