from __future__ import annotations

from pathlib import Path
from typing import Any


def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(context["workspace"])
    (workspace / "out").mkdir(parents=True, exist_ok=True)
    return {
        "INCIDENT_ID": "INC-2026-04-07-APAC-CHECKOUT",
        "SAFETY_MODE": "plan_only_no_production_changes",
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    return runtime_state


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    pass
