"use client";

// /ask: "Ask the family". One question field; the question lives in the URL
// (?q=), so a result can be shared, survives a reload and Back returns to it.
// Arriving with ?q= runs the search. The answer shows the best match as a
// paper card with "Open this page" (the moment's book, opened at its spread),
// then the other moments it quotes. Enter on the same question opens a
// confident match; nothing navigates without a press. A slower earlier search
// never overwrites a newer one (askReducer), and an answer is kept for the tab
// (cacheAnswer) so Back from the book shows it at once without asking again.
// Signed out, no family and a family not one's own are handled as on /add.
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useReducer, useRef, useState, type FormEvent, type Ref } from "react";

import { Button, ButtonLink, FOCUS_RING } from "@/components/flow/Button";
import { fieldControlClass } from "@/components/flow/Field";
import { FlowPage } from "@/components/flow/FlowPage";
import { FormError } from "@/components/flow/FormError";
import { PaperCard } from "@/components/flow/PaperCard";
import { SessionGate, type SessionValue } from "@/components/flow/SessionGate";
import { familyChoice, type FamilyChoice } from "@/components/flow/addPage";
import {
  ASK_EXAMPLES,
  ASK_IDLE,
  ASK_MAX_LENGTH,
  answerText,
  askReducer,
  bestHref,
  bestMatch,
  cacheAnswer,
  cachedAnswer,
  cleanQuestion,
  enterIntent,
  fieldHint,
  memoryPageHref,
  momentTitle,
  otherGroups,
  readAskQuery,
  sameQuestion,
  tellerName,
  type AskResult,
  type BestMatch,
  type QuoteGroup,
} from "@/components/flow/askPage";
import { hrefs, recallFamily, rememberFamily } from "@/components/flow/session";
import { isApiError } from "@/lib/api";
import type { AskQuote, AskResponse } from "@/lib/types";

const TITLE = "Ask the family anything";
const LEDE = "Ask about any moment someone has told. Stitch finds the page it’s on.";
const BACK = { href: hrefs.home(), label: "Family tree" };
const ASK_ERROR = "We couldn’t search the family’s pages just now. Check your connection and try again.";

function sessionStore(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function currentPath(): string {
  return `${window.location.pathname}${window.location.search}`;
}

/** The page shell with a quiet sheet: the prerendered HTML and the session wait. */
export function AskLoading() {
  return (
    <FlowPage title={TITLE} lede={LEDE} back={BACK} width="wide">
      <QuietSheet label="Opening your family’s pages…" />
    </FlowPage>
  );
}

export function AskScreen() {
  const params = useSearchParams();
  const { familyId, q } = readAskQuery(params);
  return (
    <SessionGate requireFamily width="wide" loading={<AskLoading />}>
      {(session) => <AskFlow session={session} familyId={familyId} q={q} />}
    </SessionGate>
  );
}

function AskFlow(props: { session: SessionValue; familyId: string | null; q: string | null }) {
  const { session, familyId, q } = props;
  const { me, api } = session;
  const router = useRouter();

  // As /add: the family in the URL, else the remembered one, else the first;
  // a family id that is not one of this person's falls back the same way.
  const wanted = familyId ?? recallFamily();
  const choice = useMemo(() => familyChoice(me.families, wanted), [me.families, wanted]);
  const fid = choice.current?.id ?? null;
  useEffect(() => {
    if (fid) rememberFamily(fid);
  }, [fid]);

  // Names for "told by": the family's people, as the other flow pages load them.
  const [names, setNames] = useState<{ fid: string; map: Map<string, string> } | null>(null);
  useEffect(() => {
    if (!fid) return;
    let live = true;
    api.getTree(fid).then(
      (tree) => {
        if (live) setNames({ fid, map: new Map(tree.people.map((p) => [p.id, p.name])) });
      },
      () => {
        // Names are a nicety: without them a teller reads "Someone in the family".
      },
    );
    return () => {
      live = false;
    };
  }, [api, fid]);
  const nameMap = names?.fid === fid ? names.map : null;

  // The search follows the URL's ?q=. Each search gets a number; the reducer
  // drops any answer that is not for the latest one.
  const [view, dispatch] = useReducer(askReducer, ASK_IDLE);
  const seq = useRef(0);
  const [retries, setRetries] = useState(0);
  const forceRef = useRef(false);

  useEffect(() => {
    if (!fid) return;
    const n = ++seq.current;
    if (!q) {
      dispatch({ type: "clear" });
      return;
    }
    const force = forceRef.current;
    forceRef.current = false;
    const kept = force ? null : cachedAnswer(sessionStore(), fid, q);
    dispatch({ type: "start", q, seq: n });
    if (kept) {
      dispatch({ type: "done", seq: n, res: kept });
      return;
    }
    api.ask(fid, { query: q }).then(
      (res: AskResponse) => {
        cacheAnswer(sessionStore(), fid, q, res);
        dispatch({ type: "done", seq: n, res });
      },
      (e: unknown) => {
        if (isApiError(e) && e.status === 401) {
          router.replace(hrefs.login(currentPath()));
          return;
        }
        const message = isApiError(e) && e.status !== 0 && e.status < 500 && e.detail ? e.detail : ASK_ERROR;
        dispatch({ type: "failed", seq: n, message });
      },
    );
  }, [api, fid, q, retries, router]);

  // The field shows the URL's question; Back and forward bring theirs.
  const [typed, setTyped] = useState(q ?? "");
  const [seenQ, setSeenQ] = useState(q);
  if (seenQ !== q) {
    setSeenQ(q);
    setTyped(q ?? "");
  }

  const ask = useCallback(
    (question: string) => {
      if (!fid) return;
      const clean = cleanQuestion(question);
      if (!clean) return;
      if (sameQuestion(clean, q)) {
        // The same question again (a chip for what is already asked): ask afresh.
        forceRef.current = true;
        setRetries((r) => r + 1);
        return;
      }
      router.push(hrefs.ask({ familyId: fid, q: clean }), { scroll: false });
    },
    [fid, q, router],
  );

  const retry = useCallback(() => {
    forceRef.current = true;
    setRetries((r) => r + 1);
  }, []);

  const openRef = useRef<HTMLAnchorElement>(null);

  const onSubmit = () => {
    const intent = enterIntent(view, typed, fid);
    switch (intent.kind) {
      case "ask":
        ask(intent.q);
        break;
      case "retry":
        retry();
        break;
      case "open":
        router.push(intent.href);
        break;
      case "stay":
        // Answered but not sure: take the keyboard to the match instead.
        openRef.current?.focus();
        break;
    }
  };

  if (!choice.current || !fid) {
    // SessionGate's requireFamily sends people without a family away; this
    // is only reached if that changes under us.
    return (
      <FlowPage title={TITLE} width="wide">
        <PaperCard className="flex flex-col items-start gap-4">
          <p className="text-[1.0625rem] leading-relaxed text-ink">
            Questions are answered from a family&rsquo;s pages. Start a family first, then come back to ask.
          </p>
          <ButtonLink href={hrefs.newFamily()}>Start a family</ButtonLink>
        </PaperCard>
      </FlowPage>
    );
  }

  const searching = view.kind === "searching";
  const shownResult: AskResult | null =
    view.kind === "results" || view.kind === "nothing" ? view : view.kind === "searching" ? view.shown : null;

  return (
    <FlowPage title={TITLE} lede={<FamilyLede choice={choice} q={q} />} back={BACK} width="wide">
      <AskForm
        typed={typed}
        onType={setTyped}
        onSubmit={onSubmit}
        busy={searching}
        hint={fieldHint(view, typed)}
        examples={view.kind === "idle" ? ASK_EXAMPLES : null}
        onExample={(example) => {
          setTyped(example);
          ask(example);
        }}
      />

      <div className="mt-8" aria-live="polite" aria-busy={searching || undefined}>
        {view.kind === "error" ? (
          <ErrorSheet message={view.message} onRetry={retry} />
        ) : shownResult ? (
          <div
            inert={searching || undefined}
            className={`transition-opacity duration-150 ease-paper motion-reduce:transition-none ${searching ? "opacity-50" : ""}`}
          >
            {shownResult.kind === "nothing" ? (
              <NothingFound fid={fid} />
            ) : (
              <Results res={shownResult.res} fid={fid} names={nameMap} openRef={openRef} />
            )}
          </div>
        ) : searching ? (
          <QuietSheet label="Looking through the family’s pages…" />
        ) : null}
      </div>
    </FlowPage>
  );
}

/** For someone in two or more families: which family is being asked, with a link to ask each other one. */
function FamilyLede(props: { choice: FamilyChoice; q: string | null }) {
  const { choice, q } = props;
  const lede = <p className="text-pretty">{LEDE}</p>;
  if (!choice.show || !choice.current) return lede;
  return (
    <>
      <div className="flex min-w-0 flex-col gap-y-1 text-[0.9375rem] leading-snug text-ink-soft sm:flex-row sm:flex-wrap sm:items-baseline sm:gap-x-2">
        <p className="min-w-0 break-words">
          Asking <span className="font-semibold text-ink">{choice.current.name}</span>
        </p>
        {choice.others.length ? (
          <>
            <span aria-hidden="true" className="hidden text-ink-soft/50 sm:inline">
              &middot;
            </span>
            <nav aria-label="Ask another family" className="min-w-0 break-words">
              Ask{" "}
              {choice.others.map((f, i) => (
                <span key={f.id}>
                  {i === 0 ? "" : i === choice.others.length - 1 ? " or " : ", "}
                  <Link href={hrefs.ask({ familyId: f.id, q })} className={INLINE_LINK}>
                    {f.name}
                  </Link>
                </span>
              ))}{" "}
              instead
            </nav>
          </>
        ) : null}
      </div>
      <div className="mt-3">{lede}</div>
    </>
  );
}

const INLINE_LINK = `rounded-paper font-medium text-accent-deep underline decoration-1 underline-offset-[0.2em] hover:decoration-2 ${FOCUS_RING}`;

function AskForm(props: {
  typed: string;
  onType: (v: string) => void;
  onSubmit: () => void;
  busy: boolean;
  hint: string | null;
  examples: readonly string[] | null;
  onExample: (q: string) => void;
}) {
  const { typed, onType, onSubmit, busy, hint, examples, onExample } = props;
  const uid = useId();
  const inputId = `${uid}q`;
  const hintId = `${uid}hint`;
  const examplesId = `${uid}examples`;
  const inputRef = useRef<HTMLInputElement>(null);

  // Ready to type on arrival.
  useEffect(() => {
    inputRef.current?.focus({ preventScroll: true });
  }, []);

  return (
    <PaperCard
      as="form"
      role="search"
      noValidate
      aria-busy={busy || undefined}
      onSubmit={(e: FormEvent<HTMLFormElement>) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      {/* The page title says it out loud; the field's own label is for screen readers. */}
      <label htmlFor={inputId} className="sr-only">
        Your question
      </label>
      {/* Phones: the whole width for the question, Ask under it; side by side from sm. */}
      <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-stretch">
        <input
          ref={inputRef}
          id={inputId}
          name="q"
          type="search"
          enterKeyHint="search"
          autoComplete="off"
          spellCheck
          maxLength={ASK_MAX_LENGTH}
          value={typed}
          onChange={(e) => onType(e.target.value)}
          // Enter asks even while the last question is still on its way (the
          // busy Ask button is disabled, which blocks the form's own Enter).
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
            e.preventDefault();
            onSubmit();
          }}
          placeholder="Where did Grandpa meet Grandma?"
          aria-describedby={hint ? hintId : undefined}
          className={fieldControlClass(
            false,
            "min-h-13 w-full min-w-0 sm:flex-1 text-[1.0625rem] sm:text-[1.25rem] [&::-webkit-search-cancel-button]:hidden",
          )}
        />
        <Button type="submit" busy={busy} busyLabel="Asking" className="min-w-[7.5rem] shrink-0">
          Ask
        </Button>
      </div>
      {/* Room for two lines on phones and one from sm, so the card keeps its height as the hint changes. */}
      <p id={hintId} aria-live="off" className="mt-2.5 min-h-[2lh] text-sm leading-snug text-ink-soft sm:min-h-[1lh]">
        {hint}
      </p>

      {examples?.length ? (
        <div className="mt-5">
          <p id={examplesId} className="kicker">
            Try asking
          </p>
          <ul aria-labelledby={examplesId} className="mt-2.5 flex list-none flex-wrap gap-2 p-0">
            {examples.map((example) => (
              <li key={example} className="min-w-0">
                <button
                  type="button"
                  onClick={() => onExample(example)}
                  className={`min-h-11 max-w-full rounded-paper border border-rule bg-paper px-3.5 py-2 text-left text-[0.9375rem] leading-snug text-ink-soft transition-colors duration-150 ease-paper hover:border-accent hover:text-ink motion-reduce:transition-none ${FOCUS_RING}`}
                >
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </PaperCard>
  );
}

function Results(props: {
  res: AskResponse;
  fid: string;
  names: ReadonlyMap<string, string> | null;
  openRef: Ref<HTMLAnchorElement>;
}) {
  const { res, fid, names, openRef } = props;
  const best = bestMatch(res);
  const others = otherGroups(res, best);
  const answer = answerText(res, best);
  // A one-line answer reads as the headline; a long one (a search that strings
  // the quotes together) steps back so the page card below leads.
  const long = answer.length > 140;
  return (
    <section aria-label="Answer" className="flex min-w-0 flex-col gap-6">
      <p
        className={
          long
            ? "text-[1.0625rem] leading-relaxed text-pretty break-words text-ink-soft"
            : "font-display text-[1.25rem] leading-snug text-pretty break-words text-ink sm:text-[1.375rem]"
        }
      >
        {answer}
      </p>
      {best ? <BestCard best={best} fid={fid} names={names} openRef={openRef} /> : null}
      {others.length ? <OtherMoments groups={others} fid={fid} names={names} hasBest={best !== null} /> : null}
    </section>
  );
}

function YearMark(props: { year: number | null }) {
  return props.year === null ? null : <span className="kicker">{props.year}</span>;
}

function BestCard(props: {
  best: BestMatch;
  fid: string;
  names: ReadonlyMap<string, string> | null;
  openRef: Ref<HTMLAnchorElement>;
}) {
  const { best, fid, names, openRef } = props;
  const quote = best.quotes[0] ?? null;
  const headingId = `best-${best.memoryId}`;
  return (
    <PaperCard aria-labelledby={headingId} className="flex flex-col gap-5">
      <div className="min-w-0">
        <p className="kicker">{best.confident ? "The page it’s on" : "The closest page"}</p>
        <h2
          id={headingId}
          className="mt-2 font-display text-[1.75rem] leading-[1.15] text-balance break-words text-ink sm:text-[2rem]"
        >
          {momentTitle(best.title)}
        </h2>
        <div className="mt-1">
          <YearMark year={best.year} />
        </div>
      </div>
      {quote ? <Quote quote={quote} names={names} large /> : null}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Link
          ref={openRef}
          href={bestHref(best, fid)}
          className={`relative inline-flex min-h-11 items-center justify-center gap-2 rounded-card border border-transparent bg-accent-deep px-5 py-2.5 font-sans text-base leading-tight font-semibold text-paper-light no-underline shadow-[0_1px_0_rgb(255_255_255/0.18)_inset,0_1px_2px_rgb(74_47_29/0.25)] transition-[background-color] duration-150 ease-paper select-none hover:bg-[color-mix(in_oklab,var(--accent-deep)_86%,var(--ink))] motion-reduce:transition-none ${FOCUS_RING}`}
        >
          Open this page
          <span aria-hidden="true">&rarr;</span>
        </Link>
      </div>
    </PaperCard>
  );
}

function Quote(props: { quote: AskQuote; names: ReadonlyMap<string, string> | null; large?: boolean }) {
  const { quote, names, large = false } = props;
  return (
    <figure className="min-w-0 border-l-[3px] border-l-accent pl-4">
      <blockquote
        className={`font-hand leading-[1.2] text-pretty break-words text-ink ${large ? "text-[1.5rem] sm:text-[1.625rem]" : "text-[1.25rem]"}`}
      >
        &ldquo;{quote.text.trim()}&rdquo;
      </blockquote>
      <figcaption className="mt-1.5 text-[0.9375rem] leading-snug text-ink-soft">
        &mdash; told by <span className="font-semibold text-ink">{tellerName(names, quote.authorId)}</span>
      </figcaption>
    </figure>
  );
}

function OtherMoments(props: {
  groups: QuoteGroup[];
  fid: string;
  names: ReadonlyMap<string, string> | null;
  hasBest: boolean;
}) {
  const { groups, fid, names, hasBest } = props;
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="min-w-0">
      <h2 id={headingId} className="font-display text-[1.1875rem] leading-tight text-ink">
        {hasBest ? "Also mentioned in" : "Pages that mention it"}
      </h2>
      <ul className="mt-3 flex list-none flex-col gap-3 p-0">
        {groups.map((g) => (
          <li key={g.memoryId} className="min-w-0">
            <Link
              href={memoryPageHref({ memoryId: g.memoryId, familyId: fid, narrator: g.quotes[0]?.authorId })}
              className={`group block min-w-0 rounded-paper border border-rule bg-paper-light px-5 py-4 text-ink no-underline shadow-paper transition-colors duration-150 ease-paper hover:border-accent motion-reduce:transition-none ${FOCUS_RING}`}
            >
              <span className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <span className="min-w-0 font-display text-[1.1875rem] leading-snug break-words">
                  {momentTitle(g.title)}
                  {g.year !== null ? <span className="kicker ml-2.5 align-[0.1em]">{g.year}</span> : null}
                </span>
                <span className="shrink-0 text-[0.9375rem] font-semibold text-accent-deep underline-offset-[0.2em] group-hover:underline">
                  Open <span aria-hidden="true">&rarr;</span>
                </span>
              </span>
              {g.quotes.map((quote, i) => (
                <span key={i} className="mt-2 block min-w-0 text-[0.9375rem] leading-snug text-ink-soft">
                  <span className="font-hand text-[1.1875rem] leading-tight text-ink">
                    &ldquo;{quote.text.trim()}&rdquo;
                  </span>{" "}
                  &mdash; {tellerName(names, quote.authorId)}
                </span>
              ))}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function NothingFound(props: { fid: string }) {
  return (
    <PaperCard className="flex flex-col items-start gap-4">
      <h2 className="font-display text-[1.5rem] leading-tight text-ink">No one has told that story yet</h2>
      <p className="text-[1.0625rem] leading-relaxed text-pretty text-ink-soft">
        If you remember it, tell it, and it becomes a page in the family book. Or try asking it another way.
      </p>
      <ButtonLink href={hrefs.add({ familyId: props.fid })}>Tell this story</ButtonLink>
    </PaperCard>
  );
}

function ErrorSheet(props: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-start gap-3">
      <div className="w-full">
        <FormError title="That didn’t go through" message={props.message} />
      </div>
      <Button variant="secondary" onClick={props.onRetry}>
        Try again
      </Button>
    </div>
  );
}

/** A quiet sheet with a running thread while something loads. */
function QuietSheet(props: { label: string }) {
  return (
    <div role="status" aria-busy="true">
      <PaperCard className="flex flex-col items-center gap-4 py-10 text-center sm:py-12">
        <span
          aria-hidden="true"
          className="block h-0 w-24 border-t-[1.5px] border-dashed border-rule motion-safe:animate-pulse"
        />
        <p className="font-display text-lg text-ink-soft">{props.label}</p>
      </PaperCard>
    </div>
  );
}
