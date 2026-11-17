/**
 * Loading the Book of Family Traditions (/traditions): where it comes from,
 * as the person and moment books decide it (components/book/bookSource.ts),
 * and the book bound from it (traditionBook.ts).
 *
 *   ?demo=1                    the mock seed's five traditions (web/
 *                              components/flow/mock/demoTraditions.ts) on
 *                              the demo tree's family and people
 *                              (demoBook.ts), read-only
 *   otherwise                  GET /families/{fid}/traditions and the tree
 *                              (for the people who keep them), from
 *                              ?family=<fid>, else the family remembered in
 *                              this browser, else the user's first, through
 *                              the flow pages' client (getApi): in mock mode
 *                              the in-memory mock, which holds the same five
 *                              traditions once signed in.
 *   Stitch out of reach        the demo traditions, with a note
 *
 * Browser-only (it draws the snapshots onto canvases).
 */

import { resolveBook, type BookSession } from "@/components/book/bookSource";
import { loadPageFonts, type PageFonts } from "@/components/book/paperArt";
import { pickFamily, recallFamily, rememberFamily } from "@/components/flow/session";
import { DEMO_TRADITIONS } from "@/components/flow/mock/demoTraditions";
import { demoFamily, demoPeople } from "@/components/tree/demoFamily";
import { getApi } from "@/components/flow/client";
import { bareMediaId } from "@/lib/api";
import type { FamilySummary, Person, Tradition } from "@/lib/types";
import { demoTraditionsBook } from "./demoBook";
import { drawKeepersSnapshot } from "./snapshot";
import { traditionBook, type TraditionBook } from "./traditionBook";
import maoDemo from "./maoDemo.json";

/** Why the demo family is showing: chosen (?demo=1), or Stitch out of reach. */
export type TraditionsDemo = "chosen" | "offline";

/** What /traditions shows once it has loaded. */
export type TraditionsShown =
  | {
      kind: "book";
      /** Remounts the book when its pages change. */
      key: string;
      book: TraditionBook;
      /** The real family it belongs to; null for the demo. */
      familyId: string | null;
      /** The user's families, for the switcher (two or more). */
      families: FamilySummary[];
      /** A signed-in member of this family: "Add a tradition" is offered. */
      canAdd: boolean;
      demo: TraditionsDemo | null;
    }
  | { kind: "empty"; familyId: string; familyName: string; families: FamilySummary[]; canAdd: boolean }
  | { kind: "signed-out" }
  | { kind: "no-family" }
  | { kind: "not-found" }
  | { kind: "error"; detail: string };

export interface TraditionsScope {
  family: string | null;
  demo: boolean;
}

const session: BookSession = {
  pickFamily,
  recallFamily: () => recallFamily(),
  rememberFamily: (fid) => rememberFamily(fid),
};

/** A short, stable fingerprint of what the pages print, for the book's key. */
function fingerprint(parts: readonly string[]): string {
  let h = 2166136261;
  for (const s of parts) {
    for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
    h = Math.imul(h ^ 10, 16777619);
  }
  return (h >>> 0).toString(36);
}

function bookKey(scope: string, traditions: readonly Tradition[], people: readonly Person[]): string {
  return `${scope}:${fingerprint([
    ...traditions.map((t) => `${t.id}@${t.updatedAt}`),
    ...people.map((p) => `${p.id}:${p.name}:${p.character?.color ?? ""}`),
  ])}`;
}

/** Photo URL -> a blob: URL of it, fetched once per page life (the canvas needs same-origin pixels). */
const photoBlobs = new Map<string, Promise<string | null>>();

async function fetchPhotos(fid: string, ids: Iterable<string>): Promise<(mediaId: string) => string | null> {
  const urls = new Map<string, string | null>();
  await Promise.all(
    [...new Set(ids)].map(async (id) => {
      const src = getApi().mediaUrl(fid, bareMediaId(id));
      let got = photoBlobs.get(src);
      if (!got) {
        got = fetch(src, { credentials: "include" })
          .then(async (res) => (res.ok ? URL.createObjectURL(await res.blob()) : null))
          .catch(() => null);
        photoBlobs.set(src, got);
        got.then((u) => {
          if (u === null) photoBlobs.delete(src);
        });
      }
      urls.set(id, await got);
    }),
  );
  return (id) => urls.get(id) ?? null;
}

function bind(
  traditions: readonly Tradition[],
  people: readonly Person[],
  familyName: string | null,
  fonts: PageFonts,
  photoUrl?: (id: string) => string | null,
): TraditionBook {
  return traditionBook(traditions, people, {
    familyName,
    photoUrl,
    snapshot: (t, keepers) => drawKeepersSnapshot(t, keepers, fonts),
  });
}

/**
 * The demo family's traditions and people: the mock seed's five traditions
 * on the demo tree's family (demoBook.ts), so a visitor coming from the
 * demo tree meets the same people here.
 */
async function demoBook(why: TraditionsDemo, signal: AbortSignal): Promise<TraditionsShown> {
  const { familyName, people, traditions } = demoTraditionsBook(DEMO_TRADITIONS, demoFamily, demoPeople);
  const fonts = await loadPageFonts();
  if (signal.aborted) throw new DOMException("aborted", "AbortError");
  return {
    kind: "book",
    key: bookKey(`demo-${why}`, traditions, people),
    book: bind(traditions, people, familyName, fonts),
    familyId: null,
    families: [],
    canAdd: false,
    demo: why,
  };
}

interface FamilyData {
  traditions: Tradition[];
  people: Person[];
  familyName: string;
}

/** What /traditions shows, for its query. Rejects only when aborted. */
export async function loadTraditionsPage(scope: TraditionsScope, signal: AbortSignal): Promise<TraditionsShown> {
  if (process.env.NEXT_PUBLIC_STITCH_MAO_TRADITION_DEMO === "1" && scope.family === "macoskos") {
    const fonts = await loadPageFonts();
    if (signal.aborted) throw new DOMException("aborted", "AbortError");
    const traditions = maoDemo.traditions as Tradition[];
    const people = maoDemo.people as Person[];
    return {
      kind: "book",
      key: bookKey("mao-example", traditions, people),
      book: bind(traditions, people, "The Maos · example", fonts),
      familyId: null,
      families: [],
      canAdd: false,
      demo: "chosen",
    };
  }
  if (scope.demo) return demoBook("chosen", signal);
  // The user's families, for the switcher: /me is asked once by resolveBook.
  let families: FamilySummary[] = [];
  // The flow pages' client: the real API, or the in-memory mock in mock mode.
  const api = getApi();
  const me = async () => {
    const m = await api.me();
    families = Array.isArray(m?.families) ? m.families : [];
    return m;
  };
  const r = await resolveBook<FamilyData>(
    { demo: false, family: scope.family, mock: false },
    {
      api: { me },
      session,
      load: async (fid) => {
        const [list, tree] = await Promise.all([api.listTraditions(fid), api.getTree(fid)]);
        return {
          traditions: Array.isArray(list?.traditions) ? list.traditions : [],
          people: Array.isArray(tree?.people) ? tree.people : [],
          familyName: (tree?.familyName ?? "").trim(),
        };
      },
    },
  );
  if (signal.aborted) throw new DOMException("aborted", "AbortError");
  switch (r.kind) {
    case "unreachable":
      return demoBook("offline", signal);
    case "family": {
      const fid = r.familyId;
      const { traditions, people } = r.data;
      const familyName = r.data.familyName || r.family?.name || "Your family";
      const canAdd = r.family !== null;
      if (traditions.length === 0) return { kind: "empty", familyId: fid, familyName, families, canAdd };
      const [fonts, photoUrl] = await Promise.all([
        loadPageFonts(),
        fetchPhotos(fid, traditions.flatMap((t) => (t.photoIds ?? []).slice(0, 2))),
      ]);
      if (signal.aborted) throw new DOMException("aborted", "AbortError");
      return {
        kind: "book",
        key: bookKey(fid, traditions, people),
        book: bind(traditions, people, familyName, fonts, photoUrl),
        familyId: fid,
        families,
        canAdd,
        demo: null,
      };
    }
    case "demo":
      // resolveBook is asked with demo and mock off; kept for the type.
      return demoBook("chosen", signal);
    default:
      return r;
  }
}
