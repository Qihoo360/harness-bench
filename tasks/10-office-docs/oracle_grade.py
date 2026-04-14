"""grading --workspace：summary.json 数值与 report.docx 关键内容（与 verify_oracle 一致）。"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

_TASK_DIR = Path(__file__).resolve().parent
_DEFAULT_GT = _TASK_DIR / "ground_truth.json"

def _docx_plain_text(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    parts: list[str] = []
    for t in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"):
        if t.text:
            parts.append(t.text)
        if t.tail:
            parts.append(t.tail)
    return " ".join(parts)


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
            "task": "10-office-docs",
            "workspace": str(w),
            "checks": [],
            "outcome_score": 0.0,
            "error": f"missing ground_truth: {gt_path}",
        }

    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    exp_policy = str(gt.get("policy_id", "")).strip()
    exp_exclude = str(gt.get("exclude_status", "")).strip()
    exp_totals: dict[str, Any] = gt.get("totals_by_region") or {}
    exp_grand = gt.get("grand_total")
    must_have: list[str] = list(gt.get("docx_must_contain") or [])

    n_checks = 4 + len(must_have)
    weight = round(1.0 / n_checks, 6) if n_checks else 0.0

    summary_path = w / "out" / "summary.json"
    summary: dict[str, Any] = {}
    if summary_path.is_file():
        try:
            raw = json.loads(summary_path.read_text(encoding="utf-8"))
            summary = raw if isinstance(raw, dict) else {}
        except json.JSONDecodeError:
            summary = {}

    def add_check(
        cid: str,
        label: str,
        ok: bool,
        detail: str | None,
    ) -> None:
        checks.append(
            {
                "id": cid,
                "label": label,
                "pass": ok,
                "weight": weight,
                "detail": detail,
            }
        )

    got_policy = str(summary.get("policy_id", "")).strip()
    add_check(
        "policy_id",
        "summary.policy_id",
        got_policy == exp_policy,
        None if got_policy == exp_policy else f"got {got_policy!r}, expected {exp_policy!r}",
    )

    got_ex = str(summary.get("exclude_status", "")).strip()
    add_check(
        "exclude_status",
        "summary.exclude_status",
        got_ex == exp_exclude,
        None if got_ex == exp_exclude else f"got {got_ex!r}, expected {exp_exclude!r}",
    )

    got_totals = summary.get("totals_by_region")
    totals_ok = isinstance(got_totals, dict)
    if totals_ok:
        for k, v in exp_totals.items():
            if k not in got_totals:
                totals_ok = False
                break
            try:
                if float(got_totals[k]) != float(v):
                    totals_ok = False
                    break
            except (TypeError, ValueError):
                totals_ok = False
                break
        if totals_ok and len(got_totals) != len(exp_totals):
            totals_ok = False
    detail_totals = None
    if not totals_ok:
        detail_totals = f"got {got_totals!r}, expected {exp_totals!r}"
    add_check(
        "totals_by_region",
        "summary.totals_by_region",
        totals_ok,
        detail_totals,
    )

    try:
        got_grand = float(summary["grand_total"])
        g_ok = float(exp_grand) == got_grand
    except (KeyError, TypeError, ValueError):
        got_grand = None
        g_ok = False
    add_check(
        "grand_total",
        "summary.grand_total",
        g_ok,
        None if g_ok else f"got {got_grand!r}, expected {exp_grand!r}",
    )

    report_path = w / "out" / "report.docx"
    docx_text = ""
    docx_err: str | None = None
    if not report_path.is_file():
        docx_err = "missing out/report.docx"
    else:
        try:
            docx_text = _docx_plain_text(report_path)
        except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError) as e:
            docx_err = str(e)

    collapsed = re.sub(r"\s+", " ", docx_text)
    for token in must_have:
        if docx_err:
            ok = False
            detail = docx_err
        else:
            ok = token in docx_text or token in collapsed
            detail = None if ok else "substring not found"
        add_check(
            f"docx_contains_{_safe_id(token)}",
            f"report.docx contains {token!r}",
            ok,
            detail,
        )

    outcome = round(sum(c["weight"] for c in checks if c["pass"]), 4)
    return {
        "task": "10-office-docs",
        "workspace": str(w),
        "checks": checks,
        "outcome_score": outcome,
    }


def _safe_id(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", s)[:48].strip("_") or "x"
