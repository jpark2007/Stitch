// The form-level message ("That email and password don't match", network
// errors, server messages that belong to no single field), a helper that
// moves focus to the first problem after a failed submit, and two hooks for a
// form the server may render before React takes it over. Imported only by
// client components.
import { useLayoutEffect, useSyncExternalStore, type RefObject } from "react";
import { FOCUS_RING } from "./Button";

// The error red (#9c3b2a) and its wash (#f7e4dc) have no tokens in
// globals.css yet, so they stay literal here, in Field and in Button.

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
      // Dimmed while its form is busy: a form that keeps the last message up
      // during a retry (so nothing shifts under the pointer) shows it is stale.
      className={`min-w-0 break-words rounded-paper border-l-2 border-[#9c3b2a] bg-[#f7e4dc] px-4 py-3 text-[0.9375rem] leading-snug text-[#9c3b2a] transition-opacity duration-150 ease-paper motion-reduce:transition-none in-[form[aria-busy=true]]:opacity-55 ${FOCUS_RING}`}
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

const subscribeNothing = () => () => {};

/**
 * False in the server's HTML and while React hydrates it, true from then on
 * (and at once in a render that never came from the server). A form disables
 * its submit button until then: before hydration a press would be the
 * browser's own GET submit, with the password in the URL.
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    subscribeNothing,
    () => true,
    () => false,
  );
}

/**
 * Keep what was typed into a server-rendered form before React took it over.
 * Hydration leaves those values in the DOM but not in state, and the next
 * render of a controlled input would put the empty state back. On mount,
 * before paint, this reads each named control of `form` and hands the values
 * to `adopt` (merge them with adoptTypedValues from accountForms.ts, which
 * changes nothing when DOM and state agree). Field names are the controls'
 * `name` attributes. Pass a stable `adopt` (useCallback).
 *
 * It runs on every mount, not once: strict mode's dev-only unmount and
 * remount runs a form's cleanups in between (LoginForm's clears the
 * password), and the second run takes the typed values back from the DOM,
 * which nothing has re-rendered yet. A hidden page shown again
 * (cacheComponents) has DOM and state in agreement, so it is a no-op there.
 */
export function useTypedBeforeHydration<K extends string>(
  form: RefObject<HTMLFormElement | null>,
  fields: readonly K[],
  adopt: (typed: Partial<Record<K, string>>) => void,
): void {
  useLayoutEffect(() => {
    const el = form.current;
    if (!el) return;
    const typed: Partial<Record<K, string>> = {};
    for (const name of fields) {
      const control = el.elements.namedItem(name);
      if (
        control instanceof HTMLInputElement ||
        control instanceof HTMLTextAreaElement ||
        control instanceof HTMLSelectElement
      ) {
        typed[name] = control.value;
      }
    }
    adopt(typed);
  }, [form, fields, adopt]);
}
