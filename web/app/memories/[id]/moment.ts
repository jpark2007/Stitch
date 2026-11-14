/**
 * Loading a moment's book: its scene (compiled timeline and the sets, props
 * and people it names) and the spreads it is printed on. The mapping from a
 * memory to its spread is pure and tested (components/book/moment.ts), and
 * so is where the book comes from (components/book/bookSource.ts).
 *
 * Sources, first match wins:
 *   1. "demo": the hand-made demo book, its first spread (the same church
 *      steps story) playing the reference sample, so the pop-up can be seen
 *      rising and folding over real page turns. If the sample fails to
 *      load, the paper book opens without it.
 *   2. A local sample (web/public/samples/<id>.{screenplay,timeline,memory}.json).
 *   3. ?demo=1 (or mock mode): a moment of the demo family, as its own book;
 *      the church steps play the sample, as on the demo book.
 *   4. The saved moment from the API, from ?family=<fid>, else the family
 *      remembered in this browser, else the signed-in user's first. One with
 *      no compiled timeline still gets its spread, with no stage on it. When
 *      Stitch is out of reach, the demo moment of that id (or the demo book)
 *      opens instead, with a note.
 */

import { api, bareMediaId, isMockMode } from "@/lib/api";
import type { MemoryResponse, Screenplay, Timeline } from "@/lib/types";
import { pickFamily, recallFamily, rememberFamily } from "@/components/flow/session";
import { resolveBook, type BookSession } from "@/components/book/bookSource";
import { demoCover, demoSpreads } from "@/components/book/demoSpreads";
import { momentCover, momentEnd, momentSpread } from "@/components/book/moment";
import { DEMO_PLAYING_MOMENT, demoMomentBook } from "@/components/book/personBook";
import type { BookShown, DemoWhy } from "@/components/book/moment/MomentBook";
import type { BookSpread } from "@/components/book/types";
import { SAMPLE_SCENES, sceneFromMemory, withPageText, type SceneData } from "@/components/stage/play";

/** The sample the demo book's church steps play. */
export const DEMO_SAMPLE = "hymn_books";

export const isSampleMoment = (id: string) => Object.hasOwn(SAMPLE_SCENES, id);
export const isDemoBook = (id: string) => id === "demo";

/** This browser's session helpers, for bookSource. */
export const browserSession: BookSession = {
  pickFamily,
  recallFamily: () => recallFamily(),
  rememberFamily: (fid) => rememberFamily(fid),
};

/** Photo URL -> a blob: URL of it, fetched once per page life. */
const photoBlobs = new Map<string, Promise<string | null>>();

/**
 * The family's telling photos, ready to print. A photo is media behind the
 * session cookie, and the story pages draw it to a canvas (an anonymous
 * CORS image, which carries no cookie), so it is fetched here with the
 * cookie and handed over as a same-origin blob: URL. Ids that fail are
 * left out (the page then tapes in the next photo, or none).
 */
export async function fetchPhotos(fid: string, ids: Iterable<string>): Promise<(mediaId: string) => string | null> {
  const wanted = [...new Set(ids)];
  const urls = new Map<string, string | null>();
  await Promise.all(
    wanted.map(async (id) => {
      const src = api.mediaUrl(fid, bareMediaId(id));
      let got = photoBlobs.get(src);
      if (!got) {
        got = fetch(src, { credentials: "include" })
          .then(async (res) => (res.ok ? URL.createObjectURL(await res.blob()) : null))
          .catch(() => null);
        photoBlobs.set(src, got);
        // A failure may be passing (offline): ask again next time.
        got.then((u) => {
          if (u === null) photoBlobs.delete(src);
        });
      }
      urls.set(id, await got);
    }),
  );
  return (id) => urls.get(id) ?? null;
}

/** The photo ids a book of these moments may print (each moment tapes in at most two). */
export function photoIdsOf(memories: readonly MemoryResponse["memory"][]): string[] {
  return memories.flatMap((m) => [...new Set((m.tellings ?? []).flatMap((t) => t.photoIds ?? []))].slice(0, 4));
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** A sample's memory, its scene, and the spread it plays on (its timeline is a separate file, page text from the screenplay). */
export async function loadSample(name: string, signal?: AbortSignal) {
  const base = SAMPLE_SCENES[name];
  const [screenplay, timeline, res] = await Promise.all([
    getJson<Screenplay>(`${base}.screenplay.json`, signal),
    getJson<Timeline>(`${base}.timeline.json`, signal),
    getJson<MemoryResponse>(`${base}.memory.json`, signal),
  ]);
  const scene = sceneFromMemory(res, withPageText(timeline, screenplay));
  // A sample's media are not served: it plays as silent timing.
  return {
    res,
    scene: scene && { ...scene, familyId: null },
    spread: momentSpread(res, { timeline: scene?.timeline, screenplayShots: screenplay.shots }),
  };
}

/**
 * The sample that plays on the demo family's church steps, or null when it
 * does not load (the paper book opens without it).
 */
export async function loadDemoSample(signal?: AbortSignal): Promise<{ scene: SceneData; spread: BookSpread } | null> {
  try {
    const { scene, spread } = await loadSample(DEMO_SAMPLE, signal);
    if (!scene) throw new Error("the sample has no timeline");
    return { scene, spread };
  } catch (e) {
    if (signal?.aborted) throw e;
    console.warn("[book] demo: the sample scene did not load; opening the paper book", e);
    return null;
  }
}

/** The demo book (/memories/demo); its church steps play the sample when it loads. */
async function demoBook(why: DemoWhy | null, signal?: AbortSignal): Promise<BookShown> {
  const sample = await loadDemoSample(signal);
  if (!sample) return { kind: "book", key: "demo", spreads: demoSpreads, cover: demoCover, scenes: {}, demo: why };
  // The demo's own words and photos, with the sample's labels and stage.
  const first: BookSpread = { ...demoSpreads[0], tellings: sample.spread.tellings, stage: sample.spread.stage };
  return {
    kind: "book",
    key: "demo",
    spreads: [first, ...demoSpreads.slice(1)],
    cover: demoCover,
    scenes: { [first.id]: sample.scene },
    demo: why,
  };
}

/** One moment's book: its spread, then a closing spread to turn to (and back from). */
function oneMoment(key: string, scene: SceneData | null, spread: BookSpread, demo: DemoWhy | null): BookShown {
  return {
    kind: "book",
    key,
    spreads: [spread, momentEnd(spread)],
    cover: momentCover(spread),
    scenes: scene && spread.stage ? { [spread.id]: scene } : {},
    demo,
  };
}

/** A demo family moment as its own book, or null when the demo family has no such moment. */
async function demoMoment(id: string, why: DemoWhy, signal?: AbortSignal): Promise<BookShown | null> {
  if (!demoMomentBook(id)) return null;
  const sample = id === DEMO_PLAYING_MOMENT ? await loadDemoSample(signal) : null;
  const book = demoMomentBook(id, sample?.spread)!;
  return {
    kind: "book",
    key: `demo:${id}`,
    spreads: book.spreads,
    cover: book.cover,
    scenes: sample && book.spread.stage ? { [book.spread.id]: sample.scene } : {},
    demo: why,
  };
}

/** What /memories/<id> shows, for its query. Rejects only when aborted. */
export async function loadMomentPage(
  id: string,
  scope: { family: string | null; demo: boolean },
  signal: AbortSignal,
): Promise<BookShown> {
  if (isDemoBook(id)) return demoBook(null, signal);
  if (isSampleMoment(id)) {
    try {
      const { scene, spread } = await loadSample(id, signal);
      return oneMoment(id, scene, spread, null);
    } catch (e) {
      if (signal.aborted) throw e;
      console.warn(`[book] ${id}: the sample did not load`, e);
      return { kind: "error", detail: "This sample did not load. Please try again." };
    }
  }

  const r = await resolveBook<MemoryResponse>(
    { demo: scope.demo, family: scope.family, mock: isMockMode() },
    { api, session: browserSession, load: (fid) => api.getMemory(fid, id) },
  );
  if (signal.aborted) throw new DOMException("aborted", "AbortError");
  switch (r.kind) {
    case "demo":
      return (await demoMoment(id, r.why, signal)) ?? { kind: "not-found" };
    case "unreachable":
      return (await demoMoment(id, "offline", signal)) ?? demoBook("offline", signal);
    case "family": {
      const res = r.data;
      const fid = r.familyId;
      const scene = sceneFromMemory(res);
      const photoUrl = await fetchPhotos(fid, photoIdsOf([res.memory]));
      if (signal.aborted) throw new DOMException("aborted", "AbortError");
      const spread = momentSpread(res, { photoUrl });
      return oneMoment(`${fid}:${id}`, scene, spread, null);
    }
    default:
      return r;
  }
}
