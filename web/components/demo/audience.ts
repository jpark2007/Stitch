// Where the demo control page (/demo) can send the audience window, and how
// it names what that window is showing. Every family page takes `?family=`;
// the tree home (/) shows the remembered family, which /demo sets to the
// family it controls, so `?family=` there is only a hint.
//
// No runtime imports and only erasable TypeScript, so `node --test` loads this
// file directly. Tests: components/demo/__tests__/audience.test.mjs.

/** The window name every audience action targets, so it reuses one window (even after a reload of /demo). */
export const AUDIENCE_WINDOW = "stitch-audience";

/** The calm holding view: the paper and the Stitch wordmark, nothing else. */
export const HOLD_PATH = "/demo/hold";

const enc = encodeURIComponent;

function withFamily(path: string, fid: string, extra: Array<[string, string]> = []): string {
  const params = [`family=${enc(fid)}`, ...extra.map(([k, v]) => `${k}=${enc(v)}`)];
  return `${path}?${params.join("&")}`;
}

export const audienceHrefs = {
  /** The family tree (home). */
  home: (fid: string): string => withFamily("/", fid),
  threads: (fid: string): string => withFamily("/threads", fid),
  ask: (fid: string): string => withFamily("/ask", fid),
  films: (fid: string): string => withFamily("/films", fid),
  traditions: (fid: string): string => withFamily("/traditions", fid),
  /** A person's book, lying open. */
  person: (fid: string, personId: string): string =>
    withFamily(`/people/${enc(personId)}`, fid, [["state", "open"]]),
  /** A moment, in a book that holds it, opened at its spread (`?memory=`). */
  moment: (fid: string, memoryId: string, personId: string | null): string =>
    personId
      ? withFamily(`/people/${enc(personId)}`, fid, [["memory", memoryId]])
      : withFamily(`/memories/${enc(memoryId)}`, fid),
  film: (fid: string, filmId: string): string => withFamily(`/films/${enc(filmId)}`, fid),
  /** Add a memory with the prepared telling the control page just wrote (DEMO_DRAFT_KEY). */
  addDraft: (fid: string): string => withFamily("/add", fid, [["demoDraft", "1"]]),
  hold: (): string => HOLD_PATH,
};

/** The quick destinations, in the order the control page lists them. */
export type QuickDestination = "home" | "threads" | "ask" | "films" | "traditions";

export const QUICK_DESTINATIONS: ReadonlyArray<{ key: QuickDestination; label: string }> = [
  { key: "home", label: "Family tree" },
  { key: "threads", label: "Threads" },
  { key: "ask", label: "Ask" },
  { key: "films", label: "Films" },
  { key: "traditions", label: "Traditions" },
];

/** A moment's book: its first teller when they are in it, else its first participant, else none. */
export function momentBookPerson(m: { participantIds: string[]; tellingAuthorIds?: string[] }): string | null {
  const teller = m.tellingAuthorIds?.find((id) => typeof id === "string" && id !== "");
  if (teller) return teller;
  return m.participantIds.find((id) => typeof id === "string" && id !== "") ?? null;
}

/**
 * What the audience window shows, in a few words, from its path and query
 * ("Family tree", "Rosa's book", "Holding screen"). `names` maps person ids
 * to names. Unknown paths come back as the path itself.
 */
export function describeAudience(location: string, names: Readonly<Record<string, string>> = {}): string {
  if (location.startsWith("about:")) return "Opening…";
  let url: URL;
  try {
    url = new URL(location, "http://stitch.invalid");
  } catch {
    return location;
  }
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const q = url.searchParams;
  if (path === "/") return "Family tree";
  if (path === HOLD_PATH) return "Holding screen";
  if (path === "/threads") return "Threads";
  if (path === "/ask") return "Ask the family";
  if (path === "/films") return "Films";
  if (path === "/traditions") return "Traditions";
  if (path === "/add") return q.get("run") ? "Add a memory: the studio at work" : "Add a memory";
  const person = /^\/people\/([^/]+)$/.exec(path);
  if (person) {
    const id = safeDecode(person[1]);
    const name = names[id];
    const whose = name ? `${name}’s book` : "A book";
    return q.get("memory") ? `${whose}, at a moment` : whose;
  }
  if (/^\/memories\/[^/]+$/.test(path)) return "A moment";
  if (/^\/films\/[^/]+$/.test(path)) return "A film";
  return path;
}

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}
