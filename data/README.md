# SPADRA classification data

SPADRA supports three distinct classifications:

- NL-SfB: existing official 0–4 tables in nl_sfb.json (unchanged).
- OmniClass: **all 15 CSI table facets** (Tables 11–14, 21–23, 31–36, 41 and 49) through licensed CSI Crosswalk or private authorized data. Table 21 and Table 23 are shown as primary model properties, with the other 13 under a collapsed "OmniClass (Other tables)" section.
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

CSI publishes the Crosswalk v3 GraphQL API and supplies API keys to licensed users. This API provides searchable official OmniClass classifications across **all 15 tables**, UniFormat and authoritative UniFormat→OmniClass crosswalks. Request appropriate access and software rights from CSI, then set the server-side environment variable:

    SPADRA_CSI_CROSSWALK_KEY=<your licensed key>

Keep the key in the server environment or a secrets manager, **never in the client, source code, project file, frontend storage, or GitHub**. The backend connects only to https://crosswalk.azure-api.net/v3/query with a fixed HTTPS origin and short timeout. It never returns the key.

With a configured key, the Properties panel can search the full licensed hierarchy; selecting a UniFormat Assembly Code triggers a source-backed crosswalk query. **Only one distinct OmniClass Table 21 candidate is filled automatically.** Table 23 always requires independent selection. Errors, unavailable credentials, and ambiguous candidates are reported or remain unassigned, never guessed.

API evidence: https://github.com/csi-net/crosswalk (public SDK and Postman GraphQL collection). CSI licensing: https://crosswalk.squarespace.com/licensing

### Option B — Offline import of your authorized tables

If your license permits use of exported CSI spreadsheets in this software, import them privately using **the existing classifications.py**, without building additional modules. Source inputs may be CSV, TSV, TXT or XLSX (optional openpyxl; already used by NL-SfB builder).

From the SLAI root, with SPADRA installed under applications/SPADRA/:

    python -m applications.SPADRA.classifications --authorized --edition "YOUR LICENSED EDITION" --table21 "C:/private/Table21.xlsx" --table23 "C:/private/Table23.xlsx" --assembly "C:/private/UniFormat.csv" --mapping "C:/private/VerifiedCrosswalk.csv"

To import any of the other 13 tables, repeat --extra-table NUMBER=PATH, for example --extra-table "13=C:/private/Table13.xlsx" --extra-table "41=C:/private/Table41.xlsx". The --assembly, --mapping, and --extra-table options are **optional**. Importing Table 21 and 23 alone supports searching and manual assignment; automatic Assembly Code → Table 21 requires either a verified local mapping CSV or the licensed API. CSV/Excel table columns accepted: "OmniClass Number" or "Code", and "Title"/"Description" (alternatively "Level N Title", deepest nonempty level). Mapping CSV columns: assembly_code, omniclass_code, table, verified, source. Each mapping must explicitly have verified=true and table=21. UniFormat CSV uses "Assembly Code" and "Assembly Description".

The importer validates table prefixes, unique codes, editions and mapping targets, writes the new catalog atomically, and stores it at data/omniclass.local.json (gitignored) by default. It never uploads the licensed content.

For an already prepared JSON catalog, copy omniclass.example.json to data/omniclass.local.json (ignored by Git) or set SPADRA_OMNICLASS_CATALOG to a private JSON path.

A private local catalog takes precedence when both options are configured; unset SPADRA_OMNICLASS_CATALOG and remove the default private file if you want the live API to be used instead.

Local catalog format (placeholders are not actual classification codes):

    {
      "schema": "spadra.omniclass.v1",
      "edition": "YOUR LICENSED EDITION",
      "tables": {
        "21": [{"code": "<licensed-code>", "title": "<licensed-title>"}],
        "23": [{"code": "<licensed-code>", "title": "<licensed-title>"}],
        "13": [{"code": "<authorized-space-code>", "title": "<authorized-space-title>"}]
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
- GET /api/classifications/search?kind=omniclass&table=13&q=space (or any other supported table)

Properties live in SGEO spadra: metadata and travel with supported SPADRA native save, undo/redo, and native SGEO exchange. IFC metadata fields are not a claim of complete IFC exchange support.


## Validation and limitations

Search and validation run on the server. The client does not contain or download the full copyrighted data. Offline import and CSI GraphQL contracts are tested with **synthetic codes only**; the actual CSI authenticated endpoint cannot be end-to-end tested without the owner's license and key. This implementation does not change IFC interchange semantics.

The public repository deliberately contains **no complete OmniClass code lists**: shipping those would require specific authorization from CSI. See https://www.csiresources.org/csistore/oc-eula and https://crosswalk.squarespace.com/licensing.

## IFC property and quantity sets (ifc_psets.json)

`ifc_psets.json` holds the buildingSMART property sets (`Pset_*Common`) and quantity sets (`Qto_*BaseQuantities`) SPADRA offers on its elements: Wall, Slab, Roof, Door, Window, Beam, Column and Space. Every set records its `edition` and the published `source` page it was transcribed from. It is **transcribed from the published documentation pages, not parsed from the schema**, and two sets (`Pset_ColumnCommon`, `Pset_SlabCommon`) come from IFC 4.4 development pages and `Qto_ColumnBaseQuantities` from the IFC 4 page because the IFC 4.3 pages could not be fetched; the file says so per set. To take applicability and definitions from the schema itself, point the builder at the buildingSMART PSD files:

    python data/build_ifc_psets.py --psd C:/ifc/psd --edition "IFC4X3_ADD2"

(The PSD reader is covered by a synthetic-file test only; it has not been run against the real PSD files.) Property values are stored in the element's SGEO metadata (`spadra:psets`), type-level values in the project (`bim.type_psets`); quantities are calculated from the geometry on demand and only imported/overridden values are stored (`spadra:qto`). A property an official set does not define is a *custom* property and is listed apart from the official sets.

## bSDD

bSDD references are looked up through `bimdata.Bsdd` (fixed origin `https://api.bsdd.buildingsmart.org`, read-only, no key). Answers are cached in `data/bsdd_cache.local.json` (7 days; stale entries are served, marked, when the service is unreachable; the offline switch uses only the cache). The endpoint paths in `bimdata.BSDD_PATHS` follow the published v1 API but **have not been exercised against the live service**; behaviour is tested with a stand-in transport. A suggestion is never a reference until a person confirms it; an element holds one class per dictionary.

## IDS and the BIM basis ILS

`.ids` files are stored in the project (`bim.ids`), validated element by element, and the evidence is kept (`bim.ids_results`). The BIM basis ILS rules in `requirements.py` are each transcribed from one chapter of the published ILS (link and grading stored with the rule); the ILS is off until a project enables it and its edition label is a project setting.
