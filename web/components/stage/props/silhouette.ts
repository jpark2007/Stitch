/**
 * Which silhouette a prop's placeholder card is cut in (./placeholder.ts).
 *
 * The prop's own words come first: a "red bicycle with a silver bell" is
 * rigged as a vehicle (so its rider can ride it), but it must not be drawn as
 * a car, and a bouquet held in a hand must not be drawn as a stack of books.
 * The name is read before the description, and within each the word that
 * comes first wins ("a station wagon" is a car, "a bicycle with training
 * wheels" a bicycle, "a baseball cap" a cap). Words are matched whole, so
 * "Carmen's hat" is no car, "training" no train, "cupboard" no cup and
 * "keyboard" no key.
 *
 * A few words only say what a thing is for ("gift", "present", "parcel"): they
 * count only when neither the name nor the description names the object
 * itself, so "Ana's gift, a bicycle with training wheels" is a bicycle.
 *
 * With no such word the size class picks the shape: hand, carried and tiny
 * props become a neutral paper keepsake with the prop's name written on it
 * (tagLabel), which does not pretend to be any one object.
 *
 * Pure (no three, no DOM), so node runs it (__tests__/silhouette.test.mjs).
 */

export type Silhouette =
  // Vehicles
  | "bicycle"
  | "car"
  | "truck"
  | "boat"
  | "train"
  | "plane"
  | "wagon"
  | "stroller"
  // Things held, carried and worn
  | "books"
  | "flowers"
  | "letter"
  | "cup"
  | "hat"
  | "cake"
  | "ring"
  | "photo"
  | "ball"
  | "bottle"
  | "key"
  | "suitcase"
  | "umbrella"
  | "guitar"
  | "violin"
  | "piano"
  | "lamp"
  | "basket"
  | "gift"
  | "diploma"
  | "phone"
  | "camera"
  | "doll"
  | "kite";

/** One word list: the shape it names, the words (regex alternatives, whole
 *  words, any case), and the variant of the shape they ask for. */
type Entry = readonly [shape: Silhouette, words: string, variant?: string];

/** Given names that are also flowers: "Rose's hat" is Rose's, not a rose. */
const NOT_A_NAME = "(?!['’]s\\b)";

/** Keyword patterns. Longer phrases are listed with their shape; the earliest
 *  match in a text wins, and at the same place the longer one. */
const WORDS: readonly Entry[] = [
  ["bicycle", "bikes?|bicycles?|tricycles?|trikes?|motorbikes?|motorcycles?"],
  ["car", "station wagons?|cars?|sedans?|coupes?|convertibles?|automobiles?|taxis?|hatchbacks?|jeeps?"],
  ["truck", "trucks?|pickups?|lorry|lorries|vans?|buses|bus"],
  ["boat", "boats?|rowboats?|sailboats?|canoes?|kayaks?|ships?|ferry|ferries|dinghy|dinghies|yachts?"],
  ["train", "trains?|locomotives?|railcars?|trams?"],
  ["plane", "planes?|airplanes?|aeroplanes?|aircraft|biplanes?|paper planes?"],
  ["wagon", "wagons?|carts?|wheelbarrows?|carriages?"],
  ["stroller", "strollers?|prams?|pushchairs?|buggy|buggies|baby carriages?"],

  [
    "books",
    "books?|hymnals?|diary|diaries|journals?|albums?|photo albums?|picture books?|notebooks?|scrapbooks?|sketchbooks?|cookbooks?|storybooks?|bibles?|prayer books?",
  ],
  [
    "flowers",
    `flowers?|bouquets?|posy|posies|roses?${NOT_A_NAME}|tulips?|daisy${NOT_A_NAME}|daisies|lily${NOT_A_NAME}|lilies|sunflowers?|carnations?|corsages?|blossoms?|wildflowers?`,
  ],
  ["letter", "postcards?|post cards?", "postcard"],
  ["letter", "letters?|envelopes?|cards?|valentines?|invitations?|love letters?"],
  ["cup", "mugs?|coffee mugs?", "mug"],
  ["cup", "cups?|teacups?|tea cups?|coffee cups?"],
  ["hat", "caps?|baseball caps?|flat caps?|peaked caps?|beanies?", "cap"],
  ["hat", "hats?|bonnets?|berets?|fedoras?|sombreros?|top hats?|sun hats?|straw hats?"],
  ["cake", "cakes?|cupcakes?|birthday cakes?|wedding cakes?"],
  ["ring", "rings?|wedding bands?|engagement rings?|wedding rings?"],
  ["photo", "photos?|photographs?|pictures?|portraits?|frames?|picture frames?|photo frames?|snapshots?|polaroids?"],
  ["ball", "soccer balls?|footballs?", "soccer"],
  ["ball", "baseballs?", "baseball"],
  ["ball", "basketballs?", "basketball"],
  ["ball", "beach balls?", "beach"],
  ["ball", "tennis balls?", "tennis"],
  ["ball", "balls?|volleyballs?|rubber balls?"],
  ["bottle", "bottles?|flasks?|jars?|milk bottles?|wine bottles?"],
  ["key", "keys?|keyrings?|key rings?"],
  ["suitcase", "suitcases?|luggage|valises?|briefcases?|travel bags?|carpet bags?"],
  ["umbrella", "umbrellas?|parasols?|brolly|brollies"],
  ["guitar", "guitars?|ukuleles?|ukes?|banjos?|mandolins?"],
  ["violin", "violins?|fiddles?|violas?|cellos?"],
  ["piano", "pianos?|toy pianos?"],
  ["lamp", "lanterns?|oil lamps?|hurricane lamps?", "lantern"],
  ["lamp", "lamps?|table lamps?|desk lamps?|reading lamps?"],
  ["basket", "baskets?|hampers?|picnic baskets?"],
  ["diploma", "diplomas?|certificates?|scrolls?"],
  ["phone", "telephones?|rotary phones?|landlines?", "rotary"],
  ["phone", "phones?|cellphones?|cell phones?|smartphones?|mobile phones?|iphones?"],
  ["camera", "cameras?|polaroid cameras?|camcorders?"],
  ["doll", "teddy|teddies|teddy bears?|stuffed bears?|bears?", "teddy"],
  ["doll", "dolls?|rag dolls?|dollies|dolly|puppets?|stuffed animals?"],
  ["kite", "kites?"],
];

/** Words that say what a thing is for, not what it is: they count last. */
const WEAK: readonly Entry[] = [["gift", "gifts?|presents?|parcels?|packages?|gift box(?:es)?"]];

type Pattern = readonly [Silhouette, RegExp, string | null];

const compile = (entries: readonly Entry[]): Pattern[] =>
  entries.map(([shape, words, variant]) => [shape, new RegExp(`\\b(?:${words})\\b`, "i"), variant ?? null] as const);

const STRONG_PATTERNS = compile(WORDS);
const WEAK_PATTERNS = compile(WEAK);

interface Found {
  shape: Silhouette;
  variant: string | null;
}

/** The shape a text names first, or null. */
function firstShape(text: string, patterns: readonly Pattern[]): Found | null {
  let best: Found | null = null;
  let at = Infinity;
  let len = 0;
  for (const [shape, re, variant] of patterns) {
    const m = re.exec(text);
    if (!m) continue;
    // Earliest wins ("station wagon" starts before its "wagon"); at the same
    // place, the longer phrase ("baseball cap" over "baseball").
    if (m.index < at || (m.index === at && m[0].length > len)) {
      best = { shape, variant };
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
  /** Which kind of the shape the words ask for, or null for the plain one:
   *  "postcard"; "mug"; "cap"; "candles" (a cake); "gem" (a ring); "soccer",
   *  "baseball", "basketball", "beach", "tennis" (a ball); "lantern";
   *  "rotary" (a phone); "teddy" (a doll). */
  variant: string | null;
}

const has = (text: string, words: string) => new RegExp(`\\b(?:${words})\\b`, "i").test(text);

/** The placeholder look a prop's name and description ask for. */
export function placeholderLook(prop: { name?: string | null; description?: string | null }): PlaceholderLook {
  const name = prop.name ?? "";
  const description = prop.description ?? "";
  const found =
    firstShape(name, STRONG_PATTERNS) ??
    firstShape(description, STRONG_PATTERNS) ??
    firstShape(name, WEAK_PATTERNS) ??
    firstShape(description, WEAK_PATTERNS);
  const shape = found?.shape ?? null;
  const words = `${name} ${description}`;
  let variant = found?.variant ?? null;
  if (shape === "cake") variant = has(words, "candles?") ? "candles" : null;
  if (shape === "ring") {
    // A stone when one is mentioned, or when nothing says it is a plain band.
    const stone = has(words, "diamonds?|engagement|gems?|gemstones?|stones?|jewels?|rub(y|ies)|emeralds?|sapphires?|pearls?");
    const plain = has(words, "wedding|bands?|plain|gold band");
    variant = stone || !plain ? "gem" : null;
  }
  const bell = shape === "bicycle" && has(words, "bells?");
  return { shape, bell, variant };
}

/** Shapes that stand on wheels (drawn into the body unless the prop has wheel parts). */
export function rollsOnWheels(shape: Silhouette | null, size: string): boolean {
  if (shape === null) return size === "vehicle";
  return shape === "bicycle" || shape === "car" || shape === "truck" || shape === "train" || shape === "wagon" || shape === "stroller";
}

/** Shapes drawn with their handle on one side: a cup's handle on the right, a
 *  key's bow on the left. The card is mirrored when the prop's grip is on the
 *  other side, so the hand that holds it by its grip holds the handle. */
const HANDLE_SIDE: Partial<Record<Silhouette, "left" | "right">> = {
  cup: "right",
  key: "left",
};

/** Whether to mirror the card so its handle is on the grip's side. */
export function mirrorForGrip(shape: Silhouette | null, grip: readonly number[] | null | undefined): boolean {
  const side = shape ? HANDLE_SIDE[shape] : undefined;
  const u = grip?.[0];
  if (!side || typeof u !== "number" || !Number.isFinite(u)) return false;
  return side === "right" ? u < 0.4 : u > 0.6;
}

/** Longest name written whole on a keepsake tag; longer ones are shortened. */
export const TAG_LABEL_MAX = 14;

/**
 * The short name written on a neutral keepsake tag: the prop's name without a
 * leading article, and, when that is too long to write legibly, without its
 * owner ("grandpa's pocket watch" -> "pocket watch"), then its last words.
 */
export function tagLabel(name: string | null | undefined): string {
  let s = (name ?? "").replace(/\s+/g, " ").trim();
  s = s.replace(/^(?:a|an|the|some|my|our)\s+/i, "");
  if (!s) return "keepsake";
  if (s.length <= TAG_LABEL_MAX) return s;
  const owned = s.replace(/^\S+['’]s?\s+/, "").replace(/^(?:old|little|favourite|favorite)\s+/i, "");
  if (owned && owned.length <= TAG_LABEL_MAX) return owned;
  // Drop a trailing "with ..." / "from ..." phrase, then an "of ...", then
  // keep the last words.
  let head = owned.replace(/\s+(?:with|from|for|in|on|at|that|which)\s+.*$/i, "");
  if (head && head.length <= TAG_LABEL_MAX) return head;
  head = head.replace(/\s+of\s+.*$/i, "");
  if (head && head.length <= TAG_LABEL_MAX) return head;
  // A short phrase ends on its noun ("old brass pocket watch"); a long one
  // usually starts with it ("silver locket Aunt Rosa wore").
  const words = head.split(" ");
  const two = words.length > 4 ? words.slice(0, 2).join(" ") : words.slice(-2).join(" ");
  if (words.length >= 2 && two.length <= TAG_LABEL_MAX) return two;
  const last = (words.length > 4 ? words[1] : words[words.length - 1]) ?? head;
  return last.length <= TAG_LABEL_MAX + 2 ? last : `${last.slice(0, TAG_LABEL_MAX - 1)}…`;
}
