from __future__ import annotations

import io
import math
import re
import shutil
import struct
import subprocess
import wave
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient

from app.core import files, limits
from app.core.errors import AppError, install_error_handlers

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")

SERVER_NAME = re.compile(r"^f_[0-9a-f]{16}\.[a-z0-9]+$")

OGG = b"OggS\x00\x02" + b"\x00" * 58
WEBM = b"\x1a\x45\xdf\xa3\x9fB\x86\x81\x01B\xf7\x81\x01" + b"\x00" * 51
MP3_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x23" + b"\x00" * 54
MP3_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 60
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 48
M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A mp42isom" + b"\x00" * 36
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
HTML = b"<!doctype html><html><body><script>alert(document.cookie)</script></body></html>"


def docx_bytes(text: str = "Synteettinen huoltoraportti.") -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def zip_bytes(names: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "x")
    return buffer.getvalue()


def tone(path: Path, seconds: float = 1.0, rate: int = 44_100) -> Path:
    """A synthetic sine tone as a 16-bit stereo WAV file."""
    frames = b"".join(
        struct.pack("<hh", value, value)
        for value in (
            int(12_000 * math.sin(2 * math.pi * 440 * index / rate))
            for index in range(int(seconds * rate))
        )
    )
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(frames)
    return path


TRAVERSAL_SCOPES = [
    "..",
    "../outside",
    "reports/..",
    "reports/../../outside",
    "reports/./x",
    "/absolute",
    "C:/Windows",
    "C:\\Windows",
    "reports\\..\\..",
    "reports//x",
    "reports/",
    ".hidden",
    "",
    "reports/con",
    "a b",
    "%2e%2e",
]

TRAVERSAL_NAMES = [
    "..",
    ".",
    "../app.db",
    "..\\app.db",
    "sub/file.txt",
    "sub\\file.txt",
    "/etc/passwd",
    "C:file.txt",
    "file.txt:stream",
    ".env",
    "file.",
    "nul",
    "NUL.txt",
    "",
    "a\x00b",
    "tiedosto ä.txt",
]


def test_save_open_list_and_delete(settings):
    stored = files.save_file("reports/rep_1", "raportti.docx", b"docx-bytes")
    assert stored.name == "raportti.docx"
    assert stored.size == 10
    assert stored.path == settings.data_path / "files" / "reports" / "rep_1" / "raportti.docx"
    assert stored.url == "/api/files/reports/rep_1/raportti.docx"

    files.save_file("reports/rep_1", "aani.ogg", io.BytesIO(b"ogg"))
    with files.open_file("reports/rep_1", "raportti.docx") as handle:
        assert handle.read() == b"docx-bytes"

    listed = files.list_files("reports/rep_1")
    assert [item.name for item in listed] == ["aani.ogg", "raportti.docx"]
    assert files.get_file("reports/rep_1", "aani.ogg").size == 3
    assert files.get_file("reports/rep_1", "missing.txt") is None
    assert files.list_files("reports/rep_2") == []

    files.save_file("reports/rep_1", "raportti.docx", b"new")
    assert files.get_file("reports/rep_1", "raportti.docx").size == 3
    assert [item.name for item in files.list_files("reports/rep_1")] == [
        "aani.ogg",
        "raportti.docx",
    ]

    assert files.delete_file("reports/rep_1", "aani.ogg") is True
    assert files.delete_file("reports/rep_1", "aani.ogg") is False


def test_opening_a_missing_file_is_a_404():
    with pytest.raises(AppError) as raised:
        files.open_file("reports", "missing.txt")
    assert raised.value.status == 404


@pytest.mark.parametrize("scope", TRAVERSAL_SCOPES)
def test_scope_cannot_leave_the_files_folder(scope, settings):
    for call in (
        lambda: files.save_file(scope, "file.txt", b"x"),
        lambda: files.open_file(scope, "file.txt"),
        lambda: files.list_files(scope),
        lambda: files.public_url(scope, "file.txt"),
        lambda: files.delete_file(scope, "file.txt"),
    ):
        with pytest.raises(AppError) as raised:
            call()
        assert raised.value.status == 400
    assert not (settings.data_path / "outside").exists()
    assert not (settings.data_path.parent / "outside").exists()


@pytest.mark.parametrize("name", TRAVERSAL_NAMES)
def test_name_cannot_leave_its_scope(name, settings):
    (settings.data_path).mkdir(parents=True, exist_ok=True)
    (settings.data_path / "app.db").write_bytes(b"database")
    for call in (
        lambda: files.save_file("reports", name, b"x"),
        lambda: files.open_file("reports", name),
        lambda: files.public_url("reports", name),
        lambda: files.delete_file("reports", name),
    ):
        with pytest.raises(AppError) as raised:
            call()
        assert raised.value.status == 400
    assert (settings.data_path / "app.db").read_bytes() == b"database"


def test_a_symlink_out_of_the_folder_is_refused(settings, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    root = settings.data_path / "files"
    root.mkdir(parents=True)
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available to this user")
    with pytest.raises(AppError):
        files.open_file("link", "secret.txt")


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("Työmaa päiväkirja (1).docx", "Tyomaa_paivakirja_1.docx"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\someone\\note.ogg", "note.ogg"),
        ("  .env  ", "env"),
        ("", "tiedosto"),
        ("...", "tiedosto"),
        ("nul.txt", "tiedosto"),
        ("ääni.OGG", "aani.OGG"),
        ("a" * 200 + ".pdf", "a" * 80 + ".pdf"),
    ],
)
def test_safe_name(given, expected):
    result = files.safe_name(given)
    assert result == expected
    files.file_path("uploads", result)  # always a valid segment


def test_files_are_served_behind_the_access_rules(client, anon_client, bridge_client, mcp_client):
    files.save_file("reports/rep_1", "raportti.docx", b"docx-bytes")
    url = files.public_url("reports/rep_1", "raportti.docx")

    assert anon_client.get(url).status_code == 401
    assert mcp_client.get(url).status_code == 401
    assert bridge_client.get(url).status_code == 200  # the bridge sends the Word file on

    response = client.get(url)
    assert response.status_code == 200
    assert response.content == b"docx-bytes"

    missing = client.get("/api/files/reports/rep_1/missing.docx")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "file_not_found"


@pytest.mark.parametrize(
    ("name", "content_type"),
    [
        ("aani.ogg", "audio/ogg"),
        ("aani.webm", "audio/webm"),
        ("aani.mp3", "audio/mpeg"),
        ("aani.wav", "audio/wav"),
        ("aani.m4a", "audio/mp4"),
        ("AANI.MP3", "audio/mpeg"),
        ("liite.pdf", "application/pdf"),
        (
            "raportti.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("muistio.txt", "text/plain; charset=utf-8"),
        # Not on the allowlist: never a type a browser would render or run.
        ("sivu.html", "application/octet-stream"),
        ("sivu.htm", "application/octet-stream"),
        ("kuva.svg", "application/octet-stream"),
        ("koodi.js", "application/octet-stream"),
        ("sivu.xhtml", "application/octet-stream"),
        ("data.json", "application/octet-stream"),
        ("ilman-paatetta", "application/octet-stream"),
        ("sivu.html.txt", "text/plain; charset=utf-8"),
    ],
)
def test_every_stored_file_is_served_as_a_download_with_an_allowlisted_type(
    client, name, content_type
):
    files.save_file("studio", name, HTML)
    response = client.get(files.public_url("studio", name))
    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert response.headers["content-disposition"].startswith("attachment")
    assert "inline" not in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_download_name_is_a_label_only():
    files.save_file("reports/rep_1", "f_0011223344556677.docx", b"docx")
    response = files.file_response(
        "reports/rep_1", "f_0011223344556677.docx", download_name="Työmaapäiväkirja 12.11.docx"
    )
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment; filename*=utf-8''Ty%C3%B6maa")
    assert Path(response.path).name == "f_0011223344556677.docx"

    for hostile in (
        'a"; x="b.docx',
        "..\\..\\evil.docx",
        "a\r\nSet-Cookie: x=1.docx",
        "../../x.docx",
    ):
        header = files.file_response(
            "reports/rep_1", "f_0011223344556677.docx", download_name=hostile
        ).headers["content-disposition"]
        assert header.startswith("attachment; filename")
        assert "\r" not in header and "\n" not in header and "/" not in header
        assert '"; x="' not in header

    empty = files.file_response("reports/rep_1", "f_0011223344556677.docx", download_name="..")
    assert 'filename="f_0011223344556677.docx"' in empty.headers["content-disposition"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/files/reports/%2e%2e/%2e%2e/app.db",
        "/api/files/..%2f..%2fapp.db",
        "/api/files/reports/..%5c..%5capp.db",
        "/api/files/app.db",
        "/api/files/%2e%2e/app.db",
        "/api/files/reports/rep_1/.env",
    ],
)
def test_served_paths_cannot_reach_outside(client, settings, path):
    files.save_file("reports/rep_1", "raportti.docx", b"docx-bytes")
    (settings.data_path / "app.db").write_bytes(b"database")
    response = client.get(path)
    assert response.status_code in (400, 404)
    assert b"database" not in response.content


# magic bytes ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "key"),
    [
        (OGG, "ogg"),
        (WEBM, "webm"),
        (MP3_ID3, "mp3"),
        (MP3_FRAME, "mp3"),
        (b"\xff\xf3\x48\xc4" + b"\x00" * 60, "mp3"),  # MPEG-2 layer III
        (WAV, "wav"),
        (M4A, "m4a"),
        (b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00" + b"\x00" * 44, "m4a"),
    ],
)
def test_audio_is_recognised_by_its_first_bytes(head, key):
    detected = files.detect_audio(head)
    assert detected is not None and detected.key == key
    assert detected.kind == "audio" and detected.ffmpeg


@pytest.mark.parametrize(
    "head",
    [
        b"",
        b"OggX" + b"\x00" * 60,
        HTML,
        PDF,
        b"MZ\x90\x00" + b"\x00" * 60,  # a Windows program
        b"PK\x03\x04" + b"\x00" * 60,  # a zip
        b"#EXTM3U\nhttp://example.invalid/list.m3u8\n",  # a playlist ffmpeg would follow
        b"ffconcat version 1.0\nfile /etc/passwd\n",
        b"RIFF\x24\x00\x00\x00AVI LIST" + b"\x00" * 48,  # RIFF, but not WAVE
        b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00" + b"\x00" * 44,  # a photo
        b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00" + b"\x00" * 44,
        b"\xff\xf1\x50\x80" + b"\x00" * 60,  # raw AAC (ADTS)
        b"\xff\xff\xff\xff" + b"\x00" * 60,
        b"\xff\xfb\xf0\x64" + b"\x00" * 60,  # frame sync with an impossible bitrate
        b"fLaC\x00\x00\x00\x22" + b"\x00" * 56,
        b"Tavallista tekstia, ei aanta.",
    ],
)
def test_other_bytes_are_not_audio(head):
    assert files.detect_audio(head) is None


def test_documents_are_recognised_by_content(tmp_path):
    def detect(data: bytes) -> str | None:
        path = tmp_path / "upload.bin"
        path.write_bytes(data)
        detected = files.detect_document(path)
        return detected.key if detected else None

    assert detect(PDF) == "pdf"
    assert detect(docx_bytes()) == "docx"
    assert detect(b"Huoltoraportti: pumppu vaihdettu.\n") == "txt"
    assert detect("\ufeffÄäni ja öljy".encode()) == "txt"  # UTF-8 with a byte order mark
    assert detect("Ääni ja öljy".encode("cp1252")) == "txt"
    assert detect("Ääni ja öljy".encode("utf-16")) == "txt"
    assert detect(HTML) == "txt"  # text is text: it is stored and served as plain text
    assert detect(b" %PDF-1.7") == "txt"  # not at byte 0: not a PDF

    assert detect(b"") is None
    assert detect(b"   \n\t ") is None
    assert detect(zip_bytes(["a.txt"])) is None  # a zip that is not a Word file
    assert detect(zip_bytes(["word/document.xml"])) is None  # no content types part
    assert detect(zip_bytes(["xl/workbook.xml", "[Content_Types].xml"])) is None  # Excel
    assert detect(b"PK\x03\x04 not a real zip") is None
    assert detect(b"MZ\x90\x00\x03\x00\x00\x00") is None
    assert detect(OGG) is None
    assert detect(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 40) is None  # old .doc
    assert detect(b"teksti\x00jossa nolla") is None


# uploads -------------------------------------------------------------------------------------


def test_upload_is_stored_under_a_server_generated_name(settings):
    stored = files.store_upload("reports/rep_1", OGG, "audio")
    assert SERVER_NAME.fullmatch(stored.name) and stored.name.endswith(".ogg")
    assert (stored.format, stored.media_type, stored.size) == ("ogg", "audio/ogg", len(OGG))
    assert stored.path.parent == settings.data_path / "files" / "reports" / "rep_1"
    assert stored.path.read_bytes() == OGG
    assert stored.url == f"/api/files/reports/rep_1/{stored.name}"

    again = files.store_upload("reports/rep_1", io.BytesIO(OGG), "audio")
    assert again.name != stored.name  # two uploads never share a name
    assert [item.name for item in files.list_files("reports/rep_1")] == sorted(
        [stored.name, again.name]
    )


@pytest.mark.parametrize(
    ("data", "kind", "extension"),
    [
        (WEBM, "audio", ".webm"),
        (MP3_ID3, "audio", ".mp3"),
        (WAV, "audio", ".wav"),
        (M4A, "audio", ".m4a"),
        (PDF, "document", ".pdf"),
        (b"Muistio", "document", ".txt"),
    ],
)
def test_the_extension_comes_from_the_content(data, kind, extension):
    stored = files.store_upload("studio/run_1", data, kind)
    assert stored.name.endswith(extension)
    assert stored.media_type == files.FORMATS[stored.format].media_type


def test_word_upload_is_accepted():
    stored = files.store_upload("studio/run_1", docx_bytes(), "document")
    assert stored.format == "docx" and stored.name.endswith(".docx")


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (HTML, "audio"),
        (PDF, "audio"),
        (b"MZ\x90\x00" + b"\x00" * 60, "audio"),
        (b"#EXTM3U\nhttp://example.invalid/a.m3u8\n", "audio"),
        (OGG, "document"),
        (zip_bytes(["a.txt"]), "document"),
        (b"MZ\x90\x00\x03\x00\x00\x00", "document"),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "document"),
    ],
)
def test_upload_of_another_kind_is_a_415_and_leaves_nothing_behind(data, kind, settings):
    with pytest.raises(AppError) as refused:
        files.store_upload("uploads", data, kind)
    assert (refused.value.status, refused.value.code) == (415, "unsupported_media_type")
    assert "ei tueta" in refused.value.message
    assert list((settings.data_path / "files" / "uploads").iterdir()) == []


def test_empty_upload_is_refused(settings):
    with pytest.raises(AppError) as refused:
        files.store_upload("uploads", b"", "document")
    assert (refused.value.status, refused.value.code) == (400, "empty_file")
    assert list((settings.data_path / "files" / "uploads").iterdir()) == []


def test_upload_over_its_cap_is_a_413_and_leaves_nothing_behind(settings):
    for source in (OGG + b"x" * 5_000, io.BytesIO(OGG + b"x" * 5_000)):
        with pytest.raises(AppError) as refused:
            files.store_upload("uploads", source, "audio", max_bytes=4_096)
        assert refused.value.status == 413
    assert list((settings.data_path / "files" / "uploads").iterdir()) == []
    assert files.store_upload("uploads", OGG + b"x" * 4_032, "audio", max_bytes=4_096).size == 4_096


def test_default_caps_are_the_contract(monkeypatch):
    seen: list[int] = []
    real = limits.copy_capped

    def spy(source, target, max_bytes, **kwargs):
        seen.append(max_bytes)
        return real(source, target, max_bytes, **kwargs)

    monkeypatch.setattr(limits, "copy_capped", spy)
    files.store_upload("uploads", io.BytesIO(OGG), "audio")
    files.store_upload("uploads", io.BytesIO(PDF), "document")
    assert seen == [25 * 1024 * 1024, 15 * 1024 * 1024]
    with pytest.raises(ValueError):
        files.store_upload("uploads", OGG, "video")  # type: ignore[arg-type]


def test_text_upload_is_stored_as_utf8():
    stored = files.store_upload("uploads", "Ääni ja öljy".encode("cp1252"), "document")
    assert stored.path.read_bytes() == "Ääni ja öljy".encode()
    stored = files.store_upload("uploads", "Ääni".encode("utf-16"), "document")
    assert stored.path.read_text(encoding="utf-8") == "Ääni"


def upload_client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/upload/{kind}")
    async def upload(kind: str, file: UploadFile) -> dict[str, str]:
        stored = await files.save_upload("uploads", file, kind)  # type: ignore[arg-type]
        return {"name": stored.name, "url": stored.url, "label": files.safe_name(file.filename)}

    app.include_router(files.router)
    return TestClient(app)


def test_the_clients_file_name_and_type_play_no_part(settings):
    client = upload_client()
    folder = settings.data_path / "files" / "uploads"

    # Audio that claims to be a web page, with a name that tries to leave the folder.
    response = client.post(
        "/upload/audio", files={"file": ("../../..\\evil.html", OGG, "text/html")}
    )
    assert response.status_code == 200
    body = response.json()
    assert SERVER_NAME.fullmatch(body["name"]) and body["name"].endswith(".ogg")
    assert body["label"] == "evil.html"  # a label for the owning record, nothing more
    assert [path.name for path in folder.iterdir()] == [body["name"]]
    assert not (settings.data_path / "evil.html").exists()

    # A web page that claims to be audio.
    refused = client.post("/upload/audio", files={"file": ("aani.ogg", HTML, "audio/ogg")})
    assert refused.status_code == 415
    assert refused.json()["error"]["code"] == "unsupported_media_type"
    assert [path.name for path in folder.iterdir()] == [body["name"]]


def test_an_html_upload_is_never_served_inline(settings):
    client = upload_client()
    response = client.post("/upload/document", files={"file": ("sivu.html", HTML, "text/html")})
    assert response.status_code == 200
    body = response.json()
    assert body["name"].endswith(".txt") and ".html" not in body["name"]

    served = client.get(body["url"])
    assert served.status_code == 200
    assert served.content == HTML
    assert served.headers["content-type"] == "text/plain; charset=utf-8"
    assert served.headers["content-disposition"].startswith("attachment")
    assert served.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in served.headers["content-security-policy"]

    # The name it was uploaded under does not exist on the server at all.
    assert client.get("/api/files/uploads/sivu.html").status_code == 404
    # And an .html file a feature saved itself still goes out as an opaque download.
    files.save_file("uploads", "raportti.html", HTML)
    feature = client.get("/api/files/uploads/raportti.html")
    assert feature.headers["content-type"] == "application/octet-stream"
    assert feature.headers["content-disposition"].startswith("attachment")
    assert feature.headers["x-content-type-options"] == "nosniff"


async def test_save_upload_takes_bytes_files_and_paths(tmp_path):
    source = tmp_path / "lahde.bin"
    source.write_bytes(PDF)
    for upload in (PDF, io.BytesIO(PDF), source):
        stored = await files.save_upload("studio/run_2", upload, "document")
        assert stored.format == "pdf" and stored.path.read_bytes() == PDF
    with source.open("rb") as handle:
        handle.read()  # already read to the end: the upload is rewound
        assert (await files.save_upload("studio/run_2", handle, "document")).size == len(PDF)
    assert source.read_bytes() == PDF  # the source is copied, never moved


# audio conversion ----------------------------------------------------------------------------


def encode_tone(folder: Path, extension: str, arguments: list[str]) -> Path:
    """A 1.5 s tone in another format, made with ffmpeg itself."""
    source = tone(folder / "lahde.wav", seconds=1.5)
    encoded = folder / f"aani.{extension}"
    made = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(source), *arguments]
        + [str(encoded)],
        capture_output=True,
        check=False,
    )
    if made.returncode != 0:
        pytest.skip(f"this ffmpeg build cannot encode {extension}")
    return encoded


def test_ffmpeg_is_on_the_path():
    """The app needs it for every voice note; the image installs it."""
    assert files.ffmpeg_path() is not None, "install ffmpeg and put it on the PATH"


@needs_ffmpeg
def test_wav_is_converted_to_16_khz_mono(tmp_path):
    source = tone(tmp_path / "aani.wav", seconds=1.0)
    converted = files.to_wav_sync(source, "wav")
    assert converted.path == tmp_path / "aani.16k.wav"
    assert converted.duration_s == pytest.approx(1.0, abs=0.05)
    with wave.open(str(converted.path), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16_000, 1, 2)


@needs_ffmpeg
@pytest.mark.parametrize(
    ("extension", "arguments", "key"),
    [
        ("ogg", ["-c:a", "libopus", "-b:a", "24k", "-ar", "48000"], "ogg"),  # a voice note
        ("webm", ["-c:a", "libopus", "-b:a", "24k", "-ar", "48000"], "webm"),  # a browser recording
        ("mp3", ["-c:a", "libmp3lame", "-b:a", "64k"], "mp3"),
        ("m4a", ["-c:a", "aac", "-b:a", "64k"], "m4a"),
    ],
)
async def test_each_accepted_format_is_detected_and_converted(tmp_path, extension, arguments, key):
    encoded = encode_tone(tmp_path, extension, arguments)

    # Stored the way an upload is: by content, under a name that says nothing of its origin.
    stored = await files.save_upload("reports/rep_1", encoded, "audio")
    assert stored.format == key

    converted = await files.to_wav(stored.path, stored.format)
    assert converted.duration_s == pytest.approx(1.5, abs=0.2)
    with wave.open(str(converted.path), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels()) == (16_000, 1)

    # Without a format the helper detects it from the bytes, never from the name.
    disguised = tmp_path / "aani.txt"
    disguised.write_bytes(encoded.read_bytes())
    assert (await files.to_wav(disguised)).duration_s == pytest.approx(1.5, abs=0.2)


@needs_ffmpeg
def test_input_format_is_forced_so_a_playlist_is_not_followed(tmp_path):
    other = tone(tmp_path / "muu.wav", seconds=1.0)
    playlist = tmp_path / "lista.ogg"
    playlist.write_text(f"ffconcat version 1.0\nfile '{other.as_posix()}'\n", encoding="utf-8")

    # Left to probe, ffmpeg reads the playlist and opens the file it names. This is what the
    # forced input format prevents.
    probed = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-safe", "0", "-i", str(playlist)]
        + [str(tmp_path / "probed.wav")],
        capture_output=True,
        check=False,
    )
    assert probed.returncode == 0 and (tmp_path / "probed.wav").stat().st_size > 1_000

    with pytest.raises(AppError) as refused:
        files.to_wav_sync(playlist, "ogg")
    assert (refused.value.status, refused.value.code) == (415, "invalid_audio")
    assert not (tmp_path / "lista.16k.wav").exists()

    with pytest.raises(AppError) as undetected:
        files.to_wav_sync(playlist)  # no format given: the bytes are not audio at all
    assert undetected.value.code == "unsupported_media_type"


@needs_ffmpeg
def test_wrong_or_unknown_format_is_refused(tmp_path):
    source = tone(tmp_path / "aani.wav", seconds=0.5)
    for audio_format in ("mp4", "pdf", "txt", "", files.FORMATS["docx"]):
        with pytest.raises(AppError) as refused:
            files.to_wav_sync(source, audio_format)
        assert (refused.value.status, refused.value.code) == (415, "unsupported_media_type")

    garbage = tmp_path / "roska.ogg"
    garbage.write_bytes(OGG + b"\x00" * 2_000)
    with pytest.raises(AppError) as invalid:
        files.to_wav_sync(garbage, "ogg")
    assert invalid.value.code == "invalid_audio"
    assert invalid.value.message == "Äänitiedostoa ei voitu lukea."


@needs_ffmpeg
def test_audio_over_the_time_limit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(limits, "AUDIO_MAX_SECONDS", 1)
    source = tone(tmp_path / "pitka.wav", seconds=4.0)
    with pytest.raises(AppError) as refused:
        files.to_wav_sync(source, "wav")
    assert (refused.value.status, refused.value.code) == (413, "audio_too_long")
    assert not (tmp_path / "pitka.16k.wav").exists()

    short = tone(tmp_path / "lyhyt.wav", seconds=0.8)
    assert files.to_wav_sync(short, "wav").duration_s == pytest.approx(0.8, abs=0.05)


def test_missing_ffmpeg_is_a_503_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "ffmpeg_path", lambda: None)
    source = tone(tmp_path / "aani.wav", seconds=0.2)
    with pytest.raises(AppError) as refused:
        files.to_wav_sync(source, "wav")
    assert (refused.value.status, refused.value.code) == (503, "audio_unavailable")
