"use client";

/**
 * A button that opens a small paper menu under it: the family switcher and
 * the list of moments kept off the timeline. Closes on Escape (focus goes
 * back to the button), on a click outside, and when an item is chosen.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

export function MenuDisclosure(props: {
  label: ReactNode;
  /** Accessible name when `label` is not plain text. */
  ariaLabel?: string;
  buttonClassName?: string;
  className?: string;
  /** Wraps the button, e.g. in a heading; the menu stays outside it. */
  trigger?: (button: ReactNode) => ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      setOpen(false);
      button.current?.focus();
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  const btn = (
    <button
      ref={button}
      type="button"
      aria-expanded={open}
      aria-controls={open ? id : undefined}
      aria-label={props.ariaLabel}
      onClick={() => setOpen((o) => !o)}
      className={`max-w-full cursor-pointer text-left ${props.buttonClassName ?? ""}`}
    >
      {props.label}
    </button>
  );

  return (
    <div ref={wrap} className={`relative max-w-full ${props.className ?? "inline-block"}`}>
      {props.trigger ? props.trigger(btn) : btn}
      {open ? (
        // Choosing an item (a link or button inside) closes the menu.
        <div
          id={id}
          className="threads-menu left-0 top-full mt-1.5"
          onClick={(e) => {
            if ((e.target as HTMLElement).closest("a,button")) setOpen(false);
          }}
        >
          {props.children}
        </div>
      ) : null}
    </div>
  );
}
