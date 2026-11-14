# Contracts

The conventions you cannot infer from the types. Everyone builds against these: the compiler writes them, the stage plays them, the agent's skills describe them.

| File | What it is |
|---|---|
| `api/screenplay/schema.py` | Screenplay and timeline models (source of truth) |
| `api/schemas.py` | Data model, API request/response bodies, SSE event payloads (source of truth) |
| `web/lib/types.ts` | **Generated** TypeScript mirror. Never edit by hand. |
| `shared/vocabulary.json` | Verbs and their parameters, camera moves, effects, sizes, builds, speeds, eras |
| `shared/rig/puppet.json` | The one puppet: bones, pivots, lengths, sockets |
| `shared/rig/poses.json` | Poses and walk/run cycles |
| `web/public/samples/hymn_books.*.json` | Reference scene: screenplay, compiled timeline, and the full `MemoryResponse` the API returns |

## Rules

- **Additive only.** Add fields; never rename or remove. New fields must be optional.
- Post `INTERFACE:` on Issue #1 for every change.
- To change a model: edit the Python file, run `cd api && uv run python ../shared/gen_types.py`, commit both. `uv run pytest` fails if `types.ts` is stale.
- To change the sample: edit `api/tests/gen_hymn_books_sample.py` and run it. Do not edit the sample JSON by hand.
- JSON is camelCase everywhere. API JSON uses `id`; only the db layer knows about `_id`.
- Everything the model writes is data. Unknown fields are rejected by every model.

## Stage space

Three.js-style, right-handed. Units are **stage units**.

| Axis | Direction | Range on stage |
|---|---|---|
| x | right | −1 (left edge) to +1 (right edge). Offstage is beyond ±1. |
| y | up | 0 is the ground (the page) |
| z | toward the viewer | 0 is the back of the set at the fold |

- The acting plane, where puppets and props stand by default, is **z = 0.5**.
- Set layers stand at `z = depth × 0.35`: back 0, middle 0.35, front 0.7. A layer spans the full stage width (2 units) and is `height` units tall when risen.

## Marks

A mark is `[u, h]` or `[u, h, d]`.

- `u`: fraction across the stage, 0 = left edge, 1 = right edge. `x = 2u − 1`.
- `h`: height of the standing surface above the ground, in stage units. `y = h`.
- `d`: optional depth, `z = d`. Default is the acting plane, 0.5.

Marks come from the set (`SetDef.marks`), can be added or overridden per shot (`Shot.marks`), and two are built in: `offstage_left` (x = −1.3) and `offstage_right` (x = 1.3).

## The puppet

- Rig units: standing height = 1.0. On stage a puppet is `stageHeight (0.5) × builds[build]` units tall, so `average` is 0.5 and `tall` is 0.55.
- It is a flat doll in its own XY plane. Local +x is the way it faces; +y is up. The local origin is the hip.
- **Every bone rotation is about z, in radians, counter-clockwise positive** seen from the front, relative to its parent. All zeros is standing with arms hanging.
  - For bones that point down (arms, legs), positive swings the tip forward (+x).
  - For bones that point up (torso, head), negative leans forward. `bow_head` is `head: -0.4`.
- A bone's `pivot` is in its parent's frame and turns with the parent. Its tip is `pivot + rotate(dir × length)`.
- `root.position` is the point on the ground under the hips, in stage units. The stage adds `hipHeight × scale` to get the hip. Poses with a `rootOffset` (sit, kneel) are already baked into `root.position` by the compiler.
- `root.facing` is `1` (faces right, +x) or `-1` (mirrored about the puppet's vertical axis, faces left). Rotations are always in the puppet's local, unmirrored frame.
- Sockets: `hand_l`, `hand_r` (at the wrist, the IK target) and `head` (top of the head, for the signature item). The default hand is `hand_r`.
- Two-bone IK puts a hand socket on a target: with shoulder-to-target vector `v` in local rig units and equal bone lengths `L`, `θ = atan2(v.x, −v.y)`, `a = acos(|v| / 2L)`, `upper = θ − a`, `lower = 2a`. Out of reach when `|v| > 2L`.

## Props

- World size is `sizes[size]` stage units tall; width is `height × aspect`.
- `anchor` (where it meets the ground) and `grip` (where a hand holds it) are fractions of the image, `[0, 0]` top-left.
- A held prop is placed so its grip sits on the hand socket. A prop on the ground is placed by its anchor.
- Missing art (`null`) means draw a placeholder card in `color`.

## Timeline

- Time is seconds from the start of the timeline. `t = 0` is when the first set begins to rise. A shot's screenplay `t = 0` happens at `TimelineShot.actionStart` (1.2 s after its `start`).
- One track per `target` + `channel`. Keys are `[t, value]`, sorted by `t`.
- The stage is a dumb player: interpolate, swap parents, play audio. No decisions.

| Target | Channel | Value | Interpolation |
|---|---|---|---|
| `puppet:<personId>` | `root.position` | `[x, y, z]` | linear |
| | `root.facing` | `1` or `-1` | step |
| | `<bone>.rot` (e.g. `arm_r.upper.rot`) | radians | linear |
| `prop:<propId>` | `parent` | `"ground"`, `"puppet:<id>.<socket>"` or `"prop:<id>.<socket>"` | step |
| | `position` | `[x, y, z]` | linear (see below) |
| | `rotation` | radians about z | linear |
| | `state` | state name | step |
| | `part.<name>.rot` | radians | linear |
| `set:<setId>.layer<n>` | `fold` | 0 (flat on the page) to 1 (risen) | linear |
| `camera` | `position` | `[x, y, z]` | linear, eased |
| | `lookAt` | `[x, y, z]` | linear, eased |
| | `fov` | degrees | linear |

- Before a track's first key, hold the first value. After its last key, hold the last value. A bone with no track is at 0.
- **Prop position and parent.** `position` is in the space of the current parent: world coordinates of the anchor when the parent is `ground`, an offset from the socket when held (normally `[0, 0, 0]`). At every `parent` key there is a `position` key at the same time; apply it as a step and never interpolate across a parent change.
- **Narration.** `Timeline.narrations` maps a teller's person id to their audio, captions and word timings. All tellings share the tracks. `audio` and `captions` at the top level repeat the `defaultNarrator`'s. Switching teller swaps audio and captions and replays from 0.
- **Interactions.** A `scrub` drive maps the tab's travel onto timeline time `from`..`to` for its `targets` only, while the rest of the scene holds. In the screenplay `from`/`to` are shot time; in the timeline they are timeline time.
- **Effects** are a list (`Timeline.effects`), not tracks: `{t, kind, at, dur, value}`.

## Screenplay

- Times are seconds from the shot's start.
- `Action.to` is a mark for movement verbs and a person for `hand_to`. `shared/vocabulary.json` lists the required and optional parameters of each verb; anything else on an action is a compile error.
- A narration `cue` names an action: an action `id` if one matches, else the first action in the shot whose `do` equals it. The compiler moves that action so it starts on `cueWord` (default: the line's last word).
- `Screenplay.narrations` holds the other tellings as `authorId → shotId → lines`. `Shot.narration` is the default telling.

## The reference sample

`hymn_books`: Dad enters, Mom drops the hymn books on the church steps, Dad picks them up and hands them back, Mom bows her head. Two tellings (Dad's and Mom's) over the same action, one pull tab, three explanations.

The compiler must reproduce `hymn_books.timeline.json` from `hymn_books.screenplay.json`: the same tracks, the same parent swaps at the same times, and numeric keys within a small tolerance.

## Clarifications

Answers to questions raised while building the stage. Where the stage and compiler could disagree, this is the rule.

1. **Foot planting.** Bent legs shorten the puppet, so feet lift off the surface during walk and run cycles. The compiler is responsible for planting: it lowers `root.position.y` on walk/run keys by `(hipHeight − lowestFoot) × scale`, and sets `Timeline.feetPlanted = true` when it has. When `feetPlanted` is absent or false, the stage plants the feet itself; when true it must not. The reference sample is not planted.
2. **IK frame.** The shoulder-to-target vector `v` is in the torso's frame (the shoulder's parent): rotate the world vector by `−torso` when the torso leans (hug, slumped, run) before solving.
3. **Sockets and grip.** Only a hand socket takes a prop by its grip. On any other socket (the head, a prop's socket) the prop sits by its anchor.
4. **Camera easing.** `position` and `lookAt` use smoothstep (`3s² − 2s³`) on each key segment. `fov` is plain linear.
5. **Narration audio.** A narration's audio is one clip that starts at timeline `t = 0`, with silence before and between lines, so play it from 0 with the scene. Media references are `media/<id>`; fetch one at `GET /families/{fid}/media/<id>` with the `media/` prefix stripped.
6. **Captions.** A caption shows until the next caption, or the end of its shot. There is no end time.
7. **Pull tabs.** While dragged, a `scrub` tab drives its targets; on release they return to the scene's current time. The scene itself is never seeked by a tab. For `prop_part`, `rot` is in radians and `offset` is a translation along the part's local x in fractions of the prop's height; the default `range` is `[0, 1]`.
8. **Defaults.** A prop with no `position` track is at `[0, 0, 0]` in its parent's space. A prop with no `parent` track is on the ground. A held prop's orientation is its own `rotation` channel only; it does not inherit the hand's angle.

## Decisions

Where `ARCHITECTURE.md` was ambiguous or its example could not be followed literally:

1. **Camera is three tracks** (`position`, `lookAt`, `fov`), not one mixed `pose` track. Named moves like `wide` are resolved to numbers by the compiler so the stage stays a dumb player.
2. **`id`, not `_id`**, in all API JSON.
3. **Mark's second number is height**, matching `steps_top: [0.6, 0.3]` in the architecture example. Depth is an optional third number.
4. **The hand socket is at the wrist**, so arm IK is exactly two bones. The hand bone is art only.
5. **Set layers have their own `fold` tracks**, so rising and folding are played, not decided, by the stage.
6. **A dropped prop lands beside the dropper's feet**, 0.08 units in the direction they face, on their standing surface.
7. **Cues land on the start of the cued action.**
8. **`SetLayer.art` and prop art may be `null`** with a `color`, so the stage can be built before any art exists.
9. **Run events add `merge`**, for the agent's "is Uncle Joe the Joseph in the tree?" approval, alongside the events listed in the architecture doc.
