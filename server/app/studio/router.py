"""Studio routes (docs/ARCHITECTURE.md sections 8.3 and 15.9).

    GET  /api/studio/catalog                  the blocks and the four templates
    GET  /api/studio/samples                  the sample sets, each file with a URL
    GET  /api/studio/samples/{id}/{name}      one sample file
    POST /api/studio/generate                 SSE: status, step, fields | classes, spec, done
    POST /api/studio/run                      SSE: step, result, done   (multipart)
    POST /api/studio/export                   {python, blueprint}

`run` takes the form fields `spec` (JSON), `files` or `files[]` (uploads) or `sample_id`,
`question` for the answers template and `language` (`fi`, `en` or `sv`; default `fi`) for the
recording of the report template.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.core import files, limits
from app.core.errors import AppError
from app.core.events import sse_stream
from app.core.util import new_id
from app.studio import catalog, export, generate, samples
from app.studio import run as runner
from app.studio.models import ExportBody, ExportResult, GenerateBody, StudioSpec

logger = logging.getLogger(__name__)

router = APIRouter()

LANGUAGES = ("fi", "en", "sv")
MAX_SPEC_CHARS = 200_000


@router.get("/api/studio/catalog")
async def get_catalog() -> dict[str, Any]:
    return catalog.catalog()


@router.get("/api/studio/samples")
async def get_samples() -> list[dict[str, Any]]:
    return samples.list_samples()


@router.get("/api/studio/samples/{sample_id}/{name}")
async def get_sample_file(sample_id: str, name: str) -> FileResponse:
    return samples.sample_response(sample_id, name)


@router.post("/api/studio/generate", dependencies=[Depends(limits.model_call)])
async def post_generate(body: GenerateBody) -> StreamingResponse:
    return sse_stream(generate.generate(body.prompt))


@router.post("/api/studio/export")
async def post_export(body: ExportBody) -> ExportResult:
    return export.export(body.spec)


def _parse_spec(raw: Any) -> StudioSpec:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_SPEC_CHARS:
        raise AppError("invalid_spec", "The workflow is missing or not valid.", 422)
    try:
        return StudioSpec.model_validate_json(raw)
    except ValidationError as error:
        logger.info("Studio run refused a spec: %d problem(s)", error.error_count())
        raise AppError("invalid_spec", "The workflow is missing or not valid.", 422) from error


async def _inputs(
    spec: StudioSpec, uploads: list[UploadFile], sample_id: str | None, scope: str
) -> tuple[list[runner.RunInput], list[str]]:
    """The run's inputs and the names of the files stored for it (removed after the run)."""
    audio = spec.input.kind == "audio"
    if uploads:
        if audio:
            uploads = uploads[:1]
        else:
            limits.check_document_count(len(uploads))
        inputs: list[runner.RunInput] = []
        stored_names: list[str] = []
        try:
            for upload in uploads:
                stored = await files.save_upload(scope, upload, "audio" if audio else "document")
                stored_names.append(stored.name)
                label = files.safe_name(upload.filename, fallback="file") or stored.name
                inputs.append(runner.RunInput(label=label, path=stored.path, format=stored.format))
        except BaseException:
            for name in stored_names:
                files.delete_file(scope, name)
            raise
        return inputs, stored_names
    if sample_id:
        sample = samples.get_sample(sample_id)
        if (sample.kind == "audio") != audio:
            raise AppError("sample_mismatch", "That sample does not fit this workflow.", 400)
        paths = [path for path in sample.paths() if path.is_file()]
        if not paths:
            raise AppError("sample_not_found", "The sample was not found.", 404)
        return [runner.RunInput(label=path.name, path=path) for path in paths], []
    raise AppError("no_input", "Add a file or pick the sample first.", 400)


@router.post("/api/studio/run", dependencies=[Depends(limits.model_call)])
async def post_run(request: Request) -> StreamingResponse:
    form = await request.form()
    spec = _parse_spec(form.get("spec"))
    uploads = [
        item
        for item in [*form.getlist("files"), *form.getlist("files[]")]
        if isinstance(item, UploadFile)
    ]
    sample_id = form.get("sample_id")
    question = form.get("question")
    language = form.get("language")
    scope = f"studio/{new_id('run')}"
    # Uploads are stored before the stream starts: the form's files close with the request.
    inputs, stored_names = await _inputs(
        spec, uploads, sample_id if isinstance(sample_id, str) and sample_id else None, scope
    )

    async def events() -> AsyncIterator[tuple[str, Any]]:
        try:
            async for event in runner.run(
                spec,
                inputs,
                question=question if isinstance(question, str) else None,
                language=language if language in LANGUAGES else "fi",
                scope=scope,
            ):
                yield event
        finally:
            for name in stored_names:  # uploads are not kept after the run
                files.delete_file(scope, name)

    return sse_stream(events())
