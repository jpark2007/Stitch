"use client";

// Add a relative who may never sign up, over POST /families/{fid}/people and,
// for each child picked, PATCH /families/{fid}/people/{childId} adding the new
// person to that child's parents (ARCHITECTURE.md section 4 item 5, section 6).
//
// Route: /families/{familyId}/people/new[?next=<relative path>]
// - With a safe `next`, a successful save goes to next with `added=<id>`
//   (withAddedParam), so /add?family=<fid> can preselect the new person.
// - Without one, the form gives way to a done state on the same page.
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { AddRelativeForm } from "@/components/flow/AddRelativeForm";
import { Button, ButtonLink } from "@/components/flow/Button";
import { FlowPage } from "@/components/flow/FlowPage";
import { FormError } from "@/components/flow/FormError";
import { PaperCard } from "@/components/flow/PaperCard";
import {
  childPatches,
  relativeServerError,
  withAddedParam,
  type TreePersonLite,
} from "@/components/flow/relativeForm";
import { describeRelationships } from "@/components/flow/relationships";
import { SessionGate, useSession } from "@/components/flow/SessionGate";
import { hrefs, rememberFamily, safeNext } from "@/components/flow/session";
import { findFamily, settingsLoadError, type SettingsLoad } from "@/components/flow/settingsPage";
import { isApiError } from "@/lib/api";
import type { NewPerson, Person } from "@/lib/types";

// The family pages share their quiet sheets: the same loading thread and the
// same dead end as settings, under the same "Stitch" mark.
import { FamilyNotFound, SettingsPlaceholder as RelativePlaceholder } from "../../settings/SettingsPlaceholder";

const LEDE =
  "Someone who may never sign up, like a late great-grandparent or a young child. " +
  "Anyone can claim them later from an invite.";

type PickablePerson = TreePersonLite & { color?: string | null };

/** The route's client half: reads the URL, then waits for the session. */
export function AddRelativeScreen() {
  const params = useParams<{ familyId: string }>();
  const search = useSearchParams();
  const fid = typeof params?.familyId === "string" ? params.familyId : "";
  const next = safeNext(search?.get("next"));

  // Wide like the page, so the column doesn't jump when it arrives.
  return (
    <SessionGate requireFamily width="wide">
      <AddRelativeGate fid={fid} next={next} />
    </SessionGate>
  );
}

/** Not one of the user's families: say so calmly and ask the server nothing. */
function AddRelativeGate(props: { fid: string; next: string | null }) {
  const { me } = useSession();
  const summary = findFamily(me.families, props.fid);
  if (!summary) return <FamilyNotFound />;
  // Keyed, so another family starts from a clean load.
  return <AddRelative key={summary.id} fid={summary.id} next={props.next} />;
}

type Load = { kind: "loading" } | { kind: "ready"; people: PickablePerson[] } | SettingsLoad;

/** Who was added, and which children could not be linked to them (with why). */
type Done = { person: Person; childNames: string[]; failed: Array<{ name: string; message: string }> };

function toPickable(people: Person[]): PickablePerson[] {
  return people.map((p) => ({
    id: p.id,
    name: p.name,
    born: p.born ?? null,
    parentIds: p.parentIds ?? [],
    spouseIds: p.spouseIds ?? [],
    color: p.character?.color ?? null,
  }));
}

/** Why a child could not be linked, in the family's words (after the person was saved). */
function linkFailure(err: unknown): string {
  if (isApiError(err)) {
    if (err.status === 400) {
      const detail = err.detail.trim().toLowerCase();
      // The tree changed since the page loaded: say what the server found.
      if (detail === "a person cannot be their own ancestor") return "That would make someone their own ancestor.";
      if (detail === "a person has at most 2 parents") return "They already have two parents.";
      return relativeServerError(err.detail).message;
    }
    const field = Object.values(err.fieldErrors).find((m) => typeof m === "string" && m.trim());
    return field ?? err.detail;
  }
  return "Something went wrong.";
}

function AddRelative(props: { fid: string; next: string | null }) {
  const { fid, next } = props;
  const { api, refresh } = useSession();
  const router = useRouter();

  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [retrying, setRetrying] = useState(false);
  const [done, setDone] = useState<Done | null>(null);

  useEffect(() => {
    let live = true;
    api
      .getTree(fid)
      .then(
        (tree) => {
          if (!live) return;
          setLoad({ kind: "ready", people: toPickable(tree.people ?? []) });
          rememberFamily(fid);
        },
        (err: unknown) => {
          if (!live) return;
          if (isApiError(err) && err.status === 401) {
            // Signed out since the page opened: refresh() sends us to log in.
            refresh().catch(() => setLoad(settingsLoadError(err)));
            return;
          }
          setLoad(settingsLoadError(err));
        },
      )
      .finally(() => {
        if (live) setRetrying(false);
      });
    return () => {
      live = false;
    };
  }, [api, fid, refresh, attempt]);

  const retry = useCallback(() => {
    setRetrying(true);
    setAttempt((n) => n + 1);
  }, []);

  /** Back to an empty form, over a fresh copy of the tree (it now has the new person). */
  const addAnother = useCallback(() => {
    setDone(null);
    setLoad({ kind: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  const people = load.kind === "ready" ? load.people : null;

  const save = useCallback(
    async (body: NewPerson, childIds: string[]) => {
      if (!people) return;
      // A failure here rejects, and the form shows it: nothing was saved.
      let person: Person;
      try {
        person = await api.addPerson(fid, body);
      } catch (err) {
        if (isApiError(err) && err.status === 404) {
          // The family is gone, or the viewer was taken out of it: say so.
          setLoad({ kind: "not-found" });
        } else if (isApiError(err) && err.status === 401) {
          // Signed out meanwhile: refresh() sends us to log in.
          await refresh().catch(() => undefined);
        }
        throw err;
      }

      // The person exists now. Link each child in turn; a link that fails is
      // reported, not undone, and the others still go ahead.
      const nameOf = (id: string) => people.find((p) => p.id === id)?.name ?? "Someone";
      const linked: string[] = [];
      const failed: Done["failed"] = [];
      for (const { personId, patch } of childPatches(people, person.id, childIds)) {
        try {
          await api.updatePerson(fid, personId, patch);
          linked.push(nameOf(personId));
        } catch (err) {
          failed.push({ name: nameOf(personId), message: linkFailure(err) });
        }
      }

      if (next && failed.length === 0) {
        router.push(withAddedParam(next, person.id));
        return;
      }
      setDone({ person, childNames: linked, failed });
    },
    [api, fid, next, people, refresh, router],
  );

  const back = next ? { href: next, label: "Back" } : { href: hrefs.settings(fid), label: "Family settings" };

  let body: ReactNode;
  if (load.kind === "not-found") return <FamilyNotFound />;
  if (done) {
    body = (
      <DoneCard
        done={done}
        people={people ?? []}
        fid={fid}
        next={next}
        onAddAnother={addAnother}
      />
    );
  } else if (load.kind === "error") {
    body = (
      <PaperCard className="flex flex-col gap-5">
        <FormError title="We couldn’t open the family tree" message={load.message} />
        <div>
          <Button onClick={retry} busy={retrying} busyLabel="Trying again…">
            Try again
          </Button>
        </div>
      </PaperCard>
    );
  } else if (load.kind === "loading") {
    body = <RelativePlaceholder label="Gathering the family…" bare />;
  } else {
    body = <AddRelativeForm people={load.people} onSubmit={save} />;
  }

  return (
    <FlowPage title="Add a relative" lede={LEDE} back={back} width="wide">
      {body}
    </FlowPage>
  );
}

/** "1910–1988", "Born 1910", "Died 1988", or null. */
function yearsText(p: Person): string | null {
  const born = p.born ?? null;
  const died = p.died ?? null;
  if (born !== null && died !== null) return `${born}–${died}`;
  if (born !== null) return `Born ${born}`;
  if (died !== null) return `Died ${died}`;
  return null;
}

function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function DoneCard(props: {
  done: Done;
  people: PickablePerson[];
  fid: string;
  next: string | null;
  onAddAnother(): void;
}) {
  const { done, people, fid, next, onAddAnother } = props;
  const { person, childNames, failed } = done;
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  const names = Object.fromEntries(people.map((p) => [p.id, p.name]));
  const relations = { parentIds: person.parentIds ?? [], spouseIds: person.spouseIds ?? [] };
  const parts: string[] = [];
  if (relations.parentIds.length || relations.spouseIds.length) {
    parts.push(describeRelationships(relations, names));
  }
  if (childNames.length) {
    const text = `parent of ${joinNames(childNames)}`;
    parts.push(parts.length ? text : text.charAt(0).toUpperCase() + text.slice(1));
  }
  const years = yearsText(person);
  const caption = [parts.join(", "), years].filter(Boolean).join(" · ");

  const failure = failed.length
    ? `We couldn’t link ${joinNames(failed.map((f) => f.name))} as their child. ` +
      [...new Set(failed.map((f) => f.message))].join(" ") +
      ` ${person.name} is saved all the same.`
    : null;

  // The add page reads `added` and preselects them, here too.
  const addHref = withAddedParam(next ?? hrefs.add({ familyId: fid }), person.id);

  return (
    <PaperCard className="flex flex-col gap-6">
      <div className="flex items-start gap-4">
        <span
          aria-hidden="true"
          className="mt-3 h-4 w-4 shrink-0 rounded-full bg-ink-faint ring-2 ring-paper-light shadow-paper"
          style={person.character?.color ? { backgroundColor: person.character.color } : undefined}
        />
        <div className="min-w-0">
          <h2
            ref={headingRef}
            tabIndex={-1}
            className="font-display text-[1.75rem] leading-tight text-balance break-words text-ink focus:outline-none sm:text-[2rem]"
          >
            {person.name} is in the tree.
          </h2>
          {caption ? <p className="mt-1 font-hand text-[1.375rem] leading-snug text-ink-soft">{caption}</p> : null}
        </div>
      </div>

      <FormError
        title={failed.length === 1 ? "One link didn’t save" : "Some links didn’t save"}
        message={failure}
      />

      <p className="text-[0.9375rem] leading-relaxed text-pretty text-ink-soft">
        Anyone in the family can now pick them when they tell a memory, and they can claim
        themselves later from an invite link.
      </p>

      <div className="flex flex-col gap-4 border-t border-dashed border-rule pt-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <ButtonLink href={addHref}>{next ? "Continue" : "Add a memory"}</ButtonLink>
          <Button variant="secondary" onClick={onAddAnother}>
            Add another relative
          </Button>
        </div>
        <div className="sm:-ml-3">
          <ButtonLink variant="quiet" href={hrefs.settings(fid)} className="w-full sm:w-auto">
            Back to family settings
          </ButtonLink>
        </div>
      </div>
    </PaperCard>
  );
}
