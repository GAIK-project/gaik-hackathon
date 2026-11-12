"""The only way other features touch reports (docs/ARCHITECTURE.md section 15.7).

Voice, MCP and the WhatsApp bridge import this module; the HTTP routes in `router.py` are a
thin layer over it. Every public function is `async def`.

    report = await service.create_report("tyomaapaivakirja", "call", "Browser call", "live")
    await service.set_fields(report.id, {"weather": "sleet"}, "voice")
    await service.add_transcript(report.id, "caller", "...")
    await service.finish_call(report.id)                  # steps 3 to 6 in the background

    report, reply = await service.intake_text("...")                 # waits for the pipeline
    report, reply = await service.intake_audio(path, wait=False)     # answers at once

Every change is published on the bus: topic `reports` (event `report`, a `ReportSummary`)
and topic `report:<id>` (event `report`, the full `Report`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core import files, gaik_ops, limits, llm
from app.core.errors import AppError
from app.core.models import Channel, Report, ReportStatus, ReportSummary, ReportType, TimelineEvent
from app.core.settings import get_settings
from app.core.util import new_id, now_iso, to_iso
from app.reports import mail, pipeline, store, types

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 20000
MAX_LABEL_CHARS = 40
STALE_AFTER = timedelta(minutes=15)
CHANNELS = ("call", "whatsapp", "upload", "text", "mcp")
SOURCES = ("voice", "extractor", "edit")
DEFAULT_LABELS = {
    "call": "Browser call",
    "whatsapp": "WhatsApp",
    "upload": "Audio file",
    "text": "Text",
    "mcp": "Agent",
}

_tasks: set[asyncio.Task[Any]] = set()


# background work ----------------------------------------------------------------------------


def _spawn(work: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
    """A tracked task: it does not depend on the request or socket that started it."""
    task = asyncio.get_running_loop().create_task(work, name=name)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def wait_idle(seconds: float = 120.0) -> None:
    """Wait until no report is being processed (tests, and a tidy shutdown)."""
    pending = [task for task in _tasks if not task.done()]
    if pending:
        await asyncio.wait(pending, timeout=seconds)


async def shutdown() -> None:
    pending = [task for task in _tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def recover_stale() -> int:
    """Startup: a report that has been `processing` or `live` for a long time lost its task
    when the server stopped. Mark it failed, so the board does not show it running forever."""
    cutoff = to_iso(datetime.now(UTC) - STALE_AFTER)
    count = 0
    for report in store.list_all(limit=200):
        if report.status in ("processing", "live") and report.updated_at < cutoff:
            pipeline.fail(report.id, "The server restarted before the report was finished.")
            count += 1
    return count


# report types -------------------------------------------------------------------------------


async def list_report_types() -> list[ReportType]:
    return types.list_types()


async def get_report_type(type_id: str) -> ReportType | None:
    return types.get_type(type_id)


async def create_report_type(requirement: str) -> ReportType:
    return await types.create_type(requirement)


async def delete_report_type(type_id: str) -> None:
    types.delete_type(type_id)


# reading and writing ------------------------------------------------------------------------


def _label(caller_label: str | None, channel: str) -> str:
    """A short label for where the report came from. Never a phone number: a label with a run
    of digits is replaced by the channel's own name."""
    label = " ".join((caller_label or "").split())[:MAX_LABEL_CHARS]
    digits = sum(1 for char in label if char.isdigit())
    if not label or digits >= 5:
        return DEFAULT_LABELS.get(channel, "Report")
    return label


def _new_report(
    rtype: ReportType | None,
    channel: str,
    caller_label: str,
    status: str,
    report_id: str | None = None,
) -> Report:
    if channel not in CHANNELS:
        raise AppError("invalid_channel", "Unknown channel.", 400)
    now = now_iso()
    data: dict[str, Any] = {
        "id": report_id or new_id("rep"),
        "created_at": now,
        "updated_at": now,
        "channel": channel,
        "caller_label": _label(caller_label, channel),
        "type_id": rtype.id if rtype else "",
        "type_title": rtype.title if rtype else store.PENDING_TYPE_TITLE,
        "status": status,
        "filled": 0,
        "total": 0,
        "urgency": None,
        "headline": "",
        "fields": store.empty_fields(rtype) if rtype else [],
        "missing": [],
        "transcript": [],
        "timeline": [],
        "files": [],
        "email": None,
        "x": store.type_keys(rtype) if rtype else {},
    }
    return store.create(data)


def _known_type(type_id: str | None) -> ReportType | None:
    if not type_id:
        return None
    rtype = types.get_type(type_id)
    if rtype is None:
        raise AppError("unknown_type", "There is no such report type.", 404)
    return rtype


async def create_report(
    type_id: str, channel: Channel, caller_label: str, status: ReportStatus = "processing"
) -> Report:
    rtype = _known_type(type_id)
    if rtype is None:
        raise AppError("unknown_type", "There is no such report type.", 404)
    return _new_report(rtype, channel, caller_label, status)


async def get_report(report_id: str) -> Report | None:
    return store.load(report_id)


async def list_reports(limit: int = 50) -> list[ReportSummary]:
    limit = max(1, min(int(limit), 200))
    return [report.summary() for report in store.list_all(limit=limit)]


async def set_fields(report_id: str, values: dict[str, str | None], source: str) -> Report:
    """Set field values by name. Names the type does not have are ignored. An `edit` (a person
    changed the report) is recorded on the timeline and the Word file and the draft follow."""
    if source not in SOURCES:
        raise AppError("invalid_source", "Unknown source.", 400)
    changed: list[str] = []

    def change(data: dict[str, Any]) -> None:
        if source == "edit" and data["status"] == "sent":
            raise AppError("already_sent", "The report has already been sent.", 409)
        for field in data["fields"]:
            if field["name"] not in values:
                continue
            raw = values[field["name"]]
            value = (str(raw).strip() or None) if raw is not None else None
            if value == field.get("value"):
                continue
            field.update({"value": value, "evidence": None, "source": source})
            changed.append(field["label"])
        if source == "edit" and changed:
            event = store.new_event(
                "edit", "Fields edited", detail=", ".join(changed), component="editor"
            )
            store.append_event(data, event)

    report = store.mutate(report_id, change)
    if source == "edit" and changed:
        report = await pipeline.rebuild_outputs(report_id)
    return report


async def add_transcript(report_id: str, role: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    if role not in ("agent", "caller"):
        raise AppError("invalid_role", "Unknown speaker.", 400)
    turn = {"role": role, "text": text, "at": now_iso()}
    store.mutate(report_id, lambda data: data.setdefault("transcript", []).append(turn))


async def add_event(
    report_id: str,
    kind: str,
    label: str,
    *,
    detail: str | None = None,
    component: str | None = None,
    status: str = "done",
    ms: int | None = None,
) -> TimelineEvent:
    event = store.new_event(kind, label, detail=detail, component=component, status=status, ms=ms)
    store.add_event(report_id, event)
    return event


async def update_event(report_id: str, event_id: str, **changes: Any) -> None:
    """Change a timeline event made with `add_event` (label, detail, status, ms), for a step
    that is shown as `running` first."""
    allowed = {key: value for key, value in changes.items() if key in TimelineEvent.model_fields}
    store.mutate(report_id, lambda data: store.change_event(data, event_id, **allowed))


async def finish_call(report_id: str) -> None:
    """The call is over: run steps 3 to 6 on its transcript as a tracked background task.
    Values the voice agent saved are kept; the extractor fills what is still empty."""
    report = store.load(report_id)
    if report is None:
        raise store.not_found()
    text = pipeline.transcript_text(report)
    lines = [f"{field.label}: {field.value}" for field in report.fields if field.value]
    if not text and not lines:
        pipeline.fail(report_id, "The call ended before anything was said.")
        return
    first = True

    def claim(data: dict[str, Any]) -> None:
        nonlocal first
        first = not data["x"].get("finishing") and data["status"] in ("live", "processing")
        if first:
            data["x"]["finishing"] = True
            data["status"] = "processing"

    store.mutate(report_id, claim)
    if not first:
        return  # the pipeline already runs or has run for this call
    _spawn(
        _guarded(
            report_id,
            pipeline.finish(
                report_id, text or "\n".join(lines), mode="keep_voice", run_extract=bool(text)
            ),
        ),
        f"report-{report_id}",
    )


async def _guarded(report_id: str, work: Coroutine[Any, Any, Any]) -> None:
    try:
        await work
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("report %s: background work stopped", report_id)
        try:
            pipeline.fail(report_id, "Something went wrong while the report was processed.")
        except AppError:
            pass


async def fail_report(report_id: str, detail: str) -> None:
    pipeline.fail(report_id, detail)


async def delete_report(report_id: str) -> None:
    raw = store.load_raw(report_id)
    if raw is None:
        raise store.not_found()
    scope = store.scope_of(report_id)
    store.delete(report_id)

    def remove_files() -> None:
        for stored in files.list_files(scope):
            files.delete_file(scope, stored.name)

    try:
        await llm.run_blocking(remove_files)
    except Exception as error:  # noqa: BLE001 - the record is gone; a leftover file is harmless
        logger.warning("report %s: files not removed (%s)", report_id, type(error).__name__)


# intake -------------------------------------------------------------------------------------


def reply_for(report: Report) -> str:
    """Two short sentences for the sender, built in code."""
    if report.status == "processing":
        return "Got it. The report is being prepared now."
    if report.status == "failed":
        return "Sorry, that could not be turned into a report. Please try again."
    first = (
        f"Saved as {report.type_title.lower()}: {report.filled} of {report.total} required "
        "fields are filled."
    )
    if report.missing:
        return f"{first} Still missing: {', '.join(report.missing)}."
    return f"{first} A person will check it before it is emailed."


async def _start(report: Report, wait: bool, **inputs: Any) -> tuple[Report, str]:
    task = _spawn(pipeline.process(report.id, **inputs), f"report-{report.id}")
    if wait:
        await asyncio.shield(task)
    current = store.load(report.id) or report
    return current, reply_for(current)


async def intake_text(
    text: str,
    *,
    type_id: str | None = None,
    channel: Channel = "text",
    caller_label: str = "Text",
    wait: bool = True,
) -> tuple[Report, str]:
    text = (text or "").strip()
    if not text:
        raise AppError("empty_text", "Write or say something first.", 400)
    if len(text) > MAX_TEXT_CHARS:
        raise AppError("too_large", "The text is too long.", 413)
    rtype = _known_type(type_id)
    report = _new_report(rtype, channel, caller_label, "processing")
    await add_transcript(report.id, "caller", text)
    return await _start(report, wait, text=text, pick_type=rtype is None)


async def intake_audio(
    path: Path,
    *,
    type_id: str | None = None,
    channel: Channel = "upload",
    caller_label: str = "Audio file",
    wait: bool = True,
    language: str = "auto",
) -> tuple[Report, str]:
    """`path` is an audio file (ogg, webm, mp3, wav or m4a by its content). It is copied into
    the report's own folder, so the caller may delete `path` as soon as this returns.
    `language` is `fi`, `en`, `sv` or `auto`."""
    rtype = _known_type(type_id)
    report_id = new_id("rep")
    stored = await files.save_upload(store.scope_of(report_id), Path(path), "audio")
    report = _new_report(rtype, channel, caller_label, "processing", report_id)
    entry = {
        "name": stored.name,
        "kind": "audio",
        "size": stored.size,
        "url": f"/api/reports/{report_id}/files/{stored.name}",
    }
    report = store.mutate(report_id, lambda data: data["files"].append(entry))
    return await _start(
        report,
        wait,
        audio=stored.path,
        audio_format=stored.format,
        pick_type=rtype is None,
        language=language,
    )


# approval and correction --------------------------------------------------------------------


def _release(report_id: str, failed: bool) -> None:
    def change(data: dict[str, Any]) -> None:
        data["x"].pop("approving", None)
        if failed and data.get("email"):
            data["email"]["status"] = "failed"
            store.append_event(
                data,
                store.new_event(
                    "error", "Email was not sent", detail="Try again in a moment.", status="error"
                ),
            )

    try:
        store.mutate(report_id, change)
    except AppError:
        pass


async def approve(report_id: str, to: str | None = None) -> Report:
    """A person approved the draft: send it once, to an allowlisted address.

    The report is claimed in one store update (`needs_approval` only, single use), so two
    approvals at the same moment send one mail; the second gets 409 `already_sent`.
    """
    settings = get_settings()
    recipient = (to or "").strip().lower() or settings.mail_default_recipient
    if not recipient:
        raise AppError("mail_no_recipient", "No mail recipient is configured.", 409)
    if recipient not in settings.mail_recipients:
        raise AppError("mail_not_allowed", "That address is not on the list of recipients.", 403)

    def claim(data: dict[str, Any]) -> None:
        if data["status"] != "needs_approval" or data["x"].get("approving"):
            raise AppError("already_sent", "The report has already been sent.", 409)
        data["x"]["approving"] = True

    report = store.mutate(report_id, claim)
    started = time.perf_counter()
    try:
        limits.reserve_mail()
        raw = store.load_raw(report_id) or {}
        name = raw.get("x", {}).get("docx")

        def deliver() -> None:
            attachment = None
            if name:
                with files.open_file(store.scope_of(report_id), name) as handle:
                    attachment = handle.read()
            download = f"{report.type_id or 'report'}-{report.created_at[:10]}.docx"
            message = mail.build_message(report, recipient, attachment, download)
            if settings.mail_enabled:
                mail.send_smtp(message)
            else:
                mail.write_eml(message, report_id)

        await llm.run_blocking(deliver)
    except AppError:
        _release(report_id, failed=False)
        raise
    except Exception as error:
        logger.error("report %s: mail failed (%s)", report_id, type(error).__name__)
        _release(report_id, failed=True)
        raise AppError("mail_failed", "The email could not be sent. Try again.", 502) from None
    ms = int((time.perf_counter() - started) * 1000)
    sent = settings.mail_enabled

    def finish(data: dict[str, Any]) -> None:
        data["x"].pop("approving", None)
        data["status"] = "sent"
        data["email"] = {
            **(data.get("email") or {}),
            "to": recipient,
            "subject": mail.subject_of(report),
            "body": mail.body_of(report),
            "status": "sent",
            "sent_at": now_iso(),
        }
        for event in data["timeline"]:
            if event["kind"] == "email" and event["status"] == "waiting":
                event["status"] = "done"
                event["label"] = "Email draft approved"
        store.append_event(
            data,
            store.new_event(
                "approve",
                "Approved and sent" if sent else "Approved",
                detail=f"To: {recipient}"
                if sent
                else f"Mail is off: the message to {recipient} was saved as a file",
                component="smtp" if sent else "outbox file",
                ms=ms,
            ),
        )

    return store.mutate(report_id, finish)


async def correct(report_id: str, text: str) -> Report:
    """A correction given in words: the extractor runs again on the transcript with the
    correction added; a value it does not find again is kept as it was."""
    text = (text or "").strip()
    if not text:
        raise AppError("empty_text", "Write or say the correction first.", 400)
    if len(text) > MAX_TEXT_CHARS:
        raise AppError("too_large", "The text is too long.", 413)
    report = store.load(report_id)
    if report is None:
        raise store.not_found()
    if report.status == "sent":
        raise AppError("already_sent", "The report has already been sent.", 409)
    if report.status in ("processing", "live"):
        raise AppError("not_ready", "The report is still being prepared.", 409)
    rtype = types.get_type(report.type_id)
    if rtype is None:
        raise AppError("unknown_type", "The report type no longer exists.", 409)

    source = pipeline.transcript_text(report)
    await add_transcript(report_id, "caller", f"Correction: {text}")
    combined = (
        f"{source}\n\nCORRECTION FROM THE REPORTER (it overrides anything above that it "
        f"contradicts):\n{text}"
    ).strip()

    async def work() -> Report:
        event = store.new_event(
            "edit", "Applying correction", component="extractor", status="running"
        )
        store.add_event(report_id, event)
        started = time.perf_counter()
        before = {field.name: field.value for field in report.fields}
        try:
            values = await gaik_ops.extract_fields(combined, rtype.fields, rtype.requirement)
        except Exception as error:
            code = error.code if isinstance(error, AppError) else type(error).__name__
            logger.warning("report %s: correction failed (%s)", report_id, code)
            store.mutate(
                report_id,
                lambda data: store.change_event(
                    data,
                    event.id,
                    status="error",
                    label="Correction failed",
                    detail="The fields are unchanged.",
                ),
            )
            raise AppError(
                "correction_failed", "The correction could not be applied. Try again.", 502
            ) from None
        ms = int((time.perf_counter() - started) * 1000)

        def change(data: dict[str, Any]) -> None:
            pipeline.merge(data, values, "overlay")
            changed = [
                f["label"] for f in data["fields"] if f.get("value") != before.get(f["name"])
            ]
            if data["status"] == "failed" and any(f.get("value") for f in data["fields"]):
                data["status"] = "needs_approval"
            store.change_event(
                data,
                event.id,
                status="done",
                label="Correction applied",
                detail=("Changed: " + ", ".join(changed)) if changed else "Nothing changed",
                ms=ms,
            )

        store.mutate(report_id, change)
        raw = store.load_raw(report_id) or {}
        if not raw.get("x", {}).get("docx"):
            await pipeline.document(report_id)
            await pipeline.email(report_id)
        return await pipeline.rebuild_outputs(report_id)

    return await asyncio.shield(_spawn(work(), f"correct-{report_id}"))
