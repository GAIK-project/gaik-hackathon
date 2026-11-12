"""The Word file of a report, written with python-docx: title, date and channel, a two-column
table of labels and values, the transcript as an appendix and a footer line naming GAIK."""

from __future__ import annotations

import io
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

from app.core.models import Report

FOOTER = "Created with the GAIK toolkit · GAIK project, Haaga-Helia · synthetic demo content"
CHANNEL_NAMES = {
    "call": "Voice call",
    "whatsapp": "WhatsApp",
    "upload": "Audio upload",
    "text": "Text",
    "mcp": "Agent (MCP)",
}
URGENCY_NAMES = {"kiireellinen": "Urgent", "normaali": "Normal", "ei kiireellinen": "Not urgent"}
ROLE_NAMES = {"agent": "Agent", "caller": "Caller"}
EMPTY = "—"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_GREY = RGBColor(0x66, 0x66, 0x66)


def clean(text: str | None) -> str:
    """Text that is safe inside a Word document (no control characters)."""
    return _CONTROL.sub("", text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def when(iso: str) -> str:
    """`2026-11-12T10:03:44.120Z` as `2026-11-12 10:03 UTC`."""
    return f"{iso[:10]} {iso[11:16]} UTC" if len(iso) >= 16 else iso


def urgency_name(report: Report) -> str | None:
    return URGENCY_NAMES.get(report.urgency.label) if report.urgency else None


def build_docx(report: Report) -> bytes:
    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    document.add_heading(clean(report.type_title) or "Report", level=1)
    facts = [
        ("Date", when(report.created_at)),
        ("Channel", f"{CHANNEL_NAMES.get(report.channel, report.channel)}"),
    ]
    if name := urgency_name(report):
        facts.append(("Urgency", name))
    meta = document.add_paragraph()
    for index, (label, value) in enumerate(facts):
        if index:
            meta.add_run("   ·   ").font.color.rgb = _GREY
        meta.add_run(f"{label}: ").bold = True
        meta.add_run(clean(value))

    table = document.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for field in report.fields:
        cells = table.add_row().cells
        cells[0].width = Cm(5.5)
        cells[1].width = Cm(11)
        cells[0].paragraphs[0].add_run(clean(field.label)).bold = True
        cells[1].paragraphs[0].add_run(clean(field.value) or EMPTY)

    if report.missing:
        note = document.add_paragraph()
        run = note.add_run("Missing: " + ", ".join(clean(label) for label in report.missing))
        run.italic = True
        run.font.color.rgb = _GREY

    turns = [turn for turn in report.transcript if clean(turn.text)]
    if turns:
        document.add_heading("Appendix: transcript", level=2)
        only_caller = all(turn.role == "caller" for turn in turns)
        for turn in turns:
            paragraph = document.add_paragraph()
            if not only_caller:
                paragraph.add_run(f"{ROLE_NAMES.get(turn.role, turn.role)}: ").bold = True
            paragraph.add_run(clean(turn.text))

    footer = document.sections[0].footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run(FOOTER)
    run.font.size = Pt(8)
    run.font.color.rgb = _GREY

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
