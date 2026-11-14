// The demo family for mock mode (NEXT_PUBLIC_API_MOCK=1): the Riveras, built
// around the hymn books sample memory. No runtime imports and only erasable
// TypeScript, so `node --test` can load this file directly.
import type { Memory, MemoryResponse, Person, Prop, Question, SetDef } from "@/lib/types";

export interface MockSeed {
  users: Array<{ id: string; email: string; name: string; password: string }>;
  families: Array<{
    id: string;
    name: string;
    members: Array<{ userId: string; role: "admin" | "member" }>;
    invites: Array<{ code: string; createdBy: string; expiresAt: string; revoked?: boolean }>;
  }>;
  people: Person[];
  memories: Memory[];
  questions: Question[];
  /** The family's saved props and places (buildDemoSeed copies the sample's) */
  props?: Prop[];
  sets?: SetDef[];
}

/** Password of both demo accounts. */
export const DEMO_PASSWORD = "stitch-demo";
/** The demo family's open invite code. */
export const DEMO_INVITE_CODE = "riveras-demo";

const DAY_MS = 24 * 60 * 60 * 1000;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/**
 * Build the demo family from the hymn books sample (web/public/samples/
 * hymn_books.memory.json). The family id is the sample's familyId
 * ("sample_family"), so the sample memory, its props and its set belong to it
 * unchanged. `now` fixes the invite expiry and timestamps (default: the clock).
 */
export function buildDemoSeed(sample: MemoryResponse, now: Date = new Date()): MockSeed {
  const familyId = sample.memory.familyId || "sample_family";
  const created = now.toISOString();
  const samplePeople = new Map((sample.people ?? []).map((p) => [p.id, p]));

  const davidUser = "user_david";
  const anaUser = "user_ana";

  function fromSample(id: string, fallback: Person): Person {
    const found = samplePeople.get(id);
    return found ? { ...clone(found), familyId } : fallback;
  }

  const dadBase = fromSample("dad", {
    id: "dad",
    familyId,
    name: "David",
    born: 1962,
    character: { color: "#3f6fb5", item: "blue flat cap", build: "tall" },
  });
  const momBase = fromSample("mom", {
    id: "mom",
    familyId,
    name: "Ana",
    born: 1964,
    character: { color: "#c0504d", item: "red scarf", build: "average" },
  });

  const people: Person[] = [
    {
      id: "rosa",
      familyId,
      userId: null,
      name: "Rosa",
      born: 1938,
      died: null,
      parentIds: [],
      spouseIds: ["luis"],
      character: { color: "#7b5ea7", item: "pearl earrings", build: "small" },
      addedBy: davidUser,
    },
    {
      id: "luis",
      familyId,
      userId: null,
      name: "Luis",
      born: 1936,
      died: null,
      parentIds: [],
      spouseIds: ["rosa"],
      character: { color: "#5f7f52", item: "straw hat", build: "average" },
      addedBy: davidUser,
    },
    {
      ...dadBase,
      id: "dad",
      userId: davidUser,
      parentIds: ["rosa", "luis"],
      spouseIds: ["mom"],
      addedBy: davidUser,
    },
    {
      ...momBase,
      id: "mom",
      userId: anaUser,
      parentIds: [],
      spouseIds: ["dad"],
      addedBy: davidUser,
    },
    {
      id: "maya",
      familyId,
      userId: null,
      name: "Maya",
      born: 1991,
      died: null,
      parentIds: ["dad", "mom"],
      spouseIds: [],
      character: { color: "#d08a2e", item: "yellow raincoat", build: "short" },
      addedBy: davidUser,
    },
    {
      id: "sam",
      familyId,
      userId: null,
      name: "Sam",
      born: 1994,
      died: null,
      parentIds: ["dad", "mom"],
      spouseIds: [],
      character: { color: "#2e8b8b", item: "green backpack", build: "average" },
      addedBy: davidUser,
    },
  ];

  const sampleMemory = clone(sample.memory);

  const memories: Memory[] = [
    sampleMemory,
    {
      id: "first_house",
      familyId,
      title: "Our first house",
      year: 1993,
      era: "1990s",
      participantIds: ["dad", "mom"],
      tellings: [
        {
          authorId: "mom",
          question: "What was your first house like?",
          text:
            "It was a small yellow house on Alder Street with a crooked porch. " +
            "David painted the front door blue the first weekend, and we ate dinner on moving boxes for a month.",
          photoIds: [],
          createdAt: created,
        },
      ],
      screenplay: null,
      timeline: null,
      propIds: [],
      setIds: [],
      version: 1,
    },
    {
      id: "maya_first_bike",
      familyId,
      title: "Maya's first bike",
      year: 1998,
      era: "1990s",
      participantIds: ["dad", "maya"],
      tellings: [
        {
          authorId: "dad",
          question: "Do you remember teaching Maya to ride a bike?",
          text:
            "Maya got a red bike for her seventh birthday. I ran behind her down the whole street holding the seat, " +
            "and when I finally let go she didn't notice until she reached the corner.",
          photoIds: [],
          createdAt: created,
        },
      ],
      screenplay: null,
      timeline: null,
      propIds: [],
      setIds: [],
      version: 1,
    },
  ];

  const questions: Question[] = [
    {
      id: "q_mom_hymn_books",
      familyId,
      personId: "mom",
      text: "David told the story of the hymn books. What do you remember?",
      memoryId: sampleMemory.id,
      status: "open",
    },
    {
      id: "q_dad_maya_born",
      familyId,
      personId: "dad",
      text: "What do you remember about the day Maya was born?",
      memoryId: null,
      status: "open",
    },
  ];

  return {
    users: [
      { id: davidUser, email: "david@example.com", name: "David", password: DEMO_PASSWORD },
      { id: anaUser, email: "ana@example.com", name: "Ana", password: DEMO_PASSWORD },
    ],
    families: [
      {
        id: familyId,
        name: "The Riveras",
        members: [
          { userId: davidUser, role: "admin" },
          { userId: anaUser, role: "member" },
        ],
        invites: [
          {
            code: DEMO_INVITE_CODE,
            createdBy: davidUser,
            expiresAt: new Date(now.getTime() + 14 * DAY_MS).toISOString(),
            revoked: false,
          },
        ],
      },
    ],
    people,
    memories,
    questions,
    props: clone(sample.props ?? []).map((p) => ({ ...p, familyId: p.familyId ?? familyId })),
    sets: clone(sample.sets ?? []).map((s) => ({ ...s, familyId: s.familyId ?? familyId })),
  };
}
