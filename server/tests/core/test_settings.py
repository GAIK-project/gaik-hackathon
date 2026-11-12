from __future__ import annotations

import os
import subprocess
import sys

import pytest

from app.core.settings import (
    REPO_ROOT,
    SERVER_ROOT,
    Settings,
    get_settings,
    load_env_file,
    origin_of,
)


def test_env_file_fills_only_names_that_are_not_set(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# a comment\nHACK_TEST_FROM_FILE=file-value\nHACK_TEST_ALREADY_SET="file-value"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("HACK_TEST_FROM_FILE", raising=False)
    monkeypatch.setenv("HACK_TEST_ALREADY_SET", "real-value")

    loaded = load_env_file(env_file)

    assert loaded == ["HACK_TEST_FROM_FILE"]
    assert os.environ["HACK_TEST_FROM_FILE"] == "file-value"
    assert os.environ["HACK_TEST_ALREADY_SET"] == "real-value"
    monkeypatch.delenv("HACK_TEST_FROM_FILE")


def test_missing_env_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path / "nothing.env") == []


def _run(code: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=SERVER_ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_importing_the_app_package_loads_env_and_does_not_import_gaik():
    code = (
        "import sys, app\n"
        "from app.core import settings\n"
        "assert isinstance(settings.ENV_NAMES_FROM_FILE, list)\n"
        "print(any(name == 'gaik' or name.startswith('gaik.') for name in sys.modules))\n"
    )
    result = _run(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_real_environment_wins_over_the_env_file_before_gaik_reads_it():
    # Whatever <repo>/.env says about these names, the process environment stays in force,
    # and gaik (imported after the app package) builds its config from it.
    code = (
        "import contextlib, io, os\n"
        "import app\n"
        "with contextlib.redirect_stdout(io.StringIO()):\n"
        "    from gaik.software_components.llm import get_llm_config\n"
        "config = get_llm_config('azure')\n"
        "print(os.environ['AZURE_DEPLOYMENT'], config['model'], config['azure_endpoint'])\n"
    )
    result = _run(
        code,
        AZURE_DEPLOYMENT="deployment-from-real-env",
        AZURE_ENDPOINT="https://real-env.invalid",
        AZURE_API_KEY="key-from-real-env",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        "deployment-from-real-env",
        "deployment-from-real-env",
        "https://real-env.invalid",
    ]


def test_get_settings_is_cached_until_cleared(monkeypatch):
    first = get_settings()
    assert get_settings() is first
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://other.example:8443/")
    assert get_settings().public_base_url == first.public_base_url
    get_settings.cache_clear()
    assert get_settings().public_base_url == "https://other.example:8443/"
    assert get_settings().public_host == "other.example"


def test_flags_and_port_tolerate_empty_values(monkeypatch):
    monkeypatch.setenv("HACK_AUTH_DISABLED", "")
    monkeypatch.setenv("HACK_DEV", "1")
    monkeypatch.setenv("HACK_MODELS_OFF", "")
    monkeypatch.setenv("SMTP_PORT", "")
    monkeypatch.setenv("MAIL_MAX_PER_HOUR", "")
    settings = Settings()
    assert settings.hack_auth_disabled is False
    assert settings.hack_dev is True
    assert settings.hack_models_off is False
    assert settings.smtp_port == 25
    assert settings.mail_max_per_hour == 10
    assert settings.mail_enabled is False

    monkeypatch.setenv("HACK_MODELS_OFF", "1")
    monkeypatch.setenv("MAIL_MAX_PER_HOUR", "3")
    settings = Settings()
    assert settings.hack_models_off is True
    assert settings.mail_max_per_hour == 3


def test_every_name_of_the_contract_is_a_setting():
    names = {
        "AZURE_API_KEY",
        "AZURE_ENDPOINT",
        "AZURE_API_VERSION",
        "AZURE_DEPLOYMENT",
        "AZURE_TRANSCRIPTION_MODEL",
        "EMBEDDING_MODEL",
        "AZURE_REALTIME_ENDPOINT",
        "AZURE_REALTIME_API_KEY",
        "AZURE_REALTIME_DEPLOYMENT",
        "AZURE_TTS_MODEL",
        "TTS_ENDPOINT",
        "GAIK_DECIDE_URL",
        "GAIK_DECIDE_API_KEY",
        "HACK_BASIC_USER",
        "HACK_BASIC_PASSWORD",
        "HACK_SESSION_SECRET",
        "HACK_BRIDGE_KEY",
        "HACK_MCP_KEY",
        "HACK_AUTH_DISABLED",
        "HACK_DEV",
        "HACK_MODELS_OFF",
        "DATA_DIR",
        "PUBLIC_BASE_URL",
        "SMTP_HOST",
        "SMTP_PORT",
        "MAIL_FROM",
        "MAIL_ALLOWED_TO",
        "MAIL_DEFAULT_TO",
        "MAIL_MAX_PER_HOUR",
    }
    assert {name.upper() for name in Settings.model_fields} == names
    assert "HACK_API_KEY" not in names  # replaced by the two scoped keys


def test_the_old_shared_key_is_gone_from_the_code_and_the_docs():
    for path in (
        REPO_ROOT / ".env.example",
        REPO_ROOT / "README.md",
        *(SERVER_ROOT / "app").rglob("*.py"),
        *(SERVER_ROOT / "scripts").rglob("*.py"),
    ):
        text = path.read_text(encoding="utf-8")
        assert "HACK_API_KEY" not in text and "hack_api_key" not in text, path.name


def test_env_example_lists_the_same_names_without_values():
    lines = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assignments = [line for line in lines if line and not line.startswith("#")]
    names = {line.split("=", 1)[0] for line in assignments}
    assert names == {name.upper() for name in Settings.model_fields}
    for line in assignments:
        name, _, value = line.partition("=")
        assert value == "", f"{name} must have no value in .env.example"


def test_secrets_stay_out_of_repr(monkeypatch):
    monkeypatch.setenv("AZURE_API_KEY", "azure-key-value")
    monkeypatch.setenv("GAIK_DECIDE_API_KEY", "decide-key-value")
    text = repr(Settings())
    for value in (
        "azure-key-value",
        "decide-key-value",
        "test-password",
        "test-bridge-key",
        "test-mcp-key",
        "test-session-secret",
    ):
        assert value not in text


def test_relative_data_dir_is_taken_from_the_repository_root(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "./.data")
    assert Settings().data_path == (REPO_ROOT / ".data").resolve()


def test_mail_recipients_and_missing(monkeypatch):
    monkeypatch.setenv("MAIL_ALLOWED_TO", " A@example.com, b@example.com ,,a@example.com")
    monkeypatch.setenv("GAIK_DECIDE_URL", "")
    settings = Settings()
    assert settings.mail_recipients == ["a@example.com", "b@example.com"]
    assert settings.missing("gaik_decide_url", "hack_bridge_key") == ["GAIK_DECIDE_URL"]


def test_default_recipient_is_allowlisted_or_nothing(monkeypatch):
    monkeypatch.setenv("MAIL_ALLOWED_TO", "a@example.com, b@example.com")
    monkeypatch.setenv("MAIL_DEFAULT_TO", "")
    assert Settings().mail_default_recipient == "a@example.com"  # the first entry

    monkeypatch.setenv("MAIL_DEFAULT_TO", " B@Example.com ")
    assert Settings().mail_default_recipient == "b@example.com"

    monkeypatch.setenv("MAIL_DEFAULT_TO", "muu@example.com")
    settings = Settings()
    assert settings.mail_default_recipient is None  # never an address outside the allowlist
    assert "MAIL_DEFAULT_TO must be one of MAIL_ALLOWED_TO" in settings.startup_problems()

    monkeypatch.setenv("MAIL_ALLOWED_TO", "")
    monkeypatch.setenv("MAIL_DEFAULT_TO", "")
    assert Settings().mail_default_recipient is None


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("https://gaik-hackathon.2.rahtiapp.fi", "https://gaik-hackathon.2.rahtiapp.fi"),
        (
            "https://Gaik-Hackathon.2.rahtiapp.fi:443/polku/?q=1",
            "https://gaik-hackathon.2.rahtiapp.fi",
        ),
        ("http://localhost:8787/", "http://localhost:8787"),
        ("http://localhost:80", "http://localhost"),
        ("https://example.com:8443", "https://example.com:8443"),
        ("http://[::1]:8787", "http://[::1]:8787"),
        ("", ""),
        ("null", ""),
        ("localhost:8787", ""),
        ("ftp://example.com", ""),
        ("https://", ""),
        ("https://example.com:notaport", ""),
    ],
)
def test_origin_of(url, origin):
    assert origin_of(url) == origin


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:8787", True),
        ("http://127.0.0.1:8787", True),
        ("http://localhost", True),
        ("http://LOCALHOST:3000/", True),
        ("https://localhost:8787", False),
        ("http://localhost.example.com", False),
        ("http://127.0.0.1.example.com", False),
        ("http://192.168.1.10:8787", False),
        ("http://0.0.0.0:8787", False),
        ("https://gaik-hackathon.2.rahtiapp.fi", False),
        ("", False),
    ],
)
def test_is_local_means_a_plain_http_loopback_origin(url, local, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", url)
    assert Settings().is_local is local


def test_allowed_origins(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://gaik-hackathon.example/")
    assert Settings().allowed_origins == ("https://gaik-hackathon.example",)

    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8787")
    monkeypatch.setenv("HACK_DEV", "1")
    assert Settings().allowed_origins == ("http://localhost:8787", "http://localhost:3000")

    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:3000")
    assert Settings().allowed_origins == ("http://localhost:3000",)


def test_startup_problems_name_settings_only(monkeypatch):
    assert Settings().startup_problems() == []
    monkeypatch.setenv("HACK_AUTH_DISABLED", "1")
    monkeypatch.setenv("HACK_DEV", "1")
    monkeypatch.setenv("HACK_SESSION_SECRET", "too-short-secret")
    monkeypatch.setenv("HACK_BRIDGE_KEY", "")
    monkeypatch.setenv("MAIL_MAX_PER_HOUR", "-1")
    problems = Settings().startup_problems()
    text = " | ".join(problems)
    for name in (
        "HACK_AUTH_DISABLED",
        "HACK_DEV",
        "HACK_SESSION_SECRET",
        "HACK_BRIDGE_KEY",
        "MAIL_MAX_PER_HOUR",
    ):
        assert name in text
    assert "too-short-secret" not in text and "test-" not in text
