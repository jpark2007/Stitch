"use client";

// Review the props the agent found (AnswerRequest kind="props").
import { useEffect, useId, useRef, useState, type ChangeEvent } from "react";
import type { PropChip, PropEdit } from "@/lib/types";
import {
  toPropEdits,
  validatePropDecisions,
  type PropDecision,
} from "./propEdits";

type Action = PropDecision["action"];

type PhotoDraft = {
  photoId: string | null;
  fileName?: string;
  previewUrl?: string;
};

const SIZE_LABEL: Record<PropChip["size"], string> = {
  tiny: "Tiny",
  hand: "Hand-sized",
  carried: "Carried",
  furniture: "Furniture",
  vehicle: "Vehicle",
  building: "Building",
};

const SWATCHES = ["#e9d3b4", "#d9c2a7", "#cfd8c0", "#e3c4b6", "#d6cbe0", "#c9d6dc"];

const GENERIC_SUBMIT_ERROR = "We couldn't send your changes. Check your connection and try again.";
const GENERIC_UPLOAD_ERROR = "That photo didn't upload. Try again, or pick another one.";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#a8492f] focus-visible:ring-offset-2 focus-visible:ring-offset-[#fffdf8]";

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = words.slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "");
  return letters.join("") || "?";
}

function swatchFor(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return SWATCHES[h % SWATCHES.length];
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

export default function PropChips(props: {
  props: PropChip[];
  mediaUrl: (mediaId: string) => string;
  onSubmit: (edits: PropEdit[]) => Promise<void>;
  onUploadPhoto?: (file: File) => Promise<string>;
}) {
  const { props: chips, mediaUrl, onSubmit, onUploadPhoto } = props;
  const uid = useId();

  const [actions, setActions] = useState<Record<string, Action>>({});
  const [names, setNames] = useState<Record<string, string>>({});
  const [photos, setPhotos] = useState<Record<string, PhotoDraft>>({});
  const [uploading, setUploading] = useState<Record<string, boolean>>({});
  const [uploadErrors, setUploadErrors] = useState<Record<string, string>>({});
  const [showErrors, setShowErrors] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  // Upload sequence per chip, so a slow earlier upload can't overwrite a newer pick.
  const uploadSeq = useRef<Record<string, number>>({});
  // Every object URL made for previews, revoked on unmount.
  const objectUrls = useRef<Set<string>>(new Set());
  useEffect(() => {
    const urls = objectUrls.current;
    return () => {
      urls.forEach((u) => URL.revokeObjectURL(u));
      urls.clear();
    };
  }, []);

  function actionOf(id: string): Action {
    const a = actions[id] ?? "keep";
    // Without an upload handler the photo option doesn't exist.
    return a === "replace_photo" && !onUploadPhoto ? "keep" : a;
  }

  function decisionFor(chip: PropChip): PropDecision {
    switch (actionOf(chip.id)) {
      case "rename":
        return { action: "rename", name: names[chip.id] ?? chip.name };
      case "remove":
        return { action: "remove" };
      case "replace_photo": {
        const p = photos[chip.id];
        return { action: "replace_photo", photoId: p?.photoId ?? null, fileName: p?.fileName };
      }
      default:
        return { action: "keep" };
    }
  }

  const decisions: Record<string, PropDecision> = {};
  for (const chip of chips) decisions[chip.id] = decisionFor(chip);
  const errors = showErrors ? validatePropDecisions(chips, decisions) : {};
  const anyUploading = chips.some((c) => uploading[c.id]);
  const locked = submitting || sent;

  function setAction(id: string, action: Action) {
    setActions((prev) => ({ ...prev, [id]: action }));
    setSubmitError(null);
  }

  async function handleFile(chip: PropChip, e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !onUploadPhoto) return;
    const seq = (uploadSeq.current[chip.id] ?? 0) + 1;
    uploadSeq.current[chip.id] = seq;

    const previewUrl = URL.createObjectURL(file);
    objectUrls.current.add(previewUrl);
    setPhotos((prev) => {
      const old = prev[chip.id]?.previewUrl;
      if (old) {
        URL.revokeObjectURL(old);
        objectUrls.current.delete(old);
      }
      return { ...prev, [chip.id]: { photoId: null, fileName: file.name, previewUrl } };
    });
    setUploadErrors((prev) => {
      const next = { ...prev };
      delete next[chip.id];
      return next;
    });
    setUploading((prev) => ({ ...prev, [chip.id]: true }));

    try {
      const photoId = await onUploadPhoto(file);
      if (uploadSeq.current[chip.id] !== seq) return;
      setPhotos((prev) => ({ ...prev, [chip.id]: { ...prev[chip.id], photoId } }));
    } catch (err) {
      if (uploadSeq.current[chip.id] !== seq) return;
      setUploadErrors((prev) => ({ ...prev, [chip.id]: errorMessage(err, GENERIC_UPLOAD_ERROR) }));
    } finally {
      if (uploadSeq.current[chip.id] === seq) {
        setUploading((prev) => ({ ...prev, [chip.id]: false }));
      }
    }
  }

  async function handleSubmit() {
    if (locked || anyUploading) return;
    setSubmitError(null);
    const found = validatePropDecisions(chips, decisions);
    const bad = chips.filter((c) => found[c.id]);
    if (bad.length > 0) {
      setShowErrors(true);
      // Move focus to the first chip that needs attention.
      const first = bad[0];
      const target = document.getElementById(
        actionOf(first.id) === "rename" ? `${uid}-${first.id}-name` : `${uid}-${first.id}-photo`,
      );
      target?.focus();
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit(toPropEdits(chips, decisions));
      setSent(true);
    } catch (err) {
      setSubmitError(errorMessage(err, GENERIC_SUBMIT_ERROR));
    } finally {
      setSubmitting(false);
    }
  }

  const errorCount = Object.keys(errors).length;

  return (
    <section
      aria-labelledby={`${uid}-title`}
      aria-busy={submitting}
      className="rounded-[4px] border border-[#e4d8c4] bg-[#fffdf8] p-6 text-[#3a2e24] shadow-[0_1px_0_#efe6d6,0_10px_28px_-16px_rgba(58,46,36,0.35)] sm:p-7"
    >
      <h2 id={`${uid}-title`} className="font-serif text-lg tracking-tight">
        The things in your story
      </h2>
      <p className="mt-1 text-sm leading-relaxed text-[#7b6b5a]">
        Rename anything we got wrong, remove what doesn&rsquo;t belong
        {onUploadPhoto ? ", or swap in a photo of the real thing" : ""}.
      </p>

      {chips.length === 0 ? (
        <p className="mt-5 text-sm text-[#7b6b5a] italic">No props this time.</p>
      ) : (
        <ul className="mt-5 grid gap-4 sm:grid-cols-2">
          {chips.map((chip) => (
            <li key={chip.id}>
              <ChipCard
                chip={chip}
                baseId={`${uid}-${chip.id}`}
                action={actionOf(chip.id)}
                name={names[chip.id] ?? chip.name}
                photo={photos[chip.id]}
                uploading={!!uploading[chip.id]}
                uploadError={uploadErrors[chip.id]}
                error={errors[chip.id]}
                disabled={locked}
                canUpload={!!onUploadPhoto}
                mediaUrl={mediaUrl}
                onAction={(a) => setAction(chip.id, a)}
                onName={(v) => setNames((prev) => ({ ...prev, [chip.id]: v }))}
                onFile={(e) => handleFile(chip, e)}
              />
            </li>
          ))}
        </ul>
      )}

      {errorCount > 0 ? (
        <p role="alert" className="mt-5 text-sm text-[#a23b2a]">
          {errorCount === 1 ? "One thing above needs a look." : `${errorCount} things above need a look.`}
        </p>
      ) : null}

      {submitError ? (
        <p
          role="alert"
          className="mt-4 rounded-[3px] border border-[#e8c3b9] bg-[#fbecea] px-3 py-2 text-sm text-[#7d2c1f]"
        >
          {submitError}
        </p>
      ) : null}

      <div className="mt-6 flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={locked || anyUploading}
          className={`inline-flex items-center gap-2 rounded-full bg-[#a8492f] px-5 py-2.5 text-sm font-medium text-[#fffaf2] transition-colors hover:bg-[#8f3c26] disabled:cursor-not-allowed disabled:opacity-60 ${FOCUS_RING}`}
        >
          {submitting ? (
            <>
              <span
                aria-hidden="true"
                className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[#fffaf2]/40 border-t-[#fffaf2] motion-reduce:animate-none"
              />
              Sending…
            </>
          ) : sent ? (
            "Sent"
          ) : (
            "Looks good, keep going"
          )}
        </button>
        {anyUploading && !locked ? (
          <p role="status" className="text-sm text-[#7b6b5a]">
            Waiting for your photo to finish uploading…
          </p>
        ) : null}
        {sent ? (
          <p role="status" className="text-sm text-[#5f7a55]">
            Thanks. Building the scene now.
          </p>
        ) : null}
      </div>
    </section>
  );
}

function ChipCard(p: {
  chip: PropChip;
  baseId: string;
  action: Action;
  name: string;
  photo: PhotoDraft | undefined;
  uploading: boolean;
  uploadError: string | undefined;
  error: string | undefined;
  disabled: boolean;
  canUpload: boolean;
  mediaUrl: (mediaId: string) => string;
  onAction: (a: Action) => void;
  onName: (v: string) => void;
  onFile: (e: ChangeEvent<HTMLInputElement>) => void;
}) {
  const { chip, baseId, action, photo } = p;
  const removed = action === "remove";
  const nameId = `${baseId}-name`;
  const photoInputId = `${baseId}-photo`;
  const errorId = `${baseId}-error`;
  const uploadErrorId = `${baseId}-upload-error`;
  const titleId = `${baseId}-title`;

  const options: { value: Action; label: string }[] = [
    { value: "keep", label: "Keep" },
    { value: "rename", label: "Rename" },
    { value: "remove", label: "Remove" },
  ];
  if (p.canUpload) options.push({ value: "replace_photo", label: "Use a photo" });

  const showPhotoPreview = action === "replace_photo" && photo?.previewUrl;
  // An upload failure already explains a missing photo, so don't stack both messages.
  const showError = !!p.error && !(action === "replace_photo" && p.uploadError);

  return (
    <div
      className={`relative h-full rounded-[3px] border bg-[#fbf5ea] p-4 shadow-[0_1px_2px_rgba(58,46,36,0.08)] transition-opacity ${
        p.error || p.uploadError ? "border-[#d9a091]" : "border-[#e2d5bf]"
      }`}
    >
      {/* Tag punch hole, purely decorative. */}
      <span
        aria-hidden="true"
        className="absolute top-3 right-3 h-2.5 w-2.5 rounded-full border border-[#d8cab3] bg-[#fffdf8]"
      />

      <div className={`flex items-center gap-3 ${removed ? "opacity-55" : ""}`}>
        <div className="h-14 w-14 shrink-0 overflow-hidden rounded-[2px] border border-[#e2d5bf] bg-[#fffdf8]">
          {showPhotoPreview ? (
            // Local blob preview; next/image can't optimise object URLs.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={photo.previewUrl}
              alt={`Your photo for ${chip.name}`}
              className="h-full w-full object-cover"
            />
          ) : chip.art ? (
            // Media served by the API; sizes are unknown up front.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={p.mediaUrl(chip.art)}
              alt={`Drawing of ${chip.name}`}
              className="h-full w-full object-contain p-1"
            />
          ) : (
            <div
              aria-hidden="true"
              className="flex h-full w-full items-center justify-center font-serif text-lg text-[#5a4a3c]"
              style={{ backgroundColor: swatchFor(chip.id) }}
            >
              {initials(chip.name)}
            </div>
          )}
        </div>
        <div className="min-w-0 pr-4">
          <p
            id={titleId}
            className={`truncate font-serif text-base ${removed ? "line-through decoration-[#a8492f]" : ""}`}
          >
            {chip.name}
          </p>
          <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-[#7b6b5a]">
            <span>{SIZE_LABEL[chip.size] ?? chip.size}</span>
            {chip.reused ? (
              <span className="rounded-full bg-[#eaf0e1] px-2 py-0.5 text-[#4f6a46]">saved</span>
            ) : null}
            {removed ? <span className="sr-only">(will be removed)</span> : null}
          </p>
        </div>
      </div>

      <fieldset className="mt-3" disabled={p.disabled}>
        <legend className="sr-only">What to do with {chip.name}</legend>
        <div className="flex flex-wrap gap-1.5">
          {options.map((o) => (
            <label key={o.value} className="cursor-pointer">
              <input
                type="radio"
                name={`${baseId}-action`}
                value={o.value}
                checked={action === o.value}
                onChange={() => p.onAction(o.value)}
                className="peer sr-only"
              />
              <span className="inline-block rounded-full border border-[#d8cab3] bg-[#fffdf8] px-3 py-1 text-xs text-[#5a4a3c] transition-colors peer-checked:border-[#3a2e24] peer-checked:bg-[#3a2e24] peer-checked:text-[#fffaf2] peer-focus-visible:ring-2 peer-focus-visible:ring-[#a8492f] peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-[#fbf5ea] peer-disabled:cursor-not-allowed peer-disabled:opacity-60 hover:border-[#a8492f]">
                {o.label}
              </span>
            </label>
          ))}
        </div>

        {action === "rename" ? (
          <div className="mt-3">
            <label htmlFor={nameId} className="block text-xs font-medium text-[#5a4a3c]">
              New name <span className="sr-only">for {chip.name}</span>
            </label>
            <input
              id={nameId}
              type="text"
              value={p.name}
              autoComplete="off"
              aria-invalid={showError ? true : undefined}
              aria-describedby={showError ? errorId : undefined}
              onChange={(e) => p.onName(e.target.value)}
              className={`mt-1 block w-full rounded-[3px] border bg-[#fffdf8] px-2.5 py-1.5 text-sm text-[#3a2e24] ${FOCUS_RING} ${
                p.error ? "border-[#a23b2a]" : "border-[#d8cab3]"
              }`}
            />
          </div>
        ) : null}

        {action === "replace_photo" && p.canUpload ? (
          <div className="mt-3">
            <label htmlFor={photoInputId} className="block text-xs font-medium text-[#5a4a3c]">
              Photo of the real {chip.name}
            </label>
            <input
              id={photoInputId}
              type="file"
              accept="image/*"
              disabled={p.uploading || p.disabled}
              aria-invalid={p.error || p.uploadError ? true : undefined}
              aria-describedby={
                [p.uploadError ? uploadErrorId : null, showError ? errorId : null]
                  .filter(Boolean)
                  .join(" ") || undefined
              }
              onChange={p.onFile}
              className={`mt-1 block w-full rounded-[3px] text-xs text-[#5a4a3c] file:mr-3 file:cursor-pointer file:rounded-full file:border file:border-[#d8cab3] file:bg-[#fffdf8] file:px-3 file:py-1 file:text-xs file:text-[#3a2e24] hover:file:border-[#a8492f] ${FOCUS_RING}`}
            />
            {p.uploading ? (
              <p role="status" className="mt-1.5 text-xs text-[#7b6b5a]">
                Uploading {photo?.fileName ?? "photo"}…
              </p>
            ) : photo?.photoId && photo.fileName ? (
              <p className="mt-1.5 text-xs text-[#5f7a55]">Using {photo.fileName}</p>
            ) : null}
            {p.uploadError ? (
              <p id={uploadErrorId} role="alert" className="mt-1.5 text-xs text-[#a23b2a]">
                {p.uploadError}
              </p>
            ) : null}
          </div>
        ) : null}

        {removed ? (
          <button
            type="button"
            onClick={() => p.onAction("keep")}
            className={`mt-3 rounded-sm text-xs text-[#a8492f] underline underline-offset-2 hover:text-[#8f3c26] ${FOCUS_RING}`}
          >
            Undo remove<span className="sr-only"> for {chip.name}</span>
          </button>
        ) : null}
      </fieldset>

      {showError ? (
        <p id={errorId} role="alert" className="mt-2 text-xs text-[#a23b2a]">
          {p.error}
        </p>
      ) : null}
    </div>
  );
}
