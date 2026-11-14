// The demo family's traditions (the Riveras, see ./demoSeed.ts) for the Book of
// Family Traditions. The backend's demo seed mirrors this list exactly: same
// ids, text, people and years. Person ids are demoSeed's (rosa, luis, dad,
// mom, maya, sam); createdBy is a demo user id. No photos.
//
// No runtime imports and only erasable TypeScript, so `node --test` can load
// this file directly. ./index.ts adds these to the mock seed.
import type { Tradition } from "@/lib/types";

/** A demo tradition without its family id and timestamps (buildDemoTraditions adds them). */
export type DemoTraditionSeed = Omit<Tradition, "familyId" | "createdAt" | "updatedAt">;

export const DEMO_TRADITIONS: readonly DemoTraditionSeed[] = [
  {
    id: "tamales_christmas_eve",
    title: "Tamales on Christmas Eve",
    description:
      "Every Christmas Eve the whole kitchen turns into an assembly line. Rosa spreads the masa, Luis folds, " +
      "and the kids are in charge of the corn husks until they're old enough to be trusted with the filling. " +
      "We eat the first batch standing up, straight out of the pot.",
    category: "food",
    when: { kind: "yearly", label: "Christmas Eve", month: 12 },
    participantIds: ["rosa", "luis", "dad", "mom", "maya", "sam"],
    photoIds: [],
    startedYear: 1962,
    createdBy: "user_david",
  },
  {
    id: "sunday_pancakes",
    title: "Sunday pancakes",
    description:
      "David makes pancakes every Sunday morning, always one shaped like the first letter of your name. " +
      "Sam once asked for an S and got something closer to a snake. Nobody is allowed to skip breakfast.",
    category: "food",
    when: { kind: "weekly", label: "every Sunday" },
    participantIds: ["dad", "mom", "maya", "sam"],
    photoIds: [],
    startedYear: 1995,
    createdBy: "user_ana",
  },
  {
    id: "summer_road_trip",
    title: "The summer road trip to the coast",
    description:
      "Each July we pack the car the night before and leave before the sun is up. " +
      "The first stop is always the same diner for cinnamon rolls, and whoever spots the ocean first picks the music for the rest of the drive.",
    category: "trip",
    when: { kind: "yearly", label: "every July", month: 7 },
    participantIds: ["dad", "mom", "maya", "sam"],
    photoIds: [],
    startedYear: 1997,
    createdBy: "user_david",
  },
  {
    id: "hymns_after_dinner",
    title: "Hymns after dinner",
    description:
      "When the family is all at one table, someone starts humming and Rosa takes it from there. " +
      "Ana carries the harmony, David pretends he doesn't know the words, and by the second verse everyone is singing.",
    category: "music",
    when: { kind: "occasional", label: "when everyone is home" },
    participantIds: ["rosa", "dad", "mom", "maya", "sam"],
    photoIds: [],
    startedYear: null,
    createdBy: "user_ana",
  },
  {
    id: "rosas_easter_hats",
    title: "Rosa's Easter hats",
    description:
      "Rosa makes a new Easter hat for each of the grandchildren, out of whatever she has: ribbon, paper flowers, once a whole bird's nest. " +
      "We wear them to church and then to lunch, no matter how tall they are. She has never made the same hat twice.",
    category: "craft",
    when: { kind: "yearly", label: "Easter Sunday", month: 4 },
    participantIds: ["rosa", "maya", "sam"],
    photoIds: [],
    startedYear: 1992,
    createdBy: "user_david",
  },
];

/** The demo traditions for `familyId`, stamped with `now` (default: the clock). */
export function buildDemoTraditions(familyId: string, now: Date = new Date()): Tradition[] {
  const at = now.toISOString();
  return DEMO_TRADITIONS.map((t) => ({
    ...JSON.parse(JSON.stringify(t)),
    familyId,
    createdAt: at,
    updatedAt: at,
  }));
}
