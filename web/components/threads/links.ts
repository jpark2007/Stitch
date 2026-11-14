/**
 * Where the threads screen links to: pure, no DOM.
 *
 * The book pages and the tree are not under /families/<id>/ yet, so every
 * link says which family it belongs to: a real family as ?family=<id>, the
 * local demo family as ?demo=1 (those pages then read
 * components/tree/demoFamily.ts instead of asking the API). With neither,
 * links carry no scope and the pages pick the user's current family.
 */

import { hrefs } from "@/components/flow/session";

export interface LinkScope {
  /** A real family; wins over `demo`. */
  familyId?: string | null;
  /** The local demo family. */
  demo?: boolean;
}

const DEMO = "?demo=1";

/** A moment's spread. */
export function momentHref(id: string, scope: LinkScope): string {
  if (scope.familyId) return hrefs.memory(id, scope.familyId);
  return hrefs.memory(id) + (scope.demo ? DEMO : "");
}

/** A person's book. */
export function personHref(id: string, scope: LinkScope): string {
  if (scope.familyId) return hrefs.person(id, scope.familyId);
  return hrefs.person(id) + (scope.demo ? DEMO : "");
}

/** The family tree the "Family tree" crumb goes back to: the same demo family, or home. */
export function treeHref(scope: LinkScope): string {
  return !scope.familyId && scope.demo ? `${hrefs.home()}${DEMO}` : hrefs.home();
}
