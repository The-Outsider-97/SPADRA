# SPADRA classification data

SPADRA supports three distinct classifications:

- NL-SfB: existing official 0–4 tables in nl_sfb.json (unchanged).
- OmniClass: Table 21 Elements and Table 23 Products (licensed CSI Crosswalk API or private authorized data).
- IFC 4.3 ADD2: 128 concrete IfcTypeProduct classes and EXPRESS predefined-type enumerations in ifc_type_products.json.

## IFC type products

The supplied IFC4X3_ADD2 product type catalog is generated from the published buildingSMART EXPRESS hierarchy, not guessed. Official specification: https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/

To regenerate from an authorized IFC4X3_ADD2.exp file:

    python data/build_ifc_type_products.py /path/to/IFC4X3_ADD2.exp

IFC type definitions (IfcWallType) differ from occurrence classes (IfcWall). Assigning a type in SPADRA metadata alone does not implement IFC import/export.

## OmniClass, Assembly Codes, and licensing

In Revit, an Assembly Code is a **UniFormat** classification, not OmniClass or NL-SfB. OmniClass Table 21 classifies elements, while Table 23 classifies products. A one-to-one UniFormat-to-OmniClass mapping is not guaranteed.

CSI's single-user license restricts embedding and redistributing OmniClass tables in commercial software and sharing classification crosswalks publicly. The repository deliberately ships **no official OmniClass codes, titles or mappings**. Obtain the appropriate software/commercial license or API agreement first: https://www.csiresources.org/csistore/oc-eula

SPADRA now supports **two complete-data sources**, using the same existing classifications.py module. Neither source inserts proprietary classifications into this public repository.

### Option A — Official CSI Crosswalk API (recommended)

CSI publishes the Crosswalk v3 GraphQL API and supplies API keys to licensed users. This API provides searchable, official OmniClass tables (including T21 and T23), UniFormat and authoritative UniFormat→OmniClass crosswalks. Request appropriate access and software rights from CSI, then set the server-side environment variable:

    SPADRA_CSI_CROSSWALK_KEY=<your licensed key>

Keep the key in the server environment or a secrets manager, **never in the client, source code, project file, frontend storage, or GitHub**. The backend connects only to https://crosswalk.azure-api.net/v3/query with a fixed HTTPS origin and short timeout. It never returns the key.

With a configured key, the Properties panel can search the full licensed hierarchy; selecting a UniFormat Assembly Code triggers a source-backed crosswalk query. **Only one distinct OmniClass Table 21 candidate is filled automatically.** Table 23 always requires independent selection. Errors, unavailable credentials, and ambiguous candidates are reported or remain unassigned, never guessed.

API evidence: https://github.com/csi-net/crosswalk (public SDK and Postman GraphQL collection). CSI licensing: https://crosswalk.squarespace.com/licensing

### Option B — Offline import of your authorized tables

If your license permits use of exported CSI spreadsheets in this software, import them privately using **the existing classifications.py**, without building additional modules. Source inputs may be CSV, TSV, TXT or XLSX (optional openpyxl; already used by NL-SfB builder).

From the SLAI root, with SPADRA installed under applications/SPADRA/:

    python -m applications.SPADRA.classifications --authorized --edition "YOUR LICENSED EDITION" --table21 "C:/private/Table21.xlsx" --table23 "C:/private/Table23.xlsx" --assembly "C:/private/UniFormat.csv" --mapping "C:/private/VerifiedCrosswalk.csv"

The --assembly and --mapping options are **optional**. Importing Table 21 and 23 alone supports searching and manual assignment; automatic Assembly Code → Table 21 requires either a verified local mapping CSV or the licensed API. CSV/Excel table columns accepted: "OmniClass Number" or "Code", and "Title"/"Description" (alternatively "Level N Title", deepest nonempty level). Mapping CSV columns: assembly_code, omniclass_code, table, verified, source. Each mapping must explicitly have verified=true and table=21. UniFormat CSV uses "Assembly Code" and "Assembly Description".

The importer validates table prefixes, unique codes, editions and mapping targets, writes the new catalog atomically, and stores it at data/omniclass.local.json (gitignored) by default. It never uploads the licensed content.

For an already prepared JSON catalog, copy omniclass.example.json to data/omniclass.local.json (ignored by Git) or set SPADRA_OMNICLASS_CATALOG to a private JSON path.

A private local catalog takes precedence when both options are configured; unset SPADRA_OMNICLASS_CATALOG and remove the default private file if you want the live API to be used instead.

Local catalog format (placeholders are not actual classification codes):

    {
      "schema": "spadra.omniclass.v1",
      "edition": "YOUR LICENSED EDITION",
      "tables": {
        "21": [{"code": "<licensed-code>", "title": "<licensed-title>"}],
        "23": [{"code": "<licensed-code>", "title": "<licensed-title>"}]
      },
      "assemblies": [{"code": "<your UniFormat code>", "title": "<licensed or authorized UniFormat description>"}],
      "assembly_mappings": [{
        "assembly_code": "<your UniFormat code>",
        "omniclass_code": "<Table 21 code>",
        "table": 21,
        "verified": true,
        "source": "<authorized crosswalk evidence>"
      }]
    }

Mapping rules: A selected Assembly Code autofills OmniClass **Table 21** only when exactly one distinct, explicitly verified local or CSI Crosswalk mapping exists. Missing/ambiguous mappings produce no automatic assignment. Table 23 is independent. The value and evidence are saved in the same undoable edit. A manually selected Table 21 code is preserved when an Assembly Code changes; an automatically selected value is updated/cleared when its Assembly Code changes. No real codes or crosswalks are invented.

## API

- GET /api/classifications: IFC version, count and OmniClass catalog availability.
- GET /api/classifications/search?kind=ifc&q=Wall
- GET /api/classifications/search?kind=assembly&q=A
- GET /api/classifications/search?kind=omniclass&table=21&q=wall

Properties live in SGEO spadra: metadata and travel with supported SPADRA native save, undo/redo, and native SGEO exchange. IFC metadata fields are not a claim of complete IFC exchange support.


## Validation and limitations

Search and validation run on the server. The client does not contain or download the full copyrighted data. Offline import and CSI GraphQL contracts are tested with **synthetic codes only**; the actual CSI authenticated endpoint cannot be end-to-end tested without the owner's license and key. This implementation does not change IFC interchange semantics.

The public repository deliberately contains **no complete OmniClass code lists**: shipping those would require specific authorization from CSI. See https://www.csiresources.org/csistore/oc-eula and https://crosswalk.squarespace.com/licensing.
