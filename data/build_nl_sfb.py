"""Builds ``nl_sfb.json`` (the NL/SfB classification SPADRA ships) from the official spreadsheet.

    python build_nl_sfb.py "NL-SfB_Tabel_0-4_Update-V201912.xlsx"

Source: NL/SfB 2005 tables 0 to 4, electronic edition updated 2019-12-23 (STABU / BIM Loket), as supplied by the project owner. Needs ``openpyxl``,
which only this build step uses; SPADRA itself reads the JSON. Each row is kept as the spreadsheet has it: nothing is renamed, merged or added
(a few codes appear twice with different texts in the source, and both are kept). The one change: texts saved through the DOS code page are
restored to the characters they were meant to be (`repaired_texts` in the file says how many rows).
"""
from __future__ import annotations

import json
import sys

from datetime import date, datetime
from pathlib import Path

import openpyxl # type: ignore

SHEETS = {0: "NL-SfB_Tabel 0", 1: "NL-SfB_Tabel 1", 2: "NL-SfB_Tabel 2", 3: "NL-SfB_Tabel 3", 4: "NL-SfB_Tabel 4"}
TITLES = {0: ("Ruimtelijke voorzieningen", "Physical environment"), 1: ("Functionele gebouwelementen", "Elements"), 2: ("Constructiemethoden", "Constructions"),
          3: ("Constructiemiddelen", "Materials"), 4: ("Activiteiten, kenmerken en eigenschappen", "Activities, requirements")}


def repaired(text: str) -> tuple[str, bool]:
    """A few texts in the spreadsheet were saved through the old DOS code page (``cr├¿ches`` for ``crèches``). Undoing exactly that is lossless; any
    text that does not survive the round trip is left as it is."""
    if "├" not in text and "┬" not in text and "Ã" not in text:
        return text, False
    try:
        fixed = text.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text, False
    return fixed, fixed != text


def build(source: Path) -> dict:
    book = openpyxl.load_workbook(source, data_only=True, read_only=True)
    version = ""
    for row in book["NL-SfB"].iter_rows(values_only=True):
        cells = [c for c in row if c is not None]
        if cells and isinstance(cells[0], datetime):
            version = cells[0].date().isoformat()
        elif cells and isinstance(cells[0], date):
            version = cells[0].isoformat()
    rows: list[list] = []
    fixed_rows = 0
    for table, sheet in SHEETS.items():
        for r in list(book[sheet].iter_rows(values_only=True))[2:]:             # the first row is the header, the second the table's own title
            code, nl, en = r[3], r[5], r[10]
            if code in (None, "") or not str(code).strip():
                continue
            texts = [repaired(str(code).strip()), repaired(str(nl or "").strip()), repaired(str(en or "").strip())]
            fixed_rows += any(changed for _, changed in texts)
            rows.append([table, *(s for s, _ in texts)])
    return {"source": "NL/SfB 2005, tabellen 0-4, elektronische uitgave", "version": version, "repaired_texts": fixed_rows, "tables": {str(t): {"nl": n, "en": e} for t, (n, e) in TITLES.items()}, "rows": rows}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    data = build(Path(sys.argv[1]))
    out = Path(__file__).with_name("nl_sfb.json")
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(data['rows'])} rows, version {data['version']} -> {out}")
