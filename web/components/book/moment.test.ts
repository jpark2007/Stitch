/**
 * A moment as a spread: tellers, page text, pull tabs, cover and the shared
 * link's teller, against the reference sample
 * (web/public/samples/hymn_books.*.json).
 *
 *   cd web && node --no-warnings --test "components/book/*.test.ts"
 *
 * The module is imported by a computed path so tsc does not ask for a `.ts`
 * import extension (see leafShape.test.ts).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import type { Interaction, MemoryResponse, Screenplay, Timeline } from "@/lib/types";

type Moment = typeof import("./moment");
const paths = ["./moment.ts"];
const m: Moment = await import(paths[0]);

const sample = (suffix: string) =>
  JSON.parse(readFileSync(new URL(`../../public/samples/hymn_books.${suffix}.json`, import.meta.url), "utf8"));
const memory: MemoryResponse = sample("memory");
const timeline: Timeline = sample("timeline");
const screenplay: Screenplay = sample("screenplay");
const people = memory.people ?? [];
const tellings = memory.memory.tellings ?? [];

describe("momentSpread (hymn_books)", () => {
  const spread = m.momentSpread(memory, { timeline, screenplayShots: screenplay.shots });

  it("prints the shots' page text", () => {
    assert.equal(spread.text, "One Sunday in 1988, a stack of hymn books changed everything.");
  });

  it("tapes a label per telling, default narrator first, with their own words", () => {
    assert.deepEqual(
      spread.tellings?.map((t) => [t.id, t.name, t.color]),
      [
        ["dad", "David", "#3f6fb5"],
        ["mom", "Ana", "#c0504d"],
      ],
    );
    assert.deepEqual(spread.tellers, ["David", "Ana"]);
    assert.equal(spread.tellings?.[1].text, tellings.find((t) => t.authorId === "mom")?.text.trim());
  });

  it("cuts a slit for the pull tab on the stage page", () => {
    assert.deepEqual(spread.stage, { pullTabs: [{ id: "drop_books", label: "Pull to drop the books", at: [0.85, 0.82] }] });
  });

  it("leaves out photos it cannot show", () => {
    assert.deepEqual(spread.photos, []);
  });

  it("tapes in at most two photos, with URLs", () => {
    const withPhotos: MemoryResponse = {
      ...memory,
      memory: {
        ...memory.memory,
        tellings: tellings.map((t, i) => ({ ...t, photoIds: i ? ["media/b", "media/c"] : ["media/a", "media/b"] })),
      },
    };
    const s = m.momentSpread(withPhotos, { timeline, photoUrl: (id) => `/media/${id}` });
    assert.deepEqual(
      s.photos.map((p) => [p.id, p.src]),
      [
        ["media/a", "/media/media/a"],
        ["media/b", "/media/media/b"],
      ],
    );
  });

  it("has no stage without a timeline", () => {
    const bare: MemoryResponse = { ...memory, memory: { ...memory.memory, timeline: null } };
    const s = m.momentSpread(bare);
    assert.equal(s.stage, undefined);
    // Still a label per telling, from the tellings themselves.
    assert.deepEqual(s.tellings?.map((t) => t.id), ["dad", "mom"]);
  });
});

describe("momentPageText", () => {
  it("falls back to the screenplay's page text", () => {
    const bare = (timeline.shots ?? []).map((s) => ({ ...s, pageText: null }));
    assert.equal(m.momentPageText(bare, [{ pageText: "From the screenplay." }]), "From the screenplay.");
  });

  it("is empty with no page text anywhere, so the page prints the teller's words", () => {
    assert.equal(m.momentPageText([{ pageText: "  " }], []), "");
    assert.equal(m.momentPageText(null), "");
  });

  it("joins several shots in order", () => {
    assert.equal(m.momentPageText([{ pageText: "One." }, { pageText: null }, { pageText: " Two. " }]), "One. Two.");
  });
});

describe("momentTellers", () => {
  it("uses the tellings when there is no timeline", () => {
    assert.deepEqual(
      m.momentTellers(null, people, tellings).map((t) => t.id),
      ["dad", "mom"],
    );
  });

  it("puts the default narrator first", () => {
    assert.deepEqual(
      m.momentTellers({ ...timeline, defaultNarrator: "mom" }, people).map((t) => t.id),
      ["mom", "dad"],
    );
  });

  it("names an unknown person by id, in the fallback thread", () => {
    const [t] = m.momentTellers({ narrations: { zed: timeline.narrations!.dad }, defaultNarrator: null }, people);
    assert.deepEqual([t.id, t.name, t.color], ["zed", "zed", "#b9573d"]);
  });
});

describe("momentPullTabs", () => {
  const tab = timeline.interactions?.[0] as Interaction;

  it("clamps a tab onto the page, and places one with no position at the foot", () => {
    const tabs = m.momentPullTabs([
      { ...tab, id: "a", at: [1.4, -0.2] },
      { ...tab, id: "b", at: [] },
      { ...tab, id: "c", at: [Number.NaN, 0.5] },
    ]);
    assert.deepEqual(
      tabs.map((t) => t.at),
      [
        [1, 0],
        [0.85, 0.82],
        [0.85, 0.5],
      ],
    );
  });

  it("leaves out story-page interactions", () => {
    assert.deepEqual(m.momentPullTabs([{ ...tab, page: "story" }]), []);
    assert.deepEqual(m.momentPullTabs(null), []);
  });
});

describe("cover, closing page and names", () => {
  it("joins names like a person would", () => {
    assert.equal(m.joinNames(["Rosa", "Ana", "David"]), "Rosa, Ana & David");
    assert.equal(m.joinNames(["David", "Ana"]), "David & Ana");
    assert.equal(m.joinNames(["Ana"]), "Ana");
    assert.equal(m.joinNames([]), "");
  });

  it("titles a one-moment book with the moment", () => {
    const c = m.momentCover(m.momentSpread(memory, { timeline }));
    assert.deepEqual(c, { kicker: "A moment from 1988", title: "How I met your mother", subtitle: "as told by David & Ana" });
  });

  it("closes the book with the tellers", () => {
    const spread = m.momentSpread(memory, { timeline });
    const end = m.momentEnd(spread);
    assert.equal(end.kind, "end");
    assert.equal(end.text, "as David & Ana remember it");
    assert.equal(m.momentEnd({ ...spread, tellers: ["Ana"] }).text, "as Ana remembers it");
  });
});

describe("pickTeller (?narrator)", () => {
  const tellers = m.momentTellers(timeline, people);

  it("takes a person id or name, in any case", () => {
    assert.equal(m.pickTeller(tellers, "mom"), "mom");
    assert.equal(m.pickTeller(tellers, "ana"), "mom");
    assert.equal(m.pickTeller(tellers, " David "), "dad");
    assert.equal(m.pickTeller(tellers, "MOM"), "mom");
  });

  it("is null for no one it knows", () => {
    assert.equal(m.pickTeller(tellers, "rosa"), null);
    assert.equal(m.pickTeller(tellers, ""), null);
    assert.equal(m.pickTeller(tellers, null), null);
  });
});

/* A book's players (moment/tellings.ts): one per spread with a scene. */

type Tellings = typeof import("./moment/tellings");
type ControllerModule = typeof import("@/components/stage/playback/controller");
type SamplerModule = typeof import("@/components/stage/playback/sampler");
const tl: Tellings = await import(new URL("./moment/tellings.ts", import.meta.url).href);
const { PlaybackController } = (await import(
  new URL("../stage/playback/controller.ts", import.meta.url).href
)) as ControllerModule;
const { compileTimeline } = (await import(new URL("../stage/playback/sampler.ts", import.meta.url).href)) as SamplerModule;

class FakeAudio {
  src = "";
  playbackRate = 1;
  paused = true;
  duration = Number.NaN;
  currentTime = 0;
  muted = false;
  play() {
    this.paused = false;
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
  }
  load() {}
  removeAttribute(name: string) {
    if (name === "src") this.src = "";
  }
}

describe("ScenePlayers", () => {
  // Four spreads: the first is paper only, three play the sample (dad and mom tell it).
  const order = ["paper", "a", "b", "c", "end"];
  const timelines = { a: timeline, b: timeline, c: timeline };
  const compiled = compileTimeline(timeline);

  function setup(o: { start?: Record<string, string | null>; audio?: boolean } = {}) {
    let wall = 1000;
    const made: string[] = [];
    const audios: FakeAudio[] = [];
    let muted = false;
    const mutedListeners = new Set<(m: boolean) => void>();
    const players = new tl.ScenePlayers({
      order,
      timelines,
      start: o.start,
      create: (id, narrator) => {
        made.push(`${id}:${narrator ?? "-"}`);
        return new PlaybackController(compiled, { narrator, now: () => wall, resolveAudio: (mid) => `https://x/${mid}` });
      },
      createAudio: o.audio
        ? () => {
            const a = new FakeAudio();
            audios.push(a);
            return a;
          }
        : undefined,
      muted: {
        get: () => muted,
        subscribe: (l) => {
          mutedListeners.add(l);
          return () => mutedListeners.delete(l);
        },
      },
    });
    const mute = (m: boolean) => {
      muted = m;
      for (const l of mutedListeners) l(m);
    };
    return { players, made, audios, mute, advance: (ms: number) => (wall += ms) };
  }

  it("makes the open spread's player and its neighbours', not far ones", () => {
    const { players } = setup();
    players.setOpen("a");
    assert.deepEqual(players.liveIds, ["a", "b"]);
    players.setOpen("c");
    assert.deepEqual(players.liveIds, ["b", "c"]);
  });

  it("a paper spread has no player", () => {
    const { players } = setup();
    assert.equal(players.player("paper"), null);
    assert.equal(players.player("end"), null);
  });

  it("only the open spread may play: turning the page pauses the old one", () => {
    const { players } = setup();
    players.setOpen("a");
    const a = players.player("a")!;
    a.play();
    assert.equal(a.getStatus().playing, true);
    players.setOpen("b");
    assert.equal(a.getStatus().playing, false);
  });

  it("a stage that still stands keeps its player while the book moves on", () => {
    const { players } = setup();
    players.setOpen("a");
    const a = players.player("a")!;
    const release = players.retain("a");
    players.setOpen("c");
    assert.deepEqual(players.liveIds, ["a", "b", "c"]);
    assert.equal(players.player("a"), a);
    release();
    assert.deepEqual(players.liveIds, ["b", "c"]);
    release(); // twice is harmless
  });

  it("each spread keeps its own telling", () => {
    const { players } = setup();
    players.setOpen("b");
    players.tell("b", "mom");
    assert.equal(players.player("b")!.getStatus().narrator, "mom");
    // Back to spread a: it replays its own telling, not b's.
    players.setOpen("a");
    assert.equal(players.player("a")!.getStatus().narrator, "dad");
    assert.deepEqual(players.get(), { a: "dad", b: "mom", c: "dad" });
  });

  it("a spread let go starts again with the telling last chosen there", () => {
    const { players, made } = setup();
    players.setOpen("a");
    players.tell("a", "mom");
    players.setOpen("c"); // a is let go
    assert.ok(!players.liveIds.includes("a"));
    assert.equal(players.get().a, "mom");
    players.setOpen("a");
    assert.equal(players.player("a")!.getStatus().narrator, "mom");
    assert.ok(made.includes("a:mom"));
  });

  it("a shared link's teller starts its spread; one who is not a teller there is ignored", () => {
    const { players } = setup({ start: { b: "mom", c: "rosa" } });
    assert.deepEqual(players.get(), { a: "dad", b: "mom", c: "dad" });
    players.setOpen("c");
    assert.equal(players.player("b")!.getStatus().narrator, "mom");
    assert.equal(players.player("c")!.getStatus().narrator, "dad");
  });

  it("choosing the telling already playing hears it again from the start", () => {
    const { players, advance } = setup();
    players.setOpen("a");
    const a = players.player("a")!;
    a.play();
    advance(3000);
    a.tick();
    assert.ok(a.time > 2);
    players.tell("a", "dad");
    assert.equal(a.time, 0);
    assert.equal(a.getStatus().playing, true);
  });

  it("tells the book only when a spread's teller changes, and keeps the same object otherwise", () => {
    const { players, advance } = setup();
    let heard = 0;
    players.subscribe(() => heard++);
    players.setOpen("a");
    const before = players.get();
    const a = players.player("a")!;
    a.play();
    advance(500);
    a.tick();
    assert.equal(heard, 0);
    assert.equal(players.get(), before);
    players.tell("a", "mom");
    assert.equal(heard, 1);
    assert.equal(players.get().a, "mom");
  });

  it("an ended scene shows no telling playing", () => {
    const { players, advance } = setup();
    players.setOpen("a");
    const a = players.player("a")!;
    a.play();
    advance((a.duration + 1) * 1000);
    a.tick();
    assert.equal(a.getStatus().ended, true);
    assert.equal(players.get().a, undefined);
  });

  it("narration elements while shown, muted with the book; deactivating pauses and lets them go", () => {
    const { players, audios, mute } = setup({ audio: true });
    players.setOpen("a");
    assert.equal(audios.length, 0, "no elements before the page is shown");
    const off = players.activate();
    assert.equal(audios.length, 2);
    mute(true);
    assert.ok(audios.every((el) => el.muted));
    players.setOpen("b"); // c joins, muted from the start
    assert.equal(audios.length, 3);
    assert.equal(audios[2].muted, true);
    const b = players.player("b")!;
    b.play();
    off();
    assert.equal(b.getStatus().playing, false);
    assert.equal(b.getStatus().audio, "none");
    // Shown again: fresh elements for a, b and c.
    players.activate();
    assert.equal(audios.length, 6);
  });

  it("disposing lets go of everything", () => {
    const { players } = setup();
    players.setOpen("b");
    players.dispose();
    assert.deepEqual(players.liveIds, []);
  });
});

describe("timelineTellers", () => {
  it("default narrator first, as the player orders them", () => {
    assert.deepEqual(tl.timelineTellers(timeline), ["dad", "mom"]);
    assert.deepEqual(tl.timelineTellers({ ...timeline, defaultNarrator: "mom" }), ["mom", "dad"]);
    assert.deepEqual(tl.timelineTellers({ narrations: null, captions: [], audio: null }), []);
    assert.deepEqual(tl.timelineTellers({ narrations: null, captions: [{ t: 0, text: "hi" }] }), ["narrator"]);
  });
});
