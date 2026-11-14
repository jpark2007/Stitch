"use client";

// The photos half of "Make a film": a big drop-or-choose well over a real
// (visually hidden, still focusable) multiple file input, a count with a slim
// meter that makes the 8-to-40 rule plain, and a grid of square thumbnails.
// Each thumbnail shows its upload: waiting (dimmed), sending (a turning
// thread), sent (a small tick) or failed (a red edge and "Try again"). Each
// thumbnail owns its object URL and revokes it when it unmounts.
import { useEffect, useId, useRef, useState, type ChangeEvent, type DragEvent } from "react";

import { FOCUS_RING, FOCUS_RING_WITHIN } from "@/components/flow/Button";
import { FIELD_ERROR_CLASS, FIELD_LABEL_CLASS } from "@/components/flow/Field";

import { FILM_ACCEPT, MAX_FILM_PHOTOS, MIN_FILM_PHOTOS, photoCountLine, type UploadStatus } from "./filmForm";

export interface PickedPhoto {
  key: string;
  file: File;
  status: UploadStatus;
  mediaId: string | null;
  /** Why it did not send, when it failed. */
  error: string | null;
}

function dragHasFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

export function FilmPhotoPicker(props: {
  items: PickedPhoto[];
  onAdd(files: File[]): void;
  onRemove(key: string): void;
  onRetry(key: string): void;
  /** Uploading or starting the film: nothing can be added or removed. */
  locked: boolean;
  /** Notes about files left out of the last pick. */
  notes: string[];
  error?: string | null;
}) {
  const { items, onAdd, onRemove, onRetry, locked, notes, error } = props;
  const uid = useId();
  const inputId = `${uid}-input`;
  const countId = `${uid}-count`;
  const notesId = `${uid}-notes`;
  const [dragging, setDragging] = useState(false);

  const count = items.length;
  const line = photoCountLine(count);
  const full = count >= MAX_FILM_PHOTOS;
  const invalid = !!error;

  function handleInput(e: ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(e.target.files ?? []);
    // Cleared so picking the same files again still fires a change.
    e.target.value = "";
    if (picked.length) onAdd(picked);
  }

  function onDragOver(e: DragEvent<HTMLElement>) {
    if (locked || !dragHasFiles(e)) return;
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
    if (locked || !dragHasFiles(e)) return;
    e.preventDefault();
    setDragging(false);
    onAdd(Array.from(e.dataTransfer.files));
  }

  // The count line carries the photos' error too (see below).
  const describedBy = [countId, notes.length ? notesId : null].filter(Boolean).join(" ");
  // The meter: filled up to the count out of 40, with a notch at 8.
  const fill = Math.min(1, count / MAX_FILM_PHOTOS);
  const notch = MIN_FILM_PHOTOS / MAX_FILM_PHOTOS;

  return (
    <section
      aria-labelledby={`${uid}-label`}
      onDragOver={onDragOver}
      onDragEnter={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className="min-w-0"
    >
      <p id={`${uid}-label`} className={FIELD_LABEL_CLASS}>
        Photos
      </p>

      <label
        htmlFor={inputId}
        className={[
          "relative mt-2 flex w-full flex-col items-center justify-center gap-1.5 rounded-card border-[1.5px] border-dashed px-5 text-center transition-[background-color,border-color] duration-150 ease-paper motion-reduce:transition-none",
          count === 0 ? "min-h-[12.5rem] py-10" : "min-h-[5.5rem] py-5",
          dragging
            ? "border-accent bg-paper-light"
            : invalid
              ? "border-[#9c3b2a] bg-paper-light/50"
              : "border-ink-faint/70 bg-paper-light/45 hover:border-accent hover:bg-paper-light/80",
          locked || full ? "cursor-not-allowed" : "cursor-pointer",
          locked ? "opacity-60" : "",
          FOCUS_RING_WITHIN,
        ].join(" ")}
      >
        <input
          id={inputId}
          type="file"
          multiple
          accept={FILM_ACCEPT}
          disabled={locked || full}
          onChange={handleInput}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          className="sr-only"
        />
        {count === 0 ? <StackIcon className="mb-2 h-12 w-14 text-ink-faint" /> : null}
        <span className="text-[1.0625rem] font-semibold text-accent-deep">
          {dragging
            ? "Let go to add them"
            : full
              ? "That’s forty: remove one to add another"
              : count === 0
                ? "Choose photos"
                : "Add more photos"}
          {dragging || full ? null : <span className="font-normal text-ink-soft"> or drop them here</span>}
        </span>
        {count === 0 ? (
          <span className="text-sm text-ink-soft">
            {MIN_FILM_PHOTOS} to {MAX_FILM_PHOTOS} from one day or trip &middot; JPEG, PNG or WebP, up to 10 MB each
          </span>
        ) : null}
      </label>

      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-5">
        {/* After a send with too few photos the error says it; the count line would only repeat it. */}
        <p
          id={countId}
          aria-live="polite"
          className={error ? FIELD_ERROR_CLASS : `text-[0.9375rem] ${line.ready ? "text-ink" : "text-ink-soft"}`}
        >
          {error ?? line.text}
        </p>
        <div aria-hidden="true" className="relative h-1 w-full max-w-[12rem] shrink-0 rounded-full bg-paper-deep sm:ml-auto">
          <div
            className={`absolute inset-y-0 left-0 rounded-full transition-[width] duration-300 ease-paper ${line.ready ? "bg-sage" : "bg-accent/70"}`}
            style={{ width: `${fill * 100}%` }}
          />
          <span className="absolute -top-1 block h-3 w-[1.5px] bg-ink-soft" style={{ left: `${notch * 100}%` }} />
        </div>
      </div>

      {notes.length ? (
        <div id={notesId} role="alert" className="mt-2 space-y-1">
          {notes.map((n, i) => (
            <p key={i} className={FIELD_ERROR_CLASS}>
              {n}
            </p>
          ))}
        </div>
      ) : null}
      {count > 0 ? (
        <ul
          aria-label="Photos picked"
          className="mt-5 grid list-none grid-cols-[repeat(auto-fill,minmax(4.75rem,1fr))] gap-3 p-0 sm:grid-cols-[repeat(auto-fill,minmax(5.5rem,1fr))]"
        >
          {items.map((item, i) => (
            <li key={item.key}>
              <Thumb item={item} index={i} locked={locked} onRemove={() => onRemove(item.key)} onRetry={() => onRetry(item.key)} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function Thumb(props: { item: PickedPhoto; index: number; locked: boolean; onRemove(): void; onRetry(): void }) {
  const { item, index, locked, onRemove, onRetry } = props;
  const imgRef = useRef<HTMLImageElement>(null);
  const [unreadable, setUnreadable] = useState(false);

  // The object URL lives exactly as long as this thumbnail shows this file.
  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    const url = URL.createObjectURL(item.file);
    img.src = url;
    return () => {
      img.removeAttribute("src");
      URL.revokeObjectURL(url);
    };
  }, [item.file]);

  const name = item.file.name || `Photo ${index + 1}`;
  const status =
    item.status === "sending"
      ? "sending"
      : item.status === "sent"
        ? "sent"
        : item.status === "failed"
          ? `didn’t send${item.error ? `: ${item.error}` : ""}`
          : null;

  return (
    <figure className="relative m-0">
      <div
        className={`relative aspect-square overflow-hidden rounded-paper bg-paper-deep shadow-paper ${
          item.status === "failed" ? "outline-2 outline-offset-2 outline-[#9c3b2a]" : ""
        }`}
      >
        {/* Local blob preview; next/image cannot optimise object URLs. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          ref={imgRef}
          alt=""
          decoding="async"
          onError={() => setUnreadable(true)}
          className={`h-full w-full object-cover text-[0px] transition-opacity duration-300 ${unreadable ? "hidden" : "block"} ${
            item.status === "waiting" && locked ? "opacity-55" : ""
          }`}
        />
        {unreadable ? (
          <span className="flex h-full w-full items-center justify-center text-xs text-ink-soft">no preview</span>
        ) : null}
        {item.status === "sending" ? (
          <span className="absolute inset-0 flex items-center justify-center bg-paper-light/45">
            <span
              aria-hidden="true"
              className="block size-7 animate-spin rounded-full border-2 border-dashed border-accent-deep [animation-duration:1.6s] motion-reduce:animate-none"
            />
          </span>
        ) : null}
        {item.status === "sent" ? (
          <span
            aria-hidden="true"
            className="absolute right-1 bottom-1 flex size-5 items-center justify-center rounded-full bg-paper-light text-sage shadow-paper"
          >
            <svg viewBox="0 0 12 12" className="size-3">
              <path d="M2.5 6.4 5 8.7l4.6-5.2" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
        ) : null}
      </div>
      <figcaption className="sr-only">
        {name}
        {status ? `, ${status}` : ""}
      </figcaption>

      {item.status === "failed" && !locked ? (
        <button
          type="button"
          onClick={onRetry}
          aria-label={`Try sending ${name} again`}
          className={`mt-1 -ml-1 inline-flex min-h-8 items-center rounded-paper px-1 text-xs font-semibold text-[#9c3b2a] underline decoration-1 underline-offset-[0.2em] ${FOCUS_RING}`}
        >
          Try again
        </button>
      ) : null}

      {locked || item.status === "sent" ? null : (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${name}`}
          className="group absolute -top-3.5 -right-3.5 flex size-10 items-center justify-center rounded-full focus-visible:outline-none"
        >
          <span
            aria-hidden="true"
            className="flex size-6 items-center justify-center rounded-full border border-rule bg-paper-light text-ink-soft shadow-paper transition-colors duration-150 ease-paper group-hover:border-accent group-hover:text-accent-deep group-focus-visible:outline-2 group-focus-visible:outline-offset-3 group-focus-visible:outline-dashed group-focus-visible:outline-accent motion-reduce:transition-none"
          >
            <svg viewBox="0 0 12 12" className="size-2.5">
              <path d="M2.5 2.5 9.5 9.5M9.5 2.5 2.5 9.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </span>
        </button>
      )}
    </figure>
  );
}

/** Three prints fanned out: the empty well's picture. */
function StackIcon(props: { className?: string }) {
  return (
    <svg viewBox="0 0 56 48" aria-hidden="true" className={props.className} fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="6" y="9" width="30" height="24" rx="1.5" transform="rotate(-10 21 21)" />
      <rect x="20" y="10" width="30" height="24" rx="1.5" transform="rotate(8 35 22)" />
      <rect x="12" y="15" width="32" height="26" rx="1.5" fill="var(--paper-light)" />
      <path d="M15 37l8-8 6 5 5-4 7 7" strokeLinejoin="round" />
      <circle cx="35" cy="22" r="2.4" />
    </svg>
  );
}
