"""Live smoke tests: real transcription, classification and extraction on synthetic input.

uv run pytest tests/reports/test_live.py -m live -s
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from docx import Document

from app.core import files
from app.core.errors import AppError
from app.reports import mail, service, store

pytestmark = pytest.mark.live

SAMPLE = Path(
    os.environ.get("REPORTS_LIVE_AUDIO", "")
    or Path(__file__).resolve().parents[4]
    / "gaik-toolkit/implementation_layer/no-code-assets/samples"
    / "construction-diary-creation/fin-example-1.mp3"
)
OBSERVATION = (
    "Safety observation from the Harbour Street site this morning: on the third floor a "
    "guard rail was missing next to the lift shaft opening and a worker nearly stepped "
    "into it. I put up a temporary barrier and tape. The rail has to be fixed today."
)


def show(report) -> None:
    print(f"\n{report.type_title} [{report.status}] {report.filled}/{report.total}")
    print(f"  headline: {report.headline}")
    for event in report.timeline:
        print(f"  {event.kind:<10} {event.status:<8} {event.ms} ms  {event.label} | {event.detail}")
    for field in report.fields:
        print(f"  {field.name}: {field.value!r}  <- {field.evidence!r}")
    if report.urgency:
        print(f"  urgency: {report.urgency.label} {report.urgency.confidence:.2f}")


@pytest.mark.skipif(not SAMPLE.is_file(), reason="the sample recording is not on this machine")
async def test_audio_without_a_type_becomes_a_site_diary():
    report, reply = await service.intake_audio(SAMPLE)
    show(report)
    print(f"  reply: {reply}")
    assert report.status == "needs_approval"
    assert report.type_id == "tyomaapaivakirja"
    assert [event.kind for event in report.timeline][:2] == ["transcribe", "classify"]
    assert all(event.status in ("done", "waiting") for event in report.timeline)
    filled = [field for field in report.fields if field.value]
    assert len(filled) >= 3 and any(field.evidence for field in filled)
    assert report.urgency is not None and 0 < report.urgency.confidence <= 1
    assert report.email is not None and report.email.status == "draft" and report.email.body
    docx = next(item for item in report.files if item.kind == "docx")
    with files.open_file(store.scope_of(report.id), docx.name) as handle:
        document = Document(io.BytesIO(handle.read()))
    assert len(document.tables[0].rows) == len(report.fields)
    assert "Appendix: transcript" in [paragraph.text for paragraph in document.paragraphs]


async def test_text_safety_observation_and_single_approval():
    report, reply = await service.intake_text(OBSERVATION)
    show(report)
    print(f"  reply: {reply}")
    assert report.status == "needs_approval" and report.type_id == "turvallisuushavainto"
    assert report.filled >= 3 and report.urgency is not None
    assert report.urgency.label == "kiireellinen"

    sent = await service.approve(report.id)
    assert sent.status == "sent" and sent.email.status == "sent"
    with pytest.raises(AppError) as refused:
        await service.approve(report.id)
    assert refused.value.status == 409 and refused.value.code == "already_sent"
    assert len(list(mail.outbox_dir().glob("*.eml"))) == 1
