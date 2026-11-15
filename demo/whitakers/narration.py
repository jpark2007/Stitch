"""Portable recorded narration for the fictional Macosko demo.

Bundled recordings need no key/network. New or edited narration can opt into
ElevenLabs with ELEVENLABS_API_KEY; generated audio stays in the user's cache.
The cache identity covers spoken lines, their shot start times, voice and model.
Animation, photos and credentials are never stored in the bundled manifest.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from pathlib import Path
import threading
import uuid
import wave

from db import media as media_db
from screenplay.narration import default_author
from screenplay.schema import NarrationTrack, Screenplay, Timeline

logger = logging.getLogger(__name__)
VOICE = "cjVigY5qzO86Huf0OWal"  # Eric — Smooth, Trustworthy, also used by the call agent.
MODEL = "eleven_multilingual_v2"
BUNDLE = Path(__file__).resolve().parents[1] / "macoskos" / "data" / "narration"
_lock = threading.RLock()


def identity(timeline: Timeline, lines: dict, *, voice: str = VOICE, model: str = MODEL) -> str:
    payload = {
        "version": 1, "voice": voice, "model": model, "format": "pcm_16000",
        "starts": [[shot.id, shot.action_start] for shot in timeline.shots],
        "lines": {sid: [line.model_dump(mode="json", by_alias=True, exclude_none=True,
                                        exclude={"source", "words"}) for line in entries]
                  for sid, entries in lines.items()},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _audio_info(audio: bytes) -> float:
    with wave.open(io.BytesIO(audio), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getnframes() < 1:
            raise ValueError("Invalid demo narration WAV")
        return wav.getnframes() / wav.getframerate()


def _atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
    os.replace(temporary, path)


def _read(directory: Path, entry: dict) -> tuple[bytes, dict, float]:
    filename = entry["file"]
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".wav"):
        raise ValueError("Invalid demo narration filename")
    audio = (directory / filename).read_bytes()
    if hashlib.sha256(audio).hexdigest() != entry["sha256"]:
        raise ValueError("Demo narration checksum mismatch")
    duration = _audio_info(audio)
    if abs(duration - float(entry["audioEnd"])) > .01:
        raise ValueError("Demo narration duration mismatch")
    return audio, entry["track"], duration


def recording(timeline: Timeline, lines: dict, *, voice: str, model: str,
              bundle: Path = BUNDLE, cache: Path | None = None, tts=None,
              allow_generate: bool = True) -> tuple[bytes, dict, float] | None:
    """Find an exact recording, or synthesize only with an explicitly configured key/TTS."""
    from agent.tools.narration import build_narration_track

    key = identity(timeline, lines, voice=voice, model=model)
    if (bundle / "manifest.json").is_file():
        manifest = json.loads((bundle / "manifest.json").read_text())
        if manifest.get("version") != 1:
            raise ValueError("Unsupported demo narration manifest version")
        if entry := manifest["recordings"].get(key):
            return _read(bundle, entry)
    cache = cache or Path(os.environ.get("STITCH_DEMO_NARRATION_CACHE", "~/.cache/stitch/demo-narration")).expanduser()
    with _lock:
        metadata = cache / (key + ".json")
        if metadata.is_file():
            return _read(cache, json.loads(metadata.read_text()))
        if not allow_generate or (tts is None and not os.environ.get("ELEVENLABS_API_KEY", "").strip()):
            return None
        close = False
        if tts is None:
            from agent.adapters.tts import ElevenLabsTTS
            tts = ElevenLabsTTS(voices={"demo": voice}, model=model)
            close = True
        captured: list[bytes] = []
        def put(audio: bytes, mime: str) -> str:
            captured.append(audio)
            return "prepared-audio"
        try:
            track, end = build_narration_track(timeline, lines, author_id="prepared", tts=tts,
                                               voice="demo", put_media=put)
        finally:
            if close:
                tts.close()
        if len(captured) != 1 or not track.lines:
            raise ValueError("No recorded demo narration was generated")
        audio = captured[0]
        _audio_info(audio)
        filename = key + ".wav"
        entry = {"file": filename, "sha256": hashlib.sha256(audio).hexdigest(), "audioEnd": end,
                 "track": track.model_dump(mode="json", by_alias=True, exclude={"author_id", "audio"})}
        _atomic(cache / filename, audio)
        _atomic(metadata, json.dumps(entry, sort_keys=True).encode())
        return _read(cache, entry)


def attach_narration(db, family_id: str, screenplay: Screenplay, timeline: Timeline, *,
                     bundle: Path = BUNDLE, cache: Path | None = None, tts=None,
                     allow_generate: bool = True) -> Timeline:
    """Add real recordings through normal family-scoped media storage; never mutate the input."""
    if family_id != "macoskos" or os.environ.get("STITCH_DEMO_NARRATION", "").lower() == "off":
        return timeline
    from agent.tools.narration import apply_narration, lines_for_author

    voice = os.environ.get("ELEVENLABS_DEFAULT_VOICE", "").strip() or VOICE
    model = os.environ.get("ELEVENLABS_MODEL", "").strip() or MODEL
    default = default_author(screenplay)
    authors = [default, *[a for a in (screenplay.narrations or {}) if a != default]]
    updated = timeline
    for author in authors:
        lines = lines_for_author(screenplay, author, second_telling=author != default)
        if not any(line.text.strip() for entries in lines.values() for line in entries):
            continue
        found = recording(timeline, lines, voice=voice, model=model, bundle=bundle, cache=cache,
                          tts=tts, allow_generate=allow_generate)
        if found is None:
            logger.warning("No matching recorded narration for this Macosko telling; browser speech remains available")
            continue
        audio, data, end = found
        # Source references belong to the current telling, even when its identical
        # spoken words reuse a recording originally prepared for another teller.
        sources = [line.source for shot in timeline.shots
                   for line in sorted(lines.get(shot.id, []), key=lambda line: line.t) if line.text.strip()]
        data = {**data, "lines": [{**line, "source": source}
                                  for line, source in zip(data["lines"], sources, strict=True)]}
        media_id = media_db.put_media(db, family_id, audio, content_type="audio/wav",
                                     filename="narration.wav", kind="audio")
        track = NarrationTrack.model_validate({**data, "authorId": author, "audio": media_id})
        updated = apply_narration(updated, track, end, make_default=author == default)
    return updated
