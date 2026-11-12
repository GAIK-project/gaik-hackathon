from __future__ import annotations

import asyncio
import threading

import pytest
from pydantic import BaseModel

from app.core import llm
from app.core.errors import AppError
from app.core.settings import get_settings


@pytest.fixture
def fake_azure(monkeypatch):
    monkeypatch.setenv("AZURE_API_KEY", "fake-azure-key")
    monkeypatch.setenv("AZURE_ENDPOINT", "https://fake-resource.invalid")
    monkeypatch.setenv("AZURE_API_VERSION", "2025-03-01-preview")
    monkeypatch.setenv("AZURE_DEPLOYMENT", "fake-chat")
    monkeypatch.setenv("AZURE_TRANSCRIPTION_MODEL", "fake-transcribe")
    monkeypatch.setenv("EMBEDDING_MODEL", "fake-embedding")
    monkeypatch.setenv("GAIK_DECIDE_URL", "https://decide.invalid/v1/")
    monkeypatch.setenv("GAIK_DECIDE_API_KEY", "fake-decide-key")
    get_settings.cache_clear()


# gaik config -------------------------------------------------------------------------------


def test_gaik_config_is_azure_with_the_long_timeout(fake_azure):
    config = llm.gaik_config()
    assert config["provider"] == "azure" and config["use_azure"] is True
    assert config["timeout"] == 120
    assert config["model"] == "fake-chat"
    assert config["transcription_model"] == "fake-transcribe"
    assert config["embedding_model"] == "fake-embedding"
    assert config["azure_endpoint"] == "https://fake-resource.invalid"


def test_gaik_config_overrides_win(fake_azure):
    config = llm.gaik_config(model="other", timeout=10, reasoning_effort="low")
    assert (config["model"], config["timeout"], config["reasoning_effort"]) == ("other", 10, "low")


def test_missing_azure_settings_are_a_503_without_details(fake_azure, monkeypatch):
    monkeypatch.setenv("AZURE_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(AppError) as raised:
        llm.gaik_config()
    assert raised.value.status == 503 and raised.value.code == "not_configured"
    assert "AZURE" not in raised.value.message


def test_component_helpers_use_the_settings(fake_azure, monkeypatch):
    classifier = llm.jev_classifier()
    assert type(classifier).__name__ == "JevClassifier"
    embedder = llm.embedder()
    assert embedder.model == "fake-embedding"
    assert llm.embedder(model="explicit").model == "explicit"

    monkeypatch.setenv("GAIK_DECIDE_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(AppError) as raised:
        llm.jev_classifier()
    assert raised.value.code == "not_configured"


# worker threads ----------------------------------------------------------------------------


async def test_run_blocking_runs_in_a_worker_thread_and_keeps_prints_out_of_stdout(capsys):
    def noisy(value: int, *, factor: int) -> tuple[int, str]:
        print("gaik says: schema generated")
        print("gaik says: tokens 123")
        return value * factor, threading.current_thread().name

    print("before")
    result, thread_name = await llm.run_blocking(noisy, 21, factor=2)
    print("after")

    assert result == 42
    assert thread_name != threading.current_thread().name
    captured = capsys.readouterr()
    assert captured.out == "before\nafter\n"
    assert "gaik says" not in captured.err


async def test_run_blocking_captured_returns_what_was_printed():
    def noisy() -> str:
        print("line one")
        print("line two")
        return "done"

    result, printed = await llm.run_blocking_captured(noisy)
    assert result == "done"
    assert printed == "line one\nline two\n"


async def test_parallel_calls_do_not_mix_their_output(capsys):
    barrier = threading.Barrier(4, timeout=5)

    def noisy(tag: str) -> str:
        barrier.wait()
        for number in range(50):
            print(f"{tag}-{number}")
        return tag

    results = await asyncio.gather(*(llm.run_blocking_captured(noisy, tag) for tag in "abcd"))

    for tag, (result, printed) in zip("abcd", results, strict=True):
        assert result == tag
        assert printed.splitlines() == [f"{tag}-{number}" for number in range(50)]
    assert capsys.readouterr().out == ""


async def test_errors_pass_through_and_capture_is_released(capsys):
    def failing() -> None:
        print("about to fail")
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        await llm.run_blocking(failing)

    # A worker thread that is reused afterwards prints normally again.
    seen: list[str] = []

    def quiet_thread_check() -> None:
        seen.append(type(llm._stdout_proxy()._target()).__name__)

    await llm.run_blocking_captured(lambda: None)
    thread = threading.Thread(target=quiet_thread_check)
    thread.start()
    thread.join()
    assert seen and seen[0] != "StringIO"
    assert "about to fail" not in capsys.readouterr().out


async def test_quiet_stdout_swallows_prints_and_leaves_an_outer_capture_alone(capsys):
    with llm.quiet_stdout():
        print("gaik import warning")
    print("visible")
    assert capsys.readouterr().out == "visible\n"

    def inner() -> None:
        print("one")
        with llm.quiet_stdout():
            print("two")
        print("three")

    _, printed = await llm.run_blocking_captured(inner)
    assert printed == "one\ntwo\nthree\n"


def test_stdout_proxy_passes_stream_attributes_through():
    proxy = llm._stdout_proxy()
    assert proxy.encoding
    assert proxy.writable() and not proxy.capturing()
    with pytest.raises(AttributeError):
        proxy.no_such_attribute_anywhere  # noqa: B018


# structured chat ---------------------------------------------------------------------------


class Answer(BaseModel):
    tier: str


class FakeCompletions:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests: list[dict] = []

    async def parse(self, **request):
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def fake_client_class(completions: FakeCompletions, created: list[dict]):
    class FakeClient:
        def __init__(self, **arguments):
            created.append(arguments)
            self.chat = type("Chat", (), {"completions": completions})()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    return FakeClient


def completion(parsed):
    message = type("Message", (), {"parsed": parsed, "refusal": None})()
    return type("Completion", (), {"choices": [type("Choice", (), {"message": message})()]})()


async def test_structured_chat_calls_the_chat_deployment_and_returns_the_object(
    fake_azure, monkeypatch
):
    import openai

    completions = FakeCompletions(completion(Answer(tier="high")))
    created: list[dict] = []
    monkeypatch.setattr(openai, "AsyncAzureOpenAI", fake_client_class(completions, created))

    result = await llm.structured_chat("You classify.", "A CV screening tool.", Answer)

    assert result == Answer(tier="high")
    assert created == [
        {
            "api_key": "fake-azure-key",
            "azure_endpoint": "https://fake-resource.invalid",
            "api_version": "2025-03-01-preview",
            "timeout": 120.0,
            "max_retries": 2,
        }
    ]
    assert completions.requests == [
        {
            "model": "fake-chat",
            "messages": [
                {"role": "system", "content": "You classify."},
                {"role": "user", "content": "A CV screening tool."},
            ],
            "response_format": Answer,
        }
    ]


async def test_structured_chat_passes_options_and_another_deployment(fake_azure, monkeypatch):
    import openai

    completions = FakeCompletions(completion(Answer(tier="minimal")))
    monkeypatch.setattr(openai, "AsyncAzureOpenAI", fake_client_class(completions, []))

    await llm.structured_chat(
        "s", "u", Answer, model="other-deployment", temperature=0, max_tokens=200, seed=None
    )

    request = completions.requests[0]
    assert request["model"] == "other-deployment"
    assert request["temperature"] == 0 and request["max_tokens"] == 200
    assert "seed" not in request


async def test_provider_error_text_never_reaches_the_caller(fake_azure, monkeypatch, caplog):
    import openai

    failure = openai.OpenAIError("Incorrect API key provided: fake-azure-key at fake-resource")
    monkeypatch.setattr(openai, "AsyncAzureOpenAI", fake_client_class(FakeCompletions(failure), []))

    with caplog.at_level("ERROR"), pytest.raises(AppError) as raised:
        await llm.structured_chat("s", "u", Answer)

    assert "fake-azure-key" not in caplog.text  # the log keeps the message, minus secrets
    assert "<AZURE_API_KEY> at fake-resource" in caplog.text
    assert raised.value.status == 502 and raised.value.code == "model_failed"
    assert "fake-azure-key" not in raised.value.message
    assert "fake-resource" not in raised.value.message
    assert raised.value.__cause__ is failure


async def test_missing_parsed_object_is_a_model_failure(fake_azure, monkeypatch):
    import openai

    monkeypatch.setattr(
        openai, "AsyncAzureOpenAI", fake_client_class(FakeCompletions(completion(None)), [])
    )
    with pytest.raises(AppError) as raised:
        await llm.structured_chat("s", "u", Answer)
    assert raised.value.code == "model_failed"


@pytest.mark.live
async def test_live_structured_chat_and_classifier():
    class Reply(BaseModel):
        word: str

    reply = await llm.structured_chat("Answer with one word.", "Say ok.", Reply)
    assert reply.word

    result = await llm.run_blocking(
        llm.jev_classifier().classify,
        "Nosturin nostovaijeri on pahasti kulunut ja se on vaihdettava heti.",
        {"kiireellinen": "Vaatii toimia heti", "normaali": "Tavallinen aikataulu"},
    )
    assert result["class"] in ("kiireellinen", "normaali")
    assert 0.0 <= result["confidence"] <= 1.0
