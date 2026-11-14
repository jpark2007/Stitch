// The page shell for the flow screens (sign up, log in, start a family,
// settings, join, add a memory): one centred column with an optional back
// link, a storybook-serif title, an optional lede, then the page's content.
// The page background and paper grain come from the global body style.
import Link from "next/link";
import type { ReactNode } from "react";

const WIDTH = {
  narrow: "max-w-[28rem]", // auth and short forms
  wide: "max-w-[40rem]", // add a memory, settings
} as const;

export function FlowPage(props: {
  title: string;
  lede?: ReactNode;
  back?: { href: string; label: string };
  width?: "narrow" | "wide";
  children: ReactNode;
}) {
  const { title, lede, back, width = "narrow", children } = props;
  const ledeClass = "mt-3 text-[1.0625rem] leading-relaxed text-[var(--ink-soft,#5c4e43)]";

  return (
    <main className="w-full min-w-0 overflow-x-clip px-4 pt-8 pb-20 font-sans text-[var(--ink,#2f2620)] sm:px-6 sm:pt-14">
      <div className={`mx-auto w-full min-w-0 ${WIDTH[width]}`}>
        {back ? (
          <Link
            href={back.href}
            className="-ml-2 mb-4 inline-flex min-h-11 items-center gap-1.5 rounded-[var(--radius-paper,2px)] px-2 text-[0.9375rem] font-medium text-[var(--ink-soft,#5c4e43)] no-underline transition-colors hover:text-[var(--accent-deep,#8f3f2a)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent,#b9573d)] motion-reduce:transition-none"
          >
            <span aria-hidden="true">&larr;</span>
            {back.label}
          </Link>
        ) : null}

        <header className="mb-8">
          <h1 className="font-serif text-[2rem] leading-[1.15] tracking-[-0.015em] text-balance break-words text-[var(--ink,#2f2620)] sm:text-[2.5rem]">
            {title}
          </h1>
          {lede == null || lede === false ? null : typeof lede === "string" ? (
            <p className={`${ledeClass} text-pretty`}>{lede}</p>
          ) : (
            <div className={ledeClass}>{lede}</div>
          )}
        </header>

        {children}
      </div>
    </main>
  );
}
