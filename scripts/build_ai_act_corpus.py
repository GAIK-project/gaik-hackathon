#!/usr/bin/env python3
"""Build the AI Act text corpus from the Official Journal XHTML.

Regulation (EU) 2024/1689 (Artificial Intelligence Act), as published in
OJ L, 2024/1689, 12.7.2024. This is the original text: it does not include the
changes made by Regulation (EU) 2026/1744 (in force 27.7.2026), which the
consolidated version CELEX 02024R1689-20260727 carries in a different markup
that this script does not read. Run from the project root:

    uv run --no-project --with beautifulsoup4 --with lxml python -I scripts/build_ai_act_corpus.py

Input   data/ai-act/raw/32024R1689.<lang>.xhtml   (lang: fi, en)
Output  data/ai-act/corpus.<lang>.json            a JSON list of units

A unit is one recital, one article or one annex. Annex III also gets one unit
per point and per sub-point (annex-III-4, annex-III-4-a), because those are
what a high-risk classification cites.

    {
      "id": "art-6",              recital-12, art-6, annex-III, annex-III-4, annex-III-4-a
      "kind": "article",          recital | article | annex
      "number": "6",              "12" | "6" | "III" | "III.4" | "III.4(a)"
      "label": "Article 6",       citation in the corpus language
      "title": "...",             null for recitals
      "chapter": "III",           roman numeral; null outside the articles
      "chapter_title": "...",
      "section": "1",             null where the chapter has no sections
      "section_title": "...",
      "parent": null,             the enclosing unit of an Annex III point or sub-point
      "anchor": "art_6",          element id in the Official Journal HTML
      "lang": "en",
      "text": "...",              body text, without the heading and the title
      "paragraphs": [             numbered paragraphs; one entry with n = null
        {"n": "1", "text": "...",   where the article has no numbering
         "points": [{"n": "a", "text": "..."}]}
      ],
      "footnotes": [{"n": "7", "text": "..."}]
    }

The raw files are untrusted input: they are parsed as HTML, never executed.
--fetch downloads a missing raw file from the Publications Office (Cellar);
EUR-Lex itself answers scripted requests with a bot challenge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
import urllib.request
import warnings
from collections import defaultdict
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag, XMLParsedAsHTMLWarning

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "ai-act" / "raw"
OUT_DIR = ROOT / "data" / "ai-act"

CELEX = "32024R1689"
SOURCE_URL = f"http://publications.europa.eu/resource/celex/{CELEX}"
# corpus language -> the language tag the Publications Office negotiates on
LANGS = {"fi": "fin", "en": "eng"}

EXPECTED = {"recital": 180, "article": 113, "annex": 13}
ROMAN = "I II III IV V VI VII VIII IX X XI XII XIII".split()

LABELS = {
    "en": {
        "recital": "Recital {n}",
        "article": "Article {n}",
        "annex": "Annex {n}",
        "point": "Annex {a}, point {p}",
        "subpoint": "Annex {a}, point {p}({s})",
    },
    "fi": {
        "recital": "Johdanto-osan {n} kappale",
        "article": "{n} artikla",
        "annex": "Liite {n}",
        "point": "Liite {a}, {p} kohta",
        "subpoint": "Liite {a}, {p} kohdan {s} alakohta",
    },
}

# (unit id, paragraph number or None, field to look in, text that must be there)
SPOT_CHECKS = {
    "en": [
        ("art-5", None, "title", "Prohibited AI practices"),
        ("annex-III-4-a", None, "text", "recruitment"),
        ("art-50", "1", "text", "interacting with an AI system"),
    ],
    "fi": [
        ("art-5", None, "title", "Kielletyt tekoälyyn liittyvät käytännöt"),
        ("annex-III-4-a", None, "text", "rekrytoin"),
        ("art-50", "1", "text", "vuorovaikutuksessa tekoälyjärjestelmän kanssa"),
    ],
}

UNIT_ID = re.compile(r"^(rct_\d+|art_\d+|anx_[IVXLC]+)$")
NOTE_REF = re.compile(r"^ntc(\d+)-")
NOTE_BODY = re.compile(r"^ntr(\d+)-")
PARAGRAPH_ID = re.compile(r"^\d{3}\.\d{3}$")
CHAPTER_ID = re.compile(r"^cpt_[IVXLC]+$")
SECTION_ID = re.compile(r"^cpt_[IVXLC]+\.sct_\d+$")
LIST_LABEL = re.compile(r"\(?([0-9A-Za-z]+(?:\.[0-9]+)*)[).]{0,2}")
INLINE_TAGS = {"span", "a", "i", "b", "em", "strong", "sup", "sub", "br", "img"}
WHITESPACE = re.compile(r"\s+")


def clean(text: str) -> str:
    """Collapse whitespace; the source pads with no-break spaces."""
    return WHITESPACE.sub(" ", text.replace("\xa0", " ")).strip()


def squash(text: str) -> str:
    """Drop all whitespace, to compare two renderings of the same text."""
    return WHITESPACE.sub("", text.replace("\xa0", ""))


def label_key(label: str | None) -> str | None:
    """'(a)' -> 'a', 'a)' -> 'a', '1.' -> '1', '3.1.' -> '3.1'; a dash gives None."""
    if not label:
        return None
    match = LIST_LABEL.fullmatch(label)
    return match.group(1) if match else None


class Item:
    """A list item: its label and, in document order, text lines and nested items."""

    __slots__ = ("label", "parts")

    def __init__(self, label: str | None, parts: list | None = None) -> None:
        self.label = label
        self.parts: list[str | Item] = parts or []

    def body_lines(self) -> list[str]:
        return lines_of(self.parts)

    def lines(self) -> list[str]:
        lines = self.body_lines()
        if not self.label:
            return lines
        if lines and not isinstance(self.parts[0], Item):
            lines[0] = f"{self.label} {lines[0]}"
        else:
            lines.insert(0, self.label)
        return lines

    def body(self) -> str:
        return "\n".join(self.body_lines())

    def text(self) -> str:
        return "\n".join(self.lines())


def lines_of(parts: list) -> list[str]:
    lines: list[str] = []
    for part in parts:
        if isinstance(part, Item):
            lines.extend(part.lines())
        else:
            lines.append(part)
    return lines


def append_to_last(parts: list, text: str) -> None:
    """Attach trailing punctuation, as in '</div>;', to the line before it."""
    if not parts:
        parts.append(text)
    elif isinstance(parts[-1], Item):
        append_to_last(parts[-1].parts, text)
    else:
        parts[-1] += text


def parse_blocks(container: Tag, skip: tuple = ()) -> list:
    """Turn the children of a container into text lines and list items."""
    parts: list[str | Item] = []
    inline: list[str] = []

    def flush() -> None:
        text = clean("".join(inline))
        inline.clear()
        if not text:
            return
        if parts and re.fullmatch(r"[;,.:]+", text):
            append_to_last(parts, text)
        else:
            parts.append(text)

    for child in container.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            inline.append(str(child))
            continue
        if any(child is skipped for skipped in skip):
            continue
        name = child.name
        if name in INLINE_TAGS:
            inline.append(" " if name == "br" else child.get_text())
            continue
        flush()
        if name == "p":
            text = clean(child.get_text())
            if text:
                parts.append(text)
        elif name == "table":
            parts.extend(parse_table(child))
        elif name == "div":
            if "oj-enumeration-spacing" in (child.get("class") or []):
                cells = child.find_all("p", recursive=False)
                rest = clean(" ".join(cell.get_text() for cell in cells[1:]))
                parts.append(Item(clean(cells[0].get_text()), [rest]))
            else:
                # also the quoted paragraphs of an amended act, <div id="017.003">
                parts.extend(parse_blocks(child))
        elif name in ("hr", "col"):
            continue
        else:
            raise ValueError(f"unexpected <{name}> in {container.get('id')!r}")
    flush()
    return parts


def parse_table(table: Tag) -> list:
    """The source sets every list item as a one-row table: label cell, content cell."""
    items = []
    body = table.find("tbody", recursive=False) or table
    for row in body.find_all("tr", recursive=False):
        # a three-column list has an empty first cell as indentation
        cells = [
            cell
            for cell in row.find_all("td", recursive=False)
            if clean(cell.get_text())
        ]
        if len(cells) != 2:
            raise ValueError(
                f"table row with {len(cells)} filled cells: {clean(row.get_text())[:80]!r}"
            )
        items.append(Item(clean(cells[0].get_text()), parse_blocks(cells[1])))
    return items


def points_of(parts: list) -> list[dict]:
    return [
        {"n": label_key(part.label), "text": part.body()}
        for part in parts
        if isinstance(part, Item) and label_key(part.label)
    ]


def paragraphs_of(parts: list) -> list[dict]:
    """One entry per top-level block: a labelled item keeps its label in n."""
    paragraphs = []
    for part in parts:
        if isinstance(part, Item):
            key = label_key(part.label)
            paragraphs.append(
                {
                    "n": key,
                    "text": part.body() if key else part.text(),
                    "points": points_of(part.parts),
                }
            )
        else:
            paragraphs.append({"n": None, "text": part, "points": []})
    return paragraphs


def collect_footnotes(soup: BeautifulSoup) -> dict[str, list[dict]]:
    """Take the numbered footnote markers out of the text; file each note under its unit.

    The '(*)' notes in Articles 102 to 109 belong to the quoted amendments and stay
    in the text.
    """
    bodies: dict[str, str] = {}
    for anchor in soup.find_all("a", id=NOTE_BODY):
        number = NOTE_BODY.match(anchor["id"]).group(1)
        text = clean(anchor.parent.get_text())
        bodies[anchor["id"]] = re.sub(rf"^\(\s*{number}\s*\)\s*", "", text)
    by_unit: dict[str, list[dict]] = defaultdict(list)
    for anchor in soup.find_all("a", id=NOTE_REF):
        number = NOTE_REF.match(anchor["id"]).group(1)
        unit = anchor.find_parent(id=UNIT_ID)
        if unit is not None:
            target = (anchor.get("href") or "").lstrip("#")
            by_unit[unit["id"]].append({"n": number, "text": bodies.get(target, "")})
        # "the Council (24), Regulation": the space before the marker goes with it
        before, after = anchor.previous_sibling, anchor.next_sibling
        if (
            isinstance(before, NavigableString)
            and isinstance(after, NavigableString)
            and re.match(r"[,.;:)]", str(after))
        ):
            before.replace_with(str(before).rstrip())
        anchor.decompose()
    return by_unit


def make_unit(lang: str, **fields) -> dict:
    unit = {
        "id": None,
        "kind": None,
        "number": None,
        "label": None,
        "title": None,
        "chapter": None,
        "chapter_title": None,
        "section": None,
        "section_title": None,
        "parent": None,
        "anchor": None,
        "lang": lang,
        "text": "",
        "paragraphs": [],
        "footnotes": [],
    }
    unit.update(fields)
    return unit


def check_coverage(element: Tag, rendered: str, unit_id: str) -> None:
    """Fail when the unit does not hold exactly the source text, whitespace aside."""
    source = squash(element.get_text())
    if source != squash(rendered):
        raise ValueError(f"{unit_id}: rendered text differs from the source text")


def build_recital(element: Tag, lang: str, notes: dict) -> dict:
    number = element["id"].split("_")[1]
    (item,) = parse_blocks(element)
    if label_key(item.label) != number:
        raise ValueError(f"recital {number} is labelled {item.label!r}")
    text = item.body()
    check_coverage(element, item.text(), f"recital-{number}")
    return make_unit(
        lang,
        id=f"recital-{number}",
        kind="recital",
        number=number,
        label=LABELS[lang]["recital"].format(n=number),
        anchor=element["id"],
        text=text,
        paragraphs=[{"n": None, "text": text, "points": []}],
        footnotes=notes.get(element["id"], []),
    )


def build_article(element: Tag, soup: BeautifulSoup, lang: str, notes: dict) -> dict:
    number = int(element["id"].split("_")[1])
    heading = element.find("p", class_="oj-ti-art", recursive=False)
    title_box = element.find("div", class_="eli-title", recursive=False)
    raw_title = clean(title_box.get_text())
    # the English title of Article 1 ends in a stray backtick in the Official Journal
    title = raw_title.rstrip("`").strip()

    numbered = [
        child
        for child in element.find_all("div", recursive=False)
        if PARAGRAPH_ID.fullmatch(child.get("id") or "")
        and child["id"].startswith(f"{number:03d}.")
    ]
    paragraphs = []
    if numbered:
        for box in numbered:
            parts = parse_blocks(box)
            match = (
                re.match(r"(\d+)\.\s+(.*)", parts[0], re.S)
                if isinstance(parts[0], str)
                else None
            )
            if not match or int(match.group(1)) != int(box["id"].split(".")[1]):
                raise ValueError(
                    f"art-{number}: paragraph {box['id']} does not start with its number"
                )
            parts[0] = match.group(2)
            paragraphs.append(
                {
                    "n": match.group(1),
                    "text": "\n".join(lines_of(parts)),
                    "points": points_of(parts),
                }
            )
        text = "\n".join(f"{p['n']}. {p['text']}" for p in paragraphs)
    else:
        parts = parse_blocks(element, skip=(heading, title_box))
        text = "\n".join(lines_of(parts))
        paragraphs.append({"n": None, "text": text, "points": points_of(parts)})
    check_coverage(element, f"{heading.get_text()} {raw_title} {text}", f"art-{number}")

    chapter = element.find_parent("div", id=CHAPTER_ID)
    section = element.find_parent("div", id=SECTION_ID)
    return make_unit(
        lang,
        id=f"art-{number}",
        kind="article",
        number=str(number),
        label=LABELS[lang]["article"].format(n=number),
        title=title,
        chapter=chapter["id"].split("_")[1],
        chapter_title=clean(soup.find(id=f"{chapter['id']}.tit_1").get_text()),
        section=section["id"].rsplit("_", 1)[1] if section else None,
        section_title=clean(soup.find(id=f"{section['id']}.tit_1").get_text())
        if section
        else None,
        anchor=element["id"],
        text=text,
        paragraphs=paragraphs,
        footnotes=notes.get(element["id"], []),
    )


def build_annex(element: Tag, lang: str, notes: dict) -> list[dict]:
    roman = element["id"].split("_")[1]
    headings = element.find_all("p", class_="oj-doc-ti", recursive=False)
    if len(headings) != 2:
        raise ValueError(f"annex-{roman}: {len(headings)} heading lines, expected 2")
    title = clean(headings[1].get_text())
    parts = parse_blocks(element, skip=tuple(headings))
    text = "\n".join(lines_of(parts))
    check_coverage(
        element, f"{headings[0].get_text()} {title} {text}", f"annex-{roman}"
    )
    units = [
        make_unit(
            lang,
            id=f"annex-{roman}",
            kind="annex",
            number=roman,
            label=LABELS[lang]["annex"].format(n=roman),
            title=title,
            anchor=element["id"],
            text=text,
            paragraphs=paragraphs_of(parts),
            footnotes=notes.get(element["id"], []),
        )
    ]
    if roman != "III":
        return units

    for item in parts:
        point = label_key(item.label) if isinstance(item, Item) else None
        if not point:
            continue
        # "Employment, workers' management and access to self-employment:" names the area
        area = item.body_lines()[0].split(":")[0].strip()
        units.append(
            make_unit(
                lang,
                id=f"annex-{roman}-{point}",
                kind="annex",
                number=f"{roman}.{point}",
                label=LABELS[lang]["point"].format(a=roman, p=point),
                title=area,
                parent=f"annex-{roman}",
                anchor=element["id"],
                text=item.body(),
                paragraphs=paragraphs_of(item.parts),
            )
        )
        for sub in item.parts:
            letter = label_key(sub.label) if isinstance(sub, Item) else None
            if not letter:
                continue
            units.append(
                make_unit(
                    lang,
                    id=f"annex-{roman}-{point}-{letter}",
                    kind="annex",
                    number=f"{roman}.{point}({letter})",
                    label=LABELS[lang]["subpoint"].format(a=roman, p=point, s=letter),
                    title=area,
                    parent=f"annex-{roman}-{point}",
                    anchor=element["id"],
                    text=sub.body(),
                    paragraphs=paragraphs_of(sub.parts),
                )
            )
    return units


def build_corpus(raw: bytes, lang: str) -> list[dict]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(raw, "lxml")
    notes = collect_footnotes(soup)
    units = [
        build_recital(el, lang, notes)
        for el in soup.find_all("div", id=re.compile(r"^rct_\d+$"))
    ]
    units += [
        build_article(el, soup, lang, notes)
        for el in soup.find_all("div", id=re.compile(r"^art_\d+$"))
    ]
    for element in soup.find_all("div", id=re.compile(r"^anx_[IVXLC]+$")):
        units += build_annex(element, lang, notes)
    return units


def top_level(units: list[dict], kind: str) -> list[dict]:
    return [unit for unit in units if unit["kind"] == kind and unit["parent"] is None]


def verify(units: list[dict], lang: str) -> list[str]:
    """Return the failed checks; print the counts and the spot checks."""
    failures: list[str] = []
    by_id = {unit["id"]: unit for unit in units}
    if len(by_id) != len(units):
        failures.append("duplicate unit ids")

    counts = {kind: len(top_level(units, kind)) for kind in EXPECTED}
    points = [u for u in units if u["parent"] == "annex-III"]
    subpoints = [u for u in units if u["parent"] and u["parent"] != "annex-III"]
    print(
        f"[{lang}] units: {counts['recital']} recitals, {counts['article']} articles, "
        f"{counts['annex']} annexes; Annex III split into {len(points)} points and "
        f"{len(subpoints)} sub-points; {len(units)} units in all"
    )
    for kind, expected in EXPECTED.items():
        if counts[kind] != expected:
            failures.append(f"{counts[kind]} {kind} units, expected {expected}")

    sequences = {
        "recital": [str(n) for n in range(1, EXPECTED["recital"] + 1)],
        "article": [str(n) for n in range(1, EXPECTED["article"] + 1)],
        "annex": ROMAN,
    }
    for kind, expected in sequences.items():
        if [unit["number"] for unit in top_level(units, kind)] != expected:
            failures.append(
                f"{kind} numbers are not {expected[0]}..{expected[-1]} in order"
            )

    for unit in units:
        if not unit["text"]:
            failures.append(f"{unit['id']}: empty text")
        if unit["kind"] != "recital" and not unit["title"]:
            failures.append(f"{unit['id']}: no title")
        if unit["kind"] == "article" and not unit["chapter"]:
            failures.append(f"{unit['id']}: no chapter")
        numbers = [p["n"] for p in unit["paragraphs"]]
        if unit["kind"] == "article" and numbers != [None]:
            if numbers != [str(n) for n in range(1, len(numbers) + 1)]:
                failures.append(f"{unit['id']}: paragraph numbers {numbers}")
        for paragraph in unit["paragraphs"]:
            n = paragraph["n"]
            if paragraph["text"] not in unit["text"]:
                failures.append(f"{unit['id']}: paragraph {n} is not in the unit text")
            for point in paragraph["points"]:
                if point["text"] not in paragraph["text"]:
                    failures.append(
                        f"{unit['id']}: point {point['n']} is not in paragraph {n}"
                    )
        if unit["parent"] and unit["parent"] not in by_id:
            failures.append(f"{unit['id']}: parent {unit['parent']} is missing")

    for unit_id, paragraph_number, field, needle in SPOT_CHECKS[lang]:
        unit = by_id.get(unit_id)
        where = (
            unit_id if paragraph_number is None else f"{unit_id}({paragraph_number})"
        )
        if unit is None:
            failures.append(f"spot check: {unit_id} is missing")
            continue
        if field == "title":
            haystack = unit["title"] or ""
        elif paragraph_number is None:
            haystack = unit["text"]
        else:
            haystack = next(
                (p["text"] for p in unit["paragraphs"] if p["n"] == paragraph_number),
                "",
            )
        found = needle.lower() in haystack.lower()
        excerpt = haystack if len(haystack) <= 230 else haystack[:230] + "..."
        verdict = "ok" if found else "FAILED"
        print(f"[{lang}] spot check {where} {field} contains {needle!r}: {verdict}")
        print(f"[{lang}]   {excerpt}")
        if not found:
            failures.append(f"spot check: {where} {field} lacks {needle!r}")
    return failures


def print_distribution(units: list[dict], lang: str) -> None:
    groups = {
        "recital": top_level(units, "recital"),
        "article": top_level(units, "article"),
        "annex": top_level(units, "annex"),
        "annex III point/sub-point": [u for u in units if u["parent"]],
    }
    columns = ("n", "min", "median", "mean", "p90", "max", "total")
    header = "".join(f"{column:>8}" for column in columns)
    print(f"[{lang}] characters of text per unit:")
    print(f"[{lang}]   {'kind':<26}{header}  longest")
    for name, group in groups.items():
        sizes = sorted(len(unit["text"]) for unit in group)
        longest = max(group, key=lambda unit: len(unit["text"]))
        row = (
            len(sizes),
            sizes[0],
            round(statistics.median(sizes)),
            round(statistics.mean(sizes)),
            sizes[min(len(sizes) - 1, round(0.9 * (len(sizes) - 1)))],
            sizes[-1],
            sum(sizes),
        )
        cells = "".join(f"{value:>8}" for value in row)
        print(f"[{lang}]   {name:<26}{cells}  {longest['id']}")
    articles = groups["article"]
    paragraphs = [p for unit in articles for p in unit["paragraphs"]]
    unnumbered = sum(1 for unit in articles if unit["paragraphs"][0]["n"] is None)
    print(
        f"[{lang}]   {unnumbered} articles have no numbered paragraphs; the other "
        f"{len(articles) - unnumbered} hold {sum(1 for p in paragraphs if p['n'])} "
        f"paragraphs; {sum(len(p['points']) for p in paragraphs)} points in articles; "
        f"{sum(len(unit['footnotes']) for unit in units)} footnotes kept on their units"
    )


def shown(path: Path) -> str:
    """The path as the log prints it: relative to the project when it is inside it."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def fetch(lang: str, path: Path) -> None:
    request = urllib.request.Request(
        SOURCE_URL,
        headers={
            "Accept": "application/xhtml+xml",
            "Accept-Language": LANGS[lang],
            "User-Agent": "ai-act-corpus-builder",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
        print(f"[{lang}] fetched {len(data)} bytes from {response.geturl()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--langs", nargs="+", default=list(LANGS), choices=list(LANGS))
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument(
        "--fetch", action="store_true", help="download a raw file that is missing"
    )
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    failures: list[str] = []
    for lang in args.langs:
        raw_path = args.raw_dir / f"{CELEX}.{lang}.xhtml"
        if not raw_path.exists():
            if not args.fetch:
                print(
                    f"[{lang}] {shown(raw_path)} is missing; run again with --fetch",
                    file=sys.stderr,
                )
                return 2
            fetch(lang, raw_path)
        raw = raw_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        print(f"[{lang}] source {raw_path.name}: {len(raw)} bytes, sha256 {digest}")
        units = build_corpus(raw, lang)
        lang_failures = verify(units, lang)
        print_distribution(units, lang)
        if lang_failures:
            failures += [f"[{lang}] {failure}" for failure in lang_failures]
            continue
        out_path = args.out_dir / f"corpus.{lang}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(units, ensure_ascii=False, indent=2) + "\n"
        out_path.write_text(payload, encoding="utf-8", newline="\n")
        size = len(payload.encode("utf-8"))
        print(f"[{lang}] wrote {shown(out_path)} ({size} bytes)")

    if failures:
        print("FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
