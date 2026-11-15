// Small pieces of the demo control page's look: a panel sheet, its heading,
// compact buttons for dense lists, a status pill and a one-line action
// result. Colours, radii, shadows and the focus ring are the global look's
// tokens and the flow kit's ring, so the page reads as part of Stitch.
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { FOCUS_RING } from "@/components/flow/Button";

/** One sheet of the control page: light paper on the page, a hairline edge, a soft shadow. */
export function Panel(props: {
  title: string;
  kicker?: string;
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
  labelledBy?: string;
}) {
  const id = props.labelledBy ?? `panel-${props.title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <section
      aria-labelledby={id}
      className={`min-w-0 rounded-card border border-rule bg-paper-light/85 p-4 shadow-paper sm:p-5 ${props.className ?? ""}`}
    >
      <header className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          {props.kicker ? <p className="kicker leading-none">{props.kicker}</p> : null}
          <h2 id={id} className="mt-1 font-display text-[1.25rem]! leading-tight text-ink">
            {props.title}
          </h2>
        </div>
        {props.aside}
      </header>
      {props.children}
    </section>
  );
}

type ChipTone = "plain" | "accent" | "quiet" | "danger";

const CHIP_BASE =
  "inline-flex min-h-9 items-center justify-center gap-1.5 rounded-card px-3 py-1.5 text-sm leading-tight font-semibold " +
  "select-none transition-[background-color,border-color,color] duration-150 ease-paper motion-reduce:transition-none " +
  "disabled:cursor-not-allowed disabled:opacity-55 " +
  FOCUS_RING;

const CHIP_TONES: Record<ChipTone, string> = {
  plain: "border border-ink-faint/55 bg-paper-light text-ink not-disabled:hover:border-ink-soft not-disabled:hover:bg-paper",
  accent:
    "border border-transparent bg-accent-deep text-paper-light not-disabled:hover:bg-[color-mix(in_oklab,var(--accent-deep)_86%,var(--ink))]",
  quiet: "border border-transparent bg-transparent text-accent-deep not-disabled:hover:underline",
  danger: "border border-[#9c3b2a] bg-transparent text-[#9c3b2a] not-disabled:hover:bg-[#f7e4dc]",
};

/** A compact button for lists and toolbars (the flow kit's Button is for a form's main action). */
export function Chip(props: { tone?: ChipTone } & ButtonHTMLAttributes<HTMLButtonElement>) {
  const { tone = "plain", className, type = "button", ...rest } = props;
  return <button {...rest} type={type} className={`${CHIP_BASE} ${CHIP_TONES[tone]} ${className ?? ""}`} />;
}

type PillTone = "neutral" | "good" | "waiting" | "off" | "new";

const PILL_TONES: Record<PillTone, string> = {
  neutral: "bg-paper-deep text-ink-soft",
  good: "bg-[#e3ead9] text-[#3f5a35]",
  waiting: "bg-[#f4e6c4] text-[#7a5a14]",
  off: "bg-paper-deep text-ink-faint",
  new: "bg-accent-deep text-paper-light",
};

/** A small rounded label: a status, or the "New" mark. */
export function Pill(props: { tone?: PillTone; children: ReactNode; className?: string }) {
  const { tone = "neutral" } = props;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[0.75rem] leading-5 font-bold tracking-[0.04em] ${PILL_TONES[tone]} ${props.className ?? ""}`}
    >
      {props.children}
    </span>
  );
}

/** A dot before a status word. */
export function Dot(props: { tone: "good" | "waiting" | "off" }) {
  const color = props.tone === "good" ? "bg-[#5d8a4e]" : props.tone === "waiting" ? "bg-mustard" : "bg-ink-faint";
  return <span aria-hidden="true" className={`inline-block size-2 shrink-0 rounded-full ${color}`} />;
}

/** What an action did: pending, done, or the real error. */
export type ActionState = { kind: "idle" } | { kind: "pending"; label: string } | { kind: "done"; text: string } | { kind: "error"; text: string };

export const IDLE: ActionState = { kind: "idle" };

/** One line under an action: polite for progress and success, an alert for an error. */
export function ActionLine(props: { state: ActionState; className?: string }) {
  const { state } = props;
  const cls = `text-sm leading-snug ${props.className ?? ""}`;
  if (state.kind === "error") {
    return (
      <p role="alert" className={`${cls} rounded-paper border-l-2 border-[#9c3b2a] bg-[#f7e4dc] px-3 py-1.5 text-[#9c3b2a]`}>
        {state.text}
      </p>
    );
  }
  return (
    <p role="status" aria-live="polite" className={`${cls} ${state.kind === "idle" ? "sr-only" : "text-ink-soft"}`}>
      {state.kind === "pending" ? (
        <span className="inline-flex items-center gap-2">
          <span
            aria-hidden="true"
            className="block h-0 w-5 border-t-[1.5px] border-dashed border-accent motion-safe:animate-pulse"
          />
          {state.label}
        </span>
      ) : state.kind === "done" ? (
        state.text
      ) : (
        ""
      )}
    </p>
  );
}

/** Field label style for the dense forms here. */
export const LABEL = "block text-[0.8125rem] leading-snug font-semibold text-ink-soft";

/** A text input or textarea drawn as on the flow pages: a hairline underline that turns accent on focus. */
export const INPUT =
  "mt-1 block w-full min-w-0 rounded-none border-0 border-b border-ink-faint bg-transparent px-0 py-1.5 text-[0.9375rem] text-ink " +
  "placeholder:text-ink-faint focus:border-b-2 focus:border-accent focus:outline-none focus-visible:outline-none aria-[invalid=true]:border-[#9c3b2a]";

/** A boxed textarea for longer text (tellings, JSON). */
export const TEXTAREA =
  "mt-1 block w-full min-w-0 rounded-card border border-ink-faint/55 bg-paper-light px-3 py-2 text-[0.9375rem] leading-relaxed text-ink " +
  "placeholder:text-ink-faint focus:border-accent focus:outline-2 focus:outline-offset-1 focus:outline-accent/40 aria-[invalid=true]:border-[#9c3b2a]";

export const FIELD_ERROR = "mt-1 text-[0.8125rem] leading-snug font-medium text-[#9c3b2a]";
