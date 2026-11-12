"""The regulation as data: `data/ai-act/corpus.en.json`, the Official Journal text of 12.7.2024.

    unit = get("annex-III-4-a")          # a corpus unit
    unit = get("art-5-1-c")              # Article 5(1)(c): a point of a paragraph
    unit = get("art-50-2")               # Article 50(2): a paragraph
    ok = quote_in(unit.text, "shall be marked in a machine-readable format")

Corpus ids are `recital-N`, `art-N`, `annex-X`, `annex-III-N[-x]`. On top of them this module
resolves `art-N-P` (paragraph) and `art-N-P-x` (point), so a citation can name the exact
provision in the regulation's own style: "Article 5(1)(c)".

Only provisions that Regulation (EU) 2026/1744 left unchanged may be quoted (`may_quote`);
an amended article is cited by number with a summary.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.aiact.schemas import Unit, UnitParagraph, UnitPoint

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "ai-act"
CORPUS_FILE = DATA_DIR / "corpus.en.json"
CORPUS_VERSION = "32024R1689 (12.7.2024), application dates as amended by Regulation (EU) 2026/1744"

# Articles whose text the 2026 amendment changed in substance (research map, section 9).
AMENDED_ARTICLES = frozenset(
    {1, 2, 3, 4, 5, 6, 10, 11, 17, 25, 27, 28, 29, 30, 40, 42, 43, 50, 56, 57, 58, 60, 63, 64}
    | {69, 70, 72, 75, 76, 77, 95, 96, 97, 99, 111, 113}
)
# Inside three amended articles these paragraphs are word for word the same in the
# consolidated text of 27.7.2026.
_UNCHANGED_PARAGRAPHS = {5: set("12345678"), 6: set("12345678"), 50: set("123456")}

_SUB_ID = re.compile(r"^(art-(\d+))-(\d+)(?:-([a-z]))?$")


@lru_cache(maxsize=1)
def _raw() -> dict[str, dict[str, Any]]:
    units = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))
    return {unit["id"]: unit for unit in units}


def all_ids() -> list[str]:
    return list(_raw())


def raw_unit(unit_id: str) -> dict[str, Any] | None:
    return _raw().get(unit_id)


def _paragraph(paragraph: dict[str, Any]) -> UnitParagraph:
    return UnitParagraph(
        n=paragraph["n"],
        text=paragraph["text"],
        points=[UnitPoint(n=point["n"], text=point["text"]) for point in paragraph["points"]],
    )


@lru_cache(maxsize=1024)
def get(unit_id: str) -> Unit | None:
    """A corpus unit, or a paragraph or point of an article (`art-5-1-c`). None when unknown."""
    raw = _raw().get(unit_id)
    if raw is not None:
        return Unit(
            id=raw["id"],
            kind=raw["kind"],
            label=raw["label"],
            title=raw["title"],
            text=raw["text"],
            paragraphs=[_paragraph(paragraph) for paragraph in raw["paragraphs"]],
        )
    match = _SUB_ID.match(unit_id)
    if match is None:
        return None
    article = _raw().get(match.group(1))
    if article is None:
        return None
    paragraph = next((p for p in article["paragraphs"] if p["n"] == match.group(3)), None)
    if paragraph is None:
        return None
    label = f"{article['label']}({paragraph['n']})"
    letter = match.group(4)
    if letter is None:
        return Unit(
            id=unit_id,
            kind="article",
            label=label,
            title=article["title"],
            text=paragraph["text"],
            paragraphs=[_paragraph(paragraph)],
        )
    point = next((p for p in paragraph["points"] if p["n"] == letter), None)
    if point is None:
        return None
    return Unit(
        id=unit_id,
        kind="article",
        label=f"{label}({letter})",
        title=article["title"],
        text=point["text"],
        paragraphs=[UnitParagraph(n=letter, text=point["text"], points=[])],
    )


def may_quote(unit_id: str) -> bool:
    """False for text the 2026 amendment changed: cite it by number, do not quote it."""
    if not unit_id.startswith("art-"):
        return unit_id.startswith("annex-III")  # Annex III is unchanged
    parts = unit_id.split("-")
    number = int(parts[1])
    if number not in AMENDED_ARTICLES:
        return True
    return len(parts) >= 3 and parts[2] in _UNCHANGED_PARAGRAPHS.get(number, set())


_QUOTES = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "«": '"',
        "»": '"',
        "–": "-",
        "—": "-",
        "‑": "-",
        "‐": "-",
        "−": "-",
        " ": " ",
        " ": " ",
    }
)
_ELLIPSIS = re.compile(r"\[\.\.\.\]|\[…\]|\.\.\.|…")


def normalise(text: str) -> str:
    """Lower case, one kind of quote and dash, single spaces: both sides of a quote check."""
    return re.sub(r"\s+", " ", (text or "").translate(_QUOTES)).strip().lower()


def quote_in(text: str, quote: str) -> bool:
    """True when `quote` occurs in `text` after normalising whitespace, quotes and dashes.
    An ellipsis splits the quote into pieces that must occur in that order."""
    haystack = normalise(text)
    pieces = [normalise(piece).strip(" .,;:") for piece in _ELLIPSIS.split(quote or "")]
    pieces = [piece for piece in pieces if piece]
    if not pieces or sum(len(piece) for piece in pieces) < 12:
        return False
    position = 0
    for piece in pieces:
        found = haystack.find(piece, position)
        if found < 0:
            return False
        position = found + len(piece)
    return True
