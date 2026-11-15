"use client";

// The add-a-memory page as a desk: the writing on the left, the teller's own
// book lying open on the table to the right (desktop widths). Once the story
// is sent the book takes more of the desk and the writing folds to a quiet
// summary. On narrower windows the page is one column and the book, during a
// run, is a small picture inside it (AddRun places it).
//
// Every /add screen (the loading sheets too) sits in the same desk, so the
// writing column never moves sideways when the page arrives.
import { useSyncExternalStore, type ReactNode } from "react";

/** From this width the book lies beside the writing. Matches Tailwind's `lg`. */
const WIDE = "(min-width: 1024px)";

const subscribeWide = (cb: () => void) => {
  const m = window.matchMedia(WIDE);
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
};

/** True when the book lies beside the writing (false while prerendering). */
export function useWideDesk(): boolean {
  return useSyncExternalStore(subscribeWide, () => window.matchMedia(WIDE).matches, () => false);
}

const TABLE = {
  background:
    "radial-gradient(80% 55% at 50% 46%, rgb(255 214 163 / 0.07), transparent 70%), #1a110b",
} as const;

export function AddDesk(props: {
  /** "write": the form and the book side by side; "run": the book takes the stage. */
  mode: "write" | "run";
  /** The book, shown beside the page on a wide window; null while the page loads. */
  book: ReactNode;
  children: ReactNode;
}) {
  const { mode, book, children } = props;
  const wide = useWideDesk();
  return (
    <div
      className={`lg:grid lg:min-h-dvh lg:transition-[grid-template-columns] lg:duration-700 lg:ease-paper motion-reduce:transition-none ${
        mode === "run" ? "lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]" : "lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
      }`}
    >
      <div className="min-w-0">{children}</div>
      {wide ? (
        <aside aria-label="Your book" className="sticky top-0 hidden h-dvh min-w-0 overflow-hidden lg:flex lg:items-center" style={TABLE}>
          <div className="mx-auto flex h-full w-full max-w-[min(100%,calc((100dvh-9rem)*1.05))] items-center px-2">
            {book ?? (
              <p className="w-full text-center font-hand text-2xl text-[#f3e6cf]/50">opening your book&hellip;</p>
            )}
          </div>
        </aside>
      ) : null}
    </div>
  );
}
