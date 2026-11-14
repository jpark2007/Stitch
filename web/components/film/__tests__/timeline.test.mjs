// Run with: cd web && node --test "components/film/__tests__/*.test.mjs"
// The film player's time math (components/film/timeline.ts), checked on
// small hand-made films and on the sample film the mock serves.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import {
  CROSSFADE_SECONDS,
  TITLE_CARD_SECONDS,
  beatIndexAt,
  beatTicks,
  clampTime,
  createSilentClock,
  ease,
  filmLength,
  fitFor,
  formatTime,
  isPlayable,
  kenBurns,
  layersAt,
  motionProgress,
  phrases,
  seekBy,
  shotIndexAt,
  shotProgress,
  spokenLength,
  subtitleAt,
  titleCardAt,
  transformCss,
} from "../timeline.ts";

const SAMPLE = JSON.parse(fs.readFileSync(new URL("../../../public/samples/film.json", import.meta.url), "utf8"));

const SHOTS = [
  { start: 0, duration: 4 },
  { start: 4, duration: 6 },
  { start: 10, duration: 5 },
];

const close = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} is not ${b}`);

test("filmLength takes the duration, or the last shot's end when that is later", () => {
  assert.equal(filmLength({ duration: 20, shots: SHOTS }), 20);
  assert.equal(filmLength({ duration: 12, shots: SHOTS }), 15);
  assert.equal(filmLength({ duration: 0, shots: [] }), 0);
  assert.equal(filmLength({ duration: Number.NaN, shots: [] }), 0);
  close(filmLength(SAMPLE), 60.8);
});

test("shotIndexAt: the shot on screen at t, first before it starts, last after the end", () => {
  assert.equal(shotIndexAt([], 3), -1);
  assert.equal(shotIndexAt(SHOTS, -1), 0);
  assert.equal(shotIndexAt(SHOTS, 0), 0);
  assert.equal(shotIndexAt(SHOTS, 3.999), 0);
  assert.equal(shotIndexAt(SHOTS, 4), 1);
  assert.equal(shotIndexAt(SHOTS, 9.5), 1);
  assert.equal(shotIndexAt(SHOTS, 10), 2);
  assert.equal(shotIndexAt(SHOTS, 99), 2);
  assert.equal(shotIndexAt(SHOTS, Number.NaN), 0);
  // Every sample shot owns its own start.
  SAMPLE.shots.forEach((s, i) => assert.equal(shotIndexAt(SAMPLE.shots, s.start + 0.01), i));
});

test("shotProgress runs 0 to 1 over the shot and is clamped outside it", () => {
  const shot = { start: 4, duration: 6 };
  assert.equal(shotProgress(shot, 4), 0);
  assert.equal(shotProgress(shot, 7), 0.5);
  assert.equal(shotProgress(shot, 10), 1);
  assert.equal(shotProgress(shot, 1), 0);
  assert.equal(shotProgress(shot, 30), 1);
  assert.equal(shotProgress({ start: 0, duration: 0 }, 0), 1);
});

test("seeking is clamped to the film, and NaN goes to the start", () => {
  assert.equal(clampTime(-3, 60), 0);
  assert.equal(clampTime(61, 60), 60);
  assert.equal(clampTime(12.5, 60), 12.5);
  assert.equal(clampTime(Number.NaN, 60), 0);
  assert.equal(clampTime(5, -1), 0);
  assert.equal(seekBy(3, -5, 60), 0);
  assert.equal(seekBy(58, 5, 60), 60);
  assert.equal(seekBy(20, 5, 60), 25);
  assert.equal(seekBy(20, Number.NaN, 60), 20);
});

test("layersAt: one opaque layer, then the next fading in over the crossfade before the cut", () => {
  assert.deepEqual(layersAt([], 1), []);
  const mid = layersAt(SHOTS, 2);
  assert.equal(mid.length, 1);
  assert.equal(mid[0].index, 0);
  assert.equal(mid[0].opacity, 1);

  const f = CROSSFADE_SECONDS;
  const fading = layersAt(SHOTS, 4 - f / 2);
  assert.equal(fading.length, 2);
  assert.deepEqual(fading.map((l) => l.index), [0, 1]);
  close(fading[1].opacity, 0.5);

  // At the cut the next shot is the only layer, fully opaque: nothing jumps.
  const cut = layersAt(SHOTS, 4);
  assert.deepEqual(cut.map((l) => [l.index, l.opacity]), [[1, 1]]);
  // The last shot never fades into anything.
  assert.equal(layersAt(SHOTS, 14.9).length, 1);
});

test("the crossfade is never more than half of the shorter shot", () => {
  const short = [
    { start: 0, duration: 1 },
    { start: 1, duration: 1 },
  ];
  assert.equal(layersAt(short, 0.4).length, 1);
  const l = layersAt(short, 0.75);
  assert.equal(l.length, 2);
  close(l[1].opacity, 0.5);
});

test("a shot's motion starts as it fades in and is continuous across the cut", () => {
  const f = CROSSFADE_SECONDS;
  assert.equal(motionProgress(SHOTS, 0, 0), 0);
  assert.equal(motionProgress(SHOTS, 1, 4 - f), 0);
  const before = layersAt(SHOTS, 4 - 1e-6).find((l) => l.index === 1).progress;
  const after = layersAt(SHOTS, 4).find((l) => l.index === 1).progress;
  close(before, after, 1e-6);
  assert.equal(motionProgress(SHOTS, 2, 15), 1);
  assert.equal(motionProgress(SHOTS, 9, 3), 0);
});

test("kenBurns zooms toward the focus without ever showing an edge", () => {
  const still = kenBurns("in", [0.2, 0.8], 0);
  assert.deepEqual(still, { scale: 1, x: 0, y: 0 });
  const end = kenBurns("in", [0.2, 0.8], 1);
  close(end.scale, 1.16);
  assert.ok(end.x > 0, "a focus on the left moves the photo right");
  assert.ok(end.y < 0, "a focus low down moves the photo up");
  const out = kenBurns("out", [0.5, 0.5], 0);
  close(out.scale, 1.16);
  assert.deepEqual(kenBurns("out", [0.5, 0.5], 1), { scale: 1, x: 0, y: 0 });

  for (const motion of ["in", "out", "left", "right", undefined]) {
    for (const focus of [[0, 0], [1, 1], [0.5, 0.5], [0.9, 0.1], undefined]) {
      for (const p of [0, 0.25, 0.5, 0.75, 1]) {
        const t = kenBurns(motion, focus, p);
        const room = (t.scale - 1) / 2 + 1e-9;
        assert.ok(t.scale >= 1, `${motion} ${focus} ${p}`);
        assert.ok(Math.abs(t.x) <= room && Math.abs(t.y) <= room, `${motion} ${focus} ${p}: edge shows`);
      }
    }
  }
});

test("pans travel the way they are named, and reduced motion holds still", () => {
  const l0 = kenBurns("left", [0.5, 0.5], 0);
  const l1 = kenBurns("left", [0.5, 0.5], 1);
  assert.ok(l1.x < l0.x, "left drifts the photo left");
  const r0 = kenBurns("right", [0.5, 0.5], 0);
  const r1 = kenBurns("right", [0.5, 0.5], 1);
  assert.ok(r1.x > r0.x, "right drifts the photo right");
  assert.deepEqual(kenBurns("in", [0.1, 0.1], 0.7, true), { scale: 1, x: 0, y: 0 });
});

test("ease is gentle, monotonic and pinned at both ends", () => {
  assert.equal(ease(0), 0);
  close(ease(1), 1);
  close(ease(0.5), 0.5);
  let last = -1;
  for (let p = 0; p <= 1.0001; p += 0.05) {
    const e = ease(p);
    assert.ok(e >= last);
    last = e;
  }
  assert.equal(ease(-2), 0);
  close(ease(9), 1);
});

test("transformCss is a GPU-only transform", () => {
  assert.equal(transformCss({ scale: 1.16, x: 0.05, y: -0.02 }), "translate3d(5%, -2%, 0) scale(1.16)");
  assert.equal(transformCss({ scale: 1, x: 0, y: 0 }), "translate3d(0%, 0%, 0) scale(1)");
});

test("fitFor fills the frame for close shapes and shows all of a portrait", () => {
  assert.equal(fitFor(1500, 1000, 1.5), "cover");
  assert.equal(fitFor(4000, 3000, 1.5), "cover");
  assert.equal(fitFor(1920, 1080, 1.5), "cover");
  assert.equal(fitFor(3000, 4000, 1.5), "contain");
  assert.equal(fitFor(1000, 1000, 1.5), "contain");
  assert.equal(fitFor(3000, 1000, 1.5), "contain");
  assert.equal(fitFor(0, 0, 1.5), "cover");
});

test("beatIndexAt and titleCardAt follow the beats", () => {
  const beats = SAMPLE.beats;
  assert.equal(beatIndexAt(beats, 0), -1, "the sample's first beat starts at 1s");
  assert.equal(beatIndexAt(beats, 1), 0);
  assert.equal(beatIndexAt(beats, 17.06), 1);
  assert.equal(beatIndexAt(beats, 60), 3);
  assert.equal(beatIndexAt([], 5), -1);

  assert.equal(titleCardAt(beats, 0.5), null);
  assert.equal(titleCardAt(beats, 1), 0);
  assert.equal(titleCardAt(beats, 1 + TITLE_CARD_SECONDS - 0.01), 0);
  assert.equal(titleCardAt(beats, 1 + TITLE_CARD_SECONDS), null);
  assert.equal(titleCardAt(beats, 33), 2);
  assert.equal(titleCardAt(beats, 40), null);
});

test("beatTicks: each beat's place on the scrub bar, leaving out one at the very start", () => {
  const ticks = beatTicks([{ start: 0 }, { start: 15 }, { start: 30 }], 60);
  assert.deepEqual(ticks, [0.25, 0.5]);
  assert.deepEqual(beatTicks([{ start: 5 }], 0), []);
  assert.equal(beatTicks(SAMPLE.beats, filmLength(SAMPLE)).length, 4);
});

const W = (pairs) => pairs.map(([t, word]) => ({ t, word }));

test("phrases cut at sentence ends, commas, the word cap and pauses", () => {
  const words = W([
    [0, "We"], [0.3, "left"], [0.6, "early,"], [0.9, "the"], [1.2, "four"], [1.5, "of"], [1.8, "us."],
    [2.1, "Sam"], [2.4, "slept"], [5, "Later"], [5.3, "on"],
  ]);
  const p = phrases(words, 7, 1.1);
  assert.deepEqual(p, [
    { from: 0, to: 3 },
    { from: 3, to: 7 },
    { from: 7, to: 9 },
    { from: 9, to: 11 },
  ]);
  const capped = phrases(W(Array.from({ length: 10 }, (_, i) => [i * 0.3, `w${i}`])), 4, 5);
  assert.deepEqual(capped.map((x) => x.to - x.from), [4, 4, 2]);
  assert.deepEqual(phrases([]), []);
  // A comma after one or two words does not make a stub of a line.
  assert.deepEqual(phrases(W([[0, "Well,"], [0.3, "we"], [0.6, "went."]])), [{ from: 0, to: 3 }]);
});

test("every sample word is in exactly one phrase, in order", () => {
  const p = phrases(SAMPLE.words);
  assert.equal(p[0].from, 0);
  for (let i = 1; i < p.length; i++) assert.equal(p[i].from, p[i - 1].to);
  assert.equal(p[p.length - 1].to, SAMPLE.words.length);
  assert.ok(p.every((x) => x.to - x.from <= 7 && x.to > x.from));
});

test("subtitleAt shows the phrase being spoken with the current word, and nothing in the quiet", () => {
  const words = W([[1, "We"], [1.3, "left"], [1.6, "early."], [5, "Then"], [5.3, "rain."]]);
  const lines = phrases(words);
  assert.equal(subtitleAt(words, lines, 0.5), null);
  assert.deepEqual(subtitleAt(words, lines, 1), { phrase: 0, words: ["We", "left", "early."], current: 0 });
  assert.deepEqual(subtitleAt(words, lines, 1.45), { phrase: 0, words: ["We", "left", "early."], current: 1 });
  assert.equal(subtitleAt(words, lines, 2.5, 1.4)?.current, 2, "lingers after the last word");
  assert.equal(subtitleAt(words, lines, 3.1, 1.4), null, "gone in the pause between beats");
  assert.deepEqual(subtitleAt(words, lines, 5.31), { phrase: 1, words: ["Then", "rain."], current: 1 });
  assert.equal(subtitleAt([], [], 3), null);
});

test("formatTime and spokenLength", () => {
  assert.equal(formatTime(0), "0:00");
  assert.equal(formatTime(42.9), "0:42");
  assert.equal(formatTime(65), "1:05");
  assert.equal(formatTime(720), "12:00");
  assert.equal(formatTime(-4), "0:00");
  assert.equal(spokenLength(45.2), "45 sec");
  assert.equal(spokenLength(60.8), "1 min", "whole seconds, as the player's clock shows them");
  assert.equal(spokenLength(65.2), "1 min 5 sec");
  assert.equal(spokenLength(120), "2 min");
});

test("isPlayable needs a ready film with shots", () => {
  assert.equal(isPlayable(SAMPLE), true);
  assert.equal(isPlayable({ ...SAMPLE, status: "processing" }), false);
  assert.equal(isPlayable({ ...SAMPLE, shots: [] }), false);
});

test("the silent clock plays, pauses, seeks and stops at the end", async () => {
  let ms = 1000;
  const clock = createSilentClock(10, () => ms);
  assert.equal(clock.time(), 0);
  assert.equal(clock.playing(), false);
  await clock.play();
  ms += 2500;
  assert.equal(clock.time(), 2.5);
  assert.equal(clock.playing(), true);
  clock.pause();
  ms += 4000;
  assert.equal(clock.time(), 2.5);
  clock.seek(7);
  assert.equal(clock.time(), 7);
  await clock.play();
  ms += 1000;
  assert.equal(clock.time(), 8);
  clock.seek(-3);
  assert.equal(clock.time(), 0, "seeking while playing restarts from the new place");
  ms += 500;
  assert.equal(clock.time(), 0.5);
  ms += 60_000;
  assert.equal(clock.time(), 10, "stops at the end");
  assert.equal(clock.playing(), false);
  clock.pause();
  await clock.play();
  assert.equal(clock.time(), 0, "play after the end starts over");
});
