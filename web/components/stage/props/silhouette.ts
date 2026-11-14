/**
 * Which silhouette a prop's placeholder card is cut in (./placeholder.ts).
 *
 * The prop's own words come first: a "red bicycle with a silver bell" is
 * rigged as a vehicle (so its rider can ride it), but it must not be drawn as
 * a car. The name is read before the description, and within each the word
 * that comes first wins ("a station wagon" is a car, "a bicycle with training
 * wheels" a bicycle). Words are matched whole, so "Carmen's hat" is no car
 * and "training" no train. With no such word the size class picks the shape,
 * as before.
 *
 * Pure (no three, no DOM), so node runs it (__tests__/silhouette.test.mjs).
 */

export type Silhouette = "bicycle" | "car" | "truck" | "boat" | "train" | "plane" | "wagon" | "stroller";

/** Keyword patterns, whole words, any case. Longer phrases are listed with
 *  their shape; the earliest match in a text wins. */
const WORDS: readonly (readonly [Silhouette, string])[] = [
  ["bicycle", "bikes?|bicycles?|tricycles?|trikes?|motorbikes?|motorcycles?"],
  ["car", "station wagons?|cars?|sedans?|coupes?|convertibles?|automobiles?|taxis?|hatchbacks?|jeeps?"],
  ["truck", "trucks?|pickups?|lorry|lorries|vans?|buses|bus"],
  ["boat", "boats?|rowboats?|sailboats?|canoes?|kayaks?|ships?|ferry|ferries|dinghy|dinghies|yachts?"],
  ["train", "trains?|locomotives?|railcars?|trams?"],
  ["plane", "planes?|airplanes?|aeroplanes?|aircraft|biplanes?"],
  ["wagon", "wagons?|carts?|wheelbarrows?|carriages?"],
  ["stroller", "strollers?|prams?|pushchairs?|buggy|buggies|baby carriages?"],
];

const PATTERNS = WORDS.map(([shape, words]) => [shape, new RegExp(`\\b(?:${words})\\b`, "i")] as const);

/** The shape a text names first, or null. */
function firstShape(text: string): Silhouette | null {
  let best: Silhouette | null = null;
  let at = Infinity;
  let len = 0;
  for (const [shape, re] of PATTERNS) {
    const m = re.exec(text);
    if (!m) continue;
    // Earliest wins ("station wagon" starts before its "wagon"); at the same
    // place, the longer phrase.
    if (m.index < at || (m.index === at && m[0].length > len)) {
      best = shape;
      at = m.index;
      len = m[0].length;
    }
  }
  return best;
}

export interface PlaceholderLook {
  /** A shape named by the prop's words, or null: draw its size class. */
  shape: Silhouette | null;
  /** A bicycle with a bell mentioned: draw one on the handlebar. */
  bell: boolean;
}

/** The placeholder look a prop's name and description ask for. */
export function placeholderLook(prop: { name?: string | null; description?: string | null }): PlaceholderLook {
  const name = prop.name ?? "";
  const description = prop.description ?? "";
  const shape = firstShape(name) ?? firstShape(description);
  const bell = shape === "bicycle" && /\bbells?\b/i.test(`${name} ${description}`);
  return { shape, bell };
}

/** Shapes that stand on wheels (drawn into the body unless the prop has wheel parts). */
export function rollsOnWheels(shape: Silhouette | null, size: string): boolean {
  if (shape === null) return size === "vehicle";
  return shape !== "boat" && shape !== "plane";
}
