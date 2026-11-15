"""seed/demo_family/art/: hand-made prop and set images, baked once, then picked up by the seed.

`python -m seed.demo --bake-art` (demo.bake_demo_art) processes each image like studio art,
rewrites it in final form and records it in art/art.json; the seed (demo.load_demo_art) reads
only art.json and the PNG bytes. Every test uses small generated PNGs in a tmp folder
(demo.ART_DIR monkeypatched), never the real folder, and the in-memory database.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

import main
from agent.tools.sets import layer_size
from db import client as db_client
from db import media as media_db
from db import props as props_db
from seed import demo
from seed.demo import DEMO_FAMILY_ID, DEMO_PASSWORD, seed_demo

LOG = "seed.demo"
API_DIR = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------------------- helpers

def _png(im: Image.Image) -> bytes:
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def cutout_png(size: tuple[int, int], box: tuple[int, int, int, int], color=(150, 60, 40, 255)) -> bytes:
    """A transparent RGBA canvas with one opaque rectangle `box` (right/bottom exclusive)."""
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=color)
    return _png(im)


def white_png(size: tuple[int, int], box: tuple[int, int, int, int], color=(40, 90, 160)) -> bytes:
    """An RGB image: a coloured rectangle on a plain white background."""
    im = Image.new("RGB", size, "white")
    ImageDraw.Draw(im).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=color)
    return _png(im)


def opaque_png(size: tuple[int, int], color=(120, 170, 210)) -> bytes:
    return _png(Image.new("RGB", size, color))


def rgba(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        return im.convert("RGBA")


@pytest.fixture
def art_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty art folder (only a README, like the committed one) used by every seed."""
    folder = tmp_path / "art"
    folder.mkdir()
    (folder / "README.md").write_text("naming rules\n")
    monkeypatch.setattr(demo, "ART_DIR", folder)
    return folder


def _bake(art_dir: Path) -> demo.BakeResult:
    return demo.bake_demo_art(art_dir)


def _seed(mdb):
    db_client.ensure_indexes(mdb)
    seed_demo(mdb, reset=True)
    return mdb


def _bake_and_seed(art_dir: Path, mdb):
    _bake(art_dir)
    return _seed(mdb)


def _manifest(art_dir: Path) -> dict:
    return json.loads((art_dir / demo.MANIFEST_NAME).read_text())


def _prop(mdb, prop_id: str):
    return props_db.get_prop(mdb, DEMO_FAMILY_ID, prop_id)


def _set(mdb, set_id: str):
    return props_db.get_set(mdb, DEMO_FAMILY_ID, set_id)


def _stored(mdb, ref: str) -> media_db.MediaFile:
    found = media_db.get_media(mdb, DEMO_FAMILY_ID, ref)
    assert found is not None, ref
    return found


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOG and r.levelno == logging.WARNING]


def _load(art_dir: Path):
    return demo.load_demo_art(demo.load_props(), demo.load_sets(), art_dir)


def _authored(prop_id: str):
    return next(p for p in demo.load_props() if p.id == prop_id)


def _only_art_differs(stored, authored) -> None:
    """The stored prop is the authored one with art refs set: the rig never changes."""
    assert stored.model_dump(exclude={"art"}) == authored.model_dump(exclude={"art"})
    assert sorted(stored.art) == sorted(authored.art)


def _opaque_box(data: bytes) -> tuple[int, int, int, int] | None:
    return rgba(data).getchannel("A").point(lambda a: 255 if a >= 128 else 0).getbbox()


# ------------------------------------------------------------------------ empty folder

def test_the_art_folder_is_in_the_demo_data_and_has_its_naming_rules():
    assert demo.ART_DIR == demo.DATA_DIR / "art"
    readme = (demo.ART_DIR / "README.md").read_text()
    assert "<propId>.<artKey>.png" in readme and demo.BAKE_COMMAND in readme


def test_an_empty_folder_gives_exactly_todays_props_and_sets(art_dir: Path):
    data = demo.load_demo_data()
    assert data.art == ()
    assert data.props == demo.load_props()
    assert data.sets == demo.load_sets()
    assert all(value is None for p in data.props for value in p.art.values())
    assert all(layer.art is None for s in data.sets for layer in s.layers)


def test_a_missing_folder_changes_nothing(tmp_path: Path):
    props, sets = demo.load_props(), demo.load_sets()
    assert demo.load_demo_art(props, sets, tmp_path / "nope") == (props, sets, ())


def test_baking_an_empty_folder_writes_nothing(art_dir: Path):
    assert _bake(art_dir) == demo.BakeResult([], [])
    assert sorted(p.name for p in art_dir.iterdir()) == ["README.md"]


def test_an_empty_folder_seeds_exactly_as_today(art_dir: Path, mdb):
    seeded = _seed(mdb)
    for prop in demo.load_props():
        assert _prop(seeded, prop.id) == prop
    for set_def in demo.load_sets():
        assert _set(seeded, set_def.id) == set_def
    kinds = sorted(d["metadata"]["kind"] for d in seeded[media_db.INLINE].find({}))
    assert kinds == ["photo", "photo"]


# ------------------------------------------------------------------------------- baking

def test_bake_rewrites_the_file_and_records_it_in_the_manifest(art_dir: Path):
    # diploma is authored at aspect 1.6; this drawing is 150x100 inside a larger canvas.
    (art_dir / "diploma.png").write_bytes(cutout_png((300, 240), (60, 40, 210, 140)))
    (art_dir / "coast_road_1972.back.png").write_bytes(opaque_png((1024, 512)))
    result = _bake(art_dir)

    assert result == demo.BakeResult(["coast_road_1972.back.png", "diploma.png"], [])
    assert result.ok
    baked = (art_dir / "diploma.png").read_bytes()
    # Rewritten in final form: cropped to the 150x100 shape, then placed unscaled on the
    # smallest canvas of diploma's authored aspect (1.6), bottom-aligned and centred.
    assert rgba(baked).size == (160, 100)
    assert _opaque_box(baked) == (5, 0, 155, 100)
    back = (art_dir / "coast_road_1972.back.png").read_bytes()
    manifest = _manifest(art_dir)
    assert layer_size(0.9) == (2048, 928)  # coast_road_1972's back layer is 0.9 units tall
    assert manifest == {"version": 1, "files": {
        "coast_road_1972.back.png": {"set": "coast_road_1972", "layer": "back", "width": 2048, "height": 928,
                                     "sha256": hashlib.sha256(back).hexdigest()},
        "diploma.png": {"prop": "diploma", "artKey": "body", "width": 160, "height": 100,
                        "sha256": hashlib.sha256(baked).hexdigest()},
    }}


def test_baking_twice_changes_nothing(art_dir: Path):
    (art_dir / "diploma.png").write_bytes(white_png((300, 200), (30, 50, 270, 200)))
    (art_dir / "coast_road_1972.middle.png").write_bytes(white_png((1024, 200), (0, 120, 1024, 200)))
    _bake(art_dir)
    first = {p.name: p.read_bytes() for p in art_dir.iterdir()}
    _bake(art_dir)
    assert {p.name: p.read_bytes() for p in art_dir.iterdir()} == first
    assert not [p for p in art_dir.iterdir() if p.suffix == ".tmp"]


def test_the_seed_imports_nothing_from_agent(art_dir: Path):
    """The app seeds on start (STITCH_FAKE_DB) and must never import agent/: the bake does the
    image work, the seed reads bytes and art.json."""
    (art_dir / "diploma.png").write_bytes(white_png((300, 200), (30, 50, 270, 200)))
    (art_dir / "school_hall_2015.back.png").write_bytes(opaque_png((512, 256)))
    _bake(art_dir)
    script = textwrap.dedent(f"""
        import sys
        from pathlib import Path
        import mongomock
        from db import props as props_db
        from seed import demo
        demo.ART_DIR = Path({str(art_dir)!r})
        db = mongomock.MongoClient()["x"]
        demo.seed_demo(db)
        prop = props_db.get_prop(db, demo.DEMO_FAMILY_ID, "diploma")
        assert prop.art["body"], prop
        leaked = sorted(k for k in sys.modules if k == "agent" or k.startswith("agent."))
        print("leaked", leaked)
    """)
    env = {k: v for k, v in os.environ.items() if not k.startswith("STITCH_")}
    env["STITCH_NO_DOTENV"] = "1"
    proc = subprocess.run([sys.executable, "-c", script], cwd=API_DIR, env=env,
                          capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "leaked []"


# -------------------------------------------------------------------------------- props

def test_a_prop_body_sets_only_the_art_ref(art_dir: Path, mdb):
    (art_dir / "diploma.png").write_bytes(cutout_png((300, 240), (60, 40, 210, 140)))
    seeded = _bake_and_seed(art_dir, mdb)

    prop = _prop(seeded, "diploma")
    ref = media_db.media_ref(demo.demo_art_media_id("diploma", "body"))
    assert prop.art == {"body": ref}
    _only_art_differs(prop, _authored("diploma"))

    stored = _stored(seeded, ref)
    assert stored.kind == "cutout" and stored.content_type == "image/png"
    assert stored.filename == "diploma.png"
    assert stored.data == (art_dir / "diploma.png").read_bytes()  # the baked bytes, as they are
    w, h = rgba(stored.data).size
    assert w / h == pytest.approx(_authored("diploma").aspect, abs=0.01)


@pytest.mark.parametrize(("size", "box", "canvas", "shape"), [
    # green_sedan is authored at aspect 1.9. A 2.5-wide drawing sits shorter in its box ...
    ((300, 120), (0, 0, 300, 120), (300, 158), (0, 38, 300, 158)),
    # ... and a 0.5-wide one sits narrower, both bottom-aligned and centred, never stretched.
    ((60, 120), (0, 0, 60, 120), (228, 120), (84, 0, 144, 120)),
])
def test_a_sedan_body_of_any_shape_keeps_the_authored_rig(art_dir: Path, mdb, size, box, canvas, shape):
    (art_dir / "green_sedan.png").write_bytes(cutout_png(size, box))
    seeded = _bake_and_seed(art_dir, mdb)
    sedan = _prop(seeded, "green_sedan")
    _only_art_differs(sedan, _authored("green_sedan"))
    baked = _stored(seeded, sedan.art["body"]).data
    assert rgba(baked).size == canvas
    assert canvas[0] / canvas[1] == pytest.approx(1.9, abs=0.01)
    assert _opaque_box(baked) == shape
    # Staged exactly as authored: the stored timeline is the one compiled with no art.
    trip = demo.load_demo_data(art_dir=art_dir.parent / "none").scenes["demo_green_sedan_trip"].timeline
    stored = demo.memories_db.get_memory(seeded, DEMO_FAMILY_ID, "demo_green_sedan_trip").timeline
    assert stored == trip


def test_a_white_background_body_is_cut_out_like_studio_art(art_dir: Path, mdb):
    (art_dir / "diploma.png").write_bytes(white_png((300, 200), (30, 50, 270, 200)))
    seeded = _bake_and_seed(art_dir, mdb)
    prop = _prop(seeded, "diploma")
    _only_art_differs(prop, _authored("diploma"))
    im = rgba(_stored(seeded, prop.art["body"]).data)
    assert im.size == (240, 150)  # already diploma's 1.6
    assert im.getchannel("A").getextrema()[1] == 255


def test_the_body_is_fetchable_by_a_member_and_404_across_families(art_dir: Path, mdb):
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    seeded = _bake_and_seed(art_dir, mdb)
    media_id = demo.demo_art_media_id("diploma", "body")
    db_client.set_db(seeded)
    try:
        app = main.create_app()
        member = TestClient(app)
        res = member.post("/auth/login", json={"email": "sam@demo.stitch", "password": DEMO_PASSWORD})
        assert res.status_code == 200, res.text
        res = member.get(f"/families/{DEMO_FAMILY_ID}/media/{media_id}")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/png"
        assert rgba(res.content).size == (160, 100)

        outsider = TestClient(app)
        res = outsider.post("/auth/signup", json={"email": "zoe@example.com", "password": "pw-zoe-12345", "name": "Zoe"})
        assert res.status_code == 201, res.text
        res = outsider.post("/families", json={"name": "The Zs", "person": {"name": "Zoe"}})
        assert res.status_code == 201, res.text
        own = res.json()["family"]["id"]
        assert outsider.get(f"/families/{DEMO_FAMILY_ID}/media/{media_id}").status_code == 404
        assert outsider.get(f"/families/{own}/media/{media_id}").status_code == 404
    finally:
        db_client.set_db(None)


def test_a_part_key_file_fills_that_key_only(art_dir: Path, mdb):
    (art_dir / "green_sedan.wheel.png").write_bytes(cutout_png((120, 120), (10, 10, 110, 110)))
    seeded = _bake_and_seed(art_dir, mdb)
    sedan = _prop(seeded, "green_sedan")
    authored = next(p for p in demo.load_props() if p.id == "green_sedan")
    assert sedan.art == {"body": None, "wheel": media_db.media_ref(demo.demo_art_media_id("green_sedan", "wheel"))}
    assert sedan.parts == authored.parts
    assert (sedan.aspect, sedan.anchor) == (authored.aspect, authored.anchor)  # only a body sets the rig
    assert rgba(_stored(seeded, sedan.art["wheel"]).data).size == (100, 100)
    assert _manifest(art_dir)["files"]["green_sedan.wheel.png"]["artKey"] == "wheel"


def test_a_state_key_file_fills_the_art_the_state_switches_to(art_dir: Path, mdb):
    # black_umbrella: "states": {"open": {"body": "open"}} -> its open look is the `open` key.
    (art_dir / "black_umbrella.open.png").write_bytes(cutout_png((110, 100), (0, 0, 110, 100)))
    seeded = _bake_and_seed(art_dir, mdb)
    umbrella = _prop(seeded, "black_umbrella")
    assert umbrella.art["open"] == media_db.media_ref(demo.demo_art_media_id("black_umbrella", "open"))
    assert umbrella.art["body"] is None
    _only_art_differs(umbrella, _authored("black_umbrella"))
    # `open` is drawn in place of the body, so it is fitted into the body's box (aspect 1.1).
    stored = _stored(seeded, umbrella.art["open"])
    assert stored.kind == "cutout"
    assert rgba(stored.data).size == (110, 100)


def test_a_state_body_is_fitted_into_the_body_box_and_a_part_keeps_its_shape(art_dir: Path):
    (art_dir / "black_umbrella.open.png").write_bytes(cutout_png((200, 100), (0, 0, 200, 100)))
    (art_dir / "green_sedan.wheel.png").write_bytes(cutout_png((200, 100), (0, 0, 200, 100)))
    _bake(art_dir)
    assert rgba((art_dir / "black_umbrella.open.png").read_bytes()).size == (200, 182)  # 1.1
    assert rgba((art_dir / "green_sedan.wheel.png").read_bytes()).size == (200, 100)  # a part: its own aspect


# --------------------------------------------------------------------------------- sets

def test_each_layer_depth_gets_its_file(art_dir: Path, mdb):
    authored = next(s for s in demo.load_sets() if s.id == "coast_road_1972")
    heights = {int(layer.depth): layer.height for layer in authored.layers}
    (art_dir / "coast_road_1972.back.png").write_bytes(opaque_png((1024, 512)))
    (art_dir / "coast_road_1972.middle.png").write_bytes(white_png((1024, 200), (0, 120, 1024, 200)))
    (art_dir / "coast_road_1972.front.png").write_bytes(cutout_png((2048, 100), (0, 40, 2048, 100)))
    seeded = _bake_and_seed(art_dir, mdb)

    layers = {int(layer.depth): layer for layer in _set(seeded, "coast_road_1972").layers}
    for depth, name in enumerate(("back", "middle", "front")):
        ref = media_db.media_ref(demo.demo_art_media_id("coast_road_1972", name))
        assert layers[depth].art == ref
        assert layers[depth].height == heights[depth] and layers[depth].color == authored.layers[depth].color
        stored = _stored(seeded, ref)
        assert stored.kind == "cutout" and stored.content_type == "image/png"
        assert rgba(stored.data).size == layer_size(heights[depth])  # the studio's full frame, never cropped

    back = rgba(_stored(seeded, layers[0].art).data)
    assert back.getchannel("A").getextrema() == (255, 255)
    middle = rgba(_stored(seeded, layers[1].art).data).getchannel("A")
    assert middle.getextrema() == (0, 255)  # the white sky is gone, the strip is kept
    assert middle.getpixel((10, 5)) == 0 and middle.getpixel((10, middle.height - 2)) == 255
    # Other sets are untouched.
    other = next(s for s in demo.load_sets() if s.id == "church_steps_1988")
    assert _set(seeded, "church_steps_1988") == other


def test_a_back_layer_with_transparency_is_made_fully_opaque(art_dir: Path):
    (art_dir / "coast_road_1972.back.png").write_bytes(cutout_png((2048, 900), (0, 300, 2048, 900)))
    _bake(art_dir)
    _props, _sets, art = _load(art_dir)
    assert len(art) == 1
    assert rgba(art[0].png).getchannel("A").getextrema() == (255, 255)


# ----------------------------------------------------------------- unbaked and stale files

def test_an_unbaked_file_is_skipped_with_one_warning_naming_the_bake_command(art_dir: Path, caplog):
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    (art_dir / "sam_cap.png").write_bytes(cutout_png((140, 100), (0, 0, 140, 100)))
    with caplog.at_level(logging.WARNING, logger=LOG):
        result = _load(art_dir)
    assert result == (demo.load_props(), demo.load_sets(), ())
    warnings = [m for m in _warnings(caplog) if "not baked" in m]
    assert len(warnings) == 1
    assert "diploma.png" in warnings[0] and "sam_cap.png" in warnings[0] and demo.BAKE_COMMAND in warnings[0]


def test_a_file_added_after_baking_is_skipped_and_the_baked_ones_still_load(art_dir: Path, caplog):
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    _bake(art_dir)
    (art_dir / "sam_cap.png").write_bytes(cutout_png((140, 100), (0, 0, 140, 100)))
    with caplog.at_level(logging.WARNING, logger=LOG):
        props, _sets, art = _load(art_dir)
    assert [f.filename for f in art] == ["diploma.png"]
    assert next(p for p in props if p.id == "sam_cap").art["body"] is None
    assert any("sam_cap.png" in m and demo.BAKE_COMMAND in m for m in _warnings(caplog))


def test_a_file_edited_after_baking_is_skipped(art_dir: Path, mdb, caplog):
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    _bake(art_dir)
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100), color=(0, 0, 200, 255)))
    with caplog.at_level(logging.WARNING, logger=LOG):
        seeded = _seed(mdb)
    assert _prop(seeded, "diploma") == next(p for p in demo.load_props() if p.id == "diploma")
    assert seeded[media_db.INLINE].count_documents({"metadata.kind": "cutout"}) == 0
    assert any("diploma.png" in m and "changed since baking" in m and demo.BAKE_COMMAND in m
               for m in _warnings(caplog))
    # Baking again picks the edit up.
    _bake(art_dir)
    assert len(_load(art_dir)[2]) == 1


def test_a_broken_manifest_means_nothing_is_baked(art_dir: Path, caplog):
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    _bake(art_dir)
    (art_dir / demo.MANIFEST_NAME).write_text("{not json")
    with caplog.at_level(logging.WARNING, logger=LOG):
        assert _load(art_dir)[2] == ()
    assert any(demo.MANIFEST_NAME in m and "unreadable" in m for m in _warnings(caplog))


def test_a_manifest_entry_for_another_slot_is_not_trusted(art_dir: Path, caplog):
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    (art_dir / "sam_cap.png").write_bytes(cutout_png((140, 100), (0, 0, 140, 100)))
    _bake(art_dir)
    manifest = _manifest(art_dir)
    manifest["files"]["diploma.png"]["prop"] = "lectern"
    (art_dir / demo.MANIFEST_NAME).write_text(json.dumps(manifest))
    with caplog.at_level(logging.WARNING, logger=LOG):
        props, _sets, art = _load(art_dir)
    assert [f.filename for f in art] == ["sam_cap.png"]
    assert next(p for p in props if p.id == "diploma") == _authored("diploma")
    assert any("diploma.png" in m and "not baked" in m for m in _warnings(caplog))


# ----------------------------------------------------------------------------- bad files

BAD_FILES: dict[str, bytes | None] = {
    "diploma.png": b"this is not a png",  # not an image
    "lectern.png": None,  # truncated PNG, filled in below
    "kitchen_table.png": _png(Image.new("RGBA", (64, 64), (0, 0, 0, 0))),  # nothing opaque
    "kitchen_chair.png": _png(Image.new("RGB", (64, 64), "white")),  # all white: nothing left
    "no_such_prop.png": opaque_png((10, 10)),  # unknown id
    "green_sedan.door.png": opaque_png((10, 10)),  # unknown art key
    "coast_road_1972.png": opaque_png((10, 10)),  # a set needs a layer name
    "coast_road_1972.sky.png": opaque_png((10, 10)),  # unknown layer
    "diploma.body.extra.png": opaque_png((10, 10)),  # too many dots
    "tamale_pot..png": opaque_png((10, 10)),  # empty key after the dot
    "peaches.jpg": opaque_png((10, 10)),  # not .png
    "road_map.png": opaque_png((300, 300)),  # over the (lowered) pixel cap
}


def test_bad_files_are_reported_by_the_bake_and_the_seed_goes_on(art_dir: Path, mdb, caplog, monkeypatch):
    monkeypatch.setattr(demo, "DEMO_ART_MAX_PIXELS", 50_000)
    good = cutout_png((150, 100), (0, 0, 150, 100))
    for name, data in BAD_FILES.items():
        (art_dir / name).write_bytes(good[: len(good) // 2] if data is None else data)
    (art_dir / "sam_cap.png").write_bytes(cutout_png((140, 100), (0, 0, 140, 100)))  # one good file

    with caplog.at_level(logging.WARNING, logger=LOG):
        result = _bake(art_dir)
    assert result.baked == ["sam_cap.png"] and not result.ok
    assert sorted(result.skipped) == sorted(BAD_FILES)
    warnings = _warnings(caplog)
    for name in BAD_FILES:
        assert len([m for m in warnings if f"art/{name} " in m]) == 1, (name, warnings)  # once each
        assert any(f"art/{name}" in m and "placeholder stays" in m for m in warnings), (name, warnings)
    assert not any("README.md" in m or demo.MANIFEST_NAME in m for m in warnings)
    assert any("road_map.png" in m and "pixel cap" in m for m in warnings)
    assert any("green_sedan.door.png" in m and "no art key 'door'" in m for m in warnings)
    assert any("tamale_pot..png" in m and "empty art key" in m for m in warnings)
    assert sorted(_manifest(art_dir)["files"]) == ["sam_cap.png"]
    assert (art_dir / "diploma.png").read_bytes() == b"this is not a png"  # unusable files are left alone

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOG):
        seeded = _seed(mdb)
    authored = {p.id: p for p in demo.load_props()}
    for prop_id in ("diploma", "lectern", "kitchen_table", "kitchen_chair", "green_sedan", "road_map", "peaches",
                    "tamale_pot"):
        assert _prop(seeded, prop_id) == authored[prop_id]
    assert _set(seeded, "coast_road_1972") == next(s for s in demo.load_sets() if s.id == "coast_road_1972")
    assert _prop(seeded, "sam_cap").art["body"] == media_db.media_ref(demo.demo_art_media_id("sam_cap", "body"))
    assert seeded[media_db.INLINE].count_documents({"metadata.kind": "cutout"}) == 1
    assert _warnings(caplog)  # the seed names the files it skipped too


def test_a_result_over_the_media_limit_is_skipped(art_dir: Path, caplog, monkeypatch):
    monkeypatch.setattr(media_db, "MAX_BYTES", 100)
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    with caplog.at_level(logging.WARNING, logger=LOG):
        result = _bake(art_dir)
    assert result.skipped == ["diploma.png"]
    assert _load(art_dir) == (demo.load_props(), demo.load_sets(), ())
    assert any("art/diploma.png" in m and "media limit" in m for m in _warnings(caplog))


def test_a_second_file_for_the_same_slot_is_skipped(art_dir: Path, caplog):
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    (art_dir / "diploma.body.png").write_bytes(cutout_png((100, 100), (0, 0, 100, 100)))
    with caplog.at_level(logging.WARNING, logger=LOG):
        result = _bake(art_dir)
    assert result.baked == ["diploma.body.png"] and result.skipped == ["diploma.png"]  # first in sorted order
    props, _sets, art = _load(art_dir)
    assert [f.filename for f in art] == ["diploma.body.png"]
    assert next(p for p in props if p.id == "diploma").aspect == _authored("diploma").aspect
    assert any("art/diploma.png" in m and "diploma.body.png already" in m for m in _warnings(caplog))


def test_a_lopsided_body_cannot_move_the_anchor(art_dir: Path, mdb):
    # tamale_pot: Carmen sets it on the table at arm's length. This shape touches the ground
    # only at its far left; measured, its anchor would move her reach out of range. The rig
    # stays as authored, so the scene compiles and stages as authored.
    im = Image.new("RGBA", (109, 100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, 108, 94), fill=(90, 90, 90, 255))
    draw.rectangle((0, 95, 4, 99), fill=(90, 90, 90, 255))
    (art_dir / "tamale_pot.png").write_bytes(_png(im))
    (art_dir / "tamale_pot.lid.png").write_bytes(cutout_png((60, 20), (0, 0, 60, 20)))
    assert _bake(art_dir).ok

    seeded = _seed(mdb)
    pot = _prop(seeded, "tamale_pot")
    _only_art_differs(pot, _authored("tamale_pot"))
    assert pot.art["body"] == media_db.media_ref(demo.demo_art_media_id("tamale_pot", "body"))
    assert pot.art["lid"] == media_db.media_ref(demo.demo_art_media_id("tamale_pot", "lid"))


# ----------------------------------------------------------------------------- links

def test_links_are_skipped_by_the_bake_and_the_seed(art_dir: Path, tmp_path: Path, caplog):
    outside = tmp_path / "outside.png"
    outside.write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    (art_dir / "diploma.png").symlink_to(outside)
    (art_dir / "sam_cap.png").write_bytes(cutout_png((140, 100), (0, 0, 140, 100)))

    with caplog.at_level(logging.WARNING, logger=LOG):
        result = _bake(art_dir)
    assert result.baked == ["sam_cap.png"] and result.skipped == ["diploma.png"] and not result.ok
    assert outside.read_bytes() == cutout_png((150, 100), (0, 0, 150, 100))  # never rewritten
    assert len([m for m in _warnings(caplog) if "art/diploma.png" in m and "link" in m]) == 1

    # Even with a manifest entry that matches the target's bytes, the seed never reads it.
    manifest = _manifest(art_dir)
    manifest["files"]["diploma.png"] = {"prop": "diploma", "artKey": "body", "width": 150, "height": 100,
                                        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest()}
    (art_dir / demo.MANIFEST_NAME).write_text(json.dumps(manifest))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOG):
        _props, _sets, art = _load(art_dir)
    assert [f.filename for f in art] == ["sam_cap.png"]
    assert any("art/diploma.png" in m and "link" in m for m in _warnings(caplog))


def test_a_scene_that_fails_without_art_still_raises(art_dir: Path, monkeypatch):
    def broken(screenplay, **_kw):
        raise demo.DemoCompileError(screenplay.memory_id, [])

    monkeypatch.setattr(demo, "compile_moment", broken)
    with pytest.raises(demo.DemoCompileError):
        demo.load_demo_data()


# ----------------------------------------------------------------------------------- ids

def test_ids_are_fixed_and_apart_from_the_other_demo_ids():
    a = demo.demo_art_media_id("green_sedan", "wheel")
    assert a == demo.demo_art_media_id("green_sedan", "wheel")
    assert media_db.parse_media_ref(a) == a
    assert a.startswith("5eed0003")
    ids = {demo.demo_art_media_id(p.id, key) for p in demo.load_props() for key in p.art}
    ids |= {demo.demo_art_media_id(s.id, name) for s in demo.load_sets() for name in ("back", "middle", "front")}
    assert len(ids) == sum(len(p.art) for p in demo.load_props()) + 3 * len(demo.load_sets())
    assert not ids & set(demo.DEMO_PHOTO_IDS) and not ids & set(demo.DEMO_QUESTION_IDS)


def test_ids_are_identical_across_two_builds(art_dir: Path, mdb):
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    (art_dir / "red_bike.wheel.png").write_bytes(cutout_png((50, 50), (0, 0, 50, 50)))
    (art_dir / "school_hall_2015.back.png").write_bytes(opaque_png((512, 256)))
    _bake(art_dir)

    def snapshot(db):
        return (
            sorted(str(d["_id"]) for d in db[media_db.INLINE].find({})),
            {pid: _prop(db, pid).art for pid in ("diploma", "red_bike")},
            [layer.art for layer in _set(db, "school_hall_2015").layers],
        )

    first = snapshot(_seed(mdb))
    second = snapshot(_seed(mdb))  # reset=True: purge and rebuild
    assert first == second
    assert len(first[0]) == 5  # two photos and three art files


# ---------------------------------------------------------------------------------- CLI

def test_the_bake_cli_reports_and_exits_by_outcome(art_dir: Path, monkeypatch, capsys):
    monkeypatch.setattr(demo, "ART_DIR", art_dir)
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    assert demo.main(["--bake-art"]) == demo.EXIT_OK
    assert "Baked 1 demo art file(s)" in capsys.readouterr().out
    (art_dir / "no_such_prop.png").write_bytes(opaque_png((10, 10)))
    assert demo.main(["--bake-art"]) == demo.EXIT_BAD_DATA
    assert "no_such_prop.png" in capsys.readouterr().err
