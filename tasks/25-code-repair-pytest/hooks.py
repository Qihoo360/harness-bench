from __future__ import annotations
from pathlib import Path
from typing import Any

# Task-specific runtime preparation for task 25.
def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(context["workspace"])
    (workspace / "out").mkdir(parents=True, exist_ok=True)

    progress_file = workspace / "progress.md"
    if not progress_file.exists():
        progress_file.write_text(
            "# Code Repair Pytest Task Progress\n\n"
            "## Task Progress\n\n"
            "- [ ] Run pytest fixtures/app/test_config.py\n"
            "- [ ] Analyze failure and identify shallow merge bug in config_manager.py\n"
            "- [ ] Fix deep_update with recursive merge logic\n"
            "- [ ] Verify pytest passes and record the repair details\n\n",
            encoding="utf-8",
        )

    return {
        "TASK_ID": "25-code-repair-pytest",
        "TASK_NAME": "Deep Dictionary Merge Bug Fix with Pytest Closure",
        "TEST_FILE": str(workspace / "fixtures" / "app" / "test_config.py"),
        "SOURCE_FILE": str(workspace / "fixtures" / "app" / "config_manager.py"),
        "PROGRESS_FILE": str(progress_file),
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    return runtime_state


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    pass
