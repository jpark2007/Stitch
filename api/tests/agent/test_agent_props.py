"""find_prop, generate_prop, confirm_props (agent/tools/props.py), offline on fakes."""

from __future__ import annotations

import io
import itertools
import json
from collections.abc import Sequence
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from PIL import Image

import schemas
from agent.fake_llm import ScriptedChatModel, tool_call, tool_messages
from agent.repo import InMemoryRepo, load_sample_family
from agent.run_info import RunInfo
from agent.tools import props as props_module
from agent.tools.art import (
    ALPHA_THRESHOLD,
    ArtService,
    FakeBackgroundRemover,
    FakeImageModel,
    analyze_cutout,
    placeholder_color,
    snap_grip,
)
from agent.tools.ask import INTERRUPT_ON, answer_to_resume, interrupt_to_event
from agent.tools.props import NO_MATCH, era_label, make_prop_tools, prop_slug, search_props

FAM = "fam_a"
OTHER = "fam_b"
_threads = itertools.count()


class PropRepo(InMemoryRepo):
    """InMemoryRepo plus find_props, as the mongo-repo task adds it to AgentRepo."""

    def __init__(self) -> None:
        super().__init__()
        self.find_calls: list[tuple[str, str, int]] = []

    def find_props(self, family_id: str, text: str, *, limit: int = 5) -> list[schemas.Prop]:
        self.find_calls.append((family_id, text, limit))
        return search_props(self.list_props(family_id), text, limit=limit)


class RecordingImageModel(FakeImageModel):
    """FakeImageModel that also records the reference images of each call."""

    def __init__(self) -> None:
        super().__init__()
        self.references: list[tuple[bytes, ...]] = []

    def generate(
        self, prompt: str, *, reference_images: Sequence[bytes] = (), size: tuple[int, int] = (1024, 1024)
    ) -> bytes:
        self.references.append(tuple(reference_images))
        return super().generate(prompt, reference_images=reference_images, size=size)


class FailingRemover:
    def remove(self, png: bytes) -> bytes:
        raise RuntimeError("remover down")


def make_run(
    *, family_id: str = FAM, year: int = 1985, photo_ids: Sequence[str] = (), run_id: str = "run_1"
) -> RunInfo:
    return RunInfo(
        run_id=run_id,
        family_id=family_id,
        user_id="user_1",
        author_id="dad",
        fields=schemas.SubmitMemoryFields(text="We carried the hymn books up the church steps.", year=year),
        photo_ids=tuple(photo_ids),
    )


def tools_for(repo: InMemoryRepo, art: ArtService, **run_kw: Any) -> dict[str, Any]:
    return {t.name: t for t in make_prop_tools(repo, make_run(**run_kw), art)}


def fake_art() -> ArtService:
    return ArtService(FakeImageModel(), FakeBackgroundRemover())


def png(color: tuple[int, int, int], size: tuple[int, int] = (64, 32), shape: bool = True) -> bytes:
    """An RGB image: a colored rectangle on white (or a solid fill when shape is False)."""
    im = Image.new("RGB", size, (255, 255, 255) if shape else color)
    if shape:
        w, h = size
        im.paste(color, (w // 4, h // 4, 3 * w // 4, 3 * h // 4))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def on_edge(image: bytes, point: Sequence[float]) -> bool:
    """True when `point` (image fractions) falls on an opaque pixel touching transparency or the border."""
    with Image.open(io.BytesIO(image)) as im:
        alpha = im.convert("RGBA").getchannel("A")
    w, h = alpha.size
    x = min(w - 1, int(point[0] * w))
    y = min(h - 1, int(point[1] * h))
    if alpha.getpixel((x, y)) < ALPHA_THRESHOLD:
        return False
    for dx, dy in itertools.product((-1, 0, 1), repeat=2):
        nx, ny = x + dx, y + dy
        if not (0 <= nx < w and 0 <= ny < h) or alpha.getpixel((nx, ny)) < ALPHA_THRESHOLD:
            return True
    return False


# --------------------------------------------------------------------------- factory


def test_factory_returns_the_three_tools_in_order() -> None:
    tools = make_prop_tools(PropRepo(), make_run(), fake_art())
    assert [t.name for t in tools] == ["find_prop", "generate_prop", "confirm_props"]


def test_no_tool_schema_takes_a_family_id() -> None:
    def keys(node: Any) -> set[str]:
        if isinstance(node, dict):
            found = set(node.get("properties", {})) if isinstance(node.get("properties"), dict) else set()
            return found.union(*(keys(v) for v in node.values()))
        if isinstance(node, list):
            return set().union(*(keys(v) for v in node))
        return set()

    for t in make_prop_tools(PropRepo(), make_run(), fake_art()):
        names = keys(t.tool_call_schema.model_json_schema())
        assert names, t.name
        assert not any("family" in n.lower() for n in names), (t.name, names)


def test_generate_prop_schema_is_explicit() -> None:
    tool = tools_for(PropRepo(), fake_art())["generate_prop"]
    schema = tool.tool_call_schema.model_json_schema()
    props = schema["properties"]
    assert set(props) == {"name", "description", "size", "grip", "reference_photo_id", "parts", "states", "sockets"}
    sockets_doc = props["sockets"]["description"]
    for needle in ('"driver": [0.35, 0.55]', '"seat"', '"top"', "get_in", "drive_to", "ride_to", "sit_on",
                   "place_on", "[0, 0] top-left"):
        assert needle in sockets_doc, needle
    assert all(size in props["size"]["description"] for size in props_module.SIZES)
    assert set(schema["required"]) == {"name", "description", "size"}
    assert set(schema["$defs"]["PartSpec"]["properties"]) == {"name", "description", "at", "spin"}
    assert set(schema["$defs"]["StateSpec"]["properties"]) == {"name", "description"}


# --------------------------------------------------------------------------- find_prop


def seeded_repo() -> PropRepo:
    repo = PropRepo()
    load_sample_family(repo, FAM)  # hymn_books, saved as this family's prop
    repo.save_prop(
        FAM,
        schemas.Prop(id="lib_chair", family_id=None, name="wooden chair", description="a plain wooden chair",
                     source="library", size="furniture", art={"body": None}),
    )
    repo.save_prop(
        FAM,
        schemas.Prop(id="trumpet_case", family_id=FAM, name="trumpet case", description="dad's old black trumpet case",
                     size="carried", art={"body": "media/77"}, grip=[0.5, 0.0], states={"open": {}, "closed": {}}),
    )
    return repo


def test_find_prop_returns_saved_and_library_props() -> None:
    repo = seeded_repo()
    find = tools_for(repo, fake_art())["find_prop"]

    hymn = json.loads(find.invoke({"description": "the worn hymn book"}))
    assert [p["id"] for p in hymn] == ["hymn_books"]
    assert hymn[0]["size"] == "hand" and hymn[0]["source"] == "generated"
    assert set(hymn[0]) == {
        "id", "name", "description", "size", "source", "aspect", "grip", "parts", "states", "sockets", "preview"
    }
    assert hymn[0]["sockets"] == {}

    case = json.loads(find.invoke({"description": "Dad's trumpet case"}))[0]
    assert case["id"] == "trumpet_case"
    assert case["preview"] == "media/77"
    assert case["grip"] == [0.5, 0.0]
    assert case["states"] == ["open", "closed"] and case["parts"] == []

    chair = json.loads(find.invoke({"description": "a chair"}))
    assert [p["id"] for p in chair] == ["lib_chair"]
    assert chair[0]["source"] == "library"

    assert find.invoke({"description": "a red bicycle"}) == NO_MATCH
    assert repo.find_calls[0] == (FAM, "the worn hymn book", 5)


def test_find_prop_ranks_family_props_before_library_props() -> None:
    repo = seeded_repo()
    repo.save_prop(FAM, schemas.Prop(id="kitchen_chair", family_id=FAM, name="kitchen chair", size="furniture",
                                     art={"body": None}))
    found = json.loads(tools_for(repo, fake_art())["find_prop"].invoke({"description": "wooden chair"}))
    assert [p["id"] for p in found] == ["kitchen_chair", "lib_chair"]


def test_find_prop_never_sees_another_family() -> None:
    repo = seeded_repo()
    other = tools_for(repo, fake_art(), family_id=OTHER)["find_prop"]
    for text in ("hymn book", "trumpet case", "chair"):
        assert other.invoke({"description": text}) == NO_MATCH
    assert all(call[0] == OTHER for call in repo.find_calls)


def test_find_prop_falls_back_to_list_props_without_find_props() -> None:
    repo = InMemoryRepo()
    load_sample_family(repo, FAM)
    found = json.loads(tools_for(repo, fake_art())["find_prop"].invoke({"description": "hymn books"}))
    assert [p["id"] for p in found] == ["hymn_books"]
    assert tools_for(repo, fake_art())["find_prop"].invoke({"description": "   "}).startswith("error:")


# ------------------------------------------------------------------------ generate_prop


def test_generate_with_fake_art_stores_a_cutout_and_snaps_the_grip() -> None:
    repo = PropRepo()
    model = FakeImageModel()
    tool = tools_for(repo, ArtService(model, FakeBackgroundRemover()))["generate_prop"]
    hint = [0.5, 0.02]
    out = json.loads(tool.invoke({"name": "Trumpet case", "description": "a black leather trumpet case",
                                  "size": "carried", "grip": hint}))

    assert out["id"] == "trumpet_case" and out["art"] == "generated"
    prop = repo.get_prop(FAM, "trumpet_case")
    assert prop is not None
    schemas.Prop.model_validate(prop.model_dump())
    assert prop.family_id == FAM and prop.source == "generated" and prop.size == "carried"
    assert prop.description == "a black leather trumpet case"
    assert prop.color == placeholder_color("Trumpet case")

    body = prop.art["body"]
    assert body is not None and out["preview"] == body
    stored = repo.get_media(FAM, body)
    assert stored is not None and stored[1] == "image/png"
    assert repo._families[FAM].media[body].kind == "cutout"
    assert repo.get_media(OTHER, body) is None

    cutout = analyze_cutout(stored[0])
    assert prop.aspect == pytest.approx(cutout.aspect)
    assert prop.anchor == pytest.approx(cutout.anchor)
    assert prop.grip == pytest.approx(snap_grip(stored[0], hint))
    assert on_edge(stored[0], prop.grip)
    assert out["grip"] == pytest.approx(prop.grip, abs=1e-4)

    # The prompt carries the subject and the era of the story's year.
    assert len(model.calls) == 1
    assert 'Subject: "Trumpet case: a black leather trumpet case"' in model.calls[0]
    assert 'Era: "1980s"' in model.calls[0]


def test_generate_without_a_grip_hint_has_no_grip() -> None:
    repo = PropRepo()
    out = json.loads(tools_for(repo, fake_art())["generate_prop"].invoke(
        {"name": "Picnic table", "description": "a long table", "size": "furniture"}))
    assert out["grip"] is None
    assert repo.get_prop(FAM, "picnic_table").grip is None


def test_generate_with_no_art_service_saves_a_placeholder() -> None:
    repo = PropRepo()
    tool = tools_for(repo, ArtService(None, None))["generate_prop"]
    out = json.loads(tool.invoke({"name": "Hymn book", "description": "a blue hymn book", "size": "hand",
                                  "grip": [1.4, -0.2]}))
    assert out["art"] == "placeholder" and out["preview"] is None
    prop = repo.get_prop(FAM, out["id"])
    assert prop is not None
    schemas.Prop.model_validate(prop.model_dump())
    assert prop.art == {"body": None}
    assert prop.color == placeholder_color("Hymn book")
    assert prop.size == "hand"
    assert prop.aspect == 1.0 and prop.anchor == [0.5, 1.0]
    assert prop.grip == [1.0, 0.0]  # the clamped hint
    assert prop.source == "generated" and prop.family_id == FAM


def test_failed_generation_falls_back_to_a_placeholder() -> None:
    repo = PropRepo()
    tool = tools_for(repo, ArtService(FakeImageModel(), FailingRemover()))["generate_prop"]
    out = json.loads(tool.invoke({"name": "Lantern", "description": "a tin lantern", "size": "hand",
                                  "parts": [{"name": "handle", "description": "wire handle", "at": [0.5, 0.0]}],
                                  "states": [{"name": "lit", "description": "glowing"}]}))
    assert out["art"] == "placeholder"
    prop = repo.get_prop(FAM, "lantern")
    assert prop.art == {"body": None, "handle": None}
    assert prop.states == {"lit": {}}


def test_parts_and_states_with_art() -> None:
    repo = PropRepo()
    model = RecordingImageModel()
    tool = tools_for(repo, ArtService(model, FakeBackgroundRemover()))["generate_prop"]
    out = json.loads(tool.invoke({
        "name": "Suitcase",
        "description": "a brown suitcase without its lid",
        "size": "carried",
        "parts": [
            {"name": "lid", "description": "the suitcase lid", "at": [0.5, 0.1]},
            {"name": "wheel", "description": "a small wheel", "at": [0.9, 1.0], "spin": True},
        ],
        "states": [
            {"name": "open", "description": "lid open, clothes inside"},
            {"name": "closed", "description": ""},
        ],
    }))
    assert out["parts"] == ["lid", "wheel"] and out["states"] == ["open", "closed"]
    prop = repo.get_prop(FAM, "suitcase")
    schemas.Prop.model_validate(prop.model_dump())
    assert set(prop.art) == {"body", "lid", "wheel"}
    assert all(prop.art[k] is not None for k in prop.art)
    assert len(set(prop.art.values())) == 3
    assert prop.parts == {
        "lid": schemas.PropPart(art="lid", at=[0.5, 0.1], spin=False),
        "wheel": schemas.PropPart(art="wheel", at=[0.9, 1.0], spin=True),
    }
    assert set(prop.states) == {"open", "closed"}
    assert prop.states["closed"] == {}
    open_ref = prop.states["open"]["body"]
    assert open_ref not in prop.art.values()
    assert repo.get_media(FAM, open_ref)[1] == "image/png"

    # body, two parts, one state image; the empty state needs no picture.
    assert len(model.calls) == 4
    assert "moving part" in model.calls[1]  # kind="part" framing
    assert "state described" in model.calls[3]  # kind="state" framing
    assert 'Subject: "Suitcase, lid: the suitcase lid"' in model.calls[1]
    assert 'Subject: "Suitcase, open: lid open, clothes inside"' in model.calls[3]
    # Parts and states are drawn with the body cutout as reference.
    body_png = repo.get_media(FAM, prop.art["body"])[0]
    assert model.references[0] == ()
    assert all(refs[0] == body_png for refs in model.references[1:])


def test_parts_and_states_as_placeholders() -> None:
    repo = PropRepo()
    tool = tools_for(repo, ArtService(None, None))["generate_prop"]
    json.loads(tool.invoke({
        "name": "Green sedan",
        "description": "a 1985 green sedan",
        "size": "vehicle",
        "parts": [{"name": "wheel_front", "description": "a wheel", "at": [0.22, 0.9], "spin": True}],
        "states": [{"name": "parked", "description": "engine off"}, {"name": "driving", "description": "moving"}],
    }))
    prop = repo.get_prop(FAM, "green_sedan")
    assert prop.art == {"body": None, "wheel_front": None}
    assert prop.parts["wheel_front"] == schemas.PropPart(art="wheel_front", at=[0.22, 0.9], spin=True)
    assert prop.states == {"parked": {}, "driving": {}}


def test_slug_collisions_get_a_suffix() -> None:
    repo = PropRepo()
    tool = tools_for(repo, ArtService(None, None))["generate_prop"]
    ids = [json.loads(tool.invoke({"name": "Hymn book!", "description": "a hymn book", "size": "hand"}))["id"]
           for _ in range(3)]
    assert ids == ["hymn_book", "hymn_book_2", "hymn_book_3"]
    assert all(repo.get_prop(FAM, i) is not None for i in ids)
    # A collision with an existing (sample) prop also gets a suffix.
    load_sample_family(repo, FAM)
    out = json.loads(tool.invoke({"name": "Hymn books", "description": "more", "size": "hand"}))
    assert out["id"] == "hymn_books_2"


def test_parallel_generation_never_shares_an_id() -> None:
    from concurrent.futures import ThreadPoolExecutor

    repo = PropRepo()
    tool = tools_for(repo, fake_art())["generate_prop"]
    args = {"name": "Teacup", "description": "a china teacup", "size": "hand"}
    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = [json.loads(r)["id"] for r in pool.map(lambda _: tool.invoke(args), range(6))]
    assert sorted(ids) == sorted(["teacup", *(f"teacup_{n}" for n in range(2, 7))])
    assert len(repo.list_props(FAM)) == 6


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Trumpet case", "trumpet_case"),
        ("  Grandma's  Café crème cup ", "grandma_s_cafe_creme_cup"),
        ("---", "prop"),
        ("测试", "prop"),
        ("a" * 60, "a" * 40),
        ("x" * 39 + " y", "x" * 39),
    ],
)
def test_prop_slug(name: str, slug: str) -> None:
    assert prop_slug(name) == slug


def test_long_names_stay_within_forty_chars_when_suffixed() -> None:
    repo = PropRepo()
    tool = tools_for(repo, ArtService(None, None))["generate_prop"]
    name = "a very long name for an old family heirloom clock"
    first = json.loads(tool.invoke({"name": name, "description": "", "size": "furniture"}))["id"]
    second = json.loads(tool.invoke({"name": name, "description": "", "size": "furniture"}))["id"]
    assert len(first) <= 40 and len(second) <= 40
    assert second.endswith("_2") and second != first


def test_era_label() -> None:
    assert era_label(1985) == "1980s"
    assert era_label(1932) == "before 1940"
    assert era_label(2024) == "2020s"


def test_reference_photo_must_belong_to_this_run() -> None:
    repo = PropRepo()
    model = RecordingImageModel()
    art = ArtService(model, FakeBackgroundRemover())
    mine = repo.put_media(FAM, png((200, 30, 30)), "image/png", kind="photo")
    foreign = repo.put_media(FAM, png((30, 30, 200)), "image/png", kind="photo")
    elsewhere = repo.put_media(OTHER, png((30, 200, 30)), "image/png", kind="photo")
    tool = tools_for(repo, art, photo_ids=[mine])["generate_prop"]
    args = {"name": "Cap", "description": "a blue flat cap", "size": "hand"}

    for bad in (foreign, elsewhere, "media/999", "../etc"):
        result = tool.invoke({**args, "reference_photo_id": bad})
        assert result.startswith("error:") and "unknown photo" in result
    assert model.calls == [] and repo.list_props(FAM) == []

    out = json.loads(tool.invoke({**args, "reference_photo_id": mine}))
    assert out["art"] == "generated"
    assert model.references == [(repo.get_media(FAM, mine)[0],)]


def test_reference_photo_listed_but_missing_is_refused() -> None:
    repo = PropRepo()
    tool = tools_for(repo, fake_art(), photo_ids=["media/404"])["generate_prop"]
    out = tool.invoke({"name": "Cap", "description": "cap", "size": "hand", "reference_photo_id": "media/404"})
    assert out.startswith("error:")


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({"size": "huge"}, "size"),
        ({"size": ""}, "size"),
        ({"name": "   "}, "name"),
        ({"grip": [0.5]}, "grip"),
        ({"grip": [0.5, float("nan")]}, "grip"),
        ({"parts": [{"name": "lid", "description": "lid", "at": [1.2, 0.5]}]}, "between 0 and 1"),
        ({"parts": [{"name": "lid", "description": "lid", "at": [0.5, -0.01]}]}, "between 0 and 1"),
        ({"parts": [{"name": "lid", "description": "lid", "at": [0.5]}]}, "between 0 and 1"),
        ({"parts": [{"name": "Lid Top", "description": "lid", "at": [0.5, 0.5]}]}, "snake_case"),
        ({"parts": [{"name": "body", "description": "lid", "at": [0.5, 0.5]}]}, "body"),
        ({"parts": [{"name": "lid", "description": "a", "at": [0.1, 0.1]},
                    {"name": "lid", "description": "b", "at": [0.2, 0.2]}]}, "twice"),
        ({"parts": [{"name": f"p{i}", "description": "p", "at": [0.1, 0.1]} for i in range(7)]}, "at most"),
        ({"states": [{"name": "Open!", "description": "open"}]}, "snake_case"),
        ({"states": [{"name": "open", "description": "a"}, {"name": "open", "description": "b"}]}, "twice"),
        ({"states": [{"name": f"s{i}", "description": "s"} for i in range(5)]}, "at most"),
    ],
)
def test_invalid_input_returns_an_error_string(args: dict[str, Any], needle: str) -> None:
    repo = PropRepo()
    model = FakeImageModel()
    tool = tools_for(repo, ArtService(model, FakeBackgroundRemover()))["generate_prop"]
    result = tool.invoke({"name": "Suitcase", "description": "a suitcase", "size": "carried", **args})
    assert isinstance(result, str) and result.startswith("error:"), result
    assert needle in result
    assert repo.list_props(FAM) == [] and model.calls == []


def test_repo_failure_is_an_error_string() -> None:
    class BrokenRepo(PropRepo):
        def save_prop(self, family_id: str, prop: schemas.Prop) -> schemas.Prop:
            raise RuntimeError("db down")

    out = tools_for(BrokenRepo(), ArtService(None, None))["generate_prop"].invoke(
        {"name": "Cap", "description": "cap", "size": "hand"})
    assert out.startswith("error:")


# ------------------------------------------------------------------------ confirm_props


def generate(repo: InMemoryRepo, art: ArtService, **args: Any) -> dict[str, Any]:
    return json.loads(tools_for(repo, art)["generate_prop"].invoke(args))


def test_confirm_props_without_edits_keeps_everything() -> None:
    repo = seeded_repo()
    confirm = tools_for(repo, fake_art())["confirm_props"]
    chips = [{"id": "hymn_books", "name": "hymn books", "size": "hand", "reused": True},
             {"id": "trumpet_case", "name": "trumpet case", "size": "carried", "art": "media/77", "reused": True}]
    before = repo.list_props(FAM)
    out = confirm.invoke({"props": chips})
    assert out.splitlines() == [
        "hymn_books | hymn books | size=hand | kept",
        "trumpet_case | trumpet case | size=carried | kept",
        "removed: none",
    ]
    assert repo.list_props(FAM) == before


def test_confirm_props_reports_problems_without_raising() -> None:
    repo = seeded_repo()
    confirm = tools_for(repo, fake_art())["confirm_props"]
    chips = [{"id": "hymn_books", "name": "hymn books", "size": "hand"},
             {"id": "ghost", "name": "ghost prop", "size": "tiny"}]
    edits = [
        {"id": "nobody", "action": "rename", "name": "x"},
        {"id": "hymn_books", "action": "replace_photo", "photoId": "media/404"},
        {"id": "hymn_books", "action": "rename", "name": "   "},
    ]
    out = confirm.invoke({"props": chips, "edits": edits})
    lines = out.splitlines()
    assert lines[0] == "hymn_books | hymn books | size=hand | kept"
    assert lines[1] == "removed: none"
    problems = "\n".join(lines[2:])
    assert "'nobody'" in problems and "not in the reviewed list" in problems
    assert "ghost: no saved prop" in problems
    assert "media/404" in problems and "not found" in problems
    assert "rename without a name" in problems
    assert repo.get_prop(FAM, "hymn_books").name == "hymn books"


def test_confirm_props_cannot_touch_another_familys_props_or_photos() -> None:
    repo = seeded_repo()
    their_photo = repo.put_media(OTHER, png((10, 120, 10)), "image/png", kind="photo")
    other = tools_for(repo, fake_art(), family_id=OTHER)["confirm_props"]
    out = other.invoke({"props": [{"id": "hymn_books", "name": "x", "size": "hand"}],
                        "edits": [{"id": "hymn_books", "action": "rename", "name": "stolen"}]})
    assert "no saved prop" in out
    assert repo.get_prop(FAM, "hymn_books").name == "hymn books"

    mine = tools_for(repo, fake_art())["confirm_props"]
    out = mine.invoke({"props": [{"id": "hymn_books", "name": "hymn books", "size": "hand"}],
                       "edits": [{"id": "hymn_books", "action": "replace_photo", "photoId": their_photo}]})
    assert "not found" in out
    assert repo.get_prop(FAM, "hymn_books").source == "generated"


def test_replace_photo_without_a_remover_uses_the_photo_itself() -> None:
    repo = seeded_repo()
    before = repo.get_prop(FAM, "trumpet_case")
    photo = repo.put_media(FAM, png((120, 60, 20), size=(300, 200), shape=False), "image/jpeg", kind="photo")
    confirm = tools_for(repo, ArtService(FakeImageModel(), None))["confirm_props"]
    out = confirm.invoke({"props": [{"id": "trumpet_case", "name": "trumpet case", "size": "carried"}],
                          "edits": [{"id": "trumpet_case", "action": "replace_photo", "photoId": photo}]})
    # trumpet_case was reused (not generated in this run): the photo goes on a fork.
    assert out.splitlines()[0] == (
        "trumpet_case_2 | trumpet case | size=carried | photo"
        " (replaces trumpet_case; use the new id) (dropped states: open, closed)"
    )
    assert repo.get_prop(FAM, "trumpet_case") == before
    prop = repo.get_prop(FAM, "trumpet_case_2")
    assert prop.art == {"body": photo}
    assert prop.aspect == pytest.approx(1.5)
    assert prop.anchor == [0.5, 1.0]
    assert prop.source == "photo" and prop.parts == {} and prop.states == {}
    # No cutout (a raw photo has no alpha): the old grip is kept, clamped.
    assert prop.size == "carried" and prop.grip == [0.5, 0.0]


def test_replace_photo_with_a_bare_id_stores_a_media_ref() -> None:
    """The web sends the bare id POST /families/{fid}/media returned; art keeps 'media/<id>'."""
    repo = seeded_repo()
    data = png((120, 60, 20), size=(300, 200), shape=False)
    ref = repo.put_media(FAM, data, "image/jpeg", kind="photo")
    bare = ref.removeprefix("media/")
    assert bare != ref
    confirm = tools_for(repo, ArtService(FakeImageModel(), None))["confirm_props"]
    out = confirm.invoke({"props": [{"id": "trumpet_case", "name": "trumpet case", "size": "carried"}],
                          "edits": [{"id": "trumpet_case", "action": "replace_photo", "photoId": bare}]})
    assert out.splitlines()[0].startswith("trumpet_case_2 | trumpet case | size=carried | photo")
    body = repo.get_prop(FAM, "trumpet_case_2").art["body"]
    assert body == ref and body.startswith("media/")
    assert repo.get_media(FAM, body) == (data, "image/jpeg")
    assert repo.get_prop(FAM, "trumpet_case").art["body"] == "media/77"


# ------------------------------------------------- end to end through the HITL middleware


def test_confirm_props_pauses_for_review_and_applies_the_edits() -> None:
    repo = seeded_repo()
    photo = repo.put_media(FAM, png((40, 80, 160), size=(200, 100)), "image/png", kind="photo")
    art = fake_art()
    tools = make_prop_tools(repo, make_run(), art)
    cap = json.loads(tools[1].invoke({"name": "Blue flat cap", "description": "dad's blue flat cap",
                                      "size": "hand", "grip": [0.5, 0.0]}))
    sedan = json.loads(tools[1].invoke({"name": "Green sedan", "description": "a green 1985 sedan",
                                        "size": "vehicle", "states": [{"name": "parked", "description": ""}]}))
    chips = [
        {"id": "hymn_books", "name": "hymn books", "description": "worn blue hymn books", "size": "hand",
         "art": None, "reused": True},
        {"id": cap["id"], "name": "Blue flat cap", "size": "hand", "art": cap["preview"], "reused": False},
        {"id": sedan["id"], "name": "Green sedan", "size": "vehicle", "art": sedan["preview"], "reused": False},
        {"id": "trumpet_case", "name": "trumpet case", "size": "carried", "art": "media/77", "reused": True},
    ]

    def responder(messages: list[BaseMessage], names: list[str]) -> AIMessage:
        assert names == ["find_prop", "generate_prop", "confirm_props"]
        if not tool_messages(messages, "confirm_props"):
            return tool_call("confirm_props", {"props": chips})
        return AIMessage(content="Props confirmed.")

    model = ScriptedChatModel(responder=responder)
    graph = create_agent(
        model,
        tools=tools,
        middleware=[HumanInTheLoopMiddleware(interrupt_on=INTERRUPT_ON)],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": f"props-{next(_threads)}"}}
    before = {p.id: p for p in repo.list_props(FAM)}

    graph.invoke({"messages": [{"role": "user", "content": "Find the props."}]}, config)
    (pending,) = graph.get_state(config).interrupts
    # Paused before the tool ran: nothing changed yet.
    assert {p.id: p for p in repo.list_props(FAM)} == before

    name, event = interrupt_to_event(pending.value)
    assert name == "props_found"
    assert isinstance(event, schemas.RUN_EVENTS[name])
    assert event == schemas.PropsFoundEvent(props=[schemas.PropChip.model_validate(c) for c in chips])

    answer = schemas.AnswerRequest(
        kind="props",
        props=[
            schemas.PropEdit(id="hymn_books", action="rename", name="red hymnals"),
            schemas.PropEdit(id=sedan["id"], action="remove"),
            schemas.PropEdit(id=cap["id"], action="replace_photo", photo_id=photo),
            schemas.PropEdit(id="trumpet_case", action="keep"),
        ],
    )
    graph.invoke(Command(resume=answer_to_resume(pending.value, answer)), config)
    assert not graph.get_state(config).interrupts

    (msg,) = tool_messages(graph.get_state(config).values["messages"], "confirm_props")
    # The middleware prepends a note that the reviewer edited the call; the tool's own text follows.
    assert msg.content.startswith("Note: a human reviewer replaced this tool call")
    result = msg.content.split("Tool response:", 1)[1].strip()
    assert result.splitlines() == [
        "hymn_books | red hymnals | size=hand | renamed",
        f"{cap['id']} | Blue flat cap | size=hand | photo",
        "trumpet_case | trumpet case | size=carried | kept",
        f"removed: {sedan['id']}",
    ]
    assert graph.get_state(config).values["messages"][-1].content == "Props confirmed."

    # rename
    hymn = repo.get_prop(FAM, "hymn_books")
    assert hymn.name == "red hymnals"
    assert hymn.model_dump(exclude={"name"}) == before["hymn_books"].model_dump(exclude={"name"})
    # remove: dropped from the result, still saved
    assert repo.get_prop(FAM, sedan["id"]) == before[sedan["id"]]
    # replace_photo on a prop this run generated: in place, a new cutout of the photo, rig
    # reset, size kept, the grip re-snapped onto the new shape's edge
    capped = repo.get_prop(FAM, cap["id"])
    body = capped.art["body"]
    assert set(capped.art) == {"body"} and body not in (photo, cap["preview"])
    stored = repo.get_media(FAM, body)
    assert stored[1] == "image/png" and repo._families[FAM].media[body].kind == "cutout"
    cutout = analyze_cutout(stored[0])
    assert capped.aspect == pytest.approx(cutout.aspect) and capped.anchor == pytest.approx(cutout.anchor)
    assert capped.source == "photo" and capped.parts == {} and capped.states == {}
    assert capped.size == "hand"
    assert capped.grip == pytest.approx(snap_grip(stored[0], before[cap["id"]].grip))
    assert on_edge(stored[0], capped.grip)
    schemas.Prop.model_validate(capped.model_dump())
    # keep
    assert repo.get_prop(FAM, "trumpet_case") == before["trumpet_case"]


# ------------------------------------------------------------------------ sockets


SEDAN_SOCKETS = {"driver": [0.35, 0.55], "passenger": [0.65, 0.55]}


@pytest.mark.parametrize("art", [ArtService(None, None), fake_art()], ids=["placeholder", "generated"])
def test_a_vehicle_gets_driver_and_passenger_sockets_by_default(art: ArtService) -> None:
    repo = PropRepo()
    tools = tools_for(repo, art)
    out = json.loads(tools["generate_prop"].invoke(
        {"name": "green sedan", "description": "a 1985 green sedan", "size": "vehicle"}))
    assert out["sockets"] == SEDAN_SOCKETS
    prop = repo.get_prop(FAM, out["id"])
    assert prop.sockets == SEDAN_SOCKETS
    schemas.Prop.model_validate(prop.model_dump())

    (found,) = json.loads(tools["find_prop"].invoke({"description": "green sedan"}))
    assert found["id"] == out["id"] and found["sockets"] == SEDAN_SOCKETS


@pytest.mark.parametrize(
    ("size", "sockets", "expected"),
    [
        ("furniture", None, {"seat": [0.5, 0.45]}),
        ("hand", None, {}),
        ("tiny", None, {}),
        ("carried", None, {}),
        ("building", None, {}),
        ("vehicle", {}, {}),  # explicit {} means no sockets, even for a vehicle
        ("furniture", {"top": [0.5, 0.1]}, {"top": [0.5, 0.1]}),
        ("vehicle", {"driver": [0.3, 0.5], "back_seat": [0.7, 0.5]}, {"driver": [0.3, 0.5], "back_seat": [0.7, 0.5]}),
        ("furniture", {"seat_left": [0, 1], "seat_right": [1, 0]}, {"seat_left": [0.0, 1.0], "seat_right": [1.0, 0.0]}),
    ],
)
def test_socket_defaults_and_explicit_sockets(size: str, sockets: dict | None, expected: dict) -> None:
    repo = PropRepo()
    args: dict[str, Any] = {"name": "thing", "description": "a thing", "size": size}
    if sockets is not None:
        args["sockets"] = sockets
    out = json.loads(tools_for(repo, fake_art())["generate_prop"].invoke(args))
    assert out["sockets"] == expected
    assert repo.get_prop(FAM, out["id"]).sockets == expected


def test_socket_values_are_rounded_in_results_but_saved_exactly() -> None:
    repo = PropRepo()
    tools = tools_for(repo, ArtService(None, None))
    out = json.loads(tools["generate_prop"].invoke({"name": "bench", "description": "a park bench",
                                                    "size": "furniture", "sockets": {"seat": [1 / 3, 0.123456]}}))
    assert out["sockets"] == {"seat": [0.3333, 0.1235]}
    assert repo.get_prop(FAM, "bench").sockets == {"seat": [1 / 3, 0.123456]}
    assert json.loads(tools["find_prop"].invoke({"description": "park bench"}))[0]["sockets"] == out["sockets"]


@pytest.mark.parametrize(
    ("sockets", "needle"),
    [
        ({"Driver Seat": [0.5, 0.5]}, "snake_case"),
        ({"9lives": [0.5, 0.5]}, "snake_case"),
        ({"x" * 41: [0.5, 0.5]}, "snake_case"),
        ({"hand_r": [0.5, 0.5]}, "hand_r"),
        ({"hand_l": [0.5, 0.5]}, "hand_l"),
        ({"head": [0.5, 0.5]}, "head"),
        ({"body": [0.5, 0.5]}, "body"),
        ({"seat": [1.2, 0.5]}, "between 0 and 1"),
        ({"seat": [0.5, -0.01]}, "between 0 and 1"),
        ({"seat": [0.5, float("nan")]}, "between 0 and 1"),
        ({"seat": [float("inf"), 0.5]}, "between 0 and 1"),
        ({"seat": [0.5]}, "between 0 and 1"),
        ({"seat": [0.5, 0.5, 0.5]}, "between 0 and 1"),
        ({f"seat_{i}": [0.1 * i, 0.5] for i in range(7)}, "at most 6"),
    ],
)
def test_invalid_sockets_return_an_error_and_save_nothing(sockets: dict, needle: str) -> None:
    repo = PropRepo()
    model = FakeImageModel()
    tool = tools_for(repo, ArtService(model, FakeBackgroundRemover()))["generate_prop"]
    result = tool.invoke({"name": "green sedan", "description": "a car", "size": "vehicle", "sockets": sockets})
    assert isinstance(result, str) and result.startswith("error:"), result
    assert needle in result
    assert repo.list_props(FAM) == [] and model.calls == []
    family = repo._families.get(FAM)
    assert family is None or not family.media


def test_six_sockets_are_allowed() -> None:
    repo = PropRepo()
    sockets = {f"seat_{i}": [0.1 * i, 0.5] for i in range(6)}
    out = json.loads(tools_for(repo, ArtService(None, None))["generate_prop"].invoke(
        {"name": "pew", "description": "a church pew", "size": "furniture", "sockets": sockets}))
    assert set(out["sockets"]) == set(sockets)


# ------------------------------------------- sockets against the real compiler


def _scene(cast: list[str], prop_id: str, actions: list[dict], start: dict) -> str:
    return json.dumps({
        "memoryId": "m1",
        "cast": cast,
        "props": [prop_id],
        "shots": [{
            "id": "s1",
            "set": "church_steps_1988",
            "start": start,
            "propStart": {prop_id: {"at": "steps_foot"}},
            "camera": [{"t": 0, "move": "wide"}],
            "actions": actions,
            "pageText": "A test page.",
        }],
    })


@pytest.fixture
def real_compiler() -> Any:
    from agent.fake_compiler import FakeCompiler
    from agent.tools.compile import load_compiler

    compiler = load_compiler()
    if isinstance(compiler, FakeCompiler):
        pytest.skip("screenplay.compiler.compile is not on this branch (load_compiler() returns FakeCompiler)")
    return compiler


def _compile_codes(repo: InMemoryRepo, text: str, compiler: Any) -> tuple[bool, list[str]]:
    from agent.tools.compile import parse_and_compile
    from agent.wiring import scene_for

    res = parse_and_compile(text, compiler, scene_for(repo, FAM))
    return res.ok, [e.code for e in res.errors]


def test_a_generated_vehicle_can_be_got_into_and_driven_with_the_real_compiler(real_compiler: Any) -> None:
    repo = PropRepo()
    load_sample_family(repo, FAM)
    tool = tools_for(repo, ArtService(None, None))["generate_prop"]
    car = json.loads(tool.invoke({"name": "green sedan", "description": "a 1985 green sedan", "size": "vehicle"}))
    actions = [{"t": 0, "who": "dad", "do": "get_in", "what": car["id"]},
               {"t": 3, "who": "dad", "do": "drive_to", "what": car["id"], "to": "curb"}]
    start = {"dad": {"at": "steps_left", "facing": "right"}}
    ok, codes = _compile_codes(repo, _scene(["dad"], car["id"], actions, start), real_compiler)
    assert "no_free_seat" not in codes and "not_driver" not in codes
    assert ok, codes

    # Control: the same car with no sockets cannot be driven, so the check above means something.
    bare = json.loads(tool.invoke({"name": "bare sedan", "description": "a car", "size": "vehicle", "sockets": {}}))
    actions = [{**a, "what": bare["id"]} for a in actions]
    ok, codes = _compile_codes(repo, _scene(["dad"], bare["id"], actions, start), real_compiler)
    assert not ok and codes


def test_a_generated_chair_can_be_sat_on_with_the_real_compiler(real_compiler: Any) -> None:
    repo = PropRepo()
    load_sample_family(repo, FAM)
    tool = tools_for(repo, ArtService(None, None))["generate_prop"]
    chair = json.loads(tool.invoke({"name": "kitchen chair", "description": "a wooden chair", "size": "furniture"}))
    actions = [{"t": 0.5, "who": "dad", "do": "sit_on", "what": chair["id"]}]
    start = {"dad": {"at": "steps_left", "facing": "right"}}
    ok, codes = _compile_codes(repo, _scene(["dad"], chair["id"], actions, start), real_compiler)
    assert "no_seat" not in codes
    assert ok, codes

    stool = json.loads(tool.invoke({"name": "stool", "description": "a stool", "size": "furniture", "sockets": {}}))
    actions = [{**a, "what": stool["id"]} for a in actions]
    ok, codes = _compile_codes(repo, _scene(["dad"], stool["id"], actions, start), real_compiler)
    assert "no_seat" in codes


# ------------------------------------------------- replace_photo: fork and re-rig the grip


def l_shape_photo(size: int = 120) -> bytes:
    """An RGBA 'L': a bar down the left side and a bar along the bottom, the rest transparent
    (and white, so FakeBackgroundRemover also clears it). Its top-middle is empty, so a grip at
    [0.5, 0.0] carried over from another picture would hang in the air."""
    im = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    bar = size // 4
    im.paste((150, 40, 40, 255), (0, 0, bar, size))
    im.paste((150, 40, 40, 255), (0, size - bar, size, size))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def alpha_at(image: bytes, point: Sequence[float]) -> int:
    with Image.open(io.BytesIO(image)) as im:
        alpha = im.convert("RGBA").getchannel("A")
    w, h = alpha.size
    return alpha.getpixel((min(w - 1, int(point[0] * w)), min(h - 1, int(point[1] * h))))


MUSIC_BOX = {
    "name": "music box",
    "description": "grandma's wooden music box",
    "size": "carried",
    "grip": [0.5, 0.0],
    "parts": [{"name": "lid", "description": "the lid on its own", "at": [0.5, 0.1]}],
    "states": [{"name": "open", "description": "lid open, a dancer inside"}],
    "sockets": {"top": [0.5, 0.05]},
}


def test_replace_photo_on_a_prop_from_an_earlier_run_forks_it() -> None:
    """The finding: run 2's review replaced the art of a prop run 1 made, in place, so older
    memories lost the 'lid' part and 'open' state they use, and the old grip hung off the new
    shape. Now run 1's prop stays exactly as it was and run 2 gets a re-rigged fork."""
    repo = PropRepo()
    art = fake_art()
    run1 = {t.name: t for t in make_prop_tools(repo, make_run(run_id="run_1"), art)}
    made = json.loads(run1["generate_prop"].invoke(MUSIC_BOX))
    old_id = made["id"]
    before = repo.get_prop(FAM, old_id)
    assert list(before.parts) == ["lid"] and list(before.states) == ["open"] and before.grip is not None
    old_body = repo.get_media(FAM, before.art["body"])

    photo = repo.put_media(FAM, l_shape_photo(), "image/png", kind="photo")
    run2 = {t.name: t for t in make_prop_tools(repo, make_run(run_id="run_2", photo_ids=[photo]), art)}
    out = run2["confirm_props"].invoke({
        "props": [{"id": old_id, "name": "music box", "size": "carried", "art": made["preview"], "reused": True}],
        "edits": [{"id": old_id, "action": "replace_photo", "photoId": photo}],
    })
    line = out.splitlines()[0]
    new_id = line.split(" | ", 1)[0]
    assert new_id != old_id
    assert line == (f"{new_id} | music box | size=carried | photo"
                    f" (replaces {old_id}; use the new id) (dropped parts: lid; states: open)")
    assert out.splitlines()[1] == "removed: none"

    # The original is untouched: same parts, states, art, grip, everything.
    after = repo.get_prop(FAM, old_id)
    assert after == before
    assert list(after.parts) == ["lid"] and list(after.states) == ["open"]
    assert repo.get_media(FAM, after.art["body"]) == old_body

    fork = repo.get_prop(FAM, new_id)
    schemas.Prop.model_validate(fork.model_dump())
    assert fork.family_id == FAM and fork.source == "photo"
    assert (fork.name, fork.description, fork.size, fork.color) == (before.name, before.description, before.size,
                                                                     before.color)
    assert fork.parts == {} and fork.states == {}
    assert fork.sockets == before.sockets == {"top": [0.5, 0.05]}
    assert set(fork.art) == {"body"} and fork.art["body"] != before.art["body"]
    cutout_png, content_type = repo.get_media(FAM, fork.art["body"])
    assert content_type == "image/png"
    cutout = analyze_cutout(cutout_png)
    assert fork.aspect == pytest.approx(cutout.aspect) and fork.anchor == pytest.approx(cutout.anchor)
    # The old grip would hang in the air on the L; the re-rigged one is on the shape.
    assert alpha_at(cutout_png, before.grip) < ALPHA_THRESHOLD
    assert alpha_at(cutout_png, fork.grip) >= ALPHA_THRESHOLD
    assert on_edge(cutout_png, fork.grip)


def test_rename_with_replace_photo_on_a_reused_prop_goes_to_the_fork_only() -> None:
    repo = seeded_repo()
    before = repo.get_prop(FAM, "trumpet_case")
    photo = repo.put_media(FAM, l_shape_photo(), "image/png", kind="photo")
    confirm = tools_for(repo, fake_art())["confirm_props"]
    out = confirm.invoke({
        "props": [{"id": "trumpet_case", "name": "trumpet case", "size": "carried"}],
        "edits": [{"id": "trumpet_case", "action": "rename", "name": "silver trumpet case"},
                  {"id": "trumpet_case", "action": "replace_photo", "photoId": photo}],
    })
    assert out.splitlines()[0] == (
        "silver_trumpet_case | silver trumpet case | size=carried | photo"
        " (replaces trumpet_case; use the new id) (dropped states: open, closed)"
    )
    assert repo.get_prop(FAM, "trumpet_case") == before
    assert repo.get_prop(FAM, "silver_trumpet_case").name == "silver trumpet case"


def test_rename_alone_on_a_reused_prop_still_renames_in_place() -> None:
    repo = seeded_repo()
    out = tools_for(repo, fake_art())["confirm_props"].invoke({
        "props": [{"id": "trumpet_case", "name": "trumpet case", "size": "carried"}],
        "edits": [{"id": "trumpet_case", "action": "rename", "name": "brass case"}],
    })
    assert out.splitlines()[0] == "trumpet_case | brass case | size=carried | renamed"
    assert repo.get_prop(FAM, "trumpet_case").name == "brass case"


def test_replace_photo_on_a_library_prop_forks_into_the_family() -> None:
    repo = seeded_repo()
    before = repo.get_prop(FAM, "lib_chair")
    photo = repo.put_media(FAM, l_shape_photo(), "image/png", kind="photo")
    out = tools_for(repo, fake_art())["confirm_props"].invoke({
        "props": [{"id": "lib_chair", "name": "wooden chair", "size": "furniture"}],
        "edits": [{"id": "lib_chair", "action": "replace_photo", "photoId": photo}],
    })
    assert out.splitlines()[0] == "wooden_chair | wooden chair | size=furniture | photo (replaces lib_chair; use the new id)"
    assert repo.get_prop(FAM, "lib_chair") == before and before.family_id is None
    fork = repo.get_prop(FAM, "wooden_chair")
    assert fork.family_id == FAM and fork.grip is None


def test_replace_photo_on_a_prop_generated_this_run_updates_it_in_place() -> None:
    repo = PropRepo()
    photo = repo.put_media(FAM, l_shape_photo(), "image/png", kind="photo")
    tools = {t.name: t for t in make_prop_tools(repo, make_run(photo_ids=[photo]), fake_art())}
    made = json.loads(tools["generate_prop"].invoke(MUSIC_BOX))
    before = repo.get_prop(FAM, made["id"])
    out = tools["confirm_props"].invoke({
        "props": [{"id": made["id"], "name": "music box", "size": "carried", "art": made["preview"]}],
        "edits": [{"id": made["id"], "action": "replace_photo", "photoId": photo}],
    })
    assert out.splitlines()[0] == f"{made['id']} | music box | size=carried | photo (dropped parts: lid; states: open)"
    assert [p.id for p in repo.list_props(FAM)] == [made["id"]]
    prop = repo.get_prop(FAM, made["id"])
    assert prop.parts == {} and prop.states == {} and prop.sockets == before.sockets
    cutout_png = repo.get_media(FAM, prop.art["body"])[0]
    assert alpha_at(cutout_png, prop.grip) >= ALPHA_THRESHOLD
    assert on_edge(cutout_png, prop.grip)


def test_rerig_grip_falls_back_to_the_anchor_side_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    cutout = analyze_cutout(FakeBackgroundRemover().remove(l_shape_photo()))
    real_snap = props_module.snap_grip
    calls: list[list[float]] = []

    def snap_into_the_air(png: bytes, hint: Sequence[float]) -> list[float]:
        calls.append(list(hint))
        return [0.9, 0.05] if len(calls) == 1 else real_snap(png, hint)  # first: a transparent spot

    monkeypatch.setattr(props_module, "snap_grip", snap_into_the_air)
    grip = props_module.rerig_grip([0.5, 0.0], cutout)
    assert calls == [[0.5, 0.0], cutout.anchor]
    assert grip == real_snap(cutout.png, cutout.anchor)
    assert alpha_at(cutout.png, grip) >= ALPHA_THRESHOLD


def test_rerig_grip_without_a_grip_or_a_cutout() -> None:
    cutout = analyze_cutout(FakeBackgroundRemover().remove(l_shape_photo()))
    assert props_module.rerig_grip(None, cutout) is None
    assert props_module.rerig_grip([1.3, -0.2], None) == [1.0, 0.0]


# ------------------------------------------------------------------ image budget


def test_a_run_that_spent_its_image_budget_still_saves_a_placeholder_prop() -> None:
    repo = PropRepo()
    model = FakeImageModel()
    art = ArtService(model, FakeBackgroundRemover(), max_images_per_run=1)
    tool = tools_for(repo, art)["generate_prop"]
    first = json.loads(tool.invoke({"name": "Hymn book", "description": "a black hymn book", "size": "hand"}))
    assert first["art"] == "generated" and "note" not in first

    out = tool.invoke({"name": "Lantern", "description": "a tin lantern", "size": "hand",
                       "parts": [{"name": "handle", "description": "wire handle", "at": [0.5, 0.0]}],
                       "states": [{"name": "lit", "description": "glowing"}]})
    assert not out.startswith("error"), out
    result = json.loads(out)
    assert result["art"] == "placeholder" and result["preview"] is None
    assert result["note"] == "image budget for this run is used up (1 image); Lantern uses placeholder art"
    assert len(model.calls) == 1
    prop = repo.get_prop(FAM, result["id"])
    assert prop is not None
    assert prop.art == {"body": None, "handle": None} and prop.states == {"lit": {}}
    assert prop.color == placeholder_color("Lantern")


def test_a_budget_spent_mid_prop_names_the_parts_left_without_art() -> None:
    repo = PropRepo()
    model = FakeImageModel()
    tool = tools_for(repo, ArtService(model, FakeBackgroundRemover(), max_images_per_run=2))["generate_prop"]
    result = json.loads(tool.invoke({
        "name": "Suitcase", "description": "a brown suitcase", "size": "carried",
        "parts": [{"name": "lid", "description": "the lid", "at": [0.5, 0.1]},
                  {"name": "wheel", "description": "a wheel", "at": [0.9, 1.0]}],
        "states": [{"name": "open", "description": "lid open"}],
    }))
    assert len(model.calls) == 2  # the body and the lid
    assert result["art"] == "generated"
    assert result["note"] == "image budget for this run is used up (2 images); wheel, open of Suitcase uses placeholder art"
    prop = repo.get_prop(FAM, result["id"])
    assert prop.art["body"] is not None and prop.art["lid"] is not None and prop.art["wheel"] is None
    assert prop.states == {"open": {}}


def test_a_zero_budget_never_calls_the_model_and_a_failure_with_budget_left_has_no_note() -> None:
    repo = PropRepo()
    model = FakeImageModel()
    tool = tools_for(repo, ArtService(model, FakeBackgroundRemover(), max_images_per_run=0))["generate_prop"]
    result = json.loads(tool.invoke({"name": "Cup", "description": "a tin cup", "size": "hand"}))
    assert model.calls == [] and result["art"] == "placeholder" and "budget" in result["note"]

    failing = tools_for(PropRepo(), ArtService(FakeImageModel(), FailingRemover(), max_images_per_run=5))
    result = json.loads(failing["generate_prop"].invoke({"name": "Cup", "description": "a tin cup", "size": "hand"}))
    assert result["art"] == "placeholder" and "note" not in result


def test_each_run_has_its_own_prop_budget() -> None:
    repo = PropRepo()
    model = FakeImageModel()
    art = ArtService(model, FakeBackgroundRemover(), max_images_per_run=1)
    for run_id in ("run_a", "run_b"):
        tool = tools_for(repo, art, run_id=run_id)["generate_prop"]
        assert json.loads(tool.invoke({"name": f"Cup {run_id}", "description": "a cup", "size": "hand"}))["art"] == "generated"
        assert json.loads(tool.invoke({"name": f"Pot {run_id}", "description": "a pot", "size": "hand"}))["art"] == "placeholder"
    assert len(model.calls) == 2
