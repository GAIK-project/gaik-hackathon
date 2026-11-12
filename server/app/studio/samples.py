"""The Studio's sample material: synthetic files shipped with the app.

The files live in `samples/files/` and are built by `samples/build_samples.py`. A sample is
looked up by its id and a file by its name in the table below, so a client-supplied name is
never joined into a path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi.responses import FileResponse

from app.core.errors import AppError

FILES_DIR = Path(__file__).resolve().parent / "samples" / "files"
URL_PREFIX = "/api/studio/samples"

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".mp3": "audio/mpeg",
}


@dataclass(frozen=True, slots=True)
class Sample:
    id: str
    title: str
    kind: Literal["documents", "audio"]
    names: tuple[str, ...]

    def paths(self) -> list[Path]:
        return [FILES_DIR / name for name in self.names]


SAMPLES: tuple[Sample, ...] = (
    Sample(
        "invoices",
        "Three purchase invoices (Finnish, PDF)",
        "documents",
        ("lasku-pumpputarvike.pdf", "lasku-sahkotukku.pdf", "lasku-tyokaluvuokraus.pdf"),
    ),
    Sample(
        "manual",
        "Pump manual: delivery terms and maintenance (3 pages, PDF)",
        "documents",
        ("vp-200-manual.pdf",),
    ),
    Sample(
        "emails",
        "Four customer emails (Finnish, text)",
        "documents",
        (
            "viesti-1-toimitus.txt",
            "viesti-2-lasku.txt",
            "viesti-3-vika.txt",
            "viesti-4-tarjous.txt",
        ),
    ),
    Sample(
        "site-diary",
        "Dictated construction site diary (Finnish, audio)",
        "audio",
        ("tyomaapaivakirja-sanelu.mp3",),
    ),
)
_BY_ID = {sample.id: sample for sample in SAMPLES}


def _not_found() -> AppError:
    return AppError("sample_not_found", "The sample was not found.", 404)


def get_sample(sample_id: str) -> Sample:
    sample = _BY_ID.get(sample_id)
    if sample is None:
        raise _not_found()
    return sample


def list_samples() -> list[dict[str, Any]]:
    """The body of `GET /api/studio/samples`; a file that is missing on disk is left out."""
    listed = []
    for sample in SAMPLES:
        files = [
            {
                "name": path.name,
                "url": f"{URL_PREFIX}/{sample.id}/{path.name}",
                "size": path.stat().st_size,
            }
            for path in sample.paths()
            if path.is_file()
        ]
        if files:
            listed.append(
                {"id": sample.id, "title": sample.title, "kind": sample.kind, "files": files}
            )
    return listed


def sample_response(sample_id: str, name: str) -> FileResponse:
    sample = get_sample(sample_id)
    if name not in sample.names:
        raise _not_found()
    path = FILES_DIR / sample.names[sample.names.index(name)]
    if not path.is_file():
        raise _not_found()
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(path.suffix, "application/octet-stream"),
        filename=path.name,
        headers={"X-Content-Type-Options": "nosniff"},
    )
