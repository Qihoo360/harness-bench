from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

def _to_native(obj):
    if hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(v) for v in obj]
    return obj

def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    out_dir = w / "out"
    task_dir = w.parent.parent
    gt_path = task_dir / "ground_truth.json"
    gt = json.loads(gt_path.read_text(encoding="utf-8")) if gt_path.exists() else {}

    checks = []

    # 1. 检查 Agent 是否修改了 HEARTBEAT.md（自主配置心跳任务）
    heartbeat_file = w / "HEARTBEAT.md"
    heartbeat_modified = False
    if heartbeat_file.exists():
        content = heartbeat_file.read_text(encoding="utf-8")
        # 检查内容中是否包含检查 in/ 目录和写通知的逻辑
        if "in/" in content and "notifications.txt" in content and ("priority" in content or "high" in content):
            heartbeat_modified = True
    checks.append({
        "id": "heartbeat_configured",
        "label": "Agent 自主修改 HEARTBEAT.md 配置了邮箱检查任务",
        "pass": heartbeat_modified,
        "weight": 0.3,
        "detail": {"modified": heartbeat_modified}
    })

    # 2. 检查通知文件是否存在且包含紧急邮件通知
    notification_file = out_dir / "notifications.txt"
    notification_exists = notification_file.exists() and notification_file.stat().st_size > 0
    content_match = False
    expected_text = "[紧急] 来自 boss@company.com 的邮件：紧急：客户数据泄露，请及时处理。"
    if notification_exists:
        content = notification_file.read_text(encoding="utf-8")
        if expected_text in content:
            content_match = True
    checks.append({
        "id": "notification",
        "label": "紧急邮件通知已生成",
        "pass": notification_exists and content_match,
        "weight": 0.5,
        "detail": {"exists": notification_exists, "content_match": content_match}
    })

    # 3. 检查任务运行时长（通过日志或文件修改时间粗略判断）
    # 这里简单检查 notifications.txt 的修改时间是否在任务开始后 20~60 秒之间
    time_score = 0.0
    if notification_exists:
        mtime = notification_file.stat().st_mtime
        # 获取任务开始时间（通过 workspace 目录的创建时间近似）
        start_time = w.stat().st_ctime
        elapsed = mtime - start_time
        if 15 < elapsed < 70:
            time_score = 1.0
        elif 10 < elapsed < 80:
            time_score = 0.5
    checks.append({
        "id": "timing",
        "label": "通知在合理时间范围内生成（心跳触发）",
        "pass": time_score >= 0.5,
        "weight": 0.2,
        "detail": {"time_score": time_score}
    })

    total_score = (0.3 if heartbeat_modified else 0.0) + (0.5 if notification_exists and content_match else 0.0) + (0.2 * time_score)
    level = "excellent" if total_score >= 0.9 else "good" if total_score >= 0.7 else "pass" if total_score >= 0.5 else "fail"

    result = {
        "task": "16-heartbeat-test",
        "workspace": str(w),
        "outcome_score": round(total_score, 4),
        "level": level,
        "checks": _to_native(checks),
        "summary": {
            "heartbeat_configured": heartbeat_modified,
            "notification_created": notification_exists and content_match,
            "timing_ok": time_score >= 0.5
        }
    }
    return json.loads(json.dumps(result, default=str))

def score_workspace_safe(workspace: Path) -> dict[str, Any]:
    try:
        return score_workspace(workspace)
    except Exception as e:
        return {
            "task": "16-heartbeat-test",
            "workspace": str(workspace),
            "outcome_score": 0.0,
            "level": "error",
            "error": str(e),
            "checks": [],
            "summary": {}
        }