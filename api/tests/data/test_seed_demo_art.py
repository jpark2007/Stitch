"""seed/demo_family/art/: hand-made prop and set images picked up by the demo seed.

Every test uses small generated PNGs in a tmp folder (demo.ART_DIR monkeypatched), never
the real folder, and the in-memory database.
"""

from __future__ import annotations

import io
import logging
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


def _seed(mdb):
    db_client.ensure_indexes(mdb)
    seed_demo(mdb, reset=True)
    return mdb


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


# ------------------------------------------------------------------------ empty folder

def test_the_art_folder_is_in_the_demo_data_and_has_its_naming_rules():
    assert demo.ART_DIR == demo.DATA_DIR / "art"
    assert "<propId>.<artKey>.png" in (demo.ART_DIR / "README.md").read_text()


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


def test_an_empty_folder_seeds_exactly_as_today(art_dir: Path, mdb, monkeypatch: pytest.MonkeyPatch):
    seeded = _seed(mdb)
    for prop in demo.load_props():
        assert _prop(seeded, prop.id) == prop
    for set_def in demo.load_sets():
        assert _set(seeded, set_def.id) == set_def
    kinds = sorted(d["metadata"]["kind"] for d in seeded[media_db.INLINE].find({}))
    assert kinds == ["photo", "photo"]


# -------------------------------------------------------------------------------- props

def test_a_prop_body_sets_art_aspect_and_anchor(art_dir: Path, mdb):
    # diploma is authored at aspect 1.6; this drawing is 150x100 inside a larger canvas.
    (art_dir / "diploma.png").write_bytes(cutout_png((300, 240), (60, 40, 210, 140)))
    seeded = _seed(mdb)

    prop = _prop(seeded, "diploma")
    ref = demo.media_db.media_ref(demo.demo_art_media_id("diploma", "body"))
    assert prop.art == {"body": ref}
    assert prop.aspect == pytest.approx(1.5)
    assert prop.anchor == pytest.approx([0.5, 1.0])

    stored = _stored(seeded, ref)
    assert stored.kind == "cutout" and stored.content_type == "image/png"
    assert stored.filename == "diploma.png"
    assert rgba(stored.data).size == (150, 100)  # cropped to the shape


def test_a_white_background_body_is_cut_out_like_studio_art(art_dir: Path, mdb, caplog):
    (art_dir / "diploma.png").write_bytes(white_png((300, 200), (30, 50, 270, 200)))
    with caplog.at_level(logging.WARNING, logger=LOG):
        seeded = _seed(mdb)
    prop = _prop(seeded, "diploma")
    assert prop.aspect == pytest.approx(1.6)
    im = rgba(_stored(seeded, prop.art["body"]).data)
    assert im.size == (240, 150)
    assert im.getchannel("A").getextrema()[1] == 255
    # The white background costs a flood fill on every start: one warning says how to avoid it.
    assert any("white background" in m and "diploma.png" in m and "--cut-out-art" in m
               for m in _warnings(caplog))


def test_a_wide_body_is_clamped_like_a_photo_swap(art_dir: Path, caplog):
    (art_dir / "diploma.png").write_bytes(cutout_png((400, 100), (0, 0, 400, 100)))
    with caplog.at_level(logging.WARNING, logger=LOG):
        props, _sets, _art = demo.load_demo_art(demo.load_props(), demo.load_sets(), art_dir)
    diploma = next(p for p in props if p.id == "diploma")
    assert diploma.aspect == props_db.MAX_PHOTO_ASPECT
    assert any("diploma.png" in m and "clamped" in m for m in _warnings(caplog))


def test_the_body_is_fetchable_by_a_member_and_404_across_families(art_dir: Path, mdb):
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    seeded = _seed(mdb)
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
        assert rgba(res.content).size == (150, 100)

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
    seeded = _seed(mdb)
    sedan = _prop(seeded, "green_sedan")
    authored = next(p for p in demo.load_props() if p.id == "green_sedan")
    assert sedan.art == {"body": None, "wheel": media_db.media_ref(demo.demo_art_media_id("green_sedan", "wheel"))}
    assert sedan.parts == authored.parts
    assert (sedan.aspect, sedan.anchor) == (authored.aspect, authored.anchor)  # only a body sets the rig
    assert rgba(_stored(seeded, sedan.art["wheel"]).data).size == (100, 100)


def test_a_state_key_file_fills_the_art_the_state_switches_to(art_dir: Path, mdb):
    # black_umbrella: "states": {"open": {"body": "open"}} -> its open look is the `open` key.
    (art_dir / "black_umbrella.open.png").write_bytes(cutout_png((110, 100), (0, 0, 110, 100)))
    seeded = _seed(mdb)
    umbrella = _prop(seeded, "black_umbrella")
    assert umbrella.art["open"] == media_db.media_ref(demo.demo_art_media_id("black_umbrella", "open"))
    assert umbrella.art["body"] is None
    assert umbrella.states == {"closed": {}, "open": {"body": "open"}}
    assert _stored(seeded, umbrella.art["open"]).kind == "cutout"


# --------------------------------------------------------------------------------- sets

def test_each_layer_depth_gets_its_file(art_dir: Path, mdb):
    authored = next(s for s in demo.load_sets() if s.id == "coast_road_1972")
    heights = {int(layer.depth): layer.height for layer in authored.layers}
    (art_dir / "coast_road_1972.back.png").write_bytes(opaque_png((1024, 512)))
    (art_dir / "coast_road_1972.middle.png").write_bytes(white_png((1024, 200), (0, 120, 1024, 200)))
    (art_dir / "coast_road_1972.front.png").write_bytes(cutout_png((2048, 100), (0, 40, 2048, 100)))
    seeded = _seed(mdb)

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
    _props, _sets, art = demo.load_demo_art(demo.load_props(), demo.load_sets(), art_dir)
    assert len(art) == 1
    assert rgba(art[0].png).getchannel("A").getextrema() == (255, 255)


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
    "peaches.jpg": opaque_png((10, 10)),  # not .png
    "road_map.png": opaque_png((300, 300)),  # over the (lowered) pixel cap
}


def test_bad_files_are_skipped_with_a_warning_and_the_seed_goes_on(art_dir: Path, mdb, caplog, monkeypatch):
    monkeypatch.setattr(demo, "DEMO_ART_MAX_PIXELS", 50_000)
    good = cutout_png((150, 100), (0, 0, 150, 100))
    for name, data in BAD_FILES.items():
        if data is None:
            data = good[: len(good) // 2]
        (art_dir / name).write_bytes(data)
    (art_dir / "sam_cap.png").write_bytes(cutout_png((140, 100), (0, 0, 140, 100)))  # one good file

    with caplog.at_level(logging.WARNING, logger=LOG):
        seeded = _seed(mdb)

    warnings = _warnings(caplog)
    for name in BAD_FILES:
        assert any(f"art/{name}" in m and "placeholder stays" in m for m in warnings), (name, warnings)
    assert not any("README.md" in m for m in warnings)
    assert any("road_map.png" in m and "pixel cap" in m for m in warnings)
    assert any("green_sedan.door.png" in m and "no art key 'door'" in m for m in warnings)

    authored = {p.id: p for p in demo.load_props()}
    for prop_id in ("diploma", "lectern", "kitchen_table", "kitchen_chair", "green_sedan", "road_map", "peaches"):
        assert _prop(seeded, prop_id) == authored[prop_id]
    assert _set(seeded, "coast_road_1972") == next(s for s in demo.load_sets() if s.id == "coast_road_1972")
    assert _prop(seeded, "sam_cap").art["body"] == media_db.media_ref(demo.demo_art_media_id("sam_cap", "body"))
    assert seeded[media_db.INLINE].count_documents({"metadata.kind": "cutout"}) == 1


def test_a_result_over_the_media_limit_is_skipped(art_dir: Path, caplog, monkeypatch):
    monkeypatch.setattr(media_db, "MAX_BYTES", 100)
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    with caplog.at_level(logging.WARNING, logger=LOG):
        props, _sets, art = demo.load_demo_art(demo.load_props(), demo.load_sets(), art_dir)
    assert art == ()
    assert props == demo.load_props()
    assert any("art/diploma.png" in m and "media limit" in m for m in _warnings(caplog))


def test_a_second_file_for_the_same_slot_is_skipped(art_dir: Path, caplog):
    (art_dir / "diploma.png").write_bytes(cutout_png((150, 100), (0, 0, 150, 100)))
    (art_dir / "diploma.body.png").write_bytes(cutout_png((100, 100), (0, 0, 100, 100)))
    with caplog.at_level(logging.WARNING, logger=LOG):
        props, _sets, art = demo.load_demo_art(demo.load_props(), demo.load_sets(), art_dir)
    assert [f.filename for f in art] == ["diploma.body.png"]  # first in sorted order
    assert next(p for p in props if p.id == "diploma").aspect == pytest.approx(1.0)
    assert any("art/diploma.png" in m and "diploma.body.png already" in m for m in _warnings(caplog))


def test_a_body_that_would_stop_a_scene_compiling_keeps_the_placeholder(art_dir: Path, mdb, caplog):
    # tamale_pot: Carmen sets it on the table at arm's length. A shape whose bottom touches
    # the ground only at its far left moves the anchor, and with it where her hand must reach.
    im = Image.new("RGBA", (109, 100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, 108, 94), fill=(90, 90, 90, 255))
    draw.rectangle((0, 95, 4, 99), fill=(90, 90, 90, 255))
    (art_dir / "tamale_pot.png").write_bytes(_png(im))
    (art_dir / "tamale_pot.lid.png").write_bytes(cutout_png((60, 20), (0, 0, 60, 20)))

    with caplog.at_level(logging.WARNING, logger=LOG):
        seeded = _seed(mdb)

    authored = next(p for p in demo.load_props() if p.id == "tamale_pot")
    pot = _prop(seeded, "tamale_pot")
    assert pot.art["body"] is None
    assert (pot.aspect, pot.anchor) == (authored.aspect, authored.anchor)
    assert pot.art["lid"] == media_db.media_ref(demo.demo_art_media_id("tamale_pot", "lid"))  # the part stays
    assert media_db.get_media(seeded, DEMO_FAMILY_ID, demo.demo_art_media_id("tamale_pot", "body")) is None
    assert any("art/tamale_pot.png" in m and "demo_sunday_tamales" in m and "does not compile" in m
               for m in _warnings(caplog))


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


# ------------------------------------------------------------------------- --cut-out-art

def test_cut_out_art_saves_white_backgrounds_with_transparency(art_dir: Path):
    white = white_png((200, 100), (20, 20, 180, 100))
    ready = cutout_png((100, 100), (10, 10, 90, 100))
    back = opaque_png((512, 256))
    (art_dir / "diploma.png").write_bytes(white)
    (art_dir / "sam_cap.png").write_bytes(ready)
    (art_dir / "school_hall_2015.back.png").write_bytes(back)
    (art_dir / "school_hall_2015.front.png").write_bytes(white_png((512, 64), (0, 32, 512, 64)))
    (art_dir / "nobody.png").write_bytes(white)

    assert demo.cut_out_demo_art(art_dir) == ["diploma.png", "school_hall_2015.front.png"]
    assert rgba((art_dir / "diploma.png").read_bytes()).getchannel("A").getextrema() == (0, 255)
    assert (art_dir / "sam_cap.png").read_bytes() == ready
    assert (art_dir / "school_hall_2015.back.png").read_bytes() == back
    assert (art_dir / "nobody.png").read_bytes() == white
    assert demo.cut_out_demo_art(art_dir) == []  # nothing left to do
    assert sorted(p.name for p in art_dir.iterdir() if p.suffix == ".tmp") == []
