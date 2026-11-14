/**
 * Picks a procedural signature item from a character sheet's free text
 * ("blue flat cap", "red knitted shawl", "pearl reading glasses"), by
 * keywords. The art for each kind lives in itemArt.ts.
 *
 * Pure and dependency-free, so `node --test` runs it.
 */

export type ItemKind = "flat_cap" | "hat" | "beanie" | "scarf" | "glasses" | "bow" | "headband" | "token";

export interface ItemChoice {
  kind: ItemKind;
  /** A colour named in the text, as a warm paper hex; null when none is named. */
  color: string | null;
}

/** Checked in order: the first kind with a matching keyword wins. */
const KINDS: readonly [ItemKind, RegExp][] = [
  ["glasses", /\b(glasses|spectacles|specs|sunglasses|shades|eyeglasses|monocle|bifocals)\b/],
  ["scarf", /\b(scarf|scarves|shawl|muffler|stole|wrap|neckerchief|cravat)\b/],
  ["beanie", /\b(beanie|beret|toque|tuque|bobble|pom[- ]?pom|knit(ted)? (hat|cap)|wool(l?en)? (hat|cap)|ski (hat|cap))\b/],
  ["flat_cap", /\b(caps?|newsboy|baker ?boy|baseball|kepi)\b/],
  ["hat", /\b(hats?|fedora|bonnet|sombrero|stetson|cowboy|panama|trilby|bowler|derby|boater|sunhat|homburg|top ?hat|helmet)\b/],
  ["bow", /\b(bows?|ribbons?|hair ?bow|scrunchie)\b/],
  ["headband", /\b(headband|hair ?band|bandana|bandanna|sweatband|alice band|head ?scarf|tiara|circlet)\b/],
];

/** Colour words mapped to warm, slightly muted paper tones. */
const COLORS: readonly [RegExp, string][] = [
  [/\b(navy|midnight)\b/, "#2f4466"],
  [/\b(sky|baby blue|powder blue|light blue)\b/, "#86a9c9"],
  [/\b(teal|turquoise|aqua)\b/, "#3f8382"],
  [/\bblue\b/, "#46699e"],
  [/\b(maroon|burgundy|wine|oxblood)\b/, "#7d3434"],
  [/\b(crimson|scarlet|cherry)\b/, "#b23a32"],
  [/\b(pink|rose|blush|salmon)\b/, "#d98f96"],
  [/\b(rust|copper|terracotta)\b/, "#b5603c"],
  [/\borange\b/, "#d9792f"],
  [/\bred\b/, "#b8473a"],
  [/\b(mustard|ochre|amber)\b/, "#c9962f"],
  [/\b(gold|golden|brass)\b/, "#c9a13d"],
  [/\b(yellow|lemon|sunflower)\b/, "#e2b445"],
  [/\b(olive|khaki)\b/, "#7a7a42"],
  [/\b(sage|mint)\b/, "#94ad86"],
  [/\b(emerald|forest|bottle green)\b/, "#3e6b4a"],
  [/\bgreen\b/, "#5e8a55"],
  [/\b(lavender|lilac)\b/, "#a995bd"],
  [/\b(purple|violet|plum|mauve)\b/, "#7d5a8c"],
  [/\b(tan|camel|caramel|tweed)\b/, "#b0905f"],
  [/\b(beige|sand|oatmeal|straw)\b/, "#d8c3a0"],
  [/\b(brown|chocolate|chestnut|leather|coffee)\b/, "#7a5536"],
  [/\b(cream|ivory|off[- ]white)\b/, "#efe4cc"],
  [/\b(pearl|pearly)\b/, "#e9e1d3"],
  [/\bwhite\b/, "#f4efe4"],
  [/\b(silver|steel)\b/, "#b4b0a8"],
  [/\b(grey|gray|ash|slate)\b/, "#86817a"],
  [/\b(charcoal|black|ebony|jet)\b/, "#2f2a27"],
];

/** The item kind and named colour for a signature item text. */
export function pickItem(text: string | null | undefined): ItemChoice {
  const t = (text ?? "").toLowerCase().trim();
  if (!t) return { kind: "token", color: null };
  let kind: ItemKind = "token";
  for (const [k, re] of KINDS) {
    if (re.test(t)) {
      kind = k;
      break;
    }
  }
  let color: string | null = null;
  for (const [re, hex] of COLORS) {
    if (re.test(t)) {
      color = hex;
      break;
    }
  }
  return { kind, color };
}
