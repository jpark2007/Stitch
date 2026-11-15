# Demo art

Hand-made images for the demo family's props and sets. Add PNGs here, then bake them once
from `api/`:

```sh
uv run python -m seed.demo --bake-art
```

and commit the baked PNGs together with `art.json`. The seed (`seed/demo.py`,
`load_demo_art`) only reads what the bake recorded; anything without a baked file keeps its
placeholder (`art: null` and its `color`). With only this README here, the seed is exactly
as it was without the folder.

## Names

Ids are the `id`s in `../props.json` and `../sets.json`; art keys are the keys of a prop's
`art` map there.

| File | Fills |
|---|---|
| `<propId>.png` | the prop's body (`art.body`), e.g. `green_sedan.png` |
| `<propId>.<artKey>.png` | any other art key of that prop, e.g. `green_sedan.wheel.png`, `tamale_pot.lid.png` |
| `<setId>.back.png` | the set's back layer (depth 0) |
| `<setId>.middle.png` | the set's middle layer (depth 1) |
| `<setId>.front.png` | the set's front layer (depth 2) |

**States.** A state does not name an image itself: it maps an art key to another art key
of the same prop. `black_umbrella` has `"states": {"open": {"body": "open"}}` and an `open`
key in its `art` map, so its open look is `black_umbrella.open.png`; likewise
`ring_box.open.png` and `red_bike.ringing.png`. So the one rule `<propId>.<artKey>.png`
covers parts and states alike. A key that is not in the prop's `art` map is skipped (add it
to `props.json` first). `<propId>.body.png` also works for the body; giving both is a
warning and the first name in sorted order wins.

Names are case sensitive and use the `.png` extension only. Other files are reported
(this README, `art.json`, `.gitkeep` and `.DS_Store` are ignored).

## What the bake does

`--bake-art` processes each new or changed image the way the studio treats generated art,
and **rewrites the file in place** in that final form:

- **Props, parts and state art**: the white background touching the border is removed
  (an image that already has transparency is kept as it is), then the image is cropped to
  its opaque shape.
- **Art never changes a prop's shape.** The stage draws a body image stretched over the
  prop's box, whose width / height is the prop's `aspect` in `props.json`. So the bake puts
  the cropped body, unscaled, on the smallest transparent canvas of exactly that aspect,
  bottom-aligned and horizontally centred, and the prop keeps its authored `aspect`,
  `anchor` and every other rig field. The scenes stage exactly as authored. Draw close to
  the prop's aspect: a much wider drawing sits short in its box, a much taller one sits
  narrow (green_sedan is 1.9 wide per 1 tall; a 2.5-wide car comes out about three
  quarters of the box's height). The same applies to art a state shows in place of the body
  (`black_umbrella.open.png`).
- **Parts** (`green_sedan.wheel.png`, `tamale_pot.lid.png`) keep their own cropped shape:
  the stage sizes a part by its pixel height relative to the body image, so draw parts at
  the same scale as the body on the same sheet. They are placed by the `at` fractions in
  `props.json` (fractions of the body image, canvas included), so draw the body so they
  line up.
- **Back layers**: scaled evenly to the layer's frame (2048 px wide, height from the layer
  height in `sets.json`: 2048x1024 for a 1.0 layer), cropped from the bottom if the aspect
  differs, and made fully opaque.
- **Middle and front layers**: fitted the same way (e.g. 2048x464 or 2048x192), with the
  white background removed and no crop to the shape.

It then writes `art.json`: for every usable file its slot (`prop` + `artKey`, or `set` +
`layer`), its pixel size, and the `sha256` of the final PNG. Baking again skips files that
already match their entry, so it changes nothing twice. Keep your originals elsewhere if
you want to re-edit them.

The bake reports a file once and gives it no entry (its prop or layer keeps the
placeholder) when it is a symlink; its name matches no prop, set, art key or layer (or has
an empty key, like `tamale_pot..png`); it is not a readable image; it has
more than 16,000,000 pixels; nothing is opaque after the cut-out; or the result is over the
media size limit. It exits 1 when anything was reported, else 0.

## What the seed does

Each start, the seed reads `art.json` and the PNG bytes only (no image processing), stores
each file as a `cutout` with a fixed media id derived from its name (the same on every
reset), and sets its prop or layer. A PNG with no entry, or edited since it was baked (its
sha256 differs), keeps the placeholder with one warning naming the files and the bake
command. None of this stops the seed.
