---
name: directing
description: Write the screenplay (/screenplay.json) that the compiler turns into the pop-up scene. Read before writing or fixing a screenplay, narration lines, pageText, pull tabs, explanations or a second telling's narration. Holds the exact action vocabulary (every verb and its parameters, camera moves, effects, poses), marks and timing conventions, cue/cueWord rules, the compile_screenplay fix loop (3 rounds max) and a worked example, hymn_books.screenplay.json in this folder.
---

# Directing

You write `/screenplay.json`: who stands where, what they do and when, where the camera goes,
and what the narrator says over it. The compiler (no AI) turns it into keyframes and rejects
anything it cannot do, with exact errors. Use **only** the vocabulary below. Anything else
fails to compile.

## Workflow

1. Read `/shotlist.md` (from the storyboarder), the props, the sets (with their `marks`) and the
   cast (`get_person` for each: build, color, signature item, movement notes in `/memories/`).
2. Read `hymn_books.screenplay.json` in this folder (the worked example, walked through below).
3. Write `/screenplay.json`.
4. Run the fix loop: `compile_screenplay` → read errors → fix → compile again. **At most 3
   rounds** (see "The compile loop").

## The file: /screenplay.json

JSON, **camelCase** keys, **unknown fields are rejected** everywhere (a typo like `"pagetext"`
or an extra `"note"` is an error). Ids are plain: person ids (`dad`), prop ids (`hymn_books`),
mark names (`steps_top`), never `puppet:dad` except in `targets` and `explanations`.

```
Screenplay
  memoryId      string                     the memory id you were given
  cast          [personId]                 everyone who appears in any shot
  props         [propId]                   every prop used in any shot
  shots         [Shot]                     1 to 4, in story order
  narrations    {authorId: {shotId: [NarrationLine]}}   other tellings (optional)
  interactions  [Interaction]              pull tabs and sliders (optional)
  explanations  [Explanation]              click-to-explain, one per cast member and prop

Shot
  id            "s1", "s2", ...            same ids as /shotlist.md
  set           setId
  marks         {name: Mark}               extra or overriding marks for this shot (optional)
  start         {personId: StartState}     every person on stage in this shot
  propStart     {propId: PropStart}        props not held at the start (optional)
  camera        [CameraCue]
  actions       [Action]
  narration     [NarrationLine]            the default telling's lines for this shot
  transition    "fold" | "none"            default "fold"
  pageText      string                     storybook text for this shot

StartState  { at: mark, holding?: propId, pose?: poseName, facing?: "left"|"right", in?: "<propId>.<socket>" }
PropStart   { at?: mark, state?: stateName, on?: "<propId>.<socket>" }
CameraCue   { t, move, target?, dur? }
Action      { t, do, id?, ...the parameters its verb lists, nothing else }
NarrationLine { t, text, source, cue?, cueWord? }      never write `words`; TTS fills it
```

## Stage, marks and time

- The stage is 2 units wide: x = −1 (left edge) to +1 (right edge). y is up, 0 is the page.
  z is toward the viewer; puppets and props act on the **acting plane, z = 0.5**.
- A **mark** is `[u, h]` or `[u, h, d]`:
  - `u` = fraction across the stage, 0 = left edge, 1 = right edge. **x = 2u − 1**.
  - `h` = height of the standing surface (steps, porch), in stage units. y = h. Ground is 0.
  - `d` = optional depth, z = d. Default 0.5. Usually leave it out.
- Marks come from the set (`SetDef.marks`); add or override per shot in `Shot.marks`. Two are
  built in: **`offstage_left`** (x = −1.3) and **`offstage_right`** (x = 1.3). Use them for
  `enter`, `exit` and `start.at` of someone who walks on.
- On-stage marks keep u between 0.05 and 0.95. A puppet of build `average` is 0.5 tall; arm
  reach is about 0.17 stage units (0.085 in u).
- **Times are seconds from the shot's start.** A shot's t = 0 happens **1.2 s after its set
  starts rising**, so the set is up before anything moves. Every shot restarts at t = 0.
- Walk speed is 0.35 stage units/s, run 0.8, drive 0.9. Arrival time = t + |Δx| / speed, with
  Δx = 2 × Δu. Example: `offstage_left` (x = −1.3) to u = 0.2 (x = −0.6) walking: 0.7 / 0.35 =
  2.0 s.
- Give each action time to finish before the same person's next one. Rough durations from the
  reference scene: `pick_up` 0.8 s (reach 0.4, then raise), `drop` 0.3 s fall, `hand_to` 0.9 s
  (hands meet at +0.5), `pose` 0.4 s unless `dur` is given.
- A narration line that runs longer than the actions extends the shot. Estimate about
  0.25 to 0.35 s per spoken word when spacing lines (the worked example uses 0.25).

## Action vocabulary

Generated from `shared/vocabulary.json`. Every action has `t` and `do`, may have `id`, and may
have **only** the parameters its row lists. **Anything not listed for a verb is a compile
error** (for example `dur` on `walk_to`, or `who` on `rain`).

| Verb | Kind | Required | Optional | Note |
|---|---|---|---|---|
| `walk_to` | movement | `who`, `to` | - | to = mark |
| `run_to` | movement | `who`, `to` | - | to = mark |
| `enter` | movement | `who`, `to` | `from` | from = offstage_left or offstage_right (default: nearest side); to = mark |
| `exit` | movement | `who` | `to` | to = offstage_left or offstage_right (default: nearest side) |
| `turn_to` | movement | `who`, `target` | - | target = person, prop or mark |
| `follow` | movement | `who`, `target` | `dur` | target = person |
| `pose` | body | `who`, `pose` | `dur` | pose = any name in shared/rig/poses.json |
| `wave` | body | `who` | `target`, `dur` | |
| `point_at` | body | `who`, `target` | `dur` | |
| `sit_on` | body | `who`, `what` | `socket` | what = prop with a seat socket |
| `stand_up` | body | `who` | - | |
| `kneel` | body | `who` | `dur` | |
| `hug` | body | `who`, `target` | `dur` | target = person; both puppets hug |
| `lean_toward` | body | `who`, `target` | `dur` | |
| `pick_up` | prop | `who`, `what` | - | prop must not be held by anyone; who must be within reach or walks to it first |
| `hold` | prop | `who`, `what` | - | who must already be holding it; raises it into the hold pose |
| `drop` | prop | `who`, `what` | - | who must be holding it |
| `hand_to` | prop | `who`, `what`, `to` | - | to = person; who must be holding it |
| `put_on` | prop | `who`, `what` | `target` | wear a prop (head socket); target = person, default self |
| `place_on` | prop | `who`, `what`, `on` | `socket` | on = prop with a surface socket, or a mark |
| `open` | prop | `who`, `what` | - | sets prop state to open |
| `close` | prop | `who`, `what` | - | sets prop state to closed |
| `set_state` | prop | `what`, `state` | `who` | |
| `get_in` | vehicle | `who`, `what` | `socket` | socket default: driver if free, else next free seat |
| `get_out` | vehicle | `who`, `what` | - | |
| `drive_to` | vehicle | `who`, `what`, `to` | - | who must be in the driver socket; to = mark |
| `ride_to` | vehicle | `who`, `what`, `to` | - | who must be in any socket and someone in driver; to = mark |
| `confetti` | effect | - | `at`, `dur` | at = mark, person or prop |
| `steam` | effect | - | `at`, `dur` | |
| `rain` | effect | - | `dur` | |
| `smoke` | effect | - | `at`, `dur` | |
| `time_of_day` | effect | `value` | `dur` | value = dawn, day, dusk or night |

Parameter meanings: `who` person id; `what` prop id; `to` a mark for movement verbs and a
**person** for `hand_to`; `from` offstage mark; `target` person, prop or mark; `on` prop id or
mark; `socket` socket name on a prop; `pose` pose name; `state` state name; `at` mark, person
or prop (effects); `value` time of day; `dur` seconds.

**Effects** (actions with no `who`): `confetti`, `steam`, `rain`, `smoke`, `time_of_day`.
**Times of day** (`time_of_day` `value`): `dawn`, `day`, `dusk`, `night`.

### Poses (`pose` verb and `StartState.pose`)

From `shared/rig/poses.json`. No other names exist.

| Pose | Looks like | Use for |
|---|---|---|
| `stand` | standing, arms hanging | reset to neutral |
| `hold` | right forearm raised | carrying something in front |
| `sit` | seated, hips lowered | sitting without a seat prop (use `sit_on` for a chair) |
| `kneel` | one knee down | kneeling, proposing, tending a child |
| `wave` | right arm up | greeting, goodbye |
| `point` | right arm straight out | showing something |
| `reach` | right arm forward | reaching toward someone |
| `hug` | both arms forward | embrace (prefer the `hug` verb with a target) |
| `slumped` | torso and head down | sad, tired, defeated |
| `arms_raised` | both arms up | joy, celebration, surprise |
| `bow_head` | head tipped down | shy, ashamed, grieving, praying |

### Camera moves

| Move | Required | Optional | Does |
|---|---|---|---|
| `wide` | - | `dur` | whole set in view |
| `dolly_to` | `target` | `dur` | moves in toward a person, prop or mark |
| `pan_to` | `target` | `dur` | turns to a target without moving in |
| `close_up` | `target` | `dur` | tight on a target |
| `pull_back` | - | `dur` | back out to wide |

A `CameraCue` is `{ "t", "move", "target"?, "dur"? }`, nothing else. Start every shot with
`{ "t": 0, "move": "wide" }`. Move in on the emotional beat; end the last shot with a
`pull_back` before the page turns. Keep moves at least 1.5 s apart.

### Transitions

`Shot.transition` is `fold` (the set folds down and the next rises; default) or `none`.

## Rules the compiler enforces

- **Movement**: `Action.to` is a **mark** for `walk_to`, `run_to`, `enter`, `exit`,
  `drive_to`, `ride_to`. For `hand_to` it is a **person**.
- **Holding**: a held prop sits on the holder's **`hand_r`** by default. You cannot `drop`,
  `hand_to` or `hold` a prop the person is not holding, and you cannot `pick_up` a prop someone
  else holds. The receiver of `hand_to` must have a free hand and be within reach.
- **Dropping**: a dropped prop **lands beside the dropper's feet**, 0.08 units in the
  direction they face, on the surface they stand on. Plan the `pick_up` from there.
- **Reach**: to `pick_up`, `hand_to` or `hug`, first walk the person to a mark within about
  0.1 in u (0.2 stage units) of the prop or person. Out of reach is a compile error.
- **State carries between shots** (who holds what, prop states), but each shot's `start`
  places people again. Make `holding` agree with what happened in the previous shot.
- Every person in `start`, `who`, `to` (hand_to) and `target` must be in `cast`; every prop
  in `holding`, `what` and `propStart` must be in `props`; every mark must exist in the set,
  in `Shot.marks`, or be built in.

## Narration, cue and cueWord

- `Shot.narration` is the **default telling** (the memory's first teller). Each line:
  `t` (shot time), `text` (the teller's own words, lightly trimmed, never invented),
  `source` (`telling:<authorId>:s<n>`, sentence numbers 1-based).
- **`cue`** ties a line to an action: it names an action **`id`** if one matches, otherwise
  **the first action in the shot whose `do` equals it** (e.g. `"cue": "drop"`). Give cued
  actions an `id` to be unambiguous.
- **`cueWord`** is the word in `text` the action should land on. Default: the line's last
  word. The compiler uses the TTS word timings to move the cued action so that it **starts on
  the cue word**. Write `t` so that word falls roughly where the action is anyway.
- `cueWord` must be one word, exactly as it appears in `text`, without punctuation.

### Other tellings: `narrations`

One memory, one shared action, one narration per telling. The default telling goes in each
shot's `narration`; every other teller goes in

```json
"narrations": { "<authorId>": { "<shotId>": [ { "t": 0, "text": "...", "source": "telling:<authorId>:s1" } ] } }
```

- Same shot ids, same actions, a different voice. Never add or move actions for a second
  telling; cue the **same action ids** so both tellings land their words on the same moments.
- For a second telling of an existing memory, load the existing screenplay, add
  `narrations.<newAuthorId>` and leave everything else as it was.

## pageText

One or two short storybook sentences per shot, third person, past tense, warm, no invented
facts (only what the tellings and person records say). Not a copy of the narration.

## interactions (pull tabs and sliders)

A pull tab or slider printed on the page that the reader drags to make something move. Add one
or two per memory, on the most physical moment.

```
Interaction { id, kind: "pull_tab"|"slider", label, shot?, page?: "stage"|"story", at: [u, v], drives }
```

- `kind` is `pull_tab` (a paper tab pulled out from the page edge, for one-shot moments like a
  drop) or `slider` (a knob slid along a slot, for travel back and forth like driving).
- `label` is printed by the tab: "Pull to drop the books", "Slide to drive the car".
- `at` is `[u, v]` on that page, fractions from the **top-left**. Keep tabs near the page edge
  (u or v about 0.8 to 0.9).
- `shot` is the shot id it belongs to; `page` is `stage` (default) or `story`.
- `drives` has exactly one `type`:
  - `scrub`: `{ "type": "scrub", "from": 3.25, "to": 3.55, "targets": ["prop:hymn_books", "puppet:mom"] }`.
    Dragging plays shot time `from`..`to` for those targets only (shot time; default targets:
    all). Choose a range that covers one action, e.g. a drop and its 0.3 s fall.
  - `prop_state`: `{ "type": "prop_state", "prop": "suitcase", "states": ["closed", "open"] }`.
    Steps the prop through states it has.
  - `prop_part`: `{ "type": "prop_part", "prop": "green_sedan_1985", "part": "wheel_front", "channel": "rot", "range": [0, 6.28] }`.
    Drives one moving part (`channel` `rot` or `offset`) across `[min, max]`.
- Only reference props, parts and states that exist on the prop.

## explanations (click-to-explain)

**Exactly one per cast member and one per prop.** `target` is `"puppet:<personId>"` or
`"prop:<propId>"`. `text` is one or two sentences on why that person or thing matters in this
story, from the tellings and person records only. No invented ages, names or facts.

```json
{ "target": "prop:hymn_books", "text": "The hymn books Mom dropped on the steps. Dad says it is the best thing that ever fell at his feet." }
```

## The compile loop

1. Write `/screenplay.json`.
2. Call `compile_screenplay`. It returns success, or a list of errors, each with `code`,
   `message`, and where known `shot`, `t`, `who`, `do`. Example:
   `shot s1, t=5: dad cannot hand_to "hymn_books"; mom is holding it`.
3. **Fix exactly what each error names**, at that shot and time. Do not rewrite the scene,
   rename ids or change things that compiled. Typical fixes: wrong mark name → use one that
   exists; not holding → add the `pick_up` or fix `start.holding`; out of reach → walk closer
   first; unknown field → delete it; too early → move `t` after the previous action ends.
4. Compile again. **At most 3 rounds** (3 calls to `compile_screenplay`). If the third still
   fails, stop and report the remaining errors exactly as returned. Never loop further.

## Worked example: hymn_books.screenplay.json

The file `hymn_books.screenplay.json` in this folder is the reference scene. It compiles. Read
it with `read_file`, then follow along.

The tellings. Dad: "It was a Sunday in 1988. (s1) I saw her at the top of the church steps.
(s2) And then she dropped every single hymn book. (s3) I handed them back, and she wouldn't
even look at me. (s4)" Mom: "I was carrying far too many hymn books. (s1) Of course they went
everywhere, right in front of him. (s2) He handed them back so carefully. (s3) I couldn't look
up. (s4)"

**Top level**

- `"memoryId": "sample_hymn_books"`: the memory being directed.
- `"cast": ["dad", "mom"]`, `"props": ["hymn_books"]`: every person and prop used anywhere.
- One shot, `s1`: a single place and a single moment, so one shot is enough.

**The shot**

- `"set": "church_steps_1988"`: the set id from the set designer.
- `"marks"`: `steps_left [0.2, 0.0]` (x = −0.6, ground), `steps_foot [0.5, 0.0]` (x = 0,
  ground), `steps_top [0.6, 0.3]` (x = 0.2, standing 0.3 up on the steps), `curb [0.9, 0.0]`.
  `offstage_left` is built in, so it is not listed.
- `"start"`: Dad begins at `offstage_left` facing `right` (he walks on). Mom begins at
  `steps_top`, `holding` `hymn_books` (on her `hand_r`), `facing` `left` toward Dad.
- `"camera"`: `wide` at t = 0, then at 5.5 `dolly_to` `mom` over `dur` 3 s, moving in as Dad
  picks up the books.

**The actions, one by one**

1. `{ "t": 0, "who": "dad", "do": "enter", "from": "offstage_left", "to": "steps_left" }`:
   x −1.3 → −0.6 is 0.7 units at 0.35/s, so Dad arrives at t = 2.0.
2. `{ "t": 3.25, "who": "mom", "do": "drop", "what": "hymn_books", "id": "drop" }`: Mom is
   holding them, so this is legal. The books fall for 0.3 s and land beside her feet: she faces
   left, so 0.08 to her left, on the step (x = 0.12, y = 0.3). `id` lets narration cue it.
3. `{ "t": 3.6, "who": "dad", "do": "walk_to", "to": "steps_foot" }`: x −0.6 → 0.0 is 0.6
   units, arrives at about t = 5.31.
4. `{ "t": 5.5, "who": "dad", "do": "pick_up", "what": "hymn_books" }`: after he arrives; the
   books lie 0.12 to his right and 0.3 up, inside his reach. In hand at 5.9, raised by 6.3.
5. `{ "t": 6.6, "who": "dad", "do": "hand_to", "what": "hymn_books", "to": "mom", "id": "hand_back" }`:
   `to` is a person here. Dad is holding the books, Mom's hand is free and in reach; hands meet
   at 7.1 and the books move to Mom's `hand_r`.
6. `{ "t": 7.7, "who": "mom", "do": "pose", "pose": "bow_head" }`: she can't look at him. No
   face needed; the head bow says it.

**Narration (Dad's telling, the default)**

- `t 0`, "I saw her at the top of the church steps…", `source telling:dad:s2`. Sentence s1
  ("It was a Sunday in 1988.") is left to the pageText.
- `t 2.5`, "…and then she dropped every single hymn book.", `source telling:dad:s3`,
  `"cue": "drop"` (the action id), `"cueWord": "dropped"`. At 0.25 s per word, "dropped" (the
  fourth word) falls at 2.5 + 3 × 0.25 = 3.25, exactly the drop's `t`; the compiler then snaps the drop onto the real
  spoken word.
- `t 6.35`, "I handed them back, and she wouldn't even look at me.", `source telling:dad:s4`,
  `"cue": "hand_back"`, `"cueWord": "handed"`: the second word lands at 6.6, the hand_to.
- `"transition": "fold"` and `"pageText": "One Sunday in 1988, a stack of hymn books changed
  everything."`: third person, past tense, only facts from the tellings.

**Mom's telling (`narrations.mom.s1`)**: same shot, same actions, her words.

- `t 0` "I was carrying far too many hymn books." (`telling:mom:s1`).
- `t 2.0` "And of course they went everywhere, right in front of him." (`telling:mom:s2`),
  `cue drop`, `cueWord everywhere`: the sixth word, at 2.0 + 5 × 0.25 = 3.25, the same drop.
- `t 6.35` "He handed them back so carefully. I couldn't look up." (`telling:mom:s3`),
  `cue hand_back`, `cueWord handed`. Her s4 rides along in the same line.

**Interaction**: `drop_books`, a `pull_tab` labeled "Pull to drop the books" on the `stage`
page of `s1`, at `[0.85, 0.82]` (lower right). It `scrub`s shot time 3.25 → 3.55 (the drop
and its 0.3 s fall) for `prop:hymn_books` and `puppet:mom` only, while the rest holds.

**Explanations**: three, one for each cast member (`puppet:dad`, `puppet:mom`) and one for the
prop (`prop:hymn_books`), each a sentence or two on why it matters.
