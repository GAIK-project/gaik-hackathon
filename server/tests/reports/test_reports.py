"""Reports without network: the model calls are fakes, everything else is the real code."""

from __future__ import annotations

import asyncio
import io
import time
import wave
from email import message_from_bytes
from pathlib import Path

import pytest
from docx import Document

from app.core import files, gaik_ops, limits, llm
from app.core.errors import AppError
from app.core.events import get_bus
from app.core.models import Classification, FieldDef, FieldValue, Transcript
from app.core.settings import get_settings
from app.reports import mail, service, store
from tests.conftest import TEST_RECIPIENT

DIARY = "Site diary for today at the Harbour Street site. Sleet all day, eight workers on site."


class Fakes:
    """What the model would have answered. Tests change the attributes."""

    def __init__(self) -> None:
        self.type_label = "tyomaapaivakirja"
        self.type_confidence = 0.92
        self.urgency = "normaali"
        self.leave_empty = {"weather"}
        self.values: dict[str, str] = {}
        self.fail: set[str] = set()
        self.extract_texts: list[str] = []
        self.transcript = DIARY

    async def classify_azure(self, text, labels, instructions=None):
        if "classify" in self.fail:
            raise AppError("classification_failed", "provider text must not leak", 502)
        if "kiireellinen" in labels:
            label, confidence = self.urgency, 0.8
        else:
            label, confidence = self.type_label, self.type_confidence
        rest = (1 - confidence) / max(1, len(labels) - 1)
        probabilities = {name: (confidence if name == label else rest) for name in labels}
        return Classification(
            label=label, confidence=confidence, probabilities=probabilities, model="fake", ms=3
        )

    async def extract_fields(self, text, fields: list[FieldDef], requirement, *, trace=True):
        if "extract" in self.fail:
            raise AppError("extraction_failed", "provider text must not leak", 502)
        self.extract_texts.append(text)
        values = []
        for field in fields:
            value = None if field.name in self.leave_empty else f"value of {field.name}"
            value = self.values.get(field.name, value)
            values.append(
                FieldValue(
                    name=field.name,
                    label=field.label,
                    value=value,
                    evidence="Sleet all day" if value else None,
                    source="extractor",
                )
            )
        return values

    async def transcribe_audio(self, path: Path, language: str = "fi"):
        assert path.suffix == ".wav"
        return Transcript(text=self.transcript, duration_s=4.0, language=language, model="fake")

    async def generate_fields(self, requirement: str):
        return [
            FieldDef(name="item", label="Item", type="text", required=True, description="What"),
            FieldDef(name="note", label="Note", type="longtext", required=False, description="x"),
        ]

    async def to_wav(self, source: Path, audio_format=None, target: Path | None = None):
        target = target or source.with_name(f"{source.stem}.16k.wav")
        target.write_bytes(wav_bytes())
        return files.ConvertedAudio(path=target, duration_s=4.0)

    async def structured_chat(self, system, user, schema_model, **options):
        return schema_model(title="Stock check", description="Counts of items.", icon="package")


def wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def fakes(monkeypatch) -> Fakes:
    fake = Fakes()
    monkeypatch.setattr(gaik_ops, "classify_azure", fake.classify_azure)
    monkeypatch.setattr(gaik_ops, "extract_fields", fake.extract_fields)
    monkeypatch.setattr(gaik_ops, "transcribe_audio", fake.transcribe_audio)
    monkeypatch.setattr(gaik_ops, "generate_fields", fake.generate_fields)
    monkeypatch.setattr(files, "to_wav", fake.to_wav)
    monkeypatch.setattr(llm, "structured_chat", fake.structured_chat)
    return fake


def intake(client, text: str = DIARY, **extra) -> dict:
    response = client.post("/api/intake/text", json={"text": text, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def outbox() -> list[Path]:
    return sorted(mail.outbox_dir().glob("*.eml"))


# report types -------------------------------------------------------------------------------


def test_builtin_types_are_english_field_json(client):
    types = client.get("/api/report-types").json()
    assert [item["id"] for item in types] == [
        "turvallisuushavainto",
        "tyomaapaivakirja",
        "huoltoraportti",
    ]
    assert [item["title"] for item in types] == [
        "Safety observation",
        "Site diary",
        "Maintenance report",
    ]
    for item in types:
        assert item["builtin"] is True and item["requirement"]
        assert 5 <= len(item["fields"]) <= 7
        assert any(field["required"] for field in item["fields"])
        assert any(field["type"] == "choice" and field["options"] for field in item["fields"]) or (
            item["id"] == "tyomaapaivakirja"
        )


def test_user_made_type_and_deletes(client):
    made = client.post("/api/report-types", json={"requirement": "Count the stock"}).json()
    assert made["builtin"] is False and made["title"] == "Stock check"
    assert [field["name"] for field in made["fields"]] == ["item", "note"]
    assert made["id"] in [item["id"] for item in client.get("/api/report-types").json()]

    result = intake(client, type_id=made["id"])
    assert result["report"]["type_title"] == "Stock check"

    assert client.delete("/api/report-types/turvallisuushavainto").status_code == 409
    assert client.delete(f"/api/report-types/{made['id']}").status_code == 200
    assert client.delete(f"/api/report-types/{made['id']}").status_code == 404
    assert len(client.get("/api/report-types").json()) == 3


# the pipeline -------------------------------------------------------------------------------


def test_text_intake_runs_the_steps_and_waits_for_approval(client):
    result = intake(client)
    report = result["report"]
    assert report["type_id"] == "tyomaapaivakirja" and report["type_title"] == "Site diary"
    assert report["status"] == "needs_approval"
    assert report["channel"] == "text" and report["caller_label"] == "Text"

    kinds = [(event["kind"], event["status"]) for event in report["timeline"]]
    assert sorted(kinds) == sorted(
        [
            ("classify", "done"),
            ("extract", "done"),
            ("urgency", "done"),
            ("document", "done"),
            ("email", "waiting"),
        ]
    )
    assert all(event["ms"] is not None and event["component"] for event in report["timeline"])
    extract = next(event for event in report["timeline"] if event["kind"] == "extract")
    assert extract["label"] == "Fields extracted 6/7" and extract["detail"] == "Missing: Weather"

    assert report["total"] == 4 and report["filled"] == 3 and report["missing"] == ["Weather"]
    site = next(field for field in report["fields"] if field["name"] == "site")
    assert site == {
        "name": "site",
        "label": "Site",
        "value": "value of site",
        "evidence": "Sleet all day",
        "source": "extractor",
    }
    assert report["urgency"]["label"] == "normaali" and report["urgency"]["model"] == "fake"
    assert report["headline"] == "value of work_done"
    assert report["transcript"][0]["role"] == "caller"
    assert "x" not in report

    assert report["email"]["status"] == "draft" and report["email"]["to"] == TEST_RECIPIENT
    assert report["email"]["subject"] == "Site diary: value of work_done"
    assert "Weather: —" in report["email"]["body"] and "Missing: Weather" in report["email"]["body"]
    assert "site diary" in result["reply"] and "Still missing: Weather." in result["reply"]
    assert outbox() == []  # nothing is sent without approval

    (entry,) = report["files"]
    assert entry["kind"] == "docx" and entry["url"].startswith(f"/api/reports/{report['id']}/")
    download = client.get(entry["url"])
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    document = Document(io.BytesIO(download.content))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Site diary" in text and "Appendix: transcript" in text and DIARY in text
    cells = [[cell.text for cell in row.cells] for row in document.tables[0].rows]
    assert ["Site", "value of site"] in cells and ["Weather", "—"] in cells
    assert "GAIK" in document.sections[0].footer.paragraphs[0].text

    listed = client.get("/api/reports").json()
    assert [item["id"] for item in listed] == [report["id"]] and "fields" not in listed[0]
    assert client.get(f"/api/reports/{report['id']}/files/../app.db").status_code == 404
    assert client.get(f"/api/reports/{report['id']}/files/nope.docx").status_code == 404


def test_low_confidence_falls_back_to_safety_observation(client, fakes):
    fakes.type_confidence = 0.3
    report = intake(client)["report"]
    assert report["type_id"] == "turvallisuushavainto"
    classify = next(event for event in report["timeline"] if event["kind"] == "classify")
    assert classify["detail"] == "Low confidence (30 %), using Safety observation"


def test_given_type_skips_classification(client):
    report = intake(client, type_id="huoltoraportti")["report"]
    assert report["type_id"] == "huoltoraportti"
    assert "classify" not in [event["kind"] for event in report["timeline"]]
    assert client.post("/api/intake/text", json={"text": "x", "type_id": "nope"}).status_code == 404
    assert client.post("/api/intake/text", json={"text": "  "}).status_code == 400


def test_a_failing_step_is_an_error_event_and_the_rest_goes_on(client, fakes):
    fakes.fail = {"extract"}
    report = intake(client, type_id="huoltoraportti")["report"]
    extract = next(event for event in report["timeline"] if event["kind"] == "extract")
    assert (
        extract["status"] == "error" and extract["detail"] == "The fields could not be extracted."
    )
    assert "provider" not in str(report)
    assert report["status"] == "needs_approval" and report["filled"] == 0
    assert report["urgency"] is not None and report["files"]

    fakes.fail = {"classify"}
    report = intake(client)["report"]
    assert report["type_id"] == "turvallisuushavainto" and report["urgency"] is None
    assert report["status"] == "needs_approval"


def test_audio_intake_transcribes_first(client):
    response = client.post(
        "/api/intake/audio",
        files={"file": ("../../evil.wav", wav_bytes(), "audio/wav")},
        data={"caller_label": "+358 40 1234567"},
    )
    assert response.status_code == 200, response.text
    report = response.json()["report"]
    assert report["channel"] == "upload" and report["caller_label"] == "Audio file"
    assert [event["kind"] for event in report["timeline"]][:2] == ["transcribe", "classify"]
    assert report["transcript"][0]["text"] == DIARY
    assert sorted(item["kind"] for item in report["files"]) == ["audio", "docx"]
    audio = next(item for item in report["files"] if item["kind"] == "audio")
    assert "evil" not in audio["name"] and client.get(audio["url"]).status_code == 200
    folder = files.scope_dir(store.scope_of(report["id"]))
    assert sorted(path.suffix for path in folder.iterdir()) == [".docx", ".wav"]  # no leftovers

    refused = client.post("/api/intake/audio", files={"file": ("a.wav", b"<html>not audio</html>")})
    assert refused.status_code == 415
    assert len(client.get("/api/reports").json()) == 1


def test_wait_false_answers_at_once(client):
    response = client.post("/api/intake/text?wait=false", json={"text": DIARY})
    body = response.json()
    assert body["report"]["status"] == "processing"
    assert body["report"]["type_id"] == "" and body["reply"].startswith("Got it.")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        report = client.get(f"/api/reports/{body['report']['id']}").json()
        if report["status"] != "processing":
            break
        time.sleep(0.05)
    assert report["status"] == "needs_approval" and report["type_id"] == "tyomaapaivakirja"


async def test_every_change_is_published():
    board = get_bus().subscribe(store.TOPIC)
    report, _ = await service.intake_text(DIARY)
    one = get_bus().subscribe(store.topic_of(report.id))
    await service.set_fields(report.id, {"weather": "Sleet"}, "edit")
    board.close()
    one.close()
    summaries = [event async for event in board]
    assert len(summaries) >= 12  # create, transcript, and running + done for five steps
    assert {event.name for event in summaries} == {"report"}
    assert summaries[0].data.status == "processing" and summaries[-1].data.filled == 4
    assert not hasattr(summaries[0].data, "fields")
    full = [event async for event in one]
    assert full and full[-1].data.fields and full[-1].data.missing == []


# approval -----------------------------------------------------------------------------------


def test_approving_twice_sends_one_mail(client, mcp_client, bridge_client):
    report = intake(client)["report"]
    url = f"/api/reports/{report['id']}/approve"
    assert mcp_client.post(url).status_code == 401
    assert client.post(url, json={"to": "someone@else.example"}).status_code == 403
    assert outbox() == []

    first = bridge_client.post(url)
    assert first.status_code == 200, first.text
    sent = first.json()
    assert sent["status"] == "sent" and sent["email"]["status"] == "sent"
    assert sent["email"]["to"] == TEST_RECIPIENT and sent["email"]["sent_at"]
    approve = sent["timeline"][-1]
    assert approve["kind"] == "approve" and "Mail is off" in approve["detail"]
    assert all(event["status"] != "waiting" for event in sent["timeline"])

    second = client.post(url, json={})
    assert second.status_code == 409 and second.json()["error"]["code"] == "already_sent"
    (eml,) = outbox()
    message = message_from_bytes(eml.read_bytes())
    assert message["To"] == TEST_RECIPIENT and message["Subject"].startswith("Site diary")
    parts = list(message.walk())
    assert [part.get_content_type() for part in parts if not part.is_multipart()] == [
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    Document(io.BytesIO(parts[-1].get_payload(decode=True)))

    assert (
        client.patch(
            f"/api/reports/{report['id']}/fields", json={"values": {"site": "x"}}
        ).status_code
        == 409
    )
    assert (
        client.post(f"/api/reports/{report['id']}/correct", json={"text": "x"}).status_code == 409
    )


async def test_two_approvals_at_once_send_one_mail():
    report, _ = await service.intake_text(DIARY)
    results = await asyncio.gather(
        service.approve(report.id), service.approve(report.id), return_exceptions=True
    )
    refused = [item for item in results if isinstance(item, AppError)]
    assert len(refused) == 1 and refused[0].code == "already_sent" and len(outbox()) == 1


def test_smtp_is_used_when_a_host_is_set(client, monkeypatch):
    report = intake(client)["report"]
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    get_settings.cache_clear()
    sent = []
    monkeypatch.setattr(mail, "send_smtp", sent.append)
    recipients = client.get("/api/mail/recipients").json()
    assert recipients == {
        "recipients": [TEST_RECIPIENT],
        "default": TEST_RECIPIENT,
        "enabled": True,
    }
    done = client.post(f"/api/reports/{report['id']}/approve").json()
    assert done["timeline"][-1]["label"] == "Approved and sent"
    assert len(sent) == 1 and sent[0]["To"] == TEST_RECIPIENT and outbox() == []


def test_a_failed_send_can_be_tried_again(client, monkeypatch):
    report = intake(client)["report"]
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    get_settings.cache_clear()

    def broken(message):
        raise OSError("connection refused by 10.0.0.1")

    monkeypatch.setattr(mail, "send_smtp", broken)
    url = f"/api/reports/{report['id']}/approve"
    failed = client.post(url)
    assert failed.status_code == 502 and "10.0.0.1" not in failed.text
    current = client.get(f"/api/reports/{report['id']}").json()
    assert current["status"] == "needs_approval" and current["email"]["status"] == "failed"
    monkeypatch.setattr(mail, "send_smtp", lambda message: None)
    assert client.post(url).json()["status"] == "sent"


def test_mail_cap_and_missing_recipient(client, monkeypatch):
    first = intake(client)["report"]
    second = intake(client)["report"]
    monkeypatch.setenv("MAIL_MAX_PER_HOUR", "1")
    get_settings.cache_clear()
    limits.reset()
    assert client.post(f"/api/reports/{first['id']}/approve").status_code == 200
    capped = client.post(f"/api/reports/{second['id']}/approve")
    assert capped.status_code == 429
    assert client.get(f"/api/reports/{second['id']}").json()["status"] == "needs_approval"
    assert len(outbox()) == 1

    monkeypatch.setenv("MAIL_ALLOWED_TO", "")
    get_settings.cache_clear()
    nobody = client.post(f"/api/reports/{second['id']}/approve")
    assert nobody.status_code == 409 and nobody.json()["error"]["code"] == "mail_no_recipient"


# edits, corrections, deletes ----------------------------------------------------------------


def test_editing_fields_rebuilds_the_document_and_the_draft(client):
    report = intake(client)["report"]
    edited = client.patch(
        f"/api/reports/{report['id']}/fields",
        json={"values": {"weather": "Sleet, see https://evil.example/x now", "nope": "x"}},
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    weather = next(field for field in body["fields"] if field["name"] == "weather")
    assert weather["source"] == "edit" and weather["evidence"] is None
    assert body["missing"] == [] and body["filled"] == 4
    assert body["timeline"][-1]["kind"] == "edit" and body["timeline"][-1]["detail"] == "Weather"
    assert "https://" not in body["email"]["body"] and "[link removed]" in body["email"]["body"]
    docx = next(item for item in body["files"] if item["kind"] == "docx")
    document = Document(io.BytesIO(client.get(docx["url"]).content))
    cells = [[cell.text for cell in row.cells] for row in document.tables[0].rows]
    assert ["Weather", "Sleet, see https://evil.example/x now"] in cells


def test_a_correction_in_words_reruns_the_extractor(client, fakes):
    report = intake(client)["report"]
    fakes.leave_empty = {"site"}
    fakes.values = {"weather": "Sunny"}
    response = client.post(
        f"/api/reports/{report['id']}/correct", json={"text": "The weather was sunny."}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "CORRECTION FROM THE REPORTER" in fakes.extract_texts[-1]
    assert fakes.extract_texts[-1].startswith(DIARY)
    values = {field["name"]: field["value"] for field in body["fields"]}
    assert values["weather"] == "Sunny" and values["site"] == "value of site"  # kept
    assert body["transcript"][-1]["text"] == "Correction: The weather was sunny."
    event = next(item for item in body["timeline"] if item["label"] == "Correction applied")
    assert event["kind"] == "edit" and event["detail"] == "Changed: Weather"
    assert "Weather: Sunny" in body["email"]["body"] and body["status"] == "needs_approval"

    spoken = client.post(
        f"/api/reports/{report['id']}/correct", files={"file": ("c.wav", wav_bytes())}
    )
    assert spoken.status_code == 200, spoken.text
    assert spoken.json()["transcript"][-1]["text"] == f"Correction: {DIARY}"


def test_deleting_a_report_removes_its_files(client):
    report = intake(client)["report"]
    folder = files.scope_dir(store.scope_of(report["id"]))
    assert any(folder.iterdir())
    assert client.delete(f"/api/reports/{report['id']}").status_code == 200
    assert client.get(f"/api/reports/{report['id']}").status_code == 404
    assert client.delete(f"/api/reports/{report['id']}").status_code == 404
    assert not folder.exists() or not any(folder.iterdir())


# what voice and mcp use ---------------------------------------------------------------------


async def test_a_call_keeps_what_the_agent_saved(fakes):
    report = await service.create_report("tyomaapaivakirja", "call", "Browser call", "live")
    assert report.status == "live" and report.total == 4 and report.filled == 0
    await service.set_fields(report.id, {"weather": "Sleet", "unknown": "x"}, "voice")
    await service.add_transcript(report.id, "agent", "What was the weather?")
    await service.add_transcript(report.id, "caller", "Sleet all day.")
    event = await service.add_event(report.id, "call", "Call started", status="running")
    await service.update_event(report.id, event.id, status="done", label="Call ended", ms=1200)
    await service.finish_call(report.id)
    await service.finish_call(report.id)  # a second call (save, then hang-up) starts nothing
    await service.wait_idle()
    done = await service.get_report(report.id)
    assert len(fakes.extract_texts) == 1
    assert done.status == "needs_approval"
    assert fakes.extract_texts[-1] == "Agent: What was the weather?\nCaller: Sleet all day."
    weather = next(field for field in done.fields if field.name == "weather")
    assert weather.value == "Sleet" and weather.source == "voice"
    assert next(field for field in done.fields if field.name == "site").source == "extractor"
    assert done.timeline[0].label == "Call ended" and done.timeline[0].ms == 1200
    assert (await service.list_reports())[0].id == report.id

    silent = await service.create_report("tyomaapaivakirja", "call", "Browser call", "live")
    await service.finish_call(silent.id)
    assert (await service.get_report(silent.id)).status == "failed"
    other = await service.create_report("huoltoraportti", "whatsapp", "WhatsApp", "live")
    await service.fail_report(other.id, "The call was cut off")
    failed = await service.get_report(other.id)
    assert failed.status == "failed" and failed.timeline[-1].detail == "The call was cut off"
    with pytest.raises(AppError):
        await service.create_report("nope", "call", "x")


def test_reset_demo_clears_the_data_folder(client):
    from scripts import reset_demo

    intake(client)
    data_dir = get_settings().data_path
    assert any(data_dir.iterdir())
    assert reset_demo.reset(data_dir) == []
    assert data_dir.is_dir() and not any(data_dir.iterdir())
