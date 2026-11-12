"""Model access: the gaik config, worker threads with quiet stdout, Jev, embeddings, chat.

gaik is imported inside the functions, never at module import: importing it is slow, it prints
to stdout, and it must not happen before `app.core.settings` has loaded the environment.

    config = gaik_config()                                   # Azure, timeout 120 s
    rows = await run_blocking(DataExtractor(config).extract, model, req, text, [transcript])
    result = await run_blocking(jev_classifier().classify, text, {"a": "...", "b": "..."})
    spec = await structured_chat(system, user, StudioSpecDraft)

Provider errors are raised as `AppError` with a Finnish sentence; the provider's own text is
logged here and never returned.
"""

from __future__ import annotations

import contextlib
import functools
import io
import logging
import sys
import threading
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, TextIO

import anyio.to_thread
from pydantic import BaseModel

from app.core.errors import AppError
from app.core.settings import Settings, get_settings

if TYPE_CHECKING:
    from gaik.software_components.jev_classifier import JevClassifier
    from gaik.software_components.RAG.embedder import Embedder
    from openai import AzureOpenAI

logger = logging.getLogger(__name__)


_CAPTURE_LOG_CHARS = 2000

# gaik config ---------------------------------------------------------------------------------


def gaik_config(**overrides: Any) -> dict[str, Any]:
    """`get_llm_config("azure", ...)` with this app's settings and a 120 s timeout.

    Overrides win: `gaik_config(model="other-deployment", reasoning_effort="low")`.
    """
    with quiet_stdout():
        from gaik.software_components.llm import get_llm_config

    settings = get_settings()
    values: dict[str, Any] = {"timeout": settings.llm_timeout}
    for key, value in (
        ("api_key", settings.azure_api_key),
        ("azure_endpoint", settings.azure_endpoint),
        ("api_version", settings.azure_api_version),
        ("model", settings.azure_deployment),
        ("transcription_model", settings.azure_transcription_model),
        ("embedding_model", settings.embedding_model),
    ):
        if value:
            values[key] = value
    values.update(overrides)
    try:
        return get_llm_config("azure", **values)
    except ValueError as error:
        # gaik names the missing variable, not its value.
        logger.error("gaik config is incomplete: %s", error)
        raise _not_configured() from error


def _not_configured() -> AppError:
    return AppError("not_configured", "Palvelua ei ole vielä otettu käyttöön.", 503)


# worker threads with captured stdout ---------------------------------------------------------


class _ThreadStdout(io.TextIOBase):
    """Stands in for `sys.stdout`: a thread that asked for capture writes into its own buffer,
    every other thread writes to the real stream."""

    def __init__(self, fallback: TextIO) -> None:
        self.fallback = fallback
        self._local = threading.local()

    def start(self) -> io.StringIO:
        buffer = io.StringIO()
        self._local.buffer = buffer
        return buffer

    def stop(self) -> None:
        self._local.buffer = None

    def _target(self) -> TextIO:
        return getattr(self._local, "buffer", None) or self.fallback

    def write(self, text: str) -> int:
        return self._target().write(text)

    def flush(self) -> None:
        self._target().flush()

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return getattr(self.fallback, "encoding", None) or "utf-8"

    def fileno(self) -> int:
        return self.fallback.fileno()

    def capturing(self) -> bool:
        return getattr(self._local, "buffer", None) is not None

    def __getattr__(self, name: str) -> Any:
        # `buffer`, `reconfigure`, `name` and the like belong to the real stream.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.fallback, name)


_stdout_lock = threading.Lock()


def _stdout_proxy() -> _ThreadStdout:
    """Install the proxy (once, or again when something replaced `sys.stdout`)."""
    with _stdout_lock:
        current = sys.stdout
        if isinstance(current, _ThreadStdout):
            return current
        proxy = _ThreadStdout(current)
        sys.stdout = proxy
        return proxy


@contextlib.contextmanager
def quiet_stdout() -> Iterator[None]:
    """Swallow what the current thread prints inside the block, for example gaik's warnings
    when it is first imported. Inside `run_blocking` the output stays with that capture."""
    proxy = _stdout_proxy()
    if proxy.capturing():
        yield
        return
    proxy.start()
    try:
        yield
    finally:
        proxy.stop()


def _call_captured[T](fn: Callable[..., T], args: tuple[Any, ...], kwargs: dict[str, Any]):
    proxy = _stdout_proxy()
    buffer = proxy.start()
    try:
        return fn(*args, **kwargs), buffer
    finally:
        proxy.stop()


def _name(fn: Callable[..., Any]) -> str:
    target = fn.func if isinstance(fn, functools.partial) else fn
    return getattr(target, "__qualname__", type(target).__name__)


async def run_blocking_captured[T](
    fn: Callable[..., T], *args: Any, **kwargs: Any
) -> tuple[T, str]:
    """Run a synchronous call in a worker thread; return its result and what it printed.

    The printed lines of gaik (schema, field names, token counts) can feed a "what is
    happening" view. Threads the call starts itself are not captured.
    """
    result, buffer = await anyio.to_thread.run_sync(_call_captured, fn, args, kwargs)
    return result, buffer.getvalue()


async def run_blocking[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a synchronous gaik or SDK call in a worker thread, with its prints kept out of
    stdout. What it printed is logged at DEBUG, shortened."""
    result, printed = await run_blocking_captured(fn, *args, **kwargs)
    if printed and logger.isEnabledFor(logging.DEBUG):
        logger.debug("%s printed: %s", _name(fn), printed[:_CAPTURE_LOG_CHARS].rstrip())
    return result


# gaik components -----------------------------------------------------------------------------


def jev_classifier(**overrides: Any) -> JevClassifier:
    """A `JevClassifier` that talks to gaik-decide. Text sent to it leaves the EU: synthetic
    text only."""
    with quiet_stdout():
        from gaik.software_components.jev_classifier import JevClassifier

    settings = get_settings()
    if settings.missing("gaik_decide_url", "gaik_decide_api_key"):
        logger.error(
            "gaik-decide is not configured: %s",
            ", ".join(settings.missing("gaik_decide_url", "gaik_decide_api_key")),
        )
        raise _not_configured()
    base_url = settings.gaik_decide_url.rstrip("/").removesuffix("/v1")
    return JevClassifier(api_key=settings.gaik_decide_api_key, base_url=base_url, **overrides)


def embedder(*, model: str | None = None, batch_size: int = 100) -> Embedder:
    """A gaik `Embedder` on the `EMBEDDING_MODEL` deployment."""
    with quiet_stdout():
        from gaik.software_components.RAG.embedder import Embedder

    settings = get_settings()
    return Embedder(
        gaik_config(), model=model or settings.embedding_model or None, batch_size=batch_size
    )


# one structured chat call --------------------------------------------------------------------


def _azure_arguments(settings: Settings) -> dict[str, Any]:
    missing = settings.missing("azure_api_key", "azure_endpoint", "azure_deployment")
    if missing:
        logger.error("Azure chat is not configured: %s", ", ".join(missing))
        raise _not_configured()
    config = gaik_config()  # the same endpoint, key and API version every gaik component uses
    return {
        "api_key": config["api_key"],
        "azure_endpoint": config["azure_endpoint"],
        "api_version": config["api_version"],
        "timeout": float(settings.llm_timeout),
        "max_retries": 2,
    }


@functools.lru_cache(maxsize=4)
def _sync_client(key: tuple[tuple[str, Any], ...]) -> AzureOpenAI:
    from openai import AzureOpenAI

    return AzureOpenAI(**dict(key))


def _chat_request[ModelT: BaseModel](
    system: str, user: str, schema_model: type[ModelT], model: str | None, options: dict[str, Any]
) -> dict[str, Any]:
    with quiet_stdout():
        from gaik.software_components.llm.parameters import normalize_chat_kwargs

    settings = get_settings()
    deployment = model or settings.azure_deployment
    # gaik decides from the deployment name which sampling parameters the model accepts.
    given = {key: value for key, value in options.items() if value is not None}
    extra = normalize_chat_kwargs(deployment, given, config=gaik_config(model=deployment))
    return {
        "model": deployment,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": schema_model,
        **extra,
    }


def _parsed[ModelT: BaseModel](completion: Any, schema_model: type[ModelT]) -> ModelT:
    message = completion.choices[0].message if completion.choices else None
    parsed = getattr(message, "parsed", None)
    if not isinstance(parsed, schema_model):
        logger.error(
            "Structured chat returned no %s (refusal: %s)",
            schema_model.__name__,
            bool(getattr(message, "refusal", None)),
        )
        raise _chat_failed()
    return parsed


def _chat_failed() -> AppError:
    return AppError("model_failed", "Tekoäly ei vastannut. Yritä hetken kuluttua uudelleen.", 502)


def scrub(text: str) -> str:
    """A text with every configured secret replaced by its variable name, for log lines."""
    settings = get_settings()
    for name, field in type(settings).model_fields.items():
        value = getattr(settings, name)
        if not field.repr and isinstance(value, str) and len(value) >= 6:
            text = text.replace(value, f"<{name.upper()}>")
    return text


def _log_chat_error(error: Exception) -> None:
    logger.error(
        "Structured chat failed with %s (status %s): %s",
        type(error).__name__,
        getattr(error, "status_code", None),
        scrub(str(error))[:500],
    )


async def structured_chat[ModelT: BaseModel](
    system: str,
    user: str,
    schema_model: type[ModelT],
    *,
    model: str | None = None,
    **options: Any,
) -> ModelT:
    """One structured chat call on the Azure chat deployment; returns a `schema_model` object.

    `options` go to the API (`reasoning_effort="low"`, `temperature=0`, `max_tokens=...`),
    normalised by gaik for the deployment's model family. None are sent by default.
    """
    from openai import AsyncAzureOpenAI, OpenAIError
    from pydantic import ValidationError

    arguments = _azure_arguments(get_settings())
    request = _chat_request(system, user, schema_model, model, options)
    try:
        # One client per call: a cached async client would be tied to the first event loop.
        async with AsyncAzureOpenAI(**arguments) as client:
            completion = await client.chat.completions.parse(**request)
    except (OpenAIError, ValidationError) as error:
        _log_chat_error(error)
        raise _chat_failed() from error
    return _parsed(completion, schema_model)


def structured_chat_sync[ModelT: BaseModel](
    system: str,
    user: str,
    schema_model: type[ModelT],
    *,
    model: str | None = None,
    **options: Any,
) -> ModelT:
    """`structured_chat` for code that already runs in a worker thread or a script."""
    from openai import OpenAIError
    from pydantic import ValidationError

    client = _sync_client(tuple(sorted(_azure_arguments(get_settings()).items())))
    request = _chat_request(system, user, schema_model, model, options)
    try:
        completion = client.chat.completions.parse(**request)
    except (OpenAIError, ValidationError) as error:
        _log_chat_error(error)
        raise _chat_failed() from error
    return _parsed(completion, schema_model)
