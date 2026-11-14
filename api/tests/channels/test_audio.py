"""Native CAF conversion: local synthetic fixtures only, no provider request."""

import asyncio
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import wave

import pytest

from channels import audio, voice
from channels.models import InboundVoice
from db import media
from tests.channels.test_voice import stack, voice_stack, envelope, post


def pcm_wav(frames=1600):
    output = io.BytesIO()
    with wave.open(output, "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(16000)
        sound.writeframes(b"\0\0" * frames)
    return output.getvalue()


def caf_bytes():
    desc = struct.pack(">d4sIIIII", 16000.0, b"lpcm", 2, 2, 1, 1, 16)
    def chunk(name, data):
        return name + len(data).to_bytes(8, "big", signed=True) + data
    return b"caff\0\1\0\0" + chunk(b"desc", desc) + chunk(b"data", b"\0" * 4 + b"\0\0" * 1600)


@pytest.fixture
def real_caf(tmp_path):
    if sys.platform != "darwin" or not Path("/usr/bin/afconvert").exists():
        pytest.skip("macOS afconvert is unavailable")
    source, target = tmp_path / "sample.wav", tmp_path / "sample.caf"
    source.write_bytes(pcm_wav())
    result = subprocess.run(["/usr/bin/afconvert", "-f", "caff", "-d", "LEI16", str(source), str(target)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    assert result.returncode == 0
    return target.read_bytes()


def test_real_mac_converter_round_trip(real_caf):
    audio.validate_caf(real_caf)
    result = asyncio.run(audio.convert_caf(real_caf))
    audio._wav(result)
    with wave.open(io.BytesIO(result), "rb") as sound:
        assert sound.getnframes() == 1600
        assert sound.getframerate() == 16000


def test_worker_transcribes_verified_wav_and_keeps_original_caf(voice_stack, real_caf):
    client, db, worker, transcriber, _, _ = voice_stack
    assert post(client, envelope(real_caf, mimeType="audio/x-caf")).status_code == 202
    receipt = db.channelMessages.find_one({"receiptType": "voice"})
    original_id = receipt["audioId"]
    assert worker.tick()["processed"] == 1
    data, mime = transcriber.calls[0]
    assert mime == "audio/wav"
    audio._wav(data)
    original = media.get_media(db, "family-a", original_id)
    assert original.data == real_caf and original.content_type == "audio/x-caf"
    assert db.channelMessages.find_one({"receiptType": "voice"})["audioId"] == original_id


@pytest.mark.parametrize("data", [b"", b"https://evil.invalid/a.caf", b"RIFFxxxxWAVE", b"caff\0\1\0\0", caf_bytes()[:-1]])
def test_forged_caf_rejected_before_converter(data, monkeypatch):
    monkeypatch.setattr(audio, "_converter", lambda: pytest.fail("converter consulted for invalid CAF"))
    with pytest.raises(audio.AudioConversionError, match="invalid_caf"):
        asyncio.run(audio.convert_caf(data))


def test_caf_mime_routes_only_to_specific_header_validation():
    data = caf_bytes()
    assert voice.decode_voice(InboundVoice.model_validate(envelope(data, mimeType="audio/x-caf"))) == data
    with pytest.raises(ValueError):
        voice.decode_voice(InboundVoice.model_validate(envelope(pcm_wav(), mimeType="audio/x-caf")))


def test_missing_converter_blocks_before_provider(voice_stack, monkeypatch):
    client, db, worker, transcriber, _, _ = voice_stack
    def missing():
        raise audio.AudioConversionError("missing_converter")
    monkeypatch.setattr(audio, "_converter", missing)
    assert post(client, envelope(caf_bytes(), mimeType="audio/x-caf")).status_code == 202
    assert worker.tick()["blocked"] == 1 and not transcriber.calls
    record = db.conversations.find_one({"kind": "channelWorker"})
    assert record["reason"] == "audio_conversion_missing_converter"
    worker.tick()
    assert not transcriber.calls


def fake_converter(tmp_path, body):
    executable = tmp_path / "fake-converter"
    executable.write_text("#!" + sys.executable + "\n" + body)
    executable.chmod(0o700)
    return "afconvert", str(executable)


def test_timeout_kills_only_owned_process_and_removes_files(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    tool = fake_converter(tmp_path, f"import os,sys,time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(str(os.getpid())+'\\n'+str(Path.cwd()))\ntime.sleep(30)\n")
    monkeypatch.setattr(audio, "_converter", lambda: tool)
    monkeypatch.setattr(audio, "CONVERSION_TIMEOUT", 1.0)
    with pytest.raises(audio.AudioConversionError, match="conversion_timeout"):
        asyncio.run(audio.convert_caf(caf_bytes()))
    pid, directory = marker.read_text().splitlines()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid), 0)
    assert not Path(directory).exists()


def test_cancellation_kills_child_and_cleans_private_directory(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    tool = fake_converter(tmp_path, f"import os,time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(str(os.getpid())+'\\n'+str(Path.cwd()))\ntime.sleep(30)\n")
    monkeypatch.setattr(audio, "_converter", lambda: tool)
    async def cancel():
        task = asyncio.create_task(audio.convert_caf(caf_bytes()))
        async with asyncio.timeout(3):
            while not marker.exists():
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(cancel())
    pid, directory = marker.read_text().splitlines()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid), 0)
    assert not Path(directory).exists()


def test_output_file_limit_prevents_unbounded_write(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    tool = fake_converter(tmp_path, f"import os,sys\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(str(Path.cwd()))\nf=open(sys.argv[-1],'wb')\nwhile True: f.write(b'x'*1048576); f.flush()\n")
    monkeypatch.setattr(audio, "_converter", lambda: tool)
    with pytest.raises(audio.AudioConversionError):
        asyncio.run(audio.convert_caf(caf_bytes()))
    assert not Path(marker.read_text()).exists()


def test_invalid_or_overlong_decoded_output_is_not_sent(tmp_path, monkeypatch):
    for result, expected in [(b"not WAV", "invalid_output"), (pcm_wav(16000 * 301), "duration_limit")]:
        async def fake(arguments, directory):
            Path(arguments[-1]).write_bytes(result)
        monkeypatch.setattr(audio, "_converter", lambda: ("afconvert", "/unused"))
        monkeypatch.setattr(audio, "_run", fake)
        with pytest.raises(audio.AudioConversionError, match=expected):
            asyncio.run(audio.convert_caf(caf_bytes()))


def test_converter_environment_has_no_provider_secrets(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    tool = fake_converter(tmp_path, f"import os,sys\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(','.join(os.environ))\nsys.exit(1)\n")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("CHANNELS_SHARED_SECRET", "must-not-inherit")
    monkeypatch.setattr(audio, "_converter", lambda: tool)
    with pytest.raises(audio.AudioConversionError):
        asyncio.run(audio.convert_caf(caf_bytes()))
    names = marker.read_text().split(",")
    assert "OPENAI_API_KEY" not in names and "CHANNELS_SHARED_SECRET" not in names


def test_stop_during_conversion_prevents_provider_upload(voice_stack, monkeypatch):
    client, db, worker, transcriber, _, _ = voice_stack
    async def convert(data):
        db.contacts.update_many({}, {"$set": {"status": "stopped"}})
        return pcm_wav()
    monkeypatch.setattr(voice, "convert_caf", convert)
    assert post(client, envelope(caf_bytes(), mimeType="audio/x-caf")).status_code == 202
    assert worker.tick()["blocked"] == 1 and not transcriber.calls
