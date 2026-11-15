# Demo art

Hand-made images for the demo family's props and sets. The seed (`seed/demo.py`,
`load_demo_art`) picks up every PNG here; anything without a file keeps its placeholder
(`art: null` and its `color`). With only this README here, the seed is exactly as it was
without the folder.

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

Names are case sensitive and use the `.png` extension only. Other files are skipped with a
warning (this README, `.gitkeep` and `.DS_Store` are ignored silently).

## How each file is processed

The same way the studio treats generated art:

- **Props, parts and state art**: the white background touching the border is removed
  (an image that already has transparency is kept as it is), then the image is cropped to
  its opaque shape. A body image sets the prop's `aspect` (width / height of the crop,
  clamped to 0.5 to 2.5 like a photo swap) and its `anchor` (bottom center of the shape).
  Parts are placed by the `at` fractions in `props.json`, so draw the body so they line up.
- **Back layers**: scaled evenly to the layer's frame (2048 px wide, height from the layer
  height in `sets.json`: 2048x1024 for a 1.0 layer), cropped from the bottom if the aspect
  differs, and made fully opaque.
- **Middle and front layers**: fitted the same way (e.g. 2048x464 or 2048x192), with the
  white background removed and no crop to the shape.

Each result is stored as a `cutout` with a fixed media id derived from the file name, so
every reset gives the same ids.

A file is skipped with a warning naming it, and its prop or layer keeps the placeholder,
when its name matches no prop, set, art key or layer; it is not a readable image; it has
more than 16,000,000 pixels; it has no opaque pixels after the cut-out; or a body image's
aspect or anchor would stop a demo scene compiling (the warning names the scene and the
compile error). None of these stops the seed.

## Keep starts fast

Save images with transparency (RGBA PNG). A white background is flood-filled away on
every start, which takes about half a second per image. After adding white-background
files, run once from `api/`:

```sh
uv run python -m seed.demo --cut-out-art
```

It rewrites those files with the background made transparent (back layers are left alone).
