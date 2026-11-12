"""Studio against the real models: template choice and one run of each template on the
samples. Step timings are printed (run with -s)."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from app.core.models import FieldDef
from app.studio import catalog
from app.studio.models import StudioClass, StudioSpec

pytestmark = pytest.mark.live


def events_of(text: str) -> list[tuple[str, Any]]:
    events = []
    for block in text.split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


def spec_for(template: str, **changes: Any) -> StudioSpec:
    values: dict[str, Any] = {
        "id": "stu_live",
        "title": "Live check",
        "summary": "",
        "requirement": "",
        "template": template,
        "input": catalog.input_for(template),
        "steps": catalog.steps_for(template),
        "output": catalog.output_for(template),
        "notices": catalog.notices_for(template),
    }
    values.update(changes)
    return StudioSpec(**values)


def run(client, spec: StudioSpec, **form: str) -> dict[str, Any]:
    response = client.post("/api/studio/run", data={"spec": spec.model_dump_json(), **form})
    assert response.status_code == 200, response.text
    events = events_of(response.text)
    for name, data in events:
        if name == "step" and data["status"] != "running":
            print(
                f"  {spec.template} {data['id']}: {data['status']} {data['ms']} ms, {data['note']}"
            )
    assert "error" not in dict(events), dict(events)["error"]
    return dict(events)["result"]


@pytest.mark.parametrize(
    ("prompt", "template"),
    [
        ("ostolaskut taulukkoon", "documents_to_table"),
        ("saneltu työmaapäiväkirja Wordiksi", "audio_to_report"),
        ("kysymyksiä toimitusehdoista", "documents_to_answers"),
        ("sort incoming customer emails by topic", "documents_to_labels"),
    ],
)
def test_generate_picks_the_template(client, prompt, template):
    response = client.post("/api/studio/generate", json={"prompt": prompt})
    events = events_of(response.text)
    assert "error" not in dict(events), dict(events)["error"]
    spec = StudioSpec.model_validate(dict(events)["spec"]["spec"])
    print(f"\n  {prompt!r} -> {spec.template}: {spec.title!r}; {spec.summary!r}")
    print("  fields:", [(field.name, field.label, field.type) for field in spec.fields])
    print("  classes:", [item.name for item in spec.classes], "questions:", spec.question_examples)
    assert spec.template == template
    assert [name for name, _ in events if name == "step"]
    if template in ("documents_to_table", "audio_to_report"):
        assert len(spec.fields) >= 3


INVOICE_FIELDS = [
    FieldDef(name="supplier", label="Supplier", type="text", required=True, description="Seller."),
    FieldDef(
        name="invoice_number",
        label="Invoice number",
        type="text",
        required=True,
        description="The number of the invoice.",
    ),
    FieldDef(
        name="due_date", label="Due date", type="date", required=True, description="Due date."
    ),
    FieldDef(
        name="total",
        label="Total",
        type="number",
        required=True,
        description="The amount to pay including VAT.",
    ),
]


def test_run_table_reads_totals_and_due_dates(client):
    spec = spec_for(
        "documents_to_table",
        title="Invoices to a table",
        requirement="ostolaskut taulukkoon",
        fields=INVOICE_FIELDS,
    )
    result = run(client, spec, sample_id="invoices")
    rows = {row["source"]: row["values"] for row in result["rows"]}
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    expected = {
        "lasku-pumpputarvike.pdf": ("2026-11-16", 1240.0, "2026-1041"),
        "lasku-sahkotukku.pdf": ("2026-11-27", 3012.0, "30455"),
        "lasku-tyokaluvuokraus.pdf": ("2026-11-19", 702.8, "LTV-7731"),
    }
    for source, (due, total, number) in expected.items():
        assert rows[source]["due_date"] == due
        assert float(rows[source]["total"]) == total
        assert rows[source]["invoice_number"] == number


def test_run_report_from_the_dictated_diary(client):
    fields = [
        FieldDef(name="date", label="Date", type="date", required=False, description="Diary date."),
        FieldDef(
            name="weather", label="Weather", type="text", required=False, description="The weather."
        ),
        FieldDef(
            name="work_done",
            label="Work done",
            type="longtext",
            required=True,
            description="What was done on the site today.",
        ),
    ]
    spec = spec_for(
        "audio_to_report", title="Site diary", requirement="saneltu työmaapäiväkirja", fields=fields
    )
    result = run(client, spec, sample_id="site-diary")
    print("  transcript:", result["transcript"][:300])
    print(json.dumps(result["fields"], ensure_ascii=False, indent=1))
    assert len(result["transcript"]) > 100
    assert next(item for item in result["fields"] if item["name"] == "work_done")["value"]
    assert client.get(result["file"]["url"]).content.startswith(b"PK")


def test_run_answers_cites_the_page_and_knows_what_is_missing(client):
    spec = spec_for("documents_to_answers", title="Manual questions")
    found = run(
        client, spec, sample_id="manual", question="How often should the gear oil be changed?"
    )
    print(json.dumps(found, ensure_ascii=False, indent=1))
    assert found["found"] is True and "2000" in re.sub(r"[^0-9]", "", found["answer"])
    assert {source["page"] for source in found["sources"]} == {2}
    finnish = run(
        client, spec, sample_id="manual", question="Paljonko rahti maksaa pienestä tilauksesta?"
    )
    print(json.dumps(finnish, ensure_ascii=False, indent=1))
    assert finnish["found"] is True and "18" in finnish["answer"]
    assert {source["page"] for source in finnish["sources"]} == {1}
    missing = run(client, spec, sample_id="manual", question="How much does the pump weigh?")
    print(json.dumps(missing, ensure_ascii=False, indent=1))
    assert missing["found"] is False and missing["sources"] == []


def test_run_labels_on_the_emails(client):
    spec = spec_for(
        "documents_to_labels",
        title="Customer emails by topic",
        classes=[
            StudioClass(name="delivery", description="The customer asks where an order is"),
            StudioClass(name="invoice", description="A question or complaint about an invoice"),
            StudioClass(name="fault", description="A product is broken or works badly"),
            StudioClass(name="quote request", description="The customer asks for a price offer"),
        ],
    )
    result = run(client, spec, sample_id="emails")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    labels = {row["source"]: row["label"] for row in result["rows"]}
    assert labels == {
        "viesti-1-toimitus.txt": "delivery",
        "viesti-2-lasku.txt": "invoice",
        "viesti-3-vika.txt": "fault",
        "viesti-4-tarjous.txt": "quote request",
    }
    assert result["notice"] == "The description is also processed in the United States."
