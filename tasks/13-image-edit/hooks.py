from __future__ import annotations
from pathlib import Path
from typing import Any

def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(context["workspace"])
    (workspace / "out").mkdir(parents=True, exist_ok=True)
    # 提示图像生成工具（如果存在）
    return {
        "IMAGE_GEN_AVAILABLE": "true",
        "EXAMPLE_STYLE": "cartoon"
    }

def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    return runtime_state

def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    pass