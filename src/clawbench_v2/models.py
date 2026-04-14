from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AppConfig:
    project_root: Path
    data_dir: Path
    tasks_dir: Path
    results_dir: Path
    work_root: Path
    default_timeout_sec: int = 600
    default_rounds: int = 1


@dataclass
class TaskSpec:
    task_id: str
    title: str
    prompt_file: str = "prompt.txt"
    prompt_files: list[str] = field(default_factory=list)
    fixtures_dir: str = "fixtures"
    oracle_module: str = "oracle_grade.py"
    hooks_module: str = "hooks.py"
    timeout_sec: int = 600
    tags: list[str] = field(default_factory=list)
    task_dir: Path | None = None


@dataclass
class AdapterRunContext:
    task: TaskSpec
    workspace: Path
    sandbox: Path
    prompt: str
    prompt_file: Path
    session_id: str
    timeout_sec: int
    env: dict[str, str]
    model_id: str
    model_config: dict[str, Any]
    mode: str


@dataclass
class AdapterRunResult:
    ok: bool
    command: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskRunResult:
    task_id: str
    model_id: str
    mode: str
    sandbox: Path
    workspace: Path
    session_id: str
    prompt_file: Path
    adapter_result: AdapterRunResult
    oracle_result: dict[str, Any]
    workspace_kept: bool
    process_result: dict[str, Any] = field(default_factory=dict)
    combined_result: dict[str, Any] = field(default_factory=dict)
    usage_summary: dict[str, Any] = field(default_factory=dict)
    adapter_results: list[AdapterRunResult] = field(default_factory=list)
    runtime_state: dict[str, Any] = field(default_factory=dict)
    scoring: dict[str, Any] = field(default_factory=dict)
