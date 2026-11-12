"""One assessment: which tier a described AI system most likely falls under, and why.

Two methods answer independently and at the same time:

- Jev (`gaik_ops.classify_jev`) gives calibrated probabilities for the four tiers from four
  class descriptions. The description is cut to 2 000 characters and is processed in the
  United States (section 15.6).
- A structured chat call reads the candidate provisions (Article 5(1)(a) to (h), the Annex III
  points, Article 6(1), Article 50(1) to (4)) and picks the one that decides the tier, with
  words quoted from it.

Every quote is checked against the provision's text. The final tier is the one both methods
name; when they disagree the tier anchored in a verified quote wins and the result is flagged
for human review. It is also flagged when confidence is low, when a quote could not be
verified, or when the description may concern the two prohibitions added in 2026, which the
corpus does not contain.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from app.aiact import corpus, index, obligations
from app.aiact.schemas import TIERS, Assessment, Citation, Step, Tier
from app.core import gaik_ops, llm
from app.core.db import get_store
from app.core.errors import AppError
from app.core.models import Classification
from app.core.settings import get_settings
from app.core.util import new_id, now_iso

logger = logging.getLogger(__name__)

COLLECTION = "aiact_assessments"
MAX_DESCRIPTION_CHARS = 4000
MIN_DESCRIPTION_CHARS = 10
REVIEW_BELOW_CONFIDENCE = 0.6
CANDIDATE_CHARS = 900
FALLBACK_QUOTE_CHARS = 240

DISCLAIMER = (
    "This is a likely risk tier based on a short description. It is not legal advice and not "
    "a statement of compliance."
)

TIER_QUESTION = (
    "Which risk tier of the EU Artificial Intelligence Act (Regulation (EU) 2024/1689) does "
    "the described AI system fall under? Choose the most severe tier that applies."
)
# Written from Article 5(1), Annex III with Article 6, and Article 50. They describe the law,
# not the evaluation cases.
TIER_DESCRIPTIONS: dict[str, str] = {
    "prohibited": (
        "A practice banned by Article 5: manipulating or deceiving people with subliminal "
        "techniques so that they are harmed; exploiting the vulnerabilities of children, "
        "disabled or poor people; social scoring of people that leads to unfavourable "
        "treatment; predicting that a person will commit a crime from profiling or personality "
        "traits alone; building face recognition databases by untargeted scraping of images "
        "from the internet or CCTV; inferring the emotions of employees at work or of students "
        "at school; categorising people from biometric data to infer race, political opinions, "
        "religion or sexual orientation; real-time remote face recognition of people in public "
        "spaces by the police."
    ),
    "high": (
        "A high-risk use listed in Annex III, or AI that is a safety component of a regulated "
        "product (Article 6): identifying people from biometric data after the fact, biometric "
        "categorisation, or emotion recognition outside work and school; safety component of "
        "critical infrastructure such as water, gas, electricity, heating or road traffic; "
        "admission to education, grading students or monitoring exams; recruitment, screening "
        "job applicants, allocating tasks to workers, monitoring or evaluating workers; "
        "deciding eligibility for public benefits, credit scoring of private persons, risk "
        "assessment and pricing of life and health insurance, prioritising emergency calls; "
        "law enforcement, migration, asylum and border control; assisting judges; AI inside a "
        "medical device, machine, vehicle, lift or toy that needs third-party conformity "
        "assessment."
    ),
    "transparency": (
        "Not a prohibited or high-risk use, but a transparency duty of Article 50 applies: a "
        "chatbot, phone bot or companion app that converses directly with people; a system "
        "that generates synthetic text, images, audio, speech or video; deep fakes; "
        "AI-written text published to inform the public."
    ),
    "minimal": (
        "None of the other tiers: an ordinary AI application with no specific obligations. It "
        "works on things, content or internal processes, for example filtering spam, "
        "recommending products or music, forecasting demand, predicting machine maintenance, "
        "optimising routes, correcting spelling, extracting data from documents or steering "
        "characters in a game. It does not decide about people's access to jobs, education, "
        "credit, benefits or justice, does not converse with people as a chatbot and does not "
        "generate synthetic media."
    ),
}

_SYSTEM = """You apply the EU Artificial Intelligence Act (Regulation (EU) 2024/1689) to a short \
description of an AI system. The description may be in English, Finnish or Swedish. Work only \
from the candidate provisions given; do not rely on provisions that are not listed.

Decide the risk tier, taking the first that applies:
1. prohibited: the described use is one of the practices in Article 5(1)(a) to (h), with the \
conditions of that point met as described (for example, emotion recognition is prohibited only \
at work and in education; real-time remote biometric identification only in publicly \
accessible spaces for law enforcement).
2. high: the intended use is listed in Annex III, or the system is a safety component of, or \
itself, a product covered by Annex I legislation that needs third-party conformity assessment \
(Article 6(1), e.g. medical devices, machinery, vehicles, lifts, toys).
3. transparency: neither of the above, but Article 50(1) to (4) applies: the system converses \
directly with people, generates synthetic audio, image, video or text, is an emotion \
recognition or biometric categorisation system, or produces deep fakes or text published to \
inform the public.
4. minimal: none of the above.

Do not stretch a provision to a use it does not describe: predictive maintenance of factory \
machines is not a safety component of critical infrastructure, and a tool that only handles \
documents or goods does not decide about people.

Rules for the answer:
- deciding_unit_id is the id in square brackets of the one candidate that decides the tier. \
It is null only for the minimal tier.
- quote is up to 40 consecutive words copied character for character from that candidate's \
own text after the colon (not its label or heading): the words that match the described use, \
or the whole provision when it is short. No paraphrase, no ellipsis. Null for minimal.
- transparency_unit_id: when the tier is prohibited or high and an Article 50 paragraph also \
applies (the system talks with people, generates synthetic content, recognises emotions or \
categorises people biometrically), give that paragraph's id and a transparency_quote copied \
from it the same way. Otherwise both are null.
- derogation_plausible: true only when the tier is high under Annex III and the description \
suggests a narrow procedural or preparatory task without profiling (Article 6(3)).
- possible_2026_prohibition: true when the description concerns generating sexually explicit \
or intimate images of real people without their consent, or child sexual abuse material. \
Those two prohibitions were added to Article 5 in 2026 and are not among the candidates.
- reasoning: two or three plain English sentences for a non-lawyer: what the system does, \
which provision matches and why. Never say the system is compliant."""


class _Pick(BaseModel):
    tier: Tier
    deciding_unit_id: str | None = Field(description="Candidate id that decides the tier.")
    quote: str | None = Field(description="Words copied verbatim from the deciding provision.")
    transparency_unit_id: str | None
    transparency_quote: str | None
    derogation_plausible: bool
    possible_2026_prohibition: bool
    reasoning: str


OnStep = Callable[[Step], Any]

_STEPS: dict[str, tuple[str, str]] = {
    "retrieve": ("Searching the regulation", "gaik Embedder + numpy search"),
    "classify": ("Estimating the risk tier", "gaik JevClassifier (processed in the United States)"),
    "provision": ("Picking the deciding provision", "Azure OpenAI structured output"),
    "verify": ("Checking quotes against the text", "corpus quote check"),
    "obligations": ("Mapping obligations", "reviewed obligations table"),
}

_ERRORS = {
    "classification_failed": "The risk tier could not be estimated. Try again.",
    "model_failed": "The model did not answer. Try again in a moment.",
    "upstream_timeout": "The model did not answer in time. Try again.",
    "not_configured": "The model service is not configured.",
}


def english_error(error: AppError) -> AppError:
    """The same error with an English sentence (the core's messages are Finnish)."""
    message = _ERRORS.get(error.code, "The assessment did not succeed. Try again in a moment.")
    return AppError(error.code, message, error.status, headers=error.headers)


def unit_tier(unit_id: str | None) -> Tier | None:
    """The tier a candidate provision stands for."""
    if not unit_id:
        return None
    if unit_id.startswith("art-5-1-"):
        return "prohibited"
    if unit_id.startswith("annex-III-") or unit_id == "art-6-1":
        return "high"
    if unit_id.startswith("art-50-"):
        return "transparency"
    return None


@lru_cache(maxsize=1)
def candidate_ids() -> tuple[str, ...]:
    """Always offered to the chat model: every provision that can decide a tier."""
    ids = [chunk.unit_id for chunk in index.chunks()]
    return tuple(
        unit_id
        for unit_id in ids
        if unit_id.startswith(("art-5-1-", "annex-III-"))
        or unit_id in ("art-6-1", "art-50-1", "art-50-2", "art-50-3", "art-50-4")
    )


def _candidate_text(unit_id: str) -> str:
    unit = corpus.get(unit_id)
    assert unit is not None
    text = unit.text if len(unit.text) <= CANDIDATE_CHARS else unit.text[:CANDIDATE_CHARS] + " ..."
    area = f" ({unit.title})" if unit_id.startswith("annex-III-") and unit.title else ""
    return f"[{unit_id}] {unit.label}{area}: {text}"


def _user_prompt(description: str, similar: list[str]) -> str:
    listing = "\n\n".join(_candidate_text(unit_id) for unit_id in candidate_ids())
    hint = ", ".join(similar) if similar else "none"
    return (
        f'AI system description:\n"""\n{description}\n"""\n\n'
        f"Candidate provisions most similar to the description by search: {hint}\n\n"
        f"Candidate provisions:\n\n{listing}"
    )


async def _emit(on_step: OnStep | None, step: Step) -> None:
    if on_step is None:
        return
    try:
        result = on_step(step)
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001  (a listener must not break the assessment)
        logger.exception("AI Act step listener failed")


def _step(step_id: str, status: str, started: float | None = None, **extra: Any) -> Step:
    label, component = _STEPS[step_id]
    ms = None if started is None else int((time.perf_counter() - started) * 1000)
    return Step(id=step_id, label=label, status=status, ms=ms, component=component, **extra)


async def _classify(description: str, on_step: OnStep | None) -> tuple[Classification, bool]:
    """Tier probabilities from Jev; from the chat deployment (not calibrated) when Jev fails.
    Returns the classification and whether it is calibrated."""
    started = time.perf_counter()
    await _emit(on_step, _step("classify", "running"))
    calibrated = True
    try:
        result = await gaik_ops.classify_jev(description, TIER_DESCRIPTIONS, TIER_QUESTION)
    except AppError:
        logger.warning("Jev tier estimate failed; using the chat deployment instead")
        calibrated = False
        try:
            result = await gaik_ops.classify_azure(description, TIER_DESCRIPTIONS, TIER_QUESTION)
        except AppError:
            await _emit(on_step, _step("classify", "error", started))
            raise
    probabilities = {tier: round(float(result.probabilities.get(tier, 0.0)), 4) for tier in TIERS}
    await _emit(on_step, _step("classify", "done", started, probabilities=probabilities))
    return result, calibrated


async def _pick(description: str, on_step: OnStep | None) -> _Pick:
    started = time.perf_counter()
    await _emit(on_step, _step("retrieve", "running"))
    hits = await index.search(description, k=12)
    wanted = set(candidate_ids())
    similar = [hit.unit_id for hit in hits if hit.unit_id in wanted][:6]
    await _emit(on_step, _step("retrieve", "done", started))

    started = time.perf_counter()
    await _emit(on_step, _step("provision", "running"))
    options = gaik_ops._chat_options(get_settings().azure_deployment, "low")
    try:
        pick = await llm.structured_chat(
            _SYSTEM, _user_prompt(description, similar), _Pick, **options
        )
    except AppError:
        await _emit(on_step, _step("provision", "error", started))
        raise
    await _emit(on_step, _step("provision", "done", started))
    return pick


def _opening_words(text: str) -> str:
    first = text.split("\n", 1)[0]
    if len(first) <= FALLBACK_QUOTE_CHARS:
        return first
    return first[:FALLBACK_QUOTE_CHARS].rsplit(" ", 1)[0]


def _cite(unit_id: str, quote: str | None) -> tuple[Citation, bool]:
    """A citation of a candidate and whether the model's quote was found in it. When it was
    not, the citation carries the provision's own opening words instead of the model's."""
    unit = corpus.get(unit_id)
    assert unit is not None
    if quote and corpus.quote_in(unit.text, quote):
        return Citation(unit_id=unit_id, label=unit.label, quote=quote.strip(), verified=True), True
    fallback = _opening_words(unit.text)
    return Citation(unit_id=unit_id, label=unit.label, quote=fallback, verified=True), False


_ANNEX_III_RULE = (
    "AI systems referred to in Annex III shall be considered to be high-risk."  # Article 6(2)
)
DEROGATION_NOTE = (
    "A system listed in Annex III is not high-risk when it poses no significant risk of harm, "
    "for example because it only performs a narrow procedural or preparatory task "
    "(Article 6(3)). A system that profiles natural persons is always high-risk, and a provider "
    "who relies on the derogation must document the assessment (Article 6(4))."
)
NOTE_2026 = (
    "The description may concern one of the two prohibitions added to Article 5 by Regulation "
    "(EU) 2026/1744 (non-consensual intimate imagery, child sexual abuse material), applicable "
    "from 2.12.2026. This checker's text does not contain them: have a person check."
)
NOTE_QUOTE = (
    "The model's quote was not found in the provision, so the citation shows the provision's "
    "opening words instead."
)
NOTE_DISAGREE = "The two methods disagree ({jev} by the classifier, {chat} by the provision pick)."
NOTE_UNCALIBRATED = (
    "The calibrated classifier was unavailable; the probabilities are a chat model's estimate."
)


def decide(classification: Classification, calibrated: bool, pick: _Pick) -> dict[str, Any]:
    """Combine the two methods into the fields of an `Assessment` (no id, no description)."""
    known = set(candidate_ids())
    probabilities = {
        tier: round(float(classification.probabilities.get(tier, 0.0)), 4) for tier in TIERS
    }
    jev_tier: Tier = classification.label if classification.label in TIERS else "minimal"  # type: ignore[assignment]

    unit_id = pick.deciding_unit_id if pick.deciding_unit_id in known else None
    chat_tier: Tier = pick.tier
    # A pick is anchored when its provision stands for the tier it names.
    anchored = chat_tier == "minimal" or (unit_id is not None and unit_tier(unit_id) == chat_tier)
    basis: list[Citation] = []
    quote_ok = True
    if chat_tier != "minimal" and unit_id is not None and anchored:
        citation, quote_ok = _cite(unit_id, pick.quote)
        basis.append(citation)

    agree = jev_tier == chat_tier
    tier: Tier = chat_tier if (agree or anchored) else jev_tier
    if tier != chat_tier:
        basis, unit_id = [], None

    transparency_id = None
    transparency_ok = True
    if tier in ("prohibited", "high"):
        transparency_id = obligations.transparency_paragraph(
            pick.transparency_unit_id if pick.transparency_unit_id in known else None
        )
        if transparency_id:
            citation, transparency_ok = _cite(transparency_id, pick.transparency_quote)
            basis.append(citation)

    derogation_note = None
    if tier == "high" and (unit_id or "").startswith("annex-III-"):
        basis.append(obligations.citation_for("art-6-2", _ANNEX_III_RULE))
        derogation_note = DEROGATION_NOTE
        if pick.derogation_plausible:
            derogation_note += " From the description, the derogation may be worth examining."

    confidence = probabilities[tier]
    notes: list[str] = []
    if not agree:
        notes.append(NOTE_DISAGREE.format(jev=jev_tier, chat=chat_tier))
    if not (quote_ok and transparency_ok):
        notes.append(NOTE_QUOTE)
    if pick.possible_2026_prohibition:
        notes.append(NOTE_2026)
    if not calibrated:
        notes.append(NOTE_UNCALIBRATED)
    missing_basis = tier != "minimal" and not any(
        unit_tier(citation.unit_id) == tier for citation in basis
    )
    needs_review = bool(notes) or confidence < REVIEW_BELOW_CONFIDENCE or missing_basis

    return {
        "tier": tier,
        "probabilities": probabilities,
        "confidence": round(confidence, 4),
        "needs_human_review": needs_review,
        "reasoning": " ".join([pick.reasoning.strip(), *notes]).strip(),
        "basis": basis,
        "obligations": obligations.obligations_for(tier, unit_id, transparency_id),
        "also_transparency": transparency_id is not None,
        "derogation_note": derogation_note,
        "methods": {"classifier": jev_tier, "provision": chat_tier, "unit_id": unit_id},
    }


def check_description(description: str) -> str:
    description = (description or "").strip()
    if len(description) < MIN_DESCRIPTION_CHARS:
        raise AppError("empty_description", "Describe the AI system in a sentence or two.", 400)
    return description[:MAX_DESCRIPTION_CHARS]


async def run(
    description: str, on_step: OnStep | None = None, *, store: bool = True
) -> tuple[Assessment, dict[str, Any]]:
    """Assess a description. Returns the assessment and what each method said."""
    description = check_description(description)
    try:
        (classification, calibrated), pick = await asyncio.gather(
            _classify(description, on_step), _pick(description, on_step)
        )
    except AppError as error:
        raise english_error(error) from error

    started = time.perf_counter()
    await _emit(on_step, _step("verify", "running"))
    fields = decide(classification, calibrated, pick)
    methods = fields.pop("methods")
    await _emit(on_step, _step("verify", "done", started))
    await _emit(on_step, _step("obligations", "done", started))

    assessment = Assessment(
        id=new_id("aia"),
        created_at=now_iso(),
        description=description,
        corpus_version=corpus.CORPUS_VERSION,
        disclaimer=DISCLAIMER,
        **fields,
    )
    if store:
        get_store().put(COLLECTION, assessment.id, assessment)
    return assessment, methods


def load(assessment_id: str) -> Assessment | None:
    document = get_store().get(COLLECTION, assessment_id)
    return None if document is None else Assessment.model_validate(document.data)
