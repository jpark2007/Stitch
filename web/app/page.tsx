import Link from "next/link";
import { FamilyTree } from "@/components/tree/FamilyTree";
import { demoFamily, demoPeople } from "@/components/tree/demoFamily";

// Home: the family tree. Every person opens their own pop-up book.

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-6xl flex-col px-[var(--gutter)] pb-10 pt-8 sm:pt-12">
      <header className="relative flex flex-col items-center text-center">
        <nav className="flex w-full items-center justify-between">
          <span className="kicker">Stitch</span>
          <Link
            href="/threads"
            className="group inline-flex items-center gap-2 text-sm text-ink-faint no-underline transition-colors hover:text-ink"
          >
            <svg width="26" height="10" viewBox="0 0 26 10" aria-hidden className="shrink-0">
              <path
                d="M1 6 Q 7 1 13 5 T 25 4"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeDasharray="3.5 2.5"
                strokeLinecap="round"
              />
            </svg>
            Family threads
          </Link>
        </nav>
        <h1 className="mt-8 text-[clamp(2rem,4.4vw,3.4rem)] sm:mt-6">{demoFamily.name}</h1>
        <p className="caption mt-2">pick anyone to open their book</p>
      </header>

      <section aria-label="Family tree" className="mt-6 flex flex-1 items-start justify-center sm:mt-10">
        <div className="w-full max-w-[62rem]">
          <FamilyTree people={demoPeople} maxHeight="calc(100dvh - 15rem)" />
        </div>
      </section>
    </main>
  );
}
