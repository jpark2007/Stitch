"use client";

// The "Add a tradition" screen. Reads ?family=, waits for the session (signed
// out goes to log in and comes back; no family goes to start one), picks the
// family as /add does (the asked-for one if the person belongs to it, else
// the remembered one, else their first), loads its tree for "Who takes
// part?", then shows the form. Saving uploads the photos (each once, even
// across retries), creates the tradition and goes back to the book.
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { LoadingSheet } from "@/app/add/parts";
import { Button, ButtonLink, FOCUS_RING } from "@/components/flow/Button";
import { FlowPage } from "@/components/flow/FlowPage";
import { FormError } from "@/components/flow/FormError";
import { PaperCard } from "@/components/flow/PaperCard";
import { SessionGate, type SessionValue } from "@/components/flow/SessionGate";
import { AddTraditionForm } from "@/components/flow/AddTraditionForm";
import { familyChoice, type FamilyChoice } from "@/components/flow/addPage";
import { hrefs, recallFamily, rememberFamily } from "@/components/flow/session";
import { toCreateTraditionRequest, type TraditionDraft } from "@/components/flow/traditionForm";
import { isApiError } from "@/lib/api";

const TITLE = "Add a tradition";
const LEDE = "The things your family does again and again. Write one down so it carries on.";
const LOAD_ERROR = "We couldn't open the page for adding a tradition. Check your connection and try again.";

type PickablePerson = { id: string; name: string; born?: number | null; color?: string | null };

/** The quiet link back to the book, for the family on this page. */
function backTo(familyId: string | null) {
  return { href: hrefs.traditions({ familyId }), label: "Back to the traditions" };
}

/** The path and query of this page, for coming back after logging in. */
function currentPath(): string {
  return `${window.location.pathname}${window.location.search}`;
}

/** The page shell with a quiet loading sheet, so nothing jumps when the form arrives. */
export function NewTraditionLoading(props: { familyId: string | null }) {
  return (
    <FlowPage title={TITLE} lede={LEDE} back={backTo(props.familyId)} width="wide">
      <LoadingSheet label="Opening your family’s pages…" />
    </FlowPage>
  );
}

export function NewTraditionScreen() {
  const params = useSearchParams();
  const familyId = params.get("family") || null;
  return (
    <SessionGate requireFamily width="wide" loading={<NewTraditionLoading familyId={familyId} />}>
      {(session) => <NewTraditionFlow session={session} familyId={familyId} />}
    </SessionGate>
  );
}

type Status =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; people: PickablePerson[] };

function NewTraditionFlow(props: { session: SessionValue; familyId: string | null }) {
  const { session, familyId } = props;
  const { me, api } = session;
  const router = useRouter();

  // Only runs in the browser (SessionGate renders this after asking the
  // server who is signed in), so reading storage here is safe.
  const wanted = familyId ?? recallFamily();
  const choice = useMemo(() => familyChoice(me.families, wanted), [me.families, wanted]);
  const family = choice.current;
  const fid = family?.id ?? null;

  useEffect(() => {
    if (fid) rememberFamily(fid);
  }, [fid]);

  // The family's people, for "Who takes part?". Keyed by family and try, so
  // a switch or a retry shows the loading sheet again, never stale people.
  const [loadTry, setLoadTry] = useState(0);
  const loadKey = fid ? `${fid}#${loadTry}` : null;
  const [loaded, setLoaded] = useState<{ key: string; status: Status } | null>(null);
  useEffect(() => {
    if (!fid || !loadKey) return;
    let live = true;
    api.getTree(fid).then(
      (tree) => {
        if (!live) return;
        const people = tree.people.map((p) => ({
          id: p.id,
          name: p.name,
          born: p.born ?? null,
          color: p.character?.color ?? null,
        }));
        setLoaded({ key: loadKey, status: { kind: "ready", people } });
      },
      (e: unknown) => {
        if (!live) return;
        if (isApiError(e) && e.status === 401) {
          router.replace(hrefs.login(currentPath()));
          return;
        }
        const message = isApiError(e) && e.status !== 0 && e.detail ? e.detail : LOAD_ERROR;
        setLoaded({ key: loadKey, status: { kind: "error", message } });
      },
    );
    return () => {
      live = false;
    };
  }, [api, fid, loadKey, router]);
  const status: Status = loadKey && loaded?.key === loadKey ? loaded.status : { kind: "loading" };

  // Photos already uploaded by an earlier try, so saving again after an
  // error does not upload them twice.
  const uploaded = useRef(new Map<File, string>());

  const onSubmit = useCallback(
    async (draft: TraditionDraft, photos: File[]) => {
      if (!fid) return;
      const photoIds: string[] = [];
      for (const file of photos) {
        let id = uploaded.current.get(file);
        if (!id) {
          id = (await api.uploadMedia(fid, file)).id;
          uploaded.current.set(file, id);
        }
        photoIds.push(id);
      }
      await api.createTradition(fid, toCreateTraditionRequest(draft, photoIds));
      router.push(hrefs.traditions({ familyId: fid }));
    },
    [api, fid, router],
  );

  if (!family || !fid) {
    // SessionGate's requireFamily sends people without a family away; this
    // is only reached if that changes under us.
    return (
      <FlowPage title={TITLE} width="wide">
        <PaperCard className="flex flex-col items-start gap-4">
          <p className="text-[1.0625rem] leading-relaxed text-ink">
            Traditions live in a family. Start one first, then come back to add yours.
          </p>
          <ButtonLink href={hrefs.newFamily()}>Start a family</ButtonLink>
        </PaperCard>
      </FlowPage>
    );
  }

  const lede = <FamilyLede choice={choice} />;
  const back = backTo(fid);

  if (status.kind === "loading") {
    return (
      <FlowPage title={TITLE} lede={lede} back={back} width="wide">
        <LoadingSheet label="Opening your family’s pages…" />
      </FlowPage>
    );
  }

  if (status.kind === "error") {
    return (
      <FlowPage title={TITLE} lede={lede} back={back} width="wide">
        <PaperCard className="flex flex-col gap-5">
          <FormError message={status.message} />
          <div>
            <Button onClick={() => setLoadTry((n) => n + 1)}>Try again</Button>
          </div>
        </PaperCard>
      </FlowPage>
    );
  }

  return (
    <FlowPage title={TITLE} lede={lede} back={back} width="wide">
      <AddTraditionForm
        key={fid}
        people={status.people}
        selfPersonId={family.personId ?? null}
        onSubmit={onSubmit}
      />
    </FlowPage>
  );
}

const SWITCH_LINK = `rounded-paper font-medium text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2 ${FOCUS_RING}`;

/** "A", "A or B", "A, B or C": what goes before link `i` of `n`. */
function joiner(i: number, n: number): string {
  if (i === 0) return "";
  return i === n - 1 ? " or " : ", ";
}

/**
 * The lede, with one quiet line above it for someone in two or more families:
 * which family the tradition goes into, and a link to each other family's
 * form (the same line /add shows).
 */
function FamilyLede(props: { choice: FamilyChoice }) {
  const { current, others, show } = props.choice;
  if (!show || !current) return <p className="text-pretty">{LEDE}</p>;
  return (
    <>
      <div className="flex min-w-0 flex-col gap-y-1 text-[0.9375rem] leading-snug text-ink-soft sm:flex-row sm:flex-wrap sm:items-baseline sm:gap-x-2">
        <p className="min-w-0 break-words">
          Adding to <span className="font-semibold text-ink">{current.name}</span>
        </p>
        {others.length ? (
          <>
            <span aria-hidden="true" className="hidden text-ink-soft/50 sm:inline">
              &middot;
            </span>
            <nav aria-label="Add to another family" className="min-w-0 break-words">
              Switch to{" "}
              {others.map((f, i) => (
                <Fragment key={f.id}>
                  {joiner(i, others.length)}
                  <Link href={hrefs.newTradition({ familyId: f.id })} className={SWITCH_LINK}>
                    {f.name}
                  </Link>
                </Fragment>
              ))}
            </nav>
          </>
        ) : null}
      </div>
      <p className="mt-3 text-pretty">{LEDE}</p>
    </>
  );
}
