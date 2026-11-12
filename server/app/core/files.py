"""Files under `$DATA_DIR/files/<scope>/`.

A scope is one or more plain segments (`"reports/rep_3fa9c2"`, `"studio"`); a name is one plain
segment. A segment starts with a letter or a digit and holds only letters, digits, `.`, `_`
and `-`, so `..`, separators, drive letters and hidden files cannot be expressed; the resolved
path is checked against the root once more. `safe_name()` turns an upload's own name into such
a segment.

`public_url(scope, name)` is the path the browser fetches: `GET /api/files/<scope>/<name>`,
served by `router` behind the same access rules as the rest of `/api`.
"""

from __future__ import annotations

import mimetypes
import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.core.errors import AppError
from app.core.settings import get_settings

_SEGMENT = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,118}[A-Za-z0-9_-])?$")
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.IGNORECASE)
_INLINE_TYPES = ("audio/", "image/png", "image/jpeg", "image/webp", "application/pdf")

URL_PREFIX = "/api/files"


@dataclass(frozen=True, slots=True)
class StoredFile:
    scope: str
    name: str
    size: int
    path: Path
    url: str


def _invalid() -> AppError:
    return AppError("invalid_file_name", "Tiedoston nimi ei kelpaa.", 400)


def _check_segment(segment: str) -> str:
    if not isinstance(segment, str) or not _SEGMENT.fullmatch(segment):
        raise _invalid()
    if _WINDOWS_RESERVED.fullmatch(segment):
        raise _invalid()
    return segment


def files_root() -> Path:
    return get_settings().data_path / "files"


def scope_dir(scope: str, *, create: bool = False) -> Path:
    """The folder of a scope. Raises `AppError` for anything that is not plain segments."""
    if not isinstance(scope, str) or not scope:
        raise _invalid()
    root = files_root().resolve()
    folder = root.joinpath(*[_check_segment(part) for part in scope.split("/")]).resolve()
    if not folder.is_relative_to(root) or folder == root:
        raise _invalid()
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def file_path(scope: str, name: str, *, create_dir: bool = False) -> Path:
    """The path of one file. The file itself may not exist yet."""
    folder = scope_dir(scope, create=create_dir)
    path = (folder / _check_segment(name)).resolve()
    if path.parent != folder:
        raise _invalid()
    return path


def safe_name(name: str, *, fallback: str = "tiedosto") -> str:
    """An upload's file name as a plain segment: `"Työmaa päiväkirja (1).docx"` becomes
    `"Tyomaa_paivakirja_1.docx"`. Any directory part is dropped."""
    base = re.split(r"[\\/]", name or "")[-1]
    ascii_name = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_name).strip("._-")
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    stem, dot, suffix = cleaned.rpartition(".")
    if dot and stem:
        cleaned = f"{stem[:80].rstrip('._-')}.{suffix[:12]}"
    else:
        cleaned = cleaned[:80].rstrip("._-")
    if not cleaned or not _SEGMENT.fullmatch(cleaned) or _WINDOWS_RESERVED.fullmatch(cleaned):
        return fallback
    return cleaned


def public_url(scope: str, name: str) -> str:
    """The URL path of a stored file, for `ReportFile.url` and the like."""
    file_path(scope, name)  # validates
    return f"{URL_PREFIX}/{quote(scope)}/{quote(name)}"


def _stored(scope: str, path: Path) -> StoredFile:
    return StoredFile(
        scope=scope,
        name=path.name,
        size=path.stat().st_size,
        path=path,
        url=public_url(scope, path.name),
    )


def save_file(scope: str, name: str, data: bytes | BinaryIO) -> StoredFile:
    """Write a file (replacing one of the same name) and return where it is."""
    path = file_path(scope, name, create_dir=True)
    temporary = path.with_name(f"{path.name}.part")
    with temporary.open("wb") as handle:
        if isinstance(data, (bytes, bytearray, memoryview)):
            handle.write(data)
        else:
            shutil.copyfileobj(data, handle)
    temporary.replace(path)
    return _stored(scope, path)


def open_file(scope: str, name: str) -> BinaryIO:
    """Open a stored file for reading. Raises `AppError` 404 when there is none."""
    path = file_path(scope, name)
    if not path.is_file():
        raise AppError("file_not_found", "Tiedostoa ei löytynyt.", 404)
    return path.open("rb")


def get_file(scope: str, name: str) -> StoredFile | None:
    path = file_path(scope, name)
    return _stored(scope, path) if path.is_file() else None


def list_files(scope: str) -> list[StoredFile]:
    """The files of a scope by name; folders and unfinished writes are left out."""
    folder = scope_dir(scope)
    if not folder.is_dir():
        return []
    return [
        _stored(scope, path)
        for path in sorted(folder.iterdir(), key=lambda item: item.name.lower())
        if path.is_file() and _SEGMENT.fullmatch(path.name) and path.suffix != ".part"
    ]


def delete_file(scope: str, name: str) -> bool:
    path = file_path(scope, name)
    if not path.is_file():
        return False
    path.unlink()
    return True


def file_response(scope: str, name: str, *, download_name: str | None = None) -> FileResponse:
    """A response for one stored file. Types a browser could run are sent as a download."""
    path = file_path(scope, name)
    if not path.is_file():
        raise AppError("file_not_found", "Tiedostoa ei löytynyt.", 404)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    inline = media_type.startswith(_INLINE_TYPES)
    return FileResponse(
        path,
        media_type=media_type,
        filename=download_name or path.name,
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=0"},
    )


router = APIRouter()


@router.get(f"{URL_PREFIX}/{{location:path}}", include_in_schema=False)
async def serve_file(location: str) -> FileResponse:
    scope, _, name = location.rpartition("/")
    return file_response(scope, name)
