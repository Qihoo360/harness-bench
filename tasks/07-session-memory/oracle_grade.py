"""Oracle：phase1_done.txt 恰为 ready；recalled.txt 与 memory_secret 一致。"""
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
    checks: list[dict[str, Any]] = []

    if not gt_path.is_file():
        return {
            "task": "07-session-memory",
            "workspace": str(w),
            "checks": [],
            "outcome_score": 0.0,
            "error": f"missing ground_truth: {gt_path}",
        }

    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    secret = str(gt.get("memory_secret", "")).strip()
    phase1_exact = str(gt.get("phase1_done_exact", "ready")).strip()

    p1 = w / "out" / "phase1_done.txt"
    p1_ok = False
    p1_detail = None
    if p1.is_file():
        try:
            body = p1.read_text(encoding="utf-8", errors="replace").strip()
            p1_ok = body == phase1_exact
            if not p1_ok:
                p1_detail = f"got {body!r}, expected {phase1_exact!r}"
        except OSError as e:
            p1_detail = str(e)
    else:
        p1_detail = "missing"
    checks.append(
        {
            "id": "phase1_done",
            "label": f"out/phase1_done.txt == {phase1_exact!r}",
            "pass": p1_ok,
            "weight": 0.25,
            "detail": p1_detail,
        }
    )

    rec = w / "out" / "recalled.txt"
    rec_ok = False
    rec_detail = None
    if rec.is_file():
        try:
            got = rec.read_text(encoding="utf-8", errors="replace").strip()
            rec_ok = got == secret
            if not rec_ok:
                rec_detail = f"got {got!r}, expected {secret!r}"
        except OSError as e:
            rec_detail = str(e)
    else:
        rec_detail = "missing"
    checks.append(
        {
            "id": "recalled_secret",
            "label": "out/recalled.txt matches memory_secret",
            "pass": rec_ok,
            "weight": 0.75,
            "detail": rec_detail,
        }
    )

    outcome = round(sum(c["weight"] for c in checks if c["pass"]), 4)
    return {
        "task": "07-session-memory",
        "workspace": str(w),
        "checks": checks,
        "outcome_score": outcome,
    }
