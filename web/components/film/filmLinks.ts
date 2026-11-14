// Where the film pages live. Ids are URL-encoded; `?family=` tells each page
// which family to load (as on /add and /ask).
//
// No imports, so `node --test` loads this file directly.

const enc = encodeURIComponent;

function familyQuery(familyId: string | null | undefined): string {
  return typeof familyId === "string" && familyId !== "" ? `?family=${enc(familyId)}` : "";
}

/** The family's films. */
export function filmsHref(familyId?: string | null): string {
  return `/films${familyQuery(familyId)}`;
}

/** The form for making a film. */
export function newFilmHref(familyId?: string | null): string {
  return `/films/new${familyQuery(familyId)}`;
}

/** One film: its player, or its waiting sheet while it is made. */
export function filmHref(id: string, familyId?: string | null): string {
  return `/films/${enc(id)}${familyQuery(familyId)}`;
}

/** `?family=` from a query string or URLSearchParams; null when missing or empty. */
export function familyFromQuery(search: string | URLSearchParams | null | undefined): string | null {
  if (search === null || search === undefined) return null;
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const v = params.get("family");
  return v ? v : null;
}
