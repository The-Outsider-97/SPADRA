"""Sealing and verifying regulation datasets against their official source.

``seal`` writes a dataset's manifest (the SHA-256 of its content). ``verify`` reads the official consolidated text (the BWB XML of the Dutch government, from a file or a URL
you give it) and checks every entry of the dataset's ``verify`` list: an article that must contain given figures, a table row that must end in given values. Only when *all* checks pass is
the dataset recorded as ``verified_official``; any failure records ``failed`` with the failing checks, and nothing about the dataset's rules is changed.

What is never done: the official text is not stored (only its SHA-256, URL and retrieval time); no value is copied from the source into the dataset by this tool (a person edits
the dataset, then verifies it); a network failure is reported and leaves the manifest as it was.

Run from the repository root::

    python -m applications.SPADRA.regulations list
    python -m applications.SPADRA.regulations seal   NL nl.bbl 2025-01-10
    python -m applications.SPADRA.regulations verify NL nl.bbl 2025-01-10 --date 2026-09-24
    python -m applications.SPADRA.regulations verify NL nl.bbl 2025-01-10 --xml C:\\path\\to\\BWBR0041297.xml
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import datasets as DS

MAX_XML_BYTES = 80_000_000
BWB_URL = "https://repository.officiele-overheidspublicaties.nl/bwb/{bwb}/{date}_0/xml/{bwb}_{date}_0.xml"
_BWB_ID = re.compile(r"^BWBR\d{7}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TOOL = "spadra.regulations.ingest/1"


class IngestError(Exception):
    pass


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip().lower()


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(el: ET.Element) -> str:
    return _norm("".join(el.itertext()))


def parse(data: bytes) -> ET.Element:
    """Parse the official XML defensively: no DOCTYPE or entity declarations (so no entity expansion), a size cap, and a clear error for anything else."""
    if len(data) > MAX_XML_BYTES:
        raise IngestError(f"The XML is larger than {MAX_XML_BYTES // 1_000_000} MB")
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data[:200_000].lower():
        raise IngestError("The XML declares a DOCTYPE or entities; it is refused rather than expanded")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise IngestError(f"The XML cannot be parsed: {exc}") from exc


def articles(root: ET.Element) -> dict[str, list[ET.Element]]:
    """Article number → its elements. An article's number is the text of its ``kop/nr``."""
    out: dict[str, list[ET.Element]] = {}
    for el in root.iter():
        if _local(el.tag) != "artikel":
            continue
        kop = next((c for c in el if _local(c.tag) == "kop"), None)
        nr = next((_text(c) for c in (kop if kop is not None else []) if _local(c.tag) == "nr"), None)
        if nr:
            out.setdefault(nr.strip(". "), []).append(el)
    return out


def _rows(root: ET.Element) -> list[list[str]]:
    rows = []
    for el in root.iter():
        if _local(el.tag) == "row":
            cells = [_text(c) for c in el if _local(c.tag) == "entry"]
            if any(cells):
                rows.append(cells)
    return rows


def run_checks(root: ET.Element, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each check as ``{check, ok, detail}``. A check that cannot be run (the article is not in the XML) fails; it is never skipped."""
    arts, rows = articles(root), _rows(root)
    results: list[dict[str, Any]] = []
    for chk in checks:
        kind = chk.get("kind")
        if kind == "article_contains":
            els = arts.get(str(chk.get("article")), [])
            body = " ".join(_text(e) for e in els)
            missing = [t for t in chk.get("tokens", []) if _norm(t) not in body]
            ok = bool(els) and not missing
            results.append({"check": chk, "ok": ok, "detail": "article not found in the XML" if not els else (f"missing in the article: {', '.join(missing)}" if missing else "all tokens present")})
        elif kind == "table_row":
            label, tail = _norm(str(chk.get("row_label", ""))), [_norm(t) for t in chk.get("tail", [])]
            hits = [r for r in rows if any(label in c for c in r)]
            good = [r for r in hits if [c for c in r if c][-len(tail):] == tail] if tail else bool(hits)
            ok = bool(good)
            results.append({"check": chk, "ok": ok, "detail": f"no table row mentions '{chk.get('row_label')}'" if not hits else ("row found and its last values match" if ok else
                                                                                                                                    f"row found but its last values are {[c for c in hits[0] if c][-len(tail):]}, not {tail}")})
        else:
            results.append({"check": chk, "ok": False, "detail": f"unknown check kind '{kind}'"})
    return results


def fetch(url: str, *, timeout: int = 60) -> bytes:
    if not url.startswith("https://"):
        raise IngestError("Only https:// sources are fetched")
    req = urllib.request.Request(url, headers={"User-Agent": "SPADRA-regulation-ingest/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:                  # noqa: S310 - https only, fixed host pattern chosen by the person running the tool
            data = resp.read(MAX_XML_BYTES + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise IngestError(f"The source could not be downloaded ({type(exc).__name__}: {exc}). Nothing was changed.") from exc
    if len(data) > MAX_XML_BYTES:
        raise IngestError("The download is larger than the size limit")
    return data


def _folder(jurisdiction: str, dataset_id: str, version: str, root: Path | None) -> Path:
    base = Path(root) if root is not None else DS.DATA_ROOT
    folder = base / jurisdiction / dataset_id / version
    if not (folder / "dataset.json").is_file():
        raise IngestError(f"No dataset at {folder}")
    return folder


def seal(jurisdiction: str, dataset_id: str, version: str, *, root: Path | None = None) -> dict[str, Any]:
    """Write the manifest for a dataset. Content that changed since the last seal resets verification: the new content has not been checked."""
    folder = _folder(jurisdiction, dataset_id, version, root)
    data = json.loads((folder / "dataset.json").read_text(encoding="utf-8"))
    problems = DS.validate_dataset(data)
    if problems:
        raise IngestError("The dataset is not valid: " + "; ".join(problems[:5]))
    sha = DS.dataset_sha256(data)
    try:
        old = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    verification = old.get("verification") if old.get("dataset_sha256") == sha else None
    manifest = {"schema": DS.MANIFEST_SCHEMA, "dataset_sha256": sha, "dataset_file": "dataset.json", "sealed": datetime.now(timezone.utc).date().isoformat(),
                "verification": verification or {"status": "unverified", "checked_at": None, "tool": None, "checks": []}}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def verify(jurisdiction: str, dataset_id: str, version: str, *, xml: bytes | None = None, source: str | None = None, date: str | None = None, root: Path | None = None) -> dict[str, Any]:
    """Check a dataset against the official XML and record the result in its manifest. Provide ``xml`` (bytes, with ``source`` saying where from) or a ``date`` to download."""
    folder = _folder(jurisdiction, dataset_id, version, root)
    ds = DS.load_dataset(folder)
    if not ds.usable:
        raise IngestError("The dataset is not valid or does not match its manifest (seal it first): " + "; ".join(ds.problems[:3]))
    checks = ds.data.get("verify") or []
    if not checks:
        raise IngestError("The dataset lists no 'verify' checks, so there is nothing to verify it against")
    if xml is None:
        if not date or not _DATE.match(date):
            raise IngestError("Give --xml FILE, or --date YYYY-MM-DD of the consolidation to download")
        bwb = str(ds.data["source"]["citation"])
        if not _BWB_ID.match(bwb):
            raise IngestError("The dataset's source citation is not a BWB id, so there is no official XML to download")
        source = BWB_URL.format(bwb=bwb, date=date)
        xml = fetch(source)
    results = run_checks(parse(xml), checks)
    ok = all(r["ok"] for r in results)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["verification"] = {"status": "verified_official" if ok else "failed", "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tool": TOOL,
                                "source": {"url": source, "sha256": hashlib.sha256(xml).hexdigest(), "bytes": len(xml)},
                                "checks": [{"kind": r["check"].get("kind"), "article": r["check"].get("article") or r["check"].get("table"), "ok": r["ok"], "detail": r["detail"]} for r in results]}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest["verification"]
