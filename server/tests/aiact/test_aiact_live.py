"""Live smoke tests: real embedding, Jev and chat calls. Synthetic descriptions only."""

from __future__ import annotations

import json

import pytest

from app.aiact import corpus, service

pytestmark = pytest.mark.live


async def test_search_finds_the_provision_for_a_finnish_question():
    hits = await service.search("tunteiden tunnistaminen työpaikalla", k=5)
    assert "art-5-1-f" in [hit.unit_id for hit in hits]


async def test_assess_a_recruitment_system_end_to_end():
    steps = []
    result = await service.assess(
        "A tool ranks job applicants by their CVs and proposes who to reject.",
        on_step=steps.append,
    )
    assert result.tier == "high"
    assert abs(sum(result.probabilities.values()) - 1) < 0.05
    assert result.basis[0].unit_id == "annex-III-4-a" and result.basis[0].verified
    assert corpus.quote_in(corpus.get("annex-III-4-a").text, result.basis[0].quote)
    assert all(o.citation.verified or o.citation.quote == "" for o in result.obligations)
    assert {step.id for step in steps} >= {"retrieve", "classify", "provision", "verify"}
    assert service.get_assessment(result.id) is not None


async def test_assess_a_finnish_description_of_a_minimal_system():
    result = await service.assess("Malli ennustaa leipomon tuotteiden menekkiä sään perusteella.")
    assert result.tier == "minimal" and result.obligations == []


def test_assess_over_http_streams_a_result(client):
    response = client.post(
        "/api/ai-act/assess",
        json={"description": "A chatbot answers a library's customers about opening hours."},
    )
    blocks = [block for block in response.text.strip().split("\n\n") if block]
    names = [block.split("\n")[0].removeprefix("event: ") for block in blocks]
    assert names[-2:] == ["result", "done"]
    data = json.loads(blocks[-2].split("\ndata: ", 1)[1])
    assert data["assessment"]["tier"] == "transparency"
    assert data["assessment"]["basis"][0]["unit_id"] == "art-50-1"
