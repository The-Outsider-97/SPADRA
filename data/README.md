# SPADRA classification data

SPADRA supports three distinct classifications:

- NL-SfB: existing official 0–4 tables in nl_sfb.json (unchanged).
- OmniClass: Table 21 Elements and Table 23 Products (local licensed data only).
- IFC 4.3 ADD2: 128 concrete IfcTypeProduct classes and EXPRESS predefined-type enumerations in ifc_type_products.json.

## IFC type products

The supplied IFC4X3_ADD2 product type catalog is generated from the published buildingSMART EXPRESS hierarchy, not guessed. Official specification: https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/

To regenerate from an authorized IFC4X3_ADD2.exp file:

    python data/build_ifc_type_products.py /path/to/IFC4X3_ADD2.exp

IFC type definitions (IfcWallType) differ from occurrence classes (IfcWall). Assigning a type in SPADRA metadata alone does not implement IFC import/export.

## OmniClass, Assembly Codes, and licensing

In Revit, an Assembly Code is a **UniFormat** classification, not OmniClass or NL-SfB. OmniClass Table 21 classifies elements, while Table 23 classifies products. A one-to-one UniFormat-to-OmniClass mapping is not guaranteed.

CSI's single-user license restricts embedding and redistributing OmniClass tables in commercial software and sharing classification crosswalks publicly. The repository deliberately ships **no official OmniClass codes, titles or mappings**. Obtain the appropriate software/commercial license or API agreement first: https://www.csiresources.org/csistore/oc-eula

With the appropriate rights, copy omniclass.example.json to data/omniclass.local.json (ignored by Git), or set environment variable SPADRA_OMNICLASS_CATALOG to a private JSON file path.

Local catalog format (placeholders are not actual classification codes):

    {
      "schema": "spadra.omniclass.v1",
      "edition": "YOUR LICENSED EDITION",
      "tables": {
        "21": [{"code": "<licensed-code>", "title": "<licensed-title>"}],
        "23": [{"code": "<licensed-code>", "title": "<licensed-title>"}]
      },
      "assembly_mappings": [{
        "assembly_code": "<your UniFormat code>",
        "omniclass_code": "<Table 21 code>",
        "table": 21,
        "verified": true,
        "source": "<authorized crosswalk evidence>"
      }]
    }

Mapping rules: A selected Assembly Code autofills OmniClass **Table 21** only when exactly one distinct, explicitly verified, local mapping exists. Missing/ambiguous mappings produce no automatic assignment. Table 23 is independent. The value and evidence are saved in the same undoable edit. A manually selected Table 21 code is preserved when an Assembly Code changes; an automatically selected value is updated/cleared when its Assembly Code changes. No real codes or crosswalks are invented.

## API

- GET /api/classifications: IFC version, count and OmniClass catalog availability.
- GET /api/classifications/search?kind=ifc&q=Wall
- GET /api/classifications/search?kind=assembly&q=A
- GET /api/classifications/search?kind=omniclass&table=21&q=wall

Properties live in SGEO spadra: metadata and travel with supported SPADRA native save, undo/redo, and native SGEO exchange. IFC metadata fields are not a claim of complete IFC exchange support.
