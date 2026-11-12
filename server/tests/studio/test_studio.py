"""Studio without network: samples, catalog, generate, the four runs and export, with fakes
for every model call (documents are parsed for real: that is local)."""

from __future__ import annotations

import ast
import io
import json
from pathlib import Path
from typing import Any

import pytest

from app.core import files, gaik_ops, llm
from app.core.errors import AppError
from app.core.models import Classification, FieldDef, TracedFieldValue, Transcript
from app.studio import catalog, export, generate, samples
from app.studio import run as runner
from app.studio.models import (
    NOTICE_PERSONAL_DATA,
    NOTICE_UNITED_STATES,
    StudioClass,
    StudioSpec,
)

FIELDS = [
    FieldDef(name="supplier", label="Supplier", type="text", required=True, description="Seller."),
    FieldDef(name="total", label="Total", type="number", required=True, description="With VAT."),
]


def make_spec(template: str, **changes: Any) -> StudioSpec:
    values: dict[str, Any] = {
        "id": "stu_test",
        "title": "Test workflow",
        "summary": "Things in, things out.",
        "requirement": "ostolaskut taulukkoon",
        "template": template,
        "input": catalog.input_for(template),
        "steps": catalog.steps_for(template),
        "fields": FIELDS if template in ("documents_to_table", "audio_to_report") else [],
        "classes": [
            StudioClass(name="delivery", description="Where is my order"),
            StudioClass(name="invoice", description="A billing question"),
        ]
        if template == "documents_to_labels"
        else [],
        "question_examples": ["How often is the oil changed?"]
        if template == "documents_to_answers"
        else [],
        "output": catalog.output_for(template),
        "notices": catalog.notices_for(template),
    }
    values.update(changes)
    return StudioSpec(**values)


def sse_events(text: str) -> list[tuple[str, Any]]:
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


def run_request(client, spec: StudioSpec, **form: str):
    return client.post(
        "/api/studio/run",
        data={"spec": spec.model_dump_json(), **form},
        files={"_": ("", b"")} if False else None,
    )


# samples and catalog ------------------------------------------------------------------------


def test_samples_are_listed_and_served(client):
    listed = client.get("/api/studio/samples").json()
    by_id = {item["id"]: item for item in listed}
    assert set(by_id) == {"invoices", "manual", "emails", "site-diary"}
    assert len(by_id["invoices"]["files"]) == 3
    assert len(by_id["emails"]["files"]) == 4
    assert by_id["site-diary"]["kind"] == "audio"
    first = by_id["invoices"]["files"][0]
    response = client.get(first["url"])
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert len(response.content) == first["size"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]


def test_sample_file_names_are_looked_up_not_joined(client):
    assert client.get("/api/studio/samples/invoices/vp-200-manual.pdf").status_code == 404
    assert client.get("/api/studio/samples/nope/x.pdf").status_code == 404
    assert client.get("/api/studio/samples/invoices/..%2Fbuild_samples.py").status_code == 404


def test_manual_has_three_pages_with_text():
    import pymupdf

    with pymupdf.open(samples.get_sample("manual").paths()[0]) as document:
        pages = [page.get_text() for page in document]
    assert len(pages) == 3
    assert "Delivery terms" in pages[0] and "2 000 operating hours" in pages[1]


def test_catalog(client):
    body = client.get("/api/studio/catalog").json()
    assert {item["id"] for item in body["templates"]} == set(catalog.TEMPLATE_INFO)
    for component in body["components"]:
        assert set(component) == {"id", "title", "purpose", "gaik_import", "extra"}
    table = next(item for item in body["templates"] if item["id"] == "documents_to_table")
    assert table["steps"] == ["parse", "extract"]


def test_studio_needs_a_session(anon_client):
    assert anon_client.get("/api/studio/catalog").status_code == 401
    assert anon_client.post("/api/studio/generate", json={"prompt": "x"}).status_code == 401


# generate -----------------------------------------------------------------------------------


@pytest.fixture
def fast_steps(monkeypatch):
    monkeypatch.setattr(generate, "STEP_DELAY_SECONDS", 0)


def fake_chat(answers: list[Any], calls: list[str]):
    async def structured_chat(system, user, schema_model, **options):
        calls.append(user)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return structured_chat


def test_generate_streams_steps_fields_and_spec(client, monkeypatch, fast_steps):
    calls: list[str] = []
    draft = generate.SpecDraft(
        template="documents_to_table",
        title="Invoices to a table",
        summary="Purchase invoices in, a table out.",
        field_requirement="Collect the supplier and the total amount with VAT.",
    )
    english = generate._EnglishFields(
        fields=[
            generate._EnglishField(name="supplier", label="Supplier name", description="Who."),
            generate._EnglishField(name="total", label="Total amount", description="With VAT."),
        ]
    )
    monkeypatch.setattr(llm, "structured_chat", fake_chat([draft, english], calls))

    async def generate_fields(requirement: str) -> list[FieldDef]:
        assert "supplier" in requirement
        return FIELDS

    monkeypatch.setattr(gaik_ops, "generate_fields", generate_fields)
    response = client.post("/api/studio/generate", json={"prompt": "ostolaskut taulukkoon"})
    assert response.status_code == 200
    events = sse_events(response.text)
    names = [name for name, _ in events]
    assert names[0] == "status" and names[-1] == "done"
    assert [data["step"]["id"] for name, data in events if name == "step"] == ["parse", "extract"]
    assert names.index("fields") < names.index("spec")
    spec = StudioSpec.model_validate(dict(events)["spec"]["spec"])
    assert spec.template == "documents_to_table"
    assert spec.requirement == "ostolaskut taulukkoon"
    assert [field.label for field in spec.fields] == ["Supplier name", "Total amount"]
    assert spec.input.sample_ids == ["invoices"] and spec.output.kind == "table"
    assert spec.notices == [NOTICE_PERSONAL_DATA]


def test_generate_labels_repairs_once(client, monkeypatch, fast_steps):
    calls: list[str] = []
    bad = generate.SpecDraft(
        template="documents_to_labels",
        title="Sort emails",
        summary="Emails in.",
        field_requirement="",
    )
    good = bad.model_copy(
        update={
            "classes": [
                StudioClass(name="delivery", description="Where is it"),
                StudioClass(name="invoice", description="Billing"),
            ]
        }
    )
    monkeypatch.setattr(llm, "structured_chat", fake_chat([bad, good], calls))
    events = sse_events(client.post("/api/studio/generate", json={"prompt": "sort mail"}).text)
    assert len(calls) == 2 and "refused" in calls[1]
    spec = StudioSpec.model_validate(dict(events)["spec"]["spec"])
    assert [item.name for item in spec.classes] == ["delivery", "invoice"]
    assert dict(events)["classes"]["classes"][0]["name"] == "delivery"
    assert spec.notices == [NOTICE_PERSONAL_DATA, NOTICE_UNITED_STATES]
    assert spec.fields == []


def test_generate_refuses_after_a_failed_repair(client, monkeypatch, fast_steps):
    bad = generate.SpecDraft(
        template="documents_to_answers", title="", summary="", field_requirement=""
    )
    monkeypatch.setattr(llm, "structured_chat", fake_chat([bad, bad], []))
    events = sse_events(client.post("/api/studio/generate", json={"prompt": "hm"}).text)
    assert ("spec" not in dict(events)) and dict(events)["error"]["code"] == "spec_invalid"
    assert events[-1][0] == "done"


# run ----------------------------------------------------------------------------------------


def step_events(events: list[tuple[str, Any]]) -> list[tuple[str, str]]:
    return [(data["id"], data["status"]) for name, data in events if name == "step"]


def test_run_table_on_the_invoice_sample(client, monkeypatch):
    seen: list[str] = []

    async def extract_fields(text, fields, requirement, *, trace=True):
        assert trace is False
        seen.append(text)
        total = "1240.00" if "2026-1041" in text else "0"
        return [
            TracedFieldValue(
                name="supplier", label="Supplier", value="X Oy", evidence=None, source="extractor"
            ),
            TracedFieldValue(
                name="total", label="Total", value=total, evidence=None, source="extractor"
            ),
        ]

    monkeypatch.setattr(gaik_ops, "extract_fields", extract_fields)
    response = run_request(client, make_spec("documents_to_table"), sample_id="invoices")
    events = sse_events(response.text)
    assert step_events(events) == [
        ("parse", "running"),
        ("parse", "done"),
        ("extract", "running"),
        ("extract", "done"),
    ]
    assert all(
        isinstance(data["ms"], int)
        for name, data in events
        if name == "step" and data["ms"] is not None
    )
    result = dict(events)["result"]
    assert result["kind"] == "table"
    assert [column["name"] for column in result["columns"]] == ["supplier", "total"]
    assert [row["source"] for row in result["rows"]] == list(samples.get_sample("invoices").names)
    assert result["rows"][0]["values"]["total"] == "1240.00"
    assert len(seen) == 3 and any("Maksettava yhteensä" in text for text in seen)
    assert events[-1][0] == "done"


def test_run_table_with_an_upload_and_a_bad_upload(client, monkeypatch):
    async def extract_fields(text, fields, requirement, *, trace=True):
        return [
            TracedFieldValue(
                name="supplier", label="Supplier", value=text[:5], evidence=None, source="extractor"
            )
        ]

    monkeypatch.setattr(gaik_ops, "extract_fields", extract_fields)
    spec = make_spec("documents_to_table")
    response = client.post(
        "/api/studio/run",
        data={"spec": spec.model_dump_json()},
        files=[("files[]", ("../../Oma lasku.txt", io.BytesIO(b"Hello invoice"), "text/plain"))],
    )
    result = dict(sse_events(response.text))["result"]
    assert result["rows"] == [
        {"source": "Oma_lasku.txt", "values": {"supplier": "Hello", "total": None}}
    ]
    refused = client.post(
        "/api/studio/run",
        data={"spec": spec.model_dump_json()},
        files=[("files", ("x.pdf", io.BytesIO(b"MZ\x00\x01binary"), "application/pdf"))],
    )
    assert refused.status_code == 415


def test_run_needs_a_valid_spec_and_an_input(client):
    assert client.post("/api/studio/run", data={"spec": "{}"}).status_code == 422
    spec = make_spec("documents_to_table")
    assert client.post("/api/studio/run", data={"spec": spec.model_dump_json()}).status_code == 400
    wrong = run_request(client, spec, sample_id="site-diary")
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "sample_mismatch"


def test_run_report_writes_a_word_file(client, monkeypatch, tmp_path):
    async def to_wav(source, audio_format=None, target=None):
        assert target is not None
        Path(target).write_bytes(b"RIFF")  # noqa: ASYNC240
        return files.ConvertedAudio(path=Path(target), duration_s=42.0)

    async def transcribe_audio(path, language="fi"):
        assert language == "fi"
        return Transcript(
            text="Tänään valettiin laatta.", duration_s=42.0, language="fi", model="m"
        )

    async def extract_fields(text, fields, requirement, *, trace=True):
        assert trace is True
        return [
            TracedFieldValue(
                name="supplier",
                label="Supplier",
                value="laatta",
                evidence="valettiin laatta",
                source="extractor",
                verdict="verified",
            ),
            TracedFieldValue(
                name="total", label="Total", value=None, evidence=None, source="extractor"
            ),
        ]

    monkeypatch.setattr(files, "to_wav", to_wav)
    monkeypatch.setattr(gaik_ops, "transcribe_audio", transcribe_audio)
    monkeypatch.setattr(gaik_ops, "extract_fields", extract_fields)
    response = run_request(client, make_spec("audio_to_report"), sample_id="site-diary")
    events = sse_events(response.text)
    assert [step for step, status in step_events(events) if status == "done"] == [
        "convert",
        "transcribe",
        "extract",
        "document",
    ]
    result = dict(events)["result"]
    assert result["kind"] == "report" and result["transcript"] == "Tänään valettiin laatta."
    assert result["fields"][0] == {
        "name": "supplier",
        "label": "Supplier",
        "value": "laatta",
        "evidence": "valettiin laatta",
        "source": "extractor",
    }
    download = client.get(result["file"]["url"])
    assert download.status_code == 200 and download.content.startswith(b"PK")


def answer_fakes(monkeypatch, answer: runner._Answer, calls: list[str]):
    async def embed_texts(texts):
        # The page that shares the most words with the question (the last text) wins.
        words = set(texts[-1].lower().split())
        return [[float(len(words & set(text.lower().split()))), 1.0] for text in texts]

    async def structured_chat(system, user, schema_model, **options):
        calls.append(user)
        return answer

    monkeypatch.setattr(gaik_ops, "embed_texts", embed_texts)
    monkeypatch.setattr(llm, "structured_chat", structured_chat)


def test_run_answers_cites_the_page_and_checks_the_quote(client, monkeypatch):
    calls: list[str] = []
    answer = runner._Answer(
        found=True,
        answer="After every 2 000 operating hours or once a year.",
        quotes=[
            runner._Quote(passage=1, quote="Change the gear oil after every 2 000 operating hours"),
            runner._Quote(passage=1, quote="This sentence is not in the manual at all"),
            runner._Quote(passage=9, quote="Change the gear oil"),
        ],
    )
    answer_fakes(monkeypatch, answer, calls)
    response = run_request(
        client,
        make_spec("documents_to_answers"),
        sample_id="manual",
        question="How often to change the gear oil after operating hours?",
    )
    events = sse_events(response.text)
    assert [step for step, status in step_events(events) if status == "done"] == [
        "parse",
        "embed",
        "answer",
    ]
    result = dict(events)["result"]
    assert result["found"] is True
    assert result["sources"] == [
        {
            "title": "vp-200-manual.pdf",
            "page": 2,
            "quote": "Change the gear oil after every 2 000 operating hours",
        }
    ]
    assert "[Passage 1] vp-200-manual.pdf, page 2" in calls[0]


def test_run_answers_says_not_found(client, monkeypatch):
    answer_fakes(monkeypatch, runner._Answer(found=False, answer="", quotes=[]), [])
    spec = make_spec("documents_to_answers")
    result = dict(
        sse_events(
            run_request(client, spec, sample_id="manual", question="How much does it weigh?").text
        )
    )["result"]
    assert result["found"] is False and result["sources"] == []
    assert result["answer"] == runner.NOT_FOUND_ANSWER
    # An answer whose quotes are not in the pages is not given either.
    made_up = runner._Answer(
        found=True,
        answer="12 kg.",
        quotes=[runner._Quote(passage=1, quote="The pump weighs 12 kg")],
    )
    answer_fakes(monkeypatch, made_up, [])
    result = dict(
        sse_events(
            run_request(client, spec, sample_id="manual", question="How much does it weigh?").text
        )
    )["result"]
    assert result["found"] is False and result["answer"] == runner.NOT_FOUND_ANSWER
    missing = run_request(client, spec, sample_id="manual")
    assert dict(sse_events(missing.text))["error"]["code"] == "no_question"


def test_quote_check_normalises_marks_and_spaces():
    page = runner.Passage("m.pdf", 3, "The warranty is 24 months – from the\ndelivery date.")
    quotes = [
        runner._Quote(passage=1, quote='"The warranty is 24 months - from the delivery date."')
    ]
    assert [source.page for source in runner.verified_sources(quotes, [page])] == [3]


def test_run_labels_caps_the_text_and_returns_the_notice(client, monkeypatch):
    seen: list[tuple[str, dict[str, str]]] = []

    async def classify_jev(text, labels, instructions=None):
        seen.append((text, labels))
        label = "invoice" if "lasku" in text.lower() else "delivery"
        other = "delivery" if label == "invoice" else "invoice"
        return Classification(
            label=label, confidence=0.9, probabilities={label: 0.9, other: 0.1}, model="jev"
        )

    monkeypatch.setattr(gaik_ops, "classify_jev", classify_jev)
    spec = make_spec("documents_to_labels")
    response = client.post(
        "/api/studio/run",
        data={"spec": spec.model_dump_json()},
        files=[
            ("files", ("pitka.txt", io.BytesIO(("lasku " * 1000).encode()), "text/plain")),
            ("files", ("lyhyt.txt", io.BytesIO(b"Missa pakettini on?"), "text/plain")),
        ],
    )
    result = dict(sse_events(response.text))["result"]
    assert result["notice"] == NOTICE_UNITED_STATES
    assert [(row["source"], row["label"]) for row in result["rows"]] == [
        ("pitka.txt", "invoice"),
        ("lyhyt.txt", "delivery"),
    ]
    assert max(len(text) for text, _ in seen) <= 2000
    assert seen[0][1] == {"delivery": "Where is my order", "invoice": "A billing question"}


def test_a_failing_step_is_reported_before_the_error(client, monkeypatch):
    async def extract_fields(text, fields, requirement, *, trace=True):
        raise AppError("extraction_failed", "Extraction failed.", 502)

    monkeypatch.setattr(gaik_ops, "extract_fields", extract_fields)
    events = sse_events(
        run_request(client, make_spec("documents_to_table"), sample_id="invoices").text
    )
    assert step_events(events)[-1] == ("extract", "error")
    assert dict(events)["error"]["code"] == "extraction_failed" and "result" not in dict(events)


# export -------------------------------------------------------------------------------------

HOSTILE = 'x"""\nimport os\nos.system("echo pwned")\n\'\'\'\n\\'


def _strings(tree: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


@pytest.mark.parametrize("template", list(catalog.TEMPLATE_INFO))
def test_export_holds_the_spec_only_as_one_data_literal(template):
    plain = make_spec(template)
    hostile = make_spec(
        template,
        title=HOSTILE,
        summary=HOSTILE,
        requirement=HOSTILE,
        fields=[
            field.model_copy(update={"description": HOSTILE, "label": HOSTILE}) for field in FIELDS
        ]
        if plain.fields
        else [],
        classes=[StudioClass(name=HOSTILE[:40], description=HOSTILE), *plain.classes]
        if plain.classes
        else [],
        question_examples=[HOSTILE] if plain.question_examples else [],
    )
    plain_tree = ast.parse(export.python_script(plain))
    hostile_script = export.python_script(hostile)
    hostile_tree = ast.parse(hostile_script)

    # The code is the same whatever the spec says: only one string constant differs.
    literal = json.dumps(hostile.model_dump(mode="json"), ensure_ascii=True, sort_keys=True)
    plain_literal = json.dumps(plain.model_dump(mode="json"), ensure_ascii=True, sort_keys=True)
    assert _strings(hostile_tree).count(literal) == 1
    without = ast.dump(hostile_tree).replace(repr(literal), "SPEC")
    assert without == ast.dump(plain_tree).replace(repr(plain_literal), "SPEC")

    # That constant is the argument of `SPEC = json.loads(...)`, and it round-trips.
    assigns = [
        node
        for node in hostile_tree.body
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SPEC"
    ]
    assert len(assigns) == 1
    call = assigns[0].value
    assert isinstance(call, ast.Call) and ast.unparse(call.func) == "json.loads"
    assert len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
    assert json.loads(call.args[0].value)["title"] == HOSTILE
    # No string of the hostile text stands anywhere else, and nothing imports from it.
    assert all("pwned" not in text for text in _strings(hostile_tree) if text != literal)
    top_imports = {
        alias.name
        for node in hostile_tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert top_imports == {"json", "sys"}


def test_export_endpoint_returns_script_and_blueprint(client):
    spec = make_spec("documents_to_labels")
    body = client.post("/api/studio/export", json={"spec": spec.model_dump(mode="json")}).json()
    compile(body["python"], "studio_workflow.py", "exec")
    blueprint = body["blueprint"]
    assert blueprint["use_case"]["name"] == "Test workflow"
    steps = blueprint["workflow"]["steps"]
    assert len({step["id"] for step in steps}) == len(steps)
    artifacts = blueprint["artifacts"]
    produced: set[str] = set()
    for step in steps:  # every input exists and was produced earlier (or is an upload)
        for name in step["inputs"]:
            assert name in artifacts and name in produced
        for name in step["outputs"]:
            assert name in artifacts
            produced.add(name)
            if artifacts[name]["source"] == "generated":
                assert artifacts[name]["produced_by"] == step["id"]
    assert any(item.get("final_output") for item in artifacts.values())
    classify = next(step for step in steps if step["id"] == "classify")
    assert classify["component"] == "JevClassifier"
    assert classify["parameters"]["classes"] == ["delivery", "invoice"]
    assert "KEY" not in json.dumps(blueprint["models"])


@pytest.mark.parametrize("template", list(catalog.TEMPLATE_INFO))
def test_blueprint_is_the_same_for_the_same_spec(template):
    spec = make_spec(template)
    assert export.blueprint(spec) == export.blueprint(spec)
    assert export.python_script(spec) == export.python_script(spec)
