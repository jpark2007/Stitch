# Stitch

**A pop-up storybook of your family, made from everyone's memories.**

Every family member adds photos and answers to questions like *"How did you meet Mom?"* or *"What was your first house like?"*. Stitch turns each answer into an animated page of a handmade pop-up book. You turn the paper page, the scenery rises off the paper, and the memory plays out: little paper puppets of the people who were there walk, hug and hand each other things, the camera glides through the scene, and the story is narrated in the teller's own words.

Built at HackPrinceton Fall 2026.

---

## The experience

0. **Join your family.** Each family member creates their own account. One person starts a family and shares an invite link. Everyone who joins either claims their spot in the tree ("I'm David, son of Rosa and Luis") or adds themselves to it. Relatives who will never sign up, like great-grandparents, can still be added to the tree so others can tell stories about them. A person can belong to more than one family, for example their own and their spouse's.
1. **The family tree (home).** Each family member is a small paper figure on a hand-drawn tree. Grandparents are at the top and children at the bottom.
2. **Their storybook.** Click a person and their 3D book opens. Each memory is a spread, in order by year, and you flip through it like a real book, page curl and all.
3. **A memory plays.** As a spread opens, the set rises from the fold, and the camera moves into it. The memory plays as one continuous scene:
   - Paper puppets walk in, sit, kneel, hug, and pick things up.
   - Props are real objects in the scene: the dropped hymn books, the ring, the family's green sedan.
   - Narration lines up with the action.
   - Between moments, the set folds flat and the next place unfolds, while the camera keeps moving.

   The facing page holds the tellings, the year, and the family's real photos, taped in like a scrapbook.
4. **Shared memories.** A memory belongs to everyone who was in it. Dad's "how I met your mom" page also appears in Mom's book, and when Mom adds her version, both tellings sit on the same spread.

5. **Hear the other version.** A moment is the event, and a telling is one person's answer about it. A spread has a tab for each teller ("Hear Mom's version"), and choosing one replays the same scene with that person's narration.
6. **Threads (the family timeline).** A second screen shows the whole family as stitched threads along a timeline, one color per person. Shared moments are knots where threads cross. Zoom out for decades and in for single years. It is the same set of pages as the books, sorted by time instead of by person.
7. **Play with the page.** Like a real pop-up book, spreads have pull tabs and sliders that move parts (drive the car, blow the train's steam). Click any character or object to see why it matters to the story. Orbit the camera a little around the open book, then snap back to reading.

See [DESIGN.md](DESIGN.md) for the full look, feel and interaction spec, and the quality bar every screen is checked against.

### The look

- **Papery and handmade.** The pages have paper grain, deckled edges, and soft drop shadows under every cutout. The palette is warm, and the headings use a storybook serif.
- **Faceless paper puppets.** Nobody gets a generated face, so nobody looks "wrong". Everyone is a jointed paper puppet with a fixed color and one signature item, like Grandpa's blue cap or Mom's red scarf. Emotion comes through body language.
- **Props come from the story.** Nothing is hardcoded. Every object a story mentions becomes a paper prop that the puppets can hold, hand over, or ride in. The family's own things (the first car, grandma's sewing machine) are saved and reappear in later memories.
- **Photos inspire the art.** Photos aren't pasted into the scene. The agent reads them for the setting, objects, and colors (the green car, the brick church) and builds the cut-paper scenery from them. The original photos appear on the text page.

## How a memory is made

The family member answers a question, gives a year, and optionally uploads photos. A **Deep Agent** (LangChain `deepagents`) then works like a small pop-up book studio:

| Step | Sub-agent | What it does |
|---|---|---|
| 1 | **Interviewer** | If the answer is too thin to illustrate, it pauses and asks one or two follow-ups ("Where were you when you first saw her?") |
| 2 | **Storyboarder** | Picks the moments to show, as a shot list |
| 3 | **Prop master** | Finds every object in the story, reuses the family's saved props or generates new paper ones, and rigs them. The family can review them. |
| 4 | **Set designer** | Builds each place as paper layers from the photos and story, or reuses a saved place |
| 5 | **Director** | Writes a screenplay: who walks where, who hands what to whom, and the camera moves |
| 6 | **Narrator** | Writes narration from the teller's own words and records it with timing |
| 7 | **Checker** | The screenplay is compiled into an animation. The checker renders frames, looks at them, and sends fixes back to the director. |
| 8 | **Linker** | Puts the memory in the book of everyone in it and asks those people for their version |

A compiler written in plain code (no AI) turns the screenplay into the animation. It rejects impossible actions (handing over something you aren't holding), solves the arm movements, and lines the action up with the narration. The model writes the story beats, and code makes them move.

The agent remembers each person's look and manner, the family's props, and recurring places (the family house, the corner store), so they look the same in every book.

## Prize tracks

- **LangChain: Best use of Deep Agents.** Planning, eight sub-agents, a filesystem for screenplay drafts, human-in-the-loop follow-ups and prop review, long-term memory, and a write-compile-watch-revise loop.
- **MongoDB Atlas.** Accounts and families, the family tree via `$graphLookup`, photos, cutouts and narration in GridFS, saved props and places matched by vector search, Atlas Vector Search for "ask the family", and agent checkpoints and memory via `MongoDBSaver` and `MongoDBStore`.
- **Entertainment.** An animated pop-up storybook starring your own family.

## Stack

- **Web:** Next.js, TypeScript, three.js via react-three-fiber (3D book, pop-up stage, puppets, camera), Framer Motion for the UI
- **API:** FastAPI (Python), LangChain `deepagents`, LangGraph
- **Accounts:** email and password (argon2 hashes) with a session JWT in an httpOnly cookie, and family invite links
- **Data:** MongoDB Atlas (documents, GridFS, Vector Search, LangGraph checkpointer and store)
- **Animation:** a screenplay compiler in Python (state tracking, two-bone IK, paths, narration timing) that outputs keyframe timelines
- **Art:** an image model in a locked cut-paper style for props and scenery, background removal for cutouts, and auto-rigging
- **Audio:** text-to-speech with word timestamps
- **Checking:** Playwright renders frames for the agent's vision checks

See [ARCHITECTURE.md](ARCHITECTURE.md) for how it is built and [DESIGN.md](DESIGN.md) for how it must look and behave.

## Running locally

Requires Node 20+, Python 3.12+, and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example api/.env        # fill in MONGODB_URI, JWT_SECRET, model API keys

# API (http://localhost:8000)
cd api
uv sync
uv run playwright install chromium   # headless browser for the agent's frame checks
uv run uvicorn main:app --reload

# Web (http://localhost:3000), in a second terminal
cd web
npm install
npm run dev
```

## Using the app

Keep this list current as features land: how to open it and everything that can be clicked, dragged or pulled.

- Nothing is interactive yet. The app is a scaffold.

## Scope for the weekend

The goal is one path that works end to end. Two people sign up and join the same family. One of them adds a story and photos, an animated pop-up scene comes out, and it appears in both of their books. A demo family is seeded so the stage demo starts with a full tree. There are no real weekly reminders, and AI video is not on the live path.
