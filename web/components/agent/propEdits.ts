// Pure helpers for the prop review card (PropChips). Erasable TypeScript only,
// so `node --test` can run it directly with type stripping.
import type { PropChip, PropEdit } from "@/lib/types";

export type PropDecision =
  | { action: "keep" }
  | { action: "rename"; name: string }
  | { action: "remove" }
  | { action: "replace_photo"; photoId: string | null; fileName?: string };

export const MAX_PROP_NAME = 60;

/**
 * The quiet pill on a prop the family already has (PropChip.reused): the agent
 * found it among the family's things instead of drawing a new one.
 */
export const REUSED_PROP_LABEL = "used before";

/**
 * The visible chip for one Keep / Rename / Remove / Use a photo choice. It sits
 * right after its `peer sr-only` radio. The chosen chip is ink with paper-light
 * text. The hover darkening applies only while the chip is NOT chosen: a plain
 * `hover:text-ink` has the same specificity as `peer-checked:text-paper-light`
 * and comes later in the CSS, so the chosen label would turn ink on ink under
 * the pointer (and stay that way on touch screens, where hover sticks after a
 * tap). Tailwind v4 already limits `hover:` to hover-capable pointers.
 */
export const PROP_OPTION_CHIP_CLASS = [
  "inline-flex min-h-8 items-center rounded-card border border-ink-faint bg-paper-light px-3",
  "text-sm leading-none text-ink-soft transition-colors duration-150 ease-paper",
  "peer-not-checked:hover:border-ink-soft peer-not-checked:hover:text-ink",
  "peer-checked:border-ink peer-checked:bg-ink peer-checked:text-paper-light",
  "peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent",
  "peer-disabled:cursor-not-allowed peer-disabled:opacity-60",
].join(" ");

/** A plain size word for every known PropChip size. */
export const PROP_SIZE_LABELS: Readonly<Record<PropChip["size"], string>> = {
  tiny: "Tiny",
  hand: "Hand-sized",
  carried: "Armful",
  furniture: "Furniture-sized",
  vehicle: "Car-sized",
  building: "Building-sized",
};

/**
 * The label shown under a prop's name. Known sizes get their size word;
 * anything else is humanized ("big_box" -> "Big box") rather than shown raw.
 */
export function propSizeLabel(size: PropChip["size"] | string): string {
  if (Object.prototype.hasOwnProperty.call(PROP_SIZE_LABELS, size)) {
    return PROP_SIZE_LABELS[size as PropChip["size"]];
  }
  const words = String(size ?? "")
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();
  return words ? words[0].toUpperCase() + words.slice(1) : "";
}

/** Every chip starts as "keep". */
export function initialPropDecisions(chips: PropChip[]): Record<string, PropDecision> {
  const out: Record<string, PropDecision> = {};
  for (const chip of chips) out[chip.id] = { action: "keep" };
  return out;
}

/** Error messages keyed by chip id. An empty object means everything is valid. */
export function validatePropDecisions(
  chips: PropChip[],
  d: Record<string, PropDecision>,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const chip of chips) {
    const decision = d[chip.id] ?? { action: "keep" };
    if (decision.action === "rename") {
      const name = decision.name.trim();
      if (name.length === 0) errors[chip.id] = "Give it a name, or keep the old one.";
      else if (name.length > MAX_PROP_NAME)
        errors[chip.id] = `Keep the name to ${MAX_PROP_NAME} characters or fewer.`;
      else if (name === chip.name.trim())
        errors[chip.id] = "That's the same name. Change it, or keep the old one.";
    } else if (decision.action === "replace_photo") {
      if (!decision.photoId) errors[chip.id] = "Choose a photo, or keep the drawing.";
    }
  }
  return errors;
}

/** One PropEdit per chip, in chip order. Throws if the decisions are not valid. */
export function toPropEdits(chips: PropChip[], d: Record<string, PropDecision>): PropEdit[] {
  const errors = validatePropDecisions(chips, d);
  const bad = Object.keys(errors);
  if (bad.length > 0) {
    throw new Error(`Invalid prop decisions: ${bad.map((id) => `${id}: ${errors[id]}`).join("; ")}`);
  }
  return chips.map((chip): PropEdit => {
    const decision = d[chip.id] ?? { action: "keep" };
    switch (decision.action) {
      case "rename":
        return { id: chip.id, action: "rename", name: decision.name.trim() };
      case "remove":
        return { id: chip.id, action: "remove" };
      case "replace_photo":
        return { id: chip.id, action: "replace_photo", photoId: decision.photoId };
      default:
        return { id: chip.id, action: "keep" };
    }
  });
}

/** Shown when a replacement photo did not upload and nothing more specific applies. */
export const GENERIC_UPLOAD_ERROR = "That photo didn’t upload. Try again, or pick another one.";

/**
 * The line under a photo that did not upload. The server's own words are
 * shown only for the 4xx answers that say what to change (too large, wrong
 * type, and the like: the API client's fixed sentences). Anything else (no
 * connection, a 5xx whose wording can carry internals) gets the calm generic
 * line.
 */
export function uploadErrorMessage(error: unknown): string {
  const status =
    typeof error === "object" && error !== null ? (error as { status?: unknown }).status : undefined;
  if (typeof status !== "number" || status === 0 || status >= 500) return GENERIC_UPLOAD_ERROR;
  const message = error instanceof Error ? error.message.trim() : "";
  return message || GENERIC_UPLOAD_ERROR;
}
