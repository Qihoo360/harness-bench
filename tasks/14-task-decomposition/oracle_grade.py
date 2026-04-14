from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _to_native(obj: Any) -> Any:
    if hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(v) for v in obj]
    return obj


def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    task_dir = w.parent.parent
    gt_path = task_dir / "ground_truth.json"
    gt = json.loads(gt_path.read_text(encoding="utf-8")) if gt_path.exists() else {}

    subtasks_dir = w / "subtasks"
    progress_file = w / "progress.md"
    report_file = w / "final_report.md"

    checks = []

    # ---------- 1. 分解合理性 (25%) ----------
    decomposition_score = 0.0
    if subtasks_dir.exists() and subtasks_dir.is_dir():
        subtask_files = list(subtasks_dir.glob("*.md"))
        num_subtasks = len(subtask_files)
        min_subtasks = gt.get("min_subtasks", 3)
        if num_subtasks >= min_subtasks:
            decomposition_score = min(1.0, num_subtasks / (min_subtasks + 2))
        else:
            decomposition_score = num_subtasks / min_subtasks

        # 检查是否涵盖预期主题
        expected_topics = gt.get("expected_subtask_topics", [])
        all_content = " ".join([f.read_text(encoding="utf-8") for f in subtask_files if f.exists()])
        found_topics = sum(1 for topic in expected_topics if topic.lower() in all_content.lower())
        if expected_topics:
            decomposition_score = (decomposition_score + found_topics / len(expected_topics)) / 2

        checks.append({
            "id": "decomposition",
            "label": f"子任务数量: {num_subtasks}, 主题覆盖: {found_topics}/{len(expected_topics)}",
            "pass": bool(decomposition_score >= 0.7),
            "weight": 0.25,
            "detail": {"num": num_subtasks, "topics_covered": found_topics, "total_topics": len(expected_topics)}
        })
    else:
        checks.append({"id": "decomposition_missing", "label": "subtasks/ directory missing", "pass": False, "weight": 0.25, "detail": None})
        decomposition_score = 0.0

    # ---------- 2. 执行完整性 (40%) ----------
    execution_score = 0.0
    if subtasks_dir.exists():
        done_count = 0
        total_count = 0
        for f in subtasks_dir.glob("*.md"):
            total_count += 1
            content = f.read_text(encoding="utf-8")
            if re.search(r"STATUS:\s*done", content, re.IGNORECASE):
                done_count += 1
        if total_count > 0:
            execution_score = done_count / total_count
        checks.append({
            "id": "execution",
            "label": f"子任务完成: {done_count}/{total_count}",
            "pass": bool(execution_score >= 0.8),
            "weight": 0.40,
            "detail": {"done": done_count, "total": total_count}
        })
    else:
        execution_score = 0.0

    # ---------- 3. 最终报告质量 (25%) ----------
    report_score = 0.0
    if report_file.exists():
        content = report_file.read_text(encoding="utf-8")
        # 长度分（至少 500 字）
        length_score = min(1.0, len(content) / 800)
        # 关键词检查
        has_budget = "budget" in content.lower() or "预算" in content
        has_timeline = "timeline" in content.lower() or "时间线" in content
        has_copy = "copy" in content.lower() or "文案" in content
        has_metrics = "metric" in content.lower() or "指标" in content or "kpi" in content.lower()
        element_score = (has_budget + has_timeline + has_copy + has_metrics) / 4.0
        report_score = (length_score + element_score) / 2.0
        checks.append({
            "id": "report_quality",
            "label": f"报告长度 {len(content)} 字符, 包含预算:{has_budget}, 时间线:{has_timeline}, 文案:{has_copy}, 指标:{has_metrics}",
            "pass": bool(report_score >= 0.7),
            "weight": 0.25,
            "detail": {"length": len(content), "has_budget": has_budget, "has_timeline": has_timeline, "has_copy": has_copy, "has_metrics": has_metrics}
        })
    else:
        checks.append({"id": "report_missing", "label": "final_report.md missing", "pass": False, "weight": 0.25, "detail": None})

    # ---------- 4. 过程监控 (10%) ----------
    progress_score = 0.0
    if progress_file.exists():
        content = progress_file.read_text(encoding="utf-8")
        # 检查是否包含状态变化标记
        has_pending = "pending" in content.lower()
        has_done = "done" in content.lower()
        has_start = "start" in content.lower() or "开始" in content
        has_end = "end" in content.lower() or "结束" in content
        if has_pending and has_done and has_start and has_end:
            progress_score = 1.0
        elif has_pending and has_done:
            progress_score = 0.7
        elif has_pending or has_done:
            progress_score = 0.4
        else:
            progress_score = 0.1
        checks.append({
            "id": "progress_tracking",
            "label": "progress.md 包含状态标记",
            "pass": bool(progress_score >= 0.5),
            "weight": 0.10,
            "detail": {"has_pending": has_pending, "has_done": has_done, "has_start": has_start, "has_end": has_end}
        })
    else:
        checks.append({"id": "progress_missing", "label": "progress.md missing", "pass": False, "weight": 0.10, "detail": None})

    total_score = (decomposition_score * 0.25 +
                   execution_score * 0.40 +
                   report_score * 0.25 +
                   progress_score * 0.10)

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
        "task": "15-task-decomposition",
        "workspace": str(w),
        "outcome_score": round(float(total_score), 4),
        "level": level,
        "checks": _to_native(checks),
        "summary": {
            "decomposition": round(float(decomposition_score), 4),
            "execution": round(float(execution_score), 4),
            "report_quality": round(float(report_score), 4),
            "progress_tracking": round(float(progress_score), 4)
        }
    }
    return json.loads(json.dumps(result, default=str))


def score_workspace_safe(workspace: Path) -> dict[str, Any]:
    try:
        return score_workspace(workspace)
    except Exception as e:
        return {
            "task": "15-task-decomposition",
            "workspace": str(workspace),
            "outcome_score": 0.0,
            "level": "error",
            "error": str(e),
            "checks": [],
            "summary": {}
        }