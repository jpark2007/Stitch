"use client";

/**
 * The threads screen with its data: the signed-in family's threads from the
 * API, the local demo family (chosen, in mock mode, or when the API is out
 * of reach, with a quiet note saying so), and the paper sheets for being
 * signed out, having no family yet, an empty family, or an error.
 */

import Link from "next/link";
import { useMemo } from "react";
import { Button, ButtonLink } from "@/components/flow/Button";
import { hrefs } from "@/components/flow/session";
import { demoFamily, demoMoments, demoPeople } from "@/components/tree/demoFamily";
import type { FamilySummary } from "@/lib/types";
import type { LooseMoment } from "./data";
import { momentHref } from "./links";
import { MenuDisclosure } from "./MenuDisclosure";
import type { ThreadMoment, ThreadPerson } from "./model";
import ThreadsView from "./ThreadsView";
import { ThreadsSheet, ThreadsSkeleton } from "./ThreadsPaper";
import { useThreadsSource, type DemoReason } from "./useThreadsSource";

export interface ThreadsScreenProps {
  /** Raw query params; see ThreadsView for zoom, focus and highlight. */
  zoom?: string;
  focus?: string;
  highlight?: string;
  /** ?family=<id>: open this family (when the user belongs to it). */
  family?: string;
  /** ?demo=1: show the demo family without asking the API. */
  demo?: boolean;
}

const demoThreadPeople: ThreadPerson[] = demoPeople.map((p) => ({
  id: p.id,
  name: p.name,
  born: p.born,
  parentIds: p.parentIds,
  spouseIds: p.spouseIds,
  color: p.character.color,
}));

const demoThreadMoments: ThreadMoment[] = demoMoments.map((m) => ({
  id: m.id,
  title: m.title,
  year: m.year,
  participantIds: m.participantIds,
}));

/** This page's path, to come back to after signing in. */
function here(): string {
  if (typeof window === "undefined") return "/threads";
  const url = new URL(window.location.href);
  url.searchParams.delete("demo");
  return `${url.pathname}${url.search}`;
}

/** The quiet "or explore the demo family" line under a sheet's main action. */
function DemoLink(props: { onClick: () => void; lead?: string }) {
  return (
    <p className="mt-5 text-[0.95rem] text-ink-faint">
      {props.lead ?? "or"}{" "}
      <button
        type="button"
        onClick={props.onClick}
        className="cursor-pointer font-semibold text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2"
      >
        explore the demo family
      </button>
    </p>
  );
}

export default function ThreadsScreen(props: ThreadsScreenProps) {
  const { source, switched, retry, exploreDemo, switchFamily } = useThreadsSource({
    family: props.family,
    demo: props.demo,
  });

  const viewSeed = { zoom: props.zoom, focus: props.focus, highlight: props.highlight };

  switch (source.kind) {
    case "loading":
      return <ThreadsSkeleton />;

    case "signed-out":
      return (
        <ThreadsSheet title="Your family, stitched along time" label="Sign in to see your threads">
          <p>
            Sign in to see everyone&rsquo;s thread: a line for each person, knotted wherever they shared a moment.
          </p>
          <div className="mt-6">
            <ButtonLink href={hrefs.login(here())}>Sign in</ButtonLink>
          </div>
          <DemoLink onClick={exploreDemo} />
        </ThreadsSheet>
      );

    case "no-family":
      return (
        <ThreadsSheet title="No family here yet">
          <p>Start your family, or join one with an invite link, and their threads will gather here.</p>
          <div className="mt-6">
            <ButtonLink href={hrefs.newFamily()}>Start a family</ButtonLink>
          </div>
          <DemoLink onClick={exploreDemo} />
        </ThreadsSheet>
      );

    case "error":
      return (
        <ThreadsSheet title="The threads got tangled">
          <p role="alert">{source.message}</p>
          <div className="mt-6">
            <Button onClick={retry}>Try again</Button>
          </div>
          {source.families && source.families.length > 1 ? (
            <OtherFamilies families={source.families} currentId={source.familyId} onSwitch={switchFamily} />
          ) : (
            <DemoLink onClick={exploreDemo} />
          )}
        </ThreadsSheet>
      );

    case "demo":
      return (
        <ThreadsView
          key="demo"
          demo
          familyName={demoFamily.name}
          people={demoThreadPeople}
          moments={demoThreadMoments}
          note={<DemoNote why={source.why} onRetry={retry} />}
          {...viewSeed}
        />
      );

    case "family":
      // The URL's view seeds belong to the family the page was opened on.
      return <FamilyThreads source={source} seed={switched ? {} : viewSeed} onSwitch={switchFamily} />;
  }
}

function FamilyThreads(props: {
  source: Extract<ReturnType<typeof useThreadsSource>["source"], { kind: "family" }>;
  seed: { zoom?: string; focus?: string; highlight?: string };
  onSwitch: (fid: string) => void;
}) {
  const { source, seed, onSwitch } = props;
  const { family, families, familyName, data } = source;
  const fid = family.id;
  const switcher = useMemo(() => families.map((f) => ({ id: f.id, name: f.name })), [families]);

  if (data.people.length === 0) {
    return (
      <ThreadsSheet kicker={familyName} title="No threads yet">
        <p>Once someone is in the family tree, their thread starts here. Add a relative or a first memory to begin.</p>
        <div className="mt-6 flex flex-wrap gap-3">
          <ButtonLink href={hrefs.add({ familyId: fid })}>Add a memory</ButtonLink>
          <ButtonLink variant="secondary" href={`/families/${encodeURIComponent(fid)}/people/new`}>
            Add a relative
          </ButtonLink>
        </div>
        {families.length > 1 ? <OtherFamilies families={families} currentId={fid} onSwitch={onSwitch} /> : null}
      </ThreadsSheet>
    );
  }

  const note =
    data.moments.length === 0 || data.loose.length > 0 ? (
      <>
        {data.moments.length === 0 ? (
          <span>
            no moments yet &middot;{" "}
            <Link href={hrefs.add({ familyId: fid })} className="text-accent-deep">
              add the first
            </Link>
          </span>
        ) : null}
        {data.moments.length === 0 && data.loose.length > 0 ? <span aria-hidden> &middot; </span> : null}
        {data.loose.length > 0 ? <LooseMoments loose={data.loose} familyId={fid} /> : null}
      </>
    ) : null;

  return (
    <ThreadsView
      key={fid}
      familyId={fid}
      addHref={hrefs.add({ familyId: fid })}
      familyName={familyName}
      people={data.people}
      moments={data.moments}
      families={switcher}
      onSwitchFamily={onSwitch}
      note={note}
      {...seed}
    />
  );
}

/** On a sheet about one family: a quiet way into the user's others. */
function OtherFamilies(props: { families: FamilySummary[]; currentId?: string; onSwitch: (fid: string) => void }) {
  const others = props.families.filter((f) => f.id !== props.currentId);
  return (
    <div className="mt-6 border-t border-dashed border-rule pt-4">
      <p className="text-[0.95rem] text-ink-faint">Your other families</p>
      <ul className="mt-1 list-none p-0">
        {others.map((f) => (
          <li key={f.id}>
            <button
              type="button"
              onClick={() => props.onSwitch(f.id)}
              className="min-h-10 cursor-pointer break-words text-left font-display text-[1.05rem] text-ink underline decoration-rule decoration-1 underline-offset-[0.25em] hover:decoration-accent"
            >
              {f.name}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Moments that cannot sit on the timeline (no year, or nobody known in them), one tap away. */
function LooseMoments(props: { loose: LooseMoment[]; familyId: string }) {
  const { loose, familyId } = props;
  const n = loose.length;
  const allUndated = loose.every((m) => m.reason === "year");
  const label = allUndated
    ? `${n} ${n === 1 ? "moment" : "moments"} without a year`
    : `${n} ${n === 1 ? "moment" : "moments"} off the timeline`;
  return (
    <MenuDisclosure label={label} buttonClassName="text-ink-soft underline decoration-dotted decoration-1 underline-offset-[0.2em]">
      {loose.map((m) => (
        <Link key={m.id} href={momentHref(m.id, { familyId })} className="threads-menu-item">
          <span className="min-w-0 flex-1 break-words font-hand text-[1.15rem] leading-tight">{m.title}</span>
          <span className="shrink-0 text-xs text-ink-faint">{m.reason === "year" ? "no year" : "no one"}</span>
        </Link>
      ))}
    </MenuDisclosure>
  );
}

function DemoNote(props: { why: DemoReason; onRetry: () => void }) {
  if (props.why === "mock") return <span>a demo family &middot; mock mode</span>;
  if (props.why === "offline") {
    return (
      <span>
        Stitch is out of reach, so this is a demo family &middot;{" "}
        <button type="button" onClick={props.onRetry} className="cursor-pointer text-accent-deep underline decoration-1 underline-offset-[0.2em]">
          try again
        </button>
      </span>
    );
  }
  // A fixed path: this note is also rendered on the server for ?demo=1.
  return (
    <span>
      a demo family &middot;{" "}
      <Link href={hrefs.login("/threads")} className="text-accent-deep">
        sign in to see yours
      </Link>
    </span>
  );
}
