"use client";

// Review the props the agent found (AnswerRequest kind="props").
// Written straight onto the feed's sheet: each prop is a paper cut-out with
// its picture in a small photo border, and one flow kit Button sends them all.
import { useEffect, useId, useRef, useState, type ChangeEvent } from "react";
import { Button } from "@/components/flow/Button";
import { Photo } from "@/components/paper/Photo";
import type { PropChip, PropEdit } from "@/lib/types";
import {
  PROP_OPTION_CHIP_CLASS,
  REUSED_PROP_LABEL,
  propSizeLabel,
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

// Paper tints behind a prop's initials when it has no picture yet.
const SWATCHES = ["bg-card", "bg-kraft/40", "bg-sage/30", "bg-accent/20", "bg-mustard/30", "bg-sky/25"];

const GENERIC_SUBMIT_ERROR = "We couldn't send your changes. Check your connection and try again.";
const GENERIC_UPLOAD_ERROR = "That photo didn't upload. Try again, or pick another one.";

const FOCUS_RING = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

// A soft terracotta box for a message that belongs to the whole list.
const ALERT_BOX =
  "rounded-card border border-accent/30 border-l-[3px] border-l-accent bg-accent/8 px-3.5 py-2.5 text-sm leading-snug text-accent-deep";

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = words.slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "");
  return letters.join("") || "?";
}

function hashOf(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return h;
}

function swatchFor(id: string): string {
  return SWATCHES[hashOf(id) % SWATCHES.length];
}

/** A small steady tilt per prop, between -2 and 2 degrees. */
function tiltFor(id: string): number {
  return ((hashOf(id) >>> 4) % 5) - 2;
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
    <section aria-labelledby={`${uid}-title`} aria-busy={submitting} className="text-ink">
      <h2 id={`${uid}-title`} className="font-display text-xl leading-snug sm:text-[1.375rem]">
        The things in your story
      </h2>
      <p className="mt-1.5 text-[0.9375rem] leading-relaxed text-ink-soft">
        Rename anything we got wrong, remove what doesn&rsquo;t belong
        {onUploadPhoto ? ", or swap in a photo of the real thing" : ""}.
      </p>

      {chips.length === 0 ? (
        <p className="mt-5 font-hand text-xl text-ink-soft">No props this time.</p>
      ) : (
        <ul className="mt-5 space-y-4">
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
        <p role="alert" className="mt-5 text-sm leading-snug font-medium text-accent-deep">
          {errorCount === 1 ? "One thing above needs a look." : `${errorCount} things above need a look.`}
        </p>
      ) : null}

      {submitError ? (
        <p role="alert" className={`mt-4 ${ALERT_BOX}`}>
          {submitError}
        </p>
      ) : null}

      <div className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-2">
        <Button onClick={handleSubmit} disabled={sent || anyUploading} busy={submitting} busyLabel="Sending…">
          {sent ? "Sent" : "Looks good, keep going"}
        </Button>
        {anyUploading && !locked ? (
          <p role="status" className="text-sm leading-snug text-ink-soft">
            Waiting for your photo to finish uploading…
          </p>
        ) : null}
        {sent ? (
          <p role="status" className="font-hand text-xl leading-tight text-ink-soft">
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
      className={`rounded-paper border bg-paper p-4 shadow-paper transition-[border-color] duration-150 ease-paper ${
        p.error || p.uploadError ? "border-accent-deep/60" : "border-rule"
      }`}
    >
      <div className="flex items-start gap-4">
        <div className={`shrink-0 pt-0.5 transition-opacity duration-200 ease-paper ${removed ? "opacity-55" : ""}`}>
          {showPhotoPreview ? (
            <Photo src={photo.previewUrl} alt={`Your photo for ${chip.name}`} width={72} aspect={1} tape="none" rotate={tiltFor(chip.id)} />
          ) : (
            <Photo alt="" width={72} aspect={1} tape="none" rotate={tiltFor(chip.id)}>
              {chip.art ? (
                // Media served by the API; sizes are unknown up front.
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={p.mediaUrl(chip.art)}
                  alt={`Drawing of ${chip.name}`}
                  className="block h-full w-full bg-paper-light object-contain p-1"
                />
              ) : (
                <div
                  aria-hidden="true"
                  className={`flex h-full w-full items-center justify-center font-display text-lg text-ink ${swatchFor(chip.id)}`}
                >
                  {initials(chip.name)}
                </div>
              )}
            </Photo>
          )}
        </div>

        <div className="min-w-0 flex-1">
          {/* A removed prop is struck through in softer ink (not faded), so it stays readable. */}
          <div>
            <p
              id={titleId}
              className={`font-display text-lg leading-snug break-words ${removed ? "text-ink-soft line-through decoration-accent-deep" : ""}`}
            >
              {chip.name}
            </p>
            <p className="mt-0.5 flex flex-wrap items-center gap-2 text-sm text-ink-soft">
              <span>{propSizeLabel(chip.size)}</span>
              {chip.reused ? (
                <span className="rounded-full bg-sage/20 px-2 py-px text-xs text-ink-soft">{REUSED_PROP_LABEL}</span>
              ) : null}
              {removed ? <span className="sr-only">(will be removed)</span> : null}
            </p>
          </div>

          <fieldset className="mt-1.5 min-w-0" disabled={p.disabled}>
            <legend className="sr-only">What to do with {chip.name}</legend>
            <div className="-ml-0.5 flex flex-wrap gap-x-1.5">
              {options.map((o) => (
                <label key={o.value} className="inline-flex min-h-11 cursor-pointer items-center">
                  <input
                    type="radio"
                    name={`${baseId}-action`}
                    value={o.value}
                    checked={action === o.value}
                    onChange={() => p.onAction(o.value)}
                    className="peer sr-only"
                  />
                  <span className={PROP_OPTION_CHIP_CLASS}>{o.label}</span>
                </label>
              ))}
            </div>

            {action === "rename" ? (
              <div className="mt-2">
                <label htmlFor={nameId} className="block text-sm leading-snug font-semibold text-ink">
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
                  className={`mt-1 block min-h-11 w-full rounded-paper border bg-paper-light px-3 py-2 text-base text-ink shadow-inset transition-[border-color] duration-150 ease-paper ${FOCUS_RING} ${
                    p.error ? "border-accent-deep" : "border-ink-faint hover:border-ink-soft"
                  }`}
                />
              </div>
            ) : null}

            {action === "replace_photo" && p.canUpload ? (
              <div className="mt-2">
                <p className="text-sm leading-snug font-semibold text-ink">Photo of the real {chip.name}</p>
                {/* The browser's own file control repeats the file name next to
                    our "Using …" line and looks like a form from elsewhere: it
                    stays in the DOM for keyboards and screen readers, and a
                    paper button (its label) stands in for it. */}
                <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
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
                    className="peer sr-only"
                  />
                  <label
                    htmlFor={photoInputId}
                    className="inline-flex min-h-11 cursor-pointer items-center rounded-card border border-ink-faint bg-paper-light px-4 text-sm font-semibold text-ink transition-colors duration-150 ease-paper hover:border-ink-soft peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent peer-disabled:cursor-not-allowed peer-disabled:opacity-60"
                  >
                    {photo?.fileName ? "Choose another photo" : "Choose a photo"}
                    <span className="sr-only"> of the real {chip.name}</span>
                  </label>
                  {p.uploading ? (
                    <p role="status" className="min-w-0 text-sm break-words text-ink-soft">
                      Uploading {photo?.fileName ?? "photo"}…
                    </p>
                  ) : photo?.photoId && photo.fileName ? (
                    <p className="min-w-0 text-sm break-words text-ink-soft">Using {photo.fileName}</p>
                  ) : null}
                </div>
                {p.uploadError ? (
                  <p id={uploadErrorId} role="alert" className="mt-1.5 text-sm leading-snug font-medium text-accent-deep">
                    {p.uploadError}
                  </p>
                ) : null}
              </div>
            ) : null}

          </fieldset>

          {showError ? (
            <p id={errorId} role="alert" className="mt-1.5 text-sm leading-snug font-medium text-accent-deep">
              {p.error}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
