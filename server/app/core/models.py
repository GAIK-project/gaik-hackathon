"""Shared wire types (docs/ARCHITECTURE.md section 7) and the error envelope (section 4).

These mirror `web/lib/types.ts` one to one. Feature owners add their own types in
`app/<feature>/models.py`; they do not change the ones here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, SerializationInfo, model_serializer

Channel = Literal["call", "whatsapp", "upload", "text", "mcp"]
ReportStatus = Literal["live", "processing", "needs_approval", "sent", "failed"]
FieldType = Literal["text", "longtext", "date", "time", "number", "choice"]
FieldSource = Literal["voice", "extractor", "edit"]
TimelineKind = Literal[
    "call",
    "transcribe",
    "classify",
    "extract",
    "urgency",
    "document",
    "email",
    "approve",
    "edit",
    "error",
]
TimelineStatus = Literal["running", "done", "waiting", "error"]
UrgencyLabel = Literal["kiireellinen", "normaali", "ei kiireellinen"]


class FieldDef(BaseModel):
    name: str  # snake_case key
    label: str  # Finnish label
    type: FieldType
    required: bool
    description: str  # what the value means; goes to the extractor and the voice agent
    options: list[str] | None = None  # for "choice"; left out of JSON when absent

    @model_serializer(mode="wrap")
    def _omit_absent_options(self, handler: Any, info: SerializationInfo) -> dict[str, Any]:
        data = handler(self)
        if data.get("options") is None:
            data.pop("options", None)
        return data


class ReportType(BaseModel):
    id: str  # "turvallisuushavainto" | "tyomaapaivakirja" | "huoltoraportti" | user-made
    title: str
    description: str
    icon: str  # lucide icon name
    requirement: str  # the plain-language requirement the fields were generated from
    fields: list[FieldDef]
    builtin: bool


class FieldValue(BaseModel):
    name: str
    label: str
    value: str | None
    evidence: str | None  # the words in the transcript the value rests on, when available
    source: FieldSource


class TimelineEvent(BaseModel):
    id: str
    at: str
    kind: TimelineKind
    label: str  # "Kentät poimittu 11/12"
    detail: str | None  # "Puuttuu: sääolosuhteet"
    component: str | None  # gaik component or service behind the step, e.g. "extractor"
    status: TimelineStatus
    ms: int | None


class Urgency(BaseModel):
    label: UrgencyLabel
    confidence: float  # 0..1
    probabilities: dict[str, float]
    model: str


class TranscriptTurn(BaseModel):
    role: Literal["agent", "caller"]
    text: str
    at: str


class ReportFile(BaseModel):
    name: str
    kind: Literal["docx", "audio"]
    size: int
    url: str


class ReportEmail(BaseModel):
    to: str | None
    subject: str
    body: str
    status: Literal["draft", "sent", "failed"]
    sent_at: str | None


class ReportSummary(BaseModel):
    id: str
    created_at: str
    updated_at: str
    channel: Channel
    caller_label: str  # "Selainpuhelu", "WhatsApp", never a phone number
    type_id: str
    type_title: str
    status: ReportStatus
    filled: int
    total: int  # required fields
    urgency: Urgency | None
    headline: str  # one line built from the most telling field


class Report(ReportSummary):
    fields: list[FieldValue]
    missing: list[str]  # labels of required fields still empty
    transcript: list[TranscriptTurn]
    timeline: list[TimelineEvent]
    files: list[ReportFile]
    email: ReportEmail | None

    def summary(self) -> ReportSummary:
        """The summary part of this report, for list views and the board stream."""
        return ReportSummary.model_validate(
            {name: getattr(self, name) for name in ReportSummary.model_fields}
        )


# Results of the shared gaik operations (`core/gaik_ops.py`, section 15.5) --------------------

DocumentKind = Literal["pdf", "docx", "txt"]
# How far a value can be trusted on its evidence quote (gaik SourceTracing):
# verified = the quote is in the text and holds the value; reworded = the quote is in the text
# but the value is the model's own wording; check = the quote is missing from the text or
# holds another number or date; unchecked = a value without a quote that could be compared.
EvidenceVerdict = Literal["verified", "check", "reworded", "unchecked"]


class ParsedDocument(BaseModel):
    text: str  # the whole document
    pages: list[str]  # one entry per PDF page ("" for an empty page); one entry for DOCX and TXT
    kind: DocumentKind
    ms: int = 0  # how long the operation took


class Transcript(BaseModel):
    text: str
    duration_s: float | None  # length of the audio in seconds; None when it could not be read
    language: str  # the language that was asked for, e.g. "fi"
    model: str  # the transcription deployment
    ms: int = 0


class Classification(BaseModel):
    label: str
    confidence: float  # 0..1
    probabilities: dict[str, float]  # one entry per label
    model: str
    ms: int = 0


class TracedFieldValue(FieldValue):
    """A `FieldValue` from the extractor with the verdict of the evidence check. It is a
    `FieldValue` wherever one is expected; the verdict is dropped when a `Report` is stored."""

    verdict: EvidenceVerdict | None = None  # None: no value, or tracing was not asked for


class ErrorBody(BaseModel):
    code: str  # snake_case
    message: str  # a Finnish sentence for the user


class ErrorEnvelope(BaseModel):
    error: ErrorBody
