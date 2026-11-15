/**
 * Read-aloud (readAloud.ts) over a fake speechSynthesis: the voice it
 * chooses, one voice at a time, the story over captions, mute, and the
 * spoken captions of a silent telling.
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The module is imported by a computed path so tsc does not ask for a `.ts`
 * import extension (see leafShape.test.ts).
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

type Mod = typeof import("./readAloud");
const paths = ["./readAloud.ts"];
const m: Mod = await import(paths[0]);

type Utterance = import("./readAloud").UtteranceLike;
type Voice = import("./readAloud").VoiceLike;

/** A speechSynthesis that records what it is asked, and ends lines on demand. */
function fakeSynth(voices: Voice[] = []) {
  const spoken: Utterance[] = [];
  const queue: Utterance[] = [];
  let cancels = 0;
  const synth = {
    speak(u: Utterance) {
      spoken.push(u);
      queue.push(u);
    },
    cancel() {
      cancels++;
      // As browsers do: each queued line errors "interrupted".
      for (const u of queue.splice(0)) u.onerror?.({ error: "interrupted" });
    },
    getVoices: () => voices,
  };
  return {
    synth,
    spoken,
    cancels: () => cancels,
    /** The line now speaking finishes. */
    finish() {
      queue.shift()?.onend?.({});
    },
  };
}

const utterance = (text: string): Utterance => ({ text, voice: null, lang: "", rate: 1, pitch: 1, onend: null, onerror: null });

function mute(initial = false) {
  let muted = initial;
  const ls = new Set<(m: boolean) => void>();
  return {
    get: () => muted,
    subscribe(l: (m: boolean) => void) {
      ls.add(l);
      return () => ls.delete(l);
    },
    set(v: boolean) {
      muted = v;
      ls.forEach((l) => l(v));
    },
  };
}

function engine(voices: Voice[] = [], muted = mute()) {
  const f = fakeSynth(voices);
  const voice = new m.ReadAloud({ synth: f.synth, createUtterance: utterance, muted });
  return { ...f, voice, muted };
}

describe("pickVoice", () => {
  it("prefers a warm, natural English voice", () => {
    const v = m.pickVoice([
      { name: "Albert", lang: "en-US", default: true },
      { name: "Thomas", lang: "fr-FR" },
      { name: "Daniel", lang: "en-GB" },
      { name: "Samantha", lang: "en-US" },
    ]);
    assert.equal(v?.name, "Samantha");
    assert.equal(m.pickVoice([{ name: "Microsoft Aria Online (Natural) - English", lang: "en-US" }, { name: "Samantha", lang: "en-US" }])?.name.includes("Natural"), true);
  });

  it("falls back to American English, then any English, then the default", () => {
    assert.equal(m.pickVoice([{ name: "Fred", lang: "en-GB" }, { name: "Alex", lang: "en_US" }])?.name, "Alex");
    assert.equal(m.pickVoice([{ name: "Amélie", lang: "fr-CA" }, { name: "Moira", lang: "en-IE" }])?.name, "Moira");
    assert.equal(m.pickVoice([{ name: "Natural Amélie", lang: "fr-CA" }]), null);
    assert.equal(m.pickVoice([]), null);
  });
});

describe("ReadAloud", () => {
  it("reads the story a sentence at a time, in the chosen voice at 0.95", () => {
    const e = engine([{ name: "Karen", lang: "en-AU" }]);
    assert.equal(e.voice.read("How I met your mother. It was raining! She laughed."), true);
    assert.deepEqual(
      e.spoken.map((u) => u.text),
      ["How I met your mother.", "It was raining!", "She laughed."],
    );
    assert.ok(e.spoken.every((u) => u.rate === m.STORY_RATE && u.voice?.name === "Karen" && u.lang === "en-AU"));
    assert.equal(e.voice.reading, "story");
    e.finish();
    e.finish();
    assert.equal(e.voice.reading, "story", "still reading till the last sentence ends");
    e.finish();
    assert.equal(e.voice.reading, null);
  });

  it("tells its listeners when it starts and stops", () => {
    const e = engine();
    const heard: (string | null)[] = [];
    e.voice.subscribe(() => heard.push(e.voice.reading));
    e.voice.read("One.");
    e.voice.stop();
    assert.deepEqual(heard, ["story", null]);
  });

  it("a new read cancels the one speaking, and its cancelled lines do not end the new one", () => {
    const e = engine();
    e.voice.read("First story. Two lines.");
    e.voice.read("Second story.");
    assert.equal(e.cancels(), 2);
    assert.equal(e.voice.reading, "story");
    e.finish();
    assert.equal(e.voice.reading, null);
  });

  it("says nothing when muted, and muting hushes it", () => {
    const e = engine([], mute(true));
    assert.equal(e.voice.read("Hello."), false);
    assert.equal(e.voice.caption("Hello."), false);
    assert.equal(e.spoken.length, 0);
    e.muted.set(false);
    e.voice.read("Hello.");
    const before = e.cancels();
    e.muted.set(true);
    assert.equal(e.cancels(), before + 1);
    assert.equal(e.voice.reading, null);
  });

  it("the story wins over captions", () => {
    const e = engine();
    e.voice.caption("A line.");
    assert.equal(e.voice.reading, "caption");
    e.voice.read("The story.");
    assert.equal(e.voice.reading, "story");
    assert.equal(e.voice.caption("Another line."), false, "a caption waits for the story");
    e.voice.stop("caption");
    assert.equal(e.voice.reading, "story", "stopping captions leaves the story");
    e.voice.stop("story");
    assert.equal(e.voice.reading, null);
  });

  it("nothing to say is not a read", () => {
    const e = engine();
    assert.equal(e.voice.read("   "), false);
    assert.equal(e.voice.caption(""), false);
    assert.equal(e.spoken.length, 0);
    assert.equal(e.cancels(), 0);
  });
});

describe("captionRate", () => {
  it("keeps the story's pace when there is time, and quickens (to a limit) when there is not", () => {
    assert.equal(m.captionRate("It was raining.", 6), m.STORY_RATE);
    const quick = m.captionRate("I handed them back, and she wouldn't even look at me.", 3.5);
    assert.ok(quick > m.STORY_RATE && quick <= 1.35, String(quick));
    assert.equal(m.captionRate("a b c d e f g h i j k l m n o p q r s t u v w", 1), 1.35);
    assert.equal(m.captionRate("The last line.", 0), m.STORY_RATE);
  });
});

describe("silentTelling", () => {
  it("speaks captions only without a recording", () => {
    assert.equal(m.silentTelling("none"), true);
    assert.equal(m.silentTelling("error"), true);
    assert.equal(m.silentTelling("ready"), false);
    assert.equal(m.silentTelling("blocked"), false);
  });
});

describe("CaptionVoice", () => {
  const track = {
    captions: [
      { t: 0.5, text: "It was raining." },
      { t: 4, text: "She dropped the books." },
      { t: 8, text: "I picked them up." },
    ],
  };
  type Status = import("./readAloud").CaptionStatus;
  const status = (over: Partial<Status> = {}): Status => ({
    playing: true,
    ended: false,
    audio: "none",
    narrator: "david",
    captionIndex: -1,
    ...over,
  });

  it("says each line of a silent telling once, as it is printed", () => {
    const e = engine();
    const c = new m.CaptionVoice(e.voice);
    c.update(status(), track, 12);
    assert.equal(e.spoken.length, 0, "no line yet");
    c.update(status({ captionIndex: 0 }), track, 12);
    c.update(status({ captionIndex: 0 }), track, 12);
    c.update(status({ captionIndex: 1 }), track, 12);
    assert.deepEqual(e.spoken.map((u) => u.text), ["It was raining.", "She dropped the books."]);
    assert.equal(e.cancels(), 2, "each line cancels the one before");
  });

  it("is quiet for a telling with its own recording", () => {
    const e = engine();
    const c = new m.CaptionVoice(e.voice);
    c.update(status({ captionIndex: 0, audio: "ready" }), track, 12);
    c.update(status({ captionIndex: 1, audio: "blocked" }), track, 12);
    assert.equal(e.spoken.length, 0);
  });

  it("hushes on a pause, lets the last line finish at the end, and says the lines again on a replay", () => {
    const e = engine();
    const c = new m.CaptionVoice(e.voice);
    c.update(status({ captionIndex: 0 }), track, 12);
    c.update(status({ captionIndex: 0, playing: false }), track, 12);
    assert.equal(e.voice.reading, null, "paused: hushed");
    c.update(status({ captionIndex: 0 }), track, 12);
    assert.equal(e.spoken.length, 2, "resumed: the line again");
    c.update(status({ captionIndex: 2 }), track, 12);
    c.update(status({ captionIndex: 2, playing: false, ended: true }), track, 12);
    assert.equal(e.voice.reading, "caption", "the last line finishes");
    c.update(status({ captionIndex: 2 }), track, 12);
    assert.equal(e.spoken.length, 4, "a replay says it again");
  });

  it("switching teller says the new teller's line", () => {
    const e = engine();
    const c = new m.CaptionVoice(e.voice);
    c.update(status({ captionIndex: 0 }), track, 12);
    c.update(status({ captionIndex: 0, narrator: "ana" }), track, 12);
    assert.equal(e.spoken.length, 2);
  });

  it("switching to a recorded teller hushes the spoken line", () => {
    const e = engine();
    const c = new m.CaptionVoice(e.voice);
    c.update(status({ captionIndex: 1 }), track, 12);
    c.update(status({ captionIndex: 0, narrator: "ana", audio: "ready" }), track, 12);
    assert.equal(e.voice.reading, null);
  });

  it("leaves the story alone, and dispose hushes only its own line", () => {
    const e = engine();
    const c = new m.CaptionVoice(e.voice);
    e.voice.read("The story.");
    c.update(status({ captionIndex: 0 }), track, 12);
    assert.equal(e.spoken.length, 1, "the caption waits for the story");
    c.dispose();
    assert.equal(e.voice.reading, "story");
  });
});
