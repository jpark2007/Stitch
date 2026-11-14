"use client";

// For someone in more than one family (spouses often belong to both sides):
// a short labelled list of their families, each a link to that family's tree,
// with the one they are in now marked. Renders nothing for one family or none.
import Link from "next/link";
import { useId } from "react";
import type { FamilySummary } from "@/lib/types";
import { roleLabel, shouldShowSwitcher } from "./accountForms";

export function FamilySwitcher(props: {
  families: FamilySummary[];
  currentId: string;
  hrefFor: (fid: string) => string;
}) {
  const { families, currentId, hrefFor } = props;
  const labelId = `${useId()}families`;

  if (!shouldShowSwitcher(families)) return null;

  return (
    <nav aria-labelledby={labelId} className="min-w-0">
      <p id={labelId} className="text-sm font-semibold text-ink-soft">
        Your families
      </p>
      <ul className="mt-2 list-none space-y-1.5 p-0">
        {families.map((f) => {
          const current = f.id === currentId;
          return (
            <li key={f.id} className="min-w-0">
              <Link
                href={hrefFor(f.id)}
                aria-current={current ? "true" : undefined}
                className={[
                  "flex min-h-11 min-w-0 items-baseline justify-between gap-3 rounded-[var(--radius-paper,2px)] border border-l-[3px] px-3 py-2 no-underline",
                  "transition-[background-color,border-color] duration-150 motion-reduce:transition-none",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                  current
                    ? "border-rule border-l-accent bg-paper-light shadow-paper"
                    : "border-transparent hover:border-rule hover:bg-paper-light/70",
                ].join(" ")}
              >
                <span className="min-w-0 font-display break-words text-[1.0625rem] leading-snug text-ink">
                  {f.name}
                </span>
                <span className="shrink-0 text-sm text-ink-soft">{roleLabel(f.role)}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
