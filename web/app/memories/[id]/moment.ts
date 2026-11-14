/**
 * Loading a moment's book: its scene (compiled timeline and the sets, props
 * and people it names) and the spreads it is printed on. The mapping from a
 * memory to its spread is pure and tested (components/book/moment.ts).
 *
 * Sources, first match wins:
 *   1. "demo": the hand-made demo book, its first spread (the same church
 *      steps story) playing the reference sample, so the pop-up can be seen
 *      rising and folding over real page turns. If the sample fails to
 *      load, the paper book opens without it.
 *   2. A local sample (web/public/samples/<id>.{screenplay,timeline,memory}.json).
 *   3. With a family id (?family=<fid>): the saved moment from the API. One
 *      with no compiled timeline still gets its spread, with no stage on it.
 */

import { api, bareMediaId } from "@/lib/api";
import type { MemoryResponse, Screenplay, Timeline } from "@/lib/types";
import { SAMPLE_SCENES, sceneFromMemory, withPageText, type SceneData } from "@/components/stage/play";
import { demoCover, demoSpreads } from "@/components/book/demoSpreads";
import { momentCover, momentEnd, momentSpread } from "@/components/book/moment";
import type { BookCover, BookSpread } from "@/components/book/types";

export interface MomentBookData {
  /** The scene that plays, or null (nothing compiled yet, or the sample did not load). */
  scene: SceneData | null;
  spreads: BookSpread[];
  cover: BookCover;
  /** The spread the scene plays on (one of `spreads`), when there is a scene. */
  playing: BookSpread | null;
}

/** The sample the demo book's first spread plays. */
const DEMO_SAMPLE = "hymn_books";

export const isSampleMoment = (id: string) => Object.hasOwn(SAMPLE_SCENES, id);
export const isDemoBook = (id: string) => id === "demo";

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** A sample's memory and scene (its timeline is a separate file, page text from the screenplay). */
async function loadSample(name: string, signal?: AbortSignal) {
  const base = SAMPLE_SCENES[name];
  const [screenplay, timeline, res] = await Promise.all([
    getJson<Screenplay>(`${base}.screenplay.json`, signal),
    getJson<Timeline>(`${base}.timeline.json`, signal),
    getJson<MemoryResponse>(`${base}.memory.json`, signal),
  ]);
  const scene = sceneFromMemory(res, withPageText(timeline, screenplay));
  // A sample's media are not served: it plays as silent timing.
  return { res, screenplay, scene: scene && { ...scene, familyId: null } };
}

/** The demo book; its first spread plays the sample when it loads. */
async function loadDemo(signal?: AbortSignal): Promise<MomentBookData> {
  try {
    const { res, screenplay, scene } = await loadSample(DEMO_SAMPLE, signal);
    if (!scene) throw new Error("the sample has no timeline");
    const played = momentSpread(res, { timeline: scene.timeline, screenplayShots: screenplay.shots });
    // The demo's own words and photos, with the sample's labels and stage.
    const first: BookSpread = { ...demoSpreads[0], tellings: played.tellings, stage: played.stage };
    return { scene, spreads: [first, ...demoSpreads.slice(1)], cover: demoCover, playing: first };
  } catch (e) {
    if (signal?.aborted) throw e;
    console.warn("[book] demo: the sample scene did not load; opening the paper book", e);
    return { scene: null, spreads: demoSpreads, cover: demoCover, playing: null };
  }
}

/** One moment's book: its spread, then a closing spread to turn to (and back from). */
function oneMoment(res: MemoryResponse, scene: SceneData | null, spread: BookSpread): MomentBookData {
  return { scene, spreads: [spread, momentEnd(spread)], cover: momentCover(spread), playing: scene ? spread : null };
}

/** Loads a book by route id: the demo, a sample, or a saved moment when a family id is given. */
export async function loadMomentBook(id: string, familyId: string | null, signal?: AbortSignal): Promise<MomentBookData> {
  if (isDemoBook(id)) return loadDemo(signal);
  if (isSampleMoment(id)) {
    const { res, screenplay, scene } = await loadSample(id, signal);
    return oneMoment(res, scene, momentSpread(res, { timeline: scene?.timeline, screenplayShots: screenplay.shots }));
  }
  if (!familyId) throw new Error("no family");
  const res = await api.getMemory(familyId, id);
  const scene = sceneFromMemory(res);
  const spread = momentSpread(res, { photoUrl: (pid) => api.mediaUrl(familyId, bareMediaId(pid)) });
  return oneMoment(res, scene, spread);
}
