from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_TASK_DIR = Path(__file__).resolve().parent
_GT = _TASK_DIR / "ground_truth.json"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _get_path(data: dict[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _provider_name(value: Any) -> str:
    text = str(value or "").lower()
    if "anthropic" in text or "claude" in text:
        return "anthropic"
    if "openai" in text or "gpt" in text:
        return "openai"
    if "gemini" in text or "google" in text:
        return "gemini"
    return text.strip()


def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    out = w / "out"
    gt = _load_json(_GT)
    checks: list[dict[str, Any]] = []

    weights = gt["scoring"]["weights"]
    workload_expectations: dict[str, dict[str, str]] = gt["required_workloads"]
    min_reason = int(gt.get("min_reason_codes_per_workload", 2))
    min_risk = int(gt.get("min_risk_notes_per_workload", 1))

    def add(cid: str, label: str, ok: bool, weight: float, detail: Any = None) -> None:
        checks.append({"id": cid, "label": label, "pass": bool(ok), "weight": weight, "detail": detail})

    scorecard_path = out / "provider_scorecard.json"
    scorecard_score = 0.0
    if scorecard_path.is_file():
        try:
            scorecard = _load_json(scorecard_path)
            workloads = scorecard.get("workloads", {}) if isinstance(scorecard, dict) else {}
            per_workload = 1.0 / max(len(workload_expectations), 1)
            for wid, exp in workload_expectations.items():
                row = workloads.get(wid, {}) if isinstance(workloads, dict) else {}
                primary_ok = _provider_name(row.get("primary_provider")) == exp["primary_provider"]
                fallback_ok = _provider_name(row.get("fallback_provider")) == exp["fallback_provider"]
                reasons_ok = isinstance(row.get("reason_codes"), list) and len(row["reason_codes"]) >= min_reason
                risks_ok = isinstance(row.get("risk_notes"), list) and len(row["risk_notes"]) >= min_risk
                row_score = (0.45 * primary_ok) + (0.25 * fallback_ok) + (0.20 * reasons_ok) + (0.10 * risks_ok)
                scorecard_score += per_workload * row_score
            health_ok = isinstance(scorecard.get("provider_health"), dict) and len(scorecard.get("provider_health", {})) >= 3
            defaults_ok = isinstance(scorecard.get("recommended_defaults"), dict) and bool(scorecard.get("recommended_defaults"))
            scorecard_score = min(1.0, scorecard_score * 0.85 + 0.10 * health_ok + 0.05 * defaults_ok)
            add("scorecard", "provider_scorecard.json workload routing and metadata", scorecard_score >= 0.70, weights["scorecard"], {"score": round(scorecard_score, 4)})
        except Exception as exc:
            add("scorecard_parse", "provider_scorecard.json parseable", False, weights["scorecard"], str(exc))
    else:
        add("scorecard_missing", "provider_scorecard.json exists", False, weights["scorecard"], "missing")

    patch_path = out / "openclaw_config_patch.json"
    patch_score = 0.0
    if patch_path.is_file():
        try:
            patch = _load_json(patch_path)
            key_hits = sum(1 for key in gt["required_patch_keys"] if _get_path(patch, key) is not None)
            cache_trace_ok = _get_path(patch, "diagnostics.cacheTrace.enabled") is True
            text = json.dumps(patch, ensure_ascii=False).lower()
            retention_mentions = len(re.findall(r"cacheretention|cache_retention|cache retention", text))
            provider_mentions = sum(1 for name in ("anthropic", "openai", "gemini") if name in text)
            patch_score = (
                0.45 * (key_hits / len(gt["required_patch_keys"]))
                + 0.25 * bool(cache_trace_ok)
                + 0.15 * min(retention_mentions / 2, 1)
                + 0.15 * min(provider_mentions / 3, 1)
            )
            add("config_patch", "openclaw_config_patch.json includes required routing/cache keys", patch_score >= 0.70, weights["config_patch"], {"score": round(patch_score, 4), "key_hits": key_hits})
        except Exception as exc:
            add("config_patch_parse", "openclaw_config_patch.json parseable", False, weights["config_patch"], str(exc))
    else:
        add("config_patch_missing", "openclaw_config_patch.json exists", False, weights["config_patch"], "missing")

    playbook_path = out / "failover_playbook.md"
    playbook_score = 0.0
    if playbook_path.is_file():
        text = playbook_path.read_text(encoding="utf-8", errors="replace")
        phrase_hits = sum(1 for phrase in gt["required_playbook_phrases"] if phrase.lower() in text.lower())
        table_ok = "|" in text and "primary" in text.lower() and "fallback" in text.lower()
        workload_hits = sum(1 for wid in workload_expectations if wid in text)
        playbook_score = 0.55 * (phrase_hits / len(gt["required_playbook_phrases"])) + 0.25 * bool(table_ok) + 0.20 * (workload_hits / len(workload_expectations))
        add("playbook", "failover_playbook.md has provider-specific failover guidance", playbook_score >= 0.70, weights["playbook"], {"score": round(playbook_score, 4), "phrase_hits": phrase_hits, "workload_hits": workload_hits})
    else:
        add("playbook_missing", "failover_playbook.md exists", False, weights["playbook"], "missing")

    notes_path = out / "audit_notes.md"
    notes_score = 0.0
    if notes_path.is_file():
        text = notes_path.read_text(encoding="utf-8", errors="replace").lower()
        evidence_ok = "traces/" in text or "provider_capabilities.json" in text or "gateway_config.json" in text
        recommendation_ok = "recommend" in text or "建议" in text or "修改" in text
        issue_count = len(re.findall(r"^-|\n-", text))
        notes_score = 0.4 * bool(evidence_ok) + 0.3 * bool(recommendation_ok) + 0.3 * min(issue_count / 4, 1)
        add("audit_notes", "audit_notes.md cites evidence and recommendations", notes_score >= 0.65, weights["audit_notes"], {"score": round(notes_score, 4)})
    else:
        add("audit_notes_missing", "audit_notes.md exists", False, weights["audit_notes"], "missing")

    total = (
        scorecard_score * weights["scorecard"]
        + patch_score * weights["config_patch"]
        + playbook_score * weights["playbook"]
        + notes_score * weights["audit_notes"]
    )
    thresholds = gt["scoring"]["thresholds"]
    level = "excellent" if total >= thresholds["excellent"] else "good" if total >= thresholds["good"] else "pass" if total >= thresholds["pass"] else "fail"
    return {
        "task": "18-provider-failover-audit",
        "workspace": str(w),
        "outcome_score": round(float(total), 4),
        "level": level,
        "checks": checks,
        "summary": {
            "scorecard": round(float(scorecard_score), 4),
            "config_patch": round(float(patch_score), 4),
            "playbook": round(float(playbook_score), 4),
            "audit_notes": round(float(notes_score), 4),
        },
    }
