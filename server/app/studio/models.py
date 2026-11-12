"""Wire types of the Studio (docs/ARCHITECTURE.md section 8.3). Interface text is English."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.models import FieldDef, FieldValue

StudioTemplate = Literal[
    "documents_to_table", "audio_to_report", "documents_to_answers", "documents_to_labels"
]
TEMPLATES: tuple[str, ...] = (
    "documents_to_table",
    "audio_to_report",
    "documents_to_answers",
    "documents_to_labels",
)

NOTICE_PERSONAL_DATA = "Do not share personal data."
NOTICE_UNITED_STATES = "The description is also processed in the United States."


class StudioStep(BaseModel):
    id: str = Field(max_length=40)
    component: str = Field(max_length=80)  # the gaik class (or the tool) behind the step
    title: str = Field(max_length=120)
    detail: str = Field(max_length=400)
    gaik_import: str = Field(max_length=200)  # "" when the step is not a gaik component


class StudioInput(BaseModel):
    kind: Literal["documents", "audio"]
    accept: list[str] = Field(max_length=10)
    multiple: bool
    hint: str = Field(max_length=300)
    sample_ids: list[str] = Field(max_length=5)


class StudioClass(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(max_length=300)


class StudioOutput(BaseModel):
    kind: Literal["table", "report", "answer", "labels"]


class StudioSpec(BaseModel):
    id: str = Field(max_length=40)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(max_length=400)
    requirement: str = Field(max_length=4000)  # the words of the user
    template: StudioTemplate
    input: StudioInput
    steps: list[StudioStep] = Field(max_length=12)
    fields: list[FieldDef] = Field(default_factory=list, max_length=40)
    classes: list[StudioClass] = Field(default_factory=list, max_length=12)
    question_examples: list[str] = Field(default_factory=list, max_length=8)
    output: StudioOutput
    notices: list[str] = Field(default_factory=list, max_length=4)  # fixed lines the page shows


class GenerateBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


class ExportBody(BaseModel):
    spec: StudioSpec


class ExportResult(BaseModel):
    python: str
    blueprint: dict[str, Any]


# results of a run ---------------------------------------------------------------------------


class TableColumn(BaseModel):
    name: str
    label: str


class TableRow(BaseModel):
    source: str
    values: dict[str, str | None]


class TableResult(BaseModel):
    kind: Literal["table"] = "table"
    columns: list[TableColumn]
    rows: list[TableRow]


class ResultFile(BaseModel):
    name: str
    url: str


class ReportResult(BaseModel):
    kind: Literal["report"] = "report"
    fields: list[FieldValue]
    transcript: str
    file: ResultFile | None


class AnswerSource(BaseModel):
    title: str
    page: int | None
    quote: str


class AnswerResult(BaseModel):
    kind: Literal["answer"] = "answer"
    question: str
    answer: str
    sources: list[AnswerSource]
    found: bool


class LabelRow(BaseModel):
    source: str
    label: str
    confidence: float
    probabilities: dict[str, float]


class LabelsResult(BaseModel):
    kind: Literal["labels"] = "labels"
    rows: list[LabelRow]
    notice: str = NOTICE_UNITED_STATES
