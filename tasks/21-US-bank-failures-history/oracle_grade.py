"""Oracle评分：检验美国银行倒闭潮历史图表分析任务的输出质量"""
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
        "vision_chart_reading": 0.40,
        "historical_retrieval": 0.40,
        "format_compliance": 0.20
    })

    validation_constraints = ground_truth.get("validation_constraints", {})
    expected_crisis_count = validation_constraints.get("expected_crisis_count", 3)
    wave_1_keywords = validation_constraints.get("wave_1_must_contain_keywords", [])
    wave_2_keywords = validation_constraints.get("wave_2_must_contain_keywords", [])
    wave_3_keywords = validation_constraints.get("wave_3_must_contain_keywords", [])
    background_min_length = validation_constraints.get("background_min_length", 50)

    checks: list[dict[str, Any]] = []

    # ========== 1. 图表视觉读取准确性 (40%) ==========
    vision_score = 0.0
    result_json_path = w / "result.json"

    if result_json_path.exists():
        try:
            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))

            # 检查crises数组
            crises = result_data.get("crises", [])
            crisis_count_match = len(crises) == expected_crisis_count

            checks.append({
                "id": "crisis_count_accuracy",
                "label": f"危机波数准确性: {len(crises)} vs {expected_crisis_count}",
                "pass": crisis_count_match,
                "weight": 0.40,
                "detail": f"数组长度: {len(crises)}"
            })
            if crisis_count_match:
                vision_score += 0.40

        except json.JSONDecodeError as e:
            checks.append({
                "id": "result_json_parse_error",
                "label": "result.json解析错误",
                "pass": False,
                "weight": 0.40,
                "detail": str(e)
            })
    else:
        checks.append({
            "id": "result_json_missing",
            "label": "缺少result.json文件",
            "pass": False,
            "weight": 0.40,
            "detail": "必需的输出文件不存在"
        })

    # ========== 2. 历史背景检索准确性 (40%) ==========
    historical_score = 0.0

    if result_json_path.exists():
        try:
            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))
            crises = result_data.get("crises", [])

            if len(crises) == 3:
                # 检查每个危机的背景
                wave_scores = []
                for i, crisis in enumerate(crises):
                    background = crisis.get("background", "")
                    time_period = crisis.get("time_period", "")

                    # 检查背景长度
                    length_ok = len(background) >= background_min_length

                    # 检查关键词
                    keywords = [wave_1_keywords, wave_2_keywords, wave_3_keywords][i]
                    keyword_match = any(kw in background for kw in keywords)

                    wave_score = (length_ok + keyword_match) / 2.0
                    wave_scores.append(wave_score)

                    checks.append({
                        "id": f"wave_{i+1}_background_quality",
                        "label": f"第{i+1}波背景质量: 长度{len(background)}, 关键词匹配{keyword_match}",
                        "pass": wave_score >= 0.5,
                        "weight": 0.40 / 3,
                        "detail": f"长度>=50: {length_ok}, 关键词: {keyword_match}"
                    })

                historical_score = sum(wave_scores) / 3.0 * 0.40

        except:
            pass

    # ========== 3. 格式合规性 (20%) ==========
    format_score = 0.0
    if result_json_path.exists():
        try:
            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))

            # 检查是否只有crises字段
            has_only_crises = set(result_data.keys()) == {"crises"}

            # 检查crises是数组且每个元素有time_period和background
            crises_valid = True
            if "crises" in result_data and isinstance(result_data["crises"], list):
                for crisis in result_data["crises"]:
                    if not isinstance(crisis, dict) or "time_period" not in crisis or "background" not in crisis:
                        crises_valid = False
                        break
            else:
                crises_valid = False

            checks.append({
                "id": "format_compliance",
                "label": "格式合规性: 结构正确",
                "pass": has_only_crises and crises_valid,
                "weight": 0.20,
                "detail": f"仅crises字段: {has_only_crises}, 结构正确: {crises_valid}"
            })
            if has_only_crises and crises_valid:
                format_score += 0.20

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
        vision_score * weights.get("vision_chart_reading", 0.40) +
        historical_score * weights.get("historical_retrieval", 0.40) +
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
            "task_id": "21-us-bank-failures-history",
            "expected_crisis_count": expected_crisis_count,
            "wave_1_keywords": wave_1_keywords,
            "wave_2_keywords": wave_2_keywords,
            "wave_3_keywords": wave_3_keywords,
        }
    }
