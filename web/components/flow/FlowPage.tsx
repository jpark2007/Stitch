// The page shell for the flow screens (sign up, log in, start a family,
// settings, join, add a memory): the small "Stitch" mark top left, where the
// home page has it (a quiet link home), then one centred column with an
// optional back link, a storybook-serif title, an optional lede, then the
// page's content. The page background and paper grain come from the global
// body style.
import Link from "next/link";
import type { ReactNode } from "react";
import { FOCUS_RING } from "./Button";

const WIDTH = {
  narrow: "max-w-[28rem]", // auth and short forms
  wide: "max-w-[40rem]", // add a memory, settings
} as const;

/**
 * The frame alone: the page, the "Stitch" mark and the centred column, with
 * no title. FlowPage draws its header inside it; SessionGate puts its loading
 * and error sheets in it, so they sit where the page's content will appear.
 */
export function FlowFrame(props: { width?: "narrow" | "wide"; children: ReactNode }) {
  const { width = "narrow", children } = props;
  return (
    // The same gutter and top spacing as the home page, so the mark sits in the same place.
    // data-flow-page: inside it PaperCard lies flat on the page (see PaperCard).
    <main
      data-flow-page=""
      className="w-full min-w-0 overflow-x-clip px-[var(--gutter)] pt-8 pb-24 font-sans text-ink sm:pt-12"
    >
      {/* Home's header is max-w-6xl including its gutter; this matches its inner edge. */}
      <nav aria-label="Stitch" className="mx-auto flex w-full max-w-[calc(72rem-2*var(--gutter))]">
        {/* -my keeps the 44px tap target without pushing the column down. */}
        <Link
          href="/"
          className={`kicker -mx-2 -my-3.5 inline-flex min-h-11 items-center rounded-paper px-2 no-underline transition-colors duration-150 ease-paper hover:text-ink motion-reduce:transition-none ${FOCUS_RING}`}
        >
          Stitch
        </Link>
      </nav>

      <div className={`mx-auto mt-8 w-full min-w-0 sm:mt-6 ${WIDTH[width]}`}>{children}</div>
    </main>
  );
}

export function FlowPage(props: {
  title: string;
  lede?: ReactNode;
  back?: { href: string; label: string };
  width?: "narrow" | "wide";
  children: ReactNode;
}) {
  const { title, lede, back, width = "narrow", children } = props;
  const ledeClass = "mt-3 text-base leading-relaxed text-ink-soft";

  return (
    <FlowFrame width={width}>
      {back ? (
        <Link
          href={back.href}
          className={`-ml-2 mb-3 inline-flex min-h-11 items-center gap-1.5 rounded-paper px-2 text-sm font-medium text-ink-soft no-underline transition-colors duration-150 ease-paper hover:text-accent-deep motion-reduce:transition-none ${FOCUS_RING}`}
        >
          <span aria-hidden="true">&larr;</span>
          {back.label}
        </Link>
      ) : null}

      <header className="mb-10">
        <h1 className="font-display text-[2rem] leading-[1.15] tracking-[-0.015em] text-balance break-words text-ink sm:text-[2.375rem]">
          {title}
        </h1>
        {lede == null || lede === false ? null : typeof lede === "string" ? (
          <p className={`${ledeClass} text-pretty`}>{lede}</p>
        ) : (
          <div className={ledeClass}>{lede}</div>
        )}
      </header>

      {children}
    </FlowFrame>
  );
}
