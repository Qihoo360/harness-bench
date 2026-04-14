from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    task_dir = w.parent.parent  # 上两级到task目录
    out_dir = w / "out"
    
    # 加载配置
    gt_path = task_dir / "ground_truth.json"
    gt = json.loads(gt_path.read_text(encoding="utf-8")) if gt_path.exists() else {}
    
    weights = gt.get("scoring", {}).get("weights", {
        "layers_fixed": 0.60,
        "rounds_efficiency": 0.25,
        "fix_quality": 0.15
    })
    
    checks = []
    
    # 1. 修复层数得分 (60%)
    # 从hooks的状态推断，或直接从文件推断
    code_path = w / "in" / "buggy_code.py"
    final_code = code_path.read_text(encoding="utf-8") if code_path.exists() else ""
    
    # 检查最终代码通过了多少层验证
    layers_passed = 0
    for i, layer in enumerate(gt.get("bug_layers", []), 1):
        # 简化：检查代码是否包含该层的修复特征
        # 实际应该运行验证，这里用启发式检查
        if _check_layer_fixed(final_code, layer, i):
            layers_passed += 1
    
    # 或者从out/目录的fix_log读取
    fix_log = out_dir / "fix_log.md"
    layers_from_log = _parse_fix_log(fix_log) if fix_log.exists() else {}
    
    # 取最大值
    layers_fixed = max(layers_passed, len(layers_from_log))
    total_layers = gt.get("total_layers", 5)
    
    layer_score = min(layers_fixed / total_layers, 1.0) * weights["layers_fixed"]
    
    checks.append({
        "id": "layers_fixed",
        "label": f"修复层数: {layers_fixed}/{total_layers}",
        "pass": layers_fixed >= total_layers,
        "weight": weights["layers_fixed"],
        "detail": {"layers_fixed": layers_fixed, "total": total_layers}
    })
    
    # 2. 效率得分 (25%)
    # 需要hooks记录的状态，这里简化估计
    optimal = gt.get("scoring", {}).get("efficiency", {}).get("optimal_rounds", 5)
    max_acceptable = gt.get("scoring", {}).get("efficiency", {}).get("max_acceptable_rounds", 10)
    
    # 从fix_log估计轮次
    estimated_rounds = len(layers_from_log) if layers_from_log else layers_fixed
    
    if estimated_rounds <= optimal:
        efficiency_score = 1.0
    elif estimated_rounds >= max_acceptable:
        efficiency_score = 0.0
    else:
        efficiency_score = 1.0 - (estimated_rounds - optimal) / (max_acceptable - optimal)
    
    efficiency_weighted = efficiency_score * weights["rounds_efficiency"]
    
    checks.append({
        "id": "rounds_efficiency",
        "label": f"效率: 约{estimated_rounds}轮 (最优{optimal})",
        "pass": efficiency_score > 0.5,
        "weight": weights["rounds_efficiency"],
        "detail": {"estimated_rounds": estimated_rounds, "efficiency": round(efficiency_score, 4)}
    })
    
    # 3. 修复质量 (15%)
    quality_score = 0.0
    
    # 检查fix_log是否存在
    if fix_log.exists():
        log_content = fix_log.read_text(encoding="utf-8")
        quality_checks = gt.get("scoring", {}).get("quality_checks", {})
        
        has_comments = "# FIX:" in final_code or "FIX:" in log_content
        has_log_structure = all(h in log_content for h in ["层", "修复", "问题"])
        
        quality_score = 0.0
        if has_comments:
            quality_score += 0.05
        if has_log_structure:
            quality_score += 0.05
        if layers_fixed == total_layers:
            quality_score += 0.05  # 完成度奖励
    
    quality_weighted = quality_score * weights["fix_quality"]
    
    checks.append({
        "id": "fix_quality",
        "label": "修复质量: 注释+日志+完成度",
        "pass": quality_score > 0.5,
        "weight": weights["fix_quality"],
        "detail": {"quality_score": round(quality_score, 4)}
    })
    
    # 总分
    total_score = layer_score + efficiency_weighted + quality_weighted
    
    # 等级
    level = "fail"
    if total_score >= 0.90:
        level = "excellent"
    elif total_score >= 0.75:
        level = "good"
    elif total_score >= 0.60:
        level = "pass"
    
    return {
        "task": "12-code-debug",
        "workspace": str(w),
        "outcome_score": round(total_score, 4),
        "level": level,
        "checks": checks,
        "summary": {
            "layers_fixed": f"{layers_fixed}/{total_layers}",
            "estimated_rounds": estimated_rounds,
            "quality_score": round(quality_score, 4),
            "all_layers_fixed": layers_fixed >= total_layers
        }
    }


def _check_layer_fixed(code: str, layer: dict, layer_num: int) -> bool:
    """启发式检查某层是否修复"""
    # 简化实现：检查是否包含修复后的特征
    # 实际应该运行验证
    expected = layer.get("expected_code", "")
    
    # 检查关键修复点
    if layer_num == 1:  # 语法
        return "if x > 0:" in code or "def calculate(x, y):" in code
    elif layer_num == 2:  # import
        return "import json" in code and "jsonn" not in code
    elif layer_num == 3:  # type
        return "str(score)" in code or "str(" in code
    elif layer_num == 4:  # logic
        return "<= 100" in code or "score <= 100" in code
    elif layer_num == 5:  # performance
        return "set()" in code or "seen = set" in code or "duplicates.add" in code
    
    return False


def _parse_fix_log(log_path: Path) -> dict:
    """解析fix_log.md，提取修复记录"""
    if not log_path.exists():
        return {}
    
    content = log_path.read_text(encoding="utf-8")
    # 简单解析：找"第X层"字样
    import re
    layers = re.findall(r'第(\d+)层|layer\s*(\d+)', content, re.IGNORECASE)
    return {f"L{i}": True for i in set(int(x[0] or x[1]) for x in layers)}