/**
 * The moment's play gate (playGate.ts): when the pop-up plays in the book,
 * what it offers the book's camera, and when it asks the demand-driven
 * canvas for a frame.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The bugs: on a phone resting on the story page the armed gate asked for a
 * frame every frame, so a still book redrew at the display rate forever; and
 * a telling's end gave up the camera's shot on a frame the camera had
 * already posed, without asking for another, so with no pointer input the
 * camera stayed leaned into the stage after a second telling.
 *
 * The module is imported by a computed path so tsc does not ask for a `.ts`
 * import extension (see leafShape.test.ts).
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Gate = typeof import("./playGate");
type Shot = typeof import("./shot");
const paths = ["./playGate.ts", "./shot.ts"];
const g: Gate = await import(paths[0]);
const s: Shot = await import(paths[1]);

type Input = Parameters<Gate["stepPlayGate"]>[1];
const input = (over: Partial<Input> = {}): Input => ({ reveal: 1, view: 1, ended: false, playing: false, now: 0, ...over });

/** A gate that has started its telling on an open, in-view spread. */
function started() {
  const gate = g.createPlayGate();
  gate.ready = true;
  assert.equal(g.stepPlayGate(gate, input()).action, "start");
  return gate;
}

describe("stepPlayGate", () => {
  it("starts once the scene is drawn and the spread lies open and in view", () => {
    const gate = g.createPlayGate();
    assert.equal(g.stepPlayGate(gate, input()).action, "none", "not drawn yet");
    gate.ready = true;
    assert.equal(g.stepPlayGate(gate, input({ reveal: 0.6 })).action, "none", "still rising");
    assert.equal(g.stepPlayGate(gate, input({ reveal: 0.9 })).action, "start");
    assert.equal(g.stepPlayGate(gate, input()).offer, true, "and then offers its camera");
  });

  it("asks for frames while the page settles, and none once a phone rests on the story page", () => {
    const gate = g.createPlayGate();
    gate.ready = true;
    // The spread still easing open: draw on.
    assert.equal(g.stepPlayGate(gate, input({ reveal: 0.95, view: 0 })).redraw, true);
    // Open and still, the reader on the story page (view 0): a still frame.
    assert.equal(g.stepPlayGate(gate, input({ reveal: 1, view: 0 })).redraw, false);
    for (let i = 0; i < 5; i++) {
      const step = g.stepPlayGate(gate, input({ reveal: 1, view: 0, now: i * 16 }));
      assert.equal(step.redraw, false, `frame ${i} at rest`);
      assert.equal(step.action, "none");
      assert.equal(step.offer, false);
    }
    // The camera pans toward the stage page: draw while it moves, then start.
    assert.equal(g.stepPlayGate(gate, input({ reveal: 1, view: 0.3 })).redraw, true);
    assert.equal(g.stepPlayGate(gate, input({ reveal: 1, view: 0.7 })).action, "start");
  });

  it("holds the shot a beat after the telling ends, then gives it up on a frame that asks for the next", () => {
    const gate = started();
    const shot = s.createStageShot();
    // What MomentStage does each frame: the camera hears of a change only on a drawn frame.
    const frame = (over: Partial<Input>) => {
      const step = g.stepPlayGate(gate, input(over));
      return { step, wake: shot.offer(step.offer) || step.redraw };
    };
    assert.equal(frame({ playing: true, now: 0 }).wake, true, "leaning in");
    assert.equal(shot.want, 1);
    const end = 10_000;
    for (const t of [end, end + 300, end + g.END_HOLD_MS - 1]) {
      const { step, wake } = frame({ ended: true, now: t });
      assert.equal(step.offer, true, `held at +${t - end} ms`);
      assert.equal(wake, false, "nothing to draw while the last line holds");
      assert.equal(step.wakeAt, end + g.END_HOLD_MS, "one wake, when the hold is up");
    }
    // The hold runs out: the offer goes, and that frame asks for one more.
    const out = frame({ ended: true, now: end + g.END_HOLD_MS });
    assert.equal(out.step.wakeAt, 0);
    assert.equal(out.step.offer, false);
    assert.equal(shot.want, 0);
    assert.equal(out.wake, true, "the camera must get a frame to ease out on");
    // After that, a told scene on a still page asks for nothing.
    assert.equal(frame({ ended: true, now: end + 2000 }).wake, false);
  });

  it("a second telling (replayed from 0) takes the shot and the end hold again", () => {
    const gate = started();
    g.stepPlayGate(gate, input({ ended: true, now: 0 }));
    assert.equal(g.stepPlayGate(gate, input({ ended: true, now: 5000 })).offer, false);
    // A teller chosen: the player seeks to 0 and plays.
    assert.equal(g.stepPlayGate(gate, input({ playing: true, now: 6000 })).offer, true);
    assert.equal(g.stepPlayGate(gate, input({ ended: true, now: 16000 })).offer, true, "a fresh hold");
    assert.equal(g.stepPlayGate(gate, input({ ended: true, now: 16000 + g.END_HOLD_MS })).offer, false);
  });

  it("re-arms when the page turns away, and pauses and resumes on a phone", () => {
    const gate = started();
    assert.equal(g.stepPlayGate(gate, input({ view: 0.1, playing: true })).action, "pauseAway");
    assert.equal(g.stepPlayGate(gate, input({ view: 0.1 })).action, "none");
    assert.equal(g.stepPlayGate(gate, input({ view: 0.9 })).action, "resume");
    const fold = g.stepPlayGate(gate, input({ reveal: 0.2, playing: true }));
    assert.equal(fold.action, "rearm");
    assert.equal(fold.offer, false, "a folding stage gives up the camera");
    assert.equal(g.stepPlayGate(gate, input({ reveal: 0.2 })).action, "none", "once");
    assert.equal(g.stepPlayGate(gate, input()).action, "start", "opens again from the start");
  });
});
