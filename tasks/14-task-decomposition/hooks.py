from __future__ import annotations
from pathlib import Path
from typing import Any


def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(context["workspace"])
    # 创建必要的目录
    (workspace / "subtasks").mkdir(parents=True, exist_ok=True)
    (workspace / "out").mkdir(parents=True, exist_ok=True)
    return {
        "PRODUCT_NAME": "ClawMind",
        "GOAL": "策划并执行虚拟产品发布会"
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    return runtime_state


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    pass