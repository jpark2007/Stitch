/**
 * The demo Book of Family Traditions (?demo=1, and when Stitch is out of
 * reach), kept on the same family as every other demo screen: the tree's
 * "Alvarez–Moore family" (components/tree/demoFamily.ts). The five traditions
 * are the mock seed's (components/flow/mock/demoTraditions.ts), written for
 * its Riveras, so their people are mapped onto the tree's here.
 *
 * Pure, with type-only imports and its inputs passed in, so `node --test`
 * loads it directly (demoBook.test.ts).
 */

import type { DemoPerson } from "@/components/tree/demoFamily";
import type { DemoTraditionSeed } from "@/components/flow/mock/demoTraditions";
import type { Person, Tradition } from "@/lib/types";

/** The mock seed's person ids -> the demo tree's. */
export const DEMO_PERSON_IDS: Readonly<Record<string, string>> = {
  rosa: "rosa",
  luis: "luis",
  dad: "david",
  mom: "ana",
  maya: "sofia",
  sam: "mateo",
};

/** Stories that name a seed-only person, as the demo tree's family tells them. */
const DEMO_STORIES: Readonly<Record<string, string>> = {
  sunday_pancakes:
    "David makes pancakes every Sunday morning, always one shaped like the first letter of your name. " +
    "Mateo once asked for an M and got something closer to a mountain range. Nobody is allowed to skip breakfast.",
};

export interface DemoTraditionsInput {
  familyName: string;
  people: Person[];
  traditions: Tradition[];
}

/** The demo traditions on the demo tree's family and people, stamped with `now`. */
export function demoTraditionsBook(
  seeds: readonly DemoTraditionSeed[],
  family: { readonly id: string; readonly name: string },
  people: readonly DemoPerson[],
  now: Date = new Date(),
): DemoTraditionsInput {
  const at = now.toISOString();
  const known = new Set(people.map((p) => p.id));
  const traditions = seeds.map((s): Tradition => {
    const ids = (s.participantIds ?? []).map((id) => DEMO_PERSON_IDS[id] ?? id).filter((id) => known.has(id));
    return {
      ...s,
      description: DEMO_STORIES[s.id] ?? s.description,
      when: { ...s.when },
      participantIds: [...new Set(ids)],
      photoIds: [...(s.photoIds ?? [])],
      familyId: family.id,
      createdAt: at,
      updatedAt: at,
    };
  });
  return {
    familyName: family.name,
    people: people.map((p) => ({
      id: p.id,
      familyId: family.id,
      name: p.name,
      born: p.born,
      parentIds: [...p.parentIds],
      spouseIds: [...p.spouseIds],
      character: { ...p.character },
    })),
    traditions,
  };
}
