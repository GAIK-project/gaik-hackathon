"""Search index over the provisions the checker cites.

Chunks are paragraphs or points: Article 5(1)(a) to (h) and 5(2) to (8), Article 6(1) to (8),
Article 50(1) to (6), the Annex III sub-points, and the obligation articles 9, 12 to 16 and 26
by paragraph. Articles 10, 11 and 27 were amended in 2026: they are indexed by a summary, not
by their 2024 wording.

The vectors are built once with `gaik_ops.embed_texts` and committed under
`data/ai-act/index/` (`vectors.f16.npy`, unit-normalised float16, and `chunks.json`):

    uv run python -m app.aiact.index          (from server/, needs the model environment)

`search` scores a chunk by cosine similarity to the query plus a simple keyword overlap. With
no index on disk, or when the query cannot be embedded, the keyword score alone is used.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from app.aiact import corpus
from app.aiact.schemas import SearchHit
from app.core import gaik_ops
from app.core.errors import AppError

logger = logging.getLogger(__name__)

INDEX_DIR = corpus.DATA_DIR / "index"
VECTORS_FILE = INDEX_DIR / "vectors.f16.npy"
CHUNKS_FILE = INDEX_DIR / "chunks.json"

PARAGRAPH_ARTICLES = (9, 12, 13, 14, 15, 26)
WHOLE_ARTICLES = (16,)
# Amended by Regulation (EU) 2026/1744: found by a summary, cited by number only.
AMENDED_SUMMARIES = {
    "art-10": "Data and data governance: training, validation and testing data sets of a "
    "high-risk AI system must meet quality criteria and be examined for possible bias.",
    "art-11": "Technical documentation of a high-risk AI system is drawn up before it is "
    "placed on the market and kept up to date.",
    "art-27": "Fundamental rights impact assessment: certain deployers assess the impact of a "
    "high-risk AI system on fundamental rights before first use.",
}
AMENDED_NOTE = " (Amended by Regulation (EU) 2026/1744; cited by number, not quoted.)"

VECTOR_WEIGHT = 0.75
KEYWORD_WEIGHT = 0.25
SNIPPET_CHARS = 260

_STOPWORDS = frozenset(
    "the a an of to and or in on for by with that this is are be as it its at from which shall "
    "such any not other into their they than where who whose has have ai system systems".split()
)


@dataclass(frozen=True, slots=True)
class Chunk:
    unit_id: str
    label: str
    text: str  # what is embedded and matched: heading plus provision text

    @property
    def snippet(self) -> str:
        text = self.text
        if len(text) <= SNIPPET_CHARS:
            return text
        return text[:SNIPPET_CHARS].rsplit(" ", 1)[0] + " ..."


def _article_chunks(number: int, paragraphs: tuple[str, ...] | None, points_of: str | None):
    article = corpus.raw_unit(f"art-{number}")
    assert article is not None
    for paragraph in article["paragraphs"]:
        n = paragraph["n"]
        if n is None or (paragraphs is not None and n not in paragraphs):
            continue
        if n == points_of:
            for point in paragraph["points"]:
                unit = corpus.get(f"art-{number}-{n}-{point['n']}")
                assert unit is not None
                yield Chunk(unit.id, unit.label, f"{article['title']}. {unit.text}")
            continue
        unit = corpus.get(f"art-{number}-{n}")
        assert unit is not None
        yield Chunk(unit.id, unit.label, f"{article['title']}. {unit.text}")


@lru_cache(maxsize=1)
def chunks() -> tuple[Chunk, ...]:
    """Every chunk, in a stable order."""
    result: list[Chunk] = []
    result += _article_chunks(5, tuple("12345678"), "1")
    result += _article_chunks(6, tuple("12345678"), None)
    result += _article_chunks(50, tuple("123456"), None)
    for unit_id in corpus.all_ids():
        if not unit_id.startswith("annex-III-"):
            continue
        raw = corpus.raw_unit(unit_id)
        assert raw is not None
        is_leaf = not any(other.startswith(unit_id + "-") for other in corpus.all_ids())
        if is_leaf:
            result.append(Chunk(unit_id, raw["label"], f"{raw['title']}: {raw['text']}"))
    for number in PARAGRAPH_ARTICLES:
        result += _article_chunks(number, None, None)
    for number in WHOLE_ARTICLES:
        raw = corpus.raw_unit(f"art-{number}")
        assert raw is not None
        result.append(Chunk(raw["id"], raw["label"], f"{raw['title']}. {raw['text']}"))
    for unit_id, summary in AMENDED_SUMMARIES.items():
        raw = corpus.raw_unit(unit_id)
        assert raw is not None
        result.append(Chunk(unit_id, raw["label"], summary + AMENDED_NOTE))
    return tuple(result)


def _fingerprint(items: tuple[Chunk, ...]) -> str:
    digest = hashlib.sha256()
    for chunk in items:
        digest.update(chunk.unit_id.encode())
        digest.update(b"\0")
        digest.update(chunk.text.encode())
        digest.update(b"\0")
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _vectors() -> np.ndarray | None:
    """The committed vectors as float32, or None when they are missing or out of date."""
    if not (VECTORS_FILE.is_file() and CHUNKS_FILE.is_file()):
        logger.warning("AI Act index is missing; search uses keywords only")
        return None
    meta = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    vectors = np.load(VECTORS_FILE).astype(np.float32)
    if meta.get("fingerprint") != _fingerprint(chunks()) or len(vectors) != len(chunks()):
        logger.warning("AI Act index does not match the chunks; search uses keywords only")
        return None
    return vectors


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", corpus.normalise(text))
    return {word for word in words if word not in _STOPWORDS}


@lru_cache(maxsize=1)
def _chunk_tokens() -> tuple[set[str], ...]:
    return tuple(_tokens(chunk.text) for chunk in chunks())


def _keyword_scores(query: str) -> np.ndarray:
    wanted = _tokens(query)
    if not wanted:
        return np.zeros(len(chunks()), dtype=np.float32)
    return np.array(
        [len(wanted & have) / len(wanted) for have in _chunk_tokens()], dtype=np.float32
    )


async def search(query: str, k: int = 8) -> list[SearchHit]:
    """The `k` best chunks for a question or a system description."""
    query = (query or "").strip()
    if not query:
        return []
    items = chunks()
    scores = KEYWORD_WEIGHT * _keyword_scores(query)
    vectors = _vectors()
    if vectors is not None:
        try:
            embedded = await gaik_ops.embed_texts([query])
        except AppError:
            logger.warning("Query embedding failed; search uses keywords only")
        else:
            vector = np.asarray(embedded[0], dtype=np.float32)
            norm = float(np.linalg.norm(vector))
            if norm > 0 and vector.shape[0] == vectors.shape[1]:
                scores = scores + VECTOR_WEIGHT * (vectors @ (vector / norm))
    order = np.argsort(-scores)[: max(1, min(k, len(items)))]
    return [
        SearchHit(
            unit_id=items[i].unit_id,
            label=items[i].label,
            snippet=items[i].snippet,
            score=round(float(scores[i]), 4),
        )
        for i in order
        if scores[i] > 0
    ]


async def build() -> int:
    """Embed every chunk and write the index files. Returns the number of chunks."""
    items = chunks()
    embedded = await gaik_ops.embed_texts([chunk.text for chunk in items])
    vectors = np.asarray(embedded, dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(VECTORS_FILE, vectors.astype(np.float16))
    meta = {
        "corpus": corpus.CORPUS_FILE.name,
        "fingerprint": _fingerprint(items),
        "dimensions": int(vectors.shape[1]),
        "chunks": [{"unit_id": chunk.unit_id, "label": chunk.label} for chunk in items],
    }
    CHUNKS_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    _vectors.cache_clear()
    return len(items)


if __name__ == "__main__":
    import asyncio

    print(f"Indexed {asyncio.run(build())} chunks into {INDEX_DIR}")
