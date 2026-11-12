"""The Realtime session payload and the prompt (no network)."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.settings import Settings
from app.voice import session
from tests.voice.fakes import SAFETY


def _settings(**values: str) -> Settings:
    base = {
        "azure_realtime_endpoint": "https://example.services.ai.azure.com/",
        "azure_realtime_api_key": "k",
        "azure_realtime_deployment": "gpt-realtime-2",
    }
    return Settings.model_construct(**{**base, **values})


def test_target_is_the_ga_endpoint_without_api_version():
    url, headers = session.realtime_target(_settings())
    assert url == "wss://example.services.ai.azure.com/openai/v1/realtime?model=gpt-realtime-2"
    assert list(headers) == ["api-key"]


def test_target_names_missing_settings_not_values():
    with pytest.raises(RuntimeError, match="AZURE_REALTIME_DEPLOYMENT"):
        session.realtime_target(_settings(azure_realtime_deployment=""))


@pytest.mark.parametrize("language", ["en", "fi", "sv"])
def test_session_update_has_the_ga_shape(language):
    payload = session.session_update(SAFETY, language)
    body = payload["session"]
    assert payload["type"] == "session.update"
    assert body["type"] == "realtime"
    assert body["output_modalities"] == ["audio"]
    audio_in, audio_out = body["audio"]["input"], body["audio"]["output"]
    assert audio_in["format"] == {"type": "audio/pcm", "rate": 24000}
    assert audio_out == {"format": {"type": "audio/pcm", "rate": 24000}, "voice": "cedar"}
    assert audio_in["transcription"] == {"model": "gpt-4o-transcribe", "language": language}
    assert audio_in["turn_detection"]["type"] == "server_vad"
    assert audio_in["turn_detection"]["silence_duration_ms"] == 700
    assert [tool["name"] for tool in body["tools"]] == ["set_fields", "save_report"]
    assert "model" not in body  # the deployment is in the URL only


def test_variants_fall_back_to_whisper():
    assert session.session_update(SAFETY, "fi", 1)["session"]["audio"]["input"][
        "transcription"
    ] == {
        "model": "whisper-1",
        "language": "fi",
    }
    last = session.session_update(SAFETY, "fi", 2)["session"]
    assert "reasoning" not in last and "noise_reduction" not in last["audio"]["input"]


def test_unknown_language_is_english():
    assert session.language_of("de") == "en"
    assert session.language_of(None) == "en"
    assert session.language_of("sv") == "sv"


def test_tools_are_built_from_the_report_type():
    set_fields, save_report = session.tools(SAFETY)
    properties = set_fields["parameters"]["properties"]
    assert list(properties) == ["description", "location", "severity", "action"]
    assert properties["severity"]["enum"] == ["low", "medium", "high"]
    assert properties["location"]["description"].startswith("Location.")
    assert "required" not in set_fields["parameters"]
    assert save_report["parameters"]["required"] == ["summary"]
    assert "confirmed" in save_report["description"]


def test_instructions_lock_the_language_first_and_list_the_fields():
    text = session.instructions(SAFETY, "fi", today=date(2026, 11, 12))
    assert text.startswith("# Language")
    assert "Speak only Finnish" in text.split("# Role")[0]
    assert "AI assistant of GAIK" in text
    assert "ONE missing required field" in text
    assert "Never invent" in text
    assert "Thursday 2026-11-12" in text
    for field in SAFETY.fields:
        assert f"- {field.name} ({field.label}" in text
    assert "Required: What happened, Location, Severity." in text


@pytest.mark.parametrize(
    ("language", "words"),
    [("en", "AI assistant of GAIK"), ("fi", "tekoälyavustaja"), ("sv", "AI-assistent")],
)
def test_greeting_is_scripted_and_says_it_is_an_ai(language, words):
    request = session.greeting_request(SAFETY, language)
    assert request["type"] == "response.create"
    assert request["response"]["tool_choice"] == "none"
    assert session.greeting_text(SAFETY, language) in request["response"]["instructions"]
    assert words in session.greeting_text(SAFETY, language)
    assert "GAIK" in session.greeting_text(SAFETY, language)
