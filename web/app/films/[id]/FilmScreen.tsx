"use client";

// /films/[id]: GET the film, and while it is "processing" ask again every two
// seconds behind a calm waiting sheet (a small pile of its photos and what is
// happening, in honest general words: no made-up percentages). "ready" shows
// the player; "failed" a plain message with the way to try again. A film that
// is not in this family is "not found". A check that fails while waiting is
// tried again quietly; only a failed first load shows an error.
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Button, ButtonLink, FOCUS_RING } from "@/components/flow/Button";
import { FormError } from "@/components/flow/FormError";
import { PaperCard } from "@/components/flow/PaperCard";
import { SessionGate, type SessionValue } from "@/components/flow/SessionGate";
import { hrefs } from "@/components/flow/session";
import { FilmPage, QuietSheet } from "@/components/film/FilmPage";
import { FilmPlayer } from "@/components/film/FilmPlayer";
import { Tape } from "@/components/film/Tape";
import { familyFromQuery, filmsHref, newFilmHref } from "@/components/film/filmLinks";
import { isPlayable, spokenLength } from "@/components/film/timeline";
import { useFilmFamily } from "@/components/film/useFilmFamily";
import { isApiError, type StitchApi } from "@/lib/api";
import type { Film } from "@/lib/types";

/** How often a film being made is checked. */
const POLL_MS = 2000;
const LOAD_ERROR = "We couldn’t open this film. Check your connection and try again.";

export function FilmLoading() {
  return (
    <FilmPage title="Your film" back={{ href: filmsHref(), label: "Films" }}>
      <QuietSheet label="Opening the film…" />
    </FilmPage>
  );
}

export function FilmScreen() {
  const params = useParams<{ id: string }>();
  const id = typeof params?.id === "string" ? params.id : "";
  const familyId = familyFromQuery(useSearchParams());
  return (
    <SessionGate requireFamily width="wide" loading={<FilmLoading />}>
      {(session) => <FilmView key={id} session={session} familyId={familyId} id={id} />}
    </SessionGate>
  );
}

type Loaded =
  | { key: string; kind: "film"; film: Film }
  | { key: string; kind: "missing" }
  | { key: string; kind: "error"; message: string };

function FilmView(props: { session: SessionValue; familyId: string | null; id: string }) {
  const { session, familyId, id } = props;
  const { fid, api } = useFilmFamily(session, familyId);
  const router = useRouter();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [attempt, setAttempt] = useState(0);
  const key = `${fid}:${id}`;

  useEffect(() => {
    if (!fid || !id) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let had = false;
    const load = () => {
      api.getFilm(fid, id).then(
        (film) => {
          if (!live) return;
          had = true;
          setLoaded({ key, kind: "film", film });
          if (film.status === "processing") timer = setTimeout(load, POLL_MS);
        },
        (e: unknown) => {
          if (!live) return;
          if (isApiError(e) && e.status === 401) {
            router.replace(hrefs.login(`${window.location.pathname}${window.location.search}`));
          } else if (isApiError(e) && e.status === 404) {
            setLoaded({ key, kind: "missing" });
          } else if (had) {
            // Waiting already: a dropped check is tried again, a little later.
            timer = setTimeout(load, POLL_MS * 2);
          } else {
            setLoaded({ key, kind: "error", message: isApiError(e) && e.status !== 0 ? e.detail : LOAD_ERROR });
          }
        },
      );
    };
    load();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [api, fid, id, key, attempt, router]);

  const back = { href: filmsHref(fid), label: "Films" };
  const shown = loaded && loaded.key === key ? loaded : null;

  if (!fid || !shown) {
    return (
      <FilmPage title="Your film" back={back}>
        <QuietSheet label="Opening the film…" />
      </FilmPage>
    );
  }

  if (shown.kind === "missing") {
    return (
      <FilmPage title="We couldn’t find that film" back={back}>
        <PaperCard className="flex max-w-[34rem] flex-col items-start gap-4">
          <p className="text-[1.0625rem] leading-relaxed text-pretty text-ink-soft">
            It may be in another family, or the link may be missing a part.
          </p>
          <ButtonLink variant="secondary" href={filmsHref(fid)}>
            See your family’s films
          </ButtonLink>
        </PaperCard>
      </FilmPage>
    );
  }

  if (shown.kind === "error") {
    return (
      <FilmPage title="Your film" back={back}>
        <PaperCard className="flex flex-col items-start gap-5">
          <FormError message={shown.message} />
          <Button onClick={() => setAttempt((n) => n + 1)}>Try again</Button>
        </PaperCard>
      </FilmPage>
    );
  }

  const { film } = shown;

  if (film.status === "failed") {
    return (
      <FilmPage title={film.title} back={back}>
        <PaperCard className="flex max-w-[36rem] flex-col items-start gap-4">
          <h2 className="font-display text-[1.375rem]! leading-tight text-ink">This film couldn’t be made</h2>
          <p className="text-[1.0625rem] leading-relaxed text-pretty text-ink-soft">
            {film.error?.trim() || "Something went wrong while it was being put together."}
          </p>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <ButtonLink href={newFilmHref(fid)}>Try again</ButtonLink>
            <Link
              href={filmsHref(fid)}
              className={`rounded-paper py-3 text-[0.9375rem] font-medium text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2 ${FOCUS_RING}`}
            >
              Back to your films
            </Link>
          </div>
        </PaperCard>
      </FilmPage>
    );
  }

  if (film.status === "processing" || !isPlayable(film)) {
    return (
      <FilmPage title={film.title} back={back}>
        <Waiting film={film} fid={fid} api={api} />
      </FilmPage>
    );
  }

  return <ReadyFilm film={film} fid={fid} api={api} back={back} />;
}

/** The meta line and the player, with the teller's name once the tree has loaded. */
function ReadyFilm(props: { film: Film; fid: string; api: StitchApi; back: { href: string; label: string } }) {
  const { film, fid, api, back } = props;
  const [teller, setTeller] = useState<{ fid: string; name: string } | null>(null);
  useEffect(() => {
    let live = true;
    api.getTree(fid).then(
      (tree) => {
        const name = tree.people.find((p) => p.id === film.authorId)?.name;
        if (live && name) setTeller({ fid, name });
      },
      () => {
        // The name is a nicety: without it the line reads year and length.
      },
    );
    return () => {
      live = false;
    };
  }, [api, fid, film.authorId]);
  const tellerName = teller?.fid === fid ? teller.name : null;

  return (
    <FilmPage
      title={film.title}
      back={back}
      lede={
        <p className="flex flex-wrap items-center gap-x-2 text-[0.9375rem]">
          <span className="tabular-nums">{film.year}</span>
          {tellerName ? (
            <>
              <span aria-hidden="true">&middot;</span>
              <span>Told by {tellerName}</span>
            </>
          ) : null}
          <span aria-hidden="true">&middot;</span>
          <span>{spokenLength(film.duration)}</span>
        </p>
      }
    >
      <div className="pt-2">
        <FilmPlayer film={film} mediaUrl={(mediaId) => api.mediaUrl(fid, mediaId)} />
      </div>
      <p className="mt-6 hidden text-center text-sm text-ink-soft sm:block">
        Space plays and pauses &middot; the arrow keys skip 5 seconds
      </p>
    </FilmPage>
  );
}

// Where each print in the waiting pile lies: offset (rem) and turn (deg).
const PILE = [
  { x: -3.2, y: 0.6, r: -7 },
  { x: 2.8, y: -0.4, r: 5 },
  { x: -0.8, y: 1.4, r: -2 },
  { x: 1.6, y: 0.2, r: 8 },
  { x: 0, y: -0.2, r: -3.5 },
];

/** While the film is being made: a small pile of its photos and what is happening, calmly. */
function Waiting(props: { film: Film; fid: string; api: StitchApi }) {
  const { film, fid, api } = props;
  const pile = film.photoIds.slice(0, PILE.length);
  const count = film.photoIds.length;
  return (
    <div role="status" aria-live="polite" className="flex flex-col items-center gap-12 pt-4 sm:flex-row sm:items-center sm:gap-16">
      <div aria-hidden="true" className="relative h-52 w-64 shrink-0 sm:h-60 sm:w-72">
        {pile.map((photoId, i) => {
          const at = PILE[i];
          return (
            <div
              key={photoId}
              className="absolute top-1/2 left-1/2 w-40 bg-paper-light p-1.5 pb-5 shadow-lift sm:w-44"
              style={{ transform: `translate(-50%, -50%) translate(${at.x}rem, ${at.y}rem) rotate(${at.r}deg)` }}
            >
              {i === pile.length - 1 ? <Tape className="-top-3 left-1/2 -translate-x-1/2 rotate-[2deg]" /> : null}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={api.mediaUrl(fid, photoId)} alt="" decoding="async" className="aspect-[4/3] w-full bg-paper-deep object-cover" />
            </div>
          );
        })}
      </div>

      <div className="max-w-[30rem] min-w-0">
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className="block h-0 w-10 border-t-2 border-dashed border-accent motion-safe:animate-pulse"
          />
          <p className="kicker">Being made</p>
        </div>
        <h2 className="mt-3 font-display text-[1.6rem]! leading-tight text-ink sm:text-[1.9rem]!">
          Stitch is making your film
        </h2>
        <ul className="mt-5 list-none space-y-2.5 p-0 text-[1.0625rem] leading-snug text-ink-soft">
          <li className="flex gap-3">
            <span aria-hidden="true" className="mt-[0.6em] block h-0 w-3 shrink-0 border-t-[1.5px] border-ink-faint" />
            Choosing the best of your {count} {count === 1 ? "photo" : "photos"}
          </li>
          <li className="flex gap-3">
            <span aria-hidden="true" className="mt-[0.6em] block h-0 w-3 shrink-0 border-t-[1.5px] border-ink-faint" />
            Putting them in the order of your story
          </li>
          <li className="flex gap-3">
            <span aria-hidden="true" className="mt-[0.6em] block h-0 w-3 shrink-0 border-t-[1.5px] border-ink-faint" />
            Recording the narration in your words
          </li>
        </ul>
        <p className="mt-6 text-[0.9375rem] leading-relaxed text-pretty text-ink-soft">
          This usually takes a minute or two. The film appears here when it&rsquo;s ready, or you can leave: it will be
          with{" "}
          <Link
            href={filmsHref(fid)}
            className={`rounded-paper font-medium text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2 ${FOCUS_RING}`}
          >
            your family&rsquo;s films
          </Link>
          .
        </p>
      </div>
    </div>
  );
}
