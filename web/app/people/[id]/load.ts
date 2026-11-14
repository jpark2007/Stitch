/**
 * Loading a person's book (/people/<id>): where it comes from
 * (components/book/bookSource.ts), their moments as spreads
 * (components/book/personBook.ts), and the scene that plays on each.
 *
 *   ?demo=1, or mock mode     the demo family's person; the church steps
 *                             (m-steps-1987) play the reference sample, as
 *                             on /memories/demo
 *   otherwise                 GET /families/{fid}/people/{id}/book, from
 *                             ?family=<fid>, else the family remembered in
 *                             this browser, else the user's first
 *   Stitch out of reach       the demo person of that id (else David), with
 *                             a note
 */

import { api, isMockMode } from "@/lib/api";
import type { BookResponse } from "@/lib/types";
import { resolveBook } from "@/components/book/bookSource";
import type { BookShown, DemoWhy } from "@/components/book/moment/MomentBook";
import { DEMO_PLAYING_MOMENT, demoPersonBook, personBook } from "@/components/book/personBook";
import { getDemoPerson, demoMomentsFor } from "@/components/tree/demoFamily";
import { sceneFromMemory, type SceneData } from "@/components/stage/play";
import { browserSession, fetchPhotos, loadDemoSample, photoIdsOf } from "@/app/memories/[id]/moment";

/** Who the demo book shows when Stitch is out of reach and the id is not a demo person. */
const DEMO_FALLBACK = "david";

async function demoPerson(id: string, why: DemoWhy, signal: AbortSignal): Promise<BookShown> {
  const plays = demoMomentsFor(id).some((m) => m.id === DEMO_PLAYING_MOMENT);
  const sample = plays ? await loadDemoSample(signal) : null;
  const book = demoPersonBook(id, sample?.spread);
  if (!book) return { kind: "not-found" };
  if (book.spreads.length === 0) return { kind: "empty", name: book.name, familyId: null, demo: true };
  const playing = book.spreads.find((s) => s.id === DEMO_PLAYING_MOMENT && s.stage);
  return {
    kind: "book",
    key: `demo:${id}`,
    spreads: book.spreads,
    cover: book.cover,
    scenes: sample && playing ? { [playing.id]: sample.scene } : {},
    demo: why,
  };
}

/** What /people/<id> shows, for its query. Rejects only when aborted. */
export async function loadPersonPage(
  id: string,
  scope: { family: string | null; demo: boolean },
  signal: AbortSignal,
): Promise<BookShown> {
  const r = await resolveBook<BookResponse>(
    { demo: scope.demo, family: scope.family, mock: isMockMode() },
    { api, session: browserSession, load: (fid) => api.getBook(fid, id) },
  );
  if (signal.aborted) throw new DOMException("aborted", "AbortError");
  switch (r.kind) {
    case "demo":
      return demoPerson(id, r.why, signal);
    case "unreachable":
      return demoPerson(getDemoPerson(id) ? id : DEMO_FALLBACK, "offline", signal);
    case "family": {
      const fid = r.familyId;
      const photoUrl = await fetchPhotos(fid, photoIdsOf(r.data.memories ?? []));
      if (signal.aborted) throw new DOMException("aborted", "AbortError");
      const book = personBook(r.data, { photoUrl, familyName: r.family?.name });
      if (book.spreads.length === 0) return { kind: "empty", name: book.name, familyId: fid, demo: false };
      const scenes: Record<string, SceneData> = {};
      for (const spread of book.spreads) {
        const slice = book.slices[spread.id];
        const scene = slice && spread.stage ? sceneFromMemory(slice) : null;
        if (scene) scenes[spread.id] = scene;
      }
      return { kind: "book", key: `${fid}:${id}`, spreads: book.spreads, cover: book.cover, scenes, demo: null };
    }
    default:
      return r;
  }
}
