import type { BookCover, BookSpread } from "./types";

/**
 * Three hardcoded moments from David's book in the seeded demo family
 * (see components/tree/demoFamily.ts), used by /memories/demo until the API
 * serves real memories.
 */

export const demoCover: BookCover = {
  kicker: "The Alvarez–Moore family",
  title: "David’s book",
  subtitle: "1987 – 1996",
};

export const demoSpreads: BookSpread[] = [
  {
    id: "m-steps-1987",
    title: "How I met your mother",
    year: 1987,
    tellers: ["David", "Ana"],
    text:
      "It was raining on the church steps, and Ana was carrying far too many hymn books. One slipped, then all of them did. David picked up every single one. Ana says he knocked them over on purpose. He says he would do it again.",
    photos: [
      { id: "p-steps", scene: "steps", caption: "St. Mary’s, spring ’87", aspect: 4 / 3 },
      { id: "p-sea", scene: "sea", caption: "our first walk", aspect: 1 },
    ],
  },
  {
    id: "m-wedding-1989",
    title: "The wedding",
    year: 1989,
    tellers: ["Ana", "Rosa"],
    text:
      "Two years later they were back on the same steps, this time on purpose. Grandma Rosa sewed the veil by hand and cried through the whole thing. The cake leaned a little to the left, and nobody minded at all.",
    photos: [
      { id: "p-wedding", scene: "wedding", caption: "June 10th, 1989", aspect: 3 / 4 },
      { id: "p-hills", scene: "hills", caption: "the drive home", aspect: 4 / 3 },
    ],
  },
  {
    id: "m-maple-1996",
    title: "The first house on Maple Street",
    year: 1996,
    tellers: ["David"],
    text:
      "The house on Maple Street had a crooked porch and a door that stuck in summer. Sofia claimed the room with the round window. Mateo claimed the stairs, for sliding down. David fixed the door eleven times.",
    photos: [
      { id: "p-house", scene: "house", caption: "moving day!", aspect: 4 / 3 },
    ],
  },
];
