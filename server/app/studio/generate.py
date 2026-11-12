"""`POST /api/studio/generate`: the user's words become a `StudioSpec`.

One structured chat call picks one of the four templates and writes the title, the summary,
the classes or the example questions. The draft is checked in code and repaired once; a
second invalid draft is refused. The pipeline itself comes from `catalog.py`, and the fields
of the table and report templates from gaik's SchemaGenerator (`gaik_ops.generate_fields`).
The model never writes steps or code.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core import gaik_ops, llm
from app.core.errors import AppError
from app.core.models import FieldDef
from app.core.settings import get_settings
from app.core.util import new_id
from app.studio import catalog
from app.studio.models import TEMPLATES, StudioClass, StudioSpec

logger = logging.getLogger(__name__)

STEP_DELAY_SECONDS = 0.35
MIN_CLASSES, MAX_CLASSES = 2, 8


class SpecDraft(BaseModel):
    """What the model fills in. Everything else in the spec is built in code."""

    template: Literal[
        "documents_to_table", "audio_to_report", "documents_to_answers", "documents_to_labels"
    ]
    title: str
    summary: str
    field_requirement: str
    classes: list[StudioClass] = Field(default_factory=list)
    question_examples: list[str] = Field(default_factory=list)


_SYSTEM = """You configure a small workflow builder. The user describes, in Finnish, English
or Swedish, what they want to automate. Pick exactly one template and fill the form.

Templates:
- documents_to_table: several documents of one kind (invoices, orders, forms, CVs) become a
  table, one row per document and one column per field.
- audio_to_report: one spoken recording (a dictated diary, an observation, a visit report)
  becomes a filled report and a Word file.
- documents_to_answers: questions are answered from documents (manuals, terms, contracts,
  instructions) with the page and the quote.
- documents_to_labels: short texts (emails, messages, tickets) each get one label from a list.

Write every text in English, whatever language the user wrote in:
- title: two to five words, sentence case, no full stop.
- summary: one sentence that says what goes in and what comes out.
- field_requirement: for documents_to_table and audio_to_report, one or two sentences that
  list the pieces of information to collect, concretely (for invoices: supplier, invoice
  number, invoice date, due date, total amount with VAT). Otherwise an empty string.
- classes: for documents_to_labels only, three to six labels (lowercase name of one to three
  words, and a description of when it applies). Otherwise an empty list.
- question_examples: for documents_to_answers only, three short questions a user could ask
  about such documents. Otherwise an empty list.
Do not add anything the user did not ask for."""

_ENGLISH_SYSTEM = """You prepare form fields for an English user interface. For every field
return the given name unchanged, a short English label in sentence case (one to four words,
no colon, no full stop) and one English sentence that says what the value means. Translate
choice options to English, in the same order. Return every field once, in the given order."""


class _EnglishField(BaseModel):
    name: str
    label: str
    description: str
    options: list[str] = Field(default_factory=list)


class _EnglishFields(BaseModel):
    fields: list[_EnglishField]


def _options() -> dict[str, Any]:
    """Sampling for the chat deployment, as `gaik_ops` chooses it."""
    return gaik_ops._chat_options(get_settings().azure_deployment)


def _clean(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def check_draft(draft: SpecDraft) -> list[str]:
    """What is wrong with a draft, in words the model can act on. Empty when it is fine."""
    problems: list[str] = []
    if draft.template not in TEMPLATES:
        problems.append("template must be one of the four template ids")
    if not _clean(draft.title, 120):
        problems.append("title is empty")
    if not _clean(draft.summary, 400):
        problems.append("summary is empty")
    if draft.template in ("documents_to_table", "audio_to_report"):
        if len(_clean(draft.field_requirement, 4000)) < 10:
            problems.append("field_requirement must list the information to collect")
    if draft.template == "documents_to_labels":
        names = [_clean(item.name, 60).casefold() for item in draft.classes]
        if not MIN_CLASSES <= len(names) <= MAX_CLASSES:
            problems.append(f"classes needs {MIN_CLASSES} to {MAX_CLASSES} labels")
        if len(set(names)) != len(names) or not all(names):
            problems.append("class names must be non-empty and different from each other")
    if draft.template == "documents_to_answers" and not any(
        _clean(question, 200) for question in draft.question_examples
    ):
        problems.append("question_examples needs at least one question")
    return problems


def _refused() -> AppError:
    return AppError(
        "spec_invalid",
        "That description did not turn into a workflow. Try describing it another way.",
        422,
    )


async def draft_spec(prompt: str) -> SpecDraft:
    """One call, checked in code, repaired once, then refused."""
    draft = await llm.structured_chat(_SYSTEM, prompt, SpecDraft, **_options())
    problems = check_draft(draft)
    if not problems:
        return draft
    logger.info("Studio draft needed a repair: %s", "; ".join(problems))
    repair = (
        f"{prompt}\n\nYour previous answer was refused: {'; '.join(problems)}. "
        "Answer again and fix exactly that."
    )
    draft = await llm.structured_chat(_SYSTEM, repair, SpecDraft, **_options())
    if check_draft(draft):
        raise _refused()
    return draft


async def english_copy(fields: list[FieldDef]) -> list[FieldDef]:
    """Labels, descriptions and options in English. The shared field generator writes its
    copy in Finnish; on any failure the fields are returned as they came."""
    if not fields:
        return fields
    listing = "\n".join(
        f"- name: {field.name}; label: {field.label}; description: {field.description}"
        + (f"; options: {' | '.join(field.options)}" if field.options else "")
        for field in fields
    )
    try:
        answer = await llm.structured_chat(_ENGLISH_SYSTEM, listing, _EnglishFields, **_options())
    except AppError:
        return fields
    by_name = {item.name: item for item in answer.fields}
    translated: list[FieldDef] = []
    for field in fields:
        item = by_name.get(field.name)
        if item is None or not item.label.strip():
            translated.append(field)
            continue
        update: dict[str, Any] = {
            "label": _clean(item.label, 80),
            "description": _clean(item.description, 400) or field.description,
        }
        if field.options and len(item.options) == len(field.options) and all(item.options):
            update["options"] = [_clean(option, 80) for option in item.options]
        translated.append(field.model_copy(update=update))
    return translated


def build_spec(prompt: str, draft: SpecDraft, fields: list[FieldDef]) -> StudioSpec:
    """The spec from a checked draft; steps, input and output come from the template."""
    template = draft.template
    labels = template == "documents_to_labels"
    answers = template == "documents_to_answers"
    return StudioSpec(
        id=new_id("stu"),
        title=_clean(draft.title, 120),
        summary=_clean(draft.summary, 400),
        requirement=prompt.strip()[:4000],
        template=template,
        input=catalog.input_for(template),
        steps=catalog.steps_for(template),
        fields=fields if template in ("documents_to_table", "audio_to_report") else [],
        classes=[
            StudioClass(name=_clean(item.name, 60), description=_clean(item.description, 300))
            for item in draft.classes
        ]
        if labels
        else [],
        question_examples=[
            _clean(question, 200) for question in draft.question_examples if _clean(question, 200)
        ][:5]
        if answers
        else [],
        output=catalog.output_for(template),
        notices=catalog.notices_for(template),
    )


async def generate(prompt: str) -> AsyncIterator[tuple[str, Any]]:
    """The events of `POST /api/studio/generate` (the stream adds `done`)."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise AppError("empty_prompt", "Describe what you want to automate.", 400)
    yield "status", {"text": "Reading your description"}
    draft = await draft_spec(prompt)
    template = draft.template
    yield "status", {"text": f"Picked: {catalog.TEMPLATE_INFO[template]['title']}"}

    fields_task: asyncio.Task[list[FieldDef]] | None = None
    if template in ("documents_to_table", "audio_to_report"):
        fields_task = asyncio.create_task(_fields(draft.field_requirement))
    try:
        for step in catalog.steps_for(template):
            await asyncio.sleep(STEP_DELAY_SECONDS)
            yield "step", {"step": step}
        fields: list[FieldDef] = []
        if fields_task is not None:
            yield "status", {"text": "Working out the fields"}
            fields = await fields_task
    finally:
        if fields_task is not None and not fields_task.done():
            fields_task.cancel()
    spec = build_spec(prompt, draft, fields)
    if template == "documents_to_labels":
        yield "classes", {"classes": spec.classes}
    elif fields:
        yield "fields", {"fields": spec.fields}
    yield "spec", {"spec": spec}


async def _fields(requirement: str) -> list[FieldDef]:
    fields = await gaik_ops.generate_fields(_clean(requirement, 4000))
    return await english_copy(fields)
