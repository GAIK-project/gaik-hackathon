"""What the AI Act checker offers to the rest of the app (section 15.8). The SSE endpoint and
the MCP tools call these three functions.

    assessment = await assess("A system scores job applications ...")
    assessment = await assess(description, on_step=listener)   # listener(step: Step), sync or async
    hits = await search("emotion recognition at work", k=8)
    unit = await get_unit("annex-III-4-a")                     # also "art-5-1-c", "art-50-2"

`assess` stores the assessment (`get_assessment(id)` reads it back) and raises `AppError` with
an English sentence when a model call fails. The description is also processed in the United
States (Jev), cut to 2 000 characters: do not pass personal data.
"""

from __future__ import annotations

from app.aiact import assess as _assess
from app.aiact import corpus, index
from app.aiact.assess import OnStep
from app.aiact.schemas import Assessment, Citation, Obligation, SearchHit, Step, Tier, Unit
from app.core.errors import AppError

__all__ = [
    "Assessment",
    "Citation",
    "Obligation",
    "SearchHit",
    "Step",
    "Tier",
    "Unit",
    "assess",
    "get_assessment",
    "get_unit",
    "search",
]

AMENDED_TITLE_NOTE = "amended by Regulation (EU) 2026/1744; the text shown is that of 12.7.2024"


async def assess(description: str, on_step: OnStep | None = None) -> Assessment:
    """The likely risk tier of a described AI system, with the deciding provision quoted and
    the obligations that follow. `on_step` is called with a `Step` as each stage starts and
    ends; the `classify` step carries the tier probabilities when it is done."""
    assessment, _ = await _assess.run(description, on_step)
    return assessment


async def search(q: str, k: int = 8) -> list[SearchHit]:
    """Provisions that match a question or a description, best first."""
    try:
        return await index.search(q, k)
    except AppError as error:
        raise _assess.english_error(error) from error


async def get_unit(unit_id: str) -> Unit | None:
    """A recital, article, annex, Annex III point, or a paragraph or point of an article."""
    unit = corpus.get((unit_id or "").strip())
    if unit is None:
        return None
    if unit.kind != "recital" and not corpus.may_quote(unit.id) and _amended(unit.id):
        title = f"{unit.title} ({AMENDED_TITLE_NOTE})" if unit.title else AMENDED_TITLE_NOTE
        return unit.model_copy(update={"title": title})
    return unit


def _amended(unit_id: str) -> bool:
    if unit_id.startswith("art-"):
        return int(unit_id.split("-")[1]) in corpus.AMENDED_ARTICLES
    return unit_id in ("annex-I", "annex-VIII")


def get_assessment(assessment_id: str) -> Assessment | None:
    return _assess.load(assessment_id)
