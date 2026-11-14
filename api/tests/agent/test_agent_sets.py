"""Set tools: find_set and generate_set_layer (layers, heights, marks, ids, art and placeholders)."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from PIL import Image

import schemas
from agent.fake_compiler import FakeCompiler
from agent.repo import SAMPLE_MEMORY_PATH, InMemoryRepo, load_sample_family
from agent.run_info import RunInfo
from agent.tools.art import (
    STYLE_PROMPT,
    ArtService,
    FakeBackgroundRemover,
    FakeImageModel,
    placeholder_color,
)
from agent.tools.compile import SceneInputs, load_compiler
from agent.tools.sets import (
    LAYER_HEIGHT,
    NO_MATCH,
    era_label,
    layer_size,
    make_set_tools,
    slugify,
    validate_marks,
)
from schemas import MemoryResponse, SetDef, SubmitMemoryFields
from screenplay.schema import Screenplay

FAM = "fam_mao"
OTHER = "fam_other"
PLACE = "Corner bakery"
SAMPLE_MARKS = {"steps_left": [0.2, 0.0], "steps_foot": [0.5, 0.0], "steps_top": [0.6, 0.3], "curb": [0.9, 0.0]}
EXPECTED_HEIGHTS = {0: 1.0, 1: 0.45, 2: 0.18}


class SetsRepo(InMemoryRepo):
    """InMemoryRepo plus find_sets (AgentRepo gains it in the mongo-repo task)."""

    def __init__(self) -> None:
        super().__init__()
        self.find_calls: list[tuple[str, str, int]] = []

    def find_sets(self, family_id: str, text: str, *, limit: int = 5) -> list[SetDef]:
        self.find_calls.append((family_id, text, limit))
        words = set(re.findall(r"[a-z0-9]+", text.lower()))
        hits = []
        for set_def in self.list_sets(family_id):
            hay = set(re.findall(r"[a-z0-9]+", f"{set_def.name} {set_def.description or ''}".lower()))
            if words & hay:
                hits.append(set_def)
        return hits[:limit]


class RecordingImageModel(FakeImageModel):
    """FakeImageModel that also records the size and reference images of each call."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[tuple[tuple[int, int], tuple[bytes, ...]]] = []

    def generate(self, prompt: str, *, reference_images: Sequence[bytes] = (), size=(1024, 1024)) -> bytes:
        self.requests.append((size, tuple(reference_images)))
        return super().generate(prompt, reference_images=reference_images, size=size)


def _photo_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 120, 90)).save(buf, format="PNG")
    return buf.getvalue()


def make_repo() -> SetsRepo:
    repo = SetsRepo()
    load_sample_family(repo, FAM)
    load_sample_family(repo, OTHER)
    return repo


def make_run(photo_ids: tuple[str, ...] = (), year: int = 1988) -> RunInfo:
    return RunInfo(
        run_id="run_1",
        family_id=FAM,
        user_id="user_dad",
        author_id="dad",
        fields=SubmitMemoryFields(text="We bought bread at the corner bakery.", year=year),
        photo_ids=photo_ids,
    )


def tools_for(repo: SetsRepo, run: RunInfo | None = None, art: ArtService | None = None) -> dict[str, Any]:
    tools = make_set_tools(repo, run or make_run(), art or ArtService(None, None))
    return {t.name: t for t in tools}


def gen(tools: dict[str, Any], layer: str, place: str = PLACE, description: str | None = None, **kw: Any) -> str:
    args = {"place": place, "layer": layer, "description": description or f"{layer} of the bakery", **kw}
    return tools["generate_set_layer"].invoke(args)


def gen_ok(tools: dict[str, Any], layer: str, **kw: Any) -> dict[str, Any]:
    out = gen(tools, layer, **kw)
    assert not out.startswith("error"), out
    return json.loads(out)


def png_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        return im.size


# ------------------------------------------------------------------- the factory


def test_tool_names_and_no_family_id_in_any_schema() -> None:
    tools = make_set_tools(make_repo(), make_run(), ArtService(None, None))
    assert [t.name for t in tools] == ["find_set", "generate_set_layer"]
    for t in tools:
        assert not any("family" in name.lower() for name in t.args), t.name
        assert t.description.strip()
    params = set(tools[1].args)
    assert params == {"place", "layer", "description", "marks", "reference_photo_id"}
    assert set(tools[0].args) == {"place"}


# ------------------------------------------------------------- placeholder path


def test_three_placeholder_layers_make_one_valid_set_with_marks() -> None:
    repo = make_repo()
    before = {s.id for s in repo.list_sets(FAM)}
    tools = tools_for(repo)

    first = gen_ok(tools, "back", description="cream plaster wall with a striped awning")
    second = gen_ok(tools, "middle", description="glass shop counter with bread loaves", marks=SAMPLE_MARKS)
    third = gen_ok(tools, "front", description="low wooden crates of apples")

    set_id = first["setId"]
    assert set_id == "corner_bakery"
    assert second["setId"] == third["setId"] == set_id
    assert {first["art"], second["art"], third["art"]} == {"placeholder"}
    assert first["name"] == PLACE

    new_ids = {s.id for s in repo.list_sets(FAM)} - before
    assert new_ids == {set_id}

    saved = repo.get_set(FAM, set_id)
    assert saved is not None
    assert saved.family_id == FAM
    assert saved.name == PLACE
    assert [layer.depth for layer in saved.layers] == [0, 1, 2]
    for layer, name in zip(saved.layers, ["back", "middle", "front"], strict=True):
        assert layer.art is None
        assert layer.height == EXPECTED_HEIGHTS[int(layer.depth)] == LAYER_HEIGHT[name]
        assert layer.color == placeholder_color(f"{PLACE} {name}")
        assert re.fullmatch(r"#[0-9a-f]{6}", layer.color or "")
    assert saved.marks == SAMPLE_MARKS
    assert "striped awning" in (saved.description or "")
    assert "crates of apples" in (saved.description or "")

    # It round-trips through the contract model, camelCase and all.
    again = SetDef.model_validate(saved.model_dump(mode="json", by_alias=True))
    assert again == saved
    assert third["layers"] == [
        {"depth": 0, "height": 1.0, "art": "placeholder"},
        {"depth": 1, "height": 0.45, "art": "placeholder"},
        {"depth": 2, "height": 0.18, "art": "placeholder"},
    ]
    assert third["marks"] == SAMPLE_MARKS


def test_heights_follow_the_reference_sample() -> None:
    sample = MemoryResponse.model_validate_json(SAMPLE_MEMORY_PATH.read_text())
    heights = {int(layer.depth): layer.height for layer in sample.sets[0].layers}
    assert heights == EXPECTED_HEIGHTS


def test_marks_from_every_call_are_merged_and_a_repeated_mark_is_replaced() -> None:
    repo = make_repo()
    tools = tools_for(repo)
    gen_ok(tools, "back", marks={"door": [0.3, 0.0]})
    gen_ok(tools, "middle", marks={"counter": [0.6, 0.0], "door": [0.25, 0.0, 0.4]})
    out = gen_ok(tools, "front")
    assert out["marks"] == {"door": [0.25, 0.0, 0.4], "counter": [0.6, 0.0]}
    assert repo.get_set(FAM, out["setId"]).marks == out["marks"]


def test_layers_stay_sorted_by_depth_whatever_the_call_order() -> None:
    repo = make_repo()
    tools = tools_for(repo)
    for layer in ("front", "back", "middle"):
        out = gen_ok(tools, layer)
    assert [la["depth"] for la in out["layers"]] == [0, 1, 2]
    assert [la.depth for la in repo.get_set(FAM, out["setId"]).layers] == [0, 1, 2]


def test_parallel_calls_for_one_place_build_one_set() -> None:
    repo = make_repo()
    before = {s.id for s in repo.list_sets(FAM)}
    tools = tools_for(repo, art=ArtService(FakeImageModel(), FakeBackgroundRemover()))
    with ThreadPoolExecutor(max_workers=3) as pool:
        outs = list(pool.map(lambda layer: gen_ok(tools, layer), ["back", "middle", "front"]))
    assert len({o["setId"] for o in outs}) == 1
    new = {s.id for s in repo.list_sets(FAM)} - before
    assert new == {"corner_bakery"}
    assert [la.depth for la in repo.get_set(FAM, "corner_bakery").layers] == [0, 1, 2]


# ---------------------------------------------------------------------- fake art


def test_fake_art_layers_are_stored_and_the_back_layer_is_full_bleed() -> None:
    repo = make_repo()
    model = RecordingImageModel()
    tools = tools_for(repo, art=ArtService(model, FakeBackgroundRemover()))

    outs = [gen_ok(tools, layer) for layer in ("back", "middle", "front")]
    assert [o["art"] for o in outs] == ["generated"] * 3
    assert all(la["art"] == "art" for la in outs[-1]["layers"])

    saved = repo.get_set(FAM, outs[0]["setId"])
    sizes = {}
    for layer in saved.layers:
        assert layer.art is not None
        assert layer.color is None
        stored = repo.get_media(FAM, layer.art)
        assert stored is not None
        data, content_type = stored
        assert content_type == "image/png"
        sizes[int(layer.depth)] = png_size(data)
        # Another family cannot read it.
        assert repo.get_media(OTHER, layer.art) is None

    # width / height = 2 / layer height, rounded to 16 px.
    assert sizes == {0: (2048, 1024), 1: (2048, 464), 2: (2048, 192)}
    assert [size for size, _ in model.requests] == [(2048, 1024), (2048, 464), (2048, 192)]

    back_data, _ = repo.get_media(FAM, saved.layers[0].art)
    with Image.open(io.BytesIO(back_data)) as im:
        alpha = im.convert("RGBA").getchannel("A")
        assert alpha.getextrema() == (255, 255), "the back layer keeps its background"
    mid_data, _ = repo.get_media(FAM, saved.layers[1].art)
    with Image.open(io.BytesIO(mid_data)) as im:
        assert im.convert("RGBA").getchannel("A").getextrema()[0] == 0, "middle has its white removed"


def test_art_prompt_uses_the_locked_style_layer_framing_and_era() -> None:
    model = RecordingImageModel()
    tools = tools_for(make_repo(), art=ArtService(model, FakeBackgroundRemover()))
    gen_ok(tools, "middle", description="glass shop counter with bread loaves")
    prompt = model.calls[-1]
    assert prompt.startswith(STYLE_PROMPT)
    assert "middle layer of a set" in prompt
    assert 'Subject: "glass shop counter with bread loaves"' in prompt
    assert "1980s" in prompt and "1988" in prompt


def test_back_layer_needs_only_the_image_model() -> None:
    repo = make_repo()
    tools = tools_for(repo, art=ArtService(FakeImageModel(), None))
    assert gen_ok(tools, "back")["art"] == "generated"
    assert gen_ok(tools, "front")["art"] == "placeholder"
    layers = repo.get_set(FAM, "corner_bakery").layers
    assert layers[0].art is not None and layers[1].art is None and layers[1].color


def test_failed_generation_falls_back_to_a_placeholder() -> None:
    class Broken:
        def generate(self, *a: Any, **k: Any) -> bytes:
            raise RuntimeError("service down")

    repo = make_repo()
    tools = tools_for(repo, art=ArtService(Broken(), FakeBackgroundRemover()))
    out = gen_ok(tools, "back")
    assert out["art"] == "placeholder"
    layer = repo.get_set(FAM, out["setId"]).layers[0]
    assert layer.art is None and layer.color == placeholder_color(f"{PLACE} back")


def test_regenerating_a_layer_replaces_it() -> None:
    repo = make_repo()
    tools = tools_for(repo, art=ArtService(FakeImageModel(), FakeBackgroundRemover()))
    gen_ok(tools, "back")
    gen_ok(tools, "middle", description="a plain wooden counter")
    first_art = repo.get_set(FAM, "corner_bakery").layers[1].art
    out = gen_ok(tools, "middle", description="a glass counter full of bread")
    saved = repo.get_set(FAM, "corner_bakery")
    assert [la.depth for la in saved.layers] == [0, 1]
    assert len(out["layers"]) == 2
    assert saved.layers[1].art not in (None, first_art)
    assert "glass counter" in (saved.description or "")
    assert "plain wooden counter" not in (saved.description or "")


# ------------------------------------------------------------------------- ids


def test_a_saved_place_is_never_overwritten() -> None:
    repo = make_repo()
    original = repo.get_set(FAM, "church_steps_1988")
    assert original is not None
    tools = tools_for(repo)

    out = gen_ok(tools, "back", place="Church steps 1988", marks={"door": [0.5, 0.0]})
    assert out["setId"] == "church_steps_1988_2"
    gen_ok(tools, "middle", place="Church steps 1988")
    assert repo.get_set(FAM, "church_steps_1988") == original
    made = repo.get_set(FAM, "church_steps_1988_2")
    assert [la.depth for la in made.layers] == [0, 1]
    assert made.marks == {"door": [0.5, 0.0]}


def test_a_second_run_never_overwrites_the_first_runs_set() -> None:
    repo = make_repo()
    gen_ok(tools_for(repo), "back")
    first = repo.get_set(FAM, "corner_bakery")
    out = gen_ok(tools_for(repo, make_run()), "back", description="a different wall")
    assert out["setId"] == "corner_bakery_2"
    assert repo.get_set(FAM, "corner_bakery") == first
    out3 = gen_ok(tools_for(repo, make_run()), "back")
    assert out3["setId"] == "corner_bakery_3"


def test_another_familys_set_with_the_same_id_does_not_force_a_suffix() -> None:
    repo = make_repo()
    gen_ok(tools_for(repo, RunInfo(**{**make_run().__dict__, "family_id": OTHER})), "back")
    assert repo.get_set(OTHER, "corner_bakery") is not None
    assert gen_ok(tools_for(repo), "back")["setId"] == "corner_bakery"


def test_slugify() -> None:
    assert slugify("St. Mary's Church Steps") == "st_marys_church_steps"
    assert slugify("  Grandma’s   kitchen!! ") == "grandmas_kitchen"
    assert slugify("Café Señora") == "cafe_senora"
    assert slugify("!!!") == "place"
    long = slugify("the very long winding gravel road up to the old farmhouse on the hill")
    assert len(long) <= 40 and not long.endswith("_")
    assert re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", long)


def test_suffixed_ids_stay_within_forty_characters() -> None:
    repo = make_repo()
    place = "the very long winding gravel road up to the old farmhouse on the hill"
    first = gen_ok(tools_for(repo), "back", place=place)["setId"]
    second = gen_ok(tools_for(repo, make_run()), "back", place=place)["setId"]
    assert len(first) <= 40 and len(second) <= 40
    assert second.endswith("_2") and second != first


def test_layer_size_and_era_label() -> None:
    assert layer_size(1.0) == (2048, 1024)
    assert layer_size(0.45) == (2048, 464)
    assert layer_size(0.18) == (2048, 192)
    assert all(layer_size(h)[1] % 16 == 0 for h in (0.05, 0.33, 0.7, 1.2))
    assert era_label(1988) == "1988, the 1980s"
    assert era_label(1932) == "1932, before 1940"


# ----------------------------------------------------------------------- marks


@pytest.mark.parametrize(
    ("marks", "needle"),
    [
        ({"StepsTop": [0.6, 0.3]}, "snake_case"),
        ({"steps-top": [0.6, 0.3]}, "snake_case"),
        ({"_top": [0.6, 0.3]}, "snake_case"),
        ({"offstage_left": [0.0, 0.0]}, "built in"),
        ({"offstage_right": [1.0, 0.0]}, "built in"),
        ({"top": [0.6]}, "[u, h]"),
        ({"top": [0.6, 0.3, 0.5, 0.1]}, "[u, h]"),
        ({"top": [1.2, 0.3]}, "u=1.2"),
        ({"top": [-0.1, 0.3]}, "u=-0.1"),
        ({"top": [0.6, 1.6]}, "h=1.6"),
        ({"top": [0.6, -0.2]}, "h=-0.2"),
        ({"top": [0.6, 0.3, 1.5]}, "d=1.5"),
        ({"ok": [0.5, 0.0], "bad": [0.5, 9.0]}, "h=9"),
    ],
)
def test_a_bad_mark_saves_nothing(marks: dict[str, Any], needle: str) -> None:
    repo = make_repo()
    tools = tools_for(repo, art=ArtService(FakeImageModel(), FakeBackgroundRemover()))
    gen_ok(tools, "back", marks={"door": [0.3, 0.0]})
    before = repo.get_set(FAM, "corner_bakery")
    before_sets = repo.list_sets(FAM)

    out = gen(tools, "middle", marks=marks)
    assert out.startswith("error"), out
    assert needle in out
    assert "nothing was saved" in out
    assert repo.get_set(FAM, "corner_bakery") == before
    assert repo.list_sets(FAM) == before_sets


def test_a_bad_mark_on_the_first_call_creates_no_set() -> None:
    repo = make_repo()
    model = RecordingImageModel()
    tools = tools_for(repo, art=ArtService(model, FakeBackgroundRemover()))
    before = repo.list_sets(FAM)
    assert gen(tools, "back", marks={"offstage_left": [0.0, 0.0]}).startswith("error")
    assert repo.list_sets(FAM) == before
    assert model.requests == [], "no image is generated for a rejected call"
    # The id was not used up either.
    assert gen_ok(tools, "back")["setId"] == "corner_bakery"


def test_validate_marks_rejects_non_finite_and_bool_values() -> None:
    assert validate_marks({"a": [float("nan"), 0.0]})[1]
    assert validate_marks({"a": [True, 0.0]})[1]
    assert validate_marks({"a": "0.5, 0"})[1]
    clean, problems = validate_marks({"a": [0, 1.5, 1]})
    assert problems == [] and clean == {"a": [0.0, 1.5, 1.0]}


# ---------------------------------------------------------------------- photos


def test_a_foreign_reference_photo_is_refused_and_nothing_is_saved() -> None:
    repo = make_repo()
    foreign = repo.put_media(OTHER, _photo_png(), "image/png", kind="photo")
    other_run_photo = repo.put_media(FAM, _photo_png(), "image/png", kind="photo")
    model = RecordingImageModel()
    tools = tools_for(repo, art=ArtService(model, FakeBackgroundRemover()))
    before = repo.list_sets(FAM)
    for photo_id in (foreign, other_run_photo, "media/999"):
        out = gen(tools, "back", reference_photo_id=photo_id)
        assert out.startswith("error"), out
        assert "nothing was saved" in out
    assert repo.list_sets(FAM) == before
    assert model.requests == []


def test_the_runs_own_photo_is_passed_as_reference() -> None:
    repo = make_repo()
    photo = _photo_png()
    photo_id = repo.put_media(FAM, photo, "image/png", kind="photo")
    model = RecordingImageModel()
    tools = tools_for(repo, make_run((photo_id,)), ArtService(model, FakeBackgroundRemover()))
    out = gen_ok(tools, "back", reference_photo_id=photo_id)
    assert out["art"] == "generated"
    assert model.requests[-1][1] == (photo,)


def test_a_run_photo_that_is_not_an_image_is_refused() -> None:
    repo = make_repo()
    audio_id = repo.put_media(FAM, b"ID3", "audio/mpeg", kind="audio")
    tools = tools_for(repo, make_run((audio_id,)))
    out = gen(tools, "back", reference_photo_id=audio_id)
    assert out.startswith("error") and "not an image" in out
    assert repo.get_set(FAM, "corner_bakery") is None


# -------------------------------------------------------------------- find_set


def test_find_set_returns_matches_with_layers_and_marks() -> None:
    repo = make_repo()
    out = tools_for(repo)["find_set"].invoke({"place": "the church steps"})
    found = json.loads(out)
    assert [s["id"] for s in found] == ["church_steps_1988"]
    church = found[0]
    assert church["name"] == "St. Mary's church steps"
    assert church["description"]
    assert church["layers"] == [
        {"depth": 0, "height": 1.0, "art": "placeholder"},
        {"depth": 1, "height": 0.45, "art": "placeholder"},
        {"depth": 2, "height": 0.18, "art": "placeholder"},
    ]
    assert church["marks"] == SAMPLE_MARKS
    assert repo.find_calls == [(FAM, "the church steps", 5)]


def test_find_set_with_no_match() -> None:
    assert tools_for(make_repo())["find_set"].invoke({"place": "lighthouse"}) == NO_MATCH


def test_another_familys_sets_are_invisible_to_find_set() -> None:
    repo = SetsRepo()
    load_sample_family(repo, OTHER)
    out = tools_for(repo)["find_set"].invoke({"place": "church steps"})
    assert out == NO_MATCH
    assert all(call[0] == FAM for call in repo.find_calls)


def test_find_set_sees_a_set_generated_earlier_in_the_run() -> None:
    repo = make_repo()
    tools = tools_for(repo, art=ArtService(FakeImageModel(), FakeBackgroundRemover()))
    gen_ok(tools, "back")
    found = json.loads(tools["find_set"].invoke({"place": "corner bakery"}))
    assert found[0]["id"] == "corner_bakery"
    assert found[0]["layers"] == [{"depth": 0, "height": 1.0, "art": "art"}]


def test_find_set_falls_back_to_list_sets_when_the_repo_has_no_find_sets() -> None:
    repo = InMemoryRepo()
    load_sample_family(repo, FAM)
    load_sample_family(repo, OTHER)
    tools = {t.name: t for t in make_set_tools(repo, make_run(), ArtService(None, None))}
    assert json.loads(tools["find_set"].invoke({"place": "church steps"}))[0]["id"] == "church_steps_1988"
    assert tools["find_set"].invoke({"place": "lighthouse"}) == NO_MATCH


# --------------------------------------------------------------------- compile


@pytest.mark.parametrize("compiler", [FakeCompiler(), load_compiler()], ids=["fake", "load_compiler"])
def test_a_generated_placeholder_set_compiles_with_the_samples_marks(compiler: Any) -> None:
    repo = make_repo()
    tools = tools_for(repo)
    gen_ok(tools, "back", place="Corner of Elm Street", description="brick shop fronts")
    gen_ok(tools, "middle", place="Corner of Elm Street", description="stone steps", marks=SAMPLE_MARKS)
    out = gen_ok(tools, "front", place="Corner of Elm Street", description="a low hedge")
    set_id = out["setId"]
    set_def = repo.get_set(FAM, set_id)

    sample = MemoryResponse.model_validate_json(SAMPLE_MEMORY_PATH.read_text())
    screenplay = Screenplay.model_validate_json(
        (SAMPLE_MEMORY_PATH.parent / "hymn_books.screenplay.json").read_text()
    )
    # The marks must come from the generated set, not from the shot.
    shots = [shot.model_copy(update={"set": set_id, "marks": {}}) for shot in screenplay.shots]
    screenplay = screenplay.model_copy(update={"shots": shots})

    scene = SceneInputs(
        sets={set_id: set_def},
        props={p.id: p for p in sample.props},
        people={p.id: p for p in sample.people},
    )
    result = compiler(screenplay, scene)
    assert result.ok, [e.message for e in result.errors]
    assert result.timeline is not None
    assert result.timeline.shots[0].set == set_id

    # Without the generated set's marks the same screenplay does not compile.
    bare = set_def.model_copy(update={"marks": {}})
    broken = compiler(screenplay, SceneInputs(sets={set_id: bare}, props=scene.props, people=scene.people))
    assert not broken.ok


def test_generated_set_validates_as_a_contract_set() -> None:
    repo = make_repo()
    tools = tools_for(repo)
    for layer in ("back", "middle", "front"):
        gen_ok(tools, layer, marks=SAMPLE_MARKS if layer == "middle" else None)
    data = repo.get_set(FAM, "corner_bakery").model_dump(mode="json", by_alias=True)
    assert set(data) >= {"id", "familyId", "name", "layers", "marks"}
    assert schemas.SetDef.model_validate(data).layers[2].height == 0.18


# ---------------------------------------------------------------------- image budget


def test_a_run_that_spent_its_image_budget_still_saves_placeholder_layers() -> None:
    repo = make_repo()
    model = RecordingImageModel()
    tools = tools_for(repo, art=ArtService(model, FakeBackgroundRemover(), max_images_per_run=2))

    back, middle = gen_ok(tools, "back"), gen_ok(tools, "middle", marks={"counter": [0.5, 0.0]})
    assert back["art"] == middle["art"] == "generated"
    assert "note" not in back and "note" not in middle

    front = gen_ok(tools, "front")
    assert len(model.requests) == 2
    assert front["art"] == "placeholder"
    assert front["note"] == (
        "image budget for this run is used up (2 images); the front layer of Corner bakery uses placeholder art"
    )
    saved = repo.get_set(FAM, front["setId"])
    layer = next(la for la in saved.layers if la.depth == 2)
    assert layer.art is None and layer.color == placeholder_color(f"{PLACE} front")
    # The earlier layers and marks are kept.
    assert [la.art is not None for la in saved.layers] == [True, True, False]
    assert saved.marks == {"counter": [0.5, 0.0]}


def test_a_zero_budget_never_calls_the_model_for_layers() -> None:
    model = RecordingImageModel()
    tools = tools_for(make_repo(), art=ArtService(model, FakeBackgroundRemover(), max_images_per_run=0))
    outs = [gen_ok(tools, layer) for layer in ("back", "middle", "front")]
    assert model.requests == []
    assert all(o["art"] == "placeholder" and "image budget" in o["note"] for o in outs)


def test_a_layer_failure_with_budget_left_has_no_budget_note() -> None:
    class Broken(FakeImageModel):
        def generate(self, prompt: str, **kw: Any) -> bytes:
            raise RuntimeError("image service down")

    tools = tools_for(make_repo(), art=ArtService(Broken(), FakeBackgroundRemover(), max_images_per_run=5))
    out = gen_ok(tools, "back")
    assert out["art"] == "placeholder" and "note" not in out
