"""Offline tests of `core/gaik_ops.py`: every model call is a fake, nothing leaves the machine.

The parsers, the schema builder (`create_extraction_model`) and SourceTracing's quote check are
gaik's real code: they make no model call. All content is synthetic.
"""

from __future__ import annotations

import socket
import threading
import time
import warnings
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from app.core import gaik_ops, llm
from app.core.errors import AppError
from app.core.models import (
    Classification,
    FieldDef,
    FieldValue,
    ParsedDocument,
    TracedFieldValue,
    Transcript,
)
from app.core.settings import get_settings

FAKE_KEY = "fake-azure-key-0123456789"


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    """Model settings that point nowhere, and a guard that fails the test on a name lookup."""
    monkeypatch.setenv("AZURE_API_KEY", FAKE_KEY)
    monkeypatch.setenv("AZURE_ENDPOINT", "https://fake-resource.invalid")
    monkeypatch.setenv("AZURE_API_VERSION", "2025-03-01-preview")
    monkeypatch.setenv("AZURE_DEPLOYMENT", "fake-chat")
    monkeypatch.setenv("AZURE_TRANSCRIPTION_MODEL", "fake-transcribe")
    monkeypatch.setenv("EMBEDDING_MODEL", "fake-embedding")
    monkeypatch.setenv("GAIK_DECIDE_URL", "https://decide.invalid")
    monkeypatch.setenv("GAIK_DECIDE_API_KEY", "fake-decide-key")
    get_settings.cache_clear()

    lookups: list[str] = []
    real_getaddrinfo = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        if host not in ("localhost", "127.0.0.1", "::1", None):
            lookups.append(str(host))
            raise OSError("no network in offline tests")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)
    yield
    assert lookups == [], f"an offline test tried to reach {lookups}"


def in_worker_thread() -> bool:
    return threading.current_thread() is not threading.main_thread()


# sampling parameters -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("deployment", "effort", "expected"),
    [
        ("gpt-4.1", "none", {"temperature": 0.0, "reasoning_effort": None}),
        ("my-own-name", "low", {"temperature": 0.0, "reasoning_effort": None}),
        ("gpt-5.6-sol", "none", {"temperature": 0.0, "reasoning_effort": "none"}),
        ("gpt-5.6-sol", "low", {"temperature": None, "reasoning_effort": "low"}),
        ("gpt-6-luna", "none", {"temperature": 0.0, "reasoning_effort": "none"}),
        ("gpt-6-astra", "none", {"temperature": None, "reasoning_effort": "low"}),
        ("gpt-6.1-sol", "none", {"temperature": None, "reasoning_effort": "low"}),
        ("gpt-5.1", "none", {"temperature": None, "reasoning_effort": "low"}),
        ("azure/GPT-5-mini", "none", {"temperature": None, "reasoning_effort": "low"}),
        ("o4-mini", "none", {"temperature": None, "reasoning_effort": "low"}),
    ],
)
def test_sampling_follows_the_deployment_name(deployment, effort, expected):
    assert gaik_ops._sampling(deployment, effort) == expected
    assert gaik_ops._chat_options(deployment, effort) == {
        key: value for key, value in expected.items() if value is not None
    }


def test_gaik_accepts_the_sampling_of_every_family():
    """gaik validates the effort for the gpt-6 family before any request."""
    from gaik.software_components.llm.parameters import normalize_chat_kwargs

    for deployment in ("gpt-4.1", "gpt-5.6-sol", "gpt-6-luna", "gpt-6-astra", "gpt-6.1-sol"):
        for effort in ("none", "low"):
            options = gaik_ops._chat_options(deployment, effort)
            sent = normalize_chat_kwargs(deployment, {"top_p": 1.0, **options})
            if sent.get("reasoning_effort") not in (None, "none"):
                assert "temperature" not in sent


# parse_document ------------------------------------------------------------------------------


def make_pdf(path: Path, pages: list[str]) -> Path:
    import pymupdf

    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 96), text, fontsize=11)
    document.save(path)
    document.close()
    return path


def make_docx(path: Path) -> Path:
    import docx

    document = docx.Document()
    document.add_heading("Huoltoraportti", level=1)
    document.add_paragraph("Kohde: Äänekosken halli 3.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Osa"
    table.cell(0, 1).text = "Määrä"
    table.cell(1, 0).text = "Öljynsuodatin"
    table.cell(1, 1).text = "2"
    document.save(path)
    return path


async def test_parse_pdf_returns_every_page_in_order(tmp_path, capsys):
    path = make_pdf(
        tmp_path / "tilaus.pdf",
        ["Tilaus 1001\nÄänekosken Konepaja", "", "Rivi 1: suodatin, 2 kpl\nYhteensä 84,50"],
    )
    document = await gaik_ops.parse_document(path)

    assert isinstance(document, ParsedDocument)
    assert document.kind == "pdf"
    assert len(document.pages) == 3
    assert document.pages[0] == "Tilaus 1001\nÄänekosken Konepaja"
    assert document.pages[1] == ""
    assert "Yhteensä 84,50" in document.pages[2]
    assert document.text == f"{document.pages[0]}\n\n{document.pages[2]}"
    assert "[Page" not in document.text
    assert document.ms >= 0
    assert capsys.readouterr().out == ""


async def test_parse_pdf_with_a_page_mark_in_its_own_text(tmp_path):
    path = make_pdf(tmp_path / "ansa.pdf", ["Johdanto\n[Page 2]\nlisää tekstiä", "Toinen sivu"])
    document = await gaik_ops.parse_document(path)
    assert document.pages == ["Johdanto\n[Page 2]\nlisää tekstiä", "Toinen sivu"]


async def test_parse_runs_in_a_worker_thread(tmp_path, monkeypatch):
    seen: list[bool] = []
    real = gaik_ops._parse_sync

    def spy(path):
        seen.append(in_worker_thread())
        return real(path)

    monkeypatch.setattr(gaik_ops, "_parse_sync", spy)
    await gaik_ops.parse_document(make_pdf(tmp_path / "a.pdf", ["Yksi sivu"]))
    assert seen == [True]


async def test_parse_docx_keeps_headings_and_tables(tmp_path):
    document = await gaik_ops.parse_document(make_docx(tmp_path / "raportti.docx"))
    assert document.kind == "docx"
    assert document.pages == [document.text]
    assert "# Huoltoraportti" in document.text
    assert "Äänekosken halli 3" in document.text
    assert "Öljynsuodatin" in document.text and "Määrä" in document.text


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("utf8.txt", "Työmaan päiväkirja\r\nSää: pouta".encode()),
        ("bom.txt", b"\xef\xbb\xbf" + "Työmaan päiväkirja\nSää: pouta".encode()),
        ("ansi.txt", "Työmaan päiväkirja\nSää: pouta".encode("cp1252")),
        ("utf16.txt", "Työmaan päiväkirja\nSää: pouta".encode("utf-16")),
        ("ilman-paatetta", "Työmaan päiväkirja\nSää: pouta".encode()),
    ],
)
async def test_parse_txt_in_the_encodings_finnish_files_come_in(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    document = await gaik_ops.parse_document(path)
    assert document.kind == "txt"
    assert document.text == "Työmaan päiväkirja\nSää: pouta"
    assert document.pages == [document.text]


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("kuva.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40),
        ("binaari.txt", b"abc\x00\x01\x02def"),
        ("taulukko.csv", b"a;b;c\n1;2;3\n"),
        ("paketti.docx", b"PK\x03\x04 not really a zip"),
    ],
)
async def test_parse_refuses_other_content(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    with pytest.raises(AppError) as raised:
        await gaik_ops.parse_document(path)
    assert (raised.value.status, raised.value.code) == (415, "unsupported_document")


async def test_parse_refuses_a_zip_that_is_not_a_word_file(tmp_path):
    import zipfile

    path = tmp_path / "muu.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "ei Word-tiedosto")
    with pytest.raises(AppError) as raised:
        await gaik_ops.parse_document(path)
    assert raised.value.code == "unsupported_document"


async def test_parse_errors_are_finnish_and_hold_no_path(tmp_path, caplog):
    missing = tmp_path / "puuttuu.pdf"
    with pytest.raises(AppError) as raised:
        await gaik_ops.parse_document(missing)
    assert (raised.value.status, raised.value.code) == (404, "file_not_found")

    scanned = make_pdf(tmp_path / "skannattu.pdf", ["", ""])
    with pytest.raises(AppError) as raised:
        await gaik_ops.parse_document(scanned)
    assert (raised.value.status, raised.value.code) == (422, "document_empty")
    assert raised.value.message == "Tiedostosta ei löytynyt tekstiä."

    broken = tmp_path / "rikki.pdf"
    broken.write_bytes(b"%PDF-1.7\nthis is not a pdf at all")
    with pytest.raises(AppError) as raised:
        await gaik_ops.parse_document(broken)
    assert (raised.value.status, raised.value.code) == (422, "document_unreadable")
    assert "rikki" not in raised.value.message and str(tmp_path) not in raised.value.message
    assert gaik_ops.last_timing() == gaik_ops.recent_timings()[-1]
    assert gaik_ops.last_timing().ok is False


# transcribe_audio ----------------------------------------------------------------------------


def make_wav(path: Path, seconds: float = 1.0) -> Path:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


class FakeTranscriber:
    """Stands in for gaik's Transcriber; records how it was built and called."""

    calls: list[dict[str, Any]] = []
    raw_transcript = "Hallissa kolme on öljyvuoto trukkikäytävällä."
    audio_duration_s: float | None = 12.5
    error: Exception | None = None

    def __init__(self, api_config, output_dir="transcriber_workspace", **options):
        self.record = {
            "config": api_config,
            "output_dir": Path(output_dir),
            "options": options,
            "worker_thread": in_worker_thread(),
        }
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    def transcribe(self, file_path, **kwargs):
        print("=" * 80)
        print("Transcribing job abc123 (.wav)")
        self.record["path"] = Path(file_path)
        self.record["output_dir_existed"] = self.record["output_dir"].is_dir()
        type(self).calls.append(self.record)
        if type(self).error is not None:
            raise type(self).error
        return SimpleNamespace(
            raw_transcript=type(self).raw_transcript,
            enhanced_transcript=None,
            audio_duration_s=type(self).audio_duration_s,
            model_used="fake-transcribe",
        )


@pytest.fixture
def fake_transcriber(monkeypatch):
    import gaik.software_components.transcriber as module

    FakeTranscriber.calls = []
    FakeTranscriber.raw_transcript = "Hallissa kolme on öljyvuoto trukkikäytävällä."
    FakeTranscriber.audio_duration_s = 12.5
    FakeTranscriber.error = None
    monkeypatch.setattr(module, "Transcriber", FakeTranscriber)
    return FakeTranscriber


async def test_transcribe_uses_a_temporary_folder_and_a_finnish_prompt(
    tmp_path, monkeypatch, capsys, fake_transcriber
):
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    audio = make_wav(tmp_path / "viesti.wav")

    transcript = await gaik_ops.transcribe_audio(audio)

    assert isinstance(transcript, Transcript)
    assert transcript.text == "Hallissa kolme on öljyvuoto trukkikäytävällä."
    assert transcript.duration_s == 12.5
    assert (transcript.language, transcript.model) == ("fi", "fake-transcribe")
    assert transcript.ms >= 0

    (call,) = fake_transcriber.calls
    assert call["worker_thread"] is True
    assert call["path"] == audio
    assert call["output_dir_existed"] is True
    assert not call["output_dir"].exists()  # removed afterwards
    assert workdir not in call["output_dir"].parents
    assert list(workdir.iterdir()) == []  # nothing written into the current directory
    assert "suomenkielinen" in call["options"]["default_prompt"]
    assert call["config"]["timeout"] == 120  # not gaik's 30 s
    assert call["config"]["transcription_model"] == "fake-transcribe"
    assert capsys.readouterr().out == ""  # gaik's prints stay out of stdout


async def test_transcribe_language_goes_into_the_prompt(tmp_path, fake_transcriber):
    audio = make_wav(tmp_path / "note.wav")
    await gaik_ops.transcribe_audio(audio, "en")
    await gaik_ops.transcribe_audio(audio, "auto")
    english, automatic = fake_transcriber.calls
    assert "English" in english["options"]["default_prompt"]
    assert "default_prompt" not in automatic["options"]  # gaik's own "detect the language"


async def test_transcribe_reads_the_duration_from_the_wav_when_gaik_cannot(
    tmp_path, fake_transcriber
):
    fake_transcriber.audio_duration_s = None
    transcript = await gaik_ops.transcribe_audio(make_wav(tmp_path / "puoli.wav", 0.5))
    assert transcript.duration_s == 0.5


async def test_transcribe_cleans_the_marks_of_a_split_recording(tmp_path, fake_transcriber):
    fake_transcriber.raw_transcript = (
        "\n[Timestamp: 00:00 - 20:00]\nEnsimmäinen osa.\n\n"
        "\n[Timestamp: 20:00 - 40:00]\n[Transcription failed for segment 2]\n\n"
        "\n[Timestamp: 40:00 - 60:00]\nKolmas osa."
    )
    transcript = await gaik_ops.transcribe_audio(make_wav(tmp_path / "pitka.wav"))
    assert transcript.text == "Ensimmäinen osa.\n\nKolmas osa."


async def test_transcribe_without_speech(tmp_path, fake_transcriber):
    audio = make_wav(tmp_path / "hiljaa.wav")
    for silence in ("", "  \n", gaik_ops._TRANSCRIBE_PROMPTS["fi"]):
        fake_transcriber.raw_transcript = silence
        with pytest.raises(AppError) as raised:
            await gaik_ops.transcribe_audio(audio)
        assert (raised.value.status, raised.value.code) == (422, "no_speech")

    fake_transcriber.raw_transcript = "[Transcription failed for segment 1]"
    with pytest.raises(AppError) as raised:
        await gaik_ops.transcribe_audio(audio)
    assert (raised.value.status, raised.value.code) == (502, "transcription_failed")


async def test_transcribe_refuses_what_it_cannot_send(tmp_path, fake_transcriber):
    with pytest.raises(AppError) as raised:
        await gaik_ops.transcribe_audio(tmp_path / "puuttuu.wav")
    assert raised.value.status == 404

    other = tmp_path / "viesti.m4a"
    other.write_bytes(b"\x00\x00\x00\x20ftypM4A ")
    with pytest.raises(AppError) as raised:
        await gaik_ops.transcribe_audio(other)
    assert (raised.value.status, raised.value.code) == (415, "unsupported_audio")

    empty = tmp_path / "tyhja.mp3"
    empty.write_bytes(b"")
    with pytest.raises(AppError) as raised:
        await gaik_ops.transcribe_audio(empty)
    assert raised.value.code == "no_speech"
    assert fake_transcriber.calls == []


async def test_provider_text_and_keys_never_leave(tmp_path, fake_transcriber, caplog):
    provider_text = f"Error code: 401 - Incorrect API key provided: {FAKE_KEY}. Deployment eu-7."
    fake_transcriber.error = RuntimeError(provider_text)
    with caplog.at_level("INFO"), pytest.raises(AppError) as raised:
        await gaik_ops.transcribe_audio(make_wav(tmp_path / "a.wav"))

    error = raised.value
    assert (error.status, error.code) == (502, "transcription_failed")
    assert error.message == "Puheen muuttaminen tekstiksi ei onnistunut. Yritä uudelleen."
    for leaked in ("401", "API key", "Deployment", FAKE_KEY):
        assert leaked not in error.message and leaked not in str(error)
    assert "Incorrect API key" in caplog.text  # logged for the operator
    assert FAKE_KEY not in caplog.text  # with the key scrubbed


async def test_provider_timeouts_and_rate_limits_have_their_own_answers(tmp_path, fake_transcriber):
    audio = make_wav(tmp_path / "a.wav")

    class APITimeoutError(Exception):
        pass

    fake_transcriber.error = APITimeoutError("Request timed out.")
    with pytest.raises(AppError) as raised:
        await gaik_ops.transcribe_audio(audio)
    assert (raised.value.status, raised.value.code) == (504, "upstream_timeout")

    class RateLimitError(Exception):
        status_code = 429

    fake_transcriber.error = RateLimitError("Too many requests for resource eu-7")
    with pytest.raises(AppError) as raised:
        await gaik_ops.transcribe_audio(audio)
    assert (raised.value.status, raised.value.code) == (503, "upstream_busy")
    assert "eu-7" not in raised.value.message


# generate_fields -----------------------------------------------------------------------------


def field_spec(name: str, kind: str, description: str, **extra):
    from gaik.software_components.extractor import FieldSpec

    return FieldSpec(field_name=name, field_type=kind, description=description, **extra)


class FakeSchemaGenerator:
    calls: list[dict[str, Any]] = []
    requirements: Any = None
    error: Exception | None = None

    def __init__(self, config, model=None, **sampling):
        self.record = {"config": config, "sampling": sampling, "worker_thread": in_worker_thread()}

    def generate_schema_with_usage(self, user_requirements):
        print("Generating schema from requirements...")
        self.record["requirement"] = user_requirements
        type(self).calls.append(self.record)
        if type(self).error is not None:
            raise type(self).error
        return SimpleNamespace(requirements=type(self).requirements)


def service_requirements():
    from gaik.software_components.extractor import ExtractionRequirements

    return ExtractionRequirements(
        use_case_name="huoltokaynti",
        fields=[
            field_spec("asiakkaan_nimi", "str", "Asiakkaan nimi."),
            field_spec("k_yntip_iv", "date", "Käyntipäivä."),  # gaik's snake_case of "käyntipäivä"
            field_spec(
                "saapumisaika", "str", "Kellonaika, jolloin huoltaja saapui.", nullable=True
            ),
            field_spec("tehdyt_tyot", "list[str]", "Huoltokäynnillä tehdyt työt."),
            field_spec("tunnit", "float", "Työhön kuluneet tunnit.", nullable=True),
            field_spec("laite_kunnossa", "bool", "Jäikö laite käyttökuntoon."),
            field_spec("kiireellisyys", "str", "Kiireellisyys.", enum=["kiireellinen", "normaali"]),
        ],
    )


@pytest.fixture
def fake_generator(monkeypatch):
    import gaik.software_components.extractor as module

    FakeSchemaGenerator.calls = []
    FakeSchemaGenerator.requirements = service_requirements()
    FakeSchemaGenerator.error = None
    monkeypatch.setattr(module, "SchemaGenerator", FakeSchemaGenerator)
    return FakeSchemaGenerator


@pytest.fixture
def fake_chat(monkeypatch):
    """Replaces `llm.structured_chat`; `answer` builds the reply from the schema it is given."""
    state = SimpleNamespace(calls=[], answer=None, error=None)

    async def structured_chat(system, user, schema_model, *, model=None, **options):
        state.calls.append(
            {"system": system, "user": user, "schema": schema_model, "options": options}
        )
        if state.error is not None:
            raise state.error
        return state.answer(schema_model)

    monkeypatch.setattr(llm, "structured_chat", structured_chat)
    return state


REQUIREMENT = (
    "Haluan kerätä huoltokäynnistä asiakkaan nimen, käyntipäivän, saapumisajan, tehdyt työt "
    "ja tunnit. Lisäksi tarvitaan tieto siitä, jäikö laite käyttökuntoon."
)


def finnish_copies(schema_model):
    copy = gaik_ops._FieldCopy
    return schema_model(
        fields=[
            copy(
                key="asiakkaan_nimi",
                name="asiakkaan_nimi",
                label="Asiakkaan nimi",
                description="Asiakkaan tai yrityksen nimi.",
                kind="text",
            ),
            copy(
                key="k_yntip_iv",
                name="käyntipäivä",
                label="Käyntipäivä:",
                description="Päivä, jona huolto tehtiin.",
                kind="text",
            ),
            copy(
                key="saapumisaika",
                name="saapumisaika",
                label="Saapumisaika",
                description="Kellonaika, jolloin huoltaja saapui.",
                kind="time",
            ),
            copy(
                key="tehdyt_tyot",
                name="tehdyt_tyot",
                label="Tehdyt työt",
                description="Mitä huollossa tehtiin.",
                kind="longtext",
            ),
            copy(
                key="tunnit",
                name="tunnit",
                label="Työtunnit",
                description="Työhön kuluneet tunnit.",
                kind="text",
            ),
            copy(
                key="laite_kunnossa",
                name="laite_kunnossa",
                label="Laite käyttökunnossa",
                description="Jäikö laite käyttökuntoon.",
                kind="text",
            ),
            copy(
                key="kiireellisyys",
                name="kiireellisyys",
                label="Kiireellisyys",
                description="Kuinka kiireellinen asia on.",
                kind="longtext",
            ),
        ]
    )


async def test_generate_fields_maps_gaik_fields_to_field_defs(fake_generator, fake_chat, capsys):
    fake_chat.answer = finnish_copies
    fields = await gaik_ops.generate_fields(f"  {REQUIREMENT}  ")

    assert all(isinstance(field, FieldDef) for field in fields)
    by_name = {field.name: field for field in fields}
    assert list(by_name) == [
        "asiakkaan_nimi",
        "kayntipaiva",  # the broken key was repaired, in ASCII
        "saapumisaika",
        "tehdyt_tyot",
        "tunnit",
        "laite_kunnossa",
        "kiireellisyys",
    ]
    assert {name: field.type for name, field in by_name.items()} == {
        "asiakkaan_nimi": "text",
        "kayntipaiva": "date",
        "saapumisaika": "time",
        "tehdyt_tyot": "longtext",
        "tunnit": "number",
        "laite_kunnossa": "choice",
        "kiireellisyys": "choice",
    }
    assert by_name["kayntipaiva"].label == "Käyntipäivä"
    assert by_name["kayntipaiva"].description == "Päivä, jona huolto tehtiin."
    assert by_name["laite_kunnossa"].options == ["kyllä", "ei"]
    assert by_name["kiireellisyys"].options == ["kiireellinen", "normaali"]
    assert by_name["asiakkaan_nimi"].options is None
    assert {name: field.required for name, field in by_name.items()} == {
        "asiakkaan_nimi": True,
        "kayntipaiva": True,
        "saapumisaika": False,
        "tehdyt_tyot": True,
        "tunnit": False,
        "laite_kunnossa": True,
        "kiireellisyys": True,
    }

    (call,) = fake_generator.calls
    assert call["worker_thread"] is True
    assert call["requirement"] == REQUIREMENT
    assert call["config"]["timeout"] == 120
    assert call["sampling"] == {"temperature": 0.0, "reasoning_effort": None}
    (chat,) = fake_chat.calls
    assert "k_yntip_iv" in chat["user"] and REQUIREMENT in chat["user"]
    assert chat["options"] == {"temperature": 0.0}
    assert capsys.readouterr().out == ""


async def test_generate_fields_for_a_reasoning_deployment(monkeypatch, fake_generator, fake_chat):
    monkeypatch.setenv("AZURE_DEPLOYMENT", "gpt-5.6-sol")
    get_settings.cache_clear()
    fake_chat.answer = finnish_copies
    await gaik_ops.generate_fields(REQUIREMENT)
    assert fake_generator.calls[0]["sampling"] == {"temperature": None, "reasoning_effort": "low"}
    assert fake_chat.calls[0]["options"] == {"temperature": 0.0, "reasoning_effort": "none"}


async def test_generate_fields_without_the_label_call(fake_generator, fake_chat):
    fake_chat.error = AppError("model_failed", "Tekoäly ei vastannut.", 502)
    fields = await gaik_ops.generate_fields(REQUIREMENT)
    by_name = {field.name: field for field in fields}
    assert by_name["asiakkaan_nimi"].label == "Asiakkaan nimi"
    assert by_name["asiakkaan_nimi"].description == "Asiakkaan nimi."
    assert by_name["saapumisaika"].type == "time"  # from the description
    assert by_name["k_yntip_iv"].type == "date"  # gaik's key is kept when nothing repairs it


async def test_generate_fields_from_a_parent_with_rows(fake_generator, fake_chat):
    from gaik.software_components.extractor import (
        ChildRequirements,
        CompositeExtractionRequirements,
        ExtractionRequirements,
    )

    fake_generator.requirements = CompositeExtractionRequirements(
        parent_requirements=ExtractionRequirements(
            use_case_name="tilaus", fields=[field_spec("tilausnumero", "str", "Tilauksen numero.")]
        ),
        children=[
            ChildRequirements(
                container_name="rivit",
                container_description="Tilauksen rivit",
                requirements=ExtractionRequirements(
                    use_case_name="rivi",
                    fields=[
                        field_spec("tuote", "str", "Tuote."),
                        field_spec("maara", "int", "Määrä."),
                    ],
                ),
            )
        ],
    )
    fake_chat.error = AppError("model_failed", "Tekoäly ei vastannut.", 502)
    fields = await gaik_ops.generate_fields("Tilausnumero ja jokaiselta riviltä tuote ja määrä.")
    assert [(field.name, field.type, field.required) for field in fields] == [
        ("tilausnumero", "text", True),
        ("rivit", "longtext", False),
    ]
    assert fields[1].description == "Tilauksen rivit (tuote, maara)"


async def test_generate_fields_errors(fake_generator, fake_chat):
    with pytest.raises(AppError) as raised:
        await gaik_ops.generate_fields("  ")
    assert (raised.value.status, raised.value.code) == (400, "empty_requirement")
    assert fake_generator.calls == []

    fake_generator.error = ValueError("Azure said: content filter triggered for prompt #991")
    with pytest.raises(AppError) as raised:
        await gaik_ops.generate_fields(REQUIREMENT)
    assert (raised.value.status, raised.value.code) == (502, "fields_failed")
    assert "991" not in raised.value.message and "Azure" not in raised.value.message

    from gaik.software_components.extractor import ExtractionRequirements

    fake_generator.error = None
    fake_generator.requirements = ExtractionRequirements(use_case_name="tyhja", fields=[])
    with pytest.raises(AppError) as raised:
        await gaik_ops.generate_fields(REQUIREMENT)
    assert raised.value.code == "fields_failed"


# extract_fields ------------------------------------------------------------------------------

TRANSCRIPT = (
    "Täällä Mikko Lahtinen Äänekosken tehtaalta. Hallissa 3 on öljyvuoto trukkikäytävällä, "
    "huomasin sen tänä aamuna kello 7.30. Lattialla on noin kaksi litraa öljyä ja alue on "
    "eristetty. Paikalla oli 2 henkilöä. Asia on kiireellinen, koska joku voi liukastua. "
    "Tapahtumapäivä on 12.11.2026."
)

FIELDS = [
    FieldDef(name="ilmoittaja", label="Ilmoittaja", type="text", required=True, description="Kuka"),
    FieldDef(name="paikka", label="Paikka", type="text", required=True, description="Missä"),
    FieldDef(name="sää", label="Sää", type="text", required=False, description="Sääolot"),
    FieldDef(name="saa", label="Saa", type="text", required=False, description="Toinen kenttä"),
    FieldDef(name="paiva", label="Päivä", type="date", required=True, description="Tapahtumapäivä"),
    FieldDef(name="kello", label="Kellonaika", type="time", required=False, description="Aika"),
    FieldDef(
        name="henkiloita", label="Henkilöitä", type="number", required=False, description="Määrä"
    ),
    FieldDef(
        name="litrat", label="Öljyä litroina", type="number", required=False, description="Määrä"
    ),
    FieldDef(
        name="kiireellisyys",
        label="Kiireellisyys",
        type="choice",
        required=True,
        description="Kuinka kiireellinen",
        options=["kiireellinen", "normaali", "ei kiireellinen"],
    ),
    FieldDef(
        name="kuvaus", label="Kuvaus", type="longtext", required=True, description="Mitä tapahtui"
    ),
]


def citation(field: str, quote: str) -> dict[str, Any]:
    return {"field": field, "document": 1, "page": 1, "quote": quote}


class FakeExtractor:
    """Stands in for gaik's DataExtractor: `row` is what the model would answer. It is
    validated against the schema the extractor was given and goes through gaik's own
    post-processing, so the test fails if `extract_fields` builds a schema that does not fit."""

    calls: list[dict[str, Any]] = []
    row: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    error: Exception | None = None

    def __init__(self, config, model=None, **sampling):
        self.record = {"config": config, "sampling": sampling, "worker_thread": in_worker_thread()}

    def extract(self, extraction_model, requirements, user_requirements, documents, **kwargs):
        print("EXTRACTION PHASE")
        self.record.update(
            model=extraction_model,
            requirements=requirements,
            task=user_requirements,
            documents=documents,
        )
        type(self).calls.append(self.record)
        if type(self).error is not None:
            raise type(self).error
        from gaik.software_components.extractor.schema import (
            apply_field_policies,
            normalize_extracted_data,
        )

        row = dict(type(self).row)
        if "field_sources" in extraction_model.model_fields:
            row["field_sources"] = {"items": type(self).sources}
        # What gaik does with the model's answer, without the model.
        data = extraction_model.model_validate(row).model_dump()
        data = apply_field_policies(data, requirements)
        return [normalize_extracted_data(data, requirements)]


@pytest.fixture
def fake_extractor(monkeypatch):
    import gaik.software_components.extractor as module

    def no_generator(*args, **kwargs):
        raise AssertionError("extract_fields must not generate a schema with a model call")

    FakeExtractor.calls = []
    FakeExtractor.error = None
    FakeExtractor.row = {
        "ilmoittaja": "Mikko Lahtinen",
        "paikka": "halli 3",
        "saa": None,
        "saa_2": "",
        "paiva": "12.11.2026",
        "kello": "7.30",
        "henkiloita": 2.0,
        "litrat": 2.5,
        "kiireellisyys": "kiireellinen",
        "kuvaus": "Öljyvuoto trukkikäytävällä, alue eristetty.",
    }
    FakeExtractor.sources = [
        citation("ilmoittaja", "Täällä  MIKKO Lahtinen"),  # other case and spacing than the text
        citation("paikka", "Hallissa 3 on öljyvuoto"),
        citation("saa", "aurinko paistaa"),  # a citation for a value that is not there
        citation("paiva", "Tapahtumapäivä on 12.11.2026"),
        citation("henkiloita", "Paikalla oli 2 henkilöä"),
        citation("litrat", "noin kolme litraa öljyä"),  # not in the text
        citation("kiireellisyys", "Asia on kiireellinen"),
    ]
    monkeypatch.setattr(module, "DataExtractor", FakeExtractor)
    monkeypatch.setattr(module, "SchemaGenerator", no_generator)
    return FakeExtractor


async def test_extract_fields_returns_strings_with_checked_evidence(fake_extractor, capsys):
    values = await gaik_ops.extract_fields(TRANSCRIPT, FIELDS, "Poimi turvallisuushavainto.")

    assert [value.name for value in values] == [field.name for field in FIELDS]
    assert [value.label for value in values] == [field.label for field in FIELDS]
    assert all(isinstance(value, TracedFieldValue) for value in values)
    assert all(value.source == "extractor" for value in values)
    assert all(value.value is None or isinstance(value.value, str) for value in values)
    by_name = {value.name: value for value in values}

    assert {name: value.value for name, value in by_name.items()} == {
        "ilmoittaja": "Mikko Lahtinen",
        "paikka": "halli 3",
        "sää": None,
        "saa": None,
        "paiva": "2026-11-12",
        "kello": "07:30",
        "henkiloita": "2",
        "litrat": "2.5",
        "kiireellisyys": "kiireellinen",
        "kuvaus": "Öljyvuoto trukkikäytävällä, alue eristetty.",
    }

    # Evidence is the text's own words, also where the model changed case or spacing.
    assert by_name["ilmoittaja"].evidence == "Täällä Mikko Lahtinen"
    assert by_name["ilmoittaja"].verdict == "verified"
    assert by_name["paikka"].evidence == "Hallissa 3 on öljyvuoto"
    assert by_name["paikka"].verdict == "reworded"  # "halli 3" is the base form of "Hallissa 3"
    assert by_name["paiva"].evidence == "Tapahtumapäivä on 12.11.2026"
    assert by_name["paiva"].verdict == "verified"
    assert by_name["henkiloita"].verdict == "verified"
    assert by_name["kiireellisyys"].verdict == "verified"
    # A quote that is not in the text is dropped and the value is marked for a check.
    assert (by_name["litrat"].evidence, by_name["litrat"].verdict) == (None, "check")
    # A value the model gave no quote for.
    assert (by_name["kello"].evidence, by_name["kello"].verdict) == (None, "unchecked")
    assert (by_name["kuvaus"].evidence, by_name["kuvaus"].verdict) == (None, "unchecked")
    # No value: no evidence and no verdict, whatever was cited.
    assert (by_name["sää"].evidence, by_name["sää"].verdict) == (None, None)

    for value in values:
        if value.evidence is not None:
            assert value.evidence in TRANSCRIPT
    assert capsys.readouterr().out == ""


async def test_extract_fields_builds_the_schema_from_field_defs(fake_extractor):
    await gaik_ops.extract_fields(TRANSCRIPT, FIELDS, "Poimi turvallisuushavainto.")
    (call,) = fake_extractor.calls

    assert call["worker_thread"] is True
    assert call["documents"] == [TRANSCRIPT]
    assert call["config"]["timeout"] == 120
    assert call["sampling"] == {"temperature": 0.0, "reasoning_effort": None}

    specs = {spec.field_name: spec for spec in call["requirements"].fields}
    # "sää" and "saa" both become ASCII names gaik accepts, and stay apart.
    assert list(specs) == [
        "ilmoittaja",
        "paikka",
        "saa",
        "saa_2",
        "paiva",
        "kello",
        "henkiloita",
        "litrat",
        "kiireellisyys",
        "kuvaus",
    ]
    assert {name: spec.field_type for name, spec in specs.items()} == {
        "ilmoittaja": "str",
        "paikka": "str",
        "saa": "str",
        "saa_2": "str",
        "paiva": "date",
        "kello": "str",
        "henkiloita": "float",
        "litrat": "float",
        "kiireellisyys": "str",
        "kuvaus": "str",
    }
    assert all(spec.nullable for spec in specs.values())  # the model may always answer null
    assert specs["kiireellisyys"].enum == ["kiireellinen", "normaali", "ei kiireellinen"]
    assert (
        specs["kello"].description
        == "Kellonaika. Aika A time of day as HH:MM on the 24-hour clock."
    )
    assert specs["sää" if "sää" in specs else "saa"].description.startswith("Sää.")

    model = call["model"]
    assert set(model.model_fields) == {*specs, "field_sources"}
    from openai.lib._pydantic import to_strict_json_schema

    to_strict_json_schema(model)  # the schema is one the structured-output API accepts

    task = call["task"]
    assert task.startswith("Poimi turvallisuushavainto.")
    assert gaik_ops._today().isoformat() in task
    assert "SOURCE CITATIONS" in task


async def test_extract_fields_without_trace(fake_extractor):
    values = await gaik_ops.extract_fields(TRANSCRIPT, FIELDS, "", trace=False)
    (call,) = fake_extractor.calls
    assert "field_sources" not in call["model"].model_fields
    assert "SOURCE CITATIONS" not in call["task"]
    assert call["task"].startswith("Fill the fields of the schema from the text.")
    assert all(type(value) is FieldValue for value in values)
    assert all(value.evidence is None for value in values)
    assert values[0].value == "Mikko Lahtinen"


async def test_extract_fields_normalises_values(fake_extractor):
    fake_extractor.sources = []
    fake_extractor.row = {
        "ilmoittaja": "  ",
        "paikka": None,
        "saa": "pilvistä",
        "saa_2": None,
        "paiva": "2026-11-12",
        "kello": "klo 14:05",
        "henkiloita": 0.0,
        "litrat": None,
        "kiireellisyys": "",
        "kuvaus": "Kaikki kunnossa.",
    }
    values = {
        value.name: value.value for value in await gaik_ops.extract_fields(TRANSCRIPT, FIELDS, "")
    }
    assert values == {
        "ilmoittaja": None,
        "paikka": None,
        "sää": "pilvistä",
        "saa": None,
        "paiva": "2026-11-12",
        "kello": "14:05",
        "henkiloita": "0",
        "litrat": None,
        "kiireellisyys": None,
        "kuvaus": "Kaikki kunnossa.",
    }


@pytest.mark.parametrize(
    ("value", "kind", "options", "expected"),
    [
        (True, "choice", ["kyllä", "ei"], "kyllä"),
        (False, "text", None, "ei"),
        (3, "number", None, "3"),
        (1234567.0, "number", None, "1234567"),
        (0.125, "number", None, "0.125"),
        (float("nan"), "number", None, None),
        (["a", " b ", ""], "longtext", None, "a, b"),
        ("Kiireellinen", "choice", ["kiireellinen", "normaali"], "kiireellinen"),
        ("jotain muuta", "choice", ["kiireellinen", "normaali"], None),
        ("vapaa", "choice", None, "vapaa"),
        ("7:05", "time", None, "07:05"),
        ("klo 7.05", "time", None, "07:05"),
        ("aamulla", "time", None, "aamulla"),
        ("25:00", "time", None, "25:00"),
    ],
)
def test_value_text(value, kind, options, expected):
    field = FieldDef(
        name="x", label="X", type=kind, required=False, description="", options=options
    )
    assert gaik_ops._value_text(value, field) == expected


async def test_traced_values_are_field_values_in_a_report(fake_extractor):
    class Holder(BaseModel):
        fields: list[FieldValue]

    values = await gaik_ops.extract_fields(TRANSCRIPT, FIELDS, "")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dumped = Holder(fields=values).model_dump()
        Holder(fields=values).model_dump_json()
    assert set(dumped["fields"][0]) == {"name", "label", "value", "evidence", "source"}
    assert values[0].model_dump()["verdict"] == "verified"


async def test_extract_fields_errors(fake_extractor):
    with pytest.raises(AppError) as raised:
        await gaik_ops.extract_fields("   ", FIELDS, "")
    assert (raised.value.status, raised.value.code) == (400, "empty_text")
    with pytest.raises(AppError) as raised:
        await gaik_ops.extract_fields(TRANSCRIPT, [], "")
    assert (raised.value.status, raised.value.code) == (400, "no_fields")
    assert fake_extractor.calls == []

    fake_extractor.error = RuntimeError(
        "Error code: 400 - {'error': {'message': 'schema too deep'}}"
    )
    with pytest.raises(AppError) as raised:
        await gaik_ops.extract_fields(TRANSCRIPT, FIELDS, "")
    assert (raised.value.status, raised.value.code) == (502, "extraction_failed")
    assert raised.value.message == "Tietojen poiminta ei onnistunut. Yritä uudelleen."


async def test_missing_settings_are_not_configured(monkeypatch, fake_extractor):
    monkeypatch.setenv("AZURE_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(AppError) as raised:
        await gaik_ops.extract_fields(TRANSCRIPT, FIELDS, "")
    assert (raised.value.status, raised.value.code) == (503, "not_configured")
    assert "AZURE" not in raised.value.message


def test_slug_and_safe_names():
    assert gaik_ops._slug("Sää ja lämpötila (°C)") == "saa_ja_lampotila_c"
    assert gaik_ops._slug("Työhön kuluneet tunnit") == "tyohon_kuluneet_tunnit"
    assert gaik_ops._slug("3 päivää") == "f_3_paivaa"
    assert gaik_ops._slug("???") == "kentta"
    assert len(gaik_ops._slug("a" * 200)) == 48

    from gaik.software_components.extractor import FieldSpec

    for name in ("saa_ja_lampotila_c", "f_3_paivaa", "kentta"):
        assert FieldSpec(field_name=name, field_type="str", description="").field_name == name


# classify_jev --------------------------------------------------------------------------------

LABELS = {
    "kiireellinen": "Välitön vaara ihmisille tai tuotannolle",
    "normaali": "Hoidettava lähipäivinä",
    "ei kiireellinen": "",
}


class FakeJev:
    model = "jev-fake"

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {
            "class": "kiireellinen",
            "confidence": 0.93,
            "probabilities": {"kiireellinen": 0.95, "normaali": 0.04, "ei kiireellinen": 0.01},
            "predicted": "kiireellinen",
            "model": "jev-1.13.0",
        }
        self.error: Exception | None = None

    def classify(self, text, classes, instructions=None, max_chars=10_000, **kwargs):
        self.calls.append(
            {
                "text": text,
                "classes": dict(classes),
                "instructions": instructions,
                "max_chars": max_chars,
                "worker_thread": in_worker_thread(),
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def fake_jev(monkeypatch):
    jev = FakeJev()
    monkeypatch.setattr(llm, "jev_classifier", lambda **overrides: jev)
    return jev


async def test_classify_jev_maps_the_result_and_caps_the_text(fake_jev):
    text = "Öljyvuoto hallissa 3. " + "x" * 5000
    result = await gaik_ops.classify_jev(f"  {text}", LABELS, "Kuinka kiireellinen asia on?")

    assert isinstance(result, Classification)
    assert (result.label, result.confidence, result.model) == ("kiireellinen", 0.93, "jev-1.13.0")
    assert result.probabilities == {"kiireellinen": 0.95, "normaali": 0.04, "ei kiireellinen": 0.01}
    assert result.ms >= 0

    (call,) = fake_jev.calls
    assert call["worker_thread"] is True
    assert len(call["text"]) == gaik_ops.JEV_MAX_CHARS == 2000
    assert call["text"].startswith("Öljyvuoto")
    assert call["max_chars"] == 2000
    assert call["instructions"] == "Kuinka kiireellinen asia on?"
    assert call["classes"] == {
        "kiireellinen": "Välitön vaara ihmisille tai tuotannolle",
        "normaali": "Hoidettava lähipäivinä",
        "ei kiireellinen": None,
    }


async def test_classify_jev_fills_a_label_the_service_left_out(fake_jev):
    fake_jev.result = {
        "class": "normaali",
        "confidence": 0.6,
        "probabilities": {"normaali": 0.7, "kiireellinen": 0.3},
        "model": None,
    }
    result = await gaik_ops.classify_jev("Lamppu on rikki.", LABELS)
    assert result.probabilities == {"kiireellinen": 0.3, "normaali": 0.7, "ei kiireellinen": 0.0}
    assert result.model == "jev-fake"
    assert fake_jev.calls[0]["instructions"] is None


@pytest.mark.parametrize("classify", [gaik_ops.classify_jev, gaik_ops.classify_azure])
async def test_classify_refuses_bad_input(classify, fake_jev, fake_chat):
    for labels in ({}, {"vain yksi": "x"}, {"a": "x", " ": "y"}, {"a": "x", " a ": "y"}):
        with pytest.raises(AppError) as raised:
            await classify("Teksti", labels)
        assert (raised.value.status, raised.value.code) == (400, "invalid_labels")
    with pytest.raises(AppError) as raised:
        await classify("Teksti", {f"luokka {number}": "" for number in range(51)})
    assert raised.value.code == "invalid_labels"
    with pytest.raises(AppError) as raised:
        await classify("   ", LABELS)
    assert (raised.value.status, raised.value.code) == (400, "empty_text")
    assert fake_jev.calls == [] and fake_chat.calls == []


async def test_classify_jev_failures(fake_jev):
    fake_jev.error = RuntimeError("TypeSafe 503: upstream us-east-1 unavailable")
    with pytest.raises(AppError) as raised:
        await gaik_ops.classify_jev("Teksti", LABELS)
    assert (raised.value.status, raised.value.code) == (502, "classification_failed")
    assert "us-east-1" not in raised.value.message

    fake_jev.error = None
    fake_jev.result = {"class": "unknown", "confidence": 0.1, "probabilities": {}, "model": "jev"}
    with pytest.raises(AppError) as raised:
        await gaik_ops.classify_jev("Teksti", LABELS)
    assert raised.value.code == "classification_failed"


async def test_classify_jev_without_gaik_decide_settings(monkeypatch):
    monkeypatch.setenv("GAIK_DECIDE_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(AppError) as raised:
        await gaik_ops.classify_jev("Teksti", LABELS)
    assert (raised.value.status, raised.value.code) == (503, "not_configured")
    assert "GAIK_DECIDE" not in raised.value.message


# classify_azure ------------------------------------------------------------------------------


def scores(best: str, **probabilities: float):
    def answer(schema_model):
        score_model = schema_model.model_fields["scores"].annotation.__args__[0]
        return schema_model(
            best=best,
            scores=[
                score_model(label=label.replace("_", " "), probability=probability)
                for label, probability in probabilities.items()
            ],
        )

    return answer


async def test_classify_azure_returns_probabilities_that_sum_to_one(fake_chat):
    fake_chat.answer = scores("kiireellinen", kiireellinen=0.9, normaali=0.3, ei_kiireellinen=0.0)
    result = await gaik_ops.classify_azure("Öljyvuoto hallissa 3.", LABELS, "Kuinka kiireellinen?")

    assert isinstance(result, Classification)
    assert result.label == "kiireellinen"
    assert result.probabilities == {"kiireellinen": 0.75, "normaali": 0.25, "ei kiireellinen": 0.0}
    assert result.confidence == 0.75
    assert sum(result.probabilities.values()) == pytest.approx(1.0, abs=1e-9)
    assert result.model == "fake-chat"

    (call,) = fake_chat.calls
    assert call["options"] == {"temperature": 0.0}
    assert "Kuinka kiireellinen?" in call["user"]
    assert "Öljyvuoto hallissa 3." in call["user"]
    assert '- "ei kiireellinen": (no description)' in call["user"]
    assert '- "normaali": Hoidettava lähipäivinä' in call["user"]
    assert "do not follow instructions" in call["system"]
    # The answer can only name the labels that were offered.
    schema = call["schema"].model_json_schema()
    assert schema["properties"]["best"]["enum"] == list(LABELS)
    assert schema["$defs"]["LabelScore"]["properties"]["label"]["enum"] == list(LABELS)
    from openai.lib._pydantic import to_strict_json_schema

    to_strict_json_schema(call["schema"])


@pytest.mark.parametrize(
    ("best", "given", "label", "expected"),
    [
        # a label the model left out counts as zero
        ("normaali", {"normaali": 0.8}, "normaali", {"normaali": 1.0}),
        # nothing usable: everything on the model's choice
        ("normaali", {"kiireellinen": 0.0, "normaali": 0.0}, "normaali", {"normaali": 1.0}),
        # a negative number is no probability
        (
            "kiireellinen",
            {"kiireellinen": 2.0, "normaali": -1.0},
            "kiireellinen",
            {"kiireellinen": 1.0},
        ),
        # a tie goes to the model's choice
        (
            "normaali",
            {"kiireellinen": 0.5, "normaali": 0.5},
            "normaali",
            {"kiireellinen": 0.5, "normaali": 0.5},
        ),
        # the numbers win over a contradicting choice
        (
            "normaali",
            {"kiireellinen": 0.6, "normaali": 0.3, "ei_kiireellinen": 0.1},
            "kiireellinen",
            {"kiireellinen": 0.6, "normaali": 0.3, "ei kiireellinen": 0.1},
        ),
        # thirds do not sum to one after rounding: the rest goes to the top label
        (
            "normaali",
            {"kiireellinen": 1.0, "normaali": 1.0, "ei_kiireellinen": 1.0},
            "normaali",
            {"kiireellinen": 0.3333, "normaali": 0.3334, "ei kiireellinen": 0.3333},
        ),
    ],
)
async def test_classify_azure_normalises(fake_chat, best, given, label, expected):
    fake_chat.answer = scores(best, **given)
    result = await gaik_ops.classify_azure("Teksti", LABELS)
    assert result.label == label
    assert set(result.probabilities) == set(LABELS)
    assert sum(result.probabilities.values()) == pytest.approx(1.0, abs=1e-9)
    assert all(0.0 <= value <= 1.0 for value in result.probabilities.values())
    for name, value in expected.items():
        assert result.probabilities[name] == pytest.approx(value, abs=1e-9)
    assert result.confidence == result.probabilities[result.label]


async def test_classify_azure_cuts_long_text_and_passes_model_errors_on(fake_chat, monkeypatch):
    monkeypatch.setenv("AZURE_DEPLOYMENT", "gpt-5.6-sol")
    get_settings.cache_clear()
    fake_chat.answer = scores("normaali", normaali=1.0)
    await gaik_ops.classify_azure("a" * 30_000, LABELS)
    assert "a" * 20_000 in fake_chat.calls[0]["user"]
    assert "a" * 20_001 not in fake_chat.calls[0]["user"]
    assert fake_chat.calls[0]["options"] == {"temperature": 0.0, "reasoning_effort": "none"}

    fake_chat.error = AppError("model_failed", "Tekoäly ei vastannut.", 502)
    with pytest.raises(AppError) as raised:
        await gaik_ops.classify_azure("Teksti", LABELS)
    assert raised.value is fake_chat.error
    assert gaik_ops.last_timing().op == "classify_azure" and gaik_ops.last_timing().ok is False


# embed_texts ---------------------------------------------------------------------------------


class FakeEmbedder:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.delay = 0.0
        self.drop_last = False
        self.threads: list[bool] = []

    def embed(self, documents, **kwargs):
        self.threads.append(in_worker_thread())
        self.calls.append(list(documents))
        time.sleep(self.delay)
        vectors = [[float(len(text)), float(index)] for index, text in enumerate(documents)]
        return (vectors[:-1] if self.drop_last else vectors), documents


@pytest.fixture
def fake_embedder(monkeypatch):
    embedder = FakeEmbedder()
    monkeypatch.setattr(llm, "embedder", lambda **options: embedder)
    return embedder


async def test_embed_texts_returns_one_vector_per_text_in_order(fake_embedder):
    vectors = await gaik_ops.embed_texts(["yksi", " kaksi ", "kolmas teksti"])
    assert vectors == [[4.0, 0.0], [5.0, 1.0], [13.0, 2.0]]
    assert fake_embedder.calls == [["yksi", "kaksi", "kolmas teksti"]]
    assert fake_embedder.threads == [True]


async def test_embed_texts_edges(fake_embedder):
    assert await gaik_ops.embed_texts([]) == []
    assert fake_embedder.calls == []

    with pytest.raises(AppError) as raised:
        await gaik_ops.embed_texts(["teksti", "  "])
    assert (raised.value.status, raised.value.code) == (400, "empty_text")

    await gaik_ops.embed_texts(["ä" * 30_000])
    assert len(fake_embedder.calls[-1][0]) == gaik_ops.EMBED_MAX_CHARS

    fake_embedder.drop_last = True
    with pytest.raises(AppError) as raised:
        await gaik_ops.embed_texts(["a", "b"])
    assert (raised.value.status, raised.value.code) == (502, "embedding_failed")


# timing and deadlines ------------------------------------------------------------------------


async def test_every_operation_records_its_duration(fake_embedder, fake_jev):
    fake_embedder.delay = 0.05
    before = len(gaik_ops.recent_timings())
    vectors, ms = await gaik_ops.timed(gaik_ops.embed_texts(["yksi"]))
    assert vectors == [[4.0, 0.0]]
    assert ms >= 40

    timing = gaik_ops.last_timing()
    assert (timing.op, timing.ok) == ("embed_texts", True)
    assert 40 <= timing.ms <= ms
    assert timing.at.endswith("Z")

    result = await gaik_ops.classify_jev("Teksti", LABELS)
    assert gaik_ops.last_timing().op == "classify_jev"
    assert result.ms == gaik_ops.last_timing().ms
    recent = gaik_ops.recent_timings()
    assert [timing.op for timing in recent[before:]] == ["embed_texts", "classify_jev"]


async def test_a_hanging_provider_call_ends_at_the_deadline(fake_embedder, monkeypatch):
    monkeypatch.setitem(gaik_ops.DEADLINES, "embed_texts", 0.1)
    fake_embedder.delay = 1.5
    started = time.perf_counter()
    with pytest.raises(AppError) as raised:
        await gaik_ops.embed_texts(["yksi"])
    assert time.perf_counter() - started < 1.0  # did not wait for the thread
    assert (raised.value.status, raised.value.code) == (504, "upstream_timeout")
    assert raised.value.message == "Tekoäly ei vastannut ajoissa. Yritä uudelleen."
    assert gaik_ops.last_timing().ok is False
