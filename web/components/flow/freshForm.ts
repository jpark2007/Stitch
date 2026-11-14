// A form that was sent should open empty the next time its page is shown.
//
// With cacheComponents on, a page left behind by router.push stays mounted in
// a hidden React <Activity>, state and all, and its effects run again when it
// is shown. So a screen that navigates away after a successful submit marks
// it here, and a (layout) effect on the screen asks takeReset() when it runs:
// true exactly once after each submit, which is the screen's cue to remount
// its form with a new key. A draft that was never sent is left alone.
//
// No runtime imports and only erasable TypeScript, so `node --test` loads this
// file directly. Tests: components/flow/__tests__/freshForm.test.mjs.

export interface FreshFormTracker {
  /** Record a successful submit (call before navigating away). */
  markSubmitted(): void;
  /** True once after each markSubmitted(), then false until the next one. */
  takeReset(): boolean;
}

/** A tracker with no submit recorded yet. One per screen instance. */
export function freshFormTracker(): FreshFormTracker {
  let submitted = false;
  return {
    markSubmitted() {
      submitted = true;
    },
    takeReset() {
      const reset = submitted;
      submitted = false;
      return reset;
    },
  };
}
