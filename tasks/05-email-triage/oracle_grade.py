"""grading --workspace 用 Oracle 分（与 verify_oracle 规则一致，拆成加权 checks）。"""
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
            "task": "05-email-triage",
            "workspace": str(w),
            "checks": [],
            "outcome_score": 0.0,
            "error": f"missing ground_truth: {gt_path}",
        }

    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    exp_labels: dict[str, str] = gt.get("labels") or {}
    exp_delete: list[str] = sorted(gt.get("delete_ids") or [])
    reply_required: list[str] = list(gt.get("reply_required_ids") or [])
    n_checks = len(exp_labels) + 1 + len(reply_required)
    weight = round(1.0 / n_checks, 6) if n_checks else 0.0

    triage_path = w / "out" / "triage.json"
    triage: dict = {}
    if triage_path.is_file():
        try:
            raw = json.loads(triage_path.read_text(encoding="utf-8"))
            triage = raw if isinstance(raw, dict) else {}
        except json.JSONDecodeError:
            triage = {}

    for eid, exp in exp_labels.items():
        ok = False
        detail = None
        if eid in triage and isinstance(triage[eid], dict):
            got = str(triage[eid].get("label", "")).strip()
            ok = got == exp
            if not ok:
                detail = f"got {got!r}"
        else:
            detail = "missing id"
        checks.append(
            {
                "id": f"label_{eid}",
                "label": f"triage[{eid}] == {exp}",
                "pass": ok,
                "weight": weight,
                "detail": detail,
            }
        )

    delete_path = w / "out" / "delete_ids.txt"
    raw_del = ""
    if delete_path.is_file():
        raw_del = delete_path.read_text(encoding="utf-8", errors="replace")
    got_delete = sorted({ln.strip() for ln in raw_del.splitlines() if ln.strip()})
    pass_del = got_delete == exp_delete
    checks.append(
        {
            "id": "delete_ids",
            "label": "delete_ids.txt matches spam list",
            "pass": pass_del,
            "weight": weight,
            "detail": None if pass_del else f"got {got_delete}, expected {exp_delete}",
        }
    )

    for eid in reply_required:
        rpath = w / "out" / "replies" / f"{eid}.txt"
        body = ""
        if rpath.is_file():
            body = rpath.read_text(encoding="utf-8", errors="replace").strip()
        ok = bool(body)
        checks.append(
            {
                "id": f"reply_nonempty_{eid}",
                "label": f"out/replies/{eid}.txt exists and non-empty",
                "pass": ok,
                "weight": weight,
                "detail": None if ok else ("missing" if not rpath.is_file() else "empty"),
            }
        )

    outcome = round(sum(c["weight"] for c in checks if c["pass"]), 4)
    return {
        "task": "05-email-triage",
        "workspace": str(w),
        "checks": checks,
        "outcome_score": outcome,
    }
