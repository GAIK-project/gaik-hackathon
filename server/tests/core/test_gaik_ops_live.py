"""Live smoke tests of `core/gaik_ops.py`: real, small calls to Azure OpenAI and gaik-decide.

Run with `uv run pytest tests/core/test_gaik_ops_live.py -m live -s` to see the timings. The
sample files come from the public gaik-toolkit checkout next to this repository; a test that
needs one is skipped when it is not there. Everything else is synthetic text written here.
"""

from __future__ import annotations

import asyncio
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core import gaik_ops
from app.core.models import FieldDef, TracedFieldValue
from app.core.settings import REPO_ROOT, get_settings

pytestmark = pytest.mark.live

SAMPLES = REPO_ROOT.parent / "gaik-toolkit" / "implementation_layer" / "no-code-assets" / "samples"
CUSTOMER_PDFS = SAMPLES / "purchase-order-processing" / "sample_data" / "customer_data"
INCIDENT_AUDIO = SAMPLES / "incident-report-writing" / "Sample_1.m4a"


@pytest.fixture(autouse=True)
def needs_models():
    missing = get_settings().missing(
        "azure_api_key", "azure_endpoint", "azure_deployment", "gaik_decide_url"
    )
    if missing:
        pytest.skip(f"not configured: {', '.join(missing)}")


def report(line: str) -> None:
    print(f"\n[live] {line}")


# parse_document ------------------------------------------------------------------------------


async def test_parse_the_purchase_order_pdfs():
    paths = sorted(CUSTOMER_PDFS.glob("*.pdf"))
    if not paths:
        pytest.skip("the gaik-toolkit sample PDFs are not on this machine")
    import pymupdf

    for path in paths:
        document = await gaik_ops.parse_document(path)
        with pymupdf.open(path) as pdf:
            page_count = pdf.page_count
        assert document.kind == "pdf"
        assert len(document.pages) == page_count
        assert len(document.text) > 200
        assert "[Page" not in document.text
        report(
            f"parse_document {path.name}: {document.ms} ms, {page_count} page(s), "
            f"{len(document.text)} characters"
        )
    names = {path.name for path in paths}
    assert {"PO.pdf", "BOM1.pdf"} <= names


# transcribe_audio ----------------------------------------------------------------------------


async def test_transcribe_the_incident_recording(tmp_path):
    if not INCIDENT_AUDIO.is_file():
        pytest.skip("the gaik-toolkit sample recording is not on this machine")
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")
    wav = tmp_path / "incident.wav"
    # Section 15.4: uploads are converted with the detected input format before transcription.
    await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "mp4", "-i", str(INCIDENT_AUDIO)]
        + ["-ar", "16000", "-ac", "1", str(wav)],
        check=True,
        timeout=120,
    )
    transcript = await gaik_ops.transcribe_audio(wav, "en")

    assert len(transcript.text.split()) > 20
    assert transcript.duration_s == pytest.approx(29.2, abs=1.0)
    assert transcript.model == get_settings().azure_transcription_model
    assert not list(Path.cwd().glob("transcriber_workspace"))
    report(
        f"transcribe_audio Sample_1 ({transcript.duration_s} s of audio, {transcript.model}): "
        f"{transcript.ms} ms, {len(transcript.text.split())} words: {transcript.text[:160]!r}"
    )


# generate_fields -----------------------------------------------------------------------------

REQUIREMENT = (
    "Haluan kerätä huoltokäynnistä asiakkaan nimen, käyntipäivän, saapumisen kellonajan, "
    "tehdyt työt ja työhön kuluneet tunnit. Lisäksi tarvitaan tieto siitä, jäikö laite "
    "käyttökuntoon (kyllä tai ei)."
)


async def test_generate_fields_from_a_finnish_requirement():
    fields, ms = await gaik_ops.timed(gaik_ops.generate_fields(REQUIREMENT))

    assert 5 <= len(fields) <= 10
    names = [field.name for field in fields]
    assert len(set(names)) == len(names)
    for field in fields:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", field.name), field.name
        assert not re.search(r"(^|_)[a-z](_|$)", field.name), f"broken key {field.name}"
        assert field.label and field.label[0].isupper() and not field.label.endswith((":", "."))
        assert field.description
        assert (field.options is not None) == (field.type == "choice")
    types = [field.type for field in fields]
    assert "date" in types and "number" in types and "choice" in types
    choice = next(field for field in fields if field.type == "choice")
    # The requirement spells the answers out in Finnish; without that the default is yes/no.
    assert [option.lower() for option in choice.options] in (["yes", "no"], ["kyllä", "ei"])
    report(
        f"generate_fields: {ms} ms -> "
        + ", ".join(
            f"{field.name}:{field.type}{'*' if field.required else ''} ({field.label})"
            for field in fields
        )
    )


# extract_fields ------------------------------------------------------------------------------

TRANSCRIPT = (
    "No niin, täällä Mikko Lahtinen Äänekosken tehtaalta. Hallissa kolme on öljyvuoto "
    "trukkikäytävällä, huomasin sen 12.11.2026 kello 7.30. Lattialla on noin kaksi litraa "
    "hydrauliöljyä ja alue on eristetty lippusiimalla. Paikalla oli minun lisäkseni kaksi "
    "asentajaa. Tämä on kiireellinen, koska joku voi liukastua."
)

FIELDS = [
    FieldDef(
        name="ilmoittaja",
        label="Ilmoittaja",
        type="text",
        required=True,
        description="Havainnon tekijän nimi.",
    ),
    FieldDef(
        name="paikka",
        label="Paikka",
        type="text",
        required=True,
        description="Missä havainto tehtiin.",
    ),
    FieldDef(
        name="päivä",
        label="Päivä",
        type="date",
        required=True,
        description="Päivä, jona havainto tehtiin.",
    ),
    FieldDef(
        name="kellonaika",
        label="Kellonaika",
        type="time",
        required=False,
        description="Kellonaika, jolloin havainto tehtiin.",
    ),
    FieldDef(
        name="kuvaus",
        label="Kuvaus",
        type="longtext",
        required=True,
        description="Mitä havaittiin ja mitä on jo tehty.",
    ),
    FieldDef(
        name="kiireellisyys",
        label="Kiireellisyys",
        type="choice",
        required=True,
        description="Kuinka kiireellinen asia on.",
        options=["kiireellinen", "normaali", "ei kiireellinen"],
    ),
    FieldDef(
        name="sääolosuhteet",
        label="Sääolosuhteet",
        type="text",
        required=False,
        description="Sää havainnon aikaan, jos se mainitaan.",
    ),
]


async def test_extract_fields_from_a_finnish_transcript_with_evidence():
    values = await gaik_ops.extract_fields(
        TRANSCRIPT, FIELDS, "Poimi puhelusta turvallisuushavainnon tiedot."
    )
    timing = gaik_ops.last_timing()
    by_name = {value.name: value for value in values}

    assert list(by_name) == [field.name for field in FIELDS]
    assert all(isinstance(value, TracedFieldValue) for value in values)
    assert "Lahtinen" in by_name["ilmoittaja"].value
    assert "3" in by_name["paikka"].value or "kolme" in by_name["paikka"].value.lower()
    assert by_name["päivä"].value == "2026-11-12"
    assert by_name["kellonaika"].value == "07:30"
    assert by_name["kiireellisyys"].value == "kiireellinen"
    assert "öljy" in by_name["kuvaus"].value.lower()
    assert by_name["sääolosuhteet"].value is None  # the transcript does not say
    assert by_name["sääolosuhteet"].evidence is None

    quoted = [value for value in values if value.evidence]
    assert len(quoted) >= 5
    for value in quoted:
        assert value.evidence in TRANSCRIPT  # verbatim, not the model's wording
        assert value.verdict in ("verified", "reworded", "unchecked", "check")
    assert by_name["ilmoittaja"].verdict == "verified"
    assert by_name["päivä"].verdict == "verified"
    report(
        f"extract_fields: {timing.ms} ms -> "
        + "; ".join(f"{v.name}={v.value!r} [{v.verdict}: {v.evidence!r}]" for v in values)
    )


# classification ------------------------------------------------------------------------------

LABELS = {
    "kiireellinen": "Välitön vaara ihmisille, ympäristölle tai tuotannolle; vaatii toimia heti.",
    "normaali": "Hoidettava lähipäivinä, ei välitöntä vaaraa.",
    "ei kiireellinen": "Parannusehdotus tai huomio, jolla ei ole aikarajaa.",
}
QUESTION = "Kuinka kiireellinen ilmoitettu asia on?"
URGENT = "Hallissa 3 on öljyvuoto trukkikäytävällä ja joku voi liukastua. Alue on eristetty."
CALM = "Taukotilan ilmoitustaululle voisi joskus hankkia uudet magneetit, vanhat ovat kuluneet."


@pytest.mark.parametrize("name", ["classify_jev", "classify_azure"])
async def test_classify_urgency(name):
    classify = getattr(gaik_ops, name)
    for text, expected in ((URGENT, "kiireellinen"), (CALM, "ei kiireellinen")):
        result = await classify(text, LABELS, QUESTION)
        assert result.label == expected
        assert set(result.probabilities) == set(LABELS)
        assert 0.5 < result.confidence <= 1.0
        assert max(result.probabilities, key=result.probabilities.get) == expected
        total = sum(result.probabilities.values())
        if name == "classify_azure":
            assert total == pytest.approx(1.0, abs=1e-6)
            assert result.model == get_settings().azure_deployment
        else:
            assert total == pytest.approx(1.0, abs=0.05)  # gaik-decide rounds to two decimals
        report(
            f"{name} -> {result.label} ({result.confidence:.2f}) in {result.ms} ms, "
            f"{result.model}, {result.probabilities}"
        )


# embed_texts ---------------------------------------------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def test_embed_three_texts():
    texts = [
        "Hallissa 3 on öljyvuoto trukkikäytävällä.",
        "Trukkien kulkureitillä on öljyä lattialla.",
        "Tekoälyasetus jakaa järjestelmät riskiluokkiin.",
    ]
    vectors, ms = await gaik_ops.timed(gaik_ops.embed_texts(texts))

    assert len(vectors) == 3
    assert len({len(vector) for vector in vectors}) == 1 and len(vectors[0]) >= 256
    near, far = cosine(vectors[0], vectors[1]), cosine(vectors[0], vectors[2])
    assert near > far
    report(
        f"embed_texts: {ms} ms, 3 x {len(vectors[0])} dimensions "
        f"({get_settings().embedding_model}); cosine near {near:.3f}, far {far:.3f}"
    )
