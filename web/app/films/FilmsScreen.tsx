"use client";

// /films: the family's films as a loose grid of taped prints (the cover photo,
// then title, year and length), newest first, with "Make a film" beside the
// title. A film still being made shows so; one that failed says it did not
// finish. No films yet: one sheet that says what a film is and how to start.
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Button, ButtonLink, FOCUS_RING } from "@/components/flow/Button";
import { FormError } from "@/components/flow/FormError";
import { PaperCard } from "@/components/flow/PaperCard";
import { SessionGate, type SessionValue } from "@/components/flow/SessionGate";
import { hrefs } from "@/components/flow/session";
import { FilmPage, QuietSheet } from "@/components/film/FilmPage";
import { Tape } from "@/components/film/Tape";
import { familyFromQuery, filmHref, newFilmHref } from "@/components/film/filmLinks";
import { spokenLength } from "@/components/film/timeline";
import { useFilmFamily } from "@/components/film/useFilmFamily";
import { isApiError, type StitchApi } from "@/lib/api";
import type { FilmSummary } from "@/lib/types";

const TITLE = "Films";
const BACK = { href: hrefs.home(), label: "Family tree" };
const LOAD_ERROR = "We couldn’t open your family’s films. Check your connection and try again.";

export function FilmsLoading() {
  return (
    <FilmPage title={TITLE} back={BACK}>
      <QuietSheet label="Opening your family’s films…" />
    </FilmPage>
  );
}

export function FilmsScreen() {
  const params = useSearchParams();
  const familyId = familyFromQuery(params);
  return (
    <SessionGate requireFamily width="wide" loading={<FilmsLoading />}>
      {(session) => <FilmsList session={session} familyId={familyId} />}
    </SessionGate>
  );
}

type Loaded = { fid: string; kind: "ready"; films: FilmSummary[] } | { fid: string; kind: "error"; message: string };

function FilmsList(props: { session: SessionValue; familyId: string | null }) {
  const { family, fid, api } = useFilmFamily(props.session, props.familyId);
  const router = useRouter();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!fid) return;
    let live = true;
    api.listFilms(fid).then(
      (r) => {
        if (live) setLoaded({ fid, kind: "ready", films: r.films });
      },
      (e: unknown) => {
        if (!live) return;
        if (isApiError(e) && e.status === 401) {
          router.replace(hrefs.login(`${window.location.pathname}${window.location.search}`));
          return;
        }
        setLoaded({ fid, kind: "error", message: isApiError(e) && e.status !== 0 ? e.detail : LOAD_ERROR });
      },
    );
    return () => {
      live = false;
    };
  }, [api, fid, attempt, router]);

  const shown = loaded && loaded.fid === fid ? loaded : null;
  const lede = family && props.session.me.families.length > 1 ? `In ${family.name}` : null;
  const make = fid ? <ButtonLink href={newFilmHref(fid)}>Make a film</ButtonLink> : null;

  if (!fid || !shown) {
    return (
      <FilmPage title={TITLE} lede={lede} back={BACK}>
        <QuietSheet label="Opening your family’s films…" />
      </FilmPage>
    );
  }

  if (shown.kind === "error") {
    return (
      <FilmPage title={TITLE} lede={lede} back={BACK}>
        <PaperCard className="flex flex-col items-start gap-5">
          <FormError message={shown.message} />
          <Button onClick={() => setAttempt((n) => n + 1)}>Try again</Button>
        </PaperCard>
      </FilmPage>
    );
  }

  if (shown.films.length === 0) {
    return (
      <FilmPage title={TITLE} lede={lede} back={BACK}>
        <PaperCard className="flex max-w-[34rem] flex-col items-start gap-4">
          <h2 className="font-display text-[1.375rem]! leading-tight text-ink">No films yet</h2>
          <p className="text-[1.0625rem] leading-relaxed text-pretty text-ink-soft">
            Pick the photos from one day or trip and tell what happened. Stitch chooses the best of them, puts them in
            the order of your story and reads it in your words.
          </p>
          {make}
        </PaperCard>
      </FilmPage>
    );
  }

  return (
    <FilmPage title={TITLE} lede={lede} back={BACK} action={make}>
      <ul className="grid list-none grid-cols-1 gap-x-10 gap-y-14 p-0 pt-3 sm:grid-cols-2 lg:grid-cols-3">
        {shown.films.map((f, i) => (
          <li key={f.id}>
            <FilmCover film={f} fid={fid} api={api} tilt={TILTS[i % TILTS.length]} />
          </li>
        ))}
      </ul>
    </FilmPage>
  );
}

// A loose hand-placed look: each print sits a little differently.
const TILTS = ["-rotate-[1.2deg]", "rotate-[0.9deg]", "-rotate-[0.4deg]", "rotate-[1.4deg]", "-rotate-[0.8deg]"];

function FilmCover(props: { film: FilmSummary; fid: string; api: StitchApi; tilt: string }) {
  const { film, fid, api, tilt } = props;
  const cover = film.coverPhotoId ? api.mediaUrl(fid, film.coverPhotoId) : null;
  return (
    <Link href={filmHref(film.id, fid)} className={`group block rounded-paper no-underline ${FOCUS_RING}`}>
      <div
        className={`relative bg-paper-light p-2 pb-2 shadow-lift transition-[transform,box-shadow] duration-300 ease-paper group-hover:-translate-y-1 group-hover:shadow-float motion-reduce:transition-none ${tilt}`}
      >
        <Tape className="-top-3 left-1/2 -translate-x-1/2 -rotate-[3deg]" />
        <div className="relative aspect-[3/2] overflow-hidden bg-paper-deep">
          {cover ? (
            // Family media (and mock blob URLs): next/image cannot optimise these.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={cover} alt="" loading="lazy" decoding="async" className="h-full w-full object-cover" />
          ) : null}
          {film.status === "ready" ? (
            <span
              aria-hidden="true"
              className="absolute right-2 bottom-2 flex size-9 items-center justify-center rounded-full bg-paper-light/90 text-accent-deep opacity-0 shadow-paper transition-opacity duration-200 group-hover:opacity-100 group-focus-visible:opacity-100"
            >
              <svg viewBox="0 0 20 20" className="ml-0.5 size-4">
                <path d="M6 4.2v11.6a.6.6 0 0 0 .92.5l9.1-5.8a.6.6 0 0 0 0-1L6.92 3.7A.6.6 0 0 0 6 4.2Z" fill="currentColor" />
              </svg>
            </span>
          ) : (
            <span className="absolute inset-0 bg-paper-light/35" aria-hidden="true" />
          )}
        </div>
      </div>
      <h2 className="mt-4 font-display text-[1.25rem]! leading-snug text-balance text-ink group-hover:text-accent-deep">
        {film.title}
      </h2>
      <p className="mt-1 flex flex-wrap items-center gap-x-2 text-sm text-ink-soft">
        <span className="tabular-nums">{film.year}</span>
        <span aria-hidden="true">&middot;</span>
        {film.status === "ready" ? (
          <span>{spokenLength(film.duration)}</span>
        ) : film.status === "processing" ? (
          <span className="inline-flex items-center gap-2">
            <span
              aria-hidden="true"
              className="block h-0 w-6 border-t-[1.5px] border-dashed border-accent motion-safe:animate-pulse"
            />
            Being made
          </span>
        ) : (
          <span className="text-[#9c3b2a]">Didn’t finish</span>
        )}
      </p>
    </Link>
  );
}
