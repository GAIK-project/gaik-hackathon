"""A stored assessment as a Word document (python-docx)."""

from __future__ import annotations

import io

from docx import Document
from docx.shared import Pt

from app.aiact.schemas import Assessment, Citation

TIER_TITLES = {
    "prohibited": "Prohibited practice",
    "high": "High-risk",
    "transparency": "Transparency obligations",
    "minimal": "Minimal risk",
}
WHO = {"provider": "Provider", "deployer": "Deployer", "both": "Provider and deployer"}
REVIEW_NOTICE = "Have a person review this result before relying on it."
NOT_QUOTED = "Cited by number: amended by Regulation (EU) 2026/1744."
MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _date(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{int(day)}.{int(month)}.{year}"


def _citation(document, citation: Citation) -> None:
    paragraph = document.add_paragraph()
    paragraph.add_run(citation.label).bold = True
    if citation.quote:
        paragraph.add_run(f": “{citation.quote}”")
        if not citation.verified:
            paragraph.add_run(" (quote not verified)").italic = True
    else:
        paragraph.add_run(f". {NOT_QUOTED}").italic = True


def build_docx(assessment: Assessment) -> bytes:
    """The assessment as a .docx file."""
    document = Document()
    document.styles["Normal"].font.size = Pt(10.5)
    document.add_heading("AI Act assessment", level=0)
    document.add_paragraph(f"Created {assessment.created_at[:10]} · {assessment.disclaimer}")
    if assessment.needs_human_review:
        document.add_paragraph().add_run(REVIEW_NOTICE).bold = True

    document.add_heading("Likely risk tier", level=1)
    tier = document.add_paragraph()
    tier.add_run(TIER_TITLES[assessment.tier]).bold = True
    tier.add_run(f" (confidence {round(assessment.confidence * 100)} %)")
    if assessment.also_transparency:
        document.add_paragraph("Transparency obligations of Article 50 also apply.")

    document.add_heading("System description", level=1)
    document.add_paragraph(assessment.description)

    document.add_heading("Reasoning", level=1)
    document.add_paragraph(assessment.reasoning)
    if assessment.derogation_note:
        document.add_paragraph(assessment.derogation_note)

    document.add_heading("Tier probabilities", level=1)
    table = document.add_table(rows=1, cols=2)
    table.style = "Light Grid Accent 1"
    table.rows[0].cells[0].text = "Tier"
    table.rows[0].cells[1].text = "Probability"
    for name, title in TIER_TITLES.items():
        cells = table.add_row().cells
        cells[0].text = title
        cells[1].text = f"{round(assessment.probabilities.get(name, 0.0) * 100)} %"

    document.add_heading("Provisions the result rests on", level=1)
    if assessment.basis:
        for citation in assessment.basis:
            _citation(document, citation)
    else:
        document.add_paragraph("No provision of Article 5, Annex III or Article 50 matched.")

    document.add_heading("Obligations", level=1)
    if assessment.obligations:
        for obligation in assessment.obligations:
            head = document.add_paragraph()
            head.add_run(obligation.title).bold = True
            head.add_run(
                f" · {WHO[obligation.who]} · applies from {_date(obligation.applies_from)}"
            )
            document.add_paragraph(obligation.summary)
            _citation(document, obligation.citation)
    else:
        document.add_paragraph("No specific obligations follow from this tier.")

    document.add_heading("Source", level=1)
    document.add_paragraph(f"Regulation (EU) 2024/1689, {assessment.corpus_version}.")
    document.add_paragraph(assessment.disclaimer)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
