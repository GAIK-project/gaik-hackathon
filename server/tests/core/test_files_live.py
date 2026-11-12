"""Live smoke test of the upload path: real, small calls to Azure speech and transcription.

A synthetic sentence is spoken by the TTS deployment, the audio it returns goes through the
same steps as an uploaded voice note (accepted by its first bytes, stored under a
server-generated name, converted by ffmpeg with the detected format), and the WAV that comes
out is transcribed by gaik's Transcriber. Run with
`uv run pytest tests/core/test_files_live.py -m live -s` to see the numbers.
"""

from __future__ import annotations

import tempfile
import wave

import httpx
import pytest

from app.core import files, llm
from app.core.settings import get_settings

pytestmark = pytest.mark.live

SENTENCE = "Tämä on lyhyt testi. Nosturin vaijeri on kulunut ja se pitää vaihtaa."


def _transcribe(wav_path: str) -> str:
    from gaik.software_components.transcriber import Transcriber

    with tempfile.TemporaryDirectory() as workdir:
        result = Transcriber(llm.gaik_config(), output_dir=workdir).transcribe(wav_path)
        return (result.enhanced_transcript or result.raw_transcript or "").strip()


async def test_spoken_audio_is_accepted_converted_and_transcribed():
    settings = get_settings()
    missing = settings.missing(
        "azure_api_key", "tts_endpoint", "azure_tts_model", "azure_transcription_model"
    )
    if missing:
        pytest.skip(f"not configured: {', '.join(missing)}")
    if files.ffmpeg_path() is None:
        pytest.skip("ffmpeg is not installed")

    async with httpx.AsyncClient(timeout=60) as http:
        spoken = await http.post(
            settings.tts_endpoint,
            headers={"api-key": settings.azure_api_key},
            json={"model": settings.azure_tts_model, "input": SENTENCE, "voice": "alloy"},
        )
    assert spoken.status_code == 200, f"speech call answered {spoken.status_code}"
    audio = spoken.content

    # The provider's own bytes, judged like any upload.
    stored = await files.save_upload("reports/rep_live", audio, "audio")
    assert stored.format in files.FORMATS and files.FORMATS[stored.format].kind == "audio"
    assert stored.name.startswith("f_") and stored.size == len(audio)

    converted = await files.to_wav(stored.path, stored.format)
    with wave.open(str(converted.path), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16_000, 1, 2)
    assert 2.0 < converted.duration_s < 20.0

    text = await llm.run_blocking(_transcribe, str(converted.path))
    print(
        f"\n[live] speech {len(audio)} bytes as {stored.format} -> "
        f"{converted.duration_s:.1f} s of 16 kHz WAV -> {len(text)} characters: {text[:120]}"
    )
    lowered = text.lower()
    assert "[transcription failed" not in lowered
    assert "testi" in lowered or "vaijeri" in lowered or "nosturi" in lowered
