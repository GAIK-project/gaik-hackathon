"""HTTP routes of the reports feature (docs/ARCHITECTURE.md sections 8.1 and 15.7), full paths.

Access is decided by `core/auth` before a route runs: the session cookie everywhere, the
bridge key on intake, approve, correct and the read routes of one report.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.core import files, gaik_ops, lifecycle, limits
from app.core.errors import AppError
from app.core.events import get_bus, sse_response
from app.core.models import Channel, Report, ReportSummary, ReportType
from app.core.settings import get_settings
from app.core.util import new_id
from app.reports import service, store

router = APIRouter()

lifecycle.register("reports", startup=service.recover_stale, shutdown=service.shutdown)

LANGUAGES = ("auto", "fi", "en", "sv")


class RequirementBody(BaseModel):
    requirement: str


class FieldsBody(BaseModel):
    values: dict[str, str | None]


class ApproveBody(BaseModel):
    to: str | None = None


class TextBody(BaseModel):
    text: str
    type_id: str | None = None
    channel: Channel = "text"
    caller_label: str | None = None


class IntakeResult(BaseModel):
    report: Report
    reply: str


class Recipients(BaseModel):
    recipients: list[str]
    default: str | None
    enabled: bool


async def _report(report_id: str) -> Report:
    report = await service.get_report(report_id)
    if report is None:
        raise store.not_found()
    return report


# report types -------------------------------------------------------------------------------


@router.get("/api/report-types")
async def list_report_types() -> list[ReportType]:
    return await service.list_report_types()


@router.post("/api/report-types", dependencies=[Depends(limits.model_call)])
async def create_report_type(body: RequirementBody) -> ReportType:
    return await service.create_report_type(body.requirement)


@router.delete("/api/report-types/{type_id}")
async def delete_report_type(type_id: str) -> dict[str, bool]:
    await service.delete_report_type(type_id)
    return {"deleted": True}


# reports ------------------------------------------------------------------------------------


@router.get("/api/reports")
async def list_reports(limit: int = Query(50, ge=1, le=200)) -> list[ReportSummary]:
    return await service.list_reports(limit)


@router.get("/api/reports/stream")
async def stream_reports() -> StreamingResponse:
    """`report` with a `ReportSummary` on every create or change, `deleted` with `{"id"}`."""
    return sse_response(get_bus().subscribe(store.TOPIC))


@router.get("/api/reports/{report_id}")
async def get_report(report_id: str) -> Report:
    return await _report(report_id)


@router.get("/api/reports/{report_id}/stream")
async def stream_report(report_id: str) -> StreamingResponse:
    """The report as it is now, then `report` with the full `Report` on every change."""
    subscription = get_bus().subscribe(store.topic_of(report_id))  # before reading the state
    try:
        report = await _report(report_id)
    except AppError:
        subscription.close()
        raise
    return sse_response(subscription, initial=[("report", report)])


@router.patch("/api/reports/{report_id}/fields")
async def edit_fields(report_id: str, body: FieldsBody) -> Report:
    return await service.set_fields(report_id, body.values, "edit")


@router.post("/api/reports/{report_id}/approve")
async def approve_report(report_id: str, body: ApproveBody | None = None) -> Report:
    return await service.approve(report_id, body.to if body else None)


@router.post("/api/reports/{report_id}/correct", dependencies=[Depends(limits.model_call)])
async def correct_report(report_id: str, request: Request) -> Report:
    """`{"text": ...}` as JSON, or multipart with `file` (a spoken correction)."""
    await _report(report_id)
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        typed = form.get("text")
        if upload is not None and not isinstance(upload, str):
            language = str(form.get("language") or "auto")
            text = await _spoken(upload, language if language in LANGUAGES else "auto")
        elif isinstance(typed, str):
            text = typed
        else:
            raise AppError("empty_text", "Write or say the correction first.", 400)
    else:
        try:
            payload: Any = await request.json()
        except ValueError:
            raise AppError("invalid_request", "The request is not valid.", 422) from None
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise AppError("invalid_request", "The request is not valid.", 422)
    return await service.correct(report_id, text)


async def _spoken(upload: UploadFile, language: str) -> str:
    """Words of an uploaded recording; the audio itself is not kept."""
    scope = f"reports/inbox/{new_id('in')}"
    stored = await files.save_upload(scope, upload, "audio")
    wav = None
    try:
        wav = await files.to_wav(stored.path, stored.format)
        transcript = await gaik_ops.transcribe_audio(wav.path, language)
        return transcript.text
    finally:
        if wav is not None:
            wav.path.unlink(missing_ok=True)
        files.delete_file(scope, stored.name)


@router.delete("/api/reports/{report_id}")
async def delete_report(report_id: str) -> dict[str, bool]:
    await service.delete_report(report_id)
    return {"deleted": True}


@router.get("/api/reports/{report_id}/files/{name}")
async def get_report_file(report_id: str, name: str) -> FileResponse:
    """A file of the report. The name is looked up in the report, never joined into a path."""
    report = await _report(report_id)
    entry = next((item for item in report.files if item.name == name), None)
    if entry is None:
        raise AppError("file_not_found", "There is no such file.", 404)
    download = None
    if entry.kind == "docx":
        download = f"{report.type_id or 'report'}-{report.created_at[:10]}.docx"
    return files.file_response(store.scope_of(report_id), entry.name, download_name=download)


# mail ---------------------------------------------------------------------------------------


@router.get("/api/mail/recipients")
async def mail_recipients() -> Recipients:
    settings = get_settings()
    return Recipients(
        recipients=settings.mail_recipients,
        default=settings.mail_default_recipient,
        enabled=settings.mail_enabled,
    )


# intake -------------------------------------------------------------------------------------


@router.post("/api/intake/text", dependencies=[Depends(limits.model_call)])
async def intake_text(body: TextBody, wait: bool = True) -> IntakeResult:
    report, reply = await service.intake_text(
        body.text,
        type_id=body.type_id or None,
        channel=body.channel,
        caller_label=body.caller_label or service.DEFAULT_LABELS[body.channel],
        wait=wait,
    )
    return IntakeResult(report=report, reply=reply)


@router.post("/api/intake/audio", dependencies=[Depends(limits.model_call)])
async def intake_audio(
    file: Annotated[UploadFile, File()],
    type_id: Annotated[str | None, Form()] = None,
    channel: Annotated[str, Form()] = "upload",
    caller_label: Annotated[str | None, Form()] = None,
    language: Annotated[str, Form()] = "auto",
    wait: bool = True,
) -> IntakeResult:
    if channel not in service.CHANNELS:
        raise AppError("invalid_channel", "Unknown channel.", 400)
    scope = f"reports/inbox/{new_id('in')}"
    stored = await files.save_upload(scope, file, "audio")
    try:
        report, reply = await service.intake_audio(
            stored.path,
            type_id=type_id or None,
            channel=channel,  # type: ignore[arg-type]
            caller_label=caller_label or service.DEFAULT_LABELS[channel],
            wait=wait,
            language=language if language in LANGUAGES else "auto",
        )
    finally:
        files.delete_file(scope, stored.name)
    return IntakeResult(report=report, reply=reply)
