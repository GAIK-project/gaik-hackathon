from __future__ import annotations

import io

import pytest

from app.core import files
from app.core.errors import AppError

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


def test_files_are_served_behind_the_access_rules(client, anon_client):
    files.save_file("reports/rep_1", "raportti.docx", b"docx-bytes")
    files.save_file("studio", "sivu.html", b"<script>alert(1)</script>")
    files.save_file("reports/rep_1", "aani.mp3", b"mp3")

    assert anon_client.get("/api/files/reports/rep_1/raportti.docx").status_code == 401

    response = client.get(files.public_url("reports/rep_1", "raportti.docx"))
    assert response.status_code == 200
    assert response.content == b"docx-bytes"
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"

    page = client.get("/api/files/studio/sivu.html")
    assert page.headers["content-disposition"].startswith("attachment")
    assert (
        client.get("/api/files/reports/rep_1/aani.mp3")
        .headers["content-disposition"]
        .startswith("inline")
    )

    missing = client.get("/api/files/reports/rep_1/missing.docx")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "file_not_found"


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
