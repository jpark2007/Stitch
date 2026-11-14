"""Service failure matrix: a missing or failing outside service degrades a run, never fails it.

Every case drives a whole run end to end through the public seam, offline: a RunManager
from agent.wiring.make_manager over agent.wiring.fake_services (an InMemoryRepo seeded with
the sample family, the fake studio playing the model), with one service swapped for one that
fails. Where fake_services has no override for a service, the bundle is rebuilt with
dataclasses.replace on agent.wiring.Services.

Each case asserts the run ends with 'done', a Memory is saved whose timeline validates against
the contract, and the degraded output is the documented fallback:

    service     trigger                                  fallback
    ----------  ---------------------------------------  ----------------------------------------
    images      the image model raises on every call     props and set layers: art null + color
    images      STITCH_MAX_IMAGES_PER_RUN=0              placeholder art, image model never called
    cutout      the background remover raises            props, middle and front layers: placeholder
                                                         (the back layer is never cut out: real art)
    tts         the speech service raises on every call  silent audio, estimated word timings, and a
                                                         'warning:' line from synthesize_narration
    vision      describe_photo's model raises            the photo is skipped; the run goes on
    critic      inspect_frames' model raises             the checker passes without a critique
    renderer    the browser renderer fails               labelled Pillow diagrams (FakeFrameRenderer)
    renderer    a renderer with no fallback fails        no frames; the checker passes without them

No keys, no network; every wait has a timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import io
import json
import wave
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from PIL import Image
from pydantic import BaseModel

import schemas
from agent import wiring
from agent.fake_llm import ScriptedChatModel
from agent.fake_studio import FakeStory, make_fake_studio_responder
from agent.guards import RunLimits
from agent.repo import InMemoryRepo, load_sample_family
from agent.tools.art import ArtService, FakeBackgroundRemover, FakeImageModel, make_cut_out_tool
from agent.tools.frames import (
    DIAGRAM_CRITIC_NOTE,
    FakeFrameCritic,
    FakeFrameRenderer,
    FrameRenderError,
    PlaywrightFrameRenderer,
    PlaywrightUnavailable,
)
from agent.run_info import RunInfo
from agent.tools.narration import Speech
from agent.tools.photos import FakeVision, make_photo_tools
from agent.tools.sets import make_set_tools

FAMILY = "fam_fallbacks"
MOM_USER = "user_mom"
WAIT = 25.0
"""Seconds any one wait may take; a hung run fails the test instead of hanging it."""

STORY = FakeStory(place="lake dock", prop="picnic basket", prop_size="carried", compile_failures=0)
"""A place and a prop the sample family has never saved, so the run draws both."""

MOM_TEXT = (
    "We carried the picnic basket down to the lake dock. "
    "I nearly dropped it at the end of the boards. "
    "He took it from me and laughed."
)

Event = tuple[str, BaseModel]


class ServiceDown(RuntimeError):
    """What every failing fake raises: an outside service that is down."""


# --------------------------------------------------------------------------- failing fakes


class DownImageModel:
    """art.ImageModel whose provider fails every call."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, *, reference_images: Sequence[bytes] = (), size: tuple[int, int] = (1024, 1024)) -> bytes:
        self.calls += 1
        raise ServiceDown("image provider returned HTTP 503")


class CountingImageModel(FakeImageModel):
    """The deterministic fake image model, counting its calls."""


class DownRemover:
    """art.BackgroundRemover that fails every call."""

    def __init__(self) -> None:
        self.calls = 0

    def remove(self, png: bytes) -> bytes:
        self.calls += 1
        raise ServiceDown("background removal crashed")


class DownTTS:
    """narration.TTS whose provider fails every call."""

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        self.calls += 1
        raise ServiceDown("speech provider returned HTTP 500")


class DownVision:
    """photos.VisionModel that fails every call (describe_photo or inspect_frames)."""

    def __init__(self) -> None:
        self.calls = 0

    def describe(self, images: Sequence[bytes], prompt: str) -> str:
        self.calls += 1
        raise ServiceDown("vision provider timed out")


class DownRenderer:
    """A FrameRenderer with no fallback of its own that fails every render."""

    def __init__(self) -> None:
        self.calls = 0

    def render(self, scene: schemas.MemoryResponse, times: Sequence[float]) -> list[bytes]:
        self.calls += 1
        raise FrameRenderError("the render page did not load")


class SpyDiagrams(FakeFrameRenderer):
    """The Pillow diagram renderer, counting its renders."""

    def __init__(self) -> None:
        super().__init__()
        self.renders = 0

    def render(self, scene: schemas.MemoryResponse, times: Sequence[float]) -> list[bytes]:
        self.renders += 1
        return super().render(scene, times)


class RecordingCritic(FakeFrameCritic):
    """The fake critic, keeping every prompt it was given."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    def describe(self, images: Sequence[bytes], prompt: str) -> str:
        self.prompts.append(prompt)
        return super().describe(images, prompt)


def no_browser(viewport: tuple[int, int]) -> contextlib.AbstractContextManager[Any]:
    """playwright_page on a machine with no Playwright browser installed."""
    raise PlaywrightUnavailable("Playwright's Chromium is not installed")


# --------------------------------------------------------------------------- harness


class ToolLog:
    """Wraps the fake studio's responder and keeps every tool result any agent saw, by tool name."""

    def __init__(self, responder: Callable[[list[BaseMessage], list[str]], AIMessage]) -> None:
        self.responder = responder
        self.results: dict[str, dict[str, str]] = {}

    def __call__(self, messages: list[BaseMessage], tool_names: list[str]) -> AIMessage:
        for message in messages:
            if isinstance(message, ToolMessage) and message.name:
                text = message.text if isinstance(message.text, str) else str(message.content)
                self.results.setdefault(message.name, {})[message.tool_call_id] = text
        return self.responder(messages, tool_names)

    def of(self, name: str) -> list[str]:
        return list(self.results.get(name, {}).values())


class Studio:
    """One family's RunManager on fake services, with `overrides` replacing some of them."""

    def __init__(self, *, art: ArtService | None = None, story: FakeStory = STORY, **overrides: Any) -> None:
        self.repo = InMemoryRepo()
        load_sample_family(self.repo, FAMILY)
        self.tools = ToolLog(make_fake_studio_responder(story))
        services = wiring.fake_services(model=ScriptedChatModel(responder=self.tools), repo=self.repo, art=art)
        self.services = dataclasses.replace(services, **overrides) if overrides else services
        self.manager = wiring.make_manager(self.services)
        self.before = {
            "props": {p.id for p in self.repo.list_props(FAMILY)},
            "sets": {d.id for d in self.repo.list_sets(FAMILY)},
            "memories": {m.id for m in self.repo.find_memories(FAMILY, limit=1000)},
        }

    def names(self, run_id: str) -> list[str]:
        return [name for name, _ in self.manager.events(FAMILY, run_id)]

    def drive(self, *, photo_ids: Sequence[str] = ()) -> tuple[str, list[Event]]:
        """Mom tells a new memory with dad in it; every pause is answered by keeping the props."""
        form = schemas.SubmitMemoryFields(text=MOM_TEXT, year=1994, participant_ids=["dad"])

        async def go() -> tuple[str, list[Event]]:
            run_id = self.manager.start_run(FAMILY, MOM_USER, "mom", form, list(photo_ids))
            seen: list[Event] = []

            async def follow() -> None:
                async for event in self.manager.stream_run(FAMILY, run_id):
                    seen.append(event)

            follower = asyncio.create_task(follow())
            for _ in range(5):
                status = await asyncio.to_thread(self.manager.wait, FAMILY, run_id, WAIT)
                assert status != "running", f"run still running after {WAIT}s: {self.names(run_id)}"
                if status != "waiting":
                    break
                self.manager.answer_run(FAMILY, run_id, MOM_USER, keep_props(self.manager.events(FAMILY, run_id)))
            await asyncio.wait_for(follower, WAIT)
            return run_id, seen

        return asyncio.run(asyncio.wait_for(go(), 4 * WAIT))

    def saved(self, run_id: str, events: list[Event]) -> schemas.Memory:
        """The run ended 'done' and saved a new memory whose timeline validates; returns it."""
        names = [n for n, _ in events]
        assert "error" not in names, [p for n, p in events if n == "error"]
        assert names[-1] == "done" and names.count("done") == 1, names
        assert self.manager.status(FAMILY, run_id) == "done"
        done = events[-1][1]
        assert isinstance(done, schemas.DoneEvent)
        stored = self.repo.get_memory(FAMILY, done.memory_id)
        assert stored is not None and stored.id not in self.before["memories"]
        # Round-trip through the API contract's JSON: the saved shape is what the web reads.
        memory = schemas.Memory.model_validate(stored.model_dump(mode="json", by_alias=True))
        assert memory.timeline is not None and memory.screenplay is not None
        timeline = schemas.Timeline.model_validate(memory.timeline.model_dump(mode="json", by_alias=True))
        assert timeline.duration > 0 and timeline.shots and timeline.tracks
        assert all(shot.page_text and shot.page_text.strip() for shot in timeline.shots)
        assert [t.author_id for t in memory.tellings] == ["mom"]
        # The teller's narration is always there, with word timings for every line.
        track = (timeline.narrations or {}).get("mom")
        assert track is not None and track.lines
        assert all(line.words for line in track.lines if line.text.strip())
        return memory

    def new_props(self) -> list[schemas.Prop]:
        return [p for p in self.repo.list_props(FAMILY) if p.id not in self.before["props"]]

    def new_sets(self) -> list[schemas.SetDef]:
        return [d for d in self.repo.list_sets(FAMILY) if d.id not in self.before["sets"]]


def keep_props(events: list[Event]) -> schemas.AnswerRequest:
    found = [p for n, p in events if n == "props_found"]
    assert found, f"paused without a props review: {[n for n, _ in events]}"
    chips = found[-1].props  # type: ignore[attr-defined]
    return schemas.AnswerRequest(kind="props", props=[schemas.PropEdit(id=c.id, action="keep") for c in chips])


@pytest.fixture
def studios() -> Iterator[Callable[..., Studio]]:
    made: list[Studio] = []

    def make(**kwargs: Any) -> Studio:
        s = Studio(**kwargs)
        made.append(s)
        return s

    yield make
    for s in made:
        s.manager.shutdown(wait=True)


def photo_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (40, 110, 170)).save(buf, format="PNG")
    return buf.getvalue()


def json_results(texts: list[str]) -> list[dict[str, Any]]:
    out = []
    for text in texts:
        value = json.loads(text)
        assert isinstance(value, dict), text
        out.append(value)
    return out


def assert_placeholder_prop(prop: schemas.Prop) -> None:
    assert prop.art and all(media is None for media in prop.art.values()), prop.art
    assert prop.color and prop.color.startswith("#")


def wav_samples(data: bytes) -> bytes:
    with wave.open(io.BytesIO(data)) as wav:
        return wav.readframes(wav.getnframes())


# --------------------------------------------------------------------------- baseline


def test_baseline_with_working_art_every_prop_and_layer_has_art(studios: Callable[..., Studio]) -> None:
    """The control for the art cases: with working fakes the same story gets real art."""
    model = CountingImageModel()
    s = studios(art=ArtService(model, FakeBackgroundRemover()))
    run_id, events = s.drive()
    s.saved(run_id, events)
    (prop,) = s.new_props()
    (set_def,) = s.new_sets()
    assert prop.art.get("body") is not None
    assert [layer.art is not None for layer in set_def.layers] == [True, True, True]
    assert len(model.calls) == 4  # one prop, three layers


# --------------------------------------------------------------------------- (a) images down


def test_a_image_model_down_gives_placeholder_props_and_sets(studios: Callable[..., Studio]) -> None:
    images = DownImageModel()
    s = studios(art=ArtService(images, FakeBackgroundRemover(), max_images_per_run=24))
    run_id, events = s.drive()
    memory = s.saved(run_id, events)

    assert images.calls == 4  # every drawing was tried once, none retried by the tools
    (prop,) = s.new_props()
    (set_def,) = s.new_sets()
    assert_placeholder_prop(prop)
    assert len(set_def.layers) == 3
    assert all(layer.art is None and layer.color for layer in set_def.layers)
    assert memory.screenplay is not None and memory.screenplay.props == [prop.id]
    assert {shot.set for shot in memory.screenplay.shots} == {set_def.id}

    # The tools said "placeholder", and no budget note: the budget was not what stopped the art.
    made = json_results(s.tools.of("generate_prop") + s.tools.of("generate_set_layer"))
    assert len(made) == 4 and all(r["art"] == "placeholder" and "note" not in r for r in made)


# --------------------------------------------------------------------------- (b) no image budget


def test_b_image_budget_of_zero_never_calls_the_image_model(
    studios: Callable[..., Studio], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STITCH_MAX_IMAGES_PER_RUN", "0")
    model = CountingImageModel()
    # Built the way agent.production builds real art: the cap comes from RunLimits.from_env().
    art = ArtService(model, FakeBackgroundRemover(), max_images_per_run=RunLimits.from_env().max_images_per_run)
    assert art.max_images_per_run == 0
    s = studios(art=art)
    run_id, events = s.drive()
    s.saved(run_id, events)

    assert model.calls == []
    (prop,) = s.new_props()
    (set_def,) = s.new_sets()
    assert_placeholder_prop(prop)
    assert all(layer.art is None and layer.color for layer in set_def.layers)
    made = json_results(s.tools.of("generate_prop") + s.tools.of("generate_set_layer"))
    assert len(made) == 4
    assert all(r["art"] == "placeholder" and "image budget" in r["note"] for r in made)


# --------------------------------------------------------------------------- (c) cutout down


def test_c_background_remover_down_gives_placeholder_cutouts(studios: Callable[..., Studio]) -> None:
    model = CountingImageModel()
    remover = DownRemover()
    s = studios(art=ArtService(model, remover))
    run_id, events = s.drive()
    s.saved(run_id, events)

    (prop,) = s.new_props()
    (set_def,) = s.new_sets()
    assert_placeholder_prop(prop)
    by_depth = {layer.depth: layer for layer in set_def.layers}
    assert set(by_depth) == {0, 1, 2}
    # The back layer is a full-bleed backdrop that is never cut out, so it keeps its art;
    # the middle and front layers need the remover and fall back to colored cards.
    assert by_depth[0].art is not None
    assert by_depth[1].art is None and by_depth[1].color
    assert by_depth[2].art is None and by_depth[2].color
    assert remover.calls == 3  # the prop and the two cut-out layers
    made = json_results(s.tools.of("generate_prop"))
    assert [r["art"] for r in made] == ["placeholder"]


def test_c_cut_out_tool_with_the_remover_down_answers_use_a_placeholder() -> None:
    repo = InMemoryRepo()
    load_sample_family(repo, FAMILY)
    photo = repo.put_media(FAMILY, photo_png(), "image/png", kind="photo")
    stored: list[bytes] = []

    def put(data: bytes, content_type: str) -> str:
        stored.append(data)
        return "media/never"

    tool = make_cut_out_tool(ArtService(None, DownRemover()), put, lambda mid: repo.get_media(FAMILY, mid))
    result = json.loads(tool.invoke({"media_id": photo}))
    assert result == {"error": "could not cut out this image; use a placeholder"}
    assert stored == []


# --------------------------------------------------------------------------- (d) speech down


def test_d_speech_service_down_narrates_silently_with_estimated_timings(studios: Callable[..., Studio]) -> None:
    tts = DownTTS()
    s = studios(tts=tts)
    run_id, events = s.drive()
    memory = s.saved(run_id, events)

    assert tts.calls >= 1
    voiced = s.tools.of("synthesize_narration")
    assert voiced, "the narrator never voiced the telling"
    warnings = [line for text in voiced for line in text.splitlines() if line.startswith("warning:")]
    assert any("speech service failed" in w and "ServiceDown" in w for w in warnings), voiced
    assert all("HTTP 500" not in text for text in voiced)  # the provider's message stays in the log

    timeline = memory.timeline
    assert timeline is not None
    track = (timeline.narrations or {})["mom"]
    assert timeline.default_narrator == "mom"
    # Estimated word timings: in timeline time, in order, one per spoken word.
    for line in track.lines:
        words = line.words or []
        assert [w.word for w in words] and all(w.t >= line.t - 1e-6 for w in words)
        assert [w.t for w in words] == sorted(w.t for w in words)
    assert track.captions
    # Silent audio (or none at all): never the provider's, never a test hum.
    if track.audio is not None:
        media = s.repo.get_media(FAMILY, track.audio)
        assert media is not None
        samples = wav_samples(media[0])
        assert samples and not any(samples)


# --------------------------------------------------------------------------- (e) vision down


def test_e_photo_vision_down_skips_the_photo_and_the_run_goes_on(studios: Callable[..., Studio]) -> None:
    vision = DownVision()
    s = studios(vision=vision)
    photo = s.repo.put_media(FAMILY, photo_png(), "image/png", kind="photo")
    run_id, events = s.drive(photo_ids=[photo])
    memory = s.saved(run_id, events)

    assert vision.calls == 1
    assert s.tools.of("describe_photo") == [f"could not describe photo {photo}: ServiceDown"]
    assert photo in memory.tellings[0].photo_ids  # the photo stays with the telling


# --------------------------------------------------------------------------- (f) critic down


def test_f_frame_critic_down_the_checker_passes_and_the_run_saves(studios: Callable[..., Studio]) -> None:
    critic = DownVision()
    s = studios(critic=critic)
    run_id, events = s.drive()
    s.saved(run_id, events)

    assert critic.calls >= 1
    assert s.tools.of("inspect_frames") and all(
        r == "could not inspect frames: ServiceDown" for r in s.tools.of("inspect_frames")
    )
    frames = [p for n, p in events if n == "frames"]
    assert frames and all(f.critique is None for f in frames)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- (g) renderer down


def test_g_browser_renderer_down_falls_back_to_labelled_diagrams(studios: Callable[..., Studio]) -> None:
    diagrams = SpyDiagrams()
    critic = RecordingCritic()
    renderer = PlaywrightFrameRenderer("http://render.invalid", page_factory=no_browser, fallback=diagrams)
    s = studios(renderer=renderer, critic=critic)
    run_id, events = s.drive()
    s.saved(run_id, events)

    assert diagrams.renders >= 1
    rendered = [json.loads(text) for text in s.tools.of("render_frames")]
    assert rendered and all(frame.get("diagram") is True for frames in rendered for frame in frames)
    # The critic is told it is looking at diagrams, not the finished stage.
    assert critic.prompts and all(DIAGRAM_CRITIC_NOTE.strip() in p for p in critic.prompts)
    shown = [p for n, p in events if n == "frames"]
    assert shown and shown[0].frames  # type: ignore[attr-defined]


def test_g_renderer_with_no_fallback_down_the_checker_goes_without_frames(studios: Callable[..., Studio]) -> None:
    renderer = DownRenderer()
    critic = RecordingCritic()
    s = studios(renderer=renderer, critic=critic)
    run_id, events = s.drive()
    s.saved(run_id, events)

    assert renderer.calls >= 1
    assert all(r.startswith("could not render frames:") for r in s.tools.of("render_frames"))
    assert critic.prompts == []  # nothing to inspect
    assert "frames" not in [n for n, _ in events]


# --------------------------------------------------------------------------- tool-level gaps


class BrokenStore(InMemoryRepo):
    """A family store whose media reads, set listing and set saves fail (e.g. MongoDB dropping out)."""

    def get_media(self, family_id: str, media_id: str) -> tuple[bytes, str] | None:
        raise ServiceDown("media store unavailable")

    def find_sets(self, family_id: str, text: str, *, limit: int = 5) -> list[schemas.SetDef]:
        raise ServiceDown("set store unavailable")

    def save_set(self, family_id: str, set_def: schemas.SetDef) -> schemas.SetDef:
        raise ServiceDown("set store unavailable")


def a_run(photo_ids: Sequence[str] = ()) -> RunInfo:
    return RunInfo(
        run_id="run_gaps", family_id=FAMILY, user_id=MOM_USER, author_id="mom",
        fields=schemas.SubmitMemoryFields(text=MOM_TEXT, year=1994), photo_ids=tuple(photo_ids),
    )


def test_describe_photo_with_the_media_store_down_answers_instead_of_raising() -> None:
    repo = BrokenStore()
    (tool,) = make_photo_tools(FakeVision(), lambda mid: repo.get_media(FAMILY, mid), ["media/p1"])
    assert tool.invoke({"photo_id": "media/p1"}) == "could not describe photo media/p1: ServiceDown"


def test_describe_photo_with_an_empty_answer_says_so() -> None:
    class Mute:
        def describe(self, images: Sequence[bytes], prompt: str) -> str:
            return "  \n"

    (tool,) = make_photo_tools(Mute(), {"media/p1": (photo_png(), "image/png")}.get, ["media/p1"])
    assert tool.invoke({"photo_id": "media/p1"}) == "could not describe photo media/p1: the vision model returned no text"


def test_set_tools_with_the_family_store_down_answer_instead_of_raising() -> None:
    find_set, generate_set_layer = make_set_tools(BrokenStore(), a_run(), ArtService(None, None))
    assert find_set.invoke({"place": "lake dock"}).startswith("error: could not search the family's places (ServiceDown)")
    result = generate_set_layer.invoke({"place": "lake dock", "layer": "back", "description": "the far shore"})
    assert result == "error: could not save the back layer (ServiceDown); try again"
    # Bad input still gets its own exact message, not the catch-all.
    bad = generate_set_layer.invoke({"place": "lake dock", "layer": "middle", "description": "x", "marks": {"Bad Name": [2, 9]}})
    assert bad.startswith("error: bad marks")


# --------------------------------------------------------------------------- everything down


def test_every_outside_service_down_at_once_still_saves_the_memory(studios: Callable[..., Studio]) -> None:
    """The first real run happens in front of people: with every outside service failing at
    once (art, cutouts, speech, photo vision, frame critic, browser) the run still ends 'done'."""
    s = studios(
        art=ArtService(DownImageModel(), DownRemover(), max_images_per_run=24),
        tts=DownTTS(),
        vision=DownVision(),
        critic=DownVision(),
        renderer=PlaywrightFrameRenderer("http://render.invalid", page_factory=no_browser, fallback=SpyDiagrams()),
    )
    photo = s.repo.put_media(FAMILY, photo_png(), "image/png", kind="photo")
    run_id, events = s.drive(photo_ids=[photo])
    s.saved(run_id, events)
    (prop,) = s.new_props()
    (set_def,) = s.new_sets()
    assert_placeholder_prop(prop)
    assert all(layer.art is None and layer.color for layer in set_def.layers)
