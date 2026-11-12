from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.core.errors import GENERIC_MESSAGE, AppError, install_error_handlers
from app.core.models import (
    ErrorEnvelope,
    FieldDef,
    FieldValue,
    Report,
    ReportSummary,
    ReportType,
    TimelineEvent,
    Urgency,
)
from app.core.util import new_id, now_iso


def make_client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    class Body(BaseModel):
        text: str

    @app.get("/app-error")
    async def app_error():
        raise AppError("report_not_found", "Raporttia ei löytynyt.", 404)

    @app.get("/http-error")
    async def http_error():
        raise HTTPException(502, detail="upstream said: deployment gpt-x key abc123 rejected")

    @app.get("/crash")
    async def crash():
        raise RuntimeError("AuthenticationError: key abc123 is invalid")

    @app.post("/validate")
    async def validate(body: Body):
        return body

    return TestClient(app, raise_server_exceptions=False)


def test_app_error_is_the_envelope():
    response = make_client().get("/app-error")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "report_not_found", "message": "Raporttia ei löytynyt."}
    }
    ErrorEnvelope.model_validate(response.json())


def test_http_exception_detail_is_not_passed_on():
    response = make_client().get("/http-error")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_failed"
    assert "abc123" not in response.text and "gpt-x" not in response.text


def test_unexpected_exception_is_a_generic_500():
    response = make_client().get("/crash")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": GENERIC_MESSAGE}}
    assert "abc123" not in response.text


def test_validation_and_routing_errors_use_the_envelope():
    client = make_client()
    invalid = client.post("/validate", json={"wrong": 1})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"
    assert "wrong" not in invalid.text

    assert client.get("/nothing").json()["error"]["code"] == "not_found"
    assert client.post("/app-error").json()["error"]["code"] == "method_not_allowed"


def test_ids_and_times():
    first, second = new_id("rep"), new_id("rep")
    assert first.startswith("rep_") and first != second and len(first) == 16
    stamp = now_iso()
    assert stamp.endswith("Z") and len(stamp) == 24 and stamp[10] == "T"


FIELD = {
    "name": "sijainti",
    "label": "Sijainti",
    "type": "text",
    "required": True,
    "description": "Missä havainto tehtiin",
}


def report_data() -> dict:
    return {
        "id": "rep_1",
        "created_at": "2026-11-12T10:00:00.000Z",
        "updated_at": "2026-11-12T10:01:00.000Z",
        "channel": "call",
        "caller_label": "Selainpuhelu",
        "type_id": "turvallisuushavainto",
        "type_title": "Turvallisuushavainto",
        "status": "needs_approval",
        "filled": 1,
        "total": 2,
        "urgency": {
            "label": "kiireellinen",
            "confidence": 0.93,
            "probabilities": {"kiireellinen": 0.93, "normaali": 0.06, "ei kiireellinen": 0.01},
            "model": "jev-1.13.0",
        },
        "headline": "Kulunut nostovaijeri",
        "fields": [
            {
                "name": "sijainti",
                "label": "Sijainti",
                "value": "Halli 3",
                "evidence": "hallissa kolme",
                "source": "extractor",
            },
            {"name": "aika", "label": "Aika", "value": None, "evidence": None, "source": "voice"},
        ],
        "missing": ["Aika"],
        "transcript": [
            {"role": "agent", "text": "Hei, olen tekoäly.", "at": "2026-11-12T10:00:01.000Z"},
            {"role": "caller", "text": "Hallissa kolme.", "at": "2026-11-12T10:00:05.000Z"},
        ],
        "timeline": [
            {
                "id": "ev_1",
                "at": "2026-11-12T10:00:30.000Z",
                "kind": "extract",
                "label": "Kentät poimittu 1/2",
                "detail": "Puuttuu: aika",
                "component": "extractor",
                "status": "done",
                "ms": 1200,
            }
        ],
        "files": [
            {
                "name": "raportti.docx",
                "kind": "docx",
                "size": 12000,
                "url": "/api/files/reports/rep_1/raportti.docx",
            }
        ],
        "email": {
            "to": None,
            "subject": "Turvallisuushavainto",
            "body": "Hei",
            "status": "draft",
            "sent_at": None,
        },
    }


def test_field_def_leaves_absent_options_out():
    plain = FieldDef(**FIELD)
    assert plain.model_dump() == FIELD
    assert "options" not in plain.model_dump_json()
    choice = FieldDef(**{**FIELD, "type": "choice", "options": ["a", "b"]})
    assert choice.model_dump()["options"] == ["a", "b"]

    report_type = ReportType(
        id="turvallisuushavainto",
        title="Turvallisuushavainto",
        description="Havainto työmaalta",
        icon="shield-alert",
        requirement="Kirjaa sijainti",
        fields=[plain, choice],
        builtin=True,
    )
    dumped = report_type.model_dump(mode="json")
    assert "options" not in dumped["fields"][0]
    assert dumped["fields"][1]["options"] == ["a", "b"]
    assert ReportType.model_validate(dumped) == report_type


def test_report_round_trips_with_exactly_the_contract_fields():
    data = report_data()
    report = Report.model_validate(data)
    assert report.model_dump(mode="json") == data
    assert set(ReportSummary.model_fields) == {
        "id",
        "created_at",
        "updated_at",
        "channel",
        "caller_label",
        "type_id",
        "type_title",
        "status",
        "filled",
        "total",
        "urgency",
        "headline",
    }
    assert set(Report.model_fields) - set(ReportSummary.model_fields) == {
        "fields",
        "missing",
        "transcript",
        "timeline",
        "files",
        "email",
    }
    summary = report.summary()
    assert type(summary) is ReportSummary
    assert summary.model_dump(mode="json") == {key: data[key] for key in ReportSummary.model_fields}


def test_nullable_fields_are_required_and_enums_are_checked():
    with pytest.raises(ValidationError):
        FieldValue(name="a", label="A", value="x", source="extractor")  # evidence missing
    with pytest.raises(ValidationError):
        FieldValue(name="a", label="A", value="x", evidence=None, source="model")
    with pytest.raises(ValidationError):
        Urgency(label="urgent", confidence=1, probabilities={}, model="m")
    with pytest.raises(ValidationError):
        TimelineEvent(
            id="e",
            at="t",
            kind="upload",
            label="l",
            detail=None,
            component=None,
            status="done",
            ms=None,
        )
    with pytest.raises(ValidationError):
        Report.model_validate({**report_data(), "status": "archived"})
