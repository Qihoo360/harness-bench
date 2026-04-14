from __future__ import annotations

from pathlib import Path
from typing import Any


def score_workspace(workspace: Path) -> dict[str, Any]:
    target = workspace / "out" / "page_extract.txt"
    text = ""
    if target.is_file():
        text = target.read_text(encoding="utf-8", errors="replace")
    ok = "BENCHMARK_PAGE" in text
    return {
        "task": "03-browser",
        "workspace": str(workspace),
        "checks": [
            {
                "id": "page_extract",
                "label": "out/page_extract.txt contains BENCHMARK_PAGE",
                "pass": ok,
                "weight": 1.0,
                "detail": None if ok else "missing marker",
            }
        ],
        "outcome_score": 1.0 if ok else 0.0,
    }

