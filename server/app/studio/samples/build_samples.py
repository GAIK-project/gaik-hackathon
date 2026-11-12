"""Build the Studio's synthetic sample files into `files/` next to this script.

    uv run python app/studio/samples/build_samples.py [path/to/fin-example-1.mp3]

Everything is made up: the companies, the numbers and the people do not exist. PDFs are
written with PyMuPDF (already installed with gaik). The audio sample is a copy of the GAIK
toolkit's public construction-diary example; pass its path to refresh the copy.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent / "files"
FOOTER = "Synteettinen esimerkkiaineisto / synthetic sample material. GAIK mini-hackathon."

INVOICES = [
    {
        "file": "lasku-pumpputarvike.pdf",
        "seller": "Esimerkkilan Pumpputarvike Oy",
        "seller_lines": ["Teollisuuskuja 1, 00000 Esimerkkila", "Y-tunnus 0000001-0"],
        "number": "2026-1041",
        "date": "2.11.2026",
        "due": "16.11.2026",
        "terms": "14 pv netto",
        "reference": "10417",
        "rows": [
            ("Uppopumppu UP-300", "1 kpl", "742,00", "742,00"),
            ("Paineletku 25 mm, 20 m", "2 kpl", "89,00", "178,00"),
            ("Toimitus", "1 kpl", "68,05", "68,05"),
        ],
        "net": "988,05",
        "vat": "251,95",
        "total": "1 240,00",
    },
    {
        "file": "lasku-sahkotukku.pdf",
        "seller": "Mallikylan Sahkotukku Oy",
        "seller_lines": ["Virtatie 7, 00000 Mallikyla", "Y-tunnus 0000002-0"],
        "number": "30455",
        "date": "28.10.2026",
        "due": "27.11.2026",
        "terms": "30 pv netto",
        "reference": "304559",
        "rows": [
            ("Tyomaakeskus 63 A", "2 kpl", "860,00", "1 720,00"),
            ("Jatkokaapeli 5 x 6, 25 m", "4 kpl", "170,00", "680,00"),
        ],
        "net": "2 400,00",
        "vat": "612,00",
        "total": "3 012,00",
    },
    {
        "file": "lasku-tyokaluvuokraus.pdf",
        "seller": "Kuvitteellinen Tyokaluvuokraus Oy",
        "seller_lines": ["Vuokrakatu 3, 00000 Kuvitteela", "Y-tunnus 0000003-0"],
        "number": "LTV-7731",
        "date": "5.11.2026",
        "due": "19.11.2026",
        "terms": "14 pv netto",
        "reference": "77318",
        "rows": [
            ("Timanttiporakone, viikkovuokra", "1 vk", "320,00", "320,00"),
            ("Rakennuskuivain, viikkovuokra", "2 vk", "120,00", "240,00"),
        ],
        "net": "560,00",
        "vat": "142,80",
        "total": "702,80",
    },
]
BUYER = ["Esimerkkirakennus Oy", "Ostolaskut", "Rakentajantie 10, 00000 Esimerkkila"]

MANUAL_PAGES = [
    (
        "1 Delivery terms",
        [
            "These terms apply to every order of the VP-200 transfer pump and its spare parts.",
            "",
            "Delivery time. Pumps in stock are delivered within 5 working days of the order",
            "confirmation. Spare parts are delivered within 2 working days.",
            "",
            "Freight. Freight is free for orders of 500 euros or more. For smaller orders a",
            "freight charge of 18 euros is added to the invoice.",
            "",
            "Returns. An unused product in its original package can be returned within 14 days",
            "of delivery. The buyer pays the return freight.",
            "",
            "Payment. The payment term is 14 days net. Interest on late payment is 8 percent.",
        ],
    ),
    (
        "2 Pump maintenance",
        [
            "Regular maintenance keeps the VP-200 running and is a condition of the warranty.",
            "",
            "Oil change. Change the gear oil after every 2 000 operating hours or once a year,",
            "whichever comes first. Use ISO VG 68 oil; the gearbox holds 1.2 litres.",
            "",
            "Seals. Inspect the shaft seals every 6 months. Replace a seal that leaks more than",
            "a few drops per hour.",
            "",
            "Suction filter. Clean the suction filter once a month, and always after pumping",
            "water that carries sand.",
            "",
            "Before any maintenance, switch the pump off and disconnect it from the mains.",
        ],
    ),
    (
        "3 Warranty and troubleshooting",
        [
            "Warranty. The warranty is 24 months from the delivery date. It covers defects in",
            "materials and workmanship. Wear parts such as seals and filters are not covered.",
            "",
            "The pump does not start. Check the mains fuse and the thermal overload switch.",
            "Wait 15 minutes before restarting after an overload.",
            "",
            "The flow has dropped. Clean the suction filter and check the hose for kinks.",
            "",
            "The pump is noisy. Check the oil level. Stop the pump if the noise continues and",
            "contact maintenance.",
        ],
    ),
]

EMAILS = {
    "viesti-1-toimitus.txt": (
        "Aihe: Tilaus 55021 ei ole saapunut\n\n"
        "Hei,\n\ntilasin uppopumpun viikko sitten ja vahvistuksessa luvattiin toimitus viidessa "
        "arkipaivassa. Pakettia ei ole vielakaan nakynyt eika seurantakoodi toimi. "
        "Milloin toimitus saapuu?\n\nTerveisin\nAsiakas A\n"
    ),
    "viesti-2-lasku.txt": (
        "Aihe: Laskussa virhe\n\n"
        "Hei,\n\nsaimme laskun 2026-1041, jossa toimitus on veloitettu kahteen kertaan. "
        "Pyydan hyvityslaskua ja korjattua laskua, jotta voimme maksaa sen erapaivaan mennessa."
        "\n\nYstavallisin terveisin\nAsiakas B\n"
    ),
    "viesti-3-vika.txt": (
        "Aihe: Pumppu pitaa kovaa aanta\n\n"
        "Hei,\n\nVP-200-pumppumme on alkanut pitaa kovaa aanta ja virtaus on pudonnut selvasti. "
        "Pumppu on kaksi kuukautta vanha. Voitteko lahettaa huoltomiehen tai neuvoa, "
        "mita tarkistamme ensin?\n\nTerveisin\nAsiakas C\n"
    ),
    "viesti-4-tarjous.txt": (
        "Aihe: Tarjouspyynto kolmesta pumpusta\n\n"
        "Hei,\n\nolemme aloittamassa uutta tyomaata ja tarvitsemme kolme VP-200-siirtopumppua "
        "letkuineen. Voitteko lahettaa tarjouksen ja arvion toimitusajasta?\n\n"
        "Terveisin\nAsiakas D\n"
    ),
}


def _write(page: pymupdf.Page, x: float, y: float, text: str, size: float = 10.5) -> None:
    page.insert_text((x, y), text, fontsize=size, fontname="helv")


def _right(page: pymupdf.Page, x: float, y: float, text: str, size: float = 10.5) -> None:
    width = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
    _write(page, x - width, y, text, size)


def build_invoice(data: dict) -> None:
    document = pymupdf.open()
    page = document.new_page()  # A4
    _write(page, 56, 70, data["seller"], 15)
    for index, line in enumerate(data["seller_lines"]):
        _write(page, 56, 90 + 14 * index, line)
    _write(page, 400, 70, "LASKU", 15)
    details = [
        ("Laskun numero", data["number"]),
        ("Laskun päiväys", data["date"]),
        ("Eräpäivä", data["due"]),
        ("Maksuehto", data["terms"]),
        ("Viitenumero", data["reference"]),
    ]
    for index, (label, value) in enumerate(details):
        _write(page, 400, 92 + 15 * index, f"{label}: {value}")
    _write(page, 56, 150, "Ostaja", 9)
    for index, line in enumerate(BUYER):
        _write(page, 56, 166 + 14 * index, line)

    top = 250
    _write(page, 56, top, "Tuote")
    _write(page, 300, top, "Määrä")
    _right(page, 450, top, "à-hinta EUR")
    _right(page, 540, top, "Yhteensä EUR")
    page.draw_line((56, top + 6), (540, top + 6))
    for index, (name, amount, unit, line_total) in enumerate(data["rows"]):
        y = top + 24 + 18 * index
        _write(page, 56, y, name)
        _write(page, 300, y, amount)
        _right(page, 450, y, unit)
        _right(page, 540, y, line_total)
    bottom = top + 24 + 18 * len(data["rows"]) + 8
    page.draw_line((56, bottom), (540, bottom))
    totals = [
        ("Veroton summa", data["net"]),
        ("Arvonlisävero 25,5 %", data["vat"]),
        ("Maksettava yhteensä", data["total"]),
    ]
    for index, (label, value) in enumerate(totals):
        y = bottom + 22 + 17 * index
        _write(page, 330, y, label)
        _right(page, 540, y, f"{value} EUR")
    _write(page, 56, 780, FOOTER, 8)
    document.save(OUT / data["file"], garbage=4, deflate=True)
    document.close()


def build_manual() -> None:
    document = pymupdf.open()
    for number, (heading, lines) in enumerate(MANUAL_PAGES, start=1):
        page = document.new_page()
        _write(page, 56, 60, "VP-200 transfer pump: delivery terms and maintenance manual", 9)
        _write(page, 56, 100, heading, 16)
        for index, line in enumerate(lines):
            _write(page, 56, 134 + 16 * index, line)
        _write(page, 56, 780, FOOTER, 8)
        _right(page, 540, 780, f"Page {number}", 8)
    document.save(OUT / "vp-200-manual.pdf", garbage=4, deflate=True)
    document.close()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for invoice in INVOICES:
        build_invoice(invoice)
    build_manual()
    for name, text in EMAILS.items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
    if len(sys.argv) > 1:
        shutil.copyfile(sys.argv[1], OUT / "tyomaapaivakirja-sanelu.mp3")
    for path in sorted(OUT.iterdir()):
        print(f"{path.name}  {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
