// Pure helpers for the prop review card (PropChips). Erasable TypeScript only,
// so `node --test` can run it directly with type stripping.
import type { PropChip, PropEdit } from "@/lib/types";

export type PropDecision =
  | { action: "keep" }
  | { action: "rename"; name: string }
  | { action: "remove" }
  | { action: "replace_photo"; photoId: string | null; fileName?: string };

export const MAX_PROP_NAME = 60;

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
