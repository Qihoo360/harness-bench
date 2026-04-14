from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from clawbench_v2.models import TaskSpec


def load_tasks(tasks_dir: Path) -> dict[str, TaskSpec]:
    out: dict[str, TaskSpec] = {}
    if not tasks_dir.is_dir():
        return out
    for child in sorted(tasks_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        task_yaml = child / "task.yaml"
        if not task_yaml.is_file():
            continue
        raw = task_yaml.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                import yaml  # type: ignore
            except ModuleNotFoundError as exc:
                raise RuntimeError(f"{task_yaml} is not valid JSON and PyYAML is not installed") from exc
            data = yaml.safe_load(raw) or {}
        spec = TaskSpec(
            task_id=str(data["task_id"]),
            title=str(data.get("title", data["task_id"])),
            prompt_file=str(data.get("prompt_file", "prompt.txt")),
            prompt_files=[str(x) for x in (data.get("prompt_files") or [])],
            fixtures_dir=str(data.get("fixtures_dir", "fixtures")),
            oracle_module=str(data.get("oracle_module", "oracle_grade.py")),
            hooks_module=str(data.get("hooks_module", "hooks.py")),
            timeout_sec=int(data.get("timeout_sec", 600)),
            tags=list(data.get("tags", []) or []),
            task_dir=child.resolve(),
        )
        out[spec.task_id] = spec
    return out


def _load_module(file_path: Path, module_name: str) -> Any | None:
    if not file_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_hooks(task: TaskSpec) -> Any | None:
    assert task.task_dir is not None
    return _load_module(task.task_dir / task.hooks_module, f"hooks_{task.task_id.replace('-', '_')}")


def run_oracle(task: TaskSpec, workspace: Path) -> dict[str, Any]:
    assert task.task_dir is not None
    module = _load_module(task.task_dir / task.oracle_module, f"oracle_{task.task_id.replace('-', '_')}")
    if module is None:
        return {
            "task": task.task_id,
            "workspace": str(workspace),
            "outcome_score": 0.0,
            "error": f"missing oracle module: {task.oracle_module}",
        }
    fn = getattr(module, "score_workspace", None)
    if not callable(fn):
        return {
            "task": task.task_id,
            "workspace": str(workspace),
            "outcome_score": 0.0,
            "error": "oracle module missing score_workspace(workspace)",
        }
    return dict(fn(workspace))
