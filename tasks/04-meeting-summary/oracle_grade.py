"""grading --workspace：会议纪要摘要长度 + 必含词（与 verify_oracle 一致）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TASK_DIR = Path(__file__).resolve().parent
_DEFAULT_GT = _TASK_DIR / "ground_truth.json"


def score_workspace(
    workspace: Path,
    *,
    ground_truth_path: Path | None = None,
) -> dict[str, Any]:
    w = workspace.resolve()
    gt_path = ground_truth_path or _DEFAULT_GT
    if not gt_path.is_file():
        return {
            "task": "04-meeting-summary",
            "workspace": str(w),
            "checks": [],
            "outcome_score": 0.0,
            "error": f"missing ground_truth: {gt_path}",
        }

    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    rel = str(gt.get("summary_path") or "out/meeting_summary.txt")
    min_c = int(gt.get("summary_min_chars", 180))
    max_c = int(gt.get("summary_max_chars", 480))
    phrases: list[str] = list(gt.get("required_phrases") or [])

    sp = w / rel
    text = ""
    if sp.is_file():
        try:
            text = sp.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""

    n = len(text)
    n_checks = 1 + len(phrases)
    weight = round(1.0 / n_checks, 6) if n_checks else 0.0

    checks: list[dict[str, Any]] = []

    ok_len = min_c <= n <= max_c
    checks.append(
        {
            "id": "summary_length",
            "label": f"meeting_summary.txt char count in [{min_c}, {max_c}]",
            "pass": ok_len,
            "weight": weight,
            "detail": None if ok_len else f"got {n} chars (file missing or empty counts as 0)",
        }
    )

    for ph in phrases:
        contained = ph in text
        checks.append(
            {
                "id": f"phrase_{ph}",
                "label": f"summary contains {ph!r}",
                "pass": contained,
                "weight": weight,
                "detail": None if contained else "substring not found",
            }
        )

    outcome = round(sum(c["weight"] for c in checks if c["pass"]), 4)
    return {
        "task": "04-meeting-summary",
        "workspace": str(w),
        "checks": checks,
        "outcome_score": outcome,
    }
