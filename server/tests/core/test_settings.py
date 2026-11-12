from __future__ import annotations

import os
import subprocess
import sys

from app.core.settings import REPO_ROOT, SERVER_ROOT, Settings, get_settings, load_env_file


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
    monkeypatch.setenv("SMTP_PORT", "")
    settings = Settings()
    assert settings.hack_auth_disabled is False
    assert settings.hack_dev is True
    assert settings.smtp_port == 25
    assert settings.mail_enabled is False


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
        "HACK_API_KEY",
        "HACK_AUTH_DISABLED",
        "HACK_DEV",
        "DATA_DIR",
        "PUBLIC_BASE_URL",
        "SMTP_HOST",
        "SMTP_PORT",
        "MAIL_FROM",
        "MAIL_ALLOWED_TO",
    }
    assert {name.upper() for name in Settings.model_fields} == names


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
    for value in ("azure-key-value", "decide-key-value", "test-password", "test-api-key"):
        assert value not in text
    assert "test-session-secret" not in text


def test_relative_data_dir_is_taken_from_the_repository_root(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "./.data")
    assert Settings().data_path == (REPO_ROOT / ".data").resolve()


def test_mail_recipients_and_missing(monkeypatch):
    monkeypatch.setenv("MAIL_ALLOWED_TO", " A@example.com, b@example.com ,,a@example.com")
    monkeypatch.setenv("GAIK_DECIDE_URL", "")
    settings = Settings()
    assert settings.mail_recipients == ["a@example.com", "b@example.com"]
    assert settings.missing("gaik_decide_url", "hack_api_key") == ["GAIK_DECIDE_URL"]
