# Regulation datasets

Data only. No rule is written in code; the engine (`applications/SPADRA/regulations/`) reads these files.

* `registry.json`: every source considered, its licence class, how it is reached and whether it is usable. A source marked `blocked` contributes no rule.
* `<JURISDICTION>/<dataset id>/<version>/dataset.json`: the rules and tables, with a citation for each. Strings are short pinpoints; no instrument text is copied.
* `.../manifest.json`: the SHA-256 of `dataset.json` and what was checked against the official source. A dataset that no longer matches its manifest is refused.

## Adding or updating a dataset

1. Put the new `dataset.json` in a new `<version>` folder (never edit a published version: projects pin it by hash).
2. `python -m applications.SPADRA.regulations seal NL nl.bbl <version>` writes the manifest hash.
3. `python -m applications.SPADRA.regulations verify NL nl.bbl <version>` downloads the official source (needs network access), checks every `verify` entry and records the result in the manifest.
4. Rules stay graded `transcribed` until a person reviews them against the official text; the review status is shown on every finding.

## Licences

* `public_law`: laws and decrees (for example the Dutch Bbl, Auteurswet art. 11). Values and short pinpoints are stored.
* `restricted` / `licence_required` (for example ICC codes): nothing from the source is stored. The dataset holds no rules; values come from the user's own licensed import.
* Never invent a threshold. If a value cannot be traced to the source, the rule is not added.
