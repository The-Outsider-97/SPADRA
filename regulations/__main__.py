"""``python -m applications.SPADRA.regulations <list|seal|verify>``: see ``ingest.py``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import datasets as DS
from . import ingest as IN


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m applications.SPADRA.regulations", description="Regulation datasets: list, seal, verify against the official source.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show installed datasets and their verification state")
    for name in ("seal", "verify"):
        p = sub.add_parser(name)
        p.add_argument("jurisdiction", choices=DS.JURISDICTIONS)
        p.add_argument("dataset")
        p.add_argument("version")
    v = sub.choices["verify"]
    v.add_argument("--xml", help="a downloaded official XML file")
    v.add_argument("--date", help="download the consolidation of this date (YYYY-MM-DD) from the official repository")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "list":
            for d in DS.load_all():
                info = d.describe()
                print(f"{info['jurisdiction']}  {info['id']}  {info['version']}  verification={info['verification']}  usable={info['usable']}  rules={info['rules']}  licence={info['licence']}")
                for pr in info["problems"]:
                    print(f"      problem: {pr}")
            return 0
        if args.cmd == "seal":
            m = IN.seal(args.jurisdiction, args.dataset, args.version)
            print(f"sealed {args.dataset} {args.version}: sha256 {m['dataset_sha256']}  verification={m['verification']['status']}")
            return 0
        data = Path(args.xml).read_bytes() if args.xml else None
        result = IN.verify(args.jurisdiction, args.dataset, args.version, xml=data, source=str(Path(args.xml).name) if args.xml else None, date=args.date)
        for c in result["checks"]:
            print(("PASS " if c["ok"] else "FAIL ") + f"{c['kind']} {c['article']}: {c['detail']}")
        print(f"result: {result['status']}")
        return 0 if result["status"] == "verified_official" else 2
    except (IN.IngestError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
