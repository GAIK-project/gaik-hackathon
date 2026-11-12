"""Run the evaluation cases once and write `data/ai-act/eval-results.json`.

    uv run python -m app.aiact.evaluate            (from server/, real model calls)

The results file has the shape of `GET /api/ai-act/eval`. What each method said per case goes
to `eval-details.json` beside it, for reading the misses. Nothing is stored in the app's
database.
"""

from __future__ import annotations

import asyncio
import json

from app.aiact import assess, corpus
from app.aiact.schemas import EvalCase, EvalResults
from app.core.errors import AppError
from app.core.util import now_iso

CASES_FILE = corpus.DATA_DIR / "eval-cases.en.json"
RESULTS_FILE = corpus.DATA_DIR / "eval-results.json"
DETAILS_FILE = corpus.DATA_DIR / "eval-details.json"
CONCURRENCY = 4


async def _one(case: dict, gate: asyncio.Semaphore) -> tuple[EvalCase, dict]:
    async with gate:
        try:
            assessment, methods = await assess.run(case["description"], store=False)
        except AppError as error:
            # A failed call is a miss, reported as such: never a silent pass.
            result = EvalCase(
                id=case["id"],
                title=case["title"],
                expected=case["expected"],
                got="minimal",
                ok=False,
            )
            return result, {"id": case["id"], "error": error.code}
    result = EvalCase(
        id=case["id"],
        title=case["title"],
        expected=case["expected"],
        got=assessment.tier,
        ok=assessment.tier == case["expected"],
    )
    cited = {citation.unit_id for citation in assessment.basis}
    wanted = set(case["provisions"])
    detail = {
        "id": case["id"],
        "expected": case["expected"],
        "got": assessment.tier,
        **methods,
        "confidence": assessment.confidence,
        "needs_human_review": assessment.needs_human_review,
        "also_transparency": assessment.also_transparency,
        "quotes_verified": all(c.verified for c in assessment.basis),
        "provision_ok": not wanted
        or any(c == w or c.startswith(w + "-") for c in cited for w in wanted),
    }
    return result, detail


async def main() -> EvalResults:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    gate = asyncio.Semaphore(CONCURRENCY)
    pairs = await asyncio.gather(*(_one(case, gate) for case in cases))
    results = EvalResults(
        ran_at=now_iso(),
        total=len(pairs),
        correct=sum(1 for result, _ in pairs if result.ok),
        cases=[result for result, _ in pairs],
    )
    RESULTS_FILE.write_text(
        json.dumps(results.model_dump(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    DETAILS_FILE.write_text(
        json.dumps([detail for _, detail in pairs], ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return results


if __name__ == "__main__":
    done = asyncio.run(main())
    print(f"{done.correct}/{done.total} correct")
    for item in done.cases:
        if not item.ok:
            print(f"  miss {item.id} {item.title}: expected {item.expected}, got {item.got}")
