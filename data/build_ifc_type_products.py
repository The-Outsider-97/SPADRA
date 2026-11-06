"""Regenerate IFC4X3_ADD2 concrete type-product catalog from an EXPRESS file.

Usage: python data/build_ifc_type_products.py IFC4X3_ADD2.exp
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

def build(schema: str) -> dict:
    if not re.search(r"\bSCHEMA\s+IFC4X3_ADD2\s*;", schema, re.I):
        raise ValueError("Input must be IFC4X3_ADD2")
    entities = dict(re.findall(r"\bENTITY\s+(\w+)\s*([\s\S]*?)END_ENTITY\s*;", schema, re.I))
    parent = {}
    for name, body in entities.items():
        match = re.search(r"SUBTYPE\s+OF\s*\(\s*(\w+)", body, re.I)
        if match:
            parent[name] = match.group(1)
    enums = {n: [x.strip() for x in vals.split(",")] for n, vals in re.findall(
        r"\bTYPE\s+(\w+)\s*=\s*ENUMERATION\s+OF\s*\(([^)]*)\)\s*;\s*END_TYPE\s*;", schema, re.I)}

    def is_type_product(name: str) -> bool:
        seen = set()
        while name and name not in seen:
            if name == "IfcTypeProduct":
                return True
            seen.add(name)
            name = parent.get(name, "")
        return False

    rows = {}
    for name in sorted(entities):
        body = entities[name]
        if name == "IfcTypeProduct" or not is_type_product(name):
            continue
        if re.search(r"\bABSTRACT\b", re.split(r"SUBTYPE\s+OF", body, maxsplit=1, flags=re.I)[0], re.I):
            continue
        match = re.search(r"^\s*PredefinedType\s*:\s*(\w+)", body, re.I | re.M)
        row = {"predefined_types": enums.get(match.group(1), []) if match else []}
        if name.endswith("Type") and name[:-4] in entities:
            row["occurrence"] = name[:-4]
        rows[name] = row
    if "IfcWallType" not in rows or "IfcRoofType" not in rows or len(rows) < 100:
        raise ValueError("EXPRESS parse did not produce the expected IFC type hierarchy")
    return {"schema": "IFC4X3_ADD2", "version": "4.3.2.0",
            "source": "buildingSMART IFC 4.3 ADD2 EXPRESS schema",
            "source_url": "https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/",
            "derived_from": "https://github.com/Autodesk/revit-ifc/blob/master/Source/RevitIFCTools/IFC4X3_ADD2.exp",
            "notes": "All concrete descendants of IfcTypeProduct in IFC4X3_ADD2; predefined_types are EXPRESS enum literals, not invented mappings.",
            "types": rows}

if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    source = Path(sys.argv[1]).read_text(encoding="utf-8-sig")
    target = Path(sys.argv[2]) if len(sys.argv) == 3 else Path(__file__).with_name("ifc_type_products.json")
    target.write_text(json.dumps(build(source), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(str(target))
