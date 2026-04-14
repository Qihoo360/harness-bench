from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    """初始化：暴露第一层bug"""
    workspace = Path(context["workspace"])
    task_dir = Path(context["task"].task_dir)
    
    # 加载配置
    gt_path = task_dir / "ground_truth.json"
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    
    # 确保in目录存在
    in_dir = workspace / "in"
    in_dir.mkdir(parents=True, exist_ok=True)
    
    # 写入第一层代码
    layer1 = gt["bug_layers"][0]
    code_path = in_dir / "buggy_code.py"
    code_path.write_text(layer1["code"], encoding="utf-8")
    
    return {
        "CURRENT_LAYER": "1",
        "TOTAL_LAYERS": str(gt["total_layers"]),
        "MAX_ROUNDS": str(gt["max_rounds"]),
        "LAYER_1_EXPOSED": "true",
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    """每轮后：检查修复状态，决定是否暴露下一层"""
    workspace = Path(context["workspace"])
    task_dir = Path(context["task"].task_dir)
    round_idx = context["round_index"]  # 0-based
    
    # 加载配置
    gt = json.loads((task_dir / "ground_truth.json").read_text(encoding="utf-8"))
    
    current_layer = int(runtime_state.get("CURRENT_LAYER", 1))
    total_layers = gt["total_layers"]
    max_rounds = gt["max_rounds"]
    
    # 检查当前代码状态
    code_path = workspace / "in" / "buggy_code.py"
    if not code_path.exists():
        runtime_state[f"round_{round_idx + 1}_error"] = "code_file_missing"
        return runtime_state
    
    current_code = code_path.read_text(encoding="utf-8")
    
    # 验证当前层是否修复
    layer_fixed = _verify_layer_fixed(workspace, current_layer, gt, current_code)
    
    if layer_fixed:
        # 记录修复
        runtime_state[f"layer_{current_layer}_fixed_round"] = str(round_idx + 1)
        runtime_state[f"layer_{current_layer}_fixed"] = "true"
        
        if current_layer < total_layers:
            # 暴露下一层
            next_layer = current_layer + 1
            next_layer_data = gt["bug_layers"][next_layer - 1]
            
            # 关键：下一层代码=当前修复后的代码 + 新注入的bug
            # 或者直接用预定义的下一层代码（更可控）
            next_code = next_layer_data["code"]
            code_path.write_text(next_code, encoding="utf-8")
            
            runtime_state["CURRENT_LAYER"] = str(next_layer)
            runtime_state[f"LAYER_{next_layer}_EXPOSED"] = "true"
        else:
            # 全部完成
            runtime_state["ALL_LAYERS_FIXED"] = "true"
            runtime_state["STATUS"] = "completed"
    else:
        # 未修复，记录尝试
        runtime_state[f"layer_{current_layer}_round_{round_idx + 1}_status"] = "failed"
    
    # 检查轮次限制
    if round_idx + 1 >= max_rounds:
        runtime_state["MAX_ROUNDS_REACHED"] = "true"
        if runtime_state.get("ALL_LAYERS_FIXED") != "true":
            runtime_state["STATUS"] = "incomplete_max_rounds"
    
    return runtime_state


def _verify_layer_fixed(workspace: Path, layer: int, gt: dict, current_code: str) -> bool:
    """验证指定层是否已修复"""
    layer_data = gt["bug_layers"][layer - 1]
    validation_type = layer_data.get("validation", "syntax")
    
    code_path = workspace / "in" / "buggy_code.py"
    
    try:
        if validation_type == "syntax":
            # 语法检查：尝试编译
            compile(current_code, str(code_path), 'exec')
            return True
            
        elif validation_type in ["import", "runtime", "assertion"]:
            # 运行检查
            result = subprocess.run(
                [sys.executable, str(code_path)],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=str(workspace)
            )
            return result.returncode == 0
            
        elif validation_type == "performance":
            # 性能检查：限时运行
            result = subprocess.run(
                [sys.executable, str(code_path)],
                capture_output=True,
                text=True,
                timeout=2,  # 更严格的时间限制
                cwd=str(workspace)
            )
            return result.returncode == 0 and result.stdout.strip() != ""
            
    except Exception as e:
        return False


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    """清理：可选，复制最终文件到out/"""
    workspace = Path(context["workspace"])
    
    # 如果修复完成，确保文件在out/
    if runtime_state.get("ALL_LAYERS_FIXED") == "true":
        out_dir = workspace / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        
        code_path = workspace / "in" / "buggy_code.py"
        if code_path.exists():
            import shutil
            shutil.copy2(code_path, out_dir / "buggy_code_fixed.py")