"use client";

// Add up to six photos to a memory: a paper drop area over a real (visually
// hidden, still focusable) file input, with taped-photo thumbnails. Each
// thumbnail owns its object URL and revokes it when it unmounts, so removing
// a photo or leaving the page never leaks, including under StrictMode.
import {
  useEffect,
  useId,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
} from "react";
import { MAX_PHOTOS, acceptPhotos } from "./photos";

const PAPER_SHADOW =
  "var(--shadow-paper, 0 1px 1px rgb(74 47 29 / 0.08), 0 2px 6px rgb(74 47 29 / 0.08))";

// A gentle, fixed tilt per slot so the row looks hand-placed but stays tidy.
const TILTS = [-2.5, 1.5, -1, 2.5, -1.5, 1];

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
}) {
  const { label, files, onChange, hint, error, disabled } = props;
  const uid = useId();
  const inputId = `${uid}-input`;
  const labelId = `${uid}-label`;
  const hintId = `${uid}-hint`;
  const countId = `${uid}-count`;
  const errorId = `${uid}-error`;
  const problemsId = `${uid}-problems`;

  const [problems, setProblems] = useState<string[]>([]);
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
    const { accepted, errors } = acceptPhotos(files, incoming);
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

  function onDragOver(e: DragEvent<HTMLLabelElement>) {
    if (disabled || !dragHasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    if (!dragging) setDragging(true);
  }

  function onDragLeave(e: DragEvent<HTMLLabelElement>) {
    const to = e.relatedTarget;
    if (to instanceof Node && e.currentTarget.contains(to)) return;
    setDragging(false);
  }

  function onDrop(e: DragEvent<HTMLLabelElement>) {
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
  const full = files.length >= MAX_PHOTOS;
  const keys = fileKeys(files);
  const invalid = !!error || problems.length > 0;

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span id={labelId} className="text-sm font-medium text-[var(--ink,#2f2620)]">
          {label}
        </span>
        <span
          id={countId}
          aria-live="polite"
          className="text-xs text-[var(--ink-faint,#8f8173)] tabular-nums"
        >
          {files.length} of {MAX_PHOTOS}
          <span className="sr-only"> photos added</span>
        </span>
      </div>
      {hint ? (
        <p id={hintId} className="mt-1 text-sm leading-relaxed text-[var(--ink-faint,#8f8173)]">
          {hint}
        </p>
      ) : null}

      <label
        htmlFor={inputId}
        onDragOver={onDragOver}
        onDragEnter={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        style={{ boxShadow: PAPER_SHADOW }}
        className={[
          "relative mt-2 flex min-h-28 flex-col items-center justify-center gap-1 rounded-[var(--radius-card,6px)] border-2 border-dashed px-4 py-5 text-center transition-colors",
          "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[var(--accent,#b9573d)] has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-[var(--paper,#f5eee1)]",
          disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
          dragging
            ? "border-[var(--accent,#b9573d)] bg-[var(--paper-deep,#ebe0cb)]"
            : invalid
              ? "border-[#9c3b2a] bg-[var(--paper-light,#fbf8f1)]"
              : "border-[var(--rule,#2f262024)] bg-[var(--paper-light,#fbf8f1)] hover:border-[var(--accent,#b9573d)]",
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
        <PhotoIcon />
        <span aria-hidden="true" className="text-sm text-[var(--ink-soft,#5c4e43)]">
          {dragging ? (
            "Let go to add them"
          ) : full ? (
            "That’s six. Remove one to add another."
          ) : (
            <>
              Drop photos here, or{" "}
              <span className="font-medium text-[var(--accent-deep,#8f3f2a)] underline decoration-dotted underline-offset-4">
                choose from your device
              </span>
            </>
          )}
        </span>
        <span aria-hidden="true" className="text-xs text-[var(--ink-faint,#8f8173)]">
          Up to {MAX_PHOTOS}, 10 MB each
        </span>
      </label>

      {problems.length > 0 ? (
        <div id={problemsId} role="alert" className="mt-2 space-y-1">
          {problems.map((msg, i) => (
            <p key={i} className="text-sm text-[#9c3b2a]">
              {msg}
            </p>
          ))}
        </div>
      ) : null}
      {error ? (
        <p id={errorId} className="mt-2 text-sm text-[#9c3b2a]">
          {error}
        </p>
      ) : null}

      {files.length > 0 ? (
        <ul aria-label="Photos added" className="mt-4 flex list-none flex-wrap gap-x-5 gap-y-6 p-0 pt-2">
          {files.map((file, i) => (
            <li key={keys[i]}>
              <Thumb
                file={file}
                tilt={TILTS[i % TILTS.length]}
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
    </div>
  );
}

function Thumb({
  file,
  tilt,
  disabled,
  onRemove,
  registerButton,
}: {
  file: File;
  tilt: number;
  disabled?: boolean;
  onRemove(): void;
  registerButton(el: HTMLButtonElement | null): void;
}) {
  const imgRef = useRef<HTMLImageElement>(null);

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
    <figure
      className="relative m-0 w-24 bg-[var(--paper-light,#fbf8f1)] p-1.5 pb-5"
      style={{ transform: `rotate(${tilt}deg)`, boxShadow: PAPER_SHADOW }}
    >
      {/* Masking tape holding the photo down. */}
      <span
        aria-hidden="true"
        className="absolute -top-2.5 left-1/2 h-4 w-12 bg-[#e9dcbd]/80 mix-blend-multiply"
        style={{ transform: `translateX(-50%) rotate(${-tilt * 1.6}deg)` }}
      />
      {/* Local blob preview; next/image can't optimise object URLs. The
          caption below names the photo, so the image itself is decorative. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        ref={imgRef}
        alt=""
        className="block aspect-square w-full bg-[var(--paper-deep,#ebe0cb)] object-cover text-[0px]"
      />
      <figcaption className="mt-1 truncate text-center text-[11px] leading-tight text-[var(--ink-faint,#8f8173)]">
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
          className="flex h-6 w-6 items-center justify-center rounded-full border border-[var(--rule,#2f262024)] bg-[var(--paper-light,#fbf8f1)] text-[var(--ink-soft,#5c4e43)] shadow-[0_1px_2px_rgb(74_47_29/0.2)] transition-colors group-hover:border-[var(--accent,#b9573d)] group-hover:text-[var(--accent-deep,#8f3f2a)] group-focus-visible:ring-2 group-focus-visible:ring-[var(--accent,#b9573d)] group-focus-visible:ring-offset-1 group-disabled:opacity-60"
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

function PhotoIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 32 32"
      className="mb-1 h-7 w-7 text-[var(--ink-faint,#8f8173)]"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
    >
      <rect x="5" y="7" width="22" height="18" rx="1.5" transform="rotate(-4 16 16)" />
      <path d="m8 22 6-6 4 4 3-3 4 4" strokeLinecap="round" transform="rotate(-4 16 16)" />
      <circle cx="20.5" cy="12.5" r="1.8" transform="rotate(-4 16 16)" />
    </svg>
  );
}
