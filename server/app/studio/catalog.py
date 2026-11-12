"""The Studio's block catalogue and its four templates (docs/ARCHITECTURE.md section 8.3).

Every template is a fixed pipeline: the model picks a template and fills the words, it never
invents steps. `steps_for(template)` is the pipeline `run.py` executes, step id by step id.
"""

from __future__ import annotations

from typing import Any

from app.studio.models import (
    NOTICE_PERSONAL_DATA,
    NOTICE_UNITED_STATES,
    StudioInput,
    StudioOutput,
    StudioStep,
)

_EXTRACTOR = "from gaik.software_components.extractor import "

COMPONENTS: list[dict[str, str]] = [
    {
        "id": "pymupdf_parser",
        "title": "PyMuPDFParser",
        "purpose": "Reads the text layer of a PDF page by page, locally, without a model call.",
        "gaik_import": "from gaik.software_components.parsers import PyMuPDFParser",
        "extra": "parser-cpu",
    },
    {
        "id": "docx_parser",
        "title": "DocxParser",
        "purpose": "Reads a Word file into text with its headings and tables, locally.",
        "gaik_import": "from gaik.software_components.parsers import DocxParser",
        "extra": "parser-cpu",
    },
    {
        "id": "schema_generator",
        "title": "SchemaGenerator",
        "purpose": "Turns a requirement written in plain words into typed fields.",
        "gaik_import": _EXTRACTOR + "SchemaGenerator",
        "extra": "extract",
    },
    {
        "id": "data_extractor",
        "title": "DataExtractor",
        "purpose": "Fills the fields from a text against the schema, one record per text.",
        "gaik_import": _EXTRACTOR + "DataExtractor",
        "extra": "extract",
    },
    {
        "id": "source_tracing",
        "title": "SourceTracing",
        "purpose": "Adds a word-for-word quote to every value and checks it in code.",
        "gaik_import": "from gaik.software_components.source_tracing import SourceTracing",
        "extra": "source-tracing",
    },
    {
        "id": "transcriber",
        "title": "Transcriber",
        "purpose": "Speech to text on the transcription deployment.",
        "gaik_import": "from gaik.software_components.transcriber import Transcriber",
        "extra": "transcriber",
    },
    {
        "id": "embedder",
        "title": "Embedder",
        "purpose": "Turns pieces of text into vectors that can be compared.",
        "gaik_import": "from gaik.software_components.RAG.embedder import Embedder",
        "extra": "embedder",
    },
    {
        "id": "jev_classifier",
        "title": "JevClassifier",
        "purpose": "Picks a label with the Jev decision model: a probability for every label.",
        "gaik_import": "from gaik.software_components.jev_classifier import JevClassifier",
        "extra": "jev-classifier",
    },
    {
        "id": "llm",
        "title": "LLM config",
        "purpose": "One structured chat call on the Azure OpenAI deployment gaik is set up for.",
        "gaik_import": "from gaik.software_components.llm import get_llm_config",
        "extra": "",
    },
]
_BY_ID = {item["id"]: item for item in COMPONENTS}


def _step(step_id: str, component_id: str, title: str, detail: str) -> StudioStep:
    block = _BY_ID.get(component_id)
    return StudioStep(
        id=step_id,
        component=block["title"] if block else component_id,
        title=title,
        detail=detail,
        gaik_import=block["gaik_import"] if block else "",
    )


_DOCUMENT_TYPES = [".pdf", ".docx", ".txt"]
_AUDIO_TYPES = [".mp3", ".wav", ".m4a", ".ogg", ".webm"]

TEMPLATE_INFO: dict[str, dict[str, Any]] = {
    "documents_to_table": {
        "title": "Documents to a table",
        "about": "Many documents of one kind (invoices, orders, forms) become one table: "
        "a row per document, a column per field.",
        "steps": [
            _step(
                "parse",
                "pymupdf_parser",
                "Read the documents",
                "The text of every PDF, Word or text file is read locally.",
            ),
            _step(
                "extract",
                "data_extractor",
                "Pick the fields",
                "The fields are filled from each document, one row per document.",
            ),
        ],
        "input": StudioInput(
            kind="documents",
            accept=_DOCUMENT_TYPES,
            multiple=True,
            hint="Add up to 10 PDF, Word or text files.",
            sample_ids=["invoices"],
        ),
        "output": "table",
        "notices": [NOTICE_PERSONAL_DATA],
    },
    "audio_to_report": {
        "title": "A recording to a report",
        "about": "One spoken recording (a dictated diary, an observation, a visit) becomes a "
        "filled report and a Word file.",
        "steps": [
            _step(
                "convert",
                "ffmpeg",
                "Prepare the recording",
                "The recording is converted to 16 kHz mono sound.",
            ),
            _step(
                "transcribe",
                "transcriber",
                "Turn speech into text",
                "The recording is written out as text.",
            ),
            _step(
                "extract",
                "data_extractor",
                "Fill the report",
                "The fields are filled from the transcript, each with the words it rests on.",
            ),
            _step(
                "document",
                "python-docx",
                "Write the Word file",
                "The fields and the transcript go into a Word file.",
            ),
        ],
        "input": StudioInput(
            kind="audio",
            accept=_AUDIO_TYPES,
            multiple=False,
            hint="Add one recording, at most 10 minutes.",
            sample_ids=["site-diary"],
        ),
        "output": "report",
        "notices": [NOTICE_PERSONAL_DATA],
    },
    "documents_to_answers": {
        "title": "Questions to documents",
        "about": "Questions are answered from the documents (manuals, terms, instructions), "
        "with the page and the quote the answer rests on.",
        "steps": [
            _step(
                "parse",
                "pymupdf_parser",
                "Read the documents",
                "The text is read page by page, locally.",
            ),
            _step(
                "embed",
                "embedder",
                "Find the right pages",
                "Pages and the question become vectors; the closest pages are picked.",
            ),
            _step(
                "answer",
                "llm",
                "Answer from the pages",
                "One answer from the picked pages; every quote is checked against the page.",
            ),
        ],
        "input": StudioInput(
            kind="documents",
            accept=_DOCUMENT_TYPES,
            multiple=True,
            hint="Add the documents the answers should come from.",
            sample_ids=["manual"],
        ),
        "output": "answer",
        "notices": [NOTICE_PERSONAL_DATA],
    },
    "documents_to_labels": {
        "title": "Documents to labels",
        "about": "Each short text (an email, a message, a ticket) gets one label from a list "
        "the user gives, with a probability for every label.",
        "steps": [
            _step("parse", "pymupdf_parser", "Read the texts", "The text of every file is read."),
            _step(
                "classify",
                "jev_classifier",
                "Pick a label",
                "Jev gives every label a probability. The first 2 000 characters are sent.",
            ),
        ],
        "input": StudioInput(
            kind="documents",
            accept=_DOCUMENT_TYPES,
            multiple=True,
            hint="Add up to 10 short texts.",
            sample_ids=["emails"],
        ),
        "output": "labels",
        "notices": [NOTICE_PERSONAL_DATA, NOTICE_UNITED_STATES],
    },
}


def steps_for(template: str) -> list[StudioStep]:
    return [step.model_copy() for step in TEMPLATE_INFO[template]["steps"]]


def input_for(template: str) -> StudioInput:
    return TEMPLATE_INFO[template]["input"].model_copy(deep=True)


def output_for(template: str) -> StudioOutput:
    return StudioOutput(kind=TEMPLATE_INFO[template]["output"])


def notices_for(template: str) -> list[str]:
    return list(TEMPLATE_INFO[template]["notices"])


def catalog() -> dict[str, Any]:
    """The body of `GET /api/studio/catalog`."""
    return {
        "components": [dict(item) for item in COMPONENTS],
        "templates": [
            {
                "id": template,
                "title": info["title"],
                "steps": [step.id for step in info["steps"]],
            }
            for template, info in TEMPLATE_INFO.items()
        ],
    }
