"""Build ``stabalux_profiles.json``: the Stabalux profile sizes SPADRA offers for a curtain wall, read from the manufacturer's own catalogues.

Run it (it needs ``pdftotext``) on the three catalogues the data came from:

    python build_stabalux_profiles.py Stahlkatalog_Komplett_NL.pdf Gesamtkatalog_Aluminium_NL.pdf Holzkatalog_Komplett_NL.pdf

Only what the catalogues print is taken: for steel (SR) and aluminium (AL) the "Kengetallen van de dwarsdoorsneden" tables of chapter 9.2
(H = depth, B = visible width, t = wall thickness, g = mass per metre), and for timber (H) the system widths and the strength classes the catalogue names.
Every entry records the catalogue, its date and the PDF page it was read from, so each number can be checked against the source. Nothing is estimated.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).with_name("stabalux_profiles.json")
DATE = "16.12.2025"


def pages(pdf: str) -> list[str]:
    return subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout.split("\f")


def num(text: str) -> float:
    return float(text.replace(",", "."))


def steel(pdf: str) -> list[dict]:
    out = []
    for no, page in enumerate(pages(pdf), 1):
        if "Kengetallen van de dwarsdoorsneden" not in page or "SR 6040-2" not in page:
            continue
        for line in page.splitlines():
            m = re.match(r"\s*(SR ?\d+-\d(?:-[A-Z])?)\s+(?:\d\)\s+)?(\d+)\s+(\d+)\s+(\d+)\s+[\d,]+\s+[\d,]+\s+([\d,]+)\s", line)
            if m and not m.group(1).endswith(("-E",)):
                code = re.sub(r"\s+", "", m.group(1))
                out.append({"id": f"stabalux-{code.lower()}", "system": "Stabalux SR", "name": code, "material": "Steel", "depth_mm": int(m.group(2)), "width_mm": int(m.group(3)),
                            "wall_mm": int(m.group(4)), "mass_kg_per_m": num(m.group(5)), "page": no, "table": "9.2 Kengetallen van de dwarsdoorsneden (Schroefbuizen)"})
    return out


def aluminium(pdf: str) -> list[dict]:
    out = []
    for no, page in enumerate(pages(pdf), 1):
        if "Kengetallen van de dwarsdoorsneden" not in page or "AL 5040" not in page:
            continue
        for line in page.splitlines():
            m = re.match(r"\s*(AL ?\d+)\s+(\d+)\s+(\d+)\s+([\d,]+)\s+([\d,]+)\s+[\d,]+\s+[\d,]+\s+([\d,]+)\s", line)
            if m:
                code = re.sub(r"\s+", "", m.group(1))
                out.append({"id": f"stabalux-{code.lower()}", "system": "Stabalux AL", "name": code, "material": "Aluminium", "depth_mm": int(m.group(2)), "width_mm": int(m.group(3)),
                            "wall_min_mm": num(m.group(4)), "wall_max_mm": num(m.group(5)), "mass_kg_per_m": num(m.group(6)), "page": no, "table": "9.2 Kengetallen van de dwarsdoorsneden (Aluminium profielen - 50 mm)"})
    return out


def timber(pdf: str) -> list[dict]:
    page = next((i + 1 for i, t in enumerate(pages(pdf)) if re.search(r"Systeembreedten +50, 60, 80", t)), None)
    if page is None:
        raise SystemExit("The timber catalogue does not list the system widths where expected")
    return [{"id": f"stabalux-h-{w}", "system": "Stabalux H", "name": f"H {w}", "material": "Timber", "depth_mm": None, "width_mm": w, "wall_mm": None, "mass_kg_per_m": None, "page": page,
             "table": "1.1.1 Systeemeigenschappen: Systeembreedten 50, 60, 80 mm", "note": "The catalogue fixes the width of the timber section; its depth follows from the structural calculation, so no depth is listed."} for w in (50, 60, 80)]


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit(__doc__)
    s, a, h = argv
    profiles = steel(s) + aluminium(a) + timber(h)
    data = {"schema": "spadra.stabalux_profiles.v1", "manufacturer": "Stabalux", "date": DATE,
            "sources": {"Steel": Path(s).name, "Aluminium": Path(a).name, "Timber": Path(h).name},
            "note": "Sizes as printed in the Stabalux catalogues. They are not approvals: system tests, structural capacity and fire or burglar ratings belong to the complete Stabalux system and its own documentation.",
            "materials": {"Steel": "Sendzimir-galvanised strip steel, steel grade S280, DIN EN 10021 (Stahlkatalog 1.2.1 Materiaalgegevens, PDF p. 21)",
                          "Aluminium": "EN AW 6060, temper T66, DIN EN 573-3 / DIN EN 755-2 (Gesamtkatalog Aluminium 1.2.1, PDF p. 16)",
                          "Timber": "Softwood strength class C24, or glued laminated GL24h (Holzkatalog 1.2.2 Profielopbouw, PDF p. 25)"},
            "profiles": profiles}
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(profiles)} profiles -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1:])
