from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _to_native(obj: Any) -> Any:
    """递归转换 numpy/pandas 类型为原生 Python 类型"""
    if hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(v) for v in obj]
    if isinstance(obj, (bool, int, float, str)):
        return obj
    return str(obj)  # fallback


def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    out_dir = w / "out"
    task_dir = w.parent.parent
    gt_path = task_dir / "ground_truth.json"
    gt = json.loads(gt_path.read_text(encoding="utf-8")) if gt_path.exists() else {}

    checks = []
    trust_score = 0.0
    contra_score = 0.0
    report_score = 0.0

    # ---------- 1. 可信度评估 (25%) ----------
    trust_file = out_dir / "trustworthiness.json"
    if trust_file.exists():
        try:
            data = json.loads(trust_file.read_text(encoding="utf-8"))
            expected = gt.get("expected_trust_scores", {})
            diff_sum = 0.0
            for doc, exp in expected.items():
                actual = data.get(doc, {}).get("score", 0)
                diff_sum += abs(actual - exp) / 10.0
            accuracy = 1.0 - (diff_sum / len(expected)) if expected else 0.0
            trust_score = max(0.0, min(1.0, accuracy))
            checks.append({
                "id": "trust_assessment",
                "label": "可信度评估准确度",
                "pass": bool(trust_score >= 0.7),
                "weight": 0.25,
                "detail": {"accuracy": round(float(trust_score), 4)}
            })
        except Exception as e:
            checks.append({"id": "trust_error", "label": str(e), "pass": False, "weight": 0.25, "detail": None})
    else:
        checks.append({"id": "trust_missing", "label": "trustworthiness.json missing", "pass": False, "weight": 0.25, "detail": None})

    # ---------- 2. 矛盾检测 (35%) ----------
    contra_file = out_dir / "contradictions.json"
    if contra_file.exists():
        try:
            data = json.loads(contra_file.read_text(encoding="utf-8"))
            key_contradictions = gt.get("key_contradictions", [])
            detected = data.get("contradictions", [])
            covered = 0
            for kc in key_contradictions:
                for d in detected:
                    if kc["claim"].lower() in d.get("claim", "").lower():
                        covered += 1
                        break
            contra_score = covered / len(key_contradictions) if key_contradictions else 1.0
            checks.append({
                "id": "contradiction_detection",
                "label": f"矛盾点检出率 {covered}/{len(key_contradictions)}",
                "pass": bool(contra_score >= 0.6),
                "weight": 0.35,
                "detail": {"coverage": round(float(contra_score), 4)}
            })
        except Exception as e:
            checks.append({"id": "contra_error", "label": str(e), "pass": False, "weight": 0.35, "detail": None})
    else:
        checks.append({"id": "contra_missing", "label": "contradictions.json missing", "pass": False, "weight": 0.35, "detail": None})

    # ---------- 3. 最终报告质量 (40%) ----------
    report_file = out_dir / "final_report.md"
    if report_file.exists():
        content = report_file.read_text(encoding="utf-8")
        required = gt.get("required_elements_in_report", [])
        found = sum(1 for elem in required if elem.lower() in content.lower())
        report_score = found / len(required) if required else 1.0
        # 长度惩罚：少于800字降分
        if len(content) < 800:
            report_score *= 0.7
        checks.append({
            "id": "report_quality",
            "label": f"报告元素覆盖 {found}/{len(required)}",
            "pass": bool(report_score >= 0.7),
            "weight": 0.40,
            "detail": {"coverage": round(float(report_score), 4), "length": len(content)}
        })
    else:
        checks.append({"id": "report_missing", "label": "final_report.md missing", "pass": False, "weight": 0.40, "detail": None})

    total_score = trust_score * 0.25 + contra_score * 0.35 + report_score * 0.40
    thresholds = gt.get("scoring", {}).get("thresholds", {"excellent": 0.90, "good": 0.75, "pass": 0.60})
    if total_score >= thresholds["excellent"]:
        level = "excellent"
    elif total_score >= thresholds["good"]:
        level = "good"
    elif total_score >= thresholds["pass"]:
        level = "pass"
    else:
        level = "fail"

    result = {
        "task": "13-doc-synthesis",
        "workspace": str(w),
        "outcome_score": round(float(total_score), 4),
        "level": level,
        "checks": _to_native(checks),
        "summary": {
            "trust_accuracy": round(float(trust_score), 4),
            "contradiction_coverage": round(float(contra_score), 4),
            "report_coverage": round(float(report_score), 4)
        }
    }
    # 最终保险序列化
    return json.loads(json.dumps(result, default=str))


# 安全版本（捕获所有异常）
def score_workspace_safe(workspace: Path) -> dict[str, Any]:
    try:
        return score_workspace(workspace)
    except Exception as e:
        return {
            "task": "13-doc-synthesis",
            "workspace": str(workspace),
            "outcome_score": 0.0,
            "level": "error",
            "error": str(e),
            "checks": [],
            "summary": {}
        }