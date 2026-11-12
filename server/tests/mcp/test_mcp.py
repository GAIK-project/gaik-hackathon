"""The MCP server without network: the gate, every tool with fakes for the model calls, the
activity log and the routes of the MCP page."""

from __future__ import annotations

import base64
import os
import shutil
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from app.core import files, gaik_ops
from app.core.events import get_bus
from app.core.models import Classification, FieldDef, ParsedDocument, TracedFieldValue, Transcript
from app.mcp import activity, router, server, tools
from tests.conftest import TEST_BRIDGE_KEY, TEST_MCP_KEY, TEST_ORIGIN

PUBLIC_HOST = "gaik-hackathon.example"
MCP_HEADERS = {"Host": PUBLIC_HOST, "Accept": "application/json, text/event-stream"}
TOOL_NAMES = {
    "parse_document",
    "extract_fields",
    "transcribe",
    "classify",
    "search_ai_act",
    "assess_ai_system",
    "list_reports",
    "get_report",
    "create_report",
}
SECRET_TEXT = "the valve in room nine leaks"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def rpc(client, method: str, params: dict[str, Any] | None = None, **headers: str):
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers={**MCP_HEADERS, **headers})


def call(client, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    return response.json()["result"]


# fakes ---------------------------------------------------------------------------------------

FIELDS = [
    FieldDef(name="date", label="Date", type="date", required=True, description="Day of the visit"),
    FieldDef(name="hours", label="Hours", type="number", required=False, description="Hours"),
]


@pytest.fixture
def fake_ops(monkeypatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def parse_document(path: Path) -> ParsedDocument:
        seen["parse_path"] = Path(path)
        seen["parse_existed"] = os.path.isfile(path)  # noqa: ASYNC240
        return ParsedDocument(text="Page one\nPage two", pages=["Page one", "Page two"], kind="pdf")

    async def transcribe_audio(path: Path, language: str = "fi") -> Transcript:
        seen["transcribe"] = (Path(path).suffix, language, os.path.isfile(path))  # noqa: ASYNC240
        return Transcript(text="Hello from the site.", duration_s=7.0, language=language, model="m")

    async def generate_fields(requirement: str) -> list[FieldDef]:
        seen["requirement"] = requirement
        return FIELDS

    async def extract_fields(text, fields, requirement, *, trace=True):
        seen["extract"] = (text, [field.name for field in fields], requirement)
        return [
            TracedFieldValue(
                name="date",
                label="Date",
                value="2026-11-12",
                evidence="12 November",
                source="extractor",
                verdict="verified",
            ),
            TracedFieldValue(
                name="hours", label="Hours", value=None, evidence=None, source="extractor"
            ),
        ]

    async def classify_jev(text, labels, instructions=None) -> Classification:
        seen["classify"] = (text, labels)
        share = round(1 / len(labels), 2)
        return Classification(
            label=next(iter(labels)),
            confidence=0.9,
            probabilities=dict.fromkeys(labels, share),
            model="jev",
        )

    for fn in (parse_document, transcribe_audio, generate_fields, extract_fields, classify_jev):
        monkeypatch.setattr(gaik_ops, fn.__name__, fn)
    return seen


def _report(report_id: str = "rep_1", status: str = "needs_approval") -> dict[str, Any]:
    return {
        "id": report_id,
        "created_at": "2026-11-12T10:00:00.000Z",
        "updated_at": "2026-11-12T10:00:05.000Z",
        "channel": "mcp",
        "caller_label": "MCP",
        "type_id": "turvallisuushavainto",
        "type_title": "Safety observation",
        "status": status,
        "filled": 1,
        "total": 2,
        "urgency": None,
        "headline": "Pallet in front of the exit",
        "fields": [
            {
                "name": "place",
                "label": "Place",
                "value": "Warehouse B",
                "evidence": "warehouse B",
                "source": "extractor",
            },
            {
                "name": "time",
                "label": "Time",
                "value": None,
                "evidence": None,
                "source": "extractor",
            },
        ],
        "missing": ["Time"],
        "transcript": [{"role": "caller", "text": SECRET_TEXT, "at": "2026-11-12T10:00:00.000Z"}],
        "timeline": [],
        "files": [
            {
                "name": "report.docx",
                "kind": "docx",
                "size": 1200,
                "url": f"/api/files/reports/{report_id}/f_1.docx",
            }
        ],
        "email": {
            "to": "someone@example.com",
            "subject": "s",
            "body": "b",
            "status": "draft",
            "sent_at": None,
        },
    }


@pytest.fixture
def fake_services(monkeypatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    reports = types.ModuleType("app.reports.service")
    aiact = types.ModuleType("app.aiact.service")

    async def list_reports(limit: int = 50):
        return [_report("rep_1"), _report("rep_2", "sent")][:limit]

    async def get_report(report_id: str):
        return _report(report_id) if report_id == "rep_1" else None

    async def get_report_type(type_id: str):
        return {"id": type_id} if type_id == "turvallisuushavainto" else None

    async def intake_text(text, *, type_id=None, channel="text", caller_label="Text", wait=True):
        seen["intake"] = {
            "type_id": type_id,
            "channel": channel,
            "caller_label": caller_label,
            "wait": wait,
        }
        return _report("rep_9"), "Thanks, the report is waiting for approval."

    async def search(q: str, k: int = 8):
        seen["search"] = (q, k)
        return [
            {
                "unit_id": "annex-III-4-a",
                "label": "Annex III, point 4(a)",
                "snippet": "recruitment",
                "score": 0.8,
            }
        ]

    async def assess(description: str, on_step=None):
        seen["assess"] = description
        citation = {
            "unit_id": "annex-III-4-a",
            "label": "Annex III, point 4(a)",
            "quote": "recruitment",
            "verified": True,
        }
        return {
            "id": "asm_1",
            "description": description,
            "tier": "high",
            "probabilities": {
                "prohibited": 0.02,
                "high": 0.9,
                "transparency": 0.05,
                "minimal": 0.03,
            },
            "confidence": 0.9,
            "needs_human_review": False,
            "reasoning": "It ranks applicants.",
            "basis": [citation],
            "obligations": [
                {
                    "title": "Risk management",
                    "summary": "s",
                    "who": "provider",
                    "citation": citation,
                    "applies_from": "2027-12-02",
                }
            ],
            "language": "en",
            "disclaimer": "This is not legal advice.",
        }

    for fn in (list_reports, get_report, get_report_type, intake_text):
        setattr(reports, fn.__name__, fn)
    for fn in (search, assess):
        setattr(aiact, fn.__name__, fn)
    monkeypatch.setitem(sys.modules, "app.reports.service", reports)
    monkeypatch.setitem(sys.modules, "app.aiact.service", aiact)
    return seen


# the gate ------------------------------------------------------------------------------------


def test_mcp_needs_the_mcp_key(anon_client, bridge_auth, session_auth):
    assert rpc(anon_client, "tools/list").status_code == 401
    assert rpc(anon_client, "tools/list", **bridge_auth).status_code == 401
    assert rpc(anon_client, "tools/list", Authorization="Bearer wrong").status_code == 401
    assert TEST_BRIDGE_KEY != TEST_MCP_KEY


def test_mcp_key_lists_the_nine_tools_on_both_paths(mcp_client):
    for path in ("/mcp", "/mcp/"):
        response = mcp_client.post(
            path, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=MCP_HEADERS
        )
        assert response.status_code == 200, response.text
        listed = response.json()["result"]["tools"]
        assert {item["name"] for item in listed} == TOOL_NAMES
    by_name = {item["name"]: item for item in listed}
    assert by_name["parse_document"]["inputSchema"]["required"] == ["filename", "content_base64"]
    assert "ctx" not in by_name["classify"]["inputSchema"]["properties"]
    for name in ("classify", "assess_ai_system"):
        assert tools.NOTICE_US in by_name[name]["description"]
        assert tools.NOTICE_PERSONAL in by_name[name]["description"]
    assert tools.NOTICE_US not in by_name["extract_fields"]["description"]


def test_the_mcp_key_opens_nothing_else(mcp_client):
    assert mcp_client.get("/api/mcp/info").status_code == 401
    assert mcp_client.post("/api/mcp/try", json={"tool": "list_reports"}).status_code == 401


def test_unknown_host_is_refused(mcp_client):
    response = mcp_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": MCP_HEADERS["Accept"], "Host": "evil.example"},
    )
    assert response.status_code == 421
    assert PUBLIC_HOST in server.allowed_hosts()


def test_no_tool_approves_mails_fetches_or_reads_paths():
    assert set(tools.SPECS) == TOOL_NAMES
    for spec in tools.SPECS.values():
        assert not {"url", "path", "to", "email"} & set(spec.sample_shown)


# gaik tools ----------------------------------------------------------------------------------


def test_parse_document_checks_magic_bytes_and_cleans_up(mcp_client, fake_ops):
    pdf = tools.tiny_pdf(["Hello"])
    assert pdf.startswith(b"%PDF-")
    result = call(
        mcp_client, "parse_document", {"filename": "../x.pdf", "content_base64": b64(pdf)}
    )
    assert not result.get("isError"), result
    data = result["structuredContent"]
    assert data["page_count"] == 2 and data["text"] == "Page one\nPage two"
    assert fake_ops["parse_existed"] and fake_ops["parse_path"].suffix == ".pdf"
    assert not fake_ops["parse_path"].exists()  # the working copy is gone
    assert "x.pdf" not in fake_ops["parse_path"].name  # stored under a server-made name

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    refused = call(mcp_client, "parse_document", {"filename": "a.pdf", "content_base64": b64(png)})
    assert refused["isError"] and "not supported" in refused["content"][0]["text"]
    broken = call(mcp_client, "parse_document", {"filename": "a.pdf", "content_base64": "%%%"})
    assert broken["isError"] and "base64" in broken["content"][0]["text"]

    records = activity.recent()
    assert [record["ok"] for record in records] == [False, False, True]
    assert records[2]["summary"] == "parse_document: 1 pdf file, 2 pages, 17 characters"
    assert records[0]["summary"] == "parse_document: failed (invalid_base64)"
    assert not list(files.files_root().rglob("*.pdf"))


async def test_content_over_15_mb_is_refused(fake_ops):
    too_big = "A" * (21 * 1024 * 1024)
    with pytest.raises(tools.ToolFailure) as caught:
        await tools.SPECS["parse_document"].run(filename="a.pdf", content_base64=too_big)
    assert caught.value.code == "too_large" and caught.value.status == 413
    assert "parse_path" not in fake_ops


def test_extract_fields_with_a_requirement_or_fields(mcp_client, fake_ops):
    result = call(
        mcp_client, "extract_fields", {"text": SECRET_TEXT, "requirement": "Collect the date."}
    )
    data = result["structuredContent"]
    assert data["fields_generated"] is True and [f["name"] for f in data["fields"]] == [
        "date",
        "hours",
    ]
    assert data["values"][0] == {
        "name": "date",
        "label": "Date",
        "value": "2026-11-12",
        "evidence": "12 November",
        "verdict": "verified",
    }
    assert data["filled"] == 1 and data["total"] == 2
    assert fake_ops["requirement"] == "Collect the date."

    fields = [field.model_dump() for field in FIELDS[:1]]
    result = call(mcp_client, "extract_fields", {"text": SECRET_TEXT, "fields": fields})
    assert result["structuredContent"]["fields_generated"] is False
    assert fake_ops["extract"][1] == ["date"]

    neither = call(mcp_client, "extract_fields", {"text": SECRET_TEXT})
    assert neither["isError"] and "requirement or fields" in neither["content"][0]["text"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_transcribe_converts_the_audio_and_cleans_up(mcp_client, fake_ops):
    sample = (Path(tools.__file__).parent / "samples" / "sample-en.mp3").read_bytes()
    result = call(mcp_client, "transcribe", {"filename": "note.mp3", "content_base64": b64(sample)})
    assert not result.get("isError"), result
    data = result["structuredContent"]
    assert (
        data["text"] == "Hello from the site."
        and data["format"] == "mp3"
        and data["language"] == "en"
    )
    assert fake_ops["transcribe"] == (".wav", "en", True)
    assert not [item for item in files.files_root().rglob("*") if item.is_file()]

    not_audio = call(
        mcp_client,
        "transcribe",
        {"filename": "a.mp3", "content_base64": b64(tools.tiny_pdf(["x"]))},
    )
    assert not_audio["isError"] and "not supported" in not_audio["content"][0]["text"]
    language = call(
        mcp_client,
        "transcribe",
        {"filename": "a.mp3", "content_base64": b64(sample), "language": "xx"},
    )
    assert language["isError"] and "en, fi or sv" in language["content"][0]["text"]


def test_classify_and_the_activity_summary_hold_no_argument_text(mcp_client, fake_ops):
    labels = {"urgent": "Act now", "normal": "Can wait"}
    result = call(mcp_client, "classify", {"text": SECRET_TEXT, "labels": labels})
    assert result["structuredContent"]["label"] == "urgent"
    assert set(result["structuredContent"]["probabilities"]) == set(labels)
    record = activity.recent()[0]
    assert record["tool"] == "classify" and record["ok"] is True and record["client"]
    assert record["summary"] == f"classify: 2 labels, {len(SECRET_TEXT)} characters"
    assert SECRET_TEXT not in str(activity.recent()) and "Act now" not in str(activity.recent())


def test_models_off_stops_model_tools_only(mcp_client, fake_ops, fake_services, monkeypatch):
    monkeypatch.setenv("HACK_MODELS_OFF", "1")
    from app.core.settings import get_settings

    get_settings.cache_clear()
    refused = call(mcp_client, "classify", {"text": "x", "labels": {"a": "A", "b": "B"}})
    assert refused["isError"] and "switched off" in refused["content"][0]["text"]
    assert "classify" not in fake_ops
    assert not call(mcp_client, "list_reports", {}).get("isError")


# AI Act and reports --------------------------------------------------------------------------


def test_ai_act_tools(mcp_client, fake_services):
    hits = call(mcp_client, "search_ai_act", {"query": "recruitment"})["structuredContent"]["hits"]
    assert hits[0]["unit_id"] == "annex-III-4-a" and fake_services["search"] == ("recruitment", 8)
    data = call(mcp_client, "assess_ai_system", {"description": "A tool that ranks applicants."})[
        "structuredContent"
    ]
    assert data["tier"] == "high" and data["basis"][0]["label"] == "Annex III, point 4(a)"
    assert data["obligations"][0]["title"] == "Risk management" and "description" not in data
    assert (
        activity.recent()[0]["summary"]
        == "assess_ai_system: 1 provision, 1 obligation, 29 characters"
    )


def test_report_tools(mcp_client, fake_services):
    listed = call(mcp_client, "list_reports", {})["structuredContent"]["reports"]
    assert [item["id"] for item in listed] == ["rep_1", "rep_2"]
    sent = call(mcp_client, "list_reports", {"status": "sent"})["structuredContent"]["reports"]
    assert [item["id"] for item in sent] == ["rep_2"]
    assert call(mcp_client, "list_reports", {"status": "nope"})["isError"]

    report = call(mcp_client, "get_report", {"report_id": "rep_1"})["structuredContent"]
    assert report["word_file_url"] == f"{TEST_ORIGIN}/api/files/reports/rep_1/f_1.docx"
    assert report["fields"][0]["value"] == "Warehouse B" and report["missing"] == ["Time"]
    assert "transcript" not in report and "email" not in report and SECRET_TEXT not in str(report)
    assert call(mcp_client, "get_report", {"report_id": "rep_404"})["isError"]

    created = call(mcp_client, "create_report", {"text": SECRET_TEXT})["structuredContent"]
    assert created["id"] == "rep_9" and created["reply"].startswith("Thanks")
    assert fake_services["intake"] == {
        "type_id": None,
        "channel": "mcp",
        "caller_label": "MCP",
        "wait": True,
    }
    unknown = call(mcp_client, "create_report", {"text": SECRET_TEXT, "type_id": "nope"})
    assert unknown["isError"] and "type_id" in unknown["content"][0]["text"]
    assert SECRET_TEXT not in str(activity.recent())


def test_a_missing_feature_is_a_clear_error(mcp_client, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.aiact.service", None)
    result = call(mcp_client, "search_ai_act", {"query": "biometrics"})
    assert result["isError"] and "not available" in result["content"][0]["text"]
    assert activity.recent()[0]["summary"] == "search_ai_act: failed (not_available)"


# activity ------------------------------------------------------------------------------------


def test_activity_keeps_the_newest_200(client):
    for number in range(activity.KEEP + 5):
        record = activity.start("list_reports", "tester")
        activity.finish(record, ok=True, ms=number, summary="list_reports: 0 reports")
    records = client.get("/api/mcp/activity").json()
    assert len(records) == activity.KEEP
    assert records[0]["ms"] == activity.KEEP + 4 and records[-1]["ms"] == 5
    assert set(records[0]) == {"id", "at", "tool", "client", "ok", "ms", "summary", "status"}


async def test_a_call_is_published_when_it_starts_and_ends(fake_services):
    subscription = get_bus().subscribe(activity.TOPIC)
    token = tools.calling_as(tools.Caller(client="tester"))
    try:
        await tools.SPECS["list_reports"].run()
    finally:
        tools.reset_caller(token)
    first, second = await anext(subscription), await anext(subscription)
    subscription.close()
    assert (first.name, first.data["status"], first.data["ok"]) == ("call", "running", None)
    assert (second.name, second.data["status"], second.data["ok"]) == ("call", "done", True)
    assert first.data["id"] == second.data["id"] and second.data["client"] == "tester"
    assert second.data["summary"] == "list_reports: 2 reports"


def test_activity_stream_is_served_as_sse(client, monkeypatch):
    async def ended():
        return
        yield  # pragma: no cover

    monkeypatch.setattr(
        router, "get_bus", lambda: types.SimpleNamespace(subscribe=lambda topic: ended())
    )
    response = client.get("/api/mcp/activity/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: done" in response.text


# the page's routes ---------------------------------------------------------------------------


def test_info_has_the_url_the_tools_and_snippets_but_no_key(client, anon_client):
    assert anon_client.get("/api/mcp/info").status_code == 401
    response = client.get("/api/mcp/info")
    assert response.status_code == 200
    data = response.json()
    assert data["url"] == f"{TEST_ORIGIN}/mcp"
    assert {item["name"] for item in data["tools"]} == TOOL_NAMES
    classify = next(item for item in data["tools"] if item["name"] == "classify")
    assert set(classify["arguments"]["properties"]) == {"text", "labels"}
    assert classify["sample"]["labels"] and classify["model_backed"] is True
    assert [item["id"] for item in data["clients"]] == [
        "claude-code",
        "mcp-json",
        "claude-desktop",
        "codex",
        "cursor",
    ]
    claude_code = data["clients"][0]["snippet"]
    assert claude_code.startswith(f"claude mcp add --transport http gaik {TEST_ORIGIN}/mcp ")
    assert router.KEY_PLACEHOLDER in claude_code
    assert TEST_MCP_KEY not in response.text and TEST_BRIDGE_KEY not in response.text
    parse = next(item for item in data["tools"] if item["name"] == "parse_document")
    assert parse["sample"]["content_base64"] == tools.SAMPLE_PLACEHOLDER


def test_try_runs_a_tool_with_its_built_in_sample(client, fake_ops):
    response = client.post("/api/mcp/try", json={"tool": "parse_document"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True and data["tool"] == "parse_document"
    assert data["result"]["page_count"] == 2
    assert data["arguments"] == {
        "filename": "service-report.pdf",
        "content_base64": tools.SAMPLE_PLACEHOLDER,
    }
    assert activity.recent()[0]["client"] == router.TRY_CLIENT


def test_try_with_arguments_and_its_errors(client, fake_ops, fake_services):
    labels = {"a": "A", "b": "B"}
    response = client.post(
        "/api/mcp/try", json={"tool": "classify", "arguments": {"text": "hello", "labels": labels}}
    )
    assert response.status_code == 200 and response.json()["result"]["label"] == "a"

    assert client.post("/api/mcp/try", json={"tool": "send_mail"}).status_code == 404
    wrong = client.post("/api/mcp/try", json={"tool": "classify", "arguments": {"text": 5}})
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "invalid_arguments"
    missing = client.post(
        "/api/mcp/try", json={"tool": "get_report", "arguments": {"report_id": "x"}}
    )
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    assert missing.json()["error"]["message"] == "There is no report with that id."
    newest = client.post("/api/mcp/try", json={"tool": "get_report"})
    assert newest.status_code == 200 and newest.json()["result"]["id"] == "rep_1"


def test_try_counts_model_calls_for_the_session(client, fake_ops, monkeypatch):
    from app.core import limits

    monkeypatch.setattr(limits, "MODEL_CALLS_PER_WINDOW", 1)
    body = {"tool": "classify", "arguments": {"text": "hello", "labels": {"a": "A", "b": "B"}}}
    assert client.post("/api/mcp/try", json=body).status_code == 200
    second = client.post("/api/mcp/try", json=body)
    assert second.status_code == 429
    assert second.json()["error"]["message"] == "Too many requests. Try again in a few minutes."
    assert client.post("/api/mcp/try", json={"tool": "parse_document"}).status_code == 200
