---
name: popup-style
description: The Stitch cut-paper pop-up look. Read before describing a puppet, prop or set layer, choosing a prop's size class or grip, or calling generate_prop / generate_set_layer. Covers faceless jointed puppets, cut-paper props and sets, the warm palette, size classes, grip hints, back/middle/front layers, and the locked style prompt in style_prompt.txt.
---

# Pop-up style

Every memory plays as a scene in a handmade pop-up book: paper puppets, cut-paper props and
layered paper scenery standing up from the fold of a book on a wooden table under warm light.
Everything you describe must fit that one object. When in doubt, make it simpler.

## The locked style prompt

The style prompt lives in `style_prompt.txt`, in the same folder as this SKILL.md. The image
tools (`generate_prop`, `generate_set_layer`) add it to every image request themselves.

- **Never restate, paraphrase, extend or alter it.** Do not put style words ("watercolor",
  "photorealistic", "3D render", "cartoon", "cut paper", lighting, camera angle) in your
  descriptions. Changing the style is what makes props and scenery look inconsistent.
- You only describe **what the thing is**: object, colors, material, notable details.
- You may read `style_prompt.txt` to understand the look. You never send it yourself.

## Puppets (people)

- Every person is the same jointed paper doll, joined at pivots like a split-pin puppet:
  head, torso, upper and lower arms, hands, upper and lower legs.
- **No faces, ever.** Emotion is body language only: head tilt (`bow_head`), posture
  (`slumped`, `arms_raised`), walking speed (`walk_to` vs `run_to`) and how close they stand.
  Never write "she smiled" or "his eyes" into anything that becomes a picture or an action.
- Each person has a **fixed body color** (`character.color`, also their thread color) and
  **one signature item** worn on the head socket (dad's blue flat cap, mom's red scarf).
  Never recolor a person or swap their item to suit a scene. Read them with `get_person`.
- Height comes from `character.build`: `small`, `short`, `average`, `tall`. A puppet of
  build `average` is 0.5 stage units tall.
- Puppets are never generated as images. Do not ask for pictures of people.

## Props

Any object the story mentions that someone touches, carries, sits on, rides or that matters to
the story becomes a prop. Background objects nobody touches belong in a set layer instead.

### Size classes

The size class sets the prop's height on stage, so scale is always right. Pick by real-world
size, not by importance. A puppet of build `average` is 0.5 stage units tall for comparison.

| Size | Height (stage units) | For | Examples |
|---|---|---|---|
| `tiny` | 0.03 | Things that fit in a closed hand | ring, key, coin, letter, photo |
| `hand` | 0.08 | Things held in one hand | book, stack of hymn books, cup, phone, apple |
| `carried` | 0.2 | Things carried with an arm or both hands | suitcase, guitar, trumpet case, bag of persimmons, baby |
| `furniture` | 0.4 | Things a person sits on, stands at or leans on | chair, table, bench, bicycle, piano |
| `vehicle` | 0.9 | Things people ride in | car, boat, tractor, bus |
| `building` | 1.5 | Structures people go into | house, shed, church. Usually a set layer instead. |

### Describing a prop for generation

Write one plain sentence of facts: what it is, its main colors, its material, and one or two
details from the telling or the photos. No style words, no background, no people, no hands.

- Good: `a stack of worn blue hymn books with gold lettering on the spines`
- Good: `a green 1985 four-door sedan with a dented front bumper`
- Bad: `a cute cut-paper illustration of books on a table, warm lighting` (style words and a background)
- Bad: `mom holding her hymn books` (a person in the picture)

Use details only from the tellings and photos (`describe_photo`). If a photo shows the object,
pass that photo as reference. Never invent a brand, inscription or color nobody mentioned;
leave it out instead.

For moving parts, describe the body without the part, then the part on its own
(`car body without wheels`, then `one car wheel, side view`). For an open/closed prop,
describe each state as its own image (`brown leather suitcase, closed` / `..., open`).

### Naming a grip

The grip is where a hand holds the prop. Name it in a few words: **which part, and where on the
outline**. Code snaps the point to the nearest edge of the cut-out shape.

- `the handle, on top` (suitcase, bucket)
- `the spine, left side` (book)
- `the neck, upper left` (guitar)
- `the rim, top edge` (cup, bowl)
- `null` for props nobody holds (furniture, vehicles, buildings)

### Placeholders

If art is missing or generation fails, the prop still works: its art is `null` and it is drawn
as a paper card in its `color`. Always give every prop a `color` (hex) that matches its main
color in the description, from the warm palette below where the story allows.

## Sets (scenery)

A set is the scenery for one shot: three paper layers standing at depths, rising from the fold.

| Layer | `depth` | Stage z | What goes on it | Typical height |
|---|---|---|---|---|
| Back | 0 | 0 | The place itself: building front, sky, wall of a room, distant hills | 0.8 to 1.2, tallest |
| Middle | 1 | 0.35 | Things just behind the puppets: steps, fence, counter, trees | 0.3 to 0.5 |
| Front | 2 | 0.7 | Low framing in front of the puppets: grass, curb, flowerbed, table edge | 0.1 to 0.2, never hides puppets |

- Puppets act at z = 0.5, between the middle and front layers. Keep the front layer low and
  open in the middle so it never covers the action.
- Describe each layer as one plain sentence of facts, like a prop: the place, the era, the
  main colors and materials. `brick church front with a tall arched wooden door, 1980s`.
- No people in any layer. No style words.
- If a standing surface matters (steps, a porch, a stage), say so; the set's marks give it a
  height (`steps_top: [0.6, 0.3]`).
- Reuse a saved place (`find_set`) for recurring locations (the family house, grandma's
  kitchen) so it looks the same in every memory.

## Palette

Warm and papery: cream, ochre, terracotta, brick red, sage and olive green, dusty blue, warm
browns. Avoid neon, pure black and pure white. Placeholder colors for layers and props should
come from this range, for example back `#b5654a`, middle `#cfc6b4`, front `#7d9a5b`.
