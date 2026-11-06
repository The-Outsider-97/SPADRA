"""Build data/ifc_psets.json: the buildingSMART property sets (Pset_*) and quantity sets (Qto_*) SPADRA offers on its elements.

Two sources, never mixed in one set:

* the default tables below, transcribed by hand from the published IFC documentation pages named in each set's ``source``
  (not parsed from a machine-readable schema, so a transcription slip is possible);
* ``--psd DIR``: the buildingSMART property-set definition files (``Pset_*.xml`` / ``Qto_*.xml``, the "PSD" format shipped with IFC).
  This is the authoritative route: it also gives each set's applicable classes. Use it to regenerate or to verify the table below.

    python data/build_ifc_psets.py                       # the transcribed tables
    python data/build_ifc_psets.py --psd C:/ifc/psd --edition "IFC4X3_ADD2"

Nothing here invents a property: a set or property that is not in a source is not in the output.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path(__file__).with_name("ifc_psets.json")
SCHEMA = "spadra.ifc_psets.v1"
DOCS43 = "https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/"
REL43 = "https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/"
DEV44 = "https://standards.buildingsmart.org/IFC/DEV/IFC4_4/HTML/lexical/"
DEV43 = "https://standards.buildingsmart.org/IFC/DEV/IFC4_3/HTML/lexical/"
IFC4 = "https://standards.buildingsmart.org/IFC/RELEASE/IFC4/FINAL/HTML/schema/ifcsharedbldgelements/qset/"
ED43, ED44, ED4 = "IFC 4.3.2 (IFC4X3_ADD2)", "IFC 4.4 development page (the IFC 4.3 release page could not be fetched)", "IFC 4 (the IFC 4.3 page could not be fetched)"
STATUS = ["New", "Existing", "Demolish", "Temporary"]

# data type -> (kind, unit, minimum rule). Units are the ones SPADRA shows and stores; IFC stores SI.
TYPES = {
    "IfcIdentifier": ("text", "", None), "IfcLabel": ("text", "", None), "IfcBoolean": ("bool", "", None),
    "PEnum_ElementStatus": ("enum", "", None),
    "IfcThermalTransmittanceMeasure": ("real", "W/(m²·K)", "nonneg"),
    "IfcPlaneAngleMeasure": ("real", "°", None),
    "IfcPositiveLengthMeasure": ("real", "m", "positive"),
    "IfcAreaMeasure": ("real", "m²", "nonneg"),
    "IfcVolumetricFlowRateMeasure": ("real", "m³/s", "nonneg"),
    "IfcPositiveRatioMeasure": ("real", "", "positive"),
}
Q_TYPES = {"length": ("m", "IfcQuantityLength"), "area": ("m²", "IfcQuantityArea"), "volume": ("m³", "IfcQuantityVolume"), "weight": ("kg", "IfcQuantityWeight")}

COMMON = {  # shared property definitions, as the pages list them
    "Reference": "IfcIdentifier", "Status": "PEnum_ElementStatus", "AcousticRating": "IfcLabel", "FireRating": "IfcLabel", "IsExternal": "IfcBoolean",
    "ThermalTransmittance": "IfcThermalTransmittanceMeasure", "LoadBearing": "IfcBoolean", "Combustible": "IfcBoolean", "SurfaceSpreadOfFlame": "IfcLabel",
    "Compartmentation": "IfcBoolean", "ExtendToStructure": "IfcBoolean", "SecurityRating": "IfcLabel", "Infiltration": "IfcVolumetricFlowRateMeasure",
    "GlazingAreaFraction": "IfcPositiveRatioMeasure", "HasSillExternal": "IfcBoolean", "HasSillInternal": "IfcBoolean", "HasDrive": "IfcBoolean",
    "SmokeStop": "IfcBoolean", "FireExit": "IfcBoolean", "WaterTightnessRating": "IfcLabel", "MechanicalLoadRating": "IfcLabel", "WindLoadRating": "IfcLabel",
    "DurabilityRating": "IfcLabel", "HygrothermalRating": "IfcLabel", "HandicapAccessible": "IfcBoolean", "SelfClosing": "IfcBoolean", "Span": "IfcPositiveLengthMeasure",
    "Slope": "IfcPlaneAngleMeasure", "Roll": "IfcPlaneAngleMeasure", "PitchAngle": "IfcPlaneAngleMeasure", "GrossPlannedArea": "IfcAreaMeasure",
    "NetPlannedArea": "IfcAreaMeasure", "PubliclyAccessible": "IfcBoolean",
}
PSETS = [  # (name, entity, source, edition, [property names])
    ("Pset_WallCommon", "IfcWall", DOCS43 + "Pset_WallCommon.htm", ED43,
     "Reference Status AcousticRating FireRating Combustible SurfaceSpreadOfFlame ThermalTransmittance IsExternal LoadBearing ExtendToStructure Compartmentation"),
    ("Pset_RoofCommon", "IfcRoof", REL43 + "Pset_RoofCommon.htm", ED43, "Reference Status AcousticRating IsExternal ThermalTransmittance FireRating LoadBearing"),
    ("Pset_WindowCommon", "IfcWindow", REL43 + "Pset_WindowCommon.htm", ED43,
     "Reference Status AcousticRating FireRating SecurityRating IsExternal Infiltration ThermalTransmittance GlazingAreaFraction HasSillExternal HasSillInternal "
     "HasDrive SmokeStop FireExit WaterTightnessRating MechanicalLoadRating WindLoadRating"),
    ("Pset_DoorCommon", "IfcDoor", REL43 + "Pset_DoorCommon.htm", ED43,
     "Reference Status FireRating AcousticRating SecurityRating DurabilityRating HygrothermalRating WaterTightnessRating MechanicalLoadRating WindLoadRating "
     "Infiltration IsExternal ThermalTransmittance GlazingAreaFraction HandicapAccessible FireExit HasDrive SelfClosing SmokeStop"),
    ("Pset_BeamCommon", "IfcBeam", REL43 + "Pset_BeamCommon.htm", ED43, "Reference Status Span Slope Roll IsExternal ThermalTransmittance LoadBearing FireRating"),
    ("Pset_ColumnCommon", "IfcColumn", DEV44 + "Pset_ColumnCommon.html", ED44, "Reference Status Slope Roll IsExternal ThermalTransmittance LoadBearing FireRating"),
    ("Pset_SlabCommon", "IfcSlab", DEV44 + "Pset_SlabCommon.html", ED44,
     "Reference Status AcousticRating FireRating PitchAngle Combustible SurfaceSpreadOfFlame Compartmentation IsExternal ThermalTransmittance LoadBearing"),
    ("Pset_SpaceCommon", "IfcSpace", REL43 + "Pset_SpaceCommon.htm", ED43, "Reference IsExternal GrossPlannedArea NetPlannedArea PubliclyAccessible HandicapAccessible"),
]
QTOS = [  # (name, entity, source, edition, [(quantity, kind, definition)])
    ("Qto_WallBaseQuantities", "IfcWall", REL43 + "Qto_WallBaseQuantities.htm", ED43, [
        ("Length", "length", "Length along the centre line of the wall"), ("Width", "length", "Constant thickness, perpendicular to the wall path (prismatic walls only)"),
        ("Height", "length", "Total nominal height, only where it is constant along the wall"), ("GrossFootPrintArea", "area", "Footprint area before openings"),
        ("NetFootPrintArea", "area", "Footprint area after openings"), ("GrossSideArea", "area", "Elevation area of the middle plane, ignoring openings"),
        ("NetSideArea", "area", "Elevation area of the middle plane, openings subtracted"), ("GrossVolume", "volume", "Volume excluding openings and recesses"),
        ("NetVolume", "volume", "Volume after subtracting openings and recesses"), ("GrossWeight", "weight", "Weight of the gross volume"), ("NetWeight", "weight", "Weight of the net volume")]),
    ("Qto_SlabBaseQuantities", "IfcSlab", REL43 + "Qto_SlabBaseQuantities.htm", ED43, [
        ("Width", "length", "Thickness of a slab of constant thickness"), ("Length", "length", "Length, rectangular slabs only"), ("Depth", "length", "Footprint depth, rectangular slabs only"),
        ("Perimeter", "length", "Distance around the outer boundary (prismatic slabs)"), ("GrossArea", "area", "Area excluding openings and cut-outs"),
        ("NetArea", "area", "Area after subtracting openings and cut-outs"), ("GrossVolume", "volume", "Volume excluding openings and recesses"),
        ("NetVolume", "volume", "Volume after subtracting openings and recesses"), ("GrossWeight", "weight", "Weight of the gross volume"), ("NetWeight", "weight", "Weight of the net volume")]),
    ("Qto_RoofBaseQuantities", "IfcRoof", REL43 + "Qto_RoofBaseQuantities.htm", ED43, [
        ("GrossArea", "area", "Area of the roof's outer surface, ignoring openings"), ("NetArea", "area", "Area of the outer surface with openings subtracted"),
        ("ProjectedArea", "area", "Outer surface projected onto the ground")]),
    ("Qto_DoorBaseQuantities", "IfcDoor", DEV43 + "Qto_DoorBaseQuantities.html", ED43, [
        ("Width", "length", "Overall width (outer lining of a rectangular door)"), ("Height", "length", "Overall height (outer lining of a rectangular door)"),
        ("Perimeter", "length", "Perimeter of the outline"), ("Area", "area", "Area of the outer lining")]),
    ("Qto_WindowBaseQuantities", "IfcWindow", REL43 + "Qto_WindowBaseQuantities.htm", ED43, [
        ("Width", "length", "Overall width (outer lining of a rectangular window)"), ("Height", "length", "Overall height (outer lining of a rectangular window)"),
        ("Perimeter", "length", "Length of the outline"), ("Area", "area", "Area of the outer lining")]),
    ("Qto_BeamBaseQuantities", "IfcBeam", DOCS43 + "Qto_BeamBaseQuantities.htm", ED43, [
        ("Length", "length", "Overall length, ignoring cut-outs"), ("CrossSectionArea", "area", "Area of the cross section"),
        ("OuterSurfaceArea", "area", "Side surface excluding the end caps"), ("GrossSurfaceArea", "area", "Total surface including the end caps"),
        ("NetSurfaceArea", "area", "Surface after cut-outs"), ("GrossVolume", "volume", "Volume ignoring cut-outs"), ("NetVolume", "volume", "Volume after cut-outs"),
        ("GrossWeight", "weight", "Weight of the gross volume"), ("NetWeight", "weight", "Weight of the net volume")]),
    ("Qto_ColumnBaseQuantities", "IfcColumn", IFC4 + "qto_columnbasequantities.htm", ED4, [
        ("Length", "length", "Physical length, ignoring cut-outs"), ("CrossSectionArea", "area", "Area of the cross section"),
        ("OuterSurfaceArea", "area", "Side surface excluding the end caps"), ("GrossSurfaceArea", "area", "Total surface including both ends"),
        ("NetSurfaceArea", "area", "Surface after cut-outs"), ("GrossVolume", "volume", "Volume ignoring cut-outs"), ("NetVolume", "volume", "Volume after cut-outs"),
        ("GrossWeight", "weight", "Weight of the gross volume"), ("NetWeight", "weight", "Weight of the net volume")]),
    ("Qto_SpaceBaseQuantities", "IfcSpace", DEV43 + "Qto_SpaceBaseQuantities.html", ED43, [
        ("Height", "length", "Space height from floor slab to ceiling slab, constant-height spaces only"), ("FinishCeilingHeight", "length", "Height of a suspended ceiling"),
        ("FinishFloorHeight", "length", "Thickness of the floor finish"), ("GrossPerimeter", "length", "Perimeter at floor level, openings included"),
        ("NetPerimeter", "length", "Perimeter at floor level without openings (skirting)"), ("GrossFloorArea", "area", "Floor area including internal elements"),
        ("NetFloorArea", "area", "Usable floor area"), ("GrossWallArea", "area", "Bounding wall and column area, openings included"),
        ("NetWallArea", "area", "Bounding wall and column area, openings excluded"), ("GrossCeilingArea", "area", "Ceiling area including internal elements"),
        ("NetCeilingArea", "area", "Ceiling area without internal elements"), ("GrossVolume", "volume", "Volume ignoring recesses and enclosed objects"),
        ("NetVolume", "volume", "Volume enclosed by the space")]),
]
NOTE = ("Applicable entities are stated by the set's name (Pset_WallCommon -> IfcWall); the documentation pages these sets were transcribed from list none. "
        "Plane angles are stored in degrees by SPADRA. Regenerate from the buildingSMART PSD files with --psd to take applicability from the schema itself.")


def transcribed() -> dict:
    sets: dict = {}
    for name, entity, source, edition, props in PSETS:
        defs = {}
        for prop in props.split():
            dt = COMMON[prop]
            kind, unit, rule = TYPES[dt]
            defs[prop] = {"ifc_type": dt, "kind": kind, **({"unit": unit} if unit else {}), **({"rule": rule} if rule else {}), **({"enum": STATUS} if kind == "enum" else {})}
        sets[name] = {"kind": "pset", "entities": [entity], "applicability": "by name", "edition": edition, "source": source, "properties": defs}
    for name, entity, source, edition, qs in QTOS:
        sets[name] = {"kind": "qto", "entities": [entity], "applicability": "by name", "edition": edition, "source": source,
                      "quantities": {q: {"kind": k, "unit": Q_TYPES[k][0], "ifc_type": Q_TYPES[k][1], "description": d} for q, k, d in qs}}
    return sets


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def from_psd(folder: Path, edition: str) -> dict:
    """Read buildingSMART PSD files. Only plain single-value properties and enumerations are understood; anything else is skipped and reported."""
    sets: dict = {}
    skipped: list[str] = []
    for path in sorted(folder.glob("*.xml")):
        root = ET.parse(path).getroot()                                    # PSD files are trusted local files from buildingSMART; DTDs are not used
        name = next((e.text for e in root if _local(e.tag) == "Name"), None)
        if not name or not (name.startswith("Pset_") or name.startswith("Qto_")):
            continue
        classes = sorted({(e.text or "").split("/")[0] for e in root.iter() if _local(e.tag) == "ClassName" and e.text})
        if name.startswith("Qto_"):
            qs = {}
            for q in (e for e in root.iter() if _local(e.tag) == "QtoDef"):
                qn = next((c.text for c in q if _local(c.tag) == "Name"), None)
                qt = next((c.text for c in q if _local(c.tag) == "QtoType"), "")
                kind = {"Q_LENGTH": "length", "Q_AREA": "area", "Q_VOLUME": "volume", "Q_WEIGHT": "weight"}.get(qt or "")
                if qn and kind:
                    qs[qn] = {"kind": kind, "unit": Q_TYPES[kind][0], "ifc_type": Q_TYPES[kind][1], "description": ""}
                else:
                    skipped.append(f"{name}.{qn}")
            sets[name] = {"kind": "qto", "entities": classes, "applicability": "schema", "edition": edition, "source": path.name, "quantities": qs}
            continue
        defs = {}
        for p in (e for e in root.iter() if _local(e.tag) == "PropertyDef"):
            pn = next((c.text for c in p if _local(c.tag) == "Name"), None)
            dt = next((e.get("type") for e in p.iter() if _local(e.tag) == "DataType" and e.get("type")), None)
            enum = next((e for e in p.iter() if _local(e.tag) == "TypePropertyEnumeratedValue"), None)
            if pn and enum is not None:
                vals = [(c.text or "").strip() for c in enum.iter() if _local(c.tag) == "EnumItem"]
                defs[pn] = {"ifc_type": "PEnum", "kind": "enum", "enum": vals}
            elif pn and dt in TYPES:
                kind, unit, rule = TYPES[dt]
                defs[pn] = {"ifc_type": dt, "kind": kind, **({"unit": unit} if unit else {}), **({"rule": rule} if rule else {})}
            elif pn and dt:
                defs[pn] = {"ifc_type": dt, "kind": "text" if dt in ("IfcText", "IfcLabel", "IfcIdentifier") else "real" if dt.endswith("Measure") else "text"}
            else:
                skipped.append(f"{name}.{pn}")
        sets[name] = {"kind": "pset", "entities": classes, "applicability": "schema", "edition": edition, "source": path.name, "properties": defs}
    if skipped:
        print("Skipped (unsupported definition):", ", ".join(skipped), file=sys.stderr)
    return sets


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--psd", type=Path, help="folder with buildingSMART Pset_*.xml / Qto_*.xml files")
    ap.add_argument("--edition", default="", help="edition label recorded for --psd sets, e.g. IFC4X3_ADD2")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    if a.psd and not a.edition:
        ap.error("--edition is required with --psd")
    sets = from_psd(a.psd, a.edition) if a.psd else transcribed()
    a.out.write_text(json.dumps({"schema": SCHEMA, "note": NOTE, "origin": "psd" if a.psd else "transcribed", "sets": sets}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{a.out.name}: {sum(s['kind'] == 'pset' for s in sets.values())} property sets, {sum(s['kind'] == 'qto' for s in sets.values())} quantity sets")


if __name__ == "__main__":
    main()
