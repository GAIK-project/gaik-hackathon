"""Settings (docs/ARCHITECTURE.md section 5).

Importing this module reads `<repo>/.env` into `os.environ` without overriding variables that
are already set. That has to happen before gaik is imported: gaik reads the environment at
import and looks for a `.env` from its own install location, not from this repository.

Values are never logged: secret fields are left out of `repr`, and nothing here prints.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SERVER_ROOT.parent
ENV_FILE = REPO_ROOT / ".env"
WEB_OUT_DIR = REPO_ROOT / "web" / "out"


def load_env_file(path: Path = ENV_FILE) -> list[str]:
    """Copy names from an env file into `os.environ`; names already set are left alone.

    Returns the names that were set from the file (names only, never values).
    """
    if not path.is_file():
        return []
    loaded: list[str] = []
    for name, value in dotenv_values(path).items():
        if value is None or name in os.environ:
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded


ENV_NAMES_FROM_FILE: list[str] = load_env_file()


def _secret() -> str:
    return Field(default="", repr=False)


class Settings(BaseSettings):
    """Every name of section 5. The attribute is the lower-case form of the variable name."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # Azure OpenAI, read by gaik under the same names
    azure_api_key: str = _secret()
    azure_endpoint: str = ""
    azure_api_version: str = ""
    azure_deployment: str = ""
    azure_transcription_model: str = ""
    embedding_model: str = ""
    # Azure Realtime (a different host than chat)
    azure_realtime_endpoint: str = ""
    azure_realtime_api_key: str = _secret()
    azure_realtime_deployment: str = ""
    # Speech out
    azure_tts_model: str = ""
    tts_endpoint: str = ""
    # gaik-decide (Jev typed decisions)
    gaik_decide_url: str = ""
    gaik_decide_api_key: str = _secret()
    # This app
    hack_basic_user: str = ""
    hack_basic_password: str = _secret()
    hack_session_secret: str = _secret()
    hack_api_key: str = _secret()
    hack_auth_disabled: bool = False
    hack_dev: bool = False
    data_dir: str = "./.data"
    public_base_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 25
    mail_from: str = ""
    mail_allowed_to: str = ""

    # Seconds for one model call; gaik's own default for structured calls is 30.
    llm_timeout: ClassVar[int] = 120

    @field_validator("hack_auth_disabled", "hack_dev", mode="before")
    @classmethod
    def _empty_is_false(cls, value: object) -> object:
        return False if isinstance(value, str) and not value.strip() else value

    @field_validator("smtp_port", mode="before")
    @classmethod
    def _empty_port_is_default(cls, value: object) -> object:
        return 25 if isinstance(value, str) and not value.strip() else value

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @property
    def data_path(self) -> Path:
        """`DATA_DIR` as an absolute path; a relative value is taken from the repository root."""
        path = Path(self.data_dir).expanduser()
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    @property
    def mail_enabled(self) -> bool:
        return bool(self.smtp_host)

    @property
    def mail_recipients(self) -> list[str]:
        """The allowlist of `MAIL_ALLOWED_TO`, lower-cased, in the order given."""
        seen: list[str] = []
        for part in self.mail_allowed_to.split(","):
            address = part.strip().lower()
            if address and address not in seen:
                seen.append(address)
        return seen

    @property
    def public_host(self) -> str:
        """Host name of `PUBLIC_BASE_URL` without the port, or an empty string."""
        return urlsplit(self.public_base_url).hostname or ""

    def missing(self, *names: str) -> list[str]:
        """Variable names (upper case) among `names` that have no value. Names only."""
        return [name.upper() for name in names if not getattr(self, name.lower())]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings. Tests call `get_settings.cache_clear()` after changing env."""
    return Settings()
