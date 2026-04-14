"""grading --workspace 用 Oracle 分（与 tasks/02-exec/run.sh verify_exec 一致）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

_EXPECT = (
    ("out/step1.txt", "42"),
    ("out/step2.txt", "c.txt"),
    ("out/step3.txt", "hello"),
)


def _first_line_trim(p: Path) -> str:
    if not p.is_file():
        return ""
    try:
        line = p.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return ""
    return line.strip()


def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    checks: list[dict[str, Any]] = []
    n = len(_EXPECT)
    for rel, want in _EXPECT:
        fp = w / rel
        got = _first_line_trim(fp)
        ok = got == want
        checks.append(
            {
                "id": rel.replace("/", "_"),
                "label": f"{rel} == {want!r}",
                "pass": ok,
                "weight": round(1.0 / n, 4),
                "detail": None if ok else f"got {got!r}",
            }
        )
    outcome = round(sum(1 for c in checks if c["pass"]) / len(_EXPECT), 4)
    return {
        "task": "02-exec",
        "workspace": str(w),
        "checks": checks,
        "outcome_score": outcome,
    }
