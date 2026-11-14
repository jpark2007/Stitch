/**
 * How a name is set under its puppet on the tree, in layout units. Pure, so
 * the server and the browser agree, and `node --test` loads it directly.
 * Tests: components/tree/treeData.test.ts.
 */

/** The full hand size of a name, and the smallest it may go. */
export const NAME_SIZE = 30;
export const NAME_MIN = 21;
/** The widest a line of a name may run (a touch wider than the figure). */
const NAME_WIDTH = 116;
/** Average Caveat letter width, in ems (measured ~0.38; a little spare for wide letters). */
const CHAR_EM = 0.42;

/** The size a line of `chars` letters fits at, capped at `max`. */
const fitSize = (chars: number, max: number) => Math.min(max, NAME_WIDTH / (Math.max(chars, 1) * CHAR_EM));

/**
 * Short names in the full hand size on one line; longer ones a little
 * smaller; long ones over two lines, broken at the space (or after the
 * hyphen) that balances them best. A line that still will not fit at the
 * smallest size is cut short with an ellipsis by the CSS (the full name is
 * in the link's label).
 */
export function nameFit(name: string): { lines: string[]; size: number } {
  const chars = [...name];
  const one = fitSize(chars.length, NAME_SIZE);
  if (one >= 24) return { lines: [name], size: round(one) };
  let best: [string, string] | null = null;
  const longest = (pair: [string, string]) => Math.max([...pair[0]].length, [...pair[1]].length);
  for (let i = 1; i < chars.length - 1; i++) {
    let pair: [string, string] | null = null;
    if (chars[i] === " ") pair = [chars.slice(0, i).join(""), chars.slice(i + 1).join("")];
    else if (chars[i - 1] === "-" || chars[i - 1] === "–") pair = [chars.slice(0, i).join(""), chars.slice(i).join("")];
    if (pair && pair[0].trim() && pair[1].trim() && (!best || longest(pair) < longest(best))) best = pair;
  }
  if (!best) return { lines: [name], size: round(Math.max(NAME_MIN, one)) };
  return { lines: best, size: round(Math.max(NAME_MIN, fitSize(longest(best), 27))) };
}

/** How far a name reaches below a one-line name in the full size. */
export function nameExtra(name: string): number {
  const { lines, size } = nameFit(name);
  return Math.max(0, lines.length * size - NAME_SIZE);
}

const round = (v: number) => Math.round(v * 100) / 100;
