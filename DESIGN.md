# Stitch design spec

What the app must look and feel like, and how each screen behaves. `README.md` says what Stitch is, `ARCHITECTURE.md` says how it is built, and this file is the bar the result is judged against. Read the section for your area before building UI, and before reviewing it.

## The feel

Homey and papery, but clean, structured and artsy. Never cluttered. Every screen should look like it belongs to the same handmade object: warm palette, paper grain, soft shadows, a storybook serif for headings and a handwriting font for captions. When in doubt, remove an element instead of adding one.

## Moments and tellings

- A **moment** is the event ("the wedding"). A **telling** is one person's answer about it.
- One moment can hold several tellings. The moment's spread has a tab per teller ("Hear Mom's version"), and choosing one **replays the same scene with that person's narration**. This is the strongest feature in the product; protect it when cutting scope.
- In the data model a moment is a `memories` document and its tellings are `memories.tellings[]`. The UI says "moment"; the collection and field names do not change.
- A person's book and the family timeline are **the same set of moments sorted two ways**. There is no separate "timeline content".

## Screens

### 1. Home: the family tree

- Every family member is a node with their name and their paper-puppet avatar, on a hand-drawn tree. Grandparents at the top, children at the bottom.
- Clicking a node opens that person's book: the moments of their life, in order by year.

### 2. Threads: the family timeline

One timeline for the whole family, drawn as stitched thread on paper. Think of an Obsidian graph that has a time axis.

- **Each person has a thread color** (their `character.color`). A person is a thread running along the timeline through every moment they are in.
- **A moment carries the color of everyone in it.** The wedding has Mom's and Dad's threads both passing through it.
- **Shared moments are visible knots** where threads cross. Moments with one person sit on that person's thread alone.
- **Zoom in and out** for scope: decades when zoomed out, single years and moment titles when zoomed in. Pan along time.
- Clicking a knot or a moment opens its spread. Clicking a thread highlights that person's path.
- Keep it sparse: threads, knots, years, and titles only at close zoom. No panels, legends or chrome competing with the threads.

### 3. The book

For every moment it must feel like a real, premium, handmade pop-up book lying on a wooden table under warm light.

**Materials**
- Paper looks like paper and card: visible thickness, folds, a slightly rough texture, soft shadows between layers.
- Warm, cinematic lighting with a depth-of-field feel and soft shadows.

**Opening and navigation**
- A title/intro state: the closed book sits on the table and opens when clicked.
- Click or drag the page corner to turn a page, with a physically convincing page-turn animation and sound.
- Page navigation dots or a small table of contents.

**Pop-up mechanics**
- When a spread opens, the pop-up elements unfold and rise from the page like real paper mechanisms (layered, hinged, V-folds). They fold back down when the page closes.

**Interactivity on every spread**
- **Pull tabs and sliders** on the page, like a real pop-up book, that move parts: make the car drive, make the train blow steam, kick the soccer ball.
- **Click any character or element** and it explains, in an animated way, why it matters to the person or the story.
- **Storybook text** printed on each page: short, written by the agent from the tellings, fun for kids and adults.
- **Camera:** orbit a little around the open book to see the pop-ups in 3D, then snap back to the reading view.

**Sound**
- Gentle generated music per era that changes with each spread (a 1960s moment does not sound like a 2010s one).
- Page-turn and paper sounds.
- A mute button, always visible.

**Performance**
- Smooth 60 FPS on a normal laptop.

## Quality bar and self-testing

Anyone (human or agent) who builds or changes book, stage or threads UI must do this before calling it done, and reviewers must do it before passing it:

1. Open the app in a headless browser (Playwright).
2. Check for console errors. Any error is a failure.
3. Take screenshots of every spread fully opened, plus the closed book, a mid-turn page, the tree, and the threads screen at two zoom levels.
4. Review the screenshots critically against this file: paper look, pop-up mechanics, layout, clutter.
5. Improve and repeat. **At least two full improvement passes.**

Keep the "Using the app" section of `README.md` current: how to open it and everything that can be clicked, dragged or pulled.

## Who builds what

| Piece | Owner area |
|---|---|
| Book, table, materials, page turn, pop-up mechanics, pull tabs, click-to-explain animation, orbit camera, audio playback, mute | Stage |
| Family tree home, threads screen, teller tabs, navigation dots / contents | Pages |
| Storybook text, per-telling narration, what each pull tab controls, the explanation for each character and prop | Agent + compiler |
| Listing all of a family's moments for the threads screen, era music assets and lookup | Data |

## What this adds to the contracts

`ARCHITECTURE.md` does not cover these yet. All of them are additive. The owner who implements each one posts the exact shape as `INTERFACE:` on Issue #1 first.

- **Narration per telling.** A moment needs one narration track (audio, word timings, captions) per telling, with the action shared, so switching tellers replays the same scene with a different voice.
- **Interactions.** The screenplay and timeline need a list of pull tabs and sliders: a label, where the tab sits on the page, and which prop part, state or track segment it drives.
- **Explanations.** Each cast member and prop in a moment needs a short "why this matters" text for click-to-explain.
- **Page text.** Each shot needs its short storybook text.
- **Era.** A moment's year maps to an era, and an era maps to a music track.
- **Threads data.** A route that returns every moment in a family as `id`, `title`, `year`, `participantIds`. Thread color is the existing `person.character.color`.

## If time runs short

Cut in this order, last item first. Never cut the top three.

1. One moment plays end to end as a pop-up scene with narration.
2. Two tellings of one moment, switchable by tab.
3. Tree home and the book with a convincing page turn and rising pop-ups.
4. Threads screen with colored threads, knots and zoom.
5. Pull tabs and click-to-explain.
6. Orbit camera, intro state, navigation dots.
7. Era music, depth-of-field polish.
