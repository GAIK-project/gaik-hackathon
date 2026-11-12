"""Obligations per tier: a hand-written, reviewed table.

Each row names the provision, who it binds, a one-line summary and, where the provision is
unchanged by Regulation (EU) 2026/1744, words quoted from it. The quote is checked against the
corpus every time the table is used, so `Citation.verified` is a fact, not a claim. Articles
10, 11 and 27 were amended in 2026: they are cited by number with the summary only.

Application dates (Article 113 as amended): prohibitions since 2.2.2025, Article 50 duties from
2.8.2026, high-risk duties from 2.12.2027 for Annex III systems and from 2.8.2028 for Annex I
products.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.aiact import corpus
from app.aiact.schemas import Citation, Obligation, Tier, Who

PROHIBITIONS_FROM = "2025-02-02"
TRANSPARENCY_FROM = "2026-08-02"
HIGH_ANNEX_III_FROM = "2027-12-02"
HIGH_ANNEX_I_FROM = "2028-08-02"


@dataclass(frozen=True, slots=True)
class Row:
    title: str
    summary: str
    who: Who
    unit_id: str
    quote: str | None  # None: the article was amended in 2026 and is cited by number
    annex_iii_only: bool = False


PROHIBITED: tuple[Row, ...] = (
    Row(
        "Do not place on the market, put into service or use",
        "The practice is banned outright; there is no way to make it compliant.",
        "both",
        "art-5-1",
        "The following AI practices shall be prohibited:",
    ),
)

HIGH: tuple[Row, ...] = (
    Row(
        "Risk management system",
        "Run a documented risk management process over the whole lifecycle of the system.",
        "provider",
        "art-9-1",
        "A risk management system shall be established, implemented, documented and maintained "
        "in relation to high-risk AI systems.",
    ),
    Row(
        "Data and data governance",
        "Train, validate and test with data that meets quality criteria and is examined for "
        "bias. Amended in 2026: read the consolidated text.",
        "provider",
        "art-10",
        None,
    ),
    Row(
        "Technical documentation",
        "Draw up technical documentation before the system is placed on the market and keep "
        "it up to date. Amended in 2026: read the consolidated text.",
        "provider",
        "art-11",
        None,
    ),
    Row(
        "Record-keeping",
        "The system logs events automatically so that its operation can be traced.",
        "provider",
        "art-12-1",
        "High-risk AI systems shall technically allow for the automatic recording of events "
        "(logs) over the lifetime of the system.",
    ),
    Row(
        "Transparency and information to deployers",
        "Supply instructions for use so that deployers can interpret the output correctly.",
        "provider",
        "art-13-1",
        "their operation is sufficiently transparent to enable deployers to interpret a "
        "system's output and use it appropriately",
    ),
    Row(
        "Human oversight",
        "Design the system so that people can oversee it while it is in use.",
        "provider",
        "art-14-1",
        "they can be effectively overseen by natural persons during the period in which they "
        "are in use",
    ),
    Row(
        "Accuracy, robustness and cybersecurity",
        "Reach and keep an appropriate level of accuracy, robustness and cybersecurity.",
        "provider",
        "art-15-1",
        "they achieve an appropriate level of accuracy, robustness, and cybersecurity",
    ),
    Row(
        "Conformity assessment, CE marking and registration",
        "The provider has a quality management system, passes the conformity assessment, "
        "affixes the CE marking and registers the system.",
        "provider",
        "art-16",
        "ensure that the high-risk AI system undergoes the relevant conformity assessment "
        "procedure as referred to in Article 43, prior to its being placed on the market or "
        "put into service",
    ),
    Row(
        "Use according to the instructions",
        "The deployer uses the system as its instructions for use say, and monitors it.",
        "deployer",
        "art-26-1",
        "Deployers of high-risk AI systems shall take appropriate technical and organisational "
        "measures to ensure they use such systems in accordance with the instructions for use",
    ),
    Row(
        "Competent human oversight",
        "The deployer assigns oversight to people with the competence and authority for it.",
        "deployer",
        "art-26-2",
        "Deployers shall assign human oversight to natural persons who have the necessary "
        "competence, training and authority, as well as the necessary support.",
    ),
    Row(
        "Tell the people concerned",
        "People are told when a high-risk system makes or assists decisions about them.",
        "deployer",
        "art-26-11",
        "shall inform the natural persons that they are subject to the use of the high-risk "
        "AI system",
        annex_iii_only=True,
    ),
    Row(
        "Fundamental rights impact assessment",
        "Public bodies, providers of public services and deployers of credit scoring and life "
        "and health insurance systems assess the impact before first use. Amended in 2026: "
        "read the consolidated text.",
        "deployer",
        "art-27",
        None,
        annex_iii_only=True,
    ),
)

TRANSPARENCY: dict[str, Row] = {
    "art-50-1": Row(
        "Tell people they are talking to an AI system",
        "People who interact with the system are told so, unless it is obvious.",
        "provider",
        "art-50-1",
        "the natural persons concerned are informed that they are interacting with an AI system",
    ),
    "art-50-2": Row(
        "Mark generated content",
        "Synthetic audio, image, video and text is marked in a machine-readable way. Systems "
        "placed on the market before 2.8.2026 have until 2.12.2026 (Article 111(4)).",
        "provider",
        "art-50-2",
        "the outputs of the AI system are marked in a machine-readable format and detectable "
        "as artificially generated or manipulated",
    ),
    "art-50-3": Row(
        "Tell people about emotion recognition or biometric categorisation",
        "People exposed to the system are told that it operates.",
        "deployer",
        "art-50-3",
        "shall inform the natural persons exposed thereto of the operation of the system",
    ),
    "art-50-4": Row(
        "Disclose deep fakes and AI-written public-interest text",
        "Whoever publishes the content says that it was artificially generated or manipulated.",
        "deployer",
        "art-50-4",
        "shall disclose that the content has been artificially generated or manipulated",
    ),
}
# Marking by the provider and disclosure by the deployer concern the same content.
_TRANSPARENCY_PAIRS = {"art-50-2": ("art-50-4",), "art-50-4": ("art-50-2",)}


def all_rows() -> tuple[Row, ...]:
    return (*PROHIBITED, *HIGH, *TRANSPARENCY.values())


def citation_for(unit_id: str, quote: str | None) -> Citation:
    """A citation whose `verified` says whether the quote is in the unit's text. A unit that
    may not be quoted gets an empty quote."""
    unit = corpus.get(unit_id)
    if unit is None:
        return Citation(unit_id=unit_id, label=unit_id, quote="", verified=False)
    if not quote or not corpus.may_quote(unit_id):
        return Citation(unit_id=unit_id, label=unit.label, quote="", verified=False)
    return Citation(
        unit_id=unit_id, label=unit.label, quote=quote, verified=corpus.quote_in(unit.text, quote)
    )


def _obligation(row: Row, applies_from: str) -> Obligation:
    return Obligation(
        title=row.title,
        summary=row.summary,
        who=row.who,
        citation=citation_for(row.unit_id, row.quote),
        applies_from=applies_from,
    )


def transparency_paragraph(unit_id: str | None) -> str | None:
    """`art-50-1` .. `art-50-4` for a unit id inside Article 50(1) to (4), else None."""
    if not unit_id:
        return None
    key = "-".join(unit_id.split("-")[:3])
    return key if key in TRANSPARENCY else None


def _transparency(unit_id: str | None) -> list[Obligation]:
    key = transparency_paragraph(unit_id) or "art-50-1"
    keys = (key, *_TRANSPARENCY_PAIRS.get(key, ()))
    return [_obligation(TRANSPARENCY[item], TRANSPARENCY_FROM) for item in keys]


def obligations_for(
    tier: Tier, deciding_unit_id: str | None, transparency_unit_id: str | None = None
) -> list[Obligation]:
    """The obligations of a tier. `deciding_unit_id` picks the application date of a high-risk
    system (Annex III or Annex I) and the Article 50 paragraph of a transparency system;
    `transparency_unit_id` adds the Article 50 duty that applies on top of a high-risk tier."""
    if tier == "prohibited":
        return [_obligation(row, PROHIBITIONS_FROM) for row in PROHIBITED]
    if tier == "transparency":
        return _transparency(deciding_unit_id)
    if tier == "high":
        annex_iii = not (deciding_unit_id or "").startswith("art-6-1")
        applies_from = HIGH_ANNEX_III_FROM if annex_iii else HIGH_ANNEX_I_FROM
        result = [
            _obligation(row, applies_from) for row in HIGH if annex_iii or not row.annex_iii_only
        ]
        if transparency_unit_id:
            result += _transparency(transparency_unit_id)
        return result
    return []
