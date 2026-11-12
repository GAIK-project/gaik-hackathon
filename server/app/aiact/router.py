"""HTTP API of the AI Act checker (docs/ARCHITECTURE.md sections 8.4 and 15.8).

GET  /api/ai-act/examples            six sample systems
POST /api/ai-act/assess              SSE: step, partial {probabilities}, result {assessment}
GET  /api/ai-act/units/{unit_id}     a provision
GET  /api/ai-act/search?q=           provisions that match
POST /api/ai-act/report              {assessment_id} -> .docx of a stored assessment
GET  /api/ai-act/eval                the last evaluation run (data/ai-act/eval-results.json)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.aiact import corpus, report, service
from app.aiact.assess import MAX_DESCRIPTION_CHARS, check_description
from app.aiact.schemas import EvalResults, Example, SearchHit, Step, Unit
from app.core import limits
from app.core.errors import AppError
from app.core.events import sse_stream

router = APIRouter(tags=["ai-act"])

EVAL_FILE = corpus.DATA_DIR / "eval-results.json"

# Sample systems for the page. None of them is an evaluation case.
EXAMPLES: tuple[Example, ...] = (
    Example(
        id="video-interview",
        title="Video interview ranking",
        description="A tool analyses recorded video interviews of job applicants and ranks "
        "the applicants by predicted job performance.",
    ),
    Example(
        id="classroom-attention",
        title="Attention tracking in class",
        description="Cameras in a classroom infer from pupils' faces whether they are bored "
        "or attentive and report it to the teacher.",
    ),
    Example(
        id="housing-chat",
        title="Housing company chat assistant",
        description="A chat assistant on a housing company's website answers residents' "
        "questions about repairs and sauna bookings.",
    ),
    Example(
        id="essay-grading",
        title="Essay grading",
        description="A system grades the final essays of upper secondary school pupils, and "
        "the grade counts towards their certificate.",
    ),
    Example(
        id="weld-inspection",
        title="Weld seam inspection",
        description="A camera system on a production line spots faulty weld seams and flags "
        "the parts for rework.",
    ),
    Example(
        id="voice-adverts",
        title="Cloned voice for adverts",
        description="A service clones a voice actor's voice and generates radio adverts "
        "spoken in that voice.",
    ),
)


class AssessBody(BaseModel):
    description: str = Field(max_length=MAX_DESCRIPTION_CHARS)


class ReportBody(BaseModel):
    assessment_id: str = Field(min_length=1, max_length=80)


class SearchResponse(BaseModel):
    hits: list[SearchHit]


@router.get("/api/ai-act/examples")
async def examples() -> list[Example]:
    return list(EXAMPLES)


async def _assess_events(description: str) -> AsyncIterator[tuple[str, Any]]:
    queue: asyncio.Queue[Step | None] = asyncio.Queue()
    task = asyncio.create_task(service.assess(description, on_step=queue.put_nowait))
    task.add_done_callback(lambda _: queue.put_nowait(None))
    try:
        while (step := await queue.get()) is not None:
            yield "step", step.model_dump(exclude={"probabilities"})
            if step.probabilities is not None:
                yield "partial", {"probabilities": step.probabilities}
        assessment = await task  # raises the AppError of a failed model call
        yield "result", {"assessment": assessment}
    finally:
        if not task.done():
            task.cancel()


@router.post("/api/ai-act/assess", dependencies=[Depends(limits.model_call)])
async def assess(body: AssessBody) -> StreamingResponse:
    return sse_stream(_assess_events(check_description(body.description)))


@router.get("/api/ai-act/units/{unit_id}")
async def unit(unit_id: str) -> Unit:
    found = await service.get_unit(unit_id)
    if found is None:
        raise AppError("not_found", "That provision was not found.", 404)
    return found


@router.get("/api/ai-act/search", dependencies=[Depends(limits.model_call)])
async def search(q: str = Query(min_length=2, max_length=500)) -> SearchResponse:
    return SearchResponse(hits=await service.search(q))


@router.post("/api/ai-act/report")
async def assessment_report(body: ReportBody) -> Response:
    assessment = service.get_assessment(body.assessment_id)
    if assessment is None:
        raise AppError("not_found", "That assessment was not found.", 404)
    data = await asyncio.to_thread(report.build_docx, assessment)
    return Response(
        data,
        media_type=report.MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="ai-act-assessment-{assessment.id}.docx"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/ai-act/eval")
async def evaluation() -> EvalResults:
    if not EVAL_FILE.is_file():
        raise AppError("not_found", "The evaluation has not been run yet.", 404)
    return EvalResults.model_validate(json.loads(EVAL_FILE.read_text(encoding="utf-8")))
