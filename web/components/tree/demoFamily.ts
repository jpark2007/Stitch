/**
 * The seeded demo family, shared by the tree and threads screens until the
 * real API is wired up. Shapes follow ARCHITECTURE.md section 5 (`people`,
 * `memories`) closely, trimmed to what those screens read, so they can be
 * swapped for `web/lib/types.ts` later. In the UI a memory is a "moment".
 *
 * Three generations, nine people. Each has a distinct thread colour that is
 * warm but still tells apart from its neighbours, and one signature item.
 */

export interface DemoCharacter {
  /** Thread / puppet colour, hex. */
  color: string;
  /** Signature item worn or carried by their puppet. */
  item: string;
  build?: "short" | "average" | "tall";
}

export interface DemoPerson {
  id: string;
  name: string;
  born: number;
  parentIds: string[];
  spouseIds: string[];
  character: DemoCharacter;
}

export interface DemoMoment {
  id: string;
  title: string;
  year: number;
  participantIds: string[];
  /** People who have told this moment (each is a telling). */
  tellers?: string[];
}

export const demoFamily = {
  id: "demo-family",
  name: "The Alvarez–Moore family",
} as const;

export const demoPeople: DemoPerson[] = [
  // Generation 1
  {
    id: "luis",
    name: "Luis",
    born: 1937,
    parentIds: [],
    spouseIds: ["rosa"],
    character: { color: "#3f6fb5", item: "blue flat cap", build: "tall" },
  },
  {
    id: "rosa",
    name: "Rosa",
    born: 1940,
    parentIds: [],
    spouseIds: ["luis"],
    character: { color: "#c0503a", item: "red knitted shawl", build: "short" },
  },
  {
    id: "walter",
    name: "Walter",
    born: 1935,
    parentIds: [],
    spouseIds: ["june"],
    character: { color: "#4e8a5b", item: "fishing hat with a lure", build: "average" },
  },
  {
    id: "june",
    name: "June",
    born: 1939,
    parentIds: [],
    spouseIds: ["walter"],
    character: { color: "#d39a2c", item: "pearl reading glasses", build: "short" },
  },
  // Generation 2
  {
    id: "david",
    name: "David",
    born: 1962,
    parentIds: ["luis", "rosa"],
    spouseIds: ["ana"],
    character: { color: "#2f8f8a", item: "brown leather satchel", build: "tall" },
  },
  {
    id: "ana",
    name: "Ana",
    born: 1964,
    parentIds: ["walter", "june"],
    spouseIds: ["david"],
    character: { color: "#9b4f86", item: "yellow sunflower hair clip", build: "average" },
  },
  {
    id: "carmen",
    name: "Carmen",
    born: 1967,
    parentIds: ["luis", "rosa"],
    spouseIds: [],
    character: { color: "#e07a4a", item: "film camera on a strap", build: "average" },
  },
  // Generation 3
  {
    id: "sofia",
    name: "Sofia",
    born: 1990,
    parentIds: ["david", "ana"],
    spouseIds: [],
    character: { color: "#d4627a", item: "green backpack", build: "average" },
  },
  {
    id: "mateo",
    name: "Mateo",
    born: 1994,
    parentIds: ["david", "ana"],
    spouseIds: [],
    character: { color: "#7a6a3a", item: "striped soccer scarf", build: "short" },
  },
];

export const demoMoments: DemoMoment[] = [
  { id: "m-dance-1960", title: "The dance at the church hall", year: 1960, participantIds: ["luis", "rosa"], tellers: ["luis", "rosa"] },
  { id: "m-lake-1963", title: "Walter proposes at the lake", year: 1963, participantIds: ["walter", "june"], tellers: ["june"] },
  { id: "m-sedan-1968", title: "The green sedan road trip", year: 1968, participantIds: ["luis", "rosa", "david", "carmen"], tellers: ["rosa", "david"] },
  { id: "m-recital-1975", title: "Ana's first piano recital", year: 1975, participantIds: ["ana", "june", "walter"], tellers: ["june"] },
  { id: "m-camera-1982", title: "Carmen's first camera", year: 1982, participantIds: ["carmen"], tellers: ["carmen"] },
  { id: "m-steps-1987", title: "How I met your mother", year: 1987, participantIds: ["david", "ana"], tellers: ["david", "ana"] },
  { id: "m-wedding-1989", title: "The wedding", year: 1989, participantIds: ["david", "ana", "rosa", "june"], tellers: ["ana", "rosa"] },
  { id: "m-sofia-1990", title: "Sofia comes home", year: 1990, participantIds: ["david", "ana", "sofia"], tellers: ["ana"] },
  { id: "m-maple-1996", title: "The first house on Maple Street", year: 1996, participantIds: ["david", "ana", "sofia", "mateo"], tellers: ["david"] },
  { id: "m-fishing-2003", title: "Grandpa Walt teaches Mateo to fish", year: 2003, participantIds: ["walter", "mateo"], tellers: ["mateo"] },
  { id: "m-graduation-2012", title: "Sofia's graduation", year: 2012, participantIds: ["sofia", "carmen"], tellers: ["sofia"] },
  { id: "m-birthday-2015", title: "Grandma's 75th birthday", year: 2015, participantIds: ["rosa", "luis", "sofia", "mateo"], tellers: ["sofia", "rosa"] },
  { id: "m-calls-2020", title: "Sunday calls in lockdown", year: 2020, participantIds: ["rosa", "mateo"], tellers: ["mateo"] },
];

/* ------------------------------------------------------------------------ */
/* Small lookups the screens share                                           */
/* ------------------------------------------------------------------------ */

const byId = new Map(demoPeople.map((p) => [p.id, p]));

export function getDemoPerson(id: string): DemoPerson | undefined {
  return byId.get(id);
}

/** A person's book: every moment they are in, oldest first. */
export function demoMomentsFor(personId: string): DemoMoment[] {
  return demoMoments
    .filter((m) => m.participantIds.includes(personId))
    .sort((a, b) => a.year - b.year);
}

/** Generation index (0 = oldest), from the parent links. */
export function demoGeneration(personId: string): number {
  const p = byId.get(personId);
  if (!p || p.parentIds.length === 0) return 0;
  return 1 + Math.max(...p.parentIds.map(demoGeneration));
}
