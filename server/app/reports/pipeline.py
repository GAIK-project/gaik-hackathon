"""The intake pipeline (docs/ARCHITECTURE.md sections 8.1 and 15.7).

Six steps, each one `TimelineEvent` that is published as `running` and then as `done`,
`waiting` or `error`:

1. `transcribe`  audio to text (ffmpeg, then gaik's Transcriber); skipped for text and calls
2. `classify`    pick the report type when none was given (Azure, in the EU)
3. `extract`     gaik's extractor fills the type's fields, with the words each value rests on
4. `urgency`     three labels (Azure, in the EU)
5. `document`    the Word file
6. `email`       a draft that waits for a person's approval

Steps 3 and 4 run side by side. A failing step records its error and the pipeline goes on with
what it has; the report is `failed` only when nothing useful exists.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.core import files, gaik_ops, llm
from app.core.errors import AppError
from app.core.models import Report, ReportType
from app.core.settings import get_settings
from app.core.util import now_iso
from app.reports import mail, store, types
from app.reports.docx import URGENCY_NAMES, build_docx

logger = logging.getLogger(__name__)

TYPE_CONFIDENCE_GATE = 0.5
MergeMode = Literal["replace", "keep_voice", "overlay"]

URGENCY_LABELS = {
    "kiireellinen": "Urgent: danger to people, an injury, or damage that grows unless someone "
    "acts today.",
    "normaali": "Normal: needs handling in the coming days, no immediate danger.",
    "ei kiireellinen": "Not urgent: routine information for the record, nothing to act on soon.",
}
URGENCY_INSTRUCTIONS = (
    "The text is a field report, spoken or written, possibly in Finnish. Decide how urgently "
    "someone has to act on it."
)
TYPE_INSTRUCTIONS = (
    "The text is a field report, spoken or written, possibly in Finnish. Pick the report type "
    "it belongs to."
)

STEP_ERRORS = {
    "transcribe": "The audio could not be transcribed.",
    "classify": "The report type could not be identified.",
    "extract": "The fields could not be extracted.",
    "urgency": "The urgency could not be assessed.",
    "document": "The Word report could not be created.",
    "email": "The email draft could not be written.",
}
ERROR_CODES = {
    "no_speech": "No speech was found in the recording.",
    "audio_too_long": "The recording is longer than 10 minutes.",
}


@dataclass
class Outcome:
    label: str
    detail: str | None = None
    status: str = "done"
    apply: Callable[[dict[str, Any]], Any] | None = None
    value: Any = None


async def run_step(
    report_id: str,
    kind: str,
    label: str,
    component: str,
    work: Callable[[], Awaitable[Outcome]],
) -> Outcome | None:
    """One pipeline step as one timeline event. Returns `None` when the step failed."""
    event = store.new_event(kind, label, component=component, status="running")
    store.add_event(report_id, event)
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        outcome = await work()
    except Exception as error:  # noqa: BLE001 - the pipeline goes on with what it has
        code = error.code if isinstance(error, AppError) else type(error).__name__
        logger.warning("report %s: step %s failed (%s)", report_id, kind, code)
        detail = ERROR_CODES.get(code, STEP_ERRORS.get(kind, "This step failed."))
        ms = elapsed()
        store.mutate(
            report_id,
            lambda data: store.change_event(data, event.id, status="error", detail=detail, ms=ms),
        )
        return None
    except BaseException:
        store.mutate(
            report_id,
            lambda data: store.change_event(
                data, event.id, status="error", detail="Interrupted.", ms=elapsed()
            ),
        )
        raise

    ms = elapsed()

    def change(data: dict[str, Any]) -> None:
        if outcome.apply is not None:
            outcome.apply(data)
        store.change_event(
            data, event.id, label=outcome.label, detail=outcome.detail, status=outcome.status, ms=ms
        )

    store.mutate(report_id, change)
    return outcome


def set_type(data: dict[str, Any], rtype: ReportType) -> None:
    data["type_id"] = rtype.id
    data["type_title"] = rtype.title
    data["fields"] = store.empty_fields(rtype)
    data["x"].update(store.type_keys(rtype))


def transcript_text(report: Report) -> str:
    turns = [turn for turn in report.transcript if turn.text.strip()]
    if all(turn.role == "caller" for turn in turns):
        return "\n".join(turn.text.strip() for turn in turns)
    names = {"agent": "Agent", "caller": "Caller"}
    return "\n".join(f"{names.get(turn.role, turn.role)}: {turn.text.strip()}" for turn in turns)


def fail(report_id: str, detail: str) -> Report:
    event = store.new_event("error", "Report failed", detail=detail, status="error")

    def change(data: dict[str, Any]) -> None:
        data["status"] = "failed"
        store.append_event(data, event)

    return store.mutate(report_id, change)


# steps --------------------------------------------------------------------------------------


async def transcribe(
    report_id: str, audio: Path, audio_format: str | None, language: str
) -> str | None:
    async def work() -> Outcome:
        target = files.file_path(store.scope_of(report_id), "speech.16k.wav", create_dir=True)
        try:
            wav = await files.to_wav(audio, audio_format, target)
            transcript = await gaik_ops.transcribe_audio(wav.path, language)
        finally:
            target.unlink(missing_ok=True)
        text = transcript.text.strip()
        seconds = transcript.duration_s or wav.duration_s

        def apply(data: dict[str, Any]) -> None:
            data.setdefault("transcript", []).append(
                {"role": "caller", "text": text, "at": now_iso()}
            )

        detail = f"{round(seconds)} s of speech, {len(text.split())} words" if seconds else None
        return Outcome("Audio transcribed", detail, apply=apply, value=text)

    outcome = await run_step(report_id, "transcribe", "Transcribing audio", "transcriber", work)
    return outcome.value if outcome else None


async def classify_type(report_id: str, text: str) -> None:
    fallback = types.get_type(types.FALLBACK_TYPE_ID)
    assert fallback is not None

    async def work() -> Outcome:
        known = {rtype.id: rtype for rtype in types.list_types()}
        labels = {rtype.id: f"{rtype.title}. {rtype.description}" for rtype in known.values()}
        result = await gaik_ops.classify_azure(text, labels, TYPE_INSTRUCTIONS)
        percent = f"{round(result.confidence * 100)} %"
        picked = known.get(result.label)
        if picked is None or result.confidence < TYPE_CONFIDENCE_GATE:
            return Outcome(
                f"Type: {fallback.title}",
                f"Low confidence ({percent}), using {fallback.title}",
                apply=lambda data: set_type(data, fallback),
            )
        return Outcome(
            f"Type: {picked.title}",
            f"Confidence {percent}",
            apply=lambda data: set_type(data, picked),
        )

    outcome = await run_step(
        report_id, "classify", "Identifying report type", "classifier (Azure, EU)", work
    )
    if outcome is None:
        store.mutate(report_id, lambda data: set_type(data, fallback))


def merge(data: dict[str, Any], values: list[Any], mode: MergeMode) -> None:
    current = {field["name"]: field for field in data["fields"]}
    merged = []
    for item in values:
        old = current.get(item.name)
        keep_old = (
            old is not None
            and bool(old.get("value"))
            and (
                (mode == "keep_voice" and old.get("source") == "voice")
                or (mode == "overlay" and not item.value)
            )
        )
        if keep_old:
            assert old is not None
            if not old.get("evidence") and item.evidence and mode == "keep_voice":
                old["evidence"] = item.evidence
            merged.append(old)
        else:
            merged.append(
                {
                    "name": item.name,
                    "label": item.label,
                    "value": item.value or None,
                    "evidence": item.evidence if item.value else None,
                    "source": "extractor",
                }
            )
    data["fields"] = merged


async def extract(report_id: str, text: str, mode: MergeMode = "replace") -> bool:
    async def work() -> Outcome:
        report = store.load(report_id)
        rtype = types.get_type(report.type_id) if report else None
        if rtype is None:
            raise AppError("unknown_type", "The report type no longer exists.", 409)
        values = await gaik_ops.extract_fields(text, rtype.fields, rtype.requirement)
        outcome = Outcome("Fields extracted")

        def apply(data: dict[str, Any]) -> None:
            merge(data, values, mode)
            fields = data["fields"]
            filled = sum(1 for field in fields if field.get("value"))
            required = set(data["x"].get("required", []))
            missing = [f["label"] for f in fields if f["name"] in required and not f.get("value")]
            outcome.label = f"Fields extracted {filled}/{len(fields)}"
            outcome.detail = ("Missing: " + ", ".join(missing)) if missing else None

        outcome.apply = apply
        return outcome

    step = await run_step(
        report_id, "extract", "Extracting fields", "extractor + source tracing", work
    )
    return step is not None


async def urgency(report_id: str, text: str) -> None:
    async def work() -> Outcome:
        result = await gaik_ops.classify_azure(text, URGENCY_LABELS, URGENCY_INSTRUCTIONS)
        label = result.label if result.label in URGENCY_LABELS else "normaali"
        value = {
            "label": label,
            "confidence": result.confidence,
            "probabilities": result.probabilities,
            "model": result.model,
        }
        return Outcome(
            f"Urgency: {URGENCY_NAMES[label].lower()}",
            f"Confidence {round(result.confidence * 100)} %",
            apply=lambda data: data.__setitem__("urgency", value),
        )

    await run_step(report_id, "urgency", "Assessing urgency", "classifier (Azure, EU)", work)


def docx_name(report_id: str) -> str:
    return f"report_{report_id}.docx"


def _write_docx(report: Report) -> dict[str, Any]:
    """Blocking: build the Word file and store it under the report's own name."""
    name = docx_name(report.id)
    stored = files.save_file(store.scope_of(report.id), name, build_docx(report))
    return {
        "name": name,
        "kind": "docx",
        "size": stored.size,
        "url": f"/api/reports/{report.id}/files/{name}",
    }


def _set_file(data: dict[str, Any], entry: dict[str, Any]) -> None:
    others = [item for item in data.get("files", []) if item["name"] != entry["name"]]
    data["files"] = [entry, *others]
    data["x"]["docx"] = entry["name"]


def _draft(data: dict[str, Any]) -> None:
    report = Report.model_validate(data)
    previous = data.get("email") or {}
    data["email"] = {
        "to": previous.get("to") or get_settings().mail_default_recipient,
        "subject": mail.subject_of(report),
        "body": mail.body_of(report),
        "status": "draft",
        "sent_at": None,
    }


async def document(report_id: str) -> bool:
    async def work() -> Outcome:
        report = store.load(report_id)
        if report is None:
            raise store.not_found()
        entry = await llm.run_blocking(_write_docx, report)
        return Outcome("Word report created", apply=lambda data: _set_file(data, entry))

    return (
        await run_step(report_id, "document", "Creating Word report", "python-docx", work)
        is not None
    )


async def email(report_id: str) -> None:
    async def work() -> Outcome:
        settings = get_settings()

        def apply(data: dict[str, Any]) -> None:
            store.derive(data)  # the draft quotes the headline and the missing fields
            _draft(data)
            data["status"] = "needs_approval"

        to = settings.mail_default_recipient
        detail = f"To: {to}" if to else "No recipient is configured"
        if not settings.mail_enabled:
            detail += " · mail is off, approval writes a file"
        return Outcome("Email draft waits for approval", detail, status="waiting", apply=apply)

    await run_step(report_id, "email", "Drafting email", "mail template", work)


async def rebuild_outputs(report_id: str) -> Report:
    """After an edit or a correction: the Word file and the draft follow the fields. No
    timeline steps of their own; the caller records the edit."""
    report = store.load(report_id)
    if report is None:
        raise store.not_found()
    raw = store.load_raw(report_id) or {}
    if not raw.get("x", {}).get("docx"):
        return report
    entry = await llm.run_blocking(_write_docx, report)

    def change(data: dict[str, Any]) -> None:
        _set_file(data, entry)
        if (data.get("email") or {}).get("status") != "sent":
            store.derive(data)
            _draft(data)

    return store.mutate(report_id, change)


# the whole run ------------------------------------------------------------------------------


async def finish(
    report_id: str, text: str, *, mode: MergeMode = "replace", run_extract: bool = True
) -> None:
    """Steps 3 to 6."""
    jobs = [urgency(report_id, text)]
    if run_extract:
        jobs.append(extract(report_id, text, mode))
    await asyncio.gather(*jobs)
    built = await document(report_id)
    report = store.load(report_id)
    if report is None:
        return
    if not built and not any(field.value for field in report.fields):
        fail(report_id, "Nothing could be read from the input.")
        return
    await email(report_id)
    if (after := store.load(report_id)) is not None and after.status != "needs_approval":
        fail(report_id, STEP_ERRORS["email"])


async def process(
    report_id: str,
    *,
    audio: Path | None = None,
    audio_format: str | None = None,
    text: str | None = None,
    pick_type: bool = False,
    language: str = "auto",
) -> None:
    """The whole pipeline for one report. Never raises: a failure ends in status `failed`."""
    try:
        if audio is not None:
            text = await transcribe(report_id, audio, audio_format, language)
        text = (text or "").strip()
        if not text:
            if pick_type:
                fallback = types.get_type(types.FALLBACK_TYPE_ID)
                store.mutate(report_id, lambda data: set_type(data, fallback))
            fail(report_id, "There was nothing to make a report from.")
            return
        if pick_type:
            await classify_type(report_id, text)
        await finish(report_id, text)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("report %s: the pipeline stopped", report_id)
        try:
            fail(report_id, "Something went wrong while the report was processed.")
        except AppError:
            pass  # the report was deleted meanwhile
