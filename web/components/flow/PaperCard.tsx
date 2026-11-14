// A sheet of light paper lifted off the page: nearly square corners, a
// hairline rule edge and a soft warm shadow. Inside a flow page (FlowFrame
// marks its <main> with data-flow-page) the sheet goes flat: no edge, fill,
// shadow or padding, so a form sits straight on the paper and whitespace and
// type do the work. Elsewhere (the tree home's prompts) it keeps the sheet.
import type { FormHTMLAttributes, HTMLAttributes, ReactNode, Ref } from "react";

type Common = { className?: string; children: ReactNode };

type SheetProps = Common & {
  as?: "section" | "div";
} & Omit<HTMLAttributes<HTMLElement>, "children" | "className">;

type FormSheetProps = Common & {
  as: "form";
  ref?: Ref<HTMLFormElement>;
} & Omit<FormHTMLAttributes<HTMLFormElement>, "children" | "className">;

// `!`: the flat look must beat the sheet's own sm:p-8 whatever the CSS order.
const FLAT_IN_FLOW =
  "in-[[data-flow-page]]:rounded-none! in-[[data-flow-page]]:border-0! in-[[data-flow-page]]:bg-transparent! " +
  "in-[[data-flow-page]]:p-0! in-[[data-flow-page]]:shadow-none!";

const SHEET =
  "relative block w-full min-w-0 rounded-paper border border-rule bg-paper-light p-6 text-ink sm:p-8 shadow-lift " +
  FLAT_IN_FLOW;

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
