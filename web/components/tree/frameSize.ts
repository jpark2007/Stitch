/**
 * How wide the tree's frame may grow and shrink, as CSS lengths. Pure, so
 * `node --test` loads it directly. Tests: components/tree/frameSize.test.ts.
 *
 * The frame scales as one piece with its width (1 --u = width / W), so every
 * limit on the scale is a limit on the width.
 */

export interface FrameLimits {
  /** Cap the tree's height (any CSS length); it then narrows to keep its shape. */
  maxHeight?: string;
  /**
   * The smallest scale, in px per layout unit. A tree too wide to fit at
   * this scale scrolls sideways inside its frame instead of shrinking until
   * names are unreadable.
   */
  minScale?: number;
  /**
   * The largest scale, in px per layout unit. A small family (one person, a
   * couple) stands at a natural size on a big page instead of filling it.
   */
  maxScale?: number;
}

/** A W x H (layout units) tree's frame max-width and min-width; undefined when unlimited. */
export function frameWidths(W: number, H: number, { maxHeight, minScale, maxScale }: FrameLimits) {
  const caps = [
    ...(maxHeight ? [`calc(${maxHeight} * ${(W / H).toFixed(4)})`] : []),
    ...(maxScale ? [`${(W * maxScale).toFixed(1)}px`] : []),
  ];
  return {
    maxWidth: caps.length > 1 ? `min(${caps.join(", ")})` : caps[0],
    // When both are set the floor wins (CSS min-width beats max-width), so a
    // tree never shrinks below a readable size.
    minWidth: minScale ? `${(W * minScale).toFixed(1)}px` : undefined,
  };
}
