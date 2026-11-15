"""Recorded Macosko narration is portable, authenticated media and offline by default."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from agent.adapters.tts import ElevenLabsTTS
from agent.tools.narration import FakeTTS
from db import media as media_db, memories as memories_db
from demo.replay.fixtures import load_stories
from demo.replay.manager import ReplayRunManager
from demo.whitakers import narration as n
from demo.whitakers.data import load_family
from demo.whitakers.seed import seed_family

DATA = Path(__file__).resolve().parents[2] / "macoskos" / "data"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in ("ELEVENLABS_API_KEY", "ELEVENLABS_DEFAULT_VOICE", "ELEVENLABS_MODEL", "STITCH_DEMO_NARRATION"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("STITCH_DEMO_NARRATION_CACHE", str(tmp_path / "cache"))


def test_all_seeded_tellings_have_portable_audio_offline(mdb, monkeypatch, tmp_path):
    def forbidden(*a, **kw):
        raise AssertionError("Offline seed called a provider")
    monkeypatch.setattr(ElevenLabsTTS, "synthesize", forbidden)
    # A private photo/data directory still finds the public bundled recordings.
    target = tmp_path / "private-data"
    shutil.copytree(DATA, target, ignore=shutil.ignore_patterns("narration"))
    data = load_family(target)
    assert seed_family(mdb, data)
    tracks = 0
    for moment in data.moments:
        saved = memories_db.get_memory(mdb, data.family_id, moment.id)
        assert saved.timeline.audio
        for author, track in saved.timeline.narrations.items():
            assert track.author_id == author
            media = media_db.get_media(mdb, data.family_id, track.audio)
            assert media.content_type == "audio/wav" and media.data[:4] == b"RIFF"
            assert media_db.get_media(mdb, "other-family", track.audio) is None
            tracks += 1
    assert tracks == 19


@pytest.mark.parametrize("story_name,teller", [("canoe", "rick"), ("marmalade", "jenna")])
def test_actual_prepared_replay_hook_uses_bundled_audio(mdb, monkeypatch, story_name, teller):
    seed_family(mdb, load_family(DATA))
    story = next(s for s in load_stories(DATA / "replays") if s.name == story_name)
    def forbidden(*a, **kw):
        raise AssertionError("Prepared replay called a provider")
    monkeypatch.setattr(ElevenLabsTTS, "synthesize", forbidden)
    mgr = ReplayRunManager(stories=[story], db_getter=lambda: mdb, step_seconds=0)
    try:
        staged = mgr._build_scene(SimpleNamespace(family_id="macoskos", person_id=teller), mdb,
                                  SimpleNamespace(story=story, chip_of={}), [], set())
        assert staged.result.ok
        timeline = staged.result.timeline
        assert timeline.default_narrator == teller
        assert timeline.audio and timeline.narrations[teller].audio == timeline.audio
        assert media_db.get_media(mdb, "macoskos", timeline.audio).data[:4] == b"RIFF"
        assert all(line.source.startswith("telling:" + teller + ":") for line in timeline.narrations[teller].lines)
    finally:
        mgr.shutdown()


def test_edited_story_does_not_reuse_stale_recording(mdb, caplog):
    moment = load_family(DATA).moments[0]
    screenplay = moment.screenplay.model_copy(deep=True)
    screenplay.shots[0].narration[0].text += " This is different."
    result = n.attach_narration(mdb, "macoskos", screenplay, moment.timeline)
    assert result.audio is None
    assert "No matching recorded narration" in caplog.text


def test_new_story_generated_once_with_mock_provider(mdb, tmp_path):
    moment = load_family(DATA).moments[0]
    screenplay = moment.screenplay.model_copy(deep=True)
    screenplay.shots[0].narration[0].text += " New words."
    class Provider:
        calls = 0
        def synthesize(self, text, *, voice=None):
            self.calls += 1
            return FakeTTS().synthesize(text)
    provider = Provider()
    first = n.attach_narration(mdb, "macoskos", screenplay, moment.timeline, tts=provider)
    calls = provider.calls
    second = n.attach_narration(mdb, "macoskos", screenplay, moment.timeline, tts=provider)
    assert first.audio and second.audio and calls > 0 and provider.calls == calls
    assert moment.timeline.audio is None


def test_bundle_checksum_and_path_are_checked(tmp_path):
    manifest = json.loads((n.BUNDLE / "manifest.json").read_text())
    entry = next(iter(manifest["recordings"].values()))
    with pytest.raises(ValueError, match="filename"):
        n._read(n.BUNDLE, {**entry, "file": "../outside.wav"})
    with pytest.raises(ValueError, match="checksum"):
        n._read(n.BUNDLE, {**entry, "sha256": "wrong"})


def test_other_families_and_off_setting_unchanged(mdb, monkeypatch):
    moment = load_family(DATA).moments[0]
    assert n.attach_narration(mdb, "other", moment.screenplay, moment.timeline) is moment.timeline
    monkeypatch.setenv("STITCH_DEMO_NARRATION", "off")
    assert n.attach_narration(mdb, "macoskos", moment.screenplay, moment.timeline) is moment.timeline


def test_check_and_seed_never_generate_with_a_key_present(mdb, monkeypatch):
    data = load_family(DATA)
    data.moments[0].screenplay.shots[0].narration[0].text += " Changed in local data."
    monkeypatch.setenv("ELEVENLABS_API_KEY", "not-a-real-key")
    monkeypatch.setattr(ElevenLabsTTS, "synthesize", lambda *a, **k: pytest.fail("Seed made a provider request"))
    assert seed_family(mdb, data)
