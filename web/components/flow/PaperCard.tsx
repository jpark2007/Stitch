// A sheet of light paper lifted off the page: the container for every flow
// form. Nearly square corners, a hairline rule edge and a soft warm shadow.
import type { FormHTMLAttributes, HTMLAttributes, ReactNode, Ref } from "react";

type Common = { className?: string; children: ReactNode };

type SheetProps = Common & {
  as?: "section" | "div";
} & Omit<HTMLAttributes<HTMLElement>, "children" | "className">;

type FormSheetProps = Common & {
  as: "form";
  ref?: Ref<HTMLFormElement>;
} & Omit<FormHTMLAttributes<HTMLFormElement>, "children" | "className">;

const SHEET =
  "relative block w-full min-w-0 rounded-[var(--radius-paper,2px)] border border-[var(--rule,#2f262024)] " +
  "bg-[var(--paper-light,#fbf8f1)] p-6 text-[var(--ink,#2f2620)] sm:p-8 " +
  "shadow-[0_2px_3px_rgb(74_47_29/0.1),0_10px_24px_-6px_rgb(74_47_29/0.22)]";

/**
 * `as` picks the element: "section" (default), "div", or "form" (which then
 * takes form props such as onSubmit, noValidate and action).
 */
export function PaperCard(props: SheetProps | FormSheetProps) {
  if (props.as === "form") {
    const { as: _as, className, children, ...rest } = props;
    void _as;
    return (
      <form {...rest} className={className ? `${SHEET} ${className}` : SHEET}>
        {children}
      </form>
    );
  }
  const { as: Tag = "section", className, children, ...rest } = props;
  return (
    <Tag {...rest} className={className ? `${SHEET} ${className}` : SHEET}>
      {children}
    </Tag>
  );
}
