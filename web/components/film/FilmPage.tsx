// The page shell for the film list and a film's page: the flow pages' frame
// (the small "Stitch" mark top left, the same gutter and header type) with a
// wider column, since a film and a grid of covers want more room than a form.
// The "Make a film" form uses the flow kit's own FlowPage.
import Link from "next/link";
import type { ReactNode } from "react";

import { FOCUS_RING } from "@/components/flow/Button";
import { PaperCard } from "@/components/flow/PaperCard";

export function FilmPage(props: {
  title: ReactNode;
  lede?: ReactNode;
  back?: { href: string; label: string };
  /** Beside the title from sm up (a "Make a film" link, say); under it on a phone. */
  action?: ReactNode;
  children: ReactNode;
}) {
  const { title, lede, back, action, children } = props;
  return (
    <main
      data-flow-page=""
      className="w-full min-w-0 overflow-x-clip px-[var(--gutter)] pt-8 pb-24 font-sans text-ink sm:pt-12"
    >
      <nav aria-label="Stitch" className="mx-auto flex w-full max-w-[calc(72rem-2*var(--gutter))]">
        <Link
          href="/"
          className={`kicker -mx-2 -my-3.5 inline-flex min-h-11 items-center rounded-paper px-2 no-underline transition-colors duration-150 ease-paper hover:text-ink motion-reduce:transition-none ${FOCUS_RING}`}
        >
          Stitch
        </Link>
      </nav>

      <div className="mx-auto mt-8 w-full max-w-[58rem] min-w-0 sm:mt-6">
        {back ? (
          <Link
            href={back.href}
            className={`-ml-2 mb-3 inline-flex min-h-11 items-center gap-1.5 rounded-paper px-2 text-sm font-medium text-ink-soft no-underline transition-colors duration-150 ease-paper hover:text-accent-deep motion-reduce:transition-none ${FOCUS_RING}`}
          >
            <span aria-hidden="true">&larr;</span>
            {back.label}
          </Link>
        ) : null}

        <header className="mb-10 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between sm:gap-8">
          <div className="min-w-0">
            <h1 className="font-display text-[2rem] leading-[1.15] tracking-[-0.015em] text-balance break-words text-ink sm:text-[2.375rem]">
              {title}
            </h1>
            {lede == null || lede === false ? null : (
              <div className="mt-3 text-base leading-relaxed text-pretty text-ink-soft">{lede}</div>
            )}
          </div>
          {action ? <div className="shrink-0">{action}</div> : null}
        </header>

        {children}
      </div>
    </main>
  );
}

/** A quiet sheet with a running thread while something loads. */
export function QuietSheet(props: { label: string }) {
  return (
    <div role="status" aria-busy="true" aria-live="polite">
      <PaperCard className="flex flex-col items-start gap-3">
        <span
          aria-hidden="true"
          className="block h-0 w-24 border-t-[1.5px] border-dashed border-ink-faint motion-safe:animate-pulse"
        />
        <p className="text-base text-ink-soft">{props.label}</p>
      </PaperCard>
    </div>
  );
}

/** "That year", "1 min 5 sec": the meta line under a film's title. */
export function MetaLine(props: { children: ReactNode }) {
  return <p className="kicker flex flex-wrap items-center gap-x-2 gap-y-1">{props.children}</p>;
}
