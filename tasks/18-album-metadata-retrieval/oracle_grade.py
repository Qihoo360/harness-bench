"""Oracle评分：检验专辑封面元数据提取与检索任务的输出质量"""
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
    out_dir = w / "out"

    # 加载 ground truth
    task_dir = w.parent.parent
    gt_path = task_dir / "ground_truth.json"
    ground_truth = json.loads(gt_path.read_text(encoding="utf-8")) if gt_path.exists() else {}

    required_files = ground_truth.get("required_files", [])
    weights = ground_truth.get("scoring", {}).get("weights", {
        "vision_recognition": 0.40,
        "knowledge_retrieval": 0.40,
        "execution_logic": 0.10,
        "format_compliance": 0.10
    })

    exact_match = ground_truth.get("validation_constraints", {}).get("exact_match_target", {})
    expected_singer = exact_match.get("singer", "梁静茹")
    expected_year = exact_match.get("year", 2009)

    checks: list[dict[str, Any]] = []

    # ========== 1. 视觉识别准确性 (40%) ==========
    vision_score = 0.0
    result_json_path = w / "result.json"

    if result_json_path.exists():
        try:
            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))

            # 检查singer字段
            singer_match = False
            if "singer" in result_data:
                singer_value = str(result_data["singer"]).strip()
                singer_match = singer_value == expected_singer
                # 检查是否为纯中文
                is_chinese_only = bool(re.match(r'^[\u4e00-\u9fff]+$', singer_value))

                checks.append({
                    "id": "singer_accuracy",
                    "label": f"singer字段准确性: '{singer_value}' vs '{expected_singer}'",
                    "pass": singer_match,
                    "weight": 0.20,
                    "detail": f"匹配: {singer_match}, 纯中文: {is_chinese_only}"
                })
                if singer_match:
                    vision_score += 0.20

            # 检查year字段
            year_match = False
            if "year" in result_data:
                try:
                    year_value = int(result_data["year"])
                    year_match = year_value == expected_year
                    # 检查是否为4位整数
                    is_four_digit = 1000 <= year_value <= 9999

                    checks.append({
                        "id": "year_accuracy",
                        "label": f"year字段准确性: {year_value} vs {expected_year}",
                        "pass": year_match,
                        "weight": 0.20,
                        "detail": f"匹配: {year_match}, 4位数: {is_four_digit}"
                    })
                    if year_match:
                        vision_score += 0.20

                except (ValueError, TypeError):
                    checks.append({
                        "id": "year_format_error",
                        "label": "year字段格式错误",
                        "pass": False,
                        "weight": 0.20,
                        "detail": f"无法转换为整数: {result_data.get('year')}"
                    })

            # 检查是否只有两个必需字段
            has_only_required = set(result_data.keys()) == {"singer", "year"}
            checks.append({
                "id": "json_structure",
                "label": "JSON结构完整性",
                "pass": has_only_required,
                "weight": 0.00,  # 不计入视觉识别分数
                "detail": f"字段: {list(result_data.keys())}"
            })

        except json.JSONDecodeError as e:
            checks.append({
                "id": "json_parse_error",
                "label": "result.json解析失败",
                "pass": False,
                "weight": 0.40,
                "detail": str(e)
            })
    else:
        checks.append({
            "id": "result_json_missing",
            "label": "result.json文件缺失",
            "pass": False,
            "weight": 0.40,
            "detail": "文件不存在"
        })

    # ========== 2. 知识检索准确性 (40%) ==========
    knowledge_score = 0.0

    # 检查progress.md中是否记录了检索过程
    progress_path = w / "progress.md"
    if progress_path.exists():
        progress_content = progress_path.read_text(encoding="utf-8")

        # 检查是否记录了OCR识别过程
        has_ocr = any(kw in progress_content for kw in ["OCR", "识别", "文字", "文本", "提取"])
        has_inference = any(kw in progress_content for kw in ["推理", "推断", "映射", "转换"])
        has_retrieval = any(kw in progress_content for kw in ["检索", "搜索", "查询", "年份", "发行"])

        # 检查是否记录了具体的推理逻辑
        has_singer_logic = any(kw in progress_content for kw in ["梁静茹", "singer", "歌手"])
        has_year_logic = any(kw in progress_content for kw in ["2009", "year", "年份"])

        retrieval_checks = [
            ("ocr_recording", has_ocr, "记录了OCR识别过程"),
            ("inference_recording", has_inference, "记录了推理过程"),
            ("retrieval_recording", has_retrieval, "记录了检索过程"),
            ("singer_evidence", has_singer_logic, "记录了歌手推理依据"),
            ("year_evidence", has_year_logic, "记录了年份检索依据"),
        ]

        for check_id, passed, desc in retrieval_checks:
            weight = 0.40 / len(retrieval_checks)
            checks.append({
                "id": f"retrieval_{check_id}",
                "label": desc,
                "pass": bool(passed),
                "weight": weight,
                "detail": None
            })
            if passed:
                knowledge_score += weight
    else:
        checks.append({
            "id": "progress_missing",
            "label": "progress.md文件缺失",
            "pass": False,
            "weight": 0.40,
            "detail": "文件不存在"
        })

    # ========== 3. 执行逻辑合理性 (10%) ==========
    execution_score = 0.0

    if progress_path.exists() and result_json_path.exists():
        progress_content = progress_path.read_text(encoding="utf-8")

        # 检查执行顺序是否合理（OCR -> 推理 -> 检索）
        has_logical_flow = False
        if has_ocr and has_inference and has_retrieval:
            # 检查是否按顺序出现
            ocr_pos = progress_content.find("OCR") if "OCR" in progress_content else -1
            inference_pos = progress_content.find("推理") if "推理" in progress_content else -1
            retrieval_pos = progress_content.find("检索") if "检索" in progress_content else -1

            if ocr_pos >= 0 and inference_pos >= 0 and retrieval_pos >= 0:
                has_logical_flow = ocr_pos < inference_pos < retrieval_pos

        checks.append({
            "id": "execution_flow",
            "label": "执行流程逻辑性",
            "pass": bool(has_logical_flow),
            "weight": 0.10,
            "detail": "OCR → 推理 → 检索的顺序"
        })

        if has_logical_flow:
            execution_score = 0.10

    # ========== 4. 格式合规性 (10%) ==========
    format_score = 0.0

    if result_json_path.exists():
        try:
            result_data = json.loads(result_json_path.read_text(encoding="utf-8"))

            # 检查singer字段格式
            singer_format_ok = False
            if "singer" in result_data:
                singer_val = str(result_data["singer"])
                # 必须是纯中文汉字
                singer_format_ok = bool(re.match(r'^[\u4e00-\u9fff]+$', singer_val))

            # 检查year字段格式
            year_format_ok = False
            if "year" in result_data:
                try:
                    year_val = int(result_data["year"])
                    # 必须是4位整数
                    year_format_ok = 1000 <= year_val <= 9999
                except (ValueError, TypeError):
                    pass

            format_checks = [
                ("singer_format", singer_format_ok, "singer为纯中文汉字"),
                ("year_format", year_format_ok, "year为4位整数"),
            ]

            for check_id, passed, desc in format_checks:
                weight = 0.10 / len(format_checks)
                checks.append({
                    "id": f"format_{check_id}",
                    "label": desc,
                    "pass": bool(passed),
                    "weight": weight,
                    "detail": None
                })
                if passed:
                    format_score += weight

        except json.JSONDecodeError:
            pass  # 已经在前面检查过了

    # ========== 总分 ==========
    total_score = float(
        vision_score * weights.get("vision_recognition", 0.40) / 0.40 +
        knowledge_score * weights.get("knowledge_retrieval", 0.40) / 0.40 +
        execution_score * weights.get("execution_logic", 0.10) / 0.10 +
        format_score * weights.get("format_compliance", 0.10) / 0.10
    )

    thresholds = ground_truth.get("scoring", {}).get("thresholds", {
        "excellent": 0.90,
        "good": 0.75,
        "pass": 0.60
    })

    if total_score >= thresholds.get("excellent", 0.90):
        level = "excellent"
    elif total_score >= thresholds.get("good", 0.75):
        level = "good"
    elif total_score >= thresholds.get("pass", 0.60):
        level = "pass"
    else:
        level = "fail"

    return {
        "task": "18-album-metadata-retrieval",
        "workspace": str(w),
        "checks": checks,
        "outcome_score": round(float(total_score), 4),
        "level": level,
        "summary": {
            "vision_recognition_score": float(vision_score),
            "knowledge_retrieval_score": float(knowledge_score),
            "execution_logic_score": float(execution_score),
            "format_compliance_score": float(format_score),
            "expected_singer": expected_singer,
            "expected_year": expected_year,
        }
    }
