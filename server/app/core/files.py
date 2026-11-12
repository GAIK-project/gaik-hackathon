"""Files under `$DATA_DIR/files/<scope>/` (docs/ARCHITECTURE.md section 15.4).

A scope is one or more plain segments (`"reports/rep_3fa9c2"`, `"studio"`); a name is one plain
segment. A segment starts with a letter or a digit and holds only letters, digits, `.`, `_`
and `-`, so `..`, separators, drive letters and hidden files cannot be expressed; the resolved
path is checked against the root once more.

Uploads are accepted by their first bytes, never by their name or declared type, and stored
under a name the server makes up:

    stored = await save_upload("reports/rep_1", upload, "audio")      # ogg, webm, mp3, wav, m4a
    stored = await save_upload("studio/run_1", upload, "document")    # PDF, DOCX, TXT
    stored.name, stored.format, stored.media_type                     # "f_9c1e....ogg", "ogg", ...
    label = safe_name(upload.filename)       # for display only; keep it in the owning record

Never join a client-supplied name into a path: keep `stored.name` in the record that owns the
file and look it up there. A TXT upload is stored as UTF-8 whatever it came in as.

Audio goes to gaik's Transcriber as 16 kHz mono WAV, converted with the detected input format
so ffmpeg never guesses what a file is:

    wav = await to_wav(stored.path, stored.format)     # ConvertedAudio(path, duration_s)

`public_url(scope, name)` is the path the browser fetches: `GET /api/files/<scope>/<name>`,
served by `router` behind the same access rules as the rest of `/api`. Every stored file is
served as a download (`Content-Disposition: attachment`, `nosniff`) with a content type from
the allowlist below; anything else, an `.html` file included, goes out as
`application/octet-stream`.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import subprocess
import unicodedata
import wave
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal
from urllib.parse import quote

import anyio
from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile

from app.core import limits
from app.core.errors import AppError
from app.core.settings import get_settings

logger = logging.getLogger(__name__)

_SEGMENT = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,118}[A-Za-z0-9_-])?$")
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.IGNORECASE)

URL_PREFIX = "/api/files"

FileKind = Literal["audio", "document"]


@dataclass(frozen=True, slots=True)
class FileFormat:
    """One accepted format: how it is stored, served and (for audio) read by ffmpeg."""

    key: str  # "ogg", "pdf", ...
    kind: FileKind
    extension: str  # with the dot
    media_type: str
    ffmpeg: str | None = None  # the ffmpeg demuxer name, for audio


FORMATS: dict[str, FileFormat] = {
    item.key: item
    for item in (
        FileFormat("ogg", "audio", ".ogg", "audio/ogg", "ogg"),
        FileFormat("webm", "audio", ".webm", "audio/webm", "matroska"),
        FileFormat("mp3", "audio", ".mp3", "audio/mpeg", "mp3"),
        FileFormat("wav", "audio", ".wav", "audio/wav", "wav"),
        FileFormat("m4a", "audio", ".m4a", "audio/mp4", "mov"),
        FileFormat("pdf", "document", ".pdf", "application/pdf"),
        FileFormat(
            "docx",
            "document",
            ".docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        FileFormat("txt", "document", ".txt", "text/plain; charset=utf-8"),
    )
}
_MEDIA_TYPES: dict[str, str] = {item.extension: item.media_type for item in FORMATS.values()}
_FALLBACK_MEDIA_TYPE = "application/octet-stream"

_UNSUPPORTED: dict[str, str] = {
    "audio": "Äänitiedoston muotoa ei tueta. Käytä ogg-, webm-, mp3-, wav- tai m4a-tiedostoa.",
    "document": "Tiedostomuotoa ei tueta. Käytä PDF-, DOCX- tai TXT-tiedostoa.",
}
_MAX_BYTES: dict[str, int] = {
    "audio": limits.AUDIO_MAX_BYTES,
    "document": limits.DOCUMENT_MAX_BYTES,
}

# Major brands of the ISO base media format that hold audio (m4a, mp4, 3gp, QuickTime).
_MP4_BRANDS = frozenset(
    {
        b"M4A ",
        b"M4B ",
        b"mp41",
        b"mp42",
        b"isom",
        b"iso2",
        b"iso4",
        b"iso5",
        b"iso6",
        b"dash",
        b"3gp4",
        b"3gp5",
        b"3gp6",
        b"qt  ",
    }
)
# What a text file does not contain: C0 controls other than tab, line feed, form feed and
# carriage return, and DEL.
_NOT_TEXT = frozenset(set(range(0x20)) - {0x09, 0x0A, 0x0C, 0x0D}) | {0x7F}
_HEAD_BYTES = 64

WAV_RATE = 16_000
FFMPEG_TIMEOUT_SECONDS = 120


@dataclass(frozen=True, slots=True)
class StoredFile:
    scope: str
    name: str
    size: int
    path: Path
    url: str
    format: str = ""  # a key of FORMATS when the file was accepted by `save_upload`
    media_type: str = _FALLBACK_MEDIA_TYPE


@dataclass(frozen=True, slots=True)
class ConvertedAudio:
    path: Path
    duration_s: float


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


def safe_name(name: str | None, *, fallback: str = "tiedosto") -> str:
    """An upload's file name as a plain segment: `"Työmaa päiväkirja (1).docx"` becomes
    `"Tyomaa_paivakirja_1.docx"`. Any directory part is dropped.

    Use the result as a label or a download name only. Uploads are stored under `new_name()`.
    """
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


def new_name(extension: str, *, prefix: str = "f") -> str:
    """A server-generated file name such as `f_3fa9c2d41b7e9a10.ogg`."""
    return _check_segment(f"{prefix}_{secrets.token_hex(8)}{extension}")


def media_type_of(name: str) -> str:
    """The content type a stored file is served with: from the allowlist, by extension."""
    return _MEDIA_TYPES.get(Path(name).suffix.lower(), _FALLBACK_MEDIA_TYPE)


def public_url(scope: str, name: str) -> str:
    """The URL path of a stored file, for `ReportFile.url` and the like."""
    file_path(scope, name)  # validates
    return f"{URL_PREFIX}/{quote(scope)}/{quote(name)}"


def _stored(scope: str, path: Path, file_format: FileFormat | None = None) -> StoredFile:
    return StoredFile(
        scope=scope,
        name=path.name,
        size=path.stat().st_size,
        path=path,
        url=public_url(scope, path.name),
        format=file_format.key if file_format else "",
        media_type=media_type_of(path.name),
    )


# detection ----------------------------------------------------------------------------------


def detect_audio(head: bytes) -> FileFormat | None:
    """The audio format the first bytes of a file say it is, or `None`. Give it 16 bytes or
    more; a file name or a declared content type plays no part."""
    if head.startswith(b"OggS"):
        return FORMATS["ogg"]
    if head.startswith(b"\x1a\x45\xdf\xa3"):  # EBML: WebM and Matroska
        return FORMATS["webm"]
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return FORMATS["wav"]
    if head[4:8] == b"ftyp" and head[8:12] in _MP4_BRANDS:
        return FORMATS["m4a"]
    if head.startswith(b"ID3") or _is_mp3_frame(head):
        return FORMATS["mp3"]
    return None


def _is_mp3_frame(head: bytes) -> bool:
    """An MPEG audio frame header: sync bits and a real version, layer, bitrate and rate."""
    if len(head) < 3 or head[0] != 0xFF or head[1] & 0xE0 != 0xE0:
        return False
    version, layer = head[1] & 0x18, head[1] & 0x06
    bitrate, rate = head[2] & 0xF0, head[2] & 0x0C
    # Layer 0 is reserved, and it is what an ADTS (raw AAC) header has there.
    return version != 0x08 and layer != 0 and bitrate not in (0x00, 0xF0) and rate != 0x0C


def detect_document(path: Path) -> FileFormat | None:
    """PDF, DOCX or TXT by content, or `None`. Blocking (it reads the file)."""
    with path.open("rb") as handle:
        head = handle.read(_HEAD_BYTES)
    if head.startswith(b"%PDF-"):
        return FORMATS["pdf"]
    if head.startswith(b"PK\x03\x04"):
        return FORMATS["docx"] if _is_docx(path) else None
    return FORMATS["txt"] if _decode_text(path.read_bytes()) is not None else None


def _is_docx(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:  # reads the directory only, extracts nothing
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return False
    return "[Content_Types].xml" in names and "word/document.xml" in names


def _decode_text(data: bytes) -> str | None:
    """The text of a plain-text file, or `None` when the bytes are not text."""
    if not data.strip():
        return None
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError:
            return None
        return None if any(ord(char) in _NOT_TEXT for char in text) else text
    if not _NOT_TEXT.isdisjoint(data):
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Older Windows editors save Finnish text as Windows-1252.
        return data.decode("cp1252", errors="replace")


# uploads ------------------------------------------------------------------------------------


def store_upload(
    scope: str,
    source: bytes | BinaryIO | Path,
    kind: FileKind,
    *,
    max_bytes: int | None = None,
) -> StoredFile:
    """Accept one uploaded file by its content and store it under a server-generated name.

    Blocking; `save_upload` is the async form. Raises `AppError` 413 when the content is over
    `max_bytes` (default: 25 MB for audio, 15 MB for a document; checked while copying), 400
    when it is empty and 415 when it is not one of the accepted formats.
    """
    if kind not in _MAX_BYTES:
        raise ValueError("kind is audio or document")
    cap = _MAX_BYTES[kind] if max_bytes is None else max_bytes
    folder = scope_dir(scope, create=True)
    temporary = folder / f"{secrets.token_hex(8)}.part"
    try:
        with temporary.open("wb") as target:
            if isinstance(source, Path):
                with source.open("rb") as handle:
                    size = limits.copy_capped(handle, target, cap)
            elif isinstance(source, (bytes, bytearray, memoryview)):
                limits.check_size(len(source), cap)
                size = target.write(source)
            else:
                size = limits.copy_capped(source, target, cap)
        if size == 0:
            raise AppError("empty_file", "Tiedosto on tyhjä.", 400)
        file_format = _detect(temporary, kind)
        if file_format is None:
            raise AppError("unsupported_media_type", _UNSUPPORTED[kind], 415)
        if file_format.key == "txt":
            text = _decode_text(temporary.read_bytes()) or ""
            temporary.write_bytes(text.encode("utf-8"))
        path = folder / new_name(file_format.extension)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return _stored(scope, path, file_format)


def _detect(path: Path, kind: FileKind) -> FileFormat | None:
    if kind == "document":
        return detect_document(path)
    with path.open("rb") as handle:
        return detect_audio(handle.read(_HEAD_BYTES))


async def save_upload(
    scope: str,
    upload: UploadFile | bytes | BinaryIO | Path,
    kind: FileKind,
    *,
    max_bytes: int | None = None,
) -> StoredFile:
    """`store_upload` in a worker thread. `upload` is FastAPI's `UploadFile`, bytes (decoded
    `content_base64`), an open binary file or a path."""
    source = upload.file if isinstance(upload, UploadFile) else upload
    if not isinstance(source, (bytes, bytearray, memoryview, Path)) and source.seekable():
        source.seek(0)
    return await anyio.to_thread.run_sync(
        lambda: store_upload(scope, source, kind, max_bytes=max_bytes)
    )


# audio conversion ---------------------------------------------------------------------------


def ffmpeg_path() -> str | None:
    """Where ffmpeg is on the PATH, or `None`."""
    return shutil.which("ffmpeg")


def to_wav_sync(
    source: Path, audio_format: FileFormat | str | None = None, target: Path | None = None
) -> ConvertedAudio:
    """Convert audio to 16 kHz mono 16-bit WAV: `ffmpeg -f <format> -i in -ar 16000 -ac 1 out`.

    `audio_format` is what `save_upload` detected (`stored.format`) or a `FileFormat`; without
    it the format is detected from the file's first bytes. The input format is always forced,
    so a playlist or a script dressed up as audio is never interpreted. Blocking.

    Raises `AppError` 415 for a file that is not accepted audio or cannot be decoded, 413
    when it is longer than 10 minutes and 503 when ffmpeg is not installed.
    """
    if isinstance(audio_format, str):
        file_format = FORMATS.get(audio_format)
    elif audio_format is None:
        with source.open("rb") as handle:
            file_format = detect_audio(handle.read(_HEAD_BYTES))
    else:
        file_format = audio_format
    if file_format is None or file_format.kind != "audio" or not file_format.ffmpeg:
        raise AppError("unsupported_media_type", _UNSUPPORTED["audio"], 415)
    executable = ffmpeg_path()
    if executable is None:
        logger.error("ffmpeg is not on the PATH: audio cannot be converted")
        raise AppError("audio_unavailable", "Äänen käsittely ei ole juuri nyt käytettävissä.", 503)

    target = target or source.with_name(f"{source.stem}.16k.wav")
    command = [
        executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        file_format.ffmpeg,
        "-i",
        str(source),
        "-vn",
        # One second over the limit is enough to tell that the audio is too long.
        "-t",
        str(limits.AUDIO_MAX_SECONDS + 1),
        "-ar",
        str(WAV_RATE),
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(target),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        failed = result.returncode != 0
        detail = result.stderr.decode("utf-8", "replace").strip()[-400:]
    except subprocess.TimeoutExpired:
        failed, detail = True, "timed out"
    duration = 0.0 if failed else _wav_seconds(target)
    if failed or duration <= 0:
        target.unlink(missing_ok=True)
        logger.warning("ffmpeg could not convert %s audio: %s", file_format.key, detail)
        raise AppError("invalid_audio", "Äänitiedostoa ei voitu lukea.", 415)
    try:
        limits.check_audio_seconds(duration)
    except AppError:
        target.unlink(missing_ok=True)
        raise
    return ConvertedAudio(path=target, duration_s=duration)


def _wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() / float(audio.getframerate() or WAV_RATE)
    except (wave.Error, EOFError, OSError):
        return 0.0


async def to_wav(
    source: Path, audio_format: FileFormat | str | None = None, target: Path | None = None
) -> ConvertedAudio:
    """`to_wav_sync` in a worker thread."""
    return await anyio.to_thread.run_sync(lambda: to_wav_sync(source, audio_format, target))


# stored files -------------------------------------------------------------------------------


def save_file(scope: str, name: str, data: bytes | BinaryIO) -> StoredFile:
    """Write a file the server made (a Word report, an export) under a name the server chose,
    replacing one of the same name. Uploads go through `save_upload` instead."""
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


def _download_name(name: str | None) -> str:
    """A name for `Content-Disposition`: the last path part, printable characters only.
    Finnish letters stay; Starlette encodes them (and quotes) for the header."""
    base = re.split(r"[\\/]", name or "")[-1]
    return "".join(char for char in base if char.isprintable()).strip(" .")[:150]


def file_response(scope: str, name: str, *, download_name: str | None = None) -> FileResponse:
    """A response for one stored file: always a download, never rendered by the browser.

    The content type comes from the allowlist by the stored name's extension; any other file
    is `application/octet-stream`. `download_name` is the name the browser saves it under.
    """
    path = file_path(scope, name)
    if not path.is_file():
        raise AppError("file_not_found", "Tiedostoa ei löytynyt.", 404)
    return FileResponse(
        path,
        media_type=media_type_of(path.name),
        filename=_download_name(download_name) or path.name,
        content_disposition_type="attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=0",
        },
    )


router = APIRouter()


@router.get(f"{URL_PREFIX}/{{location:path}}", include_in_schema=False)
async def serve_file(location: str) -> FileResponse:
    scope, _, name = location.rpartition("/")
    return file_response(scope, name)
