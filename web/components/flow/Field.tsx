"use client";

// Labelled form fields for the flow pages. Field is the shell (a real
// <label htmlFor>, hint, error, all wired with aria-describedby); TextField,
// TextAreaField and SelectField wrap the native controls in it. Errors are
// plain text linked to the control (no role): the form-level FormError is the
// one alert, and focusFirstError moves focus to the first invalid control,
// whose description then reads the message.
import {
  useCallback,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type InputHTMLAttributes,
  type ReactNode,
  type Ref,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

// One text style per role for every field in the kit (Field, YearInput,
// PhotoUpload, PersonPicker, RelationshipPicker, AddMemoryForm), so labels,
// legends, hints and errors read the same everywhere. Spacing rhythm: hint
// mt-0.5 under the label, the control mt-1 under that, the error mt-1.5 below.
// The look is quiet on purpose: small soft labels, and controls drawn as one
// hairline underline on the paper (no boxes, no fills) that turns accent and
// doubles while the control has focus.
// Client components only: this module is "use client".

/** A field's <label>, a fieldset's <legend>, or the label of a custom control. */
export const FIELD_LABEL_CLASS = "block text-[0.9375rem] leading-snug font-medium text-ink-soft";

/**
 * A details row: from sm up, the label in a narrow left column and the
 * control beside it, every row's label column the same width so the controls
 * line up; on a phone the label sits on its own line above. Labels sit level
 * with the first 44px line of their control. In a narrow column that is a
 * size container (the book desk's writing beside its book, under 34rem) the
 * label sits above, as on a phone; with no container (every other page) the
 * container rules never apply.
 */
export const FIELD_ROW_CLASS =
  "sm:grid sm:grid-cols-[9rem_minmax(0,1fr)] sm:items-start sm:gap-x-6 @max-[34rem]:grid-cols-1 @max-[34rem]:gap-y-1";

/** A label in a details row's left column. */
export const FIELD_ROW_LABEL_CLASS = `${FIELD_LABEL_CLASS} sm:flex sm:min-h-11 sm:items-center @max-[34rem]:min-h-0`;

/** The quiet "(optional)" after a label. */
export const FIELD_OPTIONAL_CLASS = "ml-1.5 font-normal text-ink-soft";

/**
 * The quiet "(optional)" after a label, led by a real space so the label's
 * accessible name reads "Title (optional)" rather than "Title(optional)"
 * (a margin alone does not separate words for screen readers).
 */
export function OptionalMark() {
  return (
    <>
      {" "}
      <span className="font-normal text-ink-soft">(optional)</span>
    </>
  );
}

/** A hint under a label. Add mt-0.5. */
export const FIELD_HINT_CLASS = "text-sm leading-snug text-ink-soft";

/**
 * A field's error message. Add mt-1.5. The error red (#9c3b2a, 6.2:1 on
 * paper-light) has no token in globals.css yet.
 */
export const FIELD_ERROR_CLASS = "text-sm leading-snug font-medium break-words text-[#9c3b2a]";

/** What Field hands its control: spread it onto the input. */
type ControlProps = {
  id: string;
  "aria-describedby"?: string;
  "aria-invalid"?: true;
  required?: boolean;
};

type FieldText = {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  /** Show a quiet "(optional)" after the label. */
  optionalLabel?: boolean;
};

function joinIds(...ids: (string | undefined | null | false)[]): string | undefined {
  const s = ids.filter(Boolean).join(" ");
  return s || undefined;
}

export function Field(
  props: FieldText & {
    required?: boolean;
    id?: string;
    /** Read the label to assistive tech only. */
    labelHidden?: boolean;
    children: (control: ControlProps) => ReactNode;
  },
) {
  const { label, hint, error, required, optionalLabel = false, labelHidden = false, id: givenId, children } = props;
  const uid = useId();
  const id = givenId ?? `${uid}control`;
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const hasHint = hint != null && hint !== false && hint !== "";
  const hasError = typeof error === "string" && error.trim() !== "";

  const control: ControlProps = { id };
  // The error first, so it is what a screen reader reads right after the label.
  // A hint that says exactly what the error says is not read a second time.
  const hintRepeatsError = hasError && typeof hint === "string" && hint.trim() === error.trim();
  const describedBy = joinIds(hasError && errorId, hasHint && !hintRepeatsError && hintId);
  if (describedBy) control["aria-describedby"] = describedBy;
  if (hasError) control["aria-invalid"] = true;
  if (required) control.required = true;

  return (
    <div className="min-w-0">
      <label htmlFor={id} className={labelHidden ? "sr-only" : FIELD_LABEL_CLASS}>
        {label}
        {optionalLabel ? <OptionalMark /> : null}
      </label>
      {hasHint ? (
        <p id={hintId} className={`mt-0.5 ${FIELD_HINT_CLASS}`}>
          {hint}
        </p>
      ) : null}
      <div className={labelHidden && !hasHint ? undefined : "mt-1"}>{children(control)}</div>
      {hasError ? (
        <p id={errorId} className={`mt-1.5 ${FIELD_ERROR_CLASS}`}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The focus mark of an underlined control: the 1px resting line turns accent
 * and triples to 3px (an inset shadow, so nothing shifts), so focus differs
 * from rest in both hue and weight. The kit's dashed ring would draw a box
 * round a control that has none. Forced-colours mode drops shadows, so there
 * it gets a real outline instead.
 */
export const CONTROL_FOCUS =
  "focus:outline-none focus:border-accent-deep focus:shadow-[inset_0_-2px_0_var(--accent-deep)] " +
  "forced-colors:focus:outline-2 forced-colors:focus:outline-offset-2 forced-colors:focus:outline-solid";

// A line to write on: no box and no fill, one hairline under the text. The
// line is the control's only visible edge, so it is ink-soft (#5c4e43): about
// 5.7:1 even on the darkest part of the textured page background, well above
// the 3:1 WCAG 1.4.11 asks of an input boundary (ink-faint measured as low as
// 2.3:1 on the texture). Placeholders are ink-soft at 90% (about 4.6:1 on the
// darkest texture), lighter than typed ink. Flush left (px-0), so what is
// typed lines up with the label above it.
const CONTROL_BASE =
  "block min-w-0 min-h-11 rounded-none border-0 border-b bg-transparent px-0 py-2 " +
  "font-sans text-base leading-normal text-ink placeholder:text-ink-soft/90 " +
  "transition-[border-color,box-shadow] duration-150 ease-paper motion-reduce:transition-none " +
  "hover:border-ink " +
  CONTROL_FOCUS +
  " disabled:cursor-not-allowed disabled:opacity-60";

/**
 * The class list of a kit text control (an underlined line), for a native
 * input a page lays out itself. Full width unless `extra` sets a width.
 */
export function fieldControlClass(invalid: boolean, extra?: string): string {
  const border = invalid
    ? "border-[#9c3b2a] hover:border-[#9c3b2a] shadow-[inset_0_-1px_0_#9c3b2a]"
    : "border-ink-soft";
  const width = extra && /(^|\s)w-/.test(extra) ? "" : " w-full";
  return `${CONTROL_BASE}${width} ${border}${extra ? ` ${extra}` : ""}`;
}

const controlClass = fieldControlClass;

/** A labelled <input>. `className` goes on the input itself. */
export function TextField(
  props: FieldText & { ref?: Ref<HTMLInputElement> } & InputHTMLAttributes<HTMLInputElement>,
) {
  const {
    label,
    hint,
    error,
    optionalLabel,
    id,
    required,
    className,
    "aria-describedby": extraDescribedBy,
    ...rest
  } = props;
  return (
    <Field label={label} hint={hint} error={error} optionalLabel={optionalLabel} id={id} required={required}>
      {(c) => (
        <input
          {...rest}
          {...c}
          aria-describedby={joinIds(c["aria-describedby"], extraDescribedBy)}
          className={controlClass(c["aria-invalid"] === true, className)}
        />
      )}
    </Field>
  );
}

const countFormat = new Intl.NumberFormat("en-US");

/**
 * A labelled <textarea>, at least five rows tall, that grows with its text
 * (CSS field-sizing where supported, else measured as it changes), so it
 * never needs a resize handle. With `maxLength` it shows "120 of 2,000
 * characters" under the hint. `labelHidden` keeps the label for assistive
 * tech only (for a writing area whose placeholder and place on the page say
 * what it is). `className` goes on the textarea itself.
 */
export function TextAreaField(
  props: FieldText & {
    ref?: Ref<HTMLTextAreaElement>;
    /** Read the label to assistive tech only. */
    labelHidden?: boolean;
  } & TextareaHTMLAttributes<HTMLTextAreaElement>,
) {
  const {
    label,
    hint,
    error,
    optionalLabel,
    labelHidden,
    id,
    required,
    className,
    "aria-describedby": extraDescribedBy,
    maxLength,
    rows,
    style,
    value,
    defaultValue,
    onChange,
    ref,
    ...rest
  } = props;
  const minRows = Math.max(5, rows ?? 5);
  const [typed, setTyped] = useState(() => (defaultValue == null ? 0 : String(defaultValue).length));
  const count = value == null ? typed : String(value).length;

  // Grow with the text where CSS field-sizing is missing (Firefox): reset the
  // height, then take the content's. The min-height style keeps the floor.
  const own = useRef<HTMLTextAreaElement | null>(null);
  const setRef = useCallback(
    (el: HTMLTextAreaElement | null) => {
      own.current = el;
      if (typeof ref === "function") ref(el);
      else if (ref) ref.current = el;
    },
    [ref],
  );
  useLayoutEffect(() => {
    const el = own.current;
    if (!el || (typeof CSS !== "undefined" && CSS.supports?.("field-sizing", "content"))) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [value, typed]);

  const counter =
    maxLength != null && maxLength > 0 ? (
      <span className="tabular-nums">
        {countFormat.format(count)} of {countFormat.format(maxLength)} characters
      </span>
    ) : null;
  const fullHint =
    counter && hint != null && hint !== false && hint !== "" ? (
      <span className="flex flex-wrap justify-between gap-x-4 gap-y-0.5">
        <span>{hint}</span> {counter}
      </span>
    ) : (
      (counter ?? hint)
    );

  return (
    <Field
      label={label}
      hint={fullHint}
      error={error}
      optionalLabel={optionalLabel}
      labelHidden={labelHidden}
      id={id}
      required={required}
    >
      {(c) => (
        <textarea
          {...rest}
          {...c}
          ref={setRef}
          rows={minRows}
          maxLength={maxLength}
          value={value}
          defaultValue={defaultValue}
          onChange={(e) => {
            setTyped(e.target.value.length);
            onChange?.(e);
          }}
          aria-describedby={joinIds(c["aria-describedby"], extraDescribedBy)}
          style={{ minHeight: `calc(${minRows}lh + 1rem + 1px)`, ...style }}
          className={controlClass(
            c["aria-invalid"] === true,
            `field-sizing-content resize-none overflow-hidden leading-relaxed${className ? ` ${className}` : ""}`,
          )}
        />
      )}
    </Field>
  );
}

/** A labelled native <select> with a drawn chevron. Pass <option>s as children. */
export function SelectField(
  props: FieldText & { children: ReactNode; ref?: Ref<HTMLSelectElement> } & SelectHTMLAttributes<HTMLSelectElement>,
) {
  const {
    label,
    hint,
    error,
    optionalLabel,
    id,
    required,
    className,
    children,
    "aria-describedby": extraDescribedBy,
    ...rest
  } = props;
  return (
    <Field label={label} hint={hint} error={error} optionalLabel={optionalLabel} id={id} required={required}>
      {(c) => (
        <div className="relative">
          <select
            {...rest}
            {...c}
            aria-describedby={joinIds(c["aria-describedby"], extraDescribedBy)}
            className={controlClass(
              c["aria-invalid"] === true,
              `cursor-pointer appearance-none pr-7${className ? ` ${className}` : ""}`,
            )}
          >
            {children}
          </select>
          <svg
            aria-hidden="true"
            viewBox="0 0 16 16"
            className="pointer-events-none absolute top-1/2 right-0.5 size-4 -translate-y-1/2 text-ink-soft"
          >
            <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      )}
    </Field>
  );
}
