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
    images      the real adapter gets HTTP 503 over the  the same, after its bounded retries
                wire (OpenAIImageModel, fake transport)
    images      no IMAGE_API_KEY (ArtService(None, ..),  the same, with no image model at all
                as agent.production builds it)
    images      STITCH_MAX_IMAGES_PER_RUN=0              placeholder art, image model never called
    images      a budget of 2 runs out mid-run           2 drawings, the rest placeholder + a note
    cutout      the background remover raises            props, middle and front layers: placeholder
                                                         (the back layer is never cut out: real art)
    tts         the speech service raises on every call  silent audio, estimated word timings, and a
                                                         'warning:' line from synthesize_narration
    tts         the real adapter gets HTTP 500 over the  the same; the provider's message stays in
                wire (ElevenLabsTTS, fake transport)     the server log
    tts         no ELEVENLABS_API_KEY (FakeTTS(tone=     silent audio with real word timings, no
                False), as agent.production builds it)   warning: nothing failed
    vision      describe_photo's model raises            the photo is skipped; the run goes on
    vision      the real adapter's key is rejected       the same, after one request (401 is never
                (AnthropicVision, HTTP 401 over the SDK) retried)
    vision      the API is overloaded with Retry-After:  the same, after capped 8 s waits (the SDK
                3600 (AnthropicVision over the SDK)      alone would wait an hour per retry)
    critic      inspect_frames' model raises             the checker passes without a critique
    renderer    the browser renderer fails               labelled Pillow diagrams (FakeFrameRenderer)
    renderer    a renderer with no fallback fails        no frames; the checker passes without them
    repo        the family store drops out mid-run:      each tool answers 'error: ... (<Type>);
                prop and set searches and media reads    try again ...' or 'could not describe
                raise                                    photo ...'; the studio generates instead

No ANTHROPIC_API_KEY at all is not a row: agent.production refuses to build the studio without
it (the studio model needs it), so photo vision can never be the only service missing its key.

No keys, no network; every wait has a timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import io
import json
import wave
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import anthropic._base_client as sdk_base
import httpx
import pytest
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from PIL import Image
from pydantic import BaseModel

import schemas
from agent import wiring
from agent.adapters import AlphaAwareRemover, AnthropicVision, ElevenLabsTTS, OpenAIImageModel
from agent.adapters import images as images_mod
from agent.adapters import vision as vision_mod
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
from agent.tools.narration import FakeTTS, Speech
from agent.tools.photos import FakeVision, make_photo_tools
from agent.tools.props import make_prop_tools
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

    def __init__(
        self,
        *,
        art: ArtService | None = None,
        story: FakeStory = STORY,
        repo: InMemoryRepo | None = None,
        **overrides: Any,
    ) -> None:
        self.repo = repo if repo is not None else InMemoryRepo()
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


def speech_warning(exc_name: str) -> str:
    """synthesize_narration's line when the speech service failed (agent.tools.narration)."""
    return f"warning: the speech service failed ({exc_name}); narrated silently with estimated word timings"


def warnings_of(texts: list[str]) -> list[str]:
    return [line for text in texts for line in text.splitlines() if line.startswith("warning:")]


def assert_silent_narration(s: Studio, memory: schemas.Memory) -> None:
    """The teller's track: word timings in order on every spoken line, captions, and silent audio."""
    timeline = memory.timeline
    assert timeline is not None
    assert timeline.default_narrator == "mom"
    track = (timeline.narrations or {})["mom"]
    spoken = [line for line in track.lines if line.text.strip()]
    assert spoken
    for line in spoken:
        words = line.words or []
        assert words, line
        assert all(w.t >= line.t - 1e-6 for w in words)
        assert [w.t for w in words] == sorted(w.t for w in words)
    assert track.captions
    # Silence: never the provider's audio, never a test hum.
    assert track.audio is not None
    media = s.repo.get_media(FAMILY, track.audio)
    assert media is not None and media[1] == "audio/wav"
    samples = wav_samples(media[0])
    assert samples and not any(samples)


def no_sleep(seconds: float) -> None:
    """The real adapters' retry waits, skipped (each test still checks how many requests were made)."""


class WireLog:
    """A fake provider over httpx (or httpx2, for the anthropic SDK): every request answers `status`."""

    def __init__(self, transport_module: Any, status: int, *, headers: dict[str, str] | None = None,
                 body: dict[str, Any] | None = None) -> None:
        self.module = transport_module
        self.status = status
        self.headers = headers or {}
        self.body = body or {"error": {"message": "provider is down"}}
        self.requests: list[Any] = []

    def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        return self.module.Response(self.status, headers=self.headers, json=self.body)

    def transport(self) -> Any:
        return self.module.MockTransport(self)


SDK_HTTPX = getattr(sdk_base, "httpx2", None) or sdk_base.httpx
"""The anthropic SDK speaks httpx2, a separate package, so its fake transport is httpx2's."""


def anthropic_error(kind: str) -> dict[str, Any]:
    return {"type": "error", "error": {"type": kind, "message": "nope"}}


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


def test_a_image_api_down_over_the_wire_gives_placeholder_art(studios: Callable[..., Studio]) -> None:
    """The production adapter, not a stand-in: HTTP 503 on every request, retried within its bound."""
    api = WireLog(httpx, 503)
    model = OpenAIImageModel(api_key="sk-test-not-a-key", transport=api.transport(), sleep=no_sleep)
    s = studios(art=ArtService(model, AlphaAwareRemover(), max_images_per_run=24))
    run_id, events = s.drive()
    s.saved(run_id, events)

    # Four drawings (one prop, three layers), each tried 1 + DEFAULT_MAX_RETRIES times.
    assert len(api.requests) == 4 * (1 + images_mod.DEFAULT_MAX_RETRIES)
    (prop,) = s.new_props()
    (set_def,) = s.new_sets()
    assert_placeholder_prop(prop)
    assert all(layer.art is None and layer.color for layer in set_def.layers)
    made = json_results(s.tools.of("generate_prop") + s.tools.of("generate_set_layer"))
    assert len(made) == 4 and all(r["art"] == "placeholder" and "note" not in r for r in made)


def test_a_no_image_key_gives_placeholder_art(studios: Callable[..., Studio]) -> None:
    """IMAGE_API_KEY unset: agent.production builds ArtService(None, AlphaAwareRemover())."""
    s = studios(art=ArtService(None, AlphaAwareRemover()))
    run_id, events = s.drive()
    s.saved(run_id, events)

    (prop,) = s.new_props()
    (set_def,) = s.new_sets()
    assert_placeholder_prop(prop)
    assert all(layer.art is None and layer.color for layer in set_def.layers)
    made = json_results(s.tools.of("generate_prop") + s.tools.of("generate_set_layer"))
    # No budget note: there is no budget to spend, only no image model.
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


def test_b_image_budget_used_up_mid_run_makes_the_rest_placeholder_art(studios: Callable[..., Studio]) -> None:
    model = CountingImageModel()
    s = studios(art=ArtService(model, FakeBackgroundRemover(), max_images_per_run=2))
    run_id, events = s.drive()
    s.saved(run_id, events)

    assert len(model.calls) == 2  # the cap, never more
    (prop,) = s.new_props()
    (set_def,) = s.new_sets()
    drawn = [prop.art.get("body") is not None] + [layer.art is not None for layer in set_def.layers]
    assert sorted(drawn) == [False, False, True, True]
    made = json_results(s.tools.of("generate_prop") + s.tools.of("generate_set_layer"))
    assert len(made) == 4
    generated = [r for r in made if r["art"] == "generated"]
    placeholder = [r for r in made if r["art"] == "placeholder"]
    assert len(generated) == 2 and all("note" not in r for r in generated)
    # Which two calls came first depends on thread timing; whichever were refused say why.
    assert len(placeholder) == 2
    assert all("image budget for this run is used up (2 images)" in r["note"] for r in placeholder)


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

    assert tts.calls >= 2  # the voice pass retries the speech service once before going silent
    voiced = s.tools.of("synthesize_narration")
    assert voiced, "the narrator never voiced the telling"
    assert speech_warning("ServiceDown") in warnings_of(voiced), voiced
    assert all("HTTP 500" not in text for text in voiced)  # the provider's message stays in the log
    assert_silent_narration(s, memory)


def test_d_speech_api_down_over_the_wire_narrates_silently(studios: Callable[..., Studio]) -> None:
    """The production adapter, not a stand-in: HTTP 500 on every request, retried within its bound."""
    api = WireLog(httpx, 500)
    tts = ElevenLabsTTS(api_key="el-test-not-a-key", transport=api.transport(), sleep=no_sleep)
    s = studios(tts=tts)
    run_id, events = s.drive()
    memory = s.saved(run_id, events)

    assert api.requests  # the provider was really asked
    voiced = s.tools.of("synthesize_narration")
    # The adapter raises RuntimeError naming the status; the tool names only the type.
    assert speech_warning("RuntimeError") in warnings_of(voiced), voiced
    assert all("HTTP 500" not in text and "provider is down" not in text for text in voiced)
    assert_silent_narration(s, memory)


def test_d_no_speech_key_narrates_silently_without_a_warning(studios: Callable[..., Studio]) -> None:
    """ELEVENLABS_API_KEY unset: agent.production voices every teller with FakeTTS(tone=False)."""
    s = studios(tts=FakeTTS(tone=False))
    run_id, events = s.drive()
    memory = s.saved(run_id, events)

    voiced = s.tools.of("synthesize_narration")
    assert voiced and warnings_of(voiced) == []  # nothing failed: silence is the configured voice
    assert_silent_narration(s, memory)


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


def wired_vision(api: WireLog, sleeps: list[float], monkeypatch: pytest.MonkeyPatch) -> AnthropicVision:
    """The production vision adapter on the real SDK client it builds, over a fake transport."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-key")
    http_client = SDK_HTTPX.Client(transport=api.transport())
    return AnthropicVision(http_client=http_client, sleep=sleeps.append, timeout=5, max_retries=2)


def test_e_vision_key_rejected_skips_the_photo_after_one_request(
    studios: Callable[..., Studio], monkeypatch: pytest.MonkeyPatch
) -> None:
    api = WireLog(SDK_HTTPX, 401, body=anthropic_error("authentication_error"))
    sleeps: list[float] = []
    s = studios(vision=wired_vision(api, sleeps, monkeypatch))
    photo = s.repo.put_media(FAMILY, photo_png(), "image/png", kind="photo")
    run_id, events = s.drive(photo_ids=[photo])
    memory = s.saved(run_id, events)

    assert len(api.requests) == 1 and sleeps == []  # a refused key is never retried
    assert s.tools.of("describe_photo") == [f"could not describe photo {photo}: AuthenticationError"]
    assert photo in memory.tellings[0].photo_ids


def test_e_vision_overloaded_waits_at_most_the_cap_then_skips_the_photo(
    studios: Callable[..., Studio], monkeypatch: pytest.MonkeyPatch
) -> None:
    api = WireLog(SDK_HTTPX, 529, headers={"retry-after": "3600"}, body=anthropic_error("overloaded_error"))
    sleeps: list[float] = []
    s = studios(vision=wired_vision(api, sleeps, monkeypatch))
    photo = s.repo.put_media(FAMILY, photo_png(), "image/png", kind="photo")
    run_id, events = s.drive(photo_ids=[photo])
    s.saved(run_id, events)

    # Three requests, two waits of at most 8 s each, where the SDK alone would wait 3600 s twice.
    assert len(api.requests) == 3
    assert sleeps == [vision_mod.MAX_BACKOFF_S, vision_mod.MAX_BACKOFF_S]
    (answer,) = s.tools.of("describe_photo")
    assert answer.startswith(f"could not describe photo {photo}: ") and "3600" not in answer


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


def test_set_layer_from_a_photo_the_store_cannot_read_answers_try_again() -> None:
    repo = BrokenStore()
    find_set, generate_set_layer = make_set_tools(repo, a_run(["media/p1"]), ArtService(None, None))
    result = generate_set_layer.invoke(
        {"place": "lake dock", "layer": "back", "description": "the far shore", "reference_photo_id": "media/p1"}
    )
    assert result == (
        "error: could not read photo 'media/p1' (ServiceDown); nothing was saved; "
        "try again, or generate the layer without the photo"
    )


class PropStoreDown(InMemoryRepo):
    """A family store whose prop search, media reads and media writes fail."""

    def find_props(self, family_id: str, text: str, *, limit: int = 5) -> list[schemas.Prop]:
        raise ServiceDown("prop store unavailable")

    def get_media(self, family_id: str, media_id: str) -> tuple[bytes, str] | None:
        raise ServiceDown("media store unavailable")

    def put_media(self, family_id: str, data: bytes, content_type: str, *, kind: str) -> str:
        raise ServiceDown("media store unavailable")


def test_prop_tools_with_the_family_store_down_answer_instead_of_raising() -> None:
    repo = PropStoreDown()
    load_sample_family(repo, FAMILY)
    find_prop, generate_prop, confirm_props = make_prop_tools(
        repo, a_run(["media/p1"]), ArtService(FakeImageModel(), FakeBackgroundRemover())
    )
    assert find_prop.invoke({"description": "picnic basket"}) == (
        "error: could not search the family's props (ServiceDown); try again, and if it fails again generate the prop"
    )
    from_photo = generate_prop.invoke(
        {"name": "picnic basket", "description": "wicker", "size": "carried", "reference_photo_id": "media/p1"}
    )
    assert from_photo == (
        "error: could not read photo 'media/p1' (ServiceDown); try again, or generate the prop without the photo"
    )
    # Drawn, but the media store cannot keep the drawing: a placeholder card is saved instead.
    made = json.loads(generate_prop.invoke({"name": "picnic basket", "description": "wicker", "size": "carried"}))
    assert made["art"] == "placeholder" and made["preview"] is None and "note" not in made
    saved = repo.get_prop(FAMILY, made["id"])
    assert saved is not None
    assert_placeholder_prop(saved)


def test_prop_review_with_the_prop_store_down_answers_instead_of_raising() -> None:
    class ReviewStoreDown(InMemoryRepo):
        def get_prop(self, family_id: str, prop_id: str) -> schemas.Prop | None:
            raise ServiceDown("prop store unavailable")

    _, _, confirm_props = make_prop_tools(ReviewStoreDown(), a_run(), ArtService(None, None))
    chip = {"id": "picnic_basket", "name": "picnic basket", "description": None, "size": "carried",
            "art": None, "reused": False}
    answer = confirm_props.invoke({"props": [chip], "edits": [{"id": "picnic_basket", "action": "keep"}]})
    assert answer == "error: could not apply the review (ServiceDown); keep the props as they were"


class StoreDropsOut(InMemoryRepo):
    """The family store, seeded and working, that drops out once the run starts: prop and set
    searches and reads of the uploaded photos raise (writes and the other reads still work).

    Only the photos' reads fail: inspect_frames (agent.tools.frames) and the family tools
    (agent.tools.family) still raise on a failing store, which ends the run; those belong to
    other owners and are reported, not covered here."""

    FAILING = ("find_props", "find_sets", "get_media")

    def __init__(self) -> None:
        super().__init__()
        self.down = False
        self.photos: set[str] = set()
        self.failed: Counter[str] = Counter()

    def _check(self, what: str) -> None:
        if self.down:
            self.failed[what] += 1
            raise ServiceDown(f"{what}: the family store is unavailable")

    def find_props(self, family_id: str, text: str, *, limit: int = 5) -> list[schemas.Prop]:
        self._check("find_props")
        return super().find_props(family_id, text, limit=limit)

    def find_sets(self, family_id: str, text: str, *, limit: int = 5) -> list[schemas.SetDef]:
        self._check("find_sets")
        return super().find_sets(family_id, text, limit=limit)

    def get_media(self, family_id: str, media_id: str) -> tuple[bytes, str] | None:
        if media_id in self.photos:
            self._check("get_media")
        return super().get_media(family_id, media_id)


def test_h_family_store_dropping_out_mid_run_answers_the_agent_and_the_memory_saves(
    studios: Callable[..., Studio],
) -> None:
    store = StoreDropsOut()
    s = studios(repo=store, art=ArtService(CountingImageModel(), FakeBackgroundRemover()))
    photo = s.repo.put_media(FAMILY, photo_png(), "image/png", kind="photo")
    store.photos.add(photo)
    store.down = True
    run_id, events = s.drive(photo_ids=[photo])
    s.saved(run_id, events)

    assert set(store.failed) == set(StoreDropsOut.FAILING), store.failed
    # Each read that failed answered the agent with a line it can act on; none raised.
    assert s.tools.of("find_prop") == [
        "error: could not search the family's props (ServiceDown); try again, and if it fails again generate the prop"
    ]
    assert s.tools.of("find_set") == ["error: could not search the family's places (ServiceDown); try again"]
    assert s.tools.of("describe_photo") == [f"could not describe photo {photo}: ServiceDown"]
    # The studio generated what it could not find, with real art (writes still work).
    (prop,) = s.new_props()
    (set_def,) = s.new_sets()
    assert prop.art.get("body") is not None
    assert all(layer.art is not None for layer in set_def.layers)


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
    memory = s.saved(run_id, events)
    (prop,) = s.new_props()
    (set_def,) = s.new_sets()
    assert_placeholder_prop(prop)
    assert all(layer.art is None and layer.color for layer in set_def.layers)
    assert s.tools.of("describe_photo") == [f"could not describe photo {photo}: ServiceDown"]
    assert speech_warning("ServiceDown") in warnings_of(s.tools.of("synthesize_narration"))
    assert_silent_narration(s, memory)
    assert all(r == "could not inspect frames: ServiceDown" for r in s.tools.of("inspect_frames"))
    rendered = [json.loads(text) for text in s.tools.of("render_frames")]
    assert rendered and all(frame.get("diagram") is True for frames in rendered for frame in frames)
