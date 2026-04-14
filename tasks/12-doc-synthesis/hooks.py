from __future__ import annotations
from pathlib import Path
from typing import Any


def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    """可注入一些提示变量到 prompt"""
    workspace = Path(context["workspace"])
    # 可选：创建 out 目录
    (workspace / "out").mkdir(parents=True, exist_ok=True)
    return {
        "TASK_DEADLINE": "2小时",
        "MIN_CONTRADICTIONS": "3"
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    """单轮任务，无需特殊处理"""
    return runtime_state


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    pass