"""Run the containerapp Content Understanding golden-dataset evaluator."""

# ruff: noqa: E402,I001

from __future__ import annotations

import sys
from pathlib import Path

CONTAINERAPP_ROOT = Path(__file__).resolve().parents[1] / "src" / "containerapp"
sys.path.insert(0, str(CONTAINERAPP_ROOT))

from evaluation.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
