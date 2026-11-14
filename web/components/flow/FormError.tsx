// The form-level message ("That email and password don't match", network
// errors, server messages that belong to no single field) and a helper that
// moves focus to the first problem after a failed submit.

/**
 * A role="alert" box, announced when it appears. Renders nothing when
 * `message` is empty. Place it inside the form, above the primary action.
 */
export function FormError(props: { message?: string | null; title?: string }) {
  const { message, title } = props;
  if (!message || !message.trim()) return null;
  return (
    <div
      role="alert"
      tabIndex={-1}
      data-form-error=""
      className="min-w-0 break-words rounded-[var(--radius-paper,2px)] border border-[#9c3b2a]/35 border-l-[3px] border-l-[#9c3b2a] bg-[#f7e4dc] px-4 py-3 text-[0.9375rem] leading-snug text-[#9c3b2a] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent,#b9573d)]"
    >
      {title ? <p className="font-semibold">{title}</p> : null}
      <p className={title ? "mt-0.5" : undefined}>{message}</p>
    </div>
  );
}

/**
 * Focus the form's first control marked aria-invalid="true", or else its
 * FormError alert. Runs on the next frame so it sees the error state a
 * setState in the same submit handler is about to render.
 */
export function focusFirstError(form: HTMLFormElement | null): void {
  if (!form) return;
  const run = () => {
    if (!form.isConnected) return;
    const target =
      form.querySelector<HTMLElement>('[aria-invalid="true"]') ??
      form.querySelector<HTMLElement>("[data-form-error]");
    target?.focus();
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
  else setTimeout(run, 0);
}
