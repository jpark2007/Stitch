"use client";

// Add photos to a memory (up to six) or a tradition (up to `max`): a quiet
// "Add photos" action over a real (visually hidden, still focusable) file
// input, the whole field taking dropped files, with plain square thumbnails. Each
// thumbnail owns its object URL and revokes it when it unmounts, so removing
// a photo or leaving the page never leaks, including under StrictMode. A
// photo the browser cannot draw (HEIC outside Safari) shows a paper tile with
// its kind instead of a broken image; it is still sent as it is.
import {
  useEffect,
  useId,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
} from "react";
import { FOCUS_RING_WITHIN } from "./Button";
import { FIELD_ERROR_CLASS, FIELD_HINT_CLASS, FIELD_LABEL_CLASS, FIELD_ROW_CLASS, FIELD_ROW_LABEL_CLASS } from "./Field";
import { MAX_PHOTOS, acceptPhotos } from "./photos";

function dragHasFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

/** Stable React keys for files, even if the same file appears twice. */
function fileKeys(files: File[]): string[] {
  const seen = new Map<string, number>();
  return files.map((f) => {
    const base = `${f.name}\u0000${f.size}\u0000${f.lastModified}`;
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return n === 0 ? base : `${base}\u0000${n}`;
  });
}

export function PhotoUpload(props: {
  label: ReactNode;
  files: File[];
  onChange(files: File[]): void;
  hint?: ReactNode;
  error?: string | null;
  disabled?: boolean;
  /**
   * Changed by the form on each send: the notes about files left out of the
   * last pick ("already added", "too big") are about that pick, so they go.
   */
  attempt?: number;
  /**
   * "row": the label in a left column beside the action (the add-a-memory
   * details), rather than above it. Default "stacked".
   */
  layout?: "stacked" | "row";
  /** Most photos this field takes. Defaults to MAX_PHOTOS (a memory's six). */
  max?: number;
  /** What the photos belong to, for the over-the-cap note. Defaults to "memory". */
  holder?: string;
}) {
  const {
    label,
    files,
    onChange,
    hint,
    error,
    disabled,
    attempt = 0,
    layout = "stacked",
    max = MAX_PHOTOS,
    holder,
  } = props;
  const uid = useId();
  const inputId = `${uid}-input`;
  const labelId = `${uid}-label`;
  const hintId = `${uid}-hint`;
  const countId = `${uid}-count`;
  const errorId = `${uid}-error`;
  const problemsId = `${uid}-problems`;

  const [problems, setProblems] = useState<string[]>([]);
  const [seenAttempt, setSeenAttempt] = useState(attempt);
  if (seenAttempt !== attempt) {
    setSeenAttempt(attempt);
    setProblems([]);
  }
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const removeRefs = useRef<Array<HTMLButtonElement | null>>([]);
  // After a removal, where focus should land once the list re-renders.
  const pendingFocus = useRef<number | null>(null);

  useEffect(() => {
    const target = pendingFocus.current;
    if (target === null) return;
    pendingFocus.current = null;
    if (target >= 0 && removeRefs.current[target]) removeRefs.current[target]?.focus();
    else inputRef.current?.focus();
  }, [files]);

  function add(incoming: File[]) {
    if (disabled || incoming.length === 0) return;
    const { accepted, errors } = acceptPhotos(files, incoming, max, holder);
    setProblems(errors);
    if (accepted.length > 0) onChange([...files, ...accepted.map((i) => incoming[i])]);
  }

  function handleInput(e: ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(e.target.files ?? []);
    // Clear it so picking the same file again still fires a change.
    e.target.value = "";
    add(picked);
  }

  function remove(index: number) {
    const next = files.filter((_, i) => i !== index);
    // Focus the photo that slides into this slot, else the one before, else the input.
    pendingFocus.current = next.length === 0 ? -1 : Math.min(index, next.length - 1);
    setProblems([]);
    onChange(next);
  }

  function onDragOver(e: DragEvent<HTMLElement>) {
    if (disabled || !dragHasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    if (!dragging) setDragging(true);
  }

  function onDragLeave(e: DragEvent<HTMLElement>) {
    const to = e.relatedTarget;
    if (to instanceof Node && e.currentTarget.contains(to)) return;
    setDragging(false);
  }

  function onDrop(e: DragEvent<HTMLElement>) {
    if (disabled || !dragHasFiles(e)) return;
    e.preventDefault();
    setDragging(false);
    add(Array.from(e.dataTransfer.files));
  }

  const describedBy = [
    hint ? hintId : null,
    countId,
    problems.length > 0 ? problemsId : null,
    error ? errorId : null,
  ]
    .filter(Boolean)
    .join(" ");
  const full = files.length >= max;
  const keys = fileKeys(files);
  const invalid = !!error || problems.length > 0;

  const row = layout === "row";
  // "2 of 6" once there are photos; before that only assistive tech hears it
  // (the action says "up to 6").
  const count = (
    <span
      id={countId}
      aria-live="polite"
      className={files.length > 0 ? "shrink-0 text-sm text-ink-soft tabular-nums" : "sr-only"}
    >
      {files.length} of {max}
      <span className="sr-only"> photos added</span>
    </span>
  );
  const labelEl = (
    <span id={labelId} className={row ? FIELD_ROW_LABEL_CLASS : FIELD_LABEL_CLASS}>
      {label}
    </span>
  );

  const body = (
    <>
      {hint ? (
        <p id={hintId} className={`${row ? "" : "mt-0.5 "}${FIELD_HINT_CLASS}`}>
          {hint}
        </p>
      ) : null}

      <div className={`flex flex-wrap items-center gap-x-4 ${row || !hint ? "" : "mt-1"}`}>
      {/* The action is a quiet line of text over the real (visually hidden,
          still focusable) file input. Photos can be dropped anywhere on the
          whole field (see the wrapper). */}
      <label
        htmlFor={inputId}
        className={[
          "relative -ml-1 inline-flex min-h-11 items-center gap-2 rounded-paper px-1 text-[0.9375rem] font-semibold transition-colors duration-150 ease-paper motion-reduce:transition-none",
          FOCUS_RING_WITHIN,
          disabled || full ? "cursor-not-allowed" : "cursor-pointer",
          disabled ? "opacity-60" : "",
          invalid ? "text-[#9c3b2a]" : full ? "text-ink-soft" : "text-accent-deep hover:underline hover:decoration-1 hover:underline-offset-[0.2em]",
        ].join(" ")}
      >
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          multiple
          accept="image/*"
          disabled={disabled}
          onChange={handleInput}
          aria-labelledby={labelId}
          aria-describedby={describedBy}
          aria-invalid={invalid ? true : undefined}
          className="sr-only"
        />
        <span aria-hidden="true">
          {dragging
            ? "Let go to add them"
            : full
              ? `That’s ${countWord(max)}. Remove one to add another.`
              : files.length > 0
                ? "Add more"
                : "Add photos"}
        </span>
      </label>
      {count}
      {files.length > 0 ? null : (
        <span aria-hidden="true" className="text-sm text-ink-soft">
          Up to {max}, 10 MB each
        </span>
      )}
      </div>

      {problems.length > 0 ? (
        <div id={problemsId} role="alert" className="mt-1.5 space-y-1">
          {problems.map((msg, i) => (
            <p key={i} className={FIELD_ERROR_CLASS}>
              {msg}
            </p>
          ))}
        </div>
      ) : null}
      {error ? (
        <p id={errorId} className={`mt-1.5 ${FIELD_ERROR_CLASS}`}>
          {error}
        </p>
      ) : null}

      {files.length > 0 ? (
        <ul
          aria-label="Photos added"
          className="mt-3 flex list-none flex-wrap gap-x-4 gap-y-4 p-0 pt-2"
        >
          {files.map((file, i) => (
            <li key={keys[i]}>
              <Thumb
                file={file}
                disabled={disabled}
                onRemove={() => remove(i)}
                registerButton={(el) => {
                  removeRefs.current[i] = el;
                }}
              />
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );

  // Photos can be dropped anywhere on the field, not only on the action.
  const dropProps = {
    onDragOver,
    onDragEnter: onDragOver,
    onDragLeave,
    onDrop,
  };
  const dropRing = dragging ? "rounded-card outline-2 outline-offset-4 outline-dashed outline-accent" : "";

  if (row) {
    return (
      <div {...dropProps} className={`${FIELD_ROW_CLASS} ${dropRing}`}>
        {labelEl}
        <div className="min-w-0">{body}</div>
      </div>
    );
  }
  return (
    <div {...dropProps} className={dropRing || undefined}>
      {labelEl}
      {body}
    </div>
  );
}

function Thumb({
  file,
  disabled,
  onRemove,
  registerButton,
}: {
  file: File;
  disabled?: boolean;
  onRemove(): void;
  registerButton(el: HTMLButtonElement | null): void;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  // The browser could not draw this file (a HEIC photo outside Safari, say).
  const [unreadable, setUnreadable] = useState(false);

  // The object URL lives exactly as long as this thumbnail shows this file.
  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    const url = URL.createObjectURL(file);
    img.src = url;
    return () => {
      img.removeAttribute("src");
      URL.revokeObjectURL(url);
    };
  }, [file]);

  return (
    <figure className="relative m-0 w-20 sm:w-24">
      {/* Local blob preview; next/image can't optimise object URLs. The
          caption below names the photo, so the image itself is decorative. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        ref={imgRef}
        alt=""
        onError={() => setUnreadable(true)}
        className={`aspect-square w-full rounded-paper bg-paper-deep object-cover text-[0px] ${unreadable ? "hidden" : "block"}`}
      />
      {unreadable ? (
        <span
          aria-hidden="true"
          className="flex aspect-square w-full flex-col items-center justify-center gap-0.5 rounded-paper bg-paper-deep text-center"
        >
          <span className="font-display text-sm tracking-wide text-ink-soft">{photoKind(file)}</span>
          <span className="text-[10px] leading-tight text-ink-soft">no preview</span>
        </span>
      ) : null}
      <figcaption className="mt-1.5 truncate text-xs leading-tight text-ink-soft">
        {file.name}
      </figcaption>
      <button
        ref={registerButton}
        type="button"
        onClick={onRemove}
        disabled={disabled}
        aria-label={`Remove ${file.name}`}
        className="group absolute -top-4 -right-4 flex h-11 w-11 items-center justify-center rounded-full focus-visible:outline-none disabled:cursor-not-allowed"
      >
        <span
          aria-hidden="true"
          // The kit focus ring, drawn on the visible 24px circle.
          className="flex h-6 w-6 items-center justify-center rounded-full border border-rule bg-paper-light text-ink-soft transition-colors duration-150 ease-paper group-hover:border-accent group-hover:text-accent-deep group-focus-visible:outline-2 group-focus-visible:outline-offset-3 group-focus-visible:outline-dashed group-focus-visible:outline-accent group-disabled:opacity-60 motion-reduce:transition-none"
        >
          <svg viewBox="0 0 12 12" className="h-2.5 w-2.5">
            <path
              d="M2.5 2.5 9.5 9.5M9.5 2.5 2.5 9.5"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </svg>
        </span>
      </button>
    </figure>
  );
}

const COUNT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];

/** "six" for 6, "ten" for 10; digits past twelve. */
function countWord(n: number): string {
  return COUNT_WORDS[n] ?? String(n);
}

/** A short name for a photo's kind ("HEIC", "JPEG"), from its extension or type. */
function photoKind(file: File): string {
  const dot = file.name.lastIndexOf(".");
  const ext = dot > 0 ? file.name.slice(dot + 1) : "";
  if (ext && ext.length <= 5) return ext.toUpperCase();
  const sub = file.type.split("/")[1] ?? "";
  return sub ? sub.toUpperCase() : "Photo";
}
