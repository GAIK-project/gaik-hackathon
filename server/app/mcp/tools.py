"""The MCP tools (docs/ARCHITECTURE.md sections 8.5 and 15.10).

Each tool is an async function that calls `core/gaik_ops`, `reports.service` or
`aiact.service` and returns a JSON object. `@tool` wraps it with what every call needs: the
activity record, the model-call budget, English error text and a summary with counts only.

There is no tool that approves a report, sends mail, fetches a URL or reads a path. A file
arrives as `content_base64`, is accepted by its first bytes (section 15.4), stored under a
server-made name and deleted when the call ends.

`SPECS` is what the server registers and what `/api/mcp/info` and `/api/mcp/try` read.
"""

from __future__ import annotations

import base64
import dataclasses
import functools
import importlib
import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from app.core import files, gaik_ops, limits
from app.core.errors import AppError
from app.core.models import FieldDef
from app.core.settings import get_settings
from app.core.util import new_id
from app.mcp import activity

logger = logging.getLogger(__name__)

NOTICE_PERSONAL = "Do not share personal data."
NOTICE_US = "The description is also processed in the United States."

MAX_TEXT_CHARS = 50_000
MAX_QUERY_CHARS = 500
MAX_DESCRIPTION_CHARS = 4_000
LANGUAGES = ("en", "fi", "sv")
MODEL_KEY = "mcp"  # every MCP client shares one model-call budget (section 15.3)
SAMPLE_PLACEHOLDER = "<built-in sample file>"
_SAMPLES_DIR = Path(__file__).parent / "samples"

Result = dict[str, Any]


class ToolFailure(ToolError):
    """A tool error for the agent: a code, an English sentence and the HTTP status the
    "Try it" route answers with."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# English text for the errors of the core, whose own messages are written for another surface.
_BY_CODE: dict[str, str] = {
    "models_off": "The AI features are switched off right now.",
    "too_many_requests": "Too many requests. Try again in a few minutes.",
    "too_large": "The file is too large. The limit is 15 MB.",
    "invalid_base64": "content_base64 is not valid base64.",
    "empty_file": "The file is empty.",
    "unsupported_media_type": (
        "The file format is not supported. Documents: PDF, DOCX or TXT. "
        "Audio: ogg, webm, mp3, wav or m4a."
    ),
    "unsupported_document": "The document format is not supported. Use PDF, DOCX or TXT.",
    "invalid_audio": "The audio file could not be read.",
    "audio_too_long": "The recording is too long. The limit is 10 minutes.",
    "audio_unavailable": "Audio processing is not available right now.",
    "no_speech": "No speech was found in the recording.",
    "empty_text": "The text is missing.",
    "invalid_labels": "Give between 2 and 50 labels, each with a description.",
    "upstream_timeout": "The model did not answer in time. Try again.",
    "not_configured": "The AI features are not configured on this server.",
}
_BY_STATUS: dict[int, str] = {
    400: "The request is not valid.",
    404: "Not found.",
    409: "The state has changed. Read the report again.",
    413: "The content is too large.",
    415: "The file format is not supported.",
    429: "Too many requests. Try again in a few minutes.",
    502: "The model service did not answer. Try again.",
    503: "The service is not available right now.",
    504: "The model did not answer in time. Try again.",
}
_GENERIC = "The tool failed. Try again."


def _failure_of(error: AppError) -> ToolFailure:
    message = _BY_CODE.get(error.code) or _BY_STATUS.get(error.status) or _GENERIC
    return ToolFailure(error.code, message, error.status)


def in_english(error: AppError) -> AppError:
    """A core error with its message in English, for the routes of the MCP page."""
    failure = _failure_of(error)
    return AppError(failure.code, failure.message, failure.status, headers=error.headers)


# who is calling ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Caller:
    """Set by the "Try it" route: the client name to show, and whether the tool still has to
    count the call against the MCP budget (the route counts it for the browser session)."""

    client: str
    count_model_call: bool = False


_caller: ContextVar[Caller | None] = ContextVar("mcp_caller", default=None)


def calling_as(caller: Caller):
    """Context-variable token for a server-side call: `token = calling_as(...)`, then
    `reset_caller(token)`."""
    return _caller.set(caller)


def reset_caller(token: Any) -> None:
    _caller.reset(token)


def _client_name(ctx: Context | None) -> str | None:
    """The MCP client's name: from its `initialize` call when the session has one, else the
    product token of its User-Agent."""
    if ctx is None:
        return None
    try:
        info = getattr(ctx.session.client_params, "client_info", None)
        if info is not None and info.name:
            return str(info.name)
    except Exception:  # noqa: BLE001  (no session outside a request, or a sessionless client)
        pass
    try:
        agent = (ctx.headers or {}).get("user-agent", "")
    except Exception:  # noqa: BLE001
        agent = ""
    return agent.split("/")[0].split(" ")[0] or None


# the wrapper ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    model_backed: bool
    run: Callable[..., Awaitable[Result]]  # what the MCP server registers
    sample: Callable[[], Awaitable[dict[str, Any]]]  # small built-in arguments for "Try it"
    sample_shown: dict[str, Any]  # the same arguments as shown on the page (no file content)


SPECS: dict[str, ToolSpec] = {}
_counts: ContextVar[str] = ContextVar("mcp_counts", default="done")


def _done(result: Result, counts: str) -> Result:
    """What a tool returns: the result, with the counts noted for the activity summary."""
    _counts.set(counts)
    return result


def tool(
    name: str,
    description: str,
    *,
    model_backed: bool,
    sample: Callable[[], Awaitable[dict[str, Any]]],
    sample_shown: dict[str, Any],
):
    """Register a tool function. It ends with `return _done(result, counts)`; `counts` is the
    part of the activity summary after the tool name and holds numbers only."""

    def decorate(fn: Callable[..., Awaitable[Result]]):
        @functools.wraps(fn)
        async def run(*args: Any, ctx: Context | None = None, **kwargs: Any) -> Result:
            caller = _caller.get()
            client = caller.client if caller else _client_name(ctx)
            record = activity.start(name, client)
            started = time.perf_counter()
            failure: ToolFailure | None = None
            try:
                if model_backed and (caller is None or caller.count_model_call):
                    limits.count_model_call(MODEL_KEY)
                _counts.set("done")
                result = await fn(*args, **kwargs)
            except ToolFailure as error:
                failure = error
            except AppError as error:
                failure = _failure_of(error)
            except Exception as error:  # noqa: BLE001  (provider text must not reach the agent)
                logger.error(
                    "MCP tool %s failed with %s", name, type(error).__name__, exc_info=error
                )
                failure = ToolFailure("internal_error", _GENERIC, 500)
            ms = int((time.perf_counter() - started) * 1000)
            if failure is not None:
                activity.finish(record, ok=False, ms=ms, summary=f"{name}: failed ({failure.code})")
                raise failure
            activity.finish(record, ok=True, ms=ms, summary=f"{name}: {_counts.get()}")
            return result

        SPECS[name] = ToolSpec(name, description, model_backed, run, sample, sample_shown)
        return run

    return decorate


# helpers -------------------------------------------------------------------------------------


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _text(value: str | None, *, name: str = "text", max_chars: int = MAX_TEXT_CHARS) -> str:
    text = (value or "").strip()
    if not text:
        raise ToolFailure("empty_text", f"{name} is missing.", 400)
    if len(text) > max_chars:
        raise ToolFailure(
            "too_large", f"{name} is too long. The limit is {max_chars} characters.", 413
        )
    return text


def _dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    return value


def _service(feature: str) -> Any:
    """`app.<feature>.service`, imported when a tool needs it."""
    try:
        return importlib.import_module(f"app.{feature}.service")
    except ImportError as error:
        logger.warning("MCP tool needs app.%s.service, which did not import: %s", feature, error)
        raise ToolFailure(
            "not_available", "This tool is not available on this server yet.", 503
        ) from None


def _absolute(url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    return get_settings().public_base_url.rstrip("/") + "/" + url.lstrip("/")


async def _stored(content_base64: str, kind: files.FileKind) -> tuple[files.StoredFile, str]:
    """Decode a file argument (15 MB at most), accept it by its first bytes and store it
    under a server-made name in a folder of its own. Returns the file and that folder."""
    data = limits.decode_base64(content_base64)
    scope = f"mcp/{new_id('call')}"
    try:
        return await files.save_upload(scope, data, kind), scope
    except BaseException:
        _discard(scope)
        raise


def _discard(scope: str) -> None:
    try:
        folder = files.scope_dir(scope)
        for item in folder.glob("*"):
            item.unlink(missing_ok=True)
        folder.rmdir()
    except OSError:
        logger.warning("Could not remove the working files of an MCP call")


def _report(report: Any) -> Result:
    """A report for an agent: the summary, the fields and absolute links to its files. The
    transcript, the timeline and the mail draft stay in the app."""
    data = _dump(report)
    report_files = [
        {
            "name": item.get("name"),
            "kind": item.get("kind"),
            "size": item.get("size"),
            "url": _absolute(item.get("url") or ""),
        }
        for item in data.get("files") or []
    ]
    word = next((item["url"] for item in report_files if item["kind"] == "docx"), None)
    keys = (
        "id",
        "created_at",
        "updated_at",
        "channel",
        "type_id",
        "type_title",
        "status",
        "filled",
        "total",
        "urgency",
        "headline",
    )
    return {
        **{key: data.get(key) for key in keys},
        "fields": [
            {key: field.get(key) for key in ("name", "label", "value", "evidence")}
            for field in data.get("fields") or []
        ],
        "missing": data.get("missing") or [],
        "word_file_url": word,
        "files": report_files,
    }


# samples -------------------------------------------------------------------------------------


def tiny_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with the given lines of plain ASCII text."""

    def escape(line: str) -> str:
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    text = " ".join(f"({escape(line)}) Tj T*" for line in lines)
    stream = f"BT /F1 12 Tf 72 770 Td 18 TL {text} ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


SAMPLE_DOCUMENT_LINES = [
    "Service report",
    "Site: Harbour Road 5, cooling unit 2",
    "Date: 12 November 2026",
    "Work done: the pump seal was replaced and the unit was pressure tested.",
    "Hours: 2.5",
    "Follow-up: check the seal again in one week.",
]
SAMPLE_SERVICE_TEXT = (
    "Service visit on 12 November 2026 at Harbour Road 5. The pump seal of cooling unit 2 was "
    "replaced and the unit was pressure tested. The work took 2.5 hours. The seal has to be "
    "checked again in one week."
)
SAMPLE_REQUIREMENT = (
    "From a service visit collect the date, the site address, the work done, the hours worked "
    "and whether a follow-up is needed."
)
SAMPLE_LABELS = {
    "urgent": "Danger to people or property; someone has to act now.",
    "normal": "Needs attention during the working day.",
    "not_urgent": "Can wait; information only.",
}
SAMPLE_REPORT_TEXT = (
    "Safety observation. On 12 November 2026 at 9.30 a loaded pallet was blocking the "
    "emergency exit of warehouse B. I moved it aside and told the shift supervisor. Nobody "
    "was hurt. The exit should get floor markings so it stays clear."
)


def _fixed(arguments: dict[str, Any]) -> Callable[[], Awaitable[dict[str, Any]]]:
    async def sample() -> dict[str, Any]:
        return dict(arguments)

    return sample


async def _sample_document() -> dict[str, Any]:
    content = base64.b64encode(tiny_pdf(SAMPLE_DOCUMENT_LINES)).decode()
    return {"filename": "service-report.pdf", "content_base64": content}


async def _sample_audio() -> dict[str, Any]:
    content = base64.b64encode((_SAMPLES_DIR / "sample-en.mp3").read_bytes()).decode()
    return {"filename": "sample-en.mp3", "content_base64": content, "language": "en"}


async def _sample_report_id() -> dict[str, Any]:
    reports = await _service("reports").list_reports(limit=1)
    if not reports:
        raise ToolFailure("no_reports", "There are no reports yet. Create one first.", 404)
    return {"report_id": _dump(reports[0])["id"]}


# gaik components -----------------------------------------------------------------------------


@tool(
    "parse_document",
    "Read a PDF, DOCX or TXT file and return its text, page by page (gaik document parser). "
    "Send the file as base64 in content_base64, at most 15 MB; the format is detected from "
    f"the content, not from the name. {NOTICE_PERSONAL}",
    model_backed=False,
    sample=_sample_document,
    sample_shown={"filename": "service-report.pdf", "content_base64": SAMPLE_PLACEHOLDER},
)
async def parse_document(filename: str, content_base64: str, ctx: Context | None = None) -> Result:
    stored, scope = await _stored(content_base64, "document")
    try:
        document = await gaik_ops.parse_document(stored.path)
    finally:
        _discard(scope)
    result = {
        "filename": files.safe_name(filename, fallback="document"),
        "format": document.kind,
        "page_count": len(document.pages),
        "text": document.text,
        "pages": document.pages,
    }
    counts = f"1 {document.kind} file, {_count(len(document.pages), 'page')}, "
    return _done(result, counts + _count(len(document.text), "character"))


@tool(
    "extract_fields",
    "Pull structured values out of a text (gaik extractor). Give either requirement, a "
    "plain-language sentence about what to collect (the fields are then generated), or "
    "fields, a list of {name, label, type, required, description, options?} with type one "
    "of text, longtext, date, time, number, choice. Returns the fields used and one value "
    f"per field with the words in the text it rests on. {NOTICE_PERSONAL}",
    model_backed=True,
    sample=_fixed({"text": SAMPLE_SERVICE_TEXT, "requirement": SAMPLE_REQUIREMENT}),
    sample_shown={"text": SAMPLE_SERVICE_TEXT, "requirement": SAMPLE_REQUIREMENT},
)
async def extract_fields(
    text: str,
    requirement: str | None = None,
    fields: list[FieldDef] | None = None,
    ctx: Context | None = None,
) -> Result:
    text = _text(text)
    requirement = (requirement or "").strip()
    if fields:
        if len(fields) > gaik_ops.MAX_FIELDS:
            raise ToolFailure("too_many_fields", f"At most {gaik_ops.MAX_FIELDS} fields.", 400)
        generated = False
    elif requirement:
        if len(requirement) > gaik_ops.MAX_REQUIREMENT_CHARS:
            raise ToolFailure("too_large", "requirement is too long.", 413)
        fields = await gaik_ops.generate_fields(requirement)
        generated = True
    else:
        raise ToolFailure("missing_argument", "Give requirement or fields.", 400)
    values = await gaik_ops.extract_fields(text, fields, requirement)
    dumped = [_dump(value) for value in values]
    filled = sum(1 for value in dumped if value.get("value") not in (None, ""))
    result = {
        "fields": [_dump(field) for field in fields],
        "fields_generated": generated,
        "values": [
            {key: value.get(key) for key in ("name", "label", "value", "evidence", "verdict")}
            for value in dumped
        ],
        "filled": filled,
        "total": len(dumped),
    }
    counts = f"{_count(len(fields), 'field')}, {filled} filled, {_count(len(text), 'character')}"
    return _done(result, counts)


@tool(
    "transcribe",
    "Turn speech into text (gaik transcriber). Send an ogg, webm, mp3, wav or m4a file as "
    "base64 in content_base64, at most 15 MB and 10 minutes; the format is detected from "
    f"the content. language is en (default), fi or sv. {NOTICE_PERSONAL}",
    model_backed=True,
    sample=_sample_audio,
    sample_shown={
        "filename": "sample-en.mp3",
        "content_base64": SAMPLE_PLACEHOLDER,
        "language": "en",
    },
)
async def transcribe(
    filename: str, content_base64: str, language: str = "en", ctx: Context | None = None
) -> Result:
    language = (language or "en").strip().lower()
    if language not in LANGUAGES:
        raise ToolFailure("invalid_language", "language is en, fi or sv.", 400)
    stored, scope = await _stored(content_base64, "audio")
    try:
        wav = await files.to_wav(stored.path, stored.format)
        transcript = await gaik_ops.transcribe_audio(wav.path, language)
    finally:
        _discard(scope)
    seconds = round(transcript.duration_s or wav.duration_s, 1)
    result = {
        "filename": files.safe_name(filename, fallback="audio"),
        "format": stored.format,
        "language": language,
        "duration_s": seconds,
        "text": transcript.text,
    }
    counts = f"1 {stored.format} file, {seconds} s, {_count(len(transcript.text), 'character')}"
    return _done(result, counts)


@tool(
    "classify",
    "Choose the best label for a text and return calibrated probabilities for every label "
    "(gaik Jev classifier). labels maps each label name to a sentence about what it means; "
    "2 to 50 labels. Only the first 2 000 characters of the text are read. "
    f"{NOTICE_PERSONAL} {NOTICE_US}",
    model_backed=True,
    sample=_fixed(
        {
            "text": "Water is dripping from the ceiling onto the electrical cabinet in hall 3.",
            "labels": SAMPLE_LABELS,
        }
    ),
    sample_shown={
        "text": "Water is dripping from the ceiling onto the electrical cabinet in hall 3.",
        "labels": SAMPLE_LABELS,
    },
)
async def classify(text: str, labels: dict[str, str], ctx: Context | None = None) -> Result:
    text = _text(text)
    outcome = await gaik_ops.classify_jev(text, labels)
    result = {
        "label": outcome.label,
        "confidence": outcome.confidence,
        "probabilities": outcome.probabilities,
    }
    read = min(len(text), gaik_ops.JEV_MAX_CHARS)
    return _done(result, f"{_count(len(labels), 'label')}, {_count(read, 'character')}")


# AI Act --------------------------------------------------------------------------------------


@tool(
    "search_ai_act",
    "Search the EU AI Act (Regulation (EU) 2024/1689 as amended) and return the passages "
    "that match best, each with its unit id and citation label such as "
    "'Annex III, point 4(a)'.",
    model_backed=True,
    sample=_fixed({"query": "emotion recognition in the workplace"}),
    sample_shown={"query": "emotion recognition in the workplace"},
)
async def search_ai_act(query: str, ctx: Context | None = None) -> Result:
    query = _text(query, name="query", max_chars=MAX_QUERY_CHARS)
    hits = _dump(await _service("aiact").search(query, 8))
    counts = f"{_count(len(hits), 'hit')}, {_count(len(query), 'character')}"
    return _done({"hits": hits}, counts)


@tool(
    "assess_ai_system",
    "Estimate the likely risk tier of an AI system under the EU AI Act from a description "
    "of what it does and who uses it: prohibited, high, transparency or minimal, with "
    "probabilities, the provisions the estimate rests on (quoted and checked against the "
    "regulation) and the obligations that follow. This is not legal advice. "
    f"{NOTICE_PERSONAL} {NOTICE_US}",
    model_backed=True,
    sample=_fixed(
        {
            "description": "A tool that ranks the CVs of job applicants and recommends who "
            "should be invited to an interview."
        }
    ),
    sample_shown={
        "description": "A tool that ranks the CVs of job applicants and recommends who "
        "should be invited to an interview."
    },
)
async def assess_ai_system(description: str, ctx: Context | None = None) -> Result:
    description = _text(description, name="description", max_chars=MAX_DESCRIPTION_CHARS)
    assessment = _dump(await _service("aiact").assess(description))
    assessment.pop("description", None)
    basis = len(assessment.get("basis") or [])
    obligations = len(assessment.get("obligations") or [])
    counts = (
        f"{_count(basis, 'provision')}, {_count(obligations, 'obligation')}, "
        f"{_count(len(description), 'character')}"
    )
    return _done(assessment, counts)


# reports -------------------------------------------------------------------------------------


@tool(
    "list_reports",
    "List the newest reports of the app (at most 50): id, type, status, how many required "
    "fields are filled, urgency and a one-line headline. status filters by live, "
    "processing, needs_approval, sent or failed.",
    model_backed=False,
    sample=_fixed({}),
    sample_shown={},
)
async def list_reports(status: str | None = None, ctx: Context | None = None) -> Result:
    wanted = (status or "").strip().lower() or None
    if wanted and wanted not in ("live", "processing", "needs_approval", "sent", "failed"):
        raise ToolFailure(
            "invalid_status", "status is live, processing, needs_approval, sent or failed.", 400
        )
    reports = _dump(await _service("reports").list_reports(limit=50))
    if wanted:
        reports = [report for report in reports if report.get("status") == wanted]
    return _done({"reports": reports}, _count(len(reports), "report"))


@tool(
    "get_report",
    "Read one report: its fields with values, the required fields still missing and an "
    "absolute link to its Word file. The link opens for a person signed in to the app.",
    model_backed=False,
    sample=_sample_report_id,
    sample_shown={"report_id": "<the newest report>"},
)
async def get_report(report_id: str, ctx: Context | None = None) -> Result:
    report_id = _text(report_id, name="report_id", max_chars=80)
    report = await _service("reports").get_report(report_id)
    if report is None:
        raise ToolFailure("not_found", "There is no report with that id.", 404)
    result = _report(report)
    fields, attached = len(result["fields"]), len(result["files"])
    counts = f"1 report, {_count(fields, 'field')}, {_count(attached, 'file')}"
    return _done(result, counts)


@tool(
    "create_report",
    "Create a report from a free-text account of what happened: the app picks the report "
    "type (or uses type_id), fills the fields, writes the Word file and leaves the report "
    "waiting for a person's approval in the app. Nothing is sent by this tool. "
    f"{NOTICE_PERSONAL}",
    model_backed=True,
    sample=_fixed({"text": SAMPLE_REPORT_TEXT}),
    sample_shown={"text": SAMPLE_REPORT_TEXT},
)
async def create_report(
    text: str, type_id: str | None = None, ctx: Context | None = None
) -> Result:
    text = _text(text)
    type_id = (type_id or "").strip() or None
    service = _service("reports")
    if type_id and await service.get_report_type(type_id) is None:
        raise ToolFailure("unknown_type", "There is no report type with that type_id.", 404)
    report, reply = await service.intake_text(
        text, type_id=type_id, channel="mcp", caller_label="MCP", wait=True
    )
    result = _report(report)
    result["reply"] = reply
    counts = (
        f"1 report, {result['filled']}/{result['total']} required fields, "
        f"{_count(len(text), 'character')}"
    )
    return _done(result, counts)
