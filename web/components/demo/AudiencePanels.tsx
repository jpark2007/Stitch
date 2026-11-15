"use client";

// The control page's audience controls: where the audience window is and
// what it shows, the quick destinations, the family's people (each opens
// their book), and the live lists of moments and films (each "Show" opens it
// on the audience screen; one that arrived after this page loaded is marked
// "New" and floats to the top).
import type { ReactNode } from "react";

import type { FilmSummary, MomentSummary, Person } from "@/lib/types";

import { byYearDesc, isNew, withNewFirst, type Arrivals } from "./arrivals";
import { QUICK_DESTINATIONS, audienceHrefs, describeAudience, momentBookPerson } from "./audience";
import type { Loadable } from "./useLiveFamily";
import type { AudienceStatus } from "./useAudience";
import { Chip, Dot, Panel, Pill } from "./ui";

/** The audience window's state, for the top bar. */
export function AudienceStatusLine(props: { status: AudienceStatus; names: Record<string, string> }) {
  const { status, names } = props;
  let dot: "good" | "waiting" | "off" = "off";
  let words: ReactNode;
  if (status.kind === "open") {
    dot = "good";
    words = (
      <>
        <span className="font-semibold text-ink">Showing {status.location ? describeAudience(status.location, names) : "a page"}</span>
        {status.location && !status.location.startsWith("about:") ? (
          <code className="ml-2 truncate font-mono text-[0.75rem] text-ink-faint">{status.location}</code>
        ) : null}
      </>
    );
  } else if (status.kind === "closed") {
    words = <span>Closed. Open it again to carry on.</span>;
  } else if (status.kind === "blocked") {
    dot = "waiting";
    words = <span className="text-[#9c3b2a]">The browser blocked the window. Allow pop-ups for this site, then try again.</span>;
  } else {
    dot = "waiting";
    words = (
      <span>
        <span className="font-semibold text-ink">No audience window yet.</span>{" "}
        <span className="text-ink-faint">One left open before a reload is picked up again.</span>
      </span>
    );
  }
  return (
    <p role="status" aria-live="polite" className="flex min-w-0 items-center gap-2 text-sm text-ink-soft">
      <Dot tone={dot} />
      <span className="sr-only">Audience window: </span>
      <span className="flex min-w-0 items-baseline">{words}</span>
    </p>
  );
}

export function DestinationsPanel(props: { fid: string; go: (url: string) => void }) {
  const { fid, go } = props;
  return (
    <Panel title="Send the audience to" kicker="Audience screen">
      <div className="grid grid-cols-2 gap-2">
        {QUICK_DESTINATIONS.map((d) => (
          <Chip key={d.key} onClick={() => go(audienceHrefs[d.key](fid))} className="justify-start">
            {d.label}
          </Chip>
        ))}
        <Chip onClick={() => go(audienceHrefs.hold())} className="justify-start" title="The paper and the Stitch wordmark, for transitions">
          Blank the screen
        </Chip>
      </div>
    </Panel>
  );
}

export function PeoplePanel(props: { people: Loadable<Person[]>; fid: string; go: (url: string) => void; retry: () => void }) {
  const { people, fid, go, retry } = props;
  return (
    <Panel title="People" kicker="Open a book">
      {people.kind === "loading" ? (
        <Quiet>Loading the family…</Quiet>
      ) : people.kind === "error" ? (
        <div className="flex flex-wrap items-center gap-2">
          <p role="alert" className="text-sm text-[#9c3b2a]">{people.error}</p>
          <Chip onClick={retry}>Try again</Chip>
        </div>
      ) : people.value.length === 0 ? (
        <Quiet>No one is in this family’s tree yet.</Quiet>
      ) : (
        <ul className="grid list-none grid-cols-2 gap-1.5 p-0">
          {sortPeople(people.value).map((p) => (
            <li key={p.id} className="min-w-0">
              <button
                type="button"
                onClick={() => go(audienceHrefs.person(fid, p.id))}
                className="group flex min-h-9 w-full min-w-0 items-center gap-2 rounded-card px-2 py-1 text-left text-sm text-ink transition-colors duration-150 ease-paper hover:bg-paper-deep/70 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-dashed focus-visible:outline-accent"
                title={`Open ${p.name}’s book on the audience screen`}
              >
                <span
                  aria-hidden="true"
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: p.character?.color ?? "var(--ink-faint)" }}
                />
                <span className="truncate font-medium">{p.name}</span>
                {p.born ? <span className="ml-auto shrink-0 text-xs tabular-nums text-ink-faint">{p.born}</span> : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/** Oldest first, as on the tree: the grandparents lead. */
function sortPeople(people: Person[]): Person[] {
  return [...people].sort((a, b) => (a.born ?? 9999) - (b.born ?? 9999) || a.name.localeCompare(b.name));
}

export function MomentsPanel(props: {
  moments: Loadable<MomentSummary[]>;
  arrivals: Arrivals;
  fid: string;
  names: Record<string, string>;
  go: (url: string) => void;
}) {
  const { moments, arrivals, fid, names, go } = props;
  const fresh = moments.kind === "ready" ? moments.value.filter((m) => isNew(arrivals, m.id)).length : 0;
  return (
    <Panel
      title="Moments"
      kicker="Live · every 5 seconds"
      aside={fresh > 0 ? <Pill tone="new">{fresh} new</Pill> : null}
      className="flex min-h-0 flex-col"
    >
      {moments.kind === "loading" ? (
        <Quiet>Loading moments…</Quiet>
      ) : moments.kind === "error" ? (
        <p role="alert" className="text-sm text-[#9c3b2a]">{moments.error}</p>
      ) : (
        <>
          {moments.error ? <p role="status" className="mb-2 text-xs text-[#9c3b2a]">{moments.error}</p> : null}
          {moments.value.length === 0 ? (
            <Quiet>No moments yet. A story told on the add page, or by text or call, appears here.</Quiet>
          ) : (
            <ul className="-mx-1 max-h-[22rem] list-none overflow-y-auto p-0 xl:max-h-[max(14rem,calc(100dvh-34rem))]">
              {withNewFirst(moments.value, arrivals, byYearDesc).map((m) => {
                const pid = momentBookPerson(m);
                const nu = isNew(arrivals, m.id);
                return (
                  <li
                    key={m.id}
                    className={`flex items-center gap-3 rounded-card px-2 py-1.5 ${nu ? "bg-[#f7e9df] ring-1 ring-accent/35" : ""}`}
                  >
                    <span className="w-11 shrink-0 text-sm tabular-nums text-ink-faint">{m.year}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[0.9375rem] font-semibold text-ink">{m.title}</span>
                      {pid && names[pid] ? (
                        <span className="block truncate text-xs text-ink-faint">in {names[pid]}’s book</span>
                      ) : null}
                    </span>
                    {nu ? <Pill tone="new">New</Pill> : null}
                    <Chip tone={nu ? "accent" : "plain"} onClick={() => go(audienceHrefs.moment(fid, m.id, pid))} aria-label={`Show ${m.title}`}>
                      Show
                    </Chip>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}

export function FilmsPanel(props: { films: Loadable<FilmSummary[]>; arrivals: Arrivals; fid: string; go: (url: string) => void }) {
  const { films, arrivals, fid, go } = props;
  if (films.kind === "loading") return null;
  return (
    <Panel title="Films" kicker="Live">
      {films.kind === "error" ? (
        <p role="alert" className="text-sm text-[#9c3b2a]">{films.error}</p>
      ) : films.value.length === 0 ? (
        <Quiet>No films yet.</Quiet>
      ) : (
        <ul className="-mx-1 max-h-[10rem] list-none overflow-y-auto p-0">
          {withNewFirst(films.value, arrivals, (a, b) => b.createdAt.localeCompare(a.createdAt)).map((f) => {
            const nu = isNew(arrivals, f.id);
            return (
              <li key={f.id} className={`flex items-center gap-3 rounded-card px-2 py-1.5 ${nu ? "bg-[#f7e9df]" : ""}`}>
                <span className="w-11 shrink-0 text-sm tabular-nums text-ink-faint">{f.year}</span>
                <span className="min-w-0 flex-1 truncate text-[0.9375rem] font-semibold text-ink">{f.title}</span>
                {f.status !== "ready" ? (
                  <Pill tone={f.status === "processing" ? "waiting" : "off"}>{f.status === "processing" ? "Being made" : "Didn’t finish"}</Pill>
                ) : null}
                {nu ? <Pill tone="new">New</Pill> : null}
                <Chip onClick={() => go(audienceHrefs.film(fid, f.id))} aria-label={`Show ${f.title}`}>
                  Show
                </Chip>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

function Quiet(props: { children: ReactNode }) {
  return <p className="text-sm leading-relaxed text-ink-soft">{props.children}</p>;
}
