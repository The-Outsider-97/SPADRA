"""``python applications/SPADRA/run_spadra.py`` (or double-click) starts SPADRA; the same as ``python -m applications.SPADRA``."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))          # the SLAI root, so ``applications`` and ``src`` import

from applications.SPADRA.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
