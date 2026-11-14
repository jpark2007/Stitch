/**
 * The score generator (score.ts) over the ten palettes (palettes.ts):
 * harmony parsing, voicing, determinism, loop shape and "never busy".
 *
 *   cd web && node --no-warnings --test "components/audio/*.test.ts"
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Score = typeof import("./score");
type Pals = typeof import("./palettes");
type Eras = typeof import("./eras");
const paths = ["./score.ts", "./palettes.ts", "./eras.ts"];
const s: Score = await import(paths[0]);
const { PALETTES }: Pals = await import(paths[1]);
const { ERA_IDS }: Eras = await import(paths[2]);

const pcs = (c: { root: number; intervals: number[] }) => c.intervals.map((i) => (c.root + i) % 12);

describe("harmony", () => {
  it("parses roman numerals in a key", () => {
    // C major (tonic 60)
    assert.deepEqual(pcs(s.parseChord("I", 60)), [0, 4, 7]);
    assert.deepEqual(pcs(s.parseChord("vi", 60)), [9, 0, 4]);
    assert.deepEqual(pcs(s.parseChord("V7", 60)), [7, 11, 2, 5]);
    assert.deepEqual(pcs(s.parseChord("ii7", 60)), [2, 5, 9, 0]);
    assert.deepEqual(pcs(s.parseChord("IVmaj7", 60)), [5, 9, 0, 4]);
    // A minor (tonic 57): bVI = F, bVII = G, bIII = C
    assert.deepEqual(pcs(s.parseChord("bVI", 57)), [5, 9, 0]);
    assert.deepEqual(pcs(s.parseChord("bVII", 57)), [7, 11, 2]);
    assert.deepEqual(pcs(s.parseChord("i", 57)), [9, 0, 4]);
    assert.deepEqual(pcs(s.parseChord("V7sus4", 60)), [7, 0, 2, 5]);
  });

  it("rejects nonsense", () => {
    assert.throws(() => s.parseChord("X", 60));
    assert.throws(() => s.parseChord("Iwhat", 60));
  });

  it("parses every chord in every palette", () => {
    for (const id of ERA_IDS) {
      const p = PALETTES[id];
      for (const bar of p.progression) for (const sym of bar.split("|")) assert.doesNotThrow(() => s.parseChord(sym, p.tonic), `${id} ${sym}`);
    }
  });

  it("voices chords inside the range with the chord's own notes", () => {
    const c = s.parseChord("IVmaj7", 60);
    const v = s.voiceChord(c, 4, 55, 72);
    assert.equal(v.length, 4);
    for (const n of v) {
      assert.ok(n >= 55 && n <= 72, `${n} in range`);
      assert.ok(pcs(c).includes(n % 12));
    }
    assert.deepEqual([...v].sort((a, b) => a - b), v, "ascending");
  });

  it("voice-leads: consecutive chords move little", () => {
    const p = PALETTES["1950s"];
    const vs = p.progression.map((sym) => s.voiceChord(s.parseChord(sym, p.tonic), 3, 55, 70));
    for (let i = 1; i < vs.length; i++) {
      const move = vs[i].reduce((a, n, k) => a + Math.abs(n - vs[i - 1][k]), 0);
      assert.ok(move <= 14, `bar ${i} moves ${move} semitones`);
    }
  });
});

describe("timing", () => {
  it("swings off-beat eighths and leaves on-beats alone", () => {
    const beat = 0.5;
    assert.equal(s.stepTime(0, 2, beat, 0.66, 2), 0);
    assert.equal(s.stepTime(2, 2, beat, 0.66, 2), 0.5);
    assert.ok(Math.abs(s.stepTime(1, 2, beat, 0.66, 2) - 0.33) < 1e-9);
    // Straight, or a different grid: no swing.
    assert.equal(s.stepTime(1, 2, beat, 0.5, 2), 0.25);
    assert.equal(s.stepTime(1, 4, beat, 0.66, 2), 0.125);
  });
});

describe("scores", () => {
  it("are deterministic per era", () => {
    for (const id of ERA_IDS) assert.deepEqual(s.scoreLoop(PALETTES[id]), s.scoreLoop(PALETTES[id]), id);
  });

  it("loop exactly: bar n and bar n + loopBars are the same", () => {
    for (const id of ERA_IDS) {
      const p = PALETTES[id];
      assert.deepEqual(s.barEvents(p, 3), s.barEvents(p, 3 + p.loopBars), id);
      assert.equal(s.scoreLoop(p).bars.length, p.loopBars);
      assert.equal(p.loopBars % p.progression.length, 0, `${id} loop is whole progressions`);
    }
  });

  it("keeps every event inside its bar, sorted, with sane values", () => {
    for (const id of ERA_IDS) {
      const p = PALETTES[id];
      const { bars, barDur } = s.scoreLoop(p);
      for (const evs of bars) {
        for (let i = 0; i < evs.length; i++) {
          const e = evs[i];
          assert.ok(e.t >= 0 && e.t < barDur, `${id} t ${e.t}`);
          assert.ok(e.dur > 0 && e.dur < barDur * 2, `${id} dur ${e.dur}`);
          assert.ok(e.vel > 0 && e.vel <= 1, `${id} vel ${e.vel}`);
          assert.ok(e.midi >= 24 && e.midi <= 96, `${id} midi ${e.midi}`);
          if (i) assert.ok(evs[i - 1].t <= e.t, `${id} sorted`);
        }
      }
    }
  });

  it("keeps pitched parts inside their ranges", () => {
    for (const id of ERA_IDS) {
      const p = PALETTES[id];
      for (const evs of s.scoreLoop(p).bars) {
        for (const e of evs) {
          const part = p.parts[e.part];
          if (!("range" in part)) continue;
          const lo = Math.min(part.range[0], "bass" in part ? part.bass[0] : Infinity);
          const hi = Math.max(part.range[1], "bass" in part ? part.bass[1] : -Infinity);
          assert.ok(e.midi >= lo && e.midi <= hi, `${id} part ${e.part} midi ${e.midi} outside ${lo}..${hi}`);
        }
      }
    }
  });

  it("plays only notes of the key or the bar's chord (no stray dissonance)", () => {
    for (const id of ERA_IDS) {
      const p = PALETTES[id];
      const scale = new Set(s.scalePitchClasses(p.tonic, p.mode));
      const { bars } = s.scoreLoop(p);
      bars.forEach((evs, b) => {
        const chordPcs = new Set(
          p.progression[b % p.progression.length]
            .split("|")
            .flatMap((sym) => pcs(s.parseChord(sym, p.tonic))),
        );
        for (const e of evs) {
          const part = p.parts[e.part];
          if (part.role === "drum" || part.role === "bass") continue;
          const pc = e.midi % 12;
          assert.ok(scale.has(pc) || chordPcs.has(pc), `${id} bar ${b} pc ${pc}`);
        }
      });
    }
  });

  it("is never busy: few melodic onsets per second, a gentle tempo", () => {
    for (const id of ERA_IDS) {
      const p = PALETTES[id];
      assert.ok(p.bpm >= 60 && p.bpm <= 112, `${id} bpm ${p.bpm}`);
      const { bars, barDur } = s.scoreLoop(p);
      let melodic = 0;
      for (const evs of bars) {
        const onsets = new Set<string>();
        for (const e of evs) if (p.parts[e.part].role === "melody") onsets.add(e.t.toFixed(3));
        melodic += onsets.size;
      }
      const perSec = melodic / (bars.length * barDur);
      assert.ok(perSec <= 2.2, `${id} melody ${perSec.toFixed(2)} notes/s`);
    }
  });

  it("gives each era a different piece: tempo, metre, key or chords differ from its neighbours", () => {
    for (let i = 1; i < ERA_IDS.length; i++) {
      const a = PALETTES[ERA_IDS[i - 1]];
      const b = PALETTES[ERA_IDS[i]];
      const sig = (p: typeof a) => [p.bpm, p.beatsPerBar, p.tonic % 12, p.mode, p.progression.join(" "), p.parts.map((x) => x.inst).sort().join()].join("|");
      assert.notEqual(sig(a), sig(b));
      const instA = new Set(a.parts.map((x) => x.inst));
      const shared = b.parts.filter((x) => instA.has(x.inst)).length;
      assert.ok(shared < b.parts.length, `${b.id} has an instrument ${a.id} lacks`);
    }
  });

  it("every part names a known role and the melody leaves room", () => {
    for (const id of ERA_IDS) {
      const p = PALETTES[id];
      assert.ok(p.parts.length >= 2 && p.parts.length <= 8, `${id} parts`);
      for (const part of p.parts) assert.ok(part.gain <= -5, `${id} ${part.inst} is quiet`);
    }
  });
});

describe("seeded random", () => {
  it("repeats for a seed and differs between seeds", () => {
    const a = s.seededRandom("1960s");
    const b = s.seededRandom("1960s");
    const c = s.seededRandom("1970s");
    const xs = [a(), a(), a()];
    assert.deepEqual(xs, [b(), b(), b()]);
    assert.notDeepEqual(xs, [c(), c(), c()]);
    for (const x of xs) assert.ok(x >= 0 && x < 1);
  });

  it("converts MIDI to frequency", () => {
    assert.equal(s.midiToFreq(69), 440);
    assert.ok(Math.abs(s.midiToFreq(60) - 261.6256) < 1e-3);
  });
});
