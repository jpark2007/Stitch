# Stitch

**A pop-up storybook of your family, made from everyone's memories.**

Every family member adds photos and answers to questions like *"How did you meet Mom?"* or *"What was your first house like?"*. Stitch turns each answer into a page of a handmade pop-up book. You turn the paper page, and the scene rises off the paper: cut-paper scenery, little paper figures of the people who were there, and the story told in the teller's own words.

Built at HackPrinceton Fall 2026.

---

## The experience

0. **Join your family.** Each family member creates their own account. One person starts a family and shares an invite link. Everyone who joins either claims their spot in the tree ("I'm David, son of Rosa and Luis") or adds themselves to it. Relatives who will never sign up, like great-grandparents, can still be added to the tree so others can tell stories about them. A person can belong to more than one family, for example their own and their spouse's.
1. **The family tree (home).** Each family member is a small paper figure on a hand-drawn tree. Grandparents are at the top and children at the bottom.
2. **Their storybook.** Click a person and their book opens. Each memory is a spread, in order by year, and you flip through it like a real book, page curl and all.
3. **A memory pops up.** As a spread opens, the paper layers stand up from the fold: background scenery first, then props, then the characters. The figures then move a little (walking in, waving, a car driving across). The facing page holds the story text, the year, and the family's real photos, taped in like a scrapbook.
4. **Shared memories.** A memory belongs to everyone who was in it. Dad's "how I met your mom" page also appears in Mom's book, and when Mom adds her version, both tellings sit on the same spread.

### The look

- **Papery and handmade.** The pages have paper grain, deckled edges, and soft drop shadows under every cutout. The palette is warm, and the headings use a storybook serif.
- **Faceless paper characters.** Nobody gets a generated face, so nobody looks "wrong". Each person has a fixed paper color and one signature item, like Grandpa's blue cap or Mom's red scarf, and their figure looks the same on every page.
- **Photos inspire the art.** Photos aren't pasted into the scene. The agent reads them for the setting, objects, and colors (the green car, the brick church) and builds the cut-paper scenery from them. The original photos appear on the text page.

## How a memory is made

The family member answers a question, gives a year, and optionally uploads photos. A **Deep Agent** (LangChain `deepagents`) then works like a small pop-up book studio:

| Step | Sub-agent | What it does |
|---|---|---|
| 1 | **Interviewer** | If the answer is too thin to illustrate, it pauses and asks one or two follow-ups ("Where were you when you first saw her?") |
| 2 | **Storyboarder** | Splits the story into 1 to 3 spreads, each with a caption in the teller's own words |
| 3 | **Set designer** | Reads the photos and story, then designs the scenery as 2 to 3 depth layers (back, middle, front) |
| 4 | **Illustrator** | Builds each layer as a paper cutout and places the characters from their saved character sheets |
| 5 | **Checker** | Renders the spread, inspects it with vision, fixes overlaps and empty space, and rejects any caption detail the teller never gave |
| 6 | **Linker** | Puts the memory on the timeline of everyone in it and asks those people for their version |

The agent keeps character sheets and recurring places (the family house, the corner store) in long-term memory, so they look the same in every book.

## Prize tracks

- **LangChain: Best use of Deep Agents.** Planning, sub-agents, a filesystem for scene drafts, human-in-the-loop follow-ups, long-term memory, and a draw-inspect-fix loop.
- **MongoDB Atlas.** Accounts and families, the family tree via `$graphLookup`, photos in GridFS, Atlas Vector Search for "ask the family", and agent checkpoints and memory via `MongoDBSaver` and `MongoDBStore`.
- **Entertainment.** An interactive pop-up storybook starring your own family.

## Stack

- **Web:** Next.js, TypeScript, Framer Motion, CSS 3D transforms (page flips and pop-up layers)
- **API:** FastAPI (Python), LangChain `deepagents`, LangGraph
- **Accounts:** email and password (argon2 hashes) with a session JWT in an httpOnly cookie, and family invite links
- **Data:** MongoDB Atlas (documents, GridFS, Vector Search, LangGraph checkpointer and store)
- **Art:** agent-written SVG cutouts with paper-texture filters, plus an image model for scenery layers, with background removal to cut them out

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

## Running locally

Requires Node 20+, Python 3.12+, and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example api/.env        # fill in MONGODB_URI, JWT_SECRET, model API keys

# API (http://localhost:8000)
cd api
uv sync
uv run uvicorn main:app --reload

# Web (http://localhost:3000), in a second terminal
cd web
npm install
npm run dev
```

## Scope for the weekend

The goal is one path that works end to end. Two people sign up and join the same family. One of them adds a story and photos, a pop-up spread comes out, and it appears in both of their books. A demo family is seeded so the stage demo starts with a full tree. There are no real weekly reminders, and AI video is not on the live path.
