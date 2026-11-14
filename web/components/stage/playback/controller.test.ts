// Run with: cd web && node --test "components/stage/playback/*.test.ts"
// Drives the controller with a fake clock and a fake <audio> element.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import type { Timeline } from "@/lib/types";
import type * as Controller from "./controller";
import type * as Sampler from "./sampler";

const { PlaybackController } = (await import(new URL("./controller.ts", import.meta.url).href)) as typeof Controller;
const { compileTimeline, boneRotation } = (await import(new URL("./sampler.ts", import.meta.url).href)) as typeof Sampler;

const hymn = JSON.parse(
  readFileSync(new URL("../../../public/samples/hymn_books.timeline.json", import.meta.url), "utf8"),
) as Timeline;

/** The sample with recorded narration for both tellers (the real sample has none yet). */
function withAudio(): Timeline {
  const t = structuredClone(hymn);
  t.narrations!.dad.audio = "media/n_dad";
  t.narrations!.mom.audio = "media/n_mom";
  t.audio = "media/n_dad";
  return t;
}

class FakeAudio implements Controller.AudioLike {
  src = "";
  playbackRate = 1;
  paused = true;
  duration = Number.NaN;
  plays = 0;
  loads = 0;
  seeks: number[] = [];
  refuse: "NotAllowedError" | "NotSupportedError" | null = null;
  private time = 0;
  get currentTime() {
    return this.time;
  }
  set currentTime(v: number) {
    this.time = v;
    this.seeks.push(v);
  }
  /** Simulates the browser's own clock moving the element (no seek recorded). */
  drift(to: number) {
    this.time = to;
  }
  play() {
    this.plays++;
    if (this.refuse) return Promise.reject(Object.assign(new Error("refused"), { name: this.refuse }));
    this.paused = false;
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
  }
  load() {
    this.loads++;
  }
  removeAttribute(name: string) {
    if (name === "src") this.src = "";
  }
}

function setup(options: Partial<Controller.ControllerOptions> = {}, timeline = hymn) {
  let wall = 1000;
  const clock = {
    get ms() {
      return wall;
    },
    advance(seconds: number) {
      wall += seconds * 1000;
      player.tick(wall);
    },
  };
  const player = new PlaybackController(compileTimeline(timeline), {
    now: () => wall,
    resolveAudio: (id) => `/api/media/${id}`,
    ...options,
  });
  return { player, clock };
}

const close = (a: number, e: number, msg?: string) =>
  assert.ok(Math.abs(a - e) < 1e-6, `${msg ?? ""} expected ${e}, got ${a}`);

const flush = () => new Promise((r) => setTimeout(r, 0));

// ------------------------------------------------------------------ clock

test("starts paused at 0 with the first frame sampled", () => {
  const { player } = setup();
  assert.equal(player.time, 0);
  assert.equal(player.getStatus().playing, false);
  assert.equal(player.frame.puppetsById.get("dad")!.position[0], -1.3);
  assert.equal(player.getStatus().narrator, "dad");
  assert.equal(player.getStatus().audio, "none");
});

test("play advances with the clock; pause holds", () => {
  const { player, clock } = setup();
  player.play();
  clock.advance(1);
  close(player.time, 1);
  clock.advance(0.5);
  player.pause();
  close(player.time, 1.5);
  clock.advance(3);
  close(player.time, 1.5);
  assert.equal(player.getStatus().playing, false);
  player.toggle();
  clock.advance(0.25);
  close(player.time, 1.75);
});

test("rate scales the clock from the moment it changes", () => {
  const { player, clock } = setup();
  player.play();
  clock.advance(1);
  player.setRate(2);
  clock.advance(0.5);
  close(player.time, 2);
  player.setRate(100);
  assert.equal(player.getStatus().rate, 4, "rate is clamped");
});

test("seek clamps to the timeline and keeps playing", () => {
  const { player, clock } = setup();
  player.seek(-3);
  assert.equal(player.time, 0);
  player.seek(99);
  assert.equal(player.time, hymn.duration);
  player.seek(4.6);
  assert.equal(player.frame.propsById.get("hymn_books")!.parent.kind, "ground", "seek samples at once");
  player.play();
  clock.advance(0.5);
  close(player.time, 5.1);
});

test("without loop it stops at the end; play again starts over", () => {
  const { player, clock } = setup();
  player.seek(10);
  player.play();
  clock.advance(5);
  assert.equal(player.time, hymn.duration);
  assert.equal(player.getStatus().playing, false);
  assert.equal(player.getStatus().ended, true);
  player.play();
  assert.equal(player.time, 0);
  assert.equal(player.getStatus().ended, false);
});

test("with loop it wraps around", () => {
  const { player, clock } = setup({ loop: true });
  player.seek(10);
  player.play();
  clock.advance(1);
  close(player.time, 10 + 1 - hymn.duration);
  assert.equal(player.getStatus().playing, true);
});

test("frames re-sample only when time or input changes", () => {
  const { player, clock } = setup();
  const v = player.frameVersion;
  assert.equal(player.tick(clock.ms), false);
  assert.equal(player.frameVersion, v);
  player.play();
  clock.advance(0.1);
  assert.equal(player.frameVersion, v + 1);
  let seen = 0;
  const off = player.onFrame(() => seen++);
  clock.advance(0.1);
  assert.equal(seen, 1);
  off();
  clock.advance(0.1);
  assert.equal(seen, 1);
});

test("status is a new object only when something discrete changes", () => {
  const { player, clock } = setup();
  let emits = 0;
  player.subscribe(() => emits++);
  const s0 = player.getStatus();
  player.tick(clock.ms);
  assert.equal(player.getStatus(), s0, "idle ticks change nothing");
  player.play();
  assert.equal(emits, 1);
  clock.advance(0.05); // still before the first caption
  assert.equal(emits, 1, "time alone does not emit");
  clock.advance(1.2); // t = 1.25: dad's first caption and its first word
  assert.equal(player.getStatus().captionIndex, 0);
  assert.equal(player.getStatus().wordIndex, 0);
  assert.equal(emits, 2);
});

// ------------------------------------------------------------------ narration

test("switching teller swaps captions and replays from 0", () => {
  const { player, clock } = setup();
  player.seek(5);
  player.setNarrator("mom");
  assert.equal(player.time, 0);
  assert.equal(player.getStatus().playing, true);
  assert.equal(player.getStatus().narrator, "mom");
  clock.advance(1.3);
  assert.equal(player.frame.narration.caption?.text, "I was carrying far too many hymn books.");
  const t = player.time;
  player.setNarrator("mom");
  assert.equal(player.time, t, "choosing the current teller does nothing");
  player.setNarrator("unknown");
  assert.equal(player.getStatus().narrator, "dad", "an unknown teller falls back to the default");
});

test("audio follows the teller and the clock", async () => {
  const audio = new FakeAudio();
  const { player, clock } = setup({ audio }, withAudio());
  assert.equal(audio.src, "/api/media/media/n_dad");
  assert.equal(player.getStatus().audio, "ready");
  assert.equal(audio.paused, true, "nothing plays before play()");

  player.play();
  assert.equal(audio.plays, 1, "play() starts audio in the same call (user gesture)");
  assert.equal(audio.paused, false);
  await flush();

  audio.drift(0.95);
  clock.advance(1);
  const seeks = audio.seeks.length;
  audio.drift(1.05);
  clock.advance(0);
  assert.equal(audio.seeks.length, seeks, "small drift is left alone");
  audio.drift(0.4);
  clock.advance(0);
  assert.equal(audio.seeks.length, seeks + 1);
  close(audio.seeks.at(-1)!, 1, "large drift is re-seeked to scene time");

  player.pause();
  assert.equal(audio.paused, true);
  player.seek(3);
  close(audio.currentTime, 3);

  player.setNarrator("mom");
  assert.equal(audio.src, "/api/media/media/n_mom");
  assert.equal(audio.currentTime, 0, "the new telling replays from 0");
  assert.equal(audio.paused, false);

  player.setRate(1.5);
  assert.equal(audio.playbackRate, 1.5);
});

test("audio stops when the narration is shorter than the scene", async () => {
  const audio = new FakeAudio();
  audio.duration = 2;
  const { player, clock } = setup({ audio }, withAudio());
  player.play();
  await flush();
  clock.advance(2.5);
  assert.equal(audio.paused, true);
  const plays = audio.plays;
  clock.advance(0.5);
  assert.equal(audio.plays, plays, "not restarted past its end");
  close(player.time, 3);
});

test("missing audio is silent, never an error", () => {
  const { player, clock } = setup({ audio: null }, withAudio());
  player.play();
  clock.advance(1);
  close(player.time, 1);
  assert.equal(player.getStatus().audio, "none");

  const audio = new FakeAudio();
  const silent = setup({ audio }); // the real sample: no recordings yet
  silent.player.play();
  assert.equal(audio.plays, 0);
  assert.equal(audio.src, "");
  assert.equal(silent.player.getStatus().audio, "none");

  const unresolved = setup({ audio: new FakeAudio(), resolveAudio: () => null }, withAudio());
  assert.equal(unresolved.player.getStatus().audio, "none");
});

test("blocked autoplay keeps the scene playing and retries on the next play()", async () => {
  const audio = new FakeAudio();
  audio.refuse = "NotAllowedError";
  const { player, clock } = setup({ audio }, withAudio());
  player.play();
  await flush();
  assert.equal(player.getStatus().audio, "blocked");
  clock.advance(1);
  close(player.time, 1, "the scene does not wait for audio");
  assert.equal(audio.plays, 1, "no retry loop while blocked");

  audio.refuse = null;
  player.pause();
  player.play();
  assert.equal(audio.plays, 2);
  assert.equal(player.getStatus().audio, "ready");
});

test("a failed load marks audio as errored and the scene plays on", async () => {
  const audio = new FakeAudio();
  audio.refuse = "NotSupportedError";
  const { player, clock } = setup({ audio }, withAudio());
  player.play();
  await flush();
  assert.equal(player.getStatus().audio, "error");
  clock.advance(1);
  close(player.time, 1);
});

test("attachAudio later picks up the current teller; detaching clears it", () => {
  const { player } = setup({}, withAudio());
  const audio = new FakeAudio();
  player.attachAudio(audio);
  assert.equal(audio.src, "/api/media/media/n_dad");
  player.attachAudio(null);
  assert.equal(audio.src, "");
  assert.equal(player.getStatus().audio, "none");
});

// ------------------------------------------------------------------ interactions

test("holding the pull tab scrubs its targets while the scene holds", () => {
  const { player, clock } = setup();
  player.play();
  clock.advance(2);
  const books = player.frame.propsById.get("hymn_books")!;
  const dadX = player.frame.puppetsById.get("dad")!.position[0];

  player.holdInteraction("drop_books", 0.5);
  assert.equal(player.getStatus().scrubbing, true);
  assert.equal(player.getStatus().playing, true, "still 'playing': it resumes on release");
  assert.equal(books.parent.kind, "ground");
  close(books.position[1], 0.4065);
  close(boneRotation(player.frame.puppetsById.get("mom")!, "arm_r.upper"), 0.15);

  clock.advance(1);
  close(player.time, 2, "scene time holds while the tab is held");
  close(player.frame.puppetsById.get("dad")!.position[0], dadX, "dad holds too");

  player.holdInteraction("drop_books", 1);
  close(books.position[1], 0.3);

  player.releaseInteraction("drop_books");
  assert.equal(player.getStatus().scrubbing, false);
  assert.equal(books.parent.raw, "puppet:mom.hand_r", "targets return to scene time");
  clock.advance(0.5);
  close(player.time, 2.5, "playback resumes where it was");
});

test("releasing with settle: seek moves the scene to where the tab left it", () => {
  const { player, clock } = setup();
  player.seek(2);
  player.holdInteraction("drop_books", 1);
  player.releaseInteraction("drop_books", { settle: "seek" });
  close(player.time, 4.75);
  assert.equal(player.getStatus().playing, false, "it was paused, so it stays paused");
  assert.equal(player.frame.propsById.get("hymn_books")!.parent.kind, "ground");
  clock.advance(1);
  close(player.time, 4.75);
});

test("play() while a tab is held resumes on release", () => {
  const { player, clock } = setup();
  player.holdInteraction("drop_books", 0.2);
  player.play();
  clock.advance(1);
  assert.equal(player.time, 0);
  player.releaseInteraction("drop_books");
  clock.advance(1);
  close(player.time, 1);
});

test("unknown interactions are ignored", () => {
  const { player } = setup();
  player.holdInteraction("nope", 1);
  assert.equal(player.heldValue("nope"), null);
  player.releaseInteraction("nope");
  assert.equal(player.getStatus().scrubbing, false);
});
