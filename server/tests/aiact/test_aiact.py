"""AI Act checker without network: the corpus, the quote check, the obligations table, the
decision rule and the HTTP routes, with fakes for the three model calls."""

from __future__ import annotations

import io
import json

import pytest
from docx import Document

from app.aiact import assess, corpus, index, obligations, service
from app.aiact.router import EXAMPLES
from app.aiact.schemas import TIERS
from app.core import gaik_ops, llm
from app.core.errors import AppError
from app.core.models import Classification

CV = "A system scores job applications and suggests who is invited to an interview."


def classification(label: str, confidence: float = 0.9) -> Classification:
    rest = round((1 - confidence) / 3, 4)
    probabilities = {tier: (confidence if tier == label else rest) for tier in TIERS}
    return Classification(
        label=label, confidence=confidence, probabilities=probabilities, model="fake"
    )


def pick(tier: str, unit_id: str | None, quote: str | None, **extra) -> assess._Pick:
    fields = {
        "tier": tier,
        "deciding_unit_id": unit_id,
        "quote": quote,
        "transparency_unit_id": None,
        "transparency_quote": None,
        "derogation_plausible": False,
        "possible_2026_prohibition": False,
        "reasoning": "The system screens job applicants.",
    }
    return assess._Pick(**{**fields, **extra})


RECRUITMENT_QUOTE = "to analyse and filter job applications, and to evaluate candidates"


@pytest.fixture
def fakes(monkeypatch):
    """Fake model calls. Change `state` to steer what they answer."""
    state = {
        "jev": classification("high"),
        "pick": pick("high", "annex-III-4-a", RECRUITMENT_QUOTE),
        "jev_texts": [],
        "embedded": [],
    }

    async def fake_jev(text, labels, instructions=None):
        state["jev_texts"].append(text)
        if isinstance(state["jev"], Exception):
            raise state["jev"]
        return state["jev"]

    async def fake_azure(text, labels, instructions=None):
        return classification("high", 0.7)

    async def fake_chat(system, user, schema_model, **options):
        state["prompt"] = user
        if isinstance(state["pick"], Exception):
            raise state["pick"]
        return state["pick"]

    async def fake_embed(texts):
        state["embedded"].append(texts)
        raise AppError("embedding_failed", "ei onnistu", 502)

    monkeypatch.setattr(gaik_ops, "classify_jev", fake_jev)
    monkeypatch.setattr(gaik_ops, "classify_azure", fake_azure)
    monkeypatch.setattr(gaik_ops, "embed_texts", fake_embed)
    monkeypatch.setattr(llm, "structured_chat", fake_chat)
    return state


# corpus -------------------------------------------------------------------------------------


def test_units_carry_the_regulations_citation_style():
    assert corpus.get("annex-III-4-a").label == "Annex III, point 4(a)"
    assert corpus.get("art-5-1-c").label == "Article 5(1)(c)"
    assert corpus.get("art-50-2").label == "Article 50(2)"
    assert corpus.get("art-16").label == "Article 16"
    assert "social score" in corpus.get("art-5-1-c").text
    assert corpus.get("art-5-1-z") is None
    assert corpus.get("art-999") is None
    assert corpus.get("../etc") is None


def test_only_unchanged_text_may_be_quoted():
    for unit_id in ("art-5-1-a", "art-6-3", "art-50-6", "annex-III-2", "art-9-1", "art-26-2"):
        assert corpus.may_quote(unit_id), unit_id
    for unit_id in ("art-4", "art-10", "art-11-1", "art-27", "art-50-7", "art-113", "annex-I"):
        assert not corpus.may_quote(unit_id), unit_id


def test_quote_check_normalises_whitespace_quotes_and_dashes():
    text = corpus.get("art-13-1").text
    assert "’" in text  # the corpus keeps the typographic apostrophe
    assert corpus.quote_in(text, "to interpret a  system's output\nand use it appropriately")
    assert corpus.quote_in(text, "sufficiently transparent ... use it appropriately")
    assert not corpus.quote_in(text, "deployers must always understand the system")
    assert not corpus.quote_in(text, "system")  # too short to prove anything


async def test_amended_article_is_served_with_a_note():
    unit = await service.get_unit("art-10")
    assert "amended by Regulation (EU) 2026/1744" in unit.title
    assert "amended" not in (await service.get_unit("art-9")).title
    assert await service.get_unit("nope") is None


# index --------------------------------------------------------------------------------------


def test_chunks_cover_the_deciding_provisions_and_skip_amended_text():
    ids = [chunk.unit_id for chunk in index.chunks()]
    assert len(ids) == len(set(ids))
    for unit_id in ("art-5-1-a", "art-5-1-h", "art-6-3", "art-50-4", "annex-III-2", "art-26-11"):
        assert unit_id in ids
    assert "art-50-7" not in ids and "art-4" not in ids
    assert not any(unit_id.startswith(("art-10-", "art-11-", "art-27-")) for unit_id in ids)
    assert len([i for i in assess.candidate_ids() if i.startswith("annex-III-")]) == 25


def test_committed_index_matches_the_chunks():
    index._vectors.cache_clear()
    vectors = index._vectors()
    assert vectors is not None, "run: uv run python -m app.aiact.index"
    assert vectors.shape[0] == len(index.chunks())
    meta = json.loads(index.CHUNKS_FILE.read_text(encoding="utf-8"))
    assert [chunk["unit_id"] for chunk in meta["chunks"]] == [c.unit_id for c in index.chunks()]


async def test_search_falls_back_to_keywords_when_embedding_fails(fakes):
    hits = await service.search("untargeted scraping of facial images from CCTV footage")
    assert fakes["embedded"], "the query was sent for embedding"
    assert hits[0].unit_id == "art-5-1-e"
    assert hits[0].label == "Article 5(1)(e)"
    assert await service.search("   ") == []


# obligations --------------------------------------------------------------------------------


def test_every_quote_in_the_obligations_table_is_in_the_regulation():
    for row in obligations.all_rows():
        citation = obligations.citation_for(row.unit_id, row.quote)
        if row.quote is None:
            assert not corpus.may_quote(row.unit_id), row.unit_id
            assert citation.quote == "" and not citation.verified
        else:
            assert citation.verified, f"{row.unit_id}: {row.quote}"


def test_obligations_follow_the_tier_and_the_dates_of_the_amended_article_113():
    high = obligations.obligations_for("high", "annex-III-4-a")
    assert {o.applies_from for o in high} == {"2027-12-02"}
    assert {o.citation.unit_id for o in high} >= {"art-9-1", "art-10", "art-16", "art-26-11"}
    product = obligations.obligations_for("high", "art-6-1")
    assert {o.applies_from for o in product} == {"2028-08-02"}
    assert "art-27" not in {o.citation.unit_id for o in product}
    assert obligations.obligations_for("prohibited", "art-5-1-c")[0].applies_from == "2025-02-02"
    deepfake = obligations.obligations_for("transparency", "art-50-4")
    assert [o.citation.unit_id for o in deepfake] == ["art-50-4", "art-50-2"]
    assert {o.applies_from for o in deepfake} == {"2026-08-02"}
    assert obligations.obligations_for("minimal", None) == []
    cited = {row.unit_id for row in obligations.all_rows()}
    assert not any(unit_id.startswith("art-4") for unit_id in cited)  # Article 4 is not cited


# decision rule ------------------------------------------------------------------------------


async def test_agreement_gives_a_verified_citation_and_no_review(fakes):
    seen = []
    result = await service.assess(CV, on_step=seen.append)
    assert result.tier == "high" and not result.needs_human_review
    assert result.confidence == 0.9
    assert result.basis[0].unit_id == "annex-III-4-a" and result.basis[0].verified
    assert result.basis[0].label == "Annex III, point 4(a)"
    assert result.basis[1].unit_id == "art-6-2" and result.basis[1].verified
    assert "Article 6(3)" in result.derogation_note
    assert result.corpus_version.startswith("32024R1689 (12.7.2024)")
    assert result.language == "en" and "not legal advice" in result.disclaimer
    assert service.get_assessment(result.id) == result
    done = [step.id for step in seen if step.status == "done"]
    assert set(done) == {"retrieve", "classify", "provision", "verify", "obligations"}
    assert next(s for s in seen if s.probabilities).probabilities["high"] == 0.9
    assert "[annex-III-4-a] Annex III, point 4(a)" in fakes["prompt"]
    assert "[art-5-1-f]" in fakes["prompt"] and "[art-50-1]" in fakes["prompt"]


async def test_a_quote_that_is_not_in_the_text_is_replaced_and_flagged(fakes):
    fakes["pick"] = pick("high", "annex-III-4-a", "AI that hires people is always high-risk")
    result = await service.assess(CV)
    assert result.tier == "high" and result.needs_human_review
    assert result.basis[0].verified
    assert corpus.quote_in(corpus.get("annex-III-4-a").text, result.basis[0].quote)
    assert "not found in the provision" in result.reasoning


async def test_disagreement_is_flagged_and_the_anchored_tier_wins(fakes):
    fakes["jev"] = classification("minimal", 0.55)
    result = await service.assess(CV)
    assert result.tier == "high" and result.needs_human_review
    assert "disagree" in result.reasoning


async def test_a_pick_without_a_matching_provision_does_not_decide(fakes):
    fakes["jev"] = classification("transparency", 0.8)
    fakes["pick"] = pick("prohibited", "annex-III-4-a", RECRUITMENT_QUOTE)
    result = await service.assess(CV)
    assert result.tier == "transparency" and result.needs_human_review
    assert result.basis == []


async def test_low_confidence_and_the_2026_prohibitions_ask_for_review(fakes):
    fakes["jev"] = classification("high", 0.5)
    assert (await service.assess(CV)).needs_human_review
    fakes["jev"] = classification("transparency", 0.9)
    fakes["pick"] = pick(
        "transparency",
        "art-50-4",
        "shall disclose that the content has been artificially generated or manipulated",
        possible_2026_prohibition=True,
    )
    result = await service.assess("An app generates nude images of real people from photos.")
    assert result.needs_human_review and "2026/1744" in result.reasoning


async def test_high_risk_with_a_transparency_duty(fakes):
    fakes["pick"] = pick(
        "high",
        "annex-III-1-c",
        "AI systems intended to be used for emotion recognition",
        transparency_unit_id="art-50-3",
        transparency_quote="shall inform the natural persons exposed thereto",
    )
    result = await service.assess("A shop display reads the customer's emotion from the face.")
    assert result.also_transparency
    assert [c.unit_id for c in result.basis] == ["annex-III-1-c", "art-50-3", "art-6-2"]
    assert result.obligations[-1].citation.unit_id == "art-50-3"
    assert result.obligations[-1].applies_from == "2026-08-02"


async def test_minimal_has_no_basis_and_no_obligations(fakes):
    fakes["jev"] = classification("minimal", 0.95)
    fakes["pick"] = pick("minimal", None, None)
    result = await service.assess("A filter moves spam email into its own folder.")
    assert result.tier == "minimal" and not result.needs_human_review
    assert result.basis == [] and result.obligations == [] and result.derogation_note is None


async def test_jev_gets_the_description_and_a_fallback_is_flagged(fakes):
    await service.assess(CV)
    assert fakes["jev_texts"] == [CV]
    fakes["jev"] = AppError("classification_failed", "Luokittelu ei onnistunut.", 502)
    result = await service.assess(CV)
    assert result.tier == "high" and result.needs_human_review
    assert "calibrated classifier was unavailable" in result.reasoning


async def test_a_failed_model_call_is_an_english_error(fakes):
    fakes["pick"] = AppError("model_failed", "Tekoäly ei vastannut.", 502)
    with pytest.raises(AppError) as caught:
        await service.assess(CV)
    assert caught.value.message == "The model did not answer. Try again in a moment."
    with pytest.raises(AppError) as caught:
        await service.assess("   ")
    assert caught.value.status == 400


# routes -------------------------------------------------------------------------------------


def events(text: str) -> list[tuple[str, dict]]:
    result = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if "event" in lines:
            result.append((lines["event"], json.loads(lines.get("data", "{}"))))
    return result


def test_assess_streams_steps_probabilities_and_the_result(client, fakes):
    response = client.post("/api/ai-act/assess", json={"description": CV})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    stream = events(response.text)
    names = [name for name, _ in stream]
    assert names[-2:] == ["result", "done"] and "partial" in names
    step = next(data for name, data in stream if name == "step")
    assert set(step) == {"id", "label", "status", "ms", "component"}
    partial = next(data for name, data in stream if name == "partial")
    assert partial["probabilities"]["high"] == 0.9
    assessment = stream[-2][1]["assessment"]
    assert assessment["tier"] == "high" and assessment["also_transparency"] is False

    report = client.post("/api/ai-act/report", json={"assessment_id": assessment["id"]})
    assert report.status_code == 200
    assert "attachment" in report.headers["content-disposition"]
    text = "\n".join(p.text for p in Document(io.BytesIO(report.content)).paragraphs)
    assert "Likely risk tier" in text and "Annex III, point 4(a)" in text
    assert "not legal advice" in text and "2.12.2027" in text


def test_a_failed_assessment_ends_the_stream_with_an_error(client, fakes):
    fakes["pick"] = AppError("model_failed", "Tekoäly ei vastannut.", 502)
    stream = events(client.post("/api/ai-act/assess", json={"description": CV}).text)
    assert [name for name, _ in stream][-2:] == ["error", "done"]
    assert stream[-2][1]["message"].startswith("The model did not answer")


def test_routes_for_units_search_examples_report_and_eval(client, fakes):
    unit = client.get("/api/ai-act/units/art-50-1").json()
    assert set(unit) == {"id", "kind", "label", "title", "text", "paragraphs"}
    assert unit["label"] == "Article 50(1)"
    assert client.get("/api/ai-act/units/nope").status_code == 404

    hits = client.get("/api/ai-act/search", params={"q": "creditworthiness"}).json()["hits"]
    assert hits[0]["unit_id"] == "annex-III-5-b"
    assert set(hits[0]) == {"unit_id", "label", "snippet", "score"}

    examples = client.get("/api/ai-act/examples").json()
    assert len(examples) == 6 and set(examples[0]) == {"id", "title", "description"}

    assert client.post("/api/ai-act/report", json={"assessment_id": "aia_x"}).status_code == 404
    assert client.post("/api/ai-act/assess", json={"description": "x"}).status_code == 400


def test_examples_are_not_evaluation_cases_and_the_english_cases_mirror_the_finnish():
    english = json.loads((corpus.DATA_DIR / "eval-cases.en.json").read_text(encoding="utf-8"))
    finnish = json.loads((corpus.DATA_DIR / "eval-cases.fi.json").read_text(encoding="utf-8"))
    key = lambda case: (case["id"], case["expected"], case["provisions"])  # noqa: E731
    assert [key(c) for c in english["cases"]] == [key(c) for c in finnish["cases"]]
    assert all(corpus.get(p) for case in english["cases"] for p in case["provisions"])
    descriptions = {case["description"] for case in english["cases"]}
    assert not descriptions & {example.description for example in EXAMPLES}


def test_routes_need_a_session(anon_client):
    assert anon_client.get("/api/ai-act/examples").status_code == 401
    assert anon_client.post("/api/ai-act/assess", json={"description": CV}).status_code in (
        401,
        403,
    )
