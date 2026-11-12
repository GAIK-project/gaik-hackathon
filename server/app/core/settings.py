"""Settings (docs/ARCHITECTURE.md section 5).

Importing this module reads `<repo>/.env` into `os.environ` without overriding variables that
are already set. That has to happen before gaik is imported: gaik reads the environment at
import and looks for a `.env` from its own install location, not from this repository.

Values are never logged: secret fields are left out of `repr`, and nothing here prints.

`Settings.startup_problems()` lists what makes the server refuse to start (section 15.2); the
list names settings, never their values.
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


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
DEV_ORIGIN = "http://localhost:3000"
MIN_SESSION_SECRET_LENGTH = 32
_DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(url: str) -> str:
    """`scheme://host[:port]` of an http(s) URL in lower case, without a default port.

    An empty string for anything else, `null` included, so it never equals a real origin.
    """
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return ""
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in _DEFAULT_PORTS or not host:
        return ""
    if ":" in host:
        host = f"[{host}]"
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


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
    hack_bridge_key: str = _secret()
    hack_mcp_key: str = _secret()
    hack_auth_disabled: bool = False
    hack_dev: bool = False
    hack_models_off: bool = False
    data_dir: str = "./.data"
    public_base_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 25
    mail_from: str = ""
    mail_allowed_to: str = ""
    mail_default_to: str = ""
    mail_max_per_hour: int = 10

    # Seconds for one model call; gaik's own default for structured calls is 30.
    llm_timeout: ClassVar[int] = 120

    @field_validator("hack_auth_disabled", "hack_dev", "hack_models_off", mode="before")
    @classmethod
    def _empty_is_false(cls, value: object) -> object:
        return False if isinstance(value, str) and not value.strip() else value

    @field_validator("smtp_port", mode="before")
    @classmethod
    def _empty_port_is_default(cls, value: object) -> object:
        return 25 if isinstance(value, str) and not value.strip() else value

    @field_validator("mail_max_per_hour", mode="before")
    @classmethod
    def _empty_mail_limit_is_default(cls, value: object) -> object:
        return 10 if isinstance(value, str) and not value.strip() else value

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
    def mail_default_recipient(self) -> str | None:
        """Where `approve` without `to` sends: `MAIL_DEFAULT_TO`, or the first allowlisted
        address when it is empty. `None` when there is no allowlisted recipient (and when
        `MAIL_DEFAULT_TO` is outside the allowlist, which also stops the server at startup)."""
        recipients = self.mail_recipients
        wanted = self.mail_default_to.lower()
        if wanted:
            return wanted if wanted in recipients else None
        return recipients[0] if recipients else None

    @property
    def public_host(self) -> str:
        """Host name of `PUBLIC_BASE_URL` without the port, or an empty string."""
        return urlsplit(self.public_base_url).hostname or ""

    @property
    def public_origin(self) -> str:
        """`PUBLIC_BASE_URL` as an origin (`https://host[:port]`), or an empty string."""
        return origin_of(self.public_base_url)

    @property
    def is_local(self) -> bool:
        """True when `PUBLIC_BASE_URL` is a `http://localhost` or `http://127.0.0.1` origin:
        local development. Everything else is treated as a public deployment."""
        origin = self.public_origin
        return origin.startswith("http://") and self.public_host.lower() in LOCAL_HOSTS

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        """Origins a cookie-authenticated write or WebSocket may come from."""
        origins = [self.public_origin] if self.public_origin else []
        if self.hack_dev and DEV_ORIGIN not in origins:
            origins.append(DEV_ORIGIN)
        return tuple(origins)

    def startup_problems(self) -> list[str]:
        """Why the server must not start with these settings; empty when it may.

        Each entry names settings only, never a value.
        """
        problems: list[str] = []
        if not self.public_origin:
            problems.append("PUBLIC_BASE_URL must be an http(s) origin")
        elif not self.is_local:
            for name in ("HACK_AUTH_DISABLED", "HACK_DEV"):
                if getattr(self, name.lower()):
                    problems.append(
                        f"{name}=1 is allowed only when PUBLIC_BASE_URL is a "
                        "http://localhost or http://127.0.0.1 origin"
                    )
        if len(self.hack_session_secret) < MIN_SESSION_SECRET_LENGTH:
            problems.append(
                f"HACK_SESSION_SECRET must be at least {MIN_SESSION_SECRET_LENGTH} characters"
            )
        for name in self.missing(
            "hack_basic_user", "hack_basic_password", "hack_bridge_key", "hack_mcp_key"
        ):
            problems.append(f"{name} is empty")
        if self.hack_bridge_key and self.hack_bridge_key == self.hack_mcp_key:
            problems.append("HACK_BRIDGE_KEY and HACK_MCP_KEY must differ")
        if self.mail_default_to and self.mail_default_to.lower() not in self.mail_recipients:
            problems.append("MAIL_DEFAULT_TO must be one of MAIL_ALLOWED_TO")
        if self.mail_max_per_hour < 0:
            problems.append("MAIL_MAX_PER_HOUR must not be negative")
        return problems

    def missing(self, *names: str) -> list[str]:
        """Variable names (upper case) among `names` that have no value. Names only."""
        return [name.upper() for name in names if not getattr(self, name.lower())]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings. Tests call `get_settings.cache_clear()` after changing env."""
    return Settings()
