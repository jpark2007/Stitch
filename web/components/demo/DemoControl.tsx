"use client";

// /demo?family=<fid>: backstage. One operator laptop runs the live demo from
// here while a second window (the audience window, dragged to the projector)
// shows what the audience sees. Not linked from anywhere. Behind the same
// session gate as the flow pages.
//
// Top bar: where the audience window is and what it shows. Left: the quick
// destinations, the family's people, its films. Middle: the family's moments,
// live, "Show" for each; the prepared stories. Right: this week's question,
// by phone.
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo } from "react";

import { FOCUS_RING } from "@/components/flow/Button";
import { SessionGate, type SessionValue } from "@/components/flow/SessionGate";
import { familyFromQuery } from "@/components/film/filmLinks";
import { useFilmFamily } from "@/components/film/useFilmFamily";

import { audienceHrefs } from "./audience";
import {
  AudienceStatusLine,
  DestinationsPanel,
  FilmsPanel,
  MomentsPanel,
  PeoplePanel,
} from "./AudiencePanels";
import { PhonePanel } from "./PhonePanel";
import { StoryLibrary } from "./StoryLibrary";
import { Chip } from "./ui";
import { useAudience } from "./useAudience";
import { useLiveFamily } from "./useLiveFamily";

export function DemoLoading() {
  return (
    <Shell>
      <p role="status" className="mt-10 text-center font-display text-lg text-ink-soft">
        Opening backstage…
      </p>
    </Shell>
  );
}

export function DemoControl() {
  const params = useSearchParams();
  const familyId = familyFromQuery(params);
  return (
    <SessionGate requireFamily width="wide" loading={<DemoLoading />}>
      {(session) => <Backstage session={session} familyId={familyId} />}
    </SessionGate>
  );
}

function Shell(props: { family?: string | null; bar?: React.ReactNode; children: React.ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-[90rem] min-w-0 overflow-x-clip px-4 pt-4 pb-10 font-sans text-ink sm:px-6">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-dashed border-rule pb-3">
        <div className="flex min-w-0 items-baseline gap-3">
          <Link
            href="/"
            className={`kicker -mx-1 rounded-paper px-1 no-underline transition-colors duration-150 ease-paper hover:text-ink ${FOCUS_RING}`}
          >
            Stitch
          </Link>
          <h1 className="font-display text-[1.625rem]! leading-none tracking-[-0.01em] text-ink">Backstage</h1>
          {props.family ? <span className="truncate text-sm text-ink-soft">{props.family}</span> : null}
        </div>
        {props.bar}
      </header>
      {props.children}
    </main>
  );
}

function Backstage(props: { session: SessionValue; familyId: string | null }) {
  const { family, fid, api } = useFilmFamily(props.session, props.familyId);
  const audience = useAudience();
  const live = useLiveFamily(api, fid);

  const people = live.people.kind === "ready" ? live.people.value : null;
  const names = useMemo(() => Object.fromEntries((people ?? []).map((p) => [p.id, p.name])), [people]);

  if (!family || !fid) {
    return (
      <Shell>
        <p className="mt-10 text-center text-ink-soft">This family isn’t one of yours. Check the link’s ?family=.</p>
      </Shell>
    );
  }

  const go = (url: string) => audience.go(url);
  const status = audience.status;
  const connected = status.kind === "open";

  return (
    <Shell
      family={family.name}
      bar={
        <div className="flex min-w-0 basis-full flex-wrap items-center justify-end gap-x-4 gap-y-2 lg:flex-1 lg:basis-auto">
          <div className="min-w-0 flex-1 basis-[18rem]">
            <AudienceStatusLine status={status} names={names} />
          </div>
          <div className="flex shrink-0 gap-2">
            {connected ? (
              <Chip onClick={() => go(audienceHrefs.hold())}>Blank the screen</Chip>
            ) : null}
            <Chip tone={connected ? "plain" : "accent"} onClick={() => audience.open(audienceHrefs.home(fid))}>
              {connected ? "Bring audience window forward" : "Open audience window"}
            </Chip>
          </div>
        </div>
      }
    >
      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-[minmax(0,0.85fr)_minmax(0,1.1fr)_minmax(0,1.15fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <DestinationsPanel fid={fid} go={go} />
          <PeoplePanel people={live.people} fid={fid} go={go} retry={live.reloadPeople} />
          <FilmsPanel films={live.films} arrivals={live.filmArrivals} fid={fid} go={go} />
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <MomentsPanel moments={live.moments} arrivals={live.momentArrivals} fid={fid} names={names} go={go} />
          <StoryLibrary fid={fid} go={go} />
        </div>
        <div className="flex min-w-0 flex-col gap-4 md:col-span-2 xl:col-span-1">
          <PhonePanel key={fid} api={api} family={family} people={people} />
        </div>
      </div>
    </Shell>
  );
}
