"""Oracle评分：检验跨表数据关联与库存预警分析任务的输出质量"""
from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path
from typing import Any


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _python_syntax_ok(path: Path) -> bool:
    result = subprocess.run(["python3", "-m", "py_compile", str(path)], capture_output=True, text=True)
    return result.returncode == 0


def _analysis_script_ok(path: Path) -> bool:
    try:
        text = _read_text(path)
        has_csv = "read_csv" in text or "csv." in text
        has_json = "json.load" in text or "json." in text
        has_merge = "merge" in text or "pd.merge" in text
        has_days = "Days_Remaining" in text or "Days_Remaining" in text
        return has_csv and has_json and has_merge and has_days
    except Exception:
        return False


def _compute_expected_alerts(task_dir: Path) -> dict[str, float]:
    trends_path = task_dir / "fixtures" / "data" / "consumer_trends.csv"
    stock_path = task_dir / "fixtures" / "data" / "warehouse_stock.json"

    trends = []
    with trends_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trends.append({
                "Product_ID": row["Product_ID"],
                "Search_Volume": float(row["Search_Volume"]),
                "Daily_Sales_Velocity": float(row["Daily_Sales_Velocity"]),
            })

    with stock_path.open("r", encoding="utf-8") as f:
        stock = {item["Product_ID"]: float(item["Current_Stock"]) for item in json.load(f)}

    alerts: dict[str, float] = {}
    for row in trends:
        pid = row["Product_ID"]
        if pid not in stock:
            continue
        days = stock[pid] / row["Daily_Sales_Velocity"] if row["Daily_Sales_Velocity"] else float("inf")
        if row["Search_Volume"] > 5000 and days < 7:
            alerts[pid] = round(days, 1)
    return alerts


def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    task_dir = Path(__file__).resolve().parent
    gt_path = task_dir / "ground_truth.json"
    ground_truth = json.loads(gt_path.read_text(encoding="utf-8")) if gt_path.exists() else {}

    weights = ground_truth.get("scoring", {}).get("weights", {
        "data_join_accuracy": 0.30,
        "logic_calculation": 0.40,
        "output_formatting": 0.30,
    })

    checks: list[dict[str, Any]] = []
    expected_alerts = _compute_expected_alerts(task_dir)

    # ========== 1. 跨表关联与计算准确性 (30%) ==========
    data_score = 0.0
    analysis_path = w / "analysis.py"
    analysis_exists = analysis_path.exists()
    analysis_syntax = _python_syntax_ok(analysis_path) if analysis_exists else False
    analysis_quality = _analysis_script_ok(analysis_path) if analysis_exists else False

    checks.append({
        "id": "analysis_exists",
        "label": "analysis.py 是否存在",
        "pass": analysis_exists,
        "weight": 0.10,
        "detail": f"存在: {analysis_exists}"
    })
    if analysis_exists and analysis_syntax and analysis_quality:
        data_score += 0.20
    checks.append({
        "id": "analysis_quality",
        "label": "analysis.py 逻辑检查",
        "pass": analysis_exists and analysis_quality,
        "weight": 0.20,
        "detail": f"语法: {analysis_syntax}, 内容: {analysis_quality}"
    })
    if analysis_quality:
        data_score += 0.10

    # ========== 2. 逻辑计算与过滤准确性 (40%) ==========
    logic_score = 0.0
    alert_path = w / "alert.txt"
    alert_lines = []
    if alert_path.exists():
        alert_lines = [line.strip() for line in _read_text(alert_path).splitlines() if line.strip()]
    has_expected = True
    for pid, value in expected_alerts.items():
        line = f"{pid}: {value:.1f}"
        if line not in alert_lines:
            has_expected = False
    extra_lines = [line for line in alert_lines if line not in {f"{pid}: {value:.1f}" for pid, value in expected_alerts.items()}]

    checks.append({
        "id": "alert_correct_targets",
        "label": "alert.txt 是否包含正确预警目标",
        "pass": has_expected and not extra_lines,
        "weight": 0.25,
        "detail": f"匹配目标: {has_expected}, 额外行: {len(extra_lines)}"
    })
    if has_expected and not extra_lines:
        logic_score += 0.25

    # 计算可用天数是否正确
    expected_values = {pid: value for pid, value in expected_alerts.items()}
    actual_values = {}
    for line in alert_lines:
        if ":" in line:
            pid, value = [segment.strip() for segment in line.split(":", 1)]
            try:
                actual_values[pid] = float(value)
            except ValueError:
                actual_values[pid] = None
    values_ok = all(pid in actual_values and actual_values[pid] == expected_values[pid] for pid in expected_values)
    checks.append({
        "id": "days_remaining_calculation",
        "label": "alert.txt 可用天数计算是否正确",
        "pass": values_ok,
        "weight": 0.15,
        "detail": f"计算正确: {values_ok}"
    })
    if values_ok:
        logic_score += 0.15

    # ========== 3. 输出格式与过程记录 (30%) ==========
    format_score = 0.0
    progress_path = w / "progress.md"
    progress_ok = False
    if progress_path.exists():
        progress_text = _read_text(progress_path)
        progress_ok = "consumer_trends" in progress_text or "warehouse_stock" in progress_text
        progress_ok = progress_ok and "Days_Remaining" in progress_text
    checks.append({
        "id": "progress_exists",
        "label": "progress.md 是否存在并记录分析过程",
        "pass": progress_path.exists(),
        "weight": 0.10,
        "detail": f"存在: {progress_path.exists()}"
    })
    if progress_path.exists() and progress_ok:
        format_score += 0.10

    alert_format_ok = alert_path.exists() and all(
        line.count(":") == 1 and line.split(":", 1)[0].startswith("P-") and line.split(":", 1)[1].strip().replace('.', '', 1).isdigit()
        for line in alert_lines
    ) if alert_path.exists() else False
    checks.append({
        "id": "alert_format",
        "label": "alert.txt 输出格式是否严格符合要求",
        "pass": alert_format_ok,
        "weight": 0.10,
        "detail": f"格式有效: {alert_format_ok}"
    })
    if alert_format_ok:
        format_score += 0.10

    analysis_present = analysis_exists and analysis_syntax
    checks.append({
        "id": "analysis_script_presence",
        "label": "analysis.py 是否存在且语法正确",
        "pass": analysis_present,
        "weight": 0.10,
        "detail": f"存在: {analysis_exists}, 语法: {analysis_syntax}"
    })
    if analysis_present:
        format_score += 0.10

    total_score = (
        data_score * weights.get("data_join_accuracy", 0.30) +
        logic_score * weights.get("logic_calculation", 0.40) +
        format_score * weights.get("output_formatting", 0.30)
    )

    thresholds = ground_truth.get("scoring", {}).get("thresholds", {
        "excellent": 0.90,
        "good": 0.75,
        "pass": 0.60,
    })
    if total_score >= thresholds["excellent"]:
        grade = "excellent"
    elif total_score >= thresholds["good"]:
        grade = "good"
    elif total_score >= thresholds["pass"]:
        grade = "pass"
    else:
        grade = "fail"

    return {
        "grade": grade,
        "score": round(total_score, 3),
        "max_score": 1.0,
        "checks": checks,
        "weights": weights,
        "thresholds": thresholds,
        "metadata": {
            "task_id": "23-supply-chain-alert",
            "expected_alerts": expected_alerts,
        },
    }