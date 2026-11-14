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
  "relative block w-full min-w-0 rounded-paper border border-rule bg-paper-light p-6 text-ink sm:p-8 shadow-lift";

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
