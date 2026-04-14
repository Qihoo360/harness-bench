"""Oracle评分：检验足球射门数据统计任务的输出质量"""
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
    return obj


def score_workspace(workspace: Path) -> dict[str, Any]:
    """评分工作区产出"""
    w = workspace.resolve()

    # 加载 ground truth
    task_dir = w.parent.parent
    gt_path = task_dir / "ground_truth.json"
    ground_truth = json.loads(gt_path.read_text(encoding="utf-8")) if gt_path.exists() else {}

    weights = ground_truth.get("scoring", {}).get("weights", {
        "vision_counting": 0.50,
        "domain_reasoning": 0.30,
        "format_compliance": 0.20
    })

    exact_match = ground_truth.get("validation_constraints", {}).get("exact_match_target", {})
    expected_total_shots = exact_match.get("total_shots", 19)
    expected_shots_on_target = exact_match.get("shots_on_target", 6)
    expected_goals = exact_match.get("goals", 3)

    checks: list[dict[str, Any]] = []

    # ========== 1. 视觉计数准确性 (50%) ==========
    vision_score = 0.0
    result_json_path = w / "result.json"

    if result_json_path.exists():
        try:
            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))

            # 检查total_shots字段
            total_shots_match = False
            if "total_shots" in result_data:
                try:
                    total_shots_value = int(result_data["total_shots"])
                    total_shots_match = total_shots_value == expected_total_shots

                    checks.append({
                        "id": "total_shots_accuracy",
                        "label": f"total_shots字段准确性: {total_shots_value} vs {expected_total_shots}",
                        "pass": total_shots_match,
                        "weight": 0.20,
                        "detail": f"匹配: {total_shots_match}"
                    })
                    if total_shots_match:
                        vision_score += 0.20
                except (ValueError, TypeError):
                    checks.append({
                        "id": "total_shots_format_error",
                        "label": "total_shots字段格式错误",
                        "pass": False,
                        "weight": 0.20,
                        "detail": f"无法转换为整数: {result_data.get('total_shots')}"
                    })

            # 检查shots_on_target字段
            shots_on_target_match = False
            if "shots_on_target" in result_data:
                try:
                    shots_on_target_value = int(result_data["shots_on_target"])
                    shots_on_target_match = shots_on_target_value == expected_shots_on_target

                    checks.append({
                        "id": "shots_on_target_accuracy",
                        "label": f"shots_on_target字段准确性: {shots_on_target_value} vs {expected_shots_on_target}",
                        "pass": shots_on_target_match,
                        "weight": 0.15,
                        "detail": f"匹配: {shots_on_target_match}"
                    })
                    if shots_on_target_match:
                        vision_score += 0.15
                except (ValueError, TypeError):
                    checks.append({
                        "id": "shots_on_target_format_error",
                        "label": "shots_on_target字段格式错误",
                        "pass": False,
                        "weight": 0.15,
                        "detail": f"无法转换为整数: {result_data.get('shots_on_target')}"
                    })

            # 检查goals字段
            goals_match = False
            if "goals" in result_data:
                try:
                    goals_value = int(result_data["goals"])
                    goals_match = goals_value == expected_goals

                    checks.append({
                        "id": "goals_accuracy",
                        "label": f"goals字段准确性: {goals_value} vs {expected_goals}",
                        "pass": goals_match,
                        "weight": 0.15,
                        "detail": f"匹配: {goals_match}"
                    })
                    if goals_match:
                        vision_score += 0.15
                except (ValueError, TypeError):
                    checks.append({
                        "id": "goals_format_error",
                        "label": "goals字段格式错误",
                        "pass": False,
                        "weight": 0.15,
                        "detail": f"无法转换为整数: {result_data.get('goals')}"
                    })

        except json.JSONDecodeError as e:
            checks.append({
                "id": "result_json_parse_error",
                "label": "result.json解析错误",
                "pass": False,
                "weight": 0.50,
                "detail": str(e)
            })
    else:
        checks.append({
            "id": "result_json_missing",
            "label": "缺少result.json文件",
            "pass": False,
            "weight": 0.50,
            "detail": "必需的输出文件不存在"
        })

    # ========== 2. 领域推理合理性 (30%) ==========
    domain_score = 0.0

    # 检查逻辑一致性
    logic_valid = False
    if result_json_path.exists():
        try:
            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))
            total_shots = result_data.get("total_shots")
            shots_on_target = result_data.get("shots_on_target")
            goals = result_data.get("goals")

            if all(isinstance(x, int) for x in [total_shots, shots_on_target, goals]):
                logic_valid = total_shots >= shots_on_target >= goals >= 0

                checks.append({
                    "id": "logic_consistency",
                    "label": f"逻辑一致性: {total_shots} >= {shots_on_target} >= {goals}",
                    "pass": logic_valid,
                    "weight": 0.30,
                    "detail": f"逻辑检查: {logic_valid}"
                })
                if logic_valid:
                    domain_score += 0.30
        except:
            pass

    # ========== 3. 格式合规性 (20%) ==========
    format_score = 0.0
    if result_json_path.exists():
        try:
            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))
            # 检查是否只有三个必需字段
            has_only_required = set(result_data.keys()) == {"total_shots", "shots_on_target", "goals"}
            checks.append({
                "id": "format_compliance",
                "label": "格式合规性: 仅包含必需字段",
                "pass": has_only_required,
                "weight": 0.10,
                "detail": f"字段: {list(result_data.keys())}"
            })
            if has_only_required:
                format_score += 0.10

            # 检查所有值都是整数
            all_integers = all(isinstance(v, int) for v in result_data.values())
            checks.append({
                "id": "data_type_compliance",
                "label": "数据类型合规性: 所有值都是整数",
                "pass": all_integers,
                "weight": 0.10,
                "detail": f"类型检查: {all_integers}"
            })
            if all_integers:
                format_score += 0.10

        except:
            checks.append({
                "id": "json_format_error",
                "label": "JSON格式错误",
                "pass": False,
                "weight": 0.20,
                "detail": "无法解析JSON"
            })

    # 计算总分
    total_score = (
        vision_score * weights.get("vision_counting", 0.50) +
        domain_score * weights.get("domain_reasoning", 0.30) +
        format_score * weights.get("format_compliance", 0.20)
    )

    # 确定等级
    thresholds = ground_truth.get("scoring", {}).get("thresholds", {
        "excellent": 0.90,
        "good": 0.75,
        "pass": 0.60
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
            "task_id": "20-football-shot-map-analysis",
            "expected_total_shots": expected_total_shots,
            "expected_shots_on_target": expected_shots_on_target,
            "expected_goals": expected_goals,
        }
    }
