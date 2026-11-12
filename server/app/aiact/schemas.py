"""Wire types of the AI Act checker (docs/ARCHITECTURE.md sections 8.4 and 15.8)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Tier = Literal["prohibited", "high", "transparency", "minimal"]
TIERS: tuple[Tier, ...] = ("prohibited", "high", "transparency", "minimal")
Who = Literal["provider", "deployer", "both"]


class Citation(BaseModel):
    unit_id: str  # "annex-III-4-a", "art-50-1"
    label: str  # the regulation's own citation style: "Annex III, point 4(a)"
    quote: str  # verbatim words from the regulation; empty when the unit is cited by number only
    verified: bool  # the quote was found in the unit's text


class Obligation(BaseModel):
    title: str
    summary: str
    who: Who
    citation: Citation
    applies_from: str  # ISO date from Article 113 as amended by Regulation (EU) 2026/1744


class Assessment(BaseModel):
    id: str
    created_at: str
    description: str
    tier: Tier
    probabilities: dict[str, float]
    confidence: float
    needs_human_review: bool
    reasoning: str
    basis: list[Citation]
    obligations: list[Obligation]
    also_transparency: bool = False
    derogation_note: str | None = None
    corpus_version: str
    language: Literal["en"] = "en"
    disclaimer: str


class UnitPoint(BaseModel):
    n: str | None
    text: str


class UnitParagraph(BaseModel):
    n: str | None
    text: str
    points: list[UnitPoint] = Field(default_factory=list)


class Unit(BaseModel):
    id: str
    kind: str  # "recital" | "article" | "annex"
    label: str
    title: str | None
    text: str
    paragraphs: list[UnitParagraph]


class SearchHit(BaseModel):
    unit_id: str
    label: str
    snippet: str
    score: float


class Step(BaseModel):
    id: str
    label: str
    status: Literal["running", "done", "error"]
    ms: int | None = None
    component: str | None = None
    probabilities: dict[str, float] | None = None  # set when the tier estimate is ready


class Example(BaseModel):
    id: str
    title: str
    description: str


class EvalCase(BaseModel):
    id: str
    title: str
    expected: Tier
    got: Tier
    ok: bool


class EvalResults(BaseModel):
    ran_at: str
    total: int
    correct: int
    cases: list[EvalCase]
