"""Live smoke tests: real parser, real model calls, small synthetic inputs.

uv run pytest tests/mcp -m live
"""

from __future__ import annotations

import shutil

import pytest

from app.mcp import activity, server, tools

pytestmark = pytest.mark.live


async def run(name: str, arguments: dict | None = None) -> dict:
    arguments = arguments if arguments is not None else await tools.SPECS[name].sample()
    token = tools.calling_as(tools.Caller(client="live test", count_model_call=True))
    try:
        outcome = await server.mcp.call_tool(name, arguments)
    finally:
        tools.reset_caller(token)
    return outcome.structured_content


async def test_parse_document_reads_the_sample_pdf():
    data = await run("parse_document")
    assert data["format"] == "pdf" and data["page_count"] == 1
    assert "Harbour Road 5" in data["text"]


async def test_classify_with_jev():
    data = await run("classify")
    assert data["label"] == "urgent"
    assert abs(sum(data["probabilities"].values()) - 1) < 0.05
    assert activity.recent()[0]["summary"].startswith("classify: 3 labels, ")


async def test_extract_fields_generates_fields_and_fills_them():
    data = await run("extract_fields")
    assert data["fields_generated"] is True and data["filled"] >= 3
    assert any("2026-11-12" in (value["value"] or "") for value in data["values"])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
async def test_transcribe_the_sample_recording():
    data = await run("transcribe")
    assert data["format"] == "mp3" and 5 <= data["duration_s"] <= 8
    assert len(data["text"]) > 20
