// Buttons for the flow pages. One primary action per form; everything else is
// secondary or quiet. Colours come from the global look's tokens (PR #27) with
// literal fallbacks so the kit reads right before and after it lands.
import Link from "next/link";
import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";

type Variant = "primary" | "secondary" | "quiet" | "danger";

// Shared shape: at least 44px tall, nearly square corners, accent focus ring
// (an outline, so it survives forced-colours mode).
const BASE =
  "relative inline-flex min-h-11 items-center justify-center rounded-[var(--radius-card,6px)] px-5 py-2.5 " +
  "font-sans text-base font-semibold leading-tight no-underline select-none " +
  "transition-[background-color,border-color,color,box-shadow] duration-150 motion-reduce:transition-none " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent,#b9573d)]";

// Hover only when enabled (`not-disabled:` also matches links).
const VARIANTS: Record<Variant, string> = {
  // accent-deep keeps paper-light text above 4.5:1; plain accent is just under.
  primary:
    "border border-transparent bg-[var(--accent-deep,#8f3f2a)] text-[var(--paper-light,#fbf8f1)] " +
    "shadow-[0_1px_0_rgb(255_255_255/0.18)_inset,0_1px_2px_rgb(74_47_29/0.25)] " +
    "not-disabled:hover:bg-[color-mix(in_oklab,var(--accent-deep,#8f3f2a)_86%,var(--ink,#2f2620))]",
  secondary:
    "border border-[var(--ink-faint,#8f8173)] bg-[var(--paper-light,#fbf8f1)] text-[var(--ink,#2f2620)] " +
    "not-disabled:hover:border-[var(--ink-soft,#5c4e43)] not-disabled:hover:bg-[var(--paper,#f5eee1)]",
  quiet:
    "border border-transparent bg-transparent px-3 text-[var(--accent-deep,#8f3f2a)] underline-offset-[0.2em] " +
    "not-disabled:hover:underline not-disabled:hover:decoration-1",
  danger: "border border-[#9c3b2a] bg-transparent text-[#9c3b2a] not-disabled:hover:bg-[#f7e4dc]",
};

// Busy is "working", not "unavailable": it stays at full strength.
const DISABLED = "disabled:cursor-not-allowed disabled:opacity-60";
const BUSY = "cursor-progress";

function classes(variant: Variant, state: string, extra?: string): string {
  return [BASE, VARIANTS[variant], state, extra].filter(Boolean).join(" ");
}

/** A small dashed ring, like a loop of thread; still when motion is reduced. */
function StitchSpinner() {
  return (
    <span
      aria-hidden="true"
      className="inline-block size-4 shrink-0 animate-spin rounded-full border-2 border-dashed border-current [animation-duration:1.6s] motion-reduce:animate-none"
    />
  );
}

/**
 * A form button. Defaults to type="button"; pass type="submit" for the form's
 * one primary action. `busy` disables it, sets aria-busy and swaps in
 * `busyLabel` (or a spinner beside the children) without changing its width:
 * both labels sit in the same grid cell and the button sizes to the wider.
 * The hidden layer is visibility:hidden, so it is not read or focusable; keep
 * children to text and icons (no ids), since they render in both layers.
 */
export function Button(
  props: {
    variant?: Variant;
    busy?: boolean;
    busyLabel?: string;
    ref?: Ref<HTMLButtonElement>;
  } & ButtonHTMLAttributes<HTMLButtonElement>,
) {
  const {
    variant = "primary",
    busy = false,
    busyLabel,
    type = "button",
    disabled,
    className,
    children,
    ...rest
  } = props;

  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={classes(variant, busy ? BUSY : DISABLED, className)}
    >
      <span className="grid items-center justify-items-center">
        <span
          className={`col-start-1 row-start-1 inline-flex items-center gap-2 ${busy ? "invisible" : ""}`}
        >
          {children}
        </span>
        <span
          className={`col-start-1 row-start-1 inline-flex items-center gap-2 ${busy ? "" : "invisible"}`}
        >
          <StitchSpinner />
          {busyLabel ?? children}
        </span>
      </span>
    </button>
  );
}

/** A link that looks like a Button, for navigation (next/link). */
export function ButtonLink(props: {
  variant?: Variant;
  href: string;
  children: ReactNode;
  className?: string;
}) {
  const { variant = "primary", href, children, className } = props;
  return (
    <Link href={href} className={classes(variant, "", className)}>
      {children}
    </Link>
  );
}
