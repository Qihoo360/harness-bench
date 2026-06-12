from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
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


def _rewrite_windows_python3_command(args: Any) -> Any:
    if sys.platform != "win32" or not isinstance(args, (list, tuple)) or not args:
        return args
    executable = args[0]
    if not isinstance(executable, str):
        return args
    if Path(executable).name.lower() not in {"python3", "python3.exe"}:
        return args
    rewritten = [sys.executable, *args[1:]]
    return tuple(rewritten) if isinstance(args, tuple) else rewritten


def _patch_windows_python3_subprocess() -> tuple[Any, Any, Any, Any] | None:
    if sys.platform != "win32":
        return None

    original_run = subprocess.run
    original_check_call = subprocess.check_call
    original_check_output = subprocess.check_output
    original_popen = subprocess.Popen

    def run(args: Any, *pargs: Any, **kwargs: Any) -> Any:
        return original_run(_rewrite_windows_python3_command(args), *pargs, **kwargs)

    def check_call(args: Any, *pargs: Any, **kwargs: Any) -> Any:
        return original_check_call(_rewrite_windows_python3_command(args), *pargs, **kwargs)

    def check_output(args: Any, *pargs: Any, **kwargs: Any) -> Any:
        return original_check_output(_rewrite_windows_python3_command(args), *pargs, **kwargs)

    class Popen(original_popen):  # type: ignore[misc]
        def __init__(self, args: Any, *pargs: Any, **kwargs: Any) -> None:
            super().__init__(_rewrite_windows_python3_command(args), *pargs, **kwargs)

    subprocess.run = run  # type: ignore[assignment]
    subprocess.check_call = check_call  # type: ignore[assignment]
    subprocess.check_output = check_output  # type: ignore[assignment]
    subprocess.Popen = Popen  # type: ignore[assignment]
    return original_run, original_check_call, original_check_output, original_popen


def _restore_subprocess_patch(originals: tuple[Any, Any, Any, Any] | None) -> None:
    if originals is None:
        return
    subprocess.run, subprocess.check_call, subprocess.check_output, subprocess.Popen = originals  # type: ignore[assignment]


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
    patch_state = _patch_windows_python3_subprocess()
    try:
        return dict(fn(workspace))
    finally:
        _restore_subprocess_patch(patch_state)
