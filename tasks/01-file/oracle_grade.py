from __future__ import annotations

from pathlib import Path
from typing import Any


def score_workspace(workspace: Path) -> dict[str, Any]:
    target = workspace / "out" / "linecount.txt"
    value = ""
    if target.is_file():
        value = target.read_text(encoding="utf-8", errors="replace").strip()
    ok = value == "4"
    return {
        "task": "01-file",
        "workspace": str(workspace),
        "checks": [
            {
                "id": "linecount",
                "label": "out/linecount.txt == 4",
                "pass": ok,
                "weight": 1.0,
                "detail": None if ok else f"got {value!r}",
            }
        ],
        "outcome_score": 1.0 if ok else 0.0,
    }

