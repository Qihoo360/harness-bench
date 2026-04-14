"""Oracle评分：检验社交平台点赞推流分析任务的输出质量"""
from __future__ import annotations

import csv
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
        "decomposition": 0.25,
        "execution": 0.40,
        "report_quality": 0.25,
        "progress_tracking": 0.10
    })
    
    checks: list[dict[str, Any]] = []
    required_images = ground_truth.get("required_images", [])
    for image_name in required_images:
        image_path = task_dir / "fixtures" / "in" / image_name
        exists = image_path.is_file()
        checks.append({
            "id": f"fixture_image_{image_name}",
            "label": f"required fixture image {image_name} exists",
            "pass": exists,
            "weight": 0.0,
            "detail": None if exists else "missing fixture image"
        })
    
    # ========== 1. 执行完整性 (40%) ==========
    execution_score = 0.0
    missing_files = []
    
    for fname in required_files:
        fpath = out_dir / fname
        exists = fpath.is_file() and fpath.stat().st_size > 0
        
        if fname == "cleaned_data.csv":
            weight = 0.15
            label = f"{fname} exists and is valid CSV"
            pass_check = exists
            detail = None
            
            if exists:
                try:
                    with open(fpath, encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        rows = list(reader)
                        # 检查必要字段
                        if reader.fieldnames:
                            required_fields = {"user_id", "note_id", "author_id"}
                            has_required = required_fields.issubset(set(reader.fieldnames))
                            expected_rows = ground_truth.get("reference_results", {}).get("effective_records")
                            count_ok = True if expected_rows is None else len(rows) == int(expected_rows)
                            pass_check = pass_check and has_required and len(rows) > 0 and count_ok
                            if not has_required:
                                detail = f"missing fields, got: {list(reader.fieldnames)}"
                            elif expected_rows is not None and not count_ok:
                                detail = f"row count {len(rows)} != expected {expected_rows}"
                        else:
                            pass_check = False
                            detail = "empty CSV"
                except Exception as e:
                    pass_check = False
                    detail = f"CSV parse error: {str(e)}"
            else:
                detail = "file missing or empty"
                missing_files.append(fname)
        
        elif fname == "analysis_report.md":
            weight = 0.15
            label = f"{fname} exists with analysis content"
            pass_check = False
            detail = None
            
            if exists:
                content = fpath.read_text(encoding="utf-8")
                # 检查是否包含关键分析内容
                has_stats = "清洗" in content or "统计" in content or "数据" in content
                has_author_profile = "作者" in content or "author" in content.lower()
                has_category = "分区" in content or "category" in content.lower()
                has_peak = "高峰" in content or "peak" in content.lower() or "时段" in content
                has_top_author = any(kw in content for kw in ["110292", "第一名", "top 作者", "最受欢迎作者"])

                content_checks = [has_stats, has_author_profile, has_category, has_peak, has_top_author]
                if sum(content_checks) >= 3:
                    pass_check = True
                else:
                    detail = f"insufficient analysis (found {sum(content_checks)}/5 key topics)"

                if len(content) < 200:
                    pass_check = False
                    detail = f"report too short ({len(content)} chars, need >=200)"
            else:
                detail = "file missing"
                missing_files.append(fname)
        
        elif fname == "strategy_recommendation.txt":
            weight = 0.10
            label = f"{fname} exists and meets length requirement"
            pass_check = False
            detail = None
            
            if exists:
                content = fpath.read_text(encoding="utf-8")
                content_len = len(content)
                if content_len >= 100:
                    pass_check = True
                else:
                    detail = f"too short ({content_len} chars, need >=100)"
                    pass_check = False
            else:
                detail = "file missing"
                missing_files.append(fname)
        
        else:  # progress.md
            weight = 0.10
            label = f"{fname} exists"
            pass_check = exists
            detail = None if pass_check else "file missing"
            if exists:
                missing_files = [f for f in missing_files if f != fname]
        
        checks.append({
            "id": f"file_{fname.replace('.', '_')}",
            "label": label,
            "pass": pass_check,
            "weight": weight,
            "detail": detail
        })
        
        if pass_check:
            execution_score += weight
    
    # ========== 2. 数据清洗质量 (25%) ==========
    cleaning_score = 0.0
    csv_path = out_dir / "cleaned_data.csv"
    
    if csv_path.exists():
        try:
            import pandas as pd
            
            df = pd.read_csv(csv_path)
            has_nan = df.isna().any().any()
            
            # 检查必要字段
            required_cols = {"user_id", "note_id", "author_id"}
            has_required_cols = required_cols.issubset(set(df.columns))
            
            # 检查是否进行了去重（检查 (user_id, note_id) 的唯一性）
            if "user_id" in df.columns and "note_id" in df.columns:
                dup_count = int(df.duplicated(subset=["user_id", "note_id"]).sum())
                dedup_passed = dup_count == 0
            else:
                dup_count = 0
                dedup_passed = False
            
            # 检查ID字段是否为数字
            id_numeric = True
            for col in ["user_id", "author_id", "note_id"]:
                if col in df.columns:
                    try:
                        pd.to_numeric(df[col], errors="coerce")
                        non_numeric = df[col].isna().sum() > 0
                        if non_numeric:
                            id_numeric = False
                    except:
                        id_numeric = False
            
            # 检查action_time字段格式
            time_valid = True
            if "action_time" in df.columns:
                time_valid = pd.to_datetime(df["action_time"], errors="coerce").notna().all()
            
            cleaning_checks = [
                ("no_duplicates", dedup_passed, f"duplicates: {dup_count}"),
                ("required_columns", has_required_cols, None),
                ("id_fields_numeric", id_numeric, None),
                ("valid_time_format", time_valid, None),
            ]
            
            for check_id, passed, detail_msg in cleaning_checks:
                weight = 0.25 / len(cleaning_checks)
                checks.append({
                    "id": f"cleaning_{check_id}",
                    "label": check_id,
                    "pass": bool(passed),
                    "weight": weight,
                    "detail": detail_msg if not passed else None
                })
                if passed:
                    cleaning_score += weight
        
        except Exception as e:
            cleaning_score = 0.0
            checks.append({
                "id": "cleaning_error",
                "label": "CSV parsing/analysis failed",
                "pass": False,
                "weight": 0.25,
                "detail": str(e)
            })
    
    # ========== 3. 报告质量 (25%) ==========
    report_quality_score = 0.0
    report_path = out_dir / "analysis_report.md"
    
    if report_path.exists():
        try:
            content = report_path.read_text(encoding="utf-8")
            
            # 检查长度
            length_score = min(1.0, len(content) / 1500)  # 1500字符为优秀标准
            
            # 检查内容完整性
            has_cleaning_stats = any(kw in content for kw in ["清洗", "去重", "条", "条数"])
            has_author_profile = any(kw in content for kw in ["作者", "top", "最高", "获赞"])
            has_category_analysis = any(kw in content for kw in ["分区", "category", "优势"])
            has_peak_analysis = any(kw in content for kw in ["高峰", "时段", "时间分布", "peak"])
            has_top_author = any(kw in content for kw in ["110292", "第一名", "top 作者", "最受欢迎作者"])
            
            content_score = sum([has_cleaning_stats, has_author_profile, has_category_analysis, has_peak_analysis, has_top_author]) / 5.0
            
            report_quality_score = (length_score + content_score) / 2.0
            
            checks.append({
                "id": "report_quality",
                "label": f"analysis_report quality (length: {len(content)} chars)",
                "pass": bool(report_quality_score >= 0.7),
                "weight": 0.25,
                "detail": {
                    "length_score": float(length_score),
                    "content_completeness": float(content_score),
                    "topics_found": {
                        "cleaning_stats": has_cleaning_stats,
                        "author_profile": has_author_profile,
                        "category_analysis": has_category_analysis,
                        "peak_analysis": has_peak_analysis,
                        "top_author": has_top_author
                    }
                }
            })
        except Exception as e:
            report_quality_score = 0.0
            checks.append({
                "id": "report_error",
                "label": "report analysis failed",
                "pass": False,
                "weight": 0.25,
                "detail": str(e)
            })
    else:
        report_quality_score = 0.0
    
    # ========== 4. 进度追踪 (10%) ==========
    progress_score = 0.0
    progress_path = w / "progress.md"
    
    if progress_path.exists():
        content = progress_path.read_text(encoding="utf-8")
        
        # 检查是否包含进度标记
        has_ocr = any(kw in content for kw in ["OCR", "提取", "识别"])
        has_cleaning = any(kw in content for kw in ["清洗", "去重", "重复"])
        has_feature = any(kw in content for kw in ["特征", "流量", "分析"])
        has_strategy = any(kw in content for kw in ["策略", "推流", "推荐"])
        
        topic_coverage = sum([has_ocr, has_cleaning, has_feature, has_strategy]) / 4.0
        
        # 检查状态标记
        has_status_marks = re.search(r"(done|完成|✓|\[x\]|STATUS)", content, re.IGNORECASE) is not None
        
        # 检查时间戳或步骤记录
        has_steps = re.search(r"(步骤|step|###|##|开始|完成)", content, re.IGNORECASE) is not None
        
        has_timestamp = bool(re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}|\d{2}:\d{2}:\d{2}", content))
        has_process_flow = any(kw in content for kw in ["OCR", "Cleaning", "Strategy"]) or all(kw in content for kw in ["OCR", "清洗", "策略"])

        if topic_coverage >= 0.5 and (has_status_marks or has_steps) and has_timestamp and has_process_flow:
            progress_score = 1.0
        elif (topic_coverage >= 0.5 and has_timestamp) or has_process_flow:
            progress_score = 0.7
        elif topic_coverage >= 0.3:
            progress_score = 0.4
        else:
            progress_score = 0.2
        
        checks.append({
            "id": "progress_tracking",
            "label": "progress.md tracking quality",
            "pass": bool(progress_score >= 0.5),
            "weight": 0.10,
            "detail": {
                "topic_coverage": float(topic_coverage),
                "has_status_marks": has_status_marks,
                "has_step_records": has_steps,
                "has_timestamp": has_timestamp,
                "has_process_flow": has_process_flow
            }
        })
    else:
        progress_score = 0.0
        checks.append({
            "id": "progress_missing",
            "label": "progress.md missing",
            "pass": False,
            "weight": 0.10,
            "detail": "file not found"
        })
    
    # ========== 总分 ==========
    # 优先级：执行完整性 < 数据清洗 = 报告质量 < 进度追踪
    # 使用 ground_truth 中的权重
    total_score = float(
        execution_score * weights.get("execution", 0.40) +
        cleaning_score * weights.get("execution", 0.40) * 0.625 +  # 清洗作为执行的子项
        report_quality_score * weights.get("report_quality", 0.25) +
        progress_score * weights.get("progress_tracking", 0.10)
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
        "task": "17-like-record",
        "workspace": str(w),
        "checks": checks,
        "outcome_score": round(float(total_score), 4),
        "level": level,
        "summary": {
            "files_missing": missing_files,
            "execution_completeness": float(execution_score),
            "data_cleaning_quality": float(cleaning_score),
            "report_quality": float(report_quality_score),
            "progress_tracking": float(progress_score),
        }
    }
