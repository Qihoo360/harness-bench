"""Oracle评分：检验地标建筑识别与介绍任务的输出质量"""
from __future__ import annotations

import json
import re
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
        "vision_recognition": 0.40,
        "knowledge_retrieval": 0.30,
        "content_generation": 0.20,
        "format_compliance": 0.10
    })

    exact_match = ground_truth.get("validation_constraints", {}).get("exact_match_target", {})
    expected_building_name_en = exact_match.get("building_name_en", "Royal Albert Hall")

    checks: list[dict[str, Any]] = []

    # ========== 1. 视觉识别准确性 (40%) ==========
    vision_score = 0.0
    result_json_path = w / "result.json"

    if result_json_path.exists():
        try:
            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))

            # 检查building_name_en字段
            building_name_match = False
            if "building_name_en" in result_data:
                building_value = str(result_data["building_name_en"]).strip()
                building_name_match = building_value == expected_building_name_en

                checks.append({
                    "id": "building_name_accuracy",
                    "label": f"building_name_en字段准确性: '{building_value}' vs '{expected_building_name_en}'",
                    "pass": building_name_match,
                    "weight": 0.40,
                    "detail": f"匹配: {building_name_match}"
                })
                if building_name_match:
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

    # ========== 2. 知识检索与进度追踪 (30%) ==========
    knowledge_score = 0.0
    progress_md_path = w / "progress.md"

    if progress_md_path.exists():
        try:
            progress_content = progress_md_path.read_text(encoding="utf-8")
            # 检查是否记录了三个子任务
            has_image_analysis = "图像特征提取" in progress_content or "视觉特征" in progress_content
            has_knowledge_retrieval = "知识检索" in progress_content or "背景知识" in progress_content
            has_content_generation = "文本生成" in progress_content or "介绍生成" in progress_content

            progress_complete = has_image_analysis and has_knowledge_retrieval and has_content_generation

            checks.append({
                "id": "progress_tracking",
                "label": "进度追踪完整性",
                "pass": progress_complete,
                "weight": 0.30,
                "detail": f"图像分析: {has_image_analysis}, 知识检索: {has_knowledge_retrieval}, 内容生成: {has_content_generation}"
            })
            if progress_complete:
                knowledge_score += 0.30

        except UnicodeDecodeError as e:
            checks.append({
                "id": "progress_md_read_error",
                "label": "progress.md读取错误",
                "pass": False,
                "weight": 0.30,
                "detail": str(e)
            })
    else:
        checks.append({
            "id": "progress_md_missing",
            "label": "缺少progress.md文件",
            "pass": False,
            "weight": 0.30,
            "detail": "必需的进度追踪文件不存在"
        })

    # ========== 3. 内容生成质量 (20%) ==========
    content_score = 0.0
    if result_json_path.exists():
        try:
            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))
            if "introduction" in result_data:
                intro_text = str(result_data["introduction"]).strip()
                # 检查长度
                min_length = len(intro_text) >= 50
                # 检查是否包含中文
                has_chinese = bool(re.search(r'[\u4e00-\u9fff]', intro_text))

                content_valid = min_length and has_chinese

                checks.append({
                    "id": "introduction_quality",
                    "label": f"介绍内容质量: 长度{len(intro_text)}, 中文: {has_chinese}",
                    "pass": content_valid,
                    "weight": 0.20,
                    "detail": f"长度>=50: {min_length}, 包含中文: {has_chinese}"
                })
                if content_valid:
                    content_score += 0.20
        except:
            pass

    # ========== 4. 格式合规性 (10%) ==========
    format_score = 0.0
    if result_json_path.exists():
        try:
            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))
            # 检查是否只有两个必需字段
            has_only_required = set(result_data.keys()) == {"building_name_en", "introduction"}
            checks.append({
                "id": "format_compliance",
                "label": "格式合规性: 仅包含必需字段",
                "pass": has_only_required,
                "weight": 0.10,
                "detail": f"字段: {list(result_data.keys())}"
            })
            if has_only_required:
                format_score += 0.10
        except:
            checks.append({
                "id": "json_format_error",
                "label": "JSON格式错误",
                "pass": False,
                "weight": 0.10,
                "detail": "无法解析JSON"
            })

    # 计算总分
    total_score = (
        vision_score * weights.get("vision_recognition", 0.40) +
        knowledge_score * weights.get("knowledge_retrieval", 0.30) +
        content_score * weights.get("content_generation", 0.20) +
        format_score * weights.get("format_compliance", 0.10)
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
            "task_id": "19-landmark-recognition",
            "expected_building_name_en": expected_building_name_en,
        }
    }
