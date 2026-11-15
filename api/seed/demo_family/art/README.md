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
  its opaque shape. A body image gives the prop its `aspect` (width / height of the crop,
  clamped to 0.5 to 2.5 like a photo swap) and its `anchor` (bottom center of the shape).
  Parts are placed by the `at` fractions in `props.json`, so draw the body so they line up.
- **Back layers**: scaled evenly to the layer's frame (2048 px wide, height from the layer
  height in `sets.json`: 2048x1024 for a 1.0 layer), cropped from the bottom if the aspect
  differs, and made fully opaque.
- **Middle and front layers**: fitted the same way (e.g. 2048x464 or 2048x192), with the
  white background removed and no crop to the shape.

It then writes `art.json`: for every usable file its slot (`prop` + `artKey`, or `set` +
`layer`), pixel size, a body's `aspect` and `anchor`, and the `sha256` of the final PNG.
Baking again skips files that already match their entry, so it changes nothing twice.
Keep your originals elsewhere if you want to re-edit them.

Last, it compiles every demo scene with the baked art. A body image whose aspect or anchor
would stop a scene compiling is reported (with the scene and the compile error), and the
seed draws that prop's placeholder body instead.

The bake reports a file and gives it no entry (its prop or layer keeps the placeholder)
when its name matches no prop, set, art key or layer; it is not a readable image; it has
more than 16,000,000 pixels; nothing is opaque after the cut-out; or the result is over the
media size limit. It exits 1 when anything was reported, else 0.

## What the seed does

Each start, the seed reads `art.json` and the PNG bytes only (no image processing), stores
each file as a `cutout` with a fixed media id derived from its name (the same on every
reset), and sets its prop or layer. A PNG with no entry, or edited since it was baked (its
sha256 differs), keeps the placeholder with one warning naming the files and the bake
command. None of this stops the seed.
