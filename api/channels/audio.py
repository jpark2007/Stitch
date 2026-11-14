"""Optional local CAF decoding. Fixed executables/options, private files, no network or secrets."""

import asyncio
import io
import math
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import wave

MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_DECODED_SECONDS = 300
CONVERSION_TIMEOUT = 15
CAF_MIME = "audio/x-caf"


class AudioConversionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Audio conversion unavailable ({code}); no transcription request was made")


def validate_caf(data: bytes) -> None:
    if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_AUDIO_BYTES or data[:8] != b"caff\0\1\0\0":
        raise AudioConversionError("invalid_caf")
    offset, count, description, samples = 8, 0, False, False
    while offset < len(data):
        count += 1
        if count > 1024 or len(data) - offset < 12:
            raise AudioConversionError("invalid_caf")
        kind = data[offset:offset + 4]
        size = int.from_bytes(data[offset + 4:offset + 12], "big", signed=True)
        offset += 12
        if size == -1 and kind == b"data":
            size = len(data) - offset
        if size < 0 or offset + size > len(data):
            raise AudioConversionError("invalid_caf")
        if kind == b"desc":
            if description or size != 32:
                raise AudioConversionError("invalid_caf")
            rate, codec, flags, packet_bytes, frames, channels, bits = struct.unpack(
                ">d4sIIIII", data[offset:offset + size])
            if not math.isfinite(rate) or not 0 < rate <= 384000 or not 1 <= channels <= 8 or codec == b"\0" * 4:
                raise AudioConversionError("invalid_caf")
            description = True
        elif kind == b"data":
            if samples or size <= 4:
                raise AudioConversionError("invalid_caf")
            samples = True
        offset += size
    if not description or not samples:
        raise AudioConversionError("invalid_caf")


def _converter() -> tuple[str, str]:
    if sys.platform == "darwin" and os.access("/usr/bin/afconvert", os.X_OK):
        return "afconvert", "/usr/bin/afconvert"
    executable = shutil.which("ffmpeg")
    if executable and os.name == "posix":
        return "ffmpeg", str(Path(executable).resolve())
    raise AudioConversionError("missing_converter")


def _wav(data: bytes) -> None:
    try:
        with wave.open(io.BytesIO(data), "rb") as sound:
            frames = sound.getnframes()
            if (sound.getcomptype() != "NONE" or sound.getnchannels() != 1 or sound.getsampwidth() != 2
                    or sound.getframerate() != 16000 or frames <= 0):
                raise AudioConversionError("invalid_output")
            if frames > 16000 * MAX_DECODED_SECONDS:
                raise AudioConversionError("duration_limit")
            if len(sound.readframes(frames)) != frames * 2:
                raise AudioConversionError("invalid_output")
    except (wave.Error, EOFError):
        raise AudioConversionError("invalid_output") from None


async def _run(arguments: list[str], directory: str) -> None:
    # Apply file/CPU limits in a fresh interpreter, then exec the converter under
    # the SAME owned PID. Avoid preexec_fn inside a multithreaded API process.
    wrapper = ("import os,resource,sys;"
               f"resource.setrlimit(resource.RLIMIT_FSIZE,({MAX_AUDIO_BYTES},{MAX_AUDIO_BYTES}));"
               "resource.setrlimit(resource.RLIMIT_CPU,(15,15));"
               "os.execv(sys.argv[1],sys.argv[1:])")
    task = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", wrapper, *arguments, cwd=directory,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "LANG": "C", "TMPDIR": directory},
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=True))
    process = None
    try:
        try:
            process = await asyncio.shield(task)
        except asyncio.CancelledError:
            process = await task  # Obtain the owned PID even if cancellation races spawn.
            raise
        await process.wait()
        if process.returncode != 0:
            raise AudioConversionError("conversion_failed")
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()  # Only the child created above, never other agents' processes.
            except ProcessLookupError:
                pass
            await asyncio.shield(process.wait())


async def convert_caf(data: bytes) -> bytes:
    validate_caf(data)
    kind, executable = _converter()
    try:
        with tempfile.TemporaryDirectory(prefix="stitch-caf-") as directory:
            source, output = Path(directory) / "voice.caf", Path(directory) / "voice.wav"
            descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
            if kind == "afconvert":
                arguments = [executable, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(source), str(output)]
            elif kind == "ffmpeg":
                arguments = [executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-protocol_whitelist", "file", "-f", "caf", "-i", str(source), "-map", "0:a:0",
                    "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", str(output)]
            else:
                raise AudioConversionError("missing_converter")
            async with asyncio.timeout(CONVERSION_TIMEOUT):
                await _run(arguments, directory)
            if output.stat().st_size > MAX_AUDIO_BYTES:
                raise AudioConversionError("output_size")
            with output.open("rb") as stream:
                result = stream.read(MAX_AUDIO_BYTES + 1)
            if len(result) > MAX_AUDIO_BYTES:
                raise AudioConversionError("output_size")
            _wav(result)
            return result
    except TimeoutError:
        raise AudioConversionError("conversion_timeout") from None
    except OSError:
        raise AudioConversionError("conversion_failed") from None
