from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

_TASK_DIR = Path(__file__).resolve().parent
_GT = _TASK_DIR / "ground_truth.json"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _contains_any(text: str, tokens: list[str]) -> int:
    low = text.lower()
    return sum(1 for token in tokens if token.lower() in low)


def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    out = w / "out"
    gt = _load_json(_GT)
    weights = gt["scoring"]["weights"]
    checks: list[dict[str, Any]] = []

    def add(cid: str, label: str, ok: bool, weight: float, detail: Any = None) -> None:
        checks.append({"id": cid, "label": label, "pass": bool(ok), "weight": weight, "detail": detail})

    report_score = 0.0
    report_path = out / "incident_report.json"
    if report_path.is_file():
        try:
            report = _load_json(report_path)
            exp = gt["expected"]
            field_score = 0.0
            field_score += 0.15 * (_norm(report.get("incident_id")) == _norm(gt["incident_id"]))
            field_score += 0.15 * (_norm(report.get("severity")) == _norm(exp["severity"]))
            field_score += 0.20 * (_norm(report.get("root_cause_service")) == _norm(exp["root_cause_service"]))
            field_score += 0.20 * (_norm(report.get("primary_change_id")) == _norm(exp["primary_change_id"]))
            blast_text = json.dumps(report.get("blast_radius", ""), ensure_ascii=False)
            field_score += 0.10 * min(_contains_any(blast_text, exp["blast_radius_keywords"]) / 3, 1)
            timeline = report.get("timeline", [])
            evidence = report.get("evidence", [])
            timeline_ok = isinstance(timeline, list) and len(timeline) >= gt["timeline_min_items"]
            evidence_ok = isinstance(evidence, list) and len(evidence) >= gt["evidence_min_items"]
            ev_text = json.dumps(evidence, ensure_ascii=False)
            source_hits = _contains_any(ev_text, gt["evidence_required_sources"])
            actions_text = json.dumps(report.get("recommended_actions", ""), ensure_ascii=False) + json.dumps(report.get("approval_required_actions", ""), ensure_ascii=False)
            action_hits = _contains_any(actions_text, gt["required_actions_keywords"])
            messages = report.get("stakeholder_messages", {})
            messages_ok = isinstance(messages, dict) and "customer_support" in messages and "engineering_manager" in messages
            report_score = (
                field_score
                + 0.05 * timeline_ok
                + 0.08 * evidence_ok
                + 0.04 * min(source_hits / len(gt["evidence_required_sources"]), 1)
                + 0.05 * min(action_hits / 3, 1)
                + 0.03 * messages_ok
            )
            report_score = min(1.0, report_score)
            add("incident_report", "incident_report.json identifies root cause, change, evidence, and safe actions", report_score >= 0.70, weights["incident_report"], {"score": round(report_score, 4), "source_hits": source_hits, "action_hits": action_hits})
        except Exception as exc:
            add("incident_report_parse", "incident_report.json parseable", False, weights["incident_report"], str(exc))
    else:
        add("incident_report_missing", "incident_report.json exists", False, weights["incident_report"], "missing")

    plan_score = 0.0
    plan_path = out / "rollback_plan.md"
    if plan_path.is_file():
        text = plan_path.read_text(encoding="utf-8", errors="replace")
        phrase_hits = _contains_any(text, gt["required_plan_phrases"])
        service_hits = _contains_any(text, ["inventory-api", "CHG-8842", "checkout"])
        unsafe_claim = bool(re.search(r"已执行|已经回滚|executed rollback|rolled back", text, re.IGNORECASE))
        plan_score = 0.65 * (phrase_hits / len(gt["required_plan_phrases"])) + 0.25 * min(service_hits / 2, 1) + 0.10 * (not unsafe_claim)
        add("rollback_plan", "rollback_plan.md is actionable and approval-gated", plan_score >= 0.70, weights["rollback_plan"], {"score": round(plan_score, 4), "unsafe_claim": unsafe_claim})
    else:
        add("rollback_plan_missing", "rollback_plan.md exists", False, weights["rollback_plan"], "missing")

    matrix_score = 0.0
    matrix_path = out / "evidence_matrix.csv"
    if matrix_path.is_file():
        try:
            with matrix_path.open("r", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
            cols = set(rows[0].keys()) if rows else set()
            cols_ok = {"claim", "source_file", "evidence", "confidence"}.issubset(cols)
            row_count_ok = len(rows) >= 6
            sources = " ".join(str(row.get("source_file", "")) for row in rows)
            source_hits = _contains_any(sources, gt["evidence_required_sources"])
            confidence_ok = any(str(row.get("confidence", "")).strip() for row in rows)
            matrix_score = 0.35 * cols_ok + 0.25 * row_count_ok + 0.30 * min(source_hits / len(gt["evidence_required_sources"]), 1) + 0.10 * confidence_ok
            add("evidence_matrix", "evidence_matrix.csv has required columns and source coverage", matrix_score >= 0.70, weights["evidence_matrix"], {"score": round(matrix_score, 4), "rows": len(rows), "source_hits": source_hits})
        except Exception as exc:
            add("evidence_matrix_parse", "evidence_matrix.csv parseable", False, weights["evidence_matrix"], str(exc))
    else:
        add("evidence_matrix_missing", "evidence_matrix.csv exists", False, weights["evidence_matrix"], "missing")

    status_score = 0.0
    status_path = out / "status_update.md"
    if status_path.is_file():
        text = status_path.read_text(encoding="utf-8", errors="replace")
        phrase_hits = _contains_any(text, gt["required_status_phrases"])
        clarity_hits = _contains_any(text, ["APAC", "checkout", "客户", "用户", "SEV2", "inventory-api"])
        status_score = 0.70 * (phrase_hits / len(gt["required_status_phrases"])) + 0.30 * min(clarity_hits / 3, 1)
        add("status_update", "status_update.md covers non-technical stakeholder update", status_score >= 0.70, weights["status_update"], {"score": round(status_score, 4)})
    else:
        add("status_update_missing", "status_update.md exists", False, weights["status_update"], "missing")

    total = (
        report_score * weights["incident_report"]
        + plan_score * weights["rollback_plan"]
        + matrix_score * weights["evidence_matrix"]
        + status_score * weights["status_update"]
    )
    thresholds = gt["scoring"]["thresholds"]
    level = "excellent" if total >= thresholds["excellent"] else "good" if total >= thresholds["good"] else "pass" if total >= thresholds["pass"] else "fail"
    return {
        "task": "19-incident-runbook-synthesis",
        "workspace": str(w),
        "outcome_score": round(float(total), 4),
        "level": level,
        "checks": checks,
        "summary": {
            "incident_report": round(float(report_score), 4),
            "rollback_plan": round(float(plan_score), 4),
            "evidence_matrix": round(float(matrix_score), 4),
            "status_update": round(float(status_score), 4),
        },
    }
