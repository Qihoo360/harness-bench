from __future__ import annotations

from pathlib import Path
from typing import Any


def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    checks = []
    for rel in ("out/image1_answer.txt", "out/image2_answer.txt"):
        path = w / rel
        body = ""
        if path.is_file():
            body = path.read_text(encoding="utf-8", errors="replace").strip()
        checks.append(
            {
                "id": rel.replace("/", "_"),
                "label": f"{rel} exists and is non-empty",
                "pass": bool(body),
                "weight": 0.5,
                "detail": None if body else "missing or empty",
            }
        )
    return {
        "task": "08-image-recognize",
        "workspace": str(w),
        "checks": checks,
        "outcome_score": round(sum(c["weight"] for c in checks if c["pass"]), 4),
    }
