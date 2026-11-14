---
name: storyboarding
description: Turn a telling into a shot list for the pop-up stage. Read before choosing which moments to show, writing /shotlist.md, splitting a story between action and narration, writing storybook pageText, or handling a second telling of an existing memory. Defines the 1-4 shot rule, one place per shot, the telling:<authorId>:s<n> source format and the fixed /shotlist.md table.
---

# Storyboarding

You turn one person's telling into a short list of shots. A shot is one place and one moment
that the pop-up stage acts out with paper puppets and props while the teller's own words play
as narration. Shots do not cut: one set folds down and the next rises, so the whole memory
plays as one take.

## Choose 1 to 4 shots

1. Number the telling's sentences (see "Sources" below).
2. Find the moments where **someone does something visible** with a person, a prop or a place:
   arriving, leaving, giving, dropping, hugging, sitting down, driving off. Those are shots.
3. Keep the fewest shots that tell the story. One strong shot beats three weak ones.
   - A short anecdote in one place is **1 shot**.
   - A story that moves between places is one shot per place, **at most 4**.
   - Never more than 4. Merge or drop the least visual moments.
4. **One place per shot.** If the story moves ("we drove to the lake"), that is a new shot with
   a new set, or the travel is left to the narration.
5. Keep story order unless the teller clearly tells it out of order on purpose.
6. **One main beat per shot**: the moment its source sentence names ("she dropped every single
   hymn book"). The shot's other actions only lead up to it or follow from it. The director
   moves the camera in on that beat and its pageText tells it, and the memory's pull tab plays
   the story's main beat, so a shot with two big moments is two shots (or one moment goes to
   the narration).

## Action or narration?

The stage can only show what the action vocabulary can do (the `directing` skill lists it).

| Goes to **action** (shown) | Goes to **narration** only (heard) |
|---|---|
| Walking, running, entering, leaving | Feelings, thoughts, what someone meant |
| Picking up, dropping, handing over, wearing a prop | What people said to each other |
| Sitting, kneeling, hugging, waving, pointing, bowing a head | Smells, sounds, weather details the stage can't show |
| Getting in, driving, riding | Time passing, backstory, "years later" |
| Confetti, steam, rain, smoke, time of day | Anything that happens offstage |

- Puppets have **no faces**. "She blushed" is narration; "she couldn't look at me" can be
  shown as `bow_head`.
- Every shot needs at least one visible action. If a moment has none, it is narration over
  another shot, not a shot of its own.
- Never stage something the teller did not say happened. If the telling is vague about what
  someone did, keep it in narration.

## Sources

Every shot names the sentence it comes from: `telling:<authorId>:s<n>`.

- `<authorId>` is the teller's person id (`dad`), not their name.
- `<n>` is the sentence number in that telling, **1-based**: the first sentence is `s1`.
- Split sentences on `.`, `!` and `?` (an ellipsis `…` inside a sentence does not end it).
- Example. Dad's telling: "It was a Sunday in 1988. I saw her at the top of the church steps.
  And then she dropped every single hymn book. I handed them back, and she wouldn't even look
  at me." → `s1` "It was a Sunday in 1988." `s2` "I saw her at the top..." `s3` "And then she
  dropped..." `s4` "I handed them back...".
- A shot that spans sentences names the one with its main action. Narration lines name their
  own sentences later (the narrator and director do that).

## Write /shotlist.md

Write the shot list to `/shotlist.md` in exactly this format: one heading, one table, one row
per shot, shot ids `s1`, `s2`, ... in order. No other sections.

```markdown
# Shot list

| shot id | set/place | who | what happens | source |
|---|---|---|---|---|
| s1 | church steps, 1988 | dad, mom | Dad walks up; Mom drops the hymn books; Dad picks them up and hands them back; Mom bows her head | telling:dad:s3 |
```

- **shot id**: `s1`, `s2`, ... matching the screenplay's shot ids.
- **set/place**: a short place name the set designer can reuse or generate ("grandma's kitchen",
  "church steps, 1988"). If `find_memories` shows an earlier moment at the same place, use the
  same place name so the set designer finds the saved set.
- **who**: comma-separated person ids of everyone on stage.
- **what happens**: the visible actions in order, short clauses separated by `;`, the main beat
  among them. Only things the action vocabulary can show. Name every object someone touches:
  each one becomes a prop, and every person and prop on stage gets a click-to-explain note, so
  list in your final message what the telling says about each.
- **source**: one `telling:<authorId>:s<n>`.
- Never put a `|` inside a cell.

## pageText (storybook text)

Every shot gets `pageText`: the text printed on the page, like a picture book. The director
writes it; choose shots whose main beat it can tell.

- One or two short sentences per shot, at most 30 words.
- Third person, past tense ("Dad saw Mom at the top of the church steps."), never "I".
- Fun and warm, readable by a child and an adult.
- **No invented facts.** Only names, places, years and events from the tellings and the
  person records. If the year or place is unknown, leave it out.
- Not a copy of the narration; the narration is the teller's own voice, pageText is the book's.
- Example: `One Sunday in 1988, a stack of hymn books changed everything.`

## A second telling of an existing memory

When the run is a second telling (the run context has `memoryId` set), the memory already has
its shots and action, shared by every telling. The studio does not storyboard it: the narrator
places the new teller's sentences over the existing shots. If you are asked about one anyway:

- **Do not make new shots or change the action.** Read the existing shots in `/screenplay.json`.
- Map the new teller's sentences onto the **same shot ids**: which sentence goes over which
  shot, in order. A sentence about something no shot shows is either left out or placed over
  the nearest shot, never a reason to restage.
- Their narration goes in `narrations.<newAuthorId>.<shotId>` with sources
  `telling:<newAuthorId>:s<n>`, numbered from their own telling.
- If the new teller's version disagrees with the existing action ("I didn't drop them, he
  bumped me"), keep the action and let their narration carry their side. Report the
  disagreement in your final message instead of changing the scene.
