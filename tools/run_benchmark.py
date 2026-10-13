#!/usr/bin/env python3
"""Run a frozen HarnessBench suite with Dockerized harnesses and trusted grading.

The controller is trusted and may execute task lifecycle hooks. The evaluated
OpenClaw process runs in a separate restricted container and never receives the
task oracle. A second, network-disabled container evaluates the final workspace.
Results are checkpointed after every state transition so interrupted suites can
be resumed without rerunning completed tasks.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import stat
import statistics
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any

import yaml

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
ROOT = TOOLS_DIR.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from container_runtime import (  # noqa: E402
    GRADER_IMAGE,
    GRADER_BASE_IMAGE,
    STAGER_IMAGE,
    build_images,
    constrained_run_args,
    docker,
    docker_security_identity,
    require_docker,
)
from openclaw_runtime import (  # noqa: E402
    DEFAULT_OPENCLAW_IMAGE,
    OFFICIAL_OPENCLAW_IMAGE,
    OPENCLAW_OFFLINE_IMAGE_ALIASES,
    openclaw_config,
)
from openclaw_image import build_openclaw_image  # noqa: E402
from model_budget_proxy import ModelBudgetProxy  # noqa: E402
from harness_runtime import (  # noqa: E402
    HarnessSpec,
    SPECS as OCI_HARNESS_SPECS,
    build_harness_image,
    collect_trace_files,
    contract_protocol_report,
    get_harness_spec,
    render_harness_config,
    run_contract_round,
)
from task_environment_runtime import (  # noqa: E402
    BUILDER_ID as TASK_ENVIRONMENT_BUILDER_ID,
    ResolvedTaskEnvironment,
    TaskEnvironmentSpec,
    TaskEnvironmentBuilder,
    TaskEnvironmentError,
    load_task_environment,
    run_task_environment_prepare,
)
from task_service_runtime import TrialServiceRuntime  # noqa: E402
from harnessbench.grading.oracle_contract import (  # noqa: E402
    WORKSPACE_MUTATION_SCHEMA_VERSION,
    manifest_sha256,
    normalize_mutation_contract,
    required_artifact_report as contract_artifact_report,
    workspace_manifest,
    workspace_mutation_report,
)
from harnessbench.grading.outcome_cases import aggregate_case_checks  # noqa: E402
from harnessbench.grading.semantic_judge import (  # noqa: E402
    run_semantic_judge,
    validate_judge_config,
)
from harnessbench.task_sets import (  # noqa: E402
    TaskSet,
    bytes_sha256,
    load_task_set,
    make_task_selection_identity,
    validate_task_selection_identity,
    validate_task_set_name,
)


TASKS_DIR = ROOT / "tasks"
CATALOG_DIR = ROOT / "catalog"
EVALUATIONS_DIR = ROOT / "artifacts" / "evaluations"
DASHBOARD_SOURCE = ROOT / "dashboard"


def _active_benchmark_manifest(catalog_dir: Path) -> tuple[str, int]:
    """Read the active manifest name and task count for one suite root."""
    release_path = catalog_dir / "release.yaml"
    release = yaml.safe_load(release_path.read_text(encoding="utf-8")) or {}
    active_manifest = release.get("active_manifest")
    if not isinstance(active_manifest, str) or not active_manifest:
        raise RuntimeError(f"missing active_manifest in {release_path}")
    manifest_path = catalog_dir / f"{active_manifest}.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    count = manifest.get("task_count")
    if not isinstance(count, int) or count < 1:
        raise RuntimeError(f"invalid task_count in {manifest_path}: {count!r}")
    return active_manifest, count


def _configure_suite_root(suite_root: Path) -> str:
    """Select the task and catalog directories used by this controller process."""
    global TASKS_DIR, CATALOG_DIR, BENCHMARK_TASK_COUNT

    resolved = suite_root.expanduser().resolve()
    tasks_dir = resolved / "tasks"
    catalog_dir = resolved / "catalog"
    if not tasks_dir.is_dir() or not catalog_dir.is_dir():
        raise RuntimeError(
            f"suite root must contain tasks/ and catalog/: {resolved}"
        )
    active_manifest, task_count = _active_benchmark_manifest(catalog_dir)
    TASKS_DIR = tasks_dir
    CATALOG_DIR = catalog_dir
    BENCHMARK_TASK_COUNT = task_count
    return active_manifest


_, BENCHMARK_TASK_COUNT = _active_benchmark_manifest(CATALOG_DIR)
TERMINAL_STATUSES = {
    "completed",
    "protocol_error",
    "model_error",
    "timeout",
    "infrastructure_error",
}
SCOREABLE_STATUSES = TERMINAL_STATUSES - {"infrastructure_error"}
CONTROLLER_RUNTIME_SCHEMA_VERSION = 1
ORACLE_ONLY_GRADING = "oracle_only"
BOUNDED_HYBRID_GRADING = "deterministic_oracle_with_bounded_semantic_fallback"


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    safe_chmod(temporary, 0o644)
    temporary.replace(path)
    safe_chmod(path, 0o644)


def atomic_summary_script(path: Path, payload: dict[str, Any]) -> None:
    """Write the same checkpoint as executable data for a file:// dashboard."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        "window.HARNESSBENCH_SUMMARY = "
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    safe_chmod(temporary, 0o644)
    temporary.replace(path)
    safe_chmod(path, 0o644)


def safe_chmod(path: Path, mode: int) -> None:
    try:
        path_mode = path.lstat().st_mode
        if not (stat.S_ISDIR(path_mode) or stat.S_ISREG(path_mode)):
            return
        path.chmod(mode, follow_symlinks=False)
    except OSError:
        pass


def make_agent_writable(path: Path) -> None:
    """Allow the fixed non-root container user to use one per-trial bind mount."""
    for child in path.rglob("*"):
        try:
            child_mode = child.lstat().st_mode
        except OSError:
            continue
        if stat.S_ISDIR(child_mode):
            safe_chmod(child, 0o777)
        elif stat.S_ISREG(child_mode):
            # Hooks may deliberately publish executable task tools under the
            # runtime mount. Grant write access without silently turning
            # those tools into non-executable data files.
            safe_chmod(child, 0o777 if child_mode & 0o111 else 0o666)
    safe_chmod(path, 0o777)


def make_readable(path: Path) -> None:
    for child in path.rglob("*"):
        try:
            child_mode = child.lstat().st_mode
        except OSError:
            continue
        if stat.S_ISDIR(child_mode):
            safe_chmod(child, 0o755)
        elif stat.S_ISREG(child_mode):
            safe_chmod(child, 0o644)
    safe_chmod(path, 0o755)


def load_module(path: Path, prefix: str) -> Any | None:
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"{prefix}_{uuid.uuid4().hex}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    title: str
    domain: str
    difficulty: str
    task_dir: Path
    metadata: dict[str, Any]
    definition_sha256: str

    @property
    def prompt_files(self) -> list[str]:
        names = self.metadata.get("prompt_files")
        if names:
            return [str(item) for item in names]
        return [str(self.metadata.get("prompt_file") or "prompt.txt")]

    @property
    def timeout_sec(self) -> int:
        return int(self.metadata.get("timeout_sec") or 600)

    @property
    def cases_manifest(self) -> object | None:
        if not str(self.metadata.get("outcome_test") or "").strip():
            return None
        raw_name = str(self.metadata.get("cases_file") or "tests/cases.yaml").strip()
        relative = PurePosixPath(raw_name.replace("\\", "/"))
        if (
            not raw_name
            or relative.is_absolute()
            or ".." in relative.parts
            or "." in relative.parts
        ):
            raise RuntimeError("cases_file must be one safe task-relative path")
        path = self.task_dir.joinpath(*relative.parts)
        if not path.is_file():
            raise RuntimeError(f"missing hidden case manifest: {raw_name}")
        return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class BenchmarkSelection:
    """One validated global selection before any shard partitioning."""

    tasks: tuple[BenchmarkTask, ...]
    identity: dict[str, Any]
    task_set: TaskSet | None = None
    manifest_bytes: bytes | None = None


def task_definition_sha256(task_dir: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(
        task_dir.rglob("*"),
        key=lambda path: path.relative_to(task_dir).as_posix(),
    )
    for path in paths:
        relative = path.relative_to(task_dir)
        if (
            not path.is_file()
            or "reference" in relative.parts
            or "__pycache__" in relative.parts
            or path.suffix == ".pyc"
            or path.name in {".DS_Store", "llm_rubric.py"}
        ):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def task_tree_sha256(tasks_dir: Path) -> tuple[str, int]:
    """Hash complete task packages, including hidden references and oracles."""
    digest = hashlib.sha256()
    task_yamls = sorted(
        tasks_dir.glob("*/task.yaml"),
        key=lambda path: path.relative_to(tasks_dir).as_posix(),
    )
    task_dirs = [path.parent for path in task_yamls]
    for task_dir in task_dirs:
        paths = sorted(
            task_dir.rglob("*"),
            key=lambda path: path.relative_to(tasks_dir).as_posix(),
        )
        for path in paths:
            relative = path.relative_to(tasks_dir)
            if (
                "__pycache__" in relative.parts
                or path.suffix == ".pyc"
                or path.name in {".DS_Store", "llm_rubric.py"}
            ):
                continue
            if path.is_symlink():
                raise RuntimeError(
                    f"task snapshot source must not contain symlinks: {path}"
                )
            if not path.is_file():
                continue
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest(), len(task_dirs)


def controller_definition_sha256() -> str:
    """Hash trusted host code not already captured by an OCI image ID."""
    roots = (
        Path(__file__),
        TOOLS_DIR / "container_runtime.py",
        TOOLS_DIR / "harness_runtime.py",
        TOOLS_DIR / "model_budget_proxy.py",
        TOOLS_DIR / "provider_trace.py",
        TOOLS_DIR / "openclaw_image.py",
        TOOLS_DIR / "openclaw_runtime.py",
        TOOLS_DIR / "task_environment_runtime.py",
        TOOLS_DIR / "task_service_runtime.py",
        SRC_DIR / "harnessbench" / "task_sets.py",
        SRC_DIR / "harnessbench" / "grading" / "semantic_judge.py",
    )
    digest = hashlib.sha256()
    for path in sorted(roots):
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def task_grading_mode(task: BenchmarkTask) -> str:
    raw = task.metadata.get("semantic_judge")
    if raw is None:
        return ORACLE_ONLY_GRADING
    config = validate_judge_config(raw)
    return (
        BOUNDED_HYBRID_GRADING
        if config["score_role"] == "bounded_case_fallback"
        else ORACLE_ONLY_GRADING
    )


def suite_grading_mode(tasks: list[BenchmarkTask]) -> str:
    return (
        BOUNDED_HYBRID_GRADING
        if any(task_grading_mode(task) == BOUNDED_HYBRID_GRADING for task in tasks)
        else ORACLE_ONLY_GRADING
    )


def _regular_file_sha256(path: Path, *, label: str) -> str:
    """Hash one regular, non-symlink file without recording its host path."""
    if path.is_symlink():
        raise RuntimeError(f"{label} must not be a symlink: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"cannot open required {label}: {path}: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError(f"{label} is not a regular file: {path}")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _distribution_identity(name: str) -> dict[str, Any]:
    """Hash every installed file owned by a Python distribution."""
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"required Python distribution is missing: {name}") from exc
    version = str(distribution.version or "")
    files = distribution.files
    if not version or files is None:
        raise RuntimeError(f"required Python distribution is incomplete: {name}")

    entries: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for entry in files:
        relative = PurePosixPath(str(entry).replace("\\", "/"))
        if "__pycache__" in relative.parts or relative.suffix == ".pyc":
            continue
        relative_name = relative.as_posix()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative_name in {"", "."}
        ):
            raise RuntimeError(
                f"{name} distribution contains an unsafe file entry: {relative_name}"
            )
        if relative_name in seen:
            raise RuntimeError(
                f"{name} distribution contains a duplicate file entry: {relative_name}"
            )
        seen.add(relative_name)
        entries.append((relative_name, entry))
    if not entries:
        raise RuntimeError(f"required Python distribution has no files: {name}")

    digest = hashlib.sha256()
    for relative_name, entry in sorted(entries):
        installed_path = Path(distribution.locate_file(entry))
        file_digest = _regular_file_sha256(
            installed_path,
            label=f"{name} distribution file",
        )
        digest.update(relative_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\0")
    return {
        "version": version,
        "distribution_tree_sha256": digest.hexdigest(),
        "file_count": len(entries),
    }


def _controller_runtime_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def controller_runtime_identity() -> tuple[dict[str, Any], str]:
    """Freeze the path-free host interpreter and Python dependency identity."""
    try:
        executable = Path(sys.executable).resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("cannot resolve the running Python executable") from exc
    version_info = tuple(sys.version_info[:3])
    distributions = {"PyYAML": _distribution_identity("PyYAML")}
    if version_info < (3, 11, 0):
        distributions["tomli"] = _distribution_identity("tomli")
    payload = {
        "schema_version": CONTROLLER_RUNTIME_SCHEMA_VERSION,
        "implementation": platform.python_implementation(),
        "python_version": ".".join(str(part) for part in version_info),
        "python_executable_sha256": _regular_file_sha256(
            executable,
            label="resolved Python executable",
        ),
        "distributions": distributions,
        "uv_lock_sha256": _regular_file_sha256(
            ROOT / "uv.lock",
            label="uv.lock",
        ),
    }
    return payload, _controller_runtime_id(payload)


def freeze_suite_snapshot(run_root: Path) -> tuple[Path, str, str]:
    """Create or verify the immutable task tree used throughout one run."""
    snapshot_root = run_root / "suite_snapshot"
    snapshot_tasks = snapshot_root / "tasks"
    manifest_path = snapshot_root / "manifest.json"
    if snapshot_root.exists():
        if not manifest_path.is_file() or not snapshot_tasks.is_dir():
            raise RuntimeError(
                f"incomplete suite snapshot in existing run root: {snapshot_root}"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual_sha, actual_count = task_tree_sha256(snapshot_tasks)
        controller_sha = controller_definition_sha256()
        if (
            manifest.get("schema_version") != 1
            or manifest.get("task_count") != BENCHMARK_TASK_COUNT
            or actual_count != BENCHMARK_TASK_COUNT
            or manifest.get("task_tree_sha256") != actual_sha
            or manifest.get("controller_definition_sha256") != controller_sha
        ):
            raise RuntimeError("existing suite snapshot failed its integrity manifest")
        return snapshot_tasks, actual_sha, controller_sha

    source_before, source_count = task_tree_sha256(TASKS_DIR)
    if source_count != BENCHMARK_TASK_COUNT:
        raise RuntimeError(
            "cannot freeze suite snapshot: "
            f"expected {BENCHMARK_TASK_COUNT} tasks, found {source_count}"
        )
    temporary_root = run_root / f".suite_snapshot.{uuid.uuid4().hex}.tmp"
    temporary_tasks = temporary_root / "tasks"
    temporary_tasks.mkdir(parents=True)
    try:
        for task_yaml in sorted(TASKS_DIR.glob("*/task.yaml")):
            source_task = task_yaml.parent
            shutil.copytree(
                source_task,
                temporary_tasks / source_task.name,
                ignore=shutil.ignore_patterns(
                    "__pycache__",
                    "*.pyc",
                    ".DS_Store",
                    "llm_rubric.py",
                ),
            )
        copied_sha, copied_count = task_tree_sha256(temporary_tasks)
        source_after, after_count = task_tree_sha256(TASKS_DIR)
        if (
            copied_count != BENCHMARK_TASK_COUNT
            or after_count != BENCHMARK_TASK_COUNT
            or copied_sha != source_before
            or source_after != source_before
        ):
            raise RuntimeError(
                "task tree changed while the evaluation snapshot was copied"
            )
        controller_sha = controller_definition_sha256()
        atomic_json(
            temporary_root / "manifest.json",
            {
                "schema_version": 1,
                "task_count": copied_count,
                "task_tree_sha256": copied_sha,
                "controller_definition_sha256": controller_sha,
            },
        )
        for path in sorted(
            temporary_root.rglob("*"),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            path.chmod(0o555 if path.is_dir() else 0o444)
        temporary_root.chmod(0o555)
        temporary_root.replace(snapshot_root)
        return snapshot_tasks, copied_sha, controller_sha
    except BaseException:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise


def load_benchmark_tasks(tasks_dir: Path = TASKS_DIR) -> list[BenchmarkTask]:
    tasks: list[BenchmarkTask] = []
    for task_yaml in sorted(tasks_dir.glob("*/task.yaml")):
        data = yaml.safe_load(task_yaml.read_text(encoding="utf-8")) or {}
        tasks.append(
            BenchmarkTask(
                task_id=str(data.get("task_id") or task_yaml.parent.name),
                title=str(data.get("title") or task_yaml.parent.name),
                domain=str(data.get("domain") or "Uncategorized"),
                difficulty=str(data.get("difficulty") or "unknown"),
                task_dir=task_yaml.parent.resolve(),
                metadata=data,
                definition_sha256=task_definition_sha256(task_yaml.parent),
            )
        )
    actual = [task.task_id.split("-", 1)[0] for task in tasks]
    if (
        len(tasks) != BENCHMARK_TASK_COUNT
        or len(set(actual)) != BENCHMARK_TASK_COUNT
        or any(not prefix.isdigit() or len(prefix) != 3 for prefix in actual)
    ):
        raise RuntimeError(
            f"benchmark suite must contain exactly {BENCHMARK_TASK_COUNT} uniquely numbered active "
            f"tasks; count={len(tasks)} ids={actual}"
        )
    return tasks


def resolve_benchmark_selection(
    all_tasks: list[BenchmarkTask],
    *,
    requested_tasks: str | None,
    task_set_name: str | None,
    catalog_dir: Path = CATALOG_DIR,
) -> BenchmarkSelection:
    """Resolve one complete selection and its immutable pre-shard identity."""
    if requested_tasks is not None and task_set_name is not None:
        raise ValueError("--tasks and --task-set are mutually exclusive")
    known_task_ids = {task.task_id for task in all_tasks}
    task_set: TaskSet | None = None
    manifest_bytes: bytes | None = None
    if task_set_name is not None:
        task_set = load_task_set(
            catalog_dir,
            task_set_name,
            known_task_ids=known_task_ids,
        )
        selected = set(task_set.task_ids)
        try:
            manifest_bytes = task_set.source_path.read_bytes()
        except OSError as exc:
            raise ValueError(
                f"cannot freeze task-set manifest {task_set.source_path}: {exc}"
            ) from exc
        if bytes_sha256(manifest_bytes) != task_set.manifest_sha256:
            raise ValueError("task-set manifest changed while it was being loaded")
        mode = "named"
    elif requested_tasks:
        selected = {item.strip() for item in requested_tasks.split(",") if item.strip()}
        unknown = selected - known_task_ids
        if unknown:
            raise ValueError(f"unknown benchmark task(s): {', '.join(sorted(unknown))}")
        mode = "inline"
    else:
        selected = known_task_ids
        mode = "main"
    tasks = tuple(task for task in all_tasks if task.task_id in selected)
    identity = make_task_selection_identity(
        mode=mode,
        task_ids=(task.task_id for task in tasks),
        task_set=(task_set.name if task_set is not None else None),
        manifest_sha256=(task_set.manifest_sha256 if task_set is not None else None),
    )
    return BenchmarkSelection(
        tasks=tasks,
        identity=identity,
        task_set=task_set,
        manifest_bytes=manifest_bytes,
    )


def select_benchmark_tasks(
    all_tasks: list[BenchmarkTask],
    *,
    requested_tasks: str | None,
    task_set_name: str | None,
    catalog_dir: Path = CATALOG_DIR,
) -> list[BenchmarkTask]:
    """Compatibility wrapper returning only the selected task records."""
    return list(
        resolve_benchmark_selection(
            all_tasks,
            requested_tasks=requested_tasks,
            task_set_name=task_set_name,
            catalog_dir=catalog_dir,
        ).tasks
    )


def freeze_task_set_manifest(
    run_root: Path,
    selection: BenchmarkSelection,
) -> Path:
    """Freeze a named selection manifest without overwriting resume identity."""
    if selection.task_set is None or selection.manifest_bytes is None:
        raise ValueError("only a named task selection has a manifest to freeze")
    catalog_dir = run_root / "selection" / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    safe_chmod(run_root / "selection", 0o755)
    safe_chmod(catalog_dir, 0o755)
    destination = catalog_dir / f"{selection.task_set.name}.yaml"
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_file():
            raise ValueError(
                f"frozen task-set manifest must be a regular file: {destination}"
            )
        if destination.read_bytes() != selection.manifest_bytes:
            raise ValueError(
                "frozen task-set manifest does not match the requested selection"
            )
        return catalog_dir
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(selection.manifest_bytes)
    safe_chmod(temporary, 0o644)
    temporary.replace(destination)
    safe_chmod(destination, 0o644)
    return catalog_dir


def stage_workspace_seed(task: BenchmarkTask, workspace: Path) -> None:
    """Copy the task's public seed into one writable agent workspace."""
    seed_name = str(task.metadata.get("workspace_dir") or "workspace").strip()
    seed_relative = Path(seed_name)
    if (
        not seed_name
        or seed_relative.is_absolute()
        or ".." in seed_relative.parts
        or len(seed_relative.parts) != 1
    ):
        raise RuntimeError("workspace_dir must be one safe task-root directory")
    seed = task.task_dir / seed_relative
    ensure_workspace_root(workspace)
    if not seed.exists():
        return
    if not seed.is_dir() or seed.is_symlink():
        raise RuntimeError(f"workspace seed must be a real directory: {seed}")
    ignored_runtime_artifacts = shutil.ignore_patterns(
        "__pycache__",
        "*.pyc",
        ".DS_Store",
    )
    for child in seed.iterdir():
        destination = workspace / child.name
        if child.is_dir():
            shutil.copytree(
                child,
                destination,
                dirs_exist_ok=True,
                ignore=ignored_runtime_artifacts,
            )
        elif child.name == ".DS_Store" or child.suffix == ".pyc":
            continue
        else:
            shutil.copy2(child, destination)


def ensure_workspace_root(workspace: Path) -> None:
    """Create or validate the single agent-visible workspace root."""
    if workspace.is_symlink():
        raise RuntimeError(f"workspace must not be a symlink: {workspace}")
    if workspace.exists() and not workspace.is_dir():
        raise RuntimeError(f"workspace must be a directory: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)


def aggregate_workspace_mutations(
    reports: list[dict[str, Any]],
    *,
    contract: dict[str, object] | None,
) -> dict[str, Any]:
    """Combine independently measured agent rounds without charging hooks."""
    normalized = normalize_mutation_contract(contract)
    path_fields = (
        "created",
        "modified",
        "deleted",
        "unexpected_created",
        "unexpected_modified",
        "unexpected_deleted",
        "unsafe_entries",
        "protected_modified",
        "protected_deleted",
    )
    aggregate: dict[str, Any] = {
        "schema_version": WORKSPACE_MUTATION_SCHEMA_VERSION,
        "enabled": True,
        "complete": all(bool(report.get("complete")) for report in reports),
        "safe": all(bool(report.get("safe")) for report in reports),
        "contract": normalized,
        "rounds": reports,
        "before_sha256": reports[0].get("before_sha256") if reports else None,
        "after_sha256": reports[-1].get("after_sha256") if reports else None,
    }
    for field in path_fields:
        aggregate[field] = sorted(
            {str(path) for report in reports for path in report.get(field, [])}
        )
    aggregate["safety_violations"] = {
        "protected_modified": aggregate["protected_modified"],
        "protected_deleted": aggregate["protected_deleted"],
        "unsafe_entries": aggregate["unsafe_entries"],
    }
    return aggregate


def environment_mutations(
    hooks: Any,
    context: dict[str, Any],
    runtime_state: dict[str, Any],
    round_index: int,
    *,
    callback_name: str = "environment_mutations",
) -> dict[str, dict[str, dict[str, object]]]:
    """Load a hook's content-addressed, environment-owned round changes."""
    empty = {"created": {}, "modified": {}, "deleted": {}}
    callback = getattr(hooks, callback_name, None) if hooks else None
    if not callable(callback):
        return empty
    raw = callback(context, runtime_state, round_index)
    if raw is None:
        return empty
    if not isinstance(raw, dict) or set(raw) - set(empty):
        raise RuntimeError(
            f"{callback_name} must contain only created/modified/deleted"
        )
    normalized: dict[str, dict[str, dict[str, object]]] = {}
    for operation in empty:
        values = raw.get(operation, {})
        if not isinstance(values, dict):
            raise RuntimeError(f"{callback_name}.{operation} must be an object")
        entries: dict[str, dict[str, object]] = {}
        for path, manifest_entry in values.items():
            normalized_path = normalize_mutation_contract(
                {"allowed_created": [str(path)]}
            )["allowed_created"][0]
            if normalized_path.endswith("/"):
                raise RuntimeError("environment mutation paths must be exact files")
            if not isinstance(manifest_entry, dict):
                raise RuntimeError(
                    f"environment mutation entry must be an object: {path}"
                )
            entries[normalized_path] = dict(manifest_entry)
        normalized[operation] = entries
    return normalized


def quiesce_environment(
    hooks: Any,
    context: dict[str, Any],
    runtime_state: dict[str, Any],
    round_index: int,
) -> None:
    """Let a hook finish concurrent writes before the trusted snapshot."""
    callback = getattr(hooks, "quiesce_environment", None) if hooks else None
    if callable(callback):
        callback(context, runtime_state, round_index)


def measure_agent_workspace_mutation(
    before: dict[str, dict[str, object]],
    after: dict[str, dict[str, object]],
    *,
    contract: dict[str, object] | None,
    trusted: dict[str, dict[str, dict[str, object]]] | None = None,
    protected_paths: set[str] | None = None,
) -> dict[str, Any]:
    """Subtract only content-addressed hook changes from a round delta."""
    adjusted_before = dict(before)
    matched: dict[str, list[str]] = {
        "created": [],
        "modified": [],
        "deleted": [],
    }
    mismatched: dict[str, list[str]] = {
        "created": [],
        "modified": [],
        "deleted": [],
    }
    for operation, entries in (trusted or {}).items():
        for path, expected in entries.items():
            if operation == "created":
                valid = path not in before and after.get(path) == expected
                if valid:
                    adjusted_before[path] = expected
            elif operation == "modified":
                valid = path in before and after.get(path) == expected
                if valid:
                    adjusted_before[path] = expected
            elif operation == "deleted":
                valid = (
                    path in before and path not in after and before[path] == expected
                )
                if valid:
                    adjusted_before.pop(path, None)
            else:  # pragma: no cover - normalized by environment_mutations
                raise RuntimeError(f"unsupported environment mutation: {operation}")
            (matched if valid else mismatched)[operation].append(path)
    report = workspace_mutation_report(
        adjusted_before,
        after,
        contract=contract,
        protected_paths=protected_paths,
    )
    report["environment_mutations"] = {
        "matched": matched,
        "mismatched_or_absent": mismatched,
    }
    return report


def enforce_cleanup_workspace_immutability(
    before: dict[str, dict[str, object]],
    after: dict[str, dict[str, object]],
) -> None:
    """Reject any formal-task workspace change made during cleanup."""
    if after != before:
        raise RuntimeError("formal cleanup hooks must not change the final workspace")


def container_runtime_value(value: str, workspace: Path, runtime: Path) -> str:
    replacements = (
        (str(workspace.resolve()), "/workspace"),
        (str(runtime.resolve()), "/runtime"),
    )
    rendered = value
    for source, target in replacements:
        if rendered == source or rendered.startswith(source + os.sep):
            rendered = target + rendered[len(source) :]
    return rendered


def render_prompt(
    task: BenchmarkTask,
    prompt_name: str,
    workspace: Path,
    runtime: Path,
    runtime_state: dict[str, Any],
) -> str:
    text = (task.task_dir / prompt_name).read_text(encoding="utf-8")
    text = text.replace("$WORKSPACE", "/workspace")
    for key, value in runtime_state.items():
        if isinstance(value, str):
            text = text.replace(
                f"${key}", container_runtime_value(value, workspace, runtime)
            )
    if "mutation_contract" in task.metadata:
        contract = normalize_mutation_contract(task.metadata.get("mutation_contract"))

        def render_paths(field: str) -> str:
            paths = contract[field]
            return ", ".join(f"`{path}`" for path in paths) if paths else "none"

        text = (
            text.rstrip()
            + "\n\n---\n\n"
            + "## Enforced workspace change contract\n\n"
            + "The controller compares the final state of `/workspace` after "
            + "every round with that round's starting state. At the end of the "
            + "round, only these final-state differences may remain:\n\n"
            + f"- Create: {render_paths('allowed_created')}\n"
            + f"- Modify: {render_paths('allowed_modified')}\n"
            + f"- Delete: {render_paths('allowed_deleted')}\n\n"
            + "Do not leave any other created, modified, or deleted path in "
            + "`/workspace`; do not leave symlinks, special files, or "
            + "undeclared directories there. Put persistent scratch helpers in "
            + "`/runtime` and single-round temporary files in `/tmp`.\n"
        )
    return text


def normalize_container_runtime_paths(
    workspace: Path,
    runtime: Path,
    runtime_state: dict[str, Any],
) -> None:
    """Require hooks to configure environment paths for the container mount graph."""
    remote_path = runtime_state.get("REMOTE_PATH")
    dotgit = workspace / ".git"
    if isinstance(remote_path, str) and dotgit.exists():
        container_remote = container_runtime_value(remote_path, workspace, runtime)
        if container_remote != remote_path:
            raise RuntimeError(
                "hook returned a host Git remote path; trusted setup must "
                "write the container-visible /runtime path directly"
            )
        config_path = (
            dotgit / "config"
            if dotgit.is_dir()
            else runtime / "workspace-clone" / ".git" / "config"
        )
        config_text = config_path.read_text(encoding="utf-8")
        if container_remote not in config_text:
            raise RuntimeError(
                "workspace Git origin does not match the declared "
                f"container path: {container_remote}"
            )


def hook_context(
    task: BenchmarkTask,
    workspace: Path,
    runtime: Path,
    labels: list[str],
) -> dict[str, Any]:
    task_spec = SimpleNamespace(
        task_id=task.task_id,
        task_dir=task.task_dir,
        title=task.title,
    )

    def run_trusted_helper(
        command: list[str],
        *,
        workspace_read_only: bool = False,
        runtime_read_only: bool = False,
        workdir: str = "/runtime",
    ) -> subprocess.CompletedProcess[str]:
        """Run task setup inside a fixed, network-disabled helper container."""
        if workdir not in {"/workspace", "/runtime"}:
            raise ValueError("trusted helper workdir must be /workspace or /runtime")
        workspace_mount = f"type=bind,src={workspace.resolve()},dst=/workspace" + (
            ",readonly" if workspace_read_only else ""
        )
        runtime_mount = f"type=bind,src={runtime.resolve()},dst=/runtime" + (
            ",readonly" if runtime_read_only else ""
        )
        # Trusted setup helpers create files that the controller must inspect.
        # Match the controller identity so bind mounts also work on NFS and
        # rootless Docker hosts; the untrusted harness still uses its fixed,
        # non-root image user.
        controller_user = (
            f"{os.getuid()}:{os.getgid()}"
            if hasattr(os, "getuid") and hasattr(os, "getgid")
            else "1000:1000"
        )
        return docker(
            "run",
            "--rm",
            *labels,
            *constrained_run_args(),
            "--user",
            controller_user,
            "--env",
            "HOME=/tmp",
            "--env",
            "HB_WORKSPACE=/workspace",
            "--env",
            "HB_RUNTIME=/runtime",
            "--env",
            "GIT_CONFIG_COUNT=1",
            "--env",
            "GIT_CONFIG_KEY_0=safe.directory",
            "--env",
            "GIT_CONFIG_VALUE_0=*",
            "--mount",
            workspace_mount,
            "--mount",
            runtime_mount,
            "--workdir",
            workdir,
            GRADER_BASE_IMAGE,
            *command,
        )

    return {
        "task": task_spec,
        "sandbox": runtime,
        "workspace": workspace,
        "service_bind_host": "0.0.0.0",
        "service_public_host": "host.docker.internal",
        "run_trusted_helper": run_trusted_helper,
    }


def prepare_hook(
    hooks: Any | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    if hooks is None:
        return {}
    prepare = getattr(hooks, "prepare_runtime", None)
    if callable(prepare):
        result = prepare(context)
        return dict(result) if isinstance(result, dict) else {}
    setup = getattr(hooks, "setup", None)
    if callable(setup):
        result = setup(str(context["workspace"]))
        return dict(result) if isinstance(result, dict) else {}
    return {}


def after_round_hook(
    hooks: Any | None,
    context: dict[str, Any],
    runtime_state: dict[str, Any],
    round_index: int,
    prompt_name: str,
    prompt_file: Path,
    agent: subprocess.CompletedProcess[str],
) -> None:
    after_round = getattr(hooks, "after_round", None) if hooks else None
    if not callable(after_round):
        return
    round_context = {
        **context,
        "round_index": round_index,
        "prompt_name": prompt_name,
        "prompt_file": prompt_file,
    }
    adapter_result = SimpleNamespace(
        ok=agent.returncode == 0,
        stdout=agent.stdout,
        stderr=agent.stderr,
        metadata={"returncode": agent.returncode},
    )
    result = after_round(round_context, runtime_state, adapter_result)
    if isinstance(result, dict):
        runtime_state.update(result)


def cleanup_hook(
    hooks: Any | None,
    context: dict[str, Any],
    runtime_state: dict[str, Any],
) -> None:
    if hooks is None:
        return
    cleanup = getattr(hooks, "cleanup_runtime", None)
    if callable(cleanup):
        cleanup(context, runtime_state)
        return
    teardown = getattr(hooks, "teardown", None)
    if callable(teardown):
        teardown(str(context["workspace"]), runtime_state)


def required_artifact_report(task: BenchmarkTask, workspace: Path) -> dict[str, Any]:
    report = contract_artifact_report(
        workspace,
        task.metadata.get("required_artifacts", []),
    )
    declared = report["declared"]
    report["completion_ratio"] = (
        round(len(report["present"]) / len(declared), 4) if declared else None
    )
    return report


def authoritative_artifact_report(
    task: BenchmarkTask,
    workspace: Path,
    oracle_result: dict[str, Any],
) -> dict[str, Any]:
    """Prefer the isolated grader's view when NFS ownership hides agent files.

    Agent outputs may be mode 0600 and owned by the fixed container UID.  The
    grader deliberately reuses that UID, while the remote controller user may
    be unable to open or chmod the files on NFS.  The grader's contract report
    is therefore the authoritative readability/validity result.
    """
    raw = oracle_result.get("required_artifact_contract")
    if not isinstance(raw, dict):
        return required_artifact_report(task, workspace)
    declared = list(raw.get("declared") or [])
    present = list(raw.get("present") or [])
    missing = list(raw.get("missing") or [])
    invalid = dict(raw.get("invalid") or {})
    return {
        "declared": declared,
        "present": present,
        "missing": missing,
        "invalid": invalid,
        "complete": bool(raw.get("complete")),
        "completion_ratio": (
            round(len(present) / len(declared), 4) if declared else None
        ),
    }


def grader_contract_error(oracle_result: dict[str, Any]) -> str:
    """Return a non-scoreable grader failure description, if present."""
    if not oracle_result.get("oracle_contract_failure"):
        return ""
    detail = str(oracle_result.get("error") or "unknown oracle failure")
    if oracle_result.get("oracle_infrastructure_failure"):
        return f"trusted grader infrastructure unavailable: {detail}"
    return f"trusted oracle contract failed: {detail}"


def oracle_case_failure_metrics(oracle_result: dict[str, Any]) -> dict[str, Any]:
    """Expose stable case-failure counters without leaking hidden case IDs.

    Legacy oracles do not emit the formal case lists.  Missing or malformed
    lists therefore map to the backwards-compatible no-reported-failure
    defaults instead of making an otherwise scoreable legacy run unreadable.
    """

    failed_gates = oracle_result.get("failed_gates")
    failed_contract_gates = oracle_result.get("failed_contract_gates")
    failed_core = oracle_result.get("failed_core_cases")
    task_gate_ids = (
        [str(item) for item in failed_gates] if isinstance(failed_gates, list) else []
    )
    contract_gate_ids = (
        [str(item) for item in failed_contract_gates]
        if isinstance(failed_contract_gates, list)
        else []
    )
    failed_gate_count = len(dict.fromkeys([*task_gate_ids, *contract_gate_ids]))
    failed_core_count = len(failed_core) if isinstance(failed_core, list) else 0
    return {
        "safety_gate_failed": failed_gate_count > 0,
        "failed_gate_count": failed_gate_count,
        "failed_core_count": failed_core_count,
    }


def parse_agent_payload(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def protocol_report(
    payload: dict[str, Any],
    *,
    stderr: str = "",
) -> dict[str, Any]:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    visible = str(meta.get("finalAssistantVisibleText") or "")
    raw = str(meta.get("finalAssistantRawText") or "")
    final_text = visible + "\n" + raw
    dangling_markers = (
        "<|tool_call>",
        "<tool_call|>",
        "<tool_call>",
        "</tool_call>",
    )
    dangling_tool_call = any(marker in final_text for marker in dangling_markers)
    aborted = bool(meta.get("aborted"))
    timeout_phase = str(meta.get("timeoutPhase") or "").strip() or None
    liveness_state = str(meta.get("livenessState") or "").strip() or None
    stop_reason = str(meta.get("stopReason") or "").strip() or None
    terminal_error = str(meta.get("terminalError") or "").strip() or None
    final_status = str(meta.get("finalStatus") or "").strip() or None
    payloads = payload.get("payloads")
    payload_texts = (
        [str(item.get("text") or "") for item in payloads if isinstance(item, dict)]
        if isinstance(payloads, list)
        else []
    )
    payload_text = "\n".join(payload_texts)
    normalized_payload_text = payload_text.casefold()
    payload_provider_timeout = any(
        marker in normalized_payload_text
        for marker in (
            "request timed out before a response was generated",
            "request timed out.",
        )
    )
    payload_controller_timeout = "controller timeout expired" in normalized_payload_text
    normalized_stderr = str(stderr or "").casefold()
    stderr_controller_timeout = (
        "[agent/embedded] embedded run timeout:" in normalized_stderr
    )
    stderr_provider_timeout = any(
        (
            (
                "[agent/embedded]" in normalized_stderr
                and "iserror=true" in normalized_stderr
                and "error=llm request timed out" in normalized_stderr
                and "rawerror=terminated" in normalized_stderr
                and not stderr_controller_timeout
            ),
            "failovererror: llm request timed out" in normalized_stderr,
        )
    )
    provider_timeout_phase = str(timeout_phase or "").casefold() in {
        "provider",
        "model_request",
        "upstream",
    }
    provider_request_timeout = stderr_provider_timeout or (
        aborted and (provider_timeout_phase or payload_provider_timeout)
    )
    explicit_timeout = any(
        (
            payload_provider_timeout,
            payload_controller_timeout,
            stderr_provider_timeout,
            stderr_controller_timeout,
        )
    )
    timed_out = (
        stderr_controller_timeout
        or payload_controller_timeout
        or (aborted and bool(timeout_phase) and not provider_request_timeout)
    )
    normalized_payloads = [
        text.strip().lstrip("⚠️").strip().casefold()
        for text in payload_texts
        if text.strip()
    ]
    error_payload = any(
        text.startswith(
            (
                "agent couldn't generate a response",
                "agent could not generate a response",
            )
        )
        for text in normalized_payloads
    )
    native_non_deliverable = "non_deliverable_terminal_turn" in normalized_stderr or (
        "incomplete turn detected" in normalized_stderr
        and "stopreason=length" in normalized_stderr
        and "payloads=0" in normalized_stderr
        and "surfacing error to user" in normalized_stderr
    )
    deliverable_payload_text = any(
        text
        and not text.startswith(
            (
                "agent couldn't generate a response",
                "agent could not generate a response",
            )
        )
        for text in normalized_payloads
    )
    has_deliverable_text = bool(final_text.strip()) or deliverable_payload_text
    length_without_payload = (
        str(stop_reason or "").casefold()
        in {"length", "max_tokens", "max_output_tokens"}
        and not has_deliverable_text
    )
    non_deliverable_terminal_turn = any(
        (
            str(liveness_state or "").casefold() == "abandoned",
            str(terminal_error or "").casefold() == "non_deliverable_terminal_turn",
            str(final_status or "").casefold() == "error",
            error_payload,
            native_non_deliverable,
            length_without_payload,
        )
    )
    return {
        "replay_invalid": bool(meta.get("replayInvalid")),
        # OpenClaw marks a turn replay-invalid after a mutating tool action.
        # This is automatic retry safety telemetry, not a claim that the
        # exported trajectory is structurally malformed.
        "automatic_replay_safe": not bool(meta.get("replayInvalid")),
        "liveness_state": liveness_state,
        "stop_reason": stop_reason,
        "terminal_error": terminal_error,
        "final_status": final_status,
        "dangling_tool_call": dangling_tool_call,
        "aborted": aborted,
        "timeout_phase": timeout_phase,
        "explicit_timeout": explicit_timeout,
        "stderr_timeout": stderr_controller_timeout or stderr_provider_timeout,
        "timed_out": timed_out,
        "provider_request_timeout": provider_request_timeout,
        "error_payload": error_payload,
        "native_non_deliverable": native_non_deliverable,
        "length_without_payload": length_without_payload,
        "non_deliverable_terminal_turn": non_deliverable_terminal_turn,
        "fatal": (
            dangling_tool_call
            or aborted
            or timed_out
            or provider_request_timeout
            or non_deliverable_terminal_turn
        ),
    }


def protocol_failure_status(protocol: dict[str, Any]) -> str | None:
    """Classify an OpenClaw terminal protocol report without losing telemetry."""
    if protocol.get("provider_request_timeout") or protocol.get(
        "provider_stream_error"
    ):
        return "infrastructure_error"
    if protocol.get("timed_out"):
        return "timeout"
    if protocol.get("fatal"):
        return "protocol_error"
    return None


def agent_round_failure_status(
    returncode: int,
    protocol: dict[str, Any],
) -> str | None:
    """Classify a round, preserving a protocol timeout behind exit code 1."""
    protocol_status = protocol_failure_status(protocol)
    if protocol_status in {"timeout", "infrastructure_error"}:
        return protocol_status
    if returncode == 124:
        return "timeout"
    if returncode != 0:
        return "model_error"
    return protocol_status


def provider_turn_budget_exhausted(
    proxy: ModelBudgetProxy,
    *,
    max_agent_turns: int,
) -> bool:
    """Recognize a clean controller stop at the shared generation limit."""
    return bool(
        proxy.model_requests == max_agent_turns
        and proxy.rejected_requests > 0
        and proxy.forward_errors == 0
    )


def provider_forward_failure(proxy: ModelBudgetProxy) -> str | None:
    """Return a retryable provider failure recorded outside the harness."""
    if proxy.forward_errors < 1:
        return None
    detail = proxy.last_error or "unknown upstream transport error"
    return (
        f"model provider forwarding failed {proxy.forward_errors} time(s): "
        f"{detail}"
    )


class SuiteState:
    def __init__(
        self,
        *,
        run_root: Path,
        tasks: list[BenchmarkTask],
        model: str,
        base_url: str,
        model_service_id: str,
        image: str,
        harness_id: str,
        harness_protocol: str,
        harness_version: str,
        trace_complete: bool,
        workers: int,
        max_tokens: int,
        temperature: float,
        data_parallel_rank: int | None,
        thinking: str,
        timeout_override: int | None,
        max_agent_turns: int,
        runtime_images: dict[str, str],
        isolation: dict[str, Any],
        selected_task_ids: set[str],
        shard_index: int,
        shard_count: int,
        suite_snapshot_sha256: str,
        controller_sha256: str,
        controller_runtime: dict[str, Any],
        controller_runtime_id: str,
        grading: str,
        task_set_name: str | None = None,
        selection_identity: dict[str, Any] | None = None,
        context_window: int = 200000,
        experimental_task_environments: bool = False,
        task_environment_set_sha256: str | None = None,
        task_environments: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.run_root = run_root
        self.summary_path = run_root / "summary.json"
        self.summary_script_path = run_root / "summary.js"
        self.lock = threading.Lock()
        self.started_epoch = time.time()
        self.model = model
        self.base_url = base_url
        self.model_service_id = model_service_id
        self.image = image
        self.harness_id = harness_id
        self.provider_timeout_policy = (
            "match_round_timeout" if harness_id == "openclaw" else "harness_managed"
        )
        self.harness_protocol = harness_protocol
        self.harness_version = harness_version
        self.trace_complete = trace_complete
        self.workers = workers
        self.max_tokens = max_tokens
        self.context_window = context_window
        self.temperature = temperature
        self.experimental_task_environments = bool(experimental_task_environments)
        self.task_environment_set_sha256 = task_environment_set_sha256
        self.task_environments = {
            str(task_id): dict(value)
            for task_id, value in (task_environments or {}).items()
        }
        self.data_parallel_rank = data_parallel_rank
        self.thinking = thinking
        self.timeout_override = timeout_override
        self.max_agent_turns = max_agent_turns
        self.runtime_images = dict(runtime_images)
        self.isolation = dict(isolation)
        self.selected_task_ids = set(selected_task_ids)
        self.shard_index = shard_index
        self.shard_count = shard_count
        self.suite_snapshot_sha256 = suite_snapshot_sha256
        self.controller_definition_sha256 = controller_sha256
        self.controller_runtime = dict(controller_runtime)
        self.controller_runtime_id = controller_runtime_id
        if grading not in {ORACLE_ONLY_GRADING, BOUNDED_HYBRID_GRADING}:
            raise ValueError(f"unsupported grading mode: {grading!r}")
        self.grading = grading
        self.task_set_name = task_set_name
        self.selection_identity = (
            validate_task_selection_identity(selection_identity)
            if selection_identity is not None
            else None
        )
        if self.selection_identity is not None:
            identity_name = self.selection_identity["task_set"]
            if identity_name != self.task_set_name:
                raise ValueError(
                    "task_set_name does not match the task selection identity"
                )
        elif self.task_set_name is not None:
            raise ValueError("named task set requires a task selection identity")
        computed_runtime_id = _controller_runtime_id(self.controller_runtime)
        if self.controller_runtime_id != computed_runtime_id:
            raise ValueError(
                "controller_runtime_id does not match controller_runtime: "
                f"expected={computed_runtime_id}, actual={self.controller_runtime_id}"
            )
        self.task_defs = {task.task_id: task for task in tasks}
        suite_digest = hashlib.sha256()
        for task in sorted(tasks, key=lambda item: item.task_id):
            suite_digest.update(
                f"{task.task_id}:{task.definition_sha256}\n".encode("ascii")
            )
        self.suite_definition_sha256 = suite_digest.hexdigest()
        self.records: dict[str, dict[str, Any]] = {}
        if self.summary_path.is_file():
            previous = json.loads(self.summary_path.read_text(encoding="utf-8"))
            previous_harness = previous.get("harness")
            if previous_harness == "openclaw-docker-local":
                previous_harness = "openclaw"
            previous_image = previous.get("harness_image") or previous.get(
                "openclaw_image"
            )
            expected = {
                "model": self.model,
                "base_url": self.base_url,
                "model_service_id": self.model_service_id,
                "harness": self.harness_id,
                "provider_timeout_policy": self.provider_timeout_policy,
                "harness_image": self.image,
                "harness_protocol": self.harness_protocol,
                "harness_version": self.harness_version,
                "trace_complete": self.trace_complete,
                "runtime_images": self.runtime_images,
                "isolation": self.isolation,
                "suite_definition_sha256": self.suite_definition_sha256,
                "suite_snapshot_sha256": self.suite_snapshot_sha256,
                "controller_definition_sha256": (self.controller_definition_sha256),
                "controller_runtime": self.controller_runtime,
                "controller_runtime_id": self.controller_runtime_id,
                "grading": self.grading,
                "max_tokens": self.max_tokens,
                "experimental_task_environments": self.experimental_task_environments,
                "task_environment_set_sha256": self.task_environment_set_sha256,
                "environment_builder_id": TASK_ENVIRONMENT_BUILDER_ID if self.experimental_task_environments else None,
                "context_window": self.context_window,
                "temperature": self.temperature,
                "data_parallel_rank": self.data_parallel_rank,
                "thinking": self.thinking,
                "timeout_override": self.timeout_override,
                "max_agent_turns": self.max_agent_turns,
                "shard_index": self.shard_index,
                "shard_count": self.shard_count,
                "task_set": self.task_set_name,
                "selection": self.selection_identity,
            }
            previous_values = {
                **previous,
                "harness": previous_harness,
                "harness_image": previous_image,
                "harness_protocol": previous.get("harness_protocol")
                or "chat_completions",
                "harness_version": previous.get("harness_version")
                or ("2026.5.26" if previous_harness == "openclaw" else None),
            }
            mismatches = {
                key: {"previous": previous_values.get(key), "requested": value}
                for key, value in expected.items()
                if previous_values.get(key) != value
            }
            previous_selected = {
                str(row["task_id"])
                for row in previous.get("tasks", [])
                if row.get("status") != "not_selected"
            }
            if previous_selected != self.selected_task_ids:
                mismatches["selected_task_ids"] = {
                    "previous": sorted(previous_selected),
                    "requested": sorted(self.selected_task_ids),
                }
            previous_task_hashes = {
                str(row.get("task_id")): row.get("task_definition_sha256")
                for row in previous.get("tasks", [])
                if row.get("task_id")
            }
            current_task_hashes = {
                task_id: task.definition_sha256
                for task_id, task in self.task_defs.items()
            }
            changed_task_hashes = sorted(
                task_id
                for task_id, current_hash in current_task_hashes.items()
                if previous_task_hashes.get(task_id) != current_hash
            )
            if changed_task_hashes:
                mismatches["task_definition_sha256"] = {
                    "changed_or_missing_task_ids": changed_task_hashes,
                }
            if mismatches:
                raise ValueError(
                    "cannot resume a run with different evaluation semantics: "
                    + json.dumps(mismatches, ensure_ascii=False, sort_keys=True)
                )
            self.workers = max(
                self.workers,
                int(previous.get("workers") or self.workers),
            )
            for row in previous.get("tasks", []):
                if row.get("task_id") in self.task_defs:
                    self.records[str(row["task_id"])] = dict(row)
            self.started_epoch = float(
                previous.get("started_epoch") or self.started_epoch
            )
        for task in tasks:
            row = self.records.setdefault(
                task.task_id,
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "domain": task.domain,
                    "difficulty": task.difficulty,
                    "task_definition_sha256": task.definition_sha256,
                    "status": (
                        "queued"
                        if task.task_id in self.selected_task_ids
                        else "not_selected"
                    ),
                    "outcome_score": None,
                    **self.task_environments.get(task.task_id, {}),
                    "task_success": None,
                    "elapsed_sec": None,
                    "required_artifacts": None,
                    "safety_gate_failed": False,
                    "failed_gate_count": 0,
                    "failed_core_count": 0,
                    "review_flag": "",
                    "error": "",
                },
            )
            # Older checkpoints predate formal business-case metrics.  Keep
            # them resumable and dashboard-readable with explicit defaults.
            row.setdefault("safety_gate_failed", False)
            row.setdefault("failed_gate_count", 0)
            row.setdefault("failed_core_count", 0)
            if row.get("status") == "running":
                row["status"] = "queued"
        self.write()

    def pending_tasks(
        self,
        tasks: list[BenchmarkTask],
        *,
        retry_infrastructure: bool = False,
    ) -> list[BenchmarkTask]:
        return [
            task
            for task in tasks
            if self.records[task.task_id].get("status") not in TERMINAL_STATUSES
            or (
                retry_infrastructure
                and self.records[task.task_id].get("status") == "infrastructure_error"
            )
        ]

    def update(self, task_id: str, **changes: Any) -> None:
        with self.lock:
            self.records[task_id].update(changes)
            self._write_unlocked()

    def write(self) -> None:
        with self.lock:
            self._write_unlocked()

    def _write_unlocked(self) -> None:
        rows = [self.records[task_id] for task_id in sorted(self.records)]
        completed_scores = [
            float(row["outcome_score"])
            for row in rows
            if row.get("status") in SCOREABLE_STATUSES
            and isinstance(row.get("outcome_score"), (int, float))
            and row.get("task_id") in self.selected_task_ids
        ]
        completed_success = [
            bool(row["task_success"])
            for row in rows
            if row.get("status") in SCOREABLE_STATUSES
            and isinstance(row.get("task_success"), bool)
            and row.get("task_id") in self.selected_task_ids
        ]
        scoreable_rows = [
            row
            for row in rows
            if row.get("status") in SCOREABLE_STATUSES
            and row.get("task_id") in self.selected_task_ids
        ]
        safety_gate_failed_count = sum(
            row.get("safety_gate_failed") is True for row in scoreable_rows
        )
        failed_gate_total = sum(
            value
            for row in scoreable_rows
            if type(value := row.get("failed_gate_count")) is int and value >= 0
        )
        failed_core_total = sum(
            value
            for row in scoreable_rows
            if type(value := row.get("failed_core_count")) is int and value >= 0
        )
        statuses = (
            "not_selected",
            "queued",
            "running",
            "completed",
            "protocol_error",
            "model_error",
            "timeout",
            "infrastructure_error",
        )
        status_counts = {
            status: sum(row.get("status") == status for row in rows)
            for status in statuses
        }
        terminal_count = sum(
            row.get("status") in TERMINAL_STATUSES
            and row.get("task_id") in self.selected_task_ids
            for row in rows
        )
        selected_count = len(self.selected_task_ids)
        elapsed = max(0.0, time.time() - self.started_epoch)
        eta = (
            elapsed / terminal_count * (selected_count - terminal_count)
            if terminal_count
            else None
        )
        provider_traces = [
            row["provider_trace"]
            for row in rows
            if row.get("task_id") in self.selected_task_ids
            and isinstance(row.get("provider_trace"), dict)
        ]
        provider_token_keys = (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
            "total_tokens",
        )
        provider_length_keys = (
            "provider_transactions",
            "completed_transactions",
            "request_context_events",
            "response_events",
            "user_events",
            "assistant_events",
            "tool_calls",
            "tool_results",
            "raw_request_bytes",
            "raw_response_bytes",
        )
        provider_token_usage = {
            key: sum(
                int((trace.get("token_usage") or {}).get(key, 0) or 0)
                for trace in provider_traces
            )
            for key in provider_token_keys
        }
        provider_trajectory_length = {
            key: sum(
                int((trace.get("trajectory_length") or {}).get(key, 0) or 0)
                for trace in provider_traces
            )
            for key in provider_length_keys
        }
        provider_usage_eligible = sum(
            int(trace.get("requests_eligible_for_usage", 0) or 0)
            for trace in provider_traces
        )
        provider_usage_observed = sum(
            int(trace.get("requests_with_usage", 0) or 0)
            for trace in provider_traces
        )
        provider_reasoning = [
            trace.get("reasoning")
            for trace in provider_traces
            if isinstance(trace.get("reasoning"), dict)
        ]
        provider_reasoning_summary = {
            "requested": self.thinking,
            "controlled_requests": sum(
                int(item.get("controlled_requests", 0) or 0)
                for item in provider_reasoning
            ),
            "observed_requests": sum(
                int(item.get("observed_requests", 0) or 0)
                for item in provider_reasoning
            ),
            "replay_required_transitions": sum(
                int(item.get("replay_required_transitions", 0) or 0)
                for item in provider_reasoning
            ),
            "replay_eligible_transitions": sum(
                int(item.get("replay_eligible_transitions", 0) or 0)
                for item in provider_reasoning
            ),
            "replayed_transitions": sum(
                int(item.get("replayed_transitions", 0) or 0)
                for item in provider_reasoning
            ),
            "lost_transitions": sum(
                int(item.get("lost_transitions", 0) or 0)
                for item in provider_reasoning
            ),
            "strict_eligible_tasks": sum(
                item.get("strict_eligible") is True for item in provider_reasoning
            ),
            "audited_tasks": len(provider_reasoning),
        }
        payload = {
            "schema_version": 1,
            "benchmark": "HarnessBench",
            "suite": "main",
            "suite_size": BENCHMARK_TASK_COUNT,
            "task_set": self.task_set_name,
            "selection": self.selection_identity,
            "suite_definition_sha256": self.suite_definition_sha256,
            "suite_snapshot_sha256": self.suite_snapshot_sha256,
            "controller_definition_sha256": (self.controller_definition_sha256),
            "controller_runtime": self.controller_runtime,
            "controller_runtime_id": self.controller_runtime_id,
            "selected_count": selected_count,
            "shard_index": self.shard_index,
            "shard_count": self.shard_count,
            "experimental_task_environments": self.experimental_task_environments,
            "task_environment_set_sha256": self.task_environment_set_sha256,
            "environment_builder_id": TASK_ENVIRONMENT_BUILDER_ID if self.experimental_task_environments else None,
            "run_id": self.run_root.name,
            "grading": self.grading,
            "model": self.model,
            "model_service_id": self.model_service_id,
            "harness": self.harness_id,
            "provider_timeout_policy": self.provider_timeout_policy,
            "harness_version": self.harness_version,
            "harness_protocol": self.harness_protocol,
            "harness_profile": "controlled-core",
            "base_url": self.base_url,
            "harness_image": self.image,
            "openclaw_image": (self.image if self.harness_id == "openclaw" else None),
            "trace_complete": self.trace_complete,
            "runtime_images": self.runtime_images,
            "isolation": self.isolation,
            "workers": self.workers,
            "max_tokens": self.max_tokens,
            "context_window": self.context_window,
            "temperature": self.temperature,
            "data_parallel_rank": self.data_parallel_rank,
            "thinking": self.thinking,
            "provider_reasoning": provider_reasoning_summary,
            "timeout_override": self.timeout_override,
            "max_agent_turns": self.max_agent_turns,
            "started_epoch": self.started_epoch,
            "updated_epoch": time.time(),
            "elapsed_sec": round(elapsed, 3),
            "eta_sec": round(eta, 3) if eta is not None else None,
            "counts": status_counts,
            "completed_count": terminal_count,
            "progress": (
                round(terminal_count / selected_count, 4) if selected_count else 1.0
            ),
            "mean_oracle_completed": (
                round(statistics.mean(completed_scores), 4)
                if completed_scores
                else None
            ),
            "median_oracle_completed": (
                round(statistics.median(completed_scores), 4)
                if completed_scores
                else None
            ),
            "pass_rate_completed": (
                round(sum(completed_success) / len(completed_success), 4)
                if completed_success
                else None
            ),
            "strict_pass_evaluated_count": len(completed_success),
            "strict_passed_count": sum(completed_success),
            "safety_gate_evaluated_count": len(scoreable_rows),
            "safety_gate_failed_count": safety_gate_failed_count,
            "safety_gate_failure_rate": (
                round(safety_gate_failed_count / len(scoreable_rows), 4)
                if scoreable_rows
                else None
            ),
            "failed_gate_total": failed_gate_total,
            "failed_core_total": failed_core_total,
            "provider_trace": {
                "schema_version": 1,
                "sessions_recorded": len(provider_traces),
                "sessions_expected": selected_count,
                "requests_with_usage": provider_usage_observed,
                "requests_eligible_for_usage": provider_usage_eligible,
                "token_coverage": (
                    round(provider_usage_observed / provider_usage_eligible, 6)
                    if provider_usage_eligible
                    else (1.0 if provider_traces else 0.0)
                ),
                "token_usage": provider_token_usage,
                "trajectory_length": provider_trajectory_length,
            },
            "headline_task_success_rate": (
                round(sum(completed_success) / selected_count, 4)
                if selected_count
                and terminal_count == selected_count
                and len(completed_success) == selected_count
                and status_counts["infrastructure_error"] == 0
                else None
            ),
            "headline_oracle_score": (
                round(sum(completed_scores) / selected_count, 4)
                if selected_count
                and terminal_count == selected_count
                and status_counts["infrastructure_error"] == 0
                else None
            ),
            "tasks": rows,
        }
        atomic_json(self.summary_path, payload)
        atomic_summary_script(self.summary_script_path, payload)


def stage_openclaw_state(
    *,
    state_volume: str,
    config_path: Path,
    labels: list[str],
) -> None:
    docker(
        "run",
        "--rm",
        *labels,
        "--mount",
        f"type=volume,src={state_volume},dst=/state",
        "--mount",
        f"type=bind,src={config_path.parent},dst=/source,readonly",
        STAGER_IMAGE,
        "sh",
        "-c",
        "cp /source/openclaw.json /state/openclaw.json && "
        "cp /source/gitconfig /state/.gitconfig && "
        "chown -R 1000:1000 /state && chmod -R u+rwX,go-rwx /state",
    )


def stage_contract_state(
    *,
    state_volume: str,
    controller_dir: Path,
    labels: list[str],
) -> None:
    """Give the fixed non-root harness user isolated writable state."""
    docker(
        "run",
        "--rm",
        *labels,
        "--mount",
        f"type=volume,src={state_volume},dst=/state",
        "--mount",
        f"type=bind,src={controller_dir},dst=/source,readonly",
        STAGER_IMAGE,
        "sh",
        "-c",
        "mkdir -p /state/home && cp /source/gitconfig /state/gitconfig && "
        "chown -R 1000:1000 /state && chmod -R u+rwX,go-rwx /state",
    )


def export_openclaw_trace(
    *,
    state_volume: str,
    destination: Path,
    labels: list[str],
) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    safe_chmod(destination, 0o777)
    docker(
        "run",
        "--rm",
        *labels,
        "--mount",
        f"type=volume,src={state_volume},dst=/state,readonly",
        "--mount",
        f"type=bind,src={destination},dst=/export",
        STAGER_IMAGE,
        "sh",
        "-c",
        "test -d /state/agents/main/sessions && "
        "cp -a /state/agents/main/sessions/. /export/ && "
        "chmod -R a+rX /export",
    )
    files = [
        path.relative_to(destination).as_posix()
        for path in sorted(destination.rglob("*"))
        if path.is_file()
    ]
    if not files:
        raise RuntimeError("OpenClaw session export produced no files")
    return files


def validate_openclaw_trace(
    destination: Path,
    trace_files: list[str],
) -> dict[str, Any]:
    """Validate persisted tool-call/result pairing in raw OpenClaw sessions."""

    session_files = [
        relative
        for relative in trace_files
        if relative.endswith(".jsonl") and not relative.endswith(".trajectory.jsonl")
    ]
    problems: list[str] = []
    event_count = 0
    call_count = 0
    result_count = 0
    failure_count = 0

    if not session_files:
        problems.append("no raw OpenClaw session JSONL file")

    for relative in session_files:
        path = destination / relative
        pending: set[str] = set()
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                problems.append(f"{relative}:{line_number}: invalid JSON ({exc.msg})")
                continue
            event_count += 1
            if event.get("type") != "message":
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                problems.append(
                    f"{relative}:{line_number}: message event is not an object"
                )
                continue
            role = message.get("role")
            if role == "assistant":
                if pending:
                    problems.append(
                        f"{relative}:{line_number}: assistant resumed before "
                        f"{len(pending)} tool result(s)"
                    )
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != "toolCall":
                        continue
                    tool_call_id = str(item.get("id") or "")
                    if not tool_call_id:
                        problems.append(
                            f"{relative}:{line_number}: tool call has no id"
                        )
                        continue
                    call_count += 1
                    if tool_call_id in pending:
                        problems.append(
                            f"{relative}:{line_number}: duplicate active tool call id "
                            f"{tool_call_id}"
                        )
                    pending.add(tool_call_id)
            elif role == "toolResult":
                tool_call_id = str(message.get("toolCallId") or "")
                result_count += 1
                details = (
                    message.get("details")
                    if isinstance(message.get("details"), dict)
                    else {}
                )
                detail_status = str(details.get("status") or "").strip().casefold()
                exit_code = details.get("exitCode")
                if (
                    message.get("isError") is True
                    or detail_status
                    in {
                        "aborted",
                        "error",
                        "failed",
                        "killed",
                        "timeout",
                        "timed_out",
                    }
                    or (
                        isinstance(exit_code, int)
                        and not isinstance(exit_code, bool)
                        and exit_code != 0
                    )
                ):
                    failure_count += 1
                if not tool_call_id:
                    problems.append(
                        f"{relative}:{line_number}: tool result has no call id"
                    )
                elif tool_call_id not in pending:
                    problems.append(
                        f"{relative}:{line_number}: orphan tool result id "
                        f"{tool_call_id}"
                    )
                else:
                    pending.remove(tool_call_id)
        if pending:
            problems.append(
                f"{relative}: EOF with {len(pending)} dangling tool call(s)"
            )

    return {
        "status": "pass" if not problems else "fail",
        "session_files": len(session_files),
        "events": event_count,
        "tool_calls": call_count,
        "tool_results": result_count,
        "tool_failures": failure_count,
        "tool_successes": result_count - failure_count,
        "problems": problems,
    }


def normalize_agent_owned_paths(
    *,
    workspace: Path,
    runtime: Path,
    labels: list[str],
    writable_by_controller: bool,
) -> None:
    """Normalize files created by fixed UID 1000 on shared/NFS storage.

    The controller account can traverse the bind mount but may not chmod a
    mode-0640 file created by the harness UID.  A tiny network-disabled helper
    runs as that same UID and adjusts only paths it owns.  Hooks temporarily
    need write access; the final workspace is reduced to read-only-for-others
    before grading and result inspection.
    """
    mode = "a+rwX" if writable_by_controller else "a+rX,go-w"
    docker(
        "run",
        "--rm",
        *labels,
        *constrained_run_args(),
        "--user",
        "1000:1000",
        "--mount",
        f"type=bind,src={workspace},dst=/workspace",
        "--mount",
        f"type=bind,src={runtime},dst=/runtime",
        STAGER_IMAGE,
        "sh",
        "-c",
        agent_tree_normalization_script(mode),
        "harnessbench-normalize",
        "/workspace",
        "/runtime",
    )


def agent_tree_normalization_script(mode: str) -> str:
    """Return the fixed helper script used at the untrusted/trusted boundary."""
    if mode not in {"a+rwX", "a+rX,go-w"}:
        raise ValueError(f"unsupported normalization mode: {mode}")
    return (
        "set -eu\n"
        'unsafe_paths="/tmp/harnessbench-unsafe-paths"\n'
        'find "$1" "$2" -xdev '
        '\\( ! -type d -a ! -type f \\) -print -quit > "$unsafe_paths"\n'
        'if [ -s "$unsafe_paths" ]; then\n'
        '  printf "unsafe agent-owned path: " >&2\n'
        '  head -n 1 "$unsafe_paths" >&2\n'
        "  exit 86\n"
        "fi\n"
        f'find "$1" "$2" -xdev -uid 1000 -exec chmod \'{mode}\' {{}} +\n'
    )


def run_agent_round(
    *,
    task: BenchmarkTask,
    prompt: str,
    round_index: int,
    workspace: Path,
    runtime: Path,
    state_volume: str,
    image: str,
    session_id: str,
    timeout: int,
    thinking: str,
    labels: list[str],
    network: str = "bridge",
) -> subprocess.CompletedProcess[str]:
    token = uuid.uuid4().hex[:8]
    agent_name = f"hb-agent-{task.task_id[:3]}-r{round_index + 1}-{token}"
    command = [
        "run",
        "--name",
        agent_name,
        *labels,
        "--network",
        network,
        "--dns",
        "127.0.0.1",
        # The official image healthcheck probes the long-running gateway.
        # Local ``openclaw agent`` mode intentionally starts no gateway, so
        # inheriting that probe would label a healthy task container
        # ``unhealthy`` while it is still generating.
        "--no-healthcheck",
        "--add-host",
        "host.docker.internal:host-gateway",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "512",
        "--memory",
        "4g",
        "--cpus",
        "2",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=256m",
        "--user",
        "1000:1000",
        "--mount",
        f"type=bind,src={workspace},dst=/workspace",
        "--mount",
        f"type=bind,src={runtime},dst=/runtime",
        "--mount",
        f"type=volume,src={state_volume},dst=/home/node/.openclaw",
        "--env",
        "HOME=/home/node",
        "--env",
        "OPENCLAW_HOME=/home/node/.openclaw",
        "--env",
        "OPENCLAW_STATE_DIR=/home/node/.openclaw",
        "--env",
        "OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.json",
        "--env",
        "GIT_CONFIG_GLOBAL=/home/node/.openclaw/.gitconfig",
        "--env",
        "NO_PROXY=*",
        "--env",
        "no_proxy=*",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint",
        "node",
        image,
        "openclaw.mjs",
        "agent",
        "--local",
        "--json",
        "--session-id",
        session_id,
        "--timeout",
        str(timeout),
        "--thinking",
        thinking,
        "--message",
        prompt,
    ]
    try:
        return docker(*command, check=False, timeout=timeout + 60)
    except subprocess.TimeoutExpired as exc:
        docker("rm", "--force", agent_name, check=False)
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return subprocess.CompletedProcess(
            args=exc.cmd,
            returncode=124,
            stdout=stdout,
            stderr=stderr + "\ncontroller timeout expired",
        )
    finally:
        docker("rm", "--force", agent_name, check=False)


def run_grader(
    *,
    task: BenchmarkTask,
    workspace: Path,
    runtime: Path,
    grader_control: Path,
    labels: list[str],
    image: str = GRADER_IMAGE,
) -> dict[str, Any]:
    completed = docker(
        "run",
        "--rm",
        *labels,
        *constrained_run_args(),
        "--user",
        # The agent is already gone and both task mounts are read-only. Reuse
        # its unprivileged UID so the oracle can read 0600/0640 outputs on NFS,
        # where the host controller may not own (and cannot chmod) those files.
        "1000:1000",
        "--mount",
        f"type=bind,src={workspace},dst=/workspace,readonly",
        "--mount",
        f"type=bind,src={runtime},dst=/runtime,readonly",
        "--mount",
        f"type=bind,src={grader_control},dst=/grader-control,readonly",
        "--mount",
        f"type=bind,src={task.task_dir},dst=/hidden,readonly",
        "--env",
        f"HARNESSBENCH_TASK_ID={task.task_id}",
        "--env",
        "HARNESSBENCH_REQUIRED_ARTIFACTS="
        + json.dumps(task.metadata.get("required_artifacts", [])),
        "--env",
        "HARNESSBENCH_MUTATION_REPORT=workspace-mutation.json",
        "--env",
        "HARNESSBENCH_ORACLE_MODULE=" + str(task.metadata.get("oracle_module") or ""),
        "--env",
        "HARNESSBENCH_ORACLE_SPEC=" + str(task.metadata.get("oracle_spec") or ""),
        "--env",
        "HARNESSBENCH_OUTCOME_TEST=" + str(task.metadata.get("outcome_test") or ""),
        "--env",
        "HARNESSBENCH_CASES_JSON="
        + (
            json.dumps(task.cases_manifest, ensure_ascii=False, separators=(",", ":"))
            if task.cases_manifest is not None
            else ""
        ),
        "--env",
        "HARNESSBENCH_SEMANTIC_JUDGE_JSON="
        + (
            json.dumps(
                task.metadata.get("semantic_judge"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if task.metadata.get("semantic_judge") is not None
            else ""
        ),
        "--env",
        "GIT_CONFIG_COUNT=1",
        "--env",
        "GIT_CONFIG_KEY_0=safe.directory",
        "--env",
        "GIT_CONFIG_VALUE_0=*",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        image,
        timeout=300,
    )
    return dict(json.loads(completed.stdout))


def attach_semantic_judge(
    *,
    task: BenchmarkTask,
    oracle_result: dict[str, Any],
    base_url: str,
    agent_model: str,
    data_parallel_rank: int | None,
) -> None:
    """Run one judge call and apply only an explicitly bounded case fallback."""

    raw_config = task.metadata.get("semantic_judge")
    if raw_config is None:
        return
    config = validate_judge_config(raw_config)
    request = oracle_result.pop("semantic_judge_request", None)
    if not isinstance(request, dict):
        raise RuntimeError("grader did not return the semantic judge request")
    candidate = request.get("candidate")
    unsafe = bool(
        oracle_result.get("oracle_contract_failure")
        or oracle_result.get("failed_gates")
        or oracle_result.get("failed_contract_gates")
    )
    native_valid = False
    if isinstance(candidate, dict):
        # An explicit task-specific eligibility decision is authoritative.
        # Falling back to native-document validity is only for builders that do
        # not expose a narrower semantic boundary.
        if "judge_eligible" in candidate:
            native_valid = candidate.get("judge_eligible") is True
        else:
            native_valid = candidate.get("valid_native_document") is True
    if unsafe or not native_valid:
        oracle_result["semantic_judge"] = {
            "schema_version": 1,
            "status": "skipped",
            "score_role": config["score_role"],
            "model": (
                agent_model
                if config["default_model"] in {"", "agent"}
                else config["default_model"]
            ),
            "model_source": (
                "agent" if config["default_model"] in {"", "agent"} else "task_override"
            ),
            "reason": (
                "oracle or safety gate failed"
                if unsafe
                else "candidate is not eligible for semantic fallback"
            ),
        }
        return
    case_checks: dict[str, dict[str, Any]] = {}
    if config["score_role"] == "bounded_case_fallback":
        checks = oracle_result.get("checks")
        if not isinstance(checks, list):
            raise RuntimeError("bounded semantic fallback requires formal case checks")
        case_checks = {
            str(check.get("id"))[5:]: check
            for check in checks
            if isinstance(check, dict) and str(check.get("id", "")).startswith("case:")
        }
        mapped_checks: list[dict[str, Any]] = []
        for case_id in dict.fromkeys(config["case_map"].values()):
            check = case_checks.get(case_id)
            if check is None:
                raise RuntimeError("semantic fallback references a missing case")
            if check.get("gate") is True or float(check.get("weight") or 0.0) <= 0.0:
                raise RuntimeError(
                    "semantic fallback cannot target a gate or zero-weight case"
                )
            mapped_checks.append(check)
        if (
            sum(float(check.get("weight") or 0.0) for check in mapped_checks)
            > config["max_fallback_weight"] + 1e-9
        ):
            raise RuntimeError("semantic fallback exceeds its declared weight bound")
        if all(check.get("pass") is True for check in mapped_checks):
            oracle_result["semantic_judge"] = {
                "schema_version": 1,
                "status": "skipped",
                "score_role": config["score_role"],
                "model": (
                    agent_model
                    if config["default_model"] in {"", "agent"}
                    else config["default_model"]
                ),
                "model_source": (
                    "agent"
                    if config["default_model"] in {"", "agent"}
                    else "task_override"
                ),
                "reason": "all mapped deterministic cases already passed",
            }
            oracle_result["semantic_fallback_applied"] = []
            return
    semantic_result = run_semantic_judge(
        request,
        base_url=base_url,
        agent_model=agent_model,
        configured_model=config["default_model"],
        data_parallel_rank=data_parallel_rank,
    )
    oracle_result["semantic_judge"] = semantic_result
    if (
        config["score_role"] != "bounded_case_fallback"
        or semantic_result.get("status") != "completed"
    ):
        return

    verdicts = {
        str(item.get("id")): item
        for item in semantic_result.get("verdicts", [])
        if isinstance(item, dict)
    }
    applied: list[dict[str, Any]] = []
    promoted_weight = 0.0
    criteria_by_case: dict[str, list[str]] = {}
    for criterion_id, case_id in config["case_map"].items():
        criteria_by_case.setdefault(case_id, []).append(criterion_id)
    for case_id, criterion_ids in criteria_by_case.items():
        check = case_checks.get(case_id)
        case_verdicts = [verdicts.get(criterion_id) for criterion_id in criterion_ids]
        if check is None or any(verdict is None for verdict in case_verdicts):
            raise RuntimeError("semantic fallback references a missing verdict or case")
        if check.get("pass") is True or any(
            verdict.get("verdict") != "pass" for verdict in case_verdicts
        ):
            continue
        weight = float(check.get("weight") or 0.0)
        if promoted_weight + weight > config["max_fallback_weight"] + 1e-9:
            raise RuntimeError("semantic fallback exceeds its declared weight bound")
        deterministic_detail = check.get("detail")
        check["pass"] = True
        check["detail"] = {
            "deterministic_result": deterministic_detail,
            "semantic_fallback": [
                {
                    "criterion_id": criterion_id,
                    "evidence": verdict.get("evidence"),
                    "reason": verdict.get("reason"),
                }
                for criterion_id, verdict in zip(
                    criterion_ids, case_verdicts, strict=True
                )
            ],
        }
        promoted_weight += weight
        applied.append(
            {
                "criterion_ids": criterion_ids,
                "case_id": case_id,
                "weight": weight,
            }
        )

    oracle_result["semantic_fallback_applied"] = applied
    if not applied:
        return
    oracle_result["deterministic_outcome_score"] = oracle_result.get("outcome_score")
    weighted_score, failed_core, failed_gates = aggregate_case_checks(
        list(case_checks.values())
    )
    oracle_result["weighted_score"] = weighted_score
    oracle_result["outcome_score"] = 0.0 if failed_gates else weighted_score
    oracle_result["failed_core_cases"] = failed_core
    oracle_result["failed_gates"] = failed_gates
    threshold = float(oracle_result.get("pass_threshold") or 0.8)
    oracle_result["task_success"] = bool(
        weighted_score + 1e-9 >= threshold and not failed_core and not failed_gates
    )


def review_flag(
    *,
    status: str,
    score: float | None,
    task_success: bool | None,
    artifacts: dict[str, Any] | None,
    error: str,
    safety_gate_failed: bool = False,
    failed_core_count: int = 0,
) -> str:
    if status == "infrastructure_error":
        return "infra_failure"
    if safety_gate_failed:
        return "safety_gate_failure"
    if failed_core_count > 0:
        return "core_case_failure"
    if status == "protocol_error":
        return "tool_protocol_failure"
    if status == "timeout":
        return "timeout_review"
    if status == "model_error":
        return "model_or_harness_failure"
    if artifacts and artifacts.get("missing"):
        return "missing_artifacts"
    if task_success is False:
        return "below_pass_threshold"
    if score is not None and score < 0.15:
        return "low_score_review"
    if error:
        return "execution_review"
    return ""


def run_one(
    task: BenchmarkTask,
    *,
    state: SuiteState,
    base_url: str,
    model: str,
    image: str,
    harness_spec: HarnessSpec | None,
    timeout_override: int | None,
    max_tokens: int,
    temperature: float,
    data_parallel_rank: int | None,
    thinking: str,
    context_window: int = 200000,
    max_agent_turns: int = 150,
    upstream_api_key: str | None = None,
    resolved_environment: ResolvedTaskEnvironment | None = None,
    task_environment_spec: TaskEnvironmentSpec | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    environment_identity: dict[str, Any] = {}
    grader_image = GRADER_IMAGE
    if (resolved_environment is None) != (task_environment_spec is None):
        raise ValueError("resolved environment and environment spec must be paired")
    if resolved_environment is not None:
        environment_identity = resolved_environment.as_dict()
        image = resolved_environment.combined_image
        grader_image = resolved_environment.task_grader_image

    artifact = state.run_root / "tasks" / task.task_id
    # A non-terminal trial is always restarted from a pristine task state.
    # Completed result directories are never submitted to this function.
    shutil.rmtree(artifact, ignore_errors=True)
    workspace = artifact / "workspace"
    runtime = artifact / "runtime"
    prompts_dir = artifact / "prompts"
    controller_dir = artifact / "controller"
    grader_control_dir = artifact / "grader-control"
    for path in (
        workspace,
        runtime,
        prompts_dir,
        controller_dir,
        grader_control_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    stage_workspace_seed(task, workspace)
    make_agent_writable(workspace)
    make_agent_writable(runtime)

    state.update(
        task.task_id,
        status="running",
        started_epoch=time.time(),
        safety_gate_failed=False,
        failed_gate_count=0,
        failed_core_count=0,
        review_flag="",
    )
    token = uuid.uuid4().hex[:10]
    session_id = f"hb-{task.task_id[:3]}-{token}"
    state_volume = f"hb-state-{task.task_id[:3]}-{token}"
    labels = [
        "--label",
        "org.harnessbench.benchmark=true",
        "--label",
        f"org.harnessbench.run_id={state.run_root.name}",
        "--label",
        f"org.harnessbench.task_id={task.task_id}",
    ]
    hooks_name = str(task.metadata.get("hooks_module") or "")
    hooks = (
        load_module(task.task_dir / hooks_name, "benchmark_hooks")
        if hooks_name
        else None
    )
    context = hook_context(task, workspace, runtime, labels)
    service_runtime: TrialServiceRuntime | None = None
    if task_environment_spec is not None:
        assert resolved_environment is not None
        service_runtime = TrialServiceRuntime(
            spec=task_environment_spec,
            resolved=resolved_environment,
            task_id=task.task_id,
            run_id=state.run_root.name,
            token=token,
            task_dir=task.task_dir,
            workspace=workspace,
            artifact=artifact,
            labels=labels,
            docker_fn=docker,
        )
        context["start_local_sidecar"] = service_runtime.launch_local
        context["check_local_sidecar"] = service_runtime.check_healthy
        context["snapshot_local_sidecar"] = service_runtime.snapshot
    trial_network = "bridge"
    runtime_state: dict[str, Any] = {}
    environment_prepare: dict[str, Any] = {"status": "not_declared"}
    rounds: list[dict[str, Any]] = []
    round_mutations: list[dict[str, Any]] = []
    protected_workspace_paths: set[str] = set()
    mutation_report: dict[str, Any] = aggregate_workspace_mutations(
        [],
        contract=task.metadata.get("mutation_contract"),
    )
    score: dict[str, Any] = {}
    status = "completed"
    error = ""
    cleanup_error = ""
    trace_error = ""
    turn_limit_reached = False
    trace_files: list[str] = []
    trace_integrity: dict[str, Any] = {
        "status": "not_checked",
        "reason": "no agent round completed",
    }
    round_timeout = timeout_override or task.timeout_sec
    model_proxy = ModelBudgetProxy(
        base_url,
        max_agent_turns=max_agent_turns,
        temperature=temperature,
        max_output_tokens=max_tokens,
        timeout_sec=round_timeout,
        thinking=thinking,
        trace_dir=artifact / "provider-trace",
        session_id=session_id,
        upstream_api_key=upstream_api_key,
    )
    trial_base_url = model_proxy.start()
    try:
        if harness_spec is None:
            config_path = controller_dir / "openclaw.json"
            config_text = (
                json.dumps(
                    openclaw_config(
                        trial_base_url,
                        model,
                        max_tokens,
                        temperature,
                        data_parallel_rank,
                        round_timeout,
                        context_window=context_window,
                        thinking=thinking,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
        else:
            config_path = controller_dir / harness_spec.config_name
            config_text = render_harness_config(
                harness_spec,
                base_url=trial_base_url,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking=thinking,
                context_window=context_window,
                max_agent_turns=max_agent_turns,
            )
    except Exception:
        model_proxy.close()
        raise
    config_path.write_text(config_text, encoding="utf-8")
    (controller_dir / "gitconfig").write_text(
        "[safe]\n"
        "\tdirectory = /workspace\n"
        "\tdirectory = /runtime\n"
        "\tdirectory = /runtime/remote.git\n",
        encoding="utf-8",
    )
    make_readable(controller_dir)

    try:
        if service_runtime is not None:
            service_runtime.create_network()
            trial_network = service_runtime.network_name or "bridge"
        if task_environment_spec is not None:
            assert resolved_environment is not None
            environment_prepare = run_task_environment_prepare(
                spec=task_environment_spec,
                resolved=resolved_environment,
                workspace=workspace,
                runtime=runtime,
                labels=labels,
            )
        runtime_state.update(prepare_hook(hooks, context))
        ensure_workspace_root(workspace)
        normalize_container_runtime_paths(workspace, runtime, runtime_state)
        protected_workspace_paths = set(workspace_manifest(workspace))
        make_agent_writable(workspace)
        make_agent_writable(runtime)
        docker("volume", "create", *labels, state_volume)
        if harness_spec is None:
            stage_openclaw_state(
                state_volume=state_volume,
                config_path=config_path,
                labels=labels,
            )
        else:
            stage_contract_state(
                state_volume=state_volume,
                controller_dir=controller_dir.resolve(),
                labels=labels,
            )
        for round_index, prompt_name in enumerate(task.prompt_files):
            ensure_workspace_root(workspace)
            before_round = workspace_manifest(workspace)
            prompt = render_prompt(
                task,
                prompt_name,
                workspace,
                runtime,
                runtime_state,
            )
            prompt_file = prompts_dir / f"round-{round_index + 1}.txt"
            prompt_file.write_text(prompt, encoding="utf-8")
            if harness_spec is None:
                agent = run_agent_round(
                    task=task,
                    prompt=prompt,
                    round_index=round_index,
                    workspace=workspace.resolve(),
                    runtime=runtime.resolve(),
                    state_volume=state_volume,
                    image=image,
                    session_id=session_id,
                    timeout=round_timeout,
                    thinking=thinking,
                    labels=labels,
                    network=trial_network,
                )
                payload = parse_agent_payload(agent.stdout)
                meta = (
                    payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
                )
                protocol = protocol_report(payload, stderr=agent.stderr)
            else:
                (controller_dir / "prompt.txt").write_text(
                    prompt,
                    encoding="utf-8",
                )
                make_readable(controller_dir)
                trace_round = artifact / "trace" / f"round-{round_index + 1}"
                trace_round.mkdir(parents=True, exist_ok=True)
                make_agent_writable(trace_round)
                agent_name = (
                    f"hb-{harness_spec.harness_id}-"
                    f"{task.task_id[:3]}-r{round_index + 1}-{token[:6]}"
                )
                agent = run_contract_round(
                    spec=harness_spec,
                    container_name=agent_name,
                    image=image,
                    workspace=workspace.resolve(),
                    runtime=runtime.resolve(),
                    state_volume=state_volume,
                    controller_dir=controller_dir.resolve(),
                    trace_dir=trace_round.resolve(),
                    timeout=round_timeout,
                    labels=labels,
                    network=trial_network,
                )
                meta = {}
                protocol = contract_protocol_report(
                    agent.stdout,
                    agent.stderr,
                    agent.returncode,
                )
            round_record = {
                "round_index": round_index,
                "prompt_file": prompt_name,
                "returncode": agent.returncode,
                "elapsed_ms": meta.get("durationMs"),
                "protocol": protocol,
                "tool_summary": meta.get("toolSummary"),
                "usage": (meta.get("agentMeta") or {}).get("usage"),
                "stdout": agent.stdout,
                "stderr": agent.stderr,
            }
            rounds.append(round_record)
            # Normalize ownership before the trusted controller snapshots the
            # result. The helper never follows agent-created links.
            quiesce_environment(
                hooks,
                context,
                runtime_state,
                round_index,
            )
            ensure_workspace_root(workspace)
            normalize_agent_owned_paths(
                workspace=workspace.resolve(),
                runtime=runtime.resolve(),
                labels=labels,
                writable_by_controller=True,
            )
            after_round = workspace_manifest(workspace)
            trusted_mutations = environment_mutations(
                hooks,
                context,
                runtime_state,
                round_index,
            )
            mutation = measure_agent_workspace_mutation(
                before_round,
                after_round,
                contract=task.metadata.get("mutation_contract"),
                trusted=trusted_mutations,
                protected_paths=protected_workspace_paths,
            )
            mutation["round_index"] = round_index
            round_record["workspace_mutation"] = mutation
            round_mutations.append(mutation)
            mutation_report = aggregate_workspace_mutations(
                round_mutations,
                contract=task.metadata.get("mutation_contract"),
            )
            formal_outcome_task = bool(
                str(task.metadata.get("outcome_test") or "").strip()
            )
            mutation_blocks_round = (
                not mutation["safe"]
                if formal_outcome_task
                else not mutation["complete"]
            )
            if mutation_blocks_round:
                error = (
                    f"agent round {round_index + 1} violated the protected "
                    "workspace safety boundary"
                    if formal_outcome_task
                    else f"agent round {round_index + 1} violated the workspace "
                    "mutation contract"
                )
                break
            round_failure = agent_round_failure_status(
                agent.returncode,
                protocol,
            )
            if round_failure == "infrastructure_error":
                status = "infrastructure_error"
                phase = protocol.get("timeout_phase")
                if protocol.get("provider_request_timeout"):
                    detail = f"provider request timed out during {phase or 'request'}"
                else:
                    detail = "provider response stream failed"
                error = f"agent round {round_index + 1} {detail}"
                break
            if round_failure == "timeout":
                status = "timeout"
                phase = protocol.get("timeout_phase")
                phase_detail = f" during {phase}" if phase else ""
                error = f"agent round {round_index + 1} timed out{phase_detail}"
                break
            if round_failure in {"model_error", "protocol_error"} and (
                provider_turn_budget_exhausted(
                    model_proxy,
                    max_agent_turns=max_agent_turns,
                )
            ):
                # The harness asked for generation 151 and the common
                # controller enforced the declared limit. Preserve and grade
                # the terminal workspace as a bounded trial rather than
                # mislabeling the expected stop as a model or protocol outage.
                turn_limit_reached = True
                round_record["termination_reason"] = "max_agent_turns"
                break
            if round_failure == "model_error":
                status = "model_error"
                error = f"agent round {round_index + 1} exited {agent.returncode}"
                break
            if round_failure == "protocol_error":
                status = round_failure
                if protocol.get("non_deliverable_terminal_turn"):
                    detail = "a non-deliverable terminal state"
                elif protocol.get("dangling_tool_call"):
                    detail = "an unexecuted tool call"
                else:
                    detail = "an aborted protocol"
                error = f"agent round {round_index + 1} ended with {detail}"
                break
            before_post_round_hook = workspace_manifest(workspace)
            after_round_hook(
                hooks,
                context,
                runtime_state,
                round_index,
                prompt_name,
                prompt_file,
                agent,
            )
            ensure_workspace_root(workspace)
            normalize_agent_owned_paths(
                workspace=workspace.resolve(),
                runtime=runtime.resolve(),
                labels=labels,
                writable_by_controller=True,
            )
            after_post_round_hook = workspace_manifest(workspace)
            if str(task.metadata.get("outcome_test") or "").strip():
                if (
                    before_post_round_hook != after_post_round_hook
                    and round_index + 1 < len(task.prompt_files)
                ):
                    raise RuntimeError(
                        "formal after_round hooks may publish trusted workspace "
                        "state only after the final agent round"
                    )
                declared_post_round = environment_mutations(
                    hooks,
                    context,
                    runtime_state,
                    round_index,
                    callback_name="post_round_environment_mutations",
                )
                post_round_report = measure_agent_workspace_mutation(
                    before_post_round_hook,
                    after_post_round_hook,
                    contract={
                        "allowed_created": [],
                        "allowed_modified": [],
                        "allowed_deleted": [],
                    },
                    trusted=declared_post_round,
                )
                mutation["post_round_environment_mutations"] = post_round_report[
                    "environment_mutations"
                ]
                if not post_round_report["complete"]:
                    raise RuntimeError(
                        "trusted after_round hook changed the formal workspace "
                        "outside post_round_environment_mutations"
                    )
            make_agent_writable(workspace)
            make_agent_writable(runtime)
    except Exception as exc:
        status = "infrastructure_error"
        error = f"{type(exc).__name__}: {exc}"
    finally:
        model_proxy.close()
        cleanup_workspace_before: dict[str, dict[str, object]] | None = None
        cleanup_failure: Exception | None = None
        try:
            # Security violations skip hook cleanup: a trusted hook must never
            # be invoked on redirected agent-owned workspace paths.
            ensure_workspace_root(workspace)
            normalize_agent_owned_paths(
                workspace=workspace.resolve(),
                runtime=runtime.resolve(),
                labels=labels,
                writable_by_controller=True,
            )
            if str(task.metadata.get("outcome_test") or "").strip():
                cleanup_workspace_before = workspace_manifest(workspace)
            cleanup_hook(hooks, context, runtime_state)
        except Exception as exc:
            cleanup_failure = exc
        if service_runtime is not None:
            try:
                service_runtime.cleanup()
            except Exception as exc:
                if cleanup_failure is None:
                    cleanup_failure = exc
                else:
                    cleanup_failure = RuntimeError(
                        f"{type(cleanup_failure).__name__}: {cleanup_failure}; "
                        f"sidecar cleanup failed: {type(exc).__name__}: {exc}"
                    )

        # Cleanup is allowed to tear down private services and runtime state,
        # never to rewrite the candidate that is about to be graded.  Without
        # this second snapshot a trusted cleanup hook could bypass the
        # content-addressed after_round boundary and have its replacement
        # silently blessed by the final workspace hash.
        if cleanup_workspace_before is not None:
            try:
                ensure_workspace_root(workspace)
                cleanup_workspace_after = workspace_manifest(workspace)
                enforce_cleanup_workspace_immutability(
                    cleanup_workspace_before,
                    cleanup_workspace_after,
                )
            except Exception as exc:
                if cleanup_failure is None:
                    cleanup_failure = exc
                else:
                    cleanup_failure = RuntimeError(
                        f"{type(cleanup_failure).__name__}: {cleanup_failure}; "
                        f"cleanup workspace guard failed: {type(exc).__name__}: {exc}"
                    )

        if cleanup_failure is not None:
            cleanup_error = f"{type(cleanup_failure).__name__}: {cleanup_failure}"
            if status != "infrastructure_error":
                prior_status = status
                prior_error = error
                status = "infrastructure_error"
                error = f"hook cleanup failed after {prior_status}: {cleanup_error}" + (
                    f"; prior error: {prior_error}" if prior_error else ""
                )

    if status != "infrastructure_error":
        try:
            mutation_report = aggregate_workspace_mutations(
                round_mutations,
                contract=task.metadata.get("mutation_contract"),
            )
            final_workspace_manifest = workspace_manifest(workspace)
            mutation_report["final_workspace_sha256"] = manifest_sha256(
                final_workspace_manifest
            )
            mutation_report["final_workspace_entry_count"] = len(
                final_workspace_manifest
            )
            atomic_json(
                grader_control_dir / "workspace-mutation.json",
                mutation_report,
            )
            make_readable(grader_control_dir)
            normalize_agent_owned_paths(
                workspace=workspace.resolve(),
                runtime=runtime.resolve(),
                labels=labels,
                writable_by_controller=False,
            )
            # Normalize read/traverse bits where the filesystem permits it.
            # NFS may reject chmod because the controller does not own files
            # created by the agent; run_grader also reuses the agent UID.
            make_readable(workspace)
            make_readable(runtime)
            score = run_grader(
                task=task,
                workspace=workspace.resolve(),
                runtime=runtime.resolve(),
                grader_control=grader_control_dir.resolve(),
                labels=labels,
                image=grader_image,
            )
            attach_semantic_judge(
                task=task,
                oracle_result=score,
                base_url=base_url,
                agent_model=model,
                data_parallel_rank=data_parallel_rank,
            )
            oracle_failure = grader_contract_error(score)
            if oracle_failure:
                status = "infrastructure_error"
                error = f"grader oracle failed: {oracle_failure}"
        except Exception as exc:
            status = "infrastructure_error"
            error = f"grader failed: {type(exc).__name__}: {exc}"

    if rounds and harness_spec is None:
        try:
            trace_files = export_openclaw_trace(
                state_volume=state_volume,
                destination=artifact / "trace",
                labels=labels,
            )
            trace_integrity = validate_openclaw_trace(
                artifact / "trace",
                trace_files,
            )
            if trace_integrity["status"] != "pass" and status == "completed":
                trace_error = "OpenClaw trace integrity validation failed"
                status = "infrastructure_error"
                error = f"{error}; {trace_error}" if error else trace_error
        except Exception as exc:
            trace_error = f"{type(exc).__name__}: {exc}"
            status = "infrastructure_error"
            error = (
                f"{error}; trace export failed: {trace_error}"
                if error
                else f"trace export failed: {trace_error}"
            )
    elif rounds:
        trace_files = collect_trace_files(artifact / "trace")
        if not trace_files:
            trace_error = "contract harness produced no trace files"
            status = "infrastructure_error"
            error = f"{error}; {trace_error}" if error else trace_error
        else:
            trace_integrity = {
                "status": "not_checked",
                "reason": "harness-native trace schema",
            }

    docker("volume", "rm", "--force", state_volume, check=False)
    provider_trace = model_proxy.trace_summary or {}
    expected_provider_transactions = (
        model_proxy.model_requests + model_proxy.rejected_requests
    )
    observed_provider_transactions = int(
        (provider_trace.get("trajectory_length") or {}).get(
            "provider_transactions", 0
        )
    )
    if observed_provider_transactions != expected_provider_transactions:
        trace_problem = (
            "provider trace transaction count mismatch: "
            f"observed={observed_provider_transactions}, "
            f"expected={expected_provider_transactions}"
        )
        status = "infrastructure_error"
        error = f"{error}; {trace_problem}" if error else trace_problem
    provider_problem = provider_forward_failure(model_proxy)
    if provider_problem is not None:
        status = "infrastructure_error"
        error = (
            f"{error}; {provider_problem}"
            if error and provider_problem not in error
            else provider_problem
        )
    artifacts = authoritative_artifact_report(task, workspace, score)
    numeric_score = score.get("outcome_score")
    outcome = float(numeric_score) if isinstance(numeric_score, (int, float)) else None
    task_success = score.get("task_success")
    task_success = task_success if isinstance(task_success, bool) else None
    case_failure_metrics = oracle_case_failure_metrics(score)
    reported_tool_calls = sum(
        int((row.get("tool_summary") or {}).get("calls") or 0) for row in rounds
    )
    reported_tool_failures = sum(
        int((row.get("tool_summary") or {}).get("failures") or 0) for row in rounds
    )
    if harness_spec is None and isinstance(trace_integrity.get("tool_calls"), int):
        tool_calls = int(trace_integrity["tool_calls"])
        tool_results = int(trace_integrity.get("tool_results") or 0)
        tool_failures = int(trace_integrity.get("tool_failures") or 0)
        tool_telemetry_source = "validated_openclaw_trace"
        trace_complete: bool | None = trace_integrity.get("status") == "pass"
    else:
        tool_calls = reported_tool_calls
        tool_results = None
        tool_failures = reported_tool_failures
        tool_telemetry_source = "harness_reported"
        trace_complete = (
            None
            if trace_integrity.get("status") == "not_checked"
            else trace_integrity.get("status") == "pass"
        )
    trace_complete_expected = (
        harness_spec.trace_complete if harness_spec is not None else True
    )
    record = {
        "schema_version": 1,
        "task_id": task.task_id,
        "title": task.title,
        "domain": task.domain,
        "difficulty": task.difficulty,
        "task_definition_sha256": task.definition_sha256,
        "status": status,
        **environment_identity,
        "environment_prepare": environment_prepare,
        "grading": task_grading_mode(task),
        "semantic_judge": score.get("semantic_judge"),
        "harness": (
            harness_spec.harness_id if harness_spec is not None else "openclaw"
        ),
        "harness_version": (
            harness_spec.version if harness_spec is not None else "2026.5.26"
        ),
        "harness_protocol": (
            harness_spec.protocol if harness_spec is not None else "chat_completions"
        ),
        "harness_image": image,
        "trace_complete": trace_complete,
        "trace_complete_expected": trace_complete_expected,
        "outcome_score": outcome,
        "task_success": task_success,
        **case_failure_metrics,
        "elapsed_sec": round(time.monotonic() - started, 3),
        "required_artifacts": artifacts,
        "workspace_mutation": mutation_report,
        "round_count": len(rounds),
        "round_timeout_sec": round_timeout,
        "max_agent_turns": max_agent_turns,
        "turn_limit_reached": turn_limit_reached,
        "provider_request_count": model_proxy.model_requests,
        "provider_rejected_request_count": model_proxy.rejected_requests,
        "provider_forward_error_count": model_proxy.forward_errors,
        "provider_last_error": model_proxy.last_error,
        "provider_downstream_disconnect_count": model_proxy.downstream_disconnects,
        "provider_last_downstream_disconnect": (
            model_proxy.last_downstream_disconnect
        ),
        "provider_trace": provider_trace,
        "provider_trace_files": [
            "provider-trace/transactions.jsonl",
            "provider-trace/trajectory.jsonl",
            "provider-trace/session-summary.json",
        ],
        "provider_timeout_sec": (round_timeout if harness_spec is None else None),
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "tool_failures": tool_failures,
        "tool_telemetry_source": tool_telemetry_source,
        "harness_reported_tool_calls": reported_tool_calls,
        "harness_reported_tool_failures": reported_tool_failures,
        "runtime_state": {
            key: value
            for key, value in runtime_state.items()
            if isinstance(value, (str, int, float, bool))
        },
        "task_services": (
            service_runtime.report() if service_runtime is not None else None
        ),
        "isolation": state.isolation,
        "oracle": score,
        "rounds": rounds,
        "trace_files": trace_files,
        "trace_integrity": trace_integrity,
        "trace_error": trace_error,
        "error": error,
        "cleanup_error": cleanup_error,
    }
    record["review_flag"] = review_flag(
        status=status,
        score=outcome,
        task_success=task_success,
        artifacts=artifacts,
        error=error,
        safety_gate_failed=record["safety_gate_failed"],
        failed_core_count=record["failed_core_count"],
    )
    atomic_json(artifact / "result.json", record)
    make_readable(artifact)
    state.update(
        task.task_id,
        status=status,
        outcome_score=outcome,
        task_success=task_success,
        safety_gate_failed=record["safety_gate_failed"],
        failed_gate_count=record["failed_gate_count"],
        failed_core_count=record["failed_core_count"],
        elapsed_sec=record["elapsed_sec"],
        required_artifacts=artifacts,
        round_count=record["round_count"],
        round_timeout_sec=record["round_timeout_sec"],
        provider_timeout_sec=record["provider_timeout_sec"],
        provider_request_count=record["provider_request_count"],
        provider_rejected_request_count=record["provider_rejected_request_count"],
        provider_forward_error_count=record["provider_forward_error_count"],
        provider_downstream_disconnect_count=(
            record["provider_downstream_disconnect_count"]
        ),
        provider_trace=record["provider_trace"],
        provider_trace_files=record["provider_trace_files"],
        tool_calls=record["tool_calls"],
        tool_failures=record["tool_failures"],
        review_flag=record["review_flag"],
        error=error,
        result_path=str(artifact / "result.json"),
        finished_epoch=time.time(),
    )
    return record


def copy_dashboard(run_root: Path) -> None:
    destination = run_root / "dashboard"
    destination.mkdir(parents=True, exist_ok=True)
    safe_chmod(run_root, 0o755)
    safe_chmod(destination, 0o755)
    for name in ("index.html", "app.js", "styles.css"):
        source = DASHBOARD_SOURCE / name
        if not source.is_file():
            raise RuntimeError(f"missing dashboard asset: {source}")
        shutil.copy2(source, destination / name)
        safe_chmod(destination / name, 0o644)


def ensure_image(
    image: str,
    *,
    offline_aliases: dict[str, str] | None = None,
) -> str:
    """Resolve a pinned image, including a verified air-gapped archive alias."""
    present = docker("image", "inspect", image, check=False)
    if present.returncode == 0:
        return image
    for alias, expected_id in (offline_aliases or {}).items():
        inspected = docker(
            "image",
            "inspect",
            alias,
            "--format",
            "{{.Id}}",
            check=False,
        )
        if inspected.returncode == 0 and inspected.stdout.strip() == expected_id:
            print(
                f"using verified offline image alias {alias} "
                f"for {image} ({expected_id})"
            )
            return alias
    docker("pull", image, capture=False)
    return image


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a frozen HarnessBench Docker evaluation"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--upstream-api-key-file",
        help=(
            "trusted-controller-only file containing the upstream API key; "
            "the key is injected by the per-trial proxy and never passed to "
            "the harness or recorded in provider traces"
        ),
    )
    parser.add_argument(
        "--model-service-id",
        help=(
            "stable identity for one equivalently configured model deployment; "
            "required to merge shards that use different replica URLs"
        ),
    )
    parser.add_argument(
        "--harness",
        choices=("openclaw", *sorted(OCI_HARNESS_SPECS)),
        default="openclaw",
        help="Dockerized harness implementation (default: openclaw)",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--max-tokens", type=int, default=32768)
    parser.add_argument("--context-window", type=int, default=200000)
    parser.add_argument("--max-agent-turns", type=int, default=150)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--data-parallel-rank", type=int)
    parser.add_argument(
        "--thinking",
        choices=("off", "minimal", "low", "medium", "high", "xhigh"),
        default="high",
    )
    parser.add_argument("--openclaw-image", default=DEFAULT_OPENCLAW_IMAGE)
    parser.add_argument(
        "--harness-image",
        help="override the selected non-OpenClaw OCI image",
    )
    parser.add_argument("--run-root")
    parser.add_argument(
        "--experimental-task-environments",
        action="store_true",
        help="build and verify explicit per-task runtime environments",
    )
    parser.add_argument(
        "--suite-root",
        default=str(ROOT),
        help=(
            "suite directory containing tasks/ and catalog/ "
            f"(default: {ROOT})"
        ),
    )
    selection_group = parser.add_mutually_exclusive_group()
    selection_group.add_argument("--tasks", help="optional comma-separated subset")
    selection_group.add_argument(
        "--task-set",
        metavar="NAME",
        type=validate_task_set_name,
        help="named subset loaded from catalog/NAME.yaml",
    )
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument(
        "--retry-infrastructure",
        action="store_true",
        help="rerun trials previously marked infrastructure_error",
    )
    args = parser.parse_args()
    try:
        active_task_set = _configure_suite_root(Path(args.suite_root))
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as exc:
        raise SystemExit(f"invalid --suite-root: {exc}") from exc
    if args.tasks is None and args.task_set is None:
        args.task_set = active_task_set
    if args.workers < 1:
        raise SystemExit("--workers must be positive")
    if args.context_window < 1:
        raise SystemExit("--context-window must be positive")
    if args.max_agent_turns < 1:
        raise SystemExit("--max-agent-turns must be positive")
    if not 0 <= args.temperature <= 2:
        raise SystemExit("--temperature must be in [0, 2]")
    if args.data_parallel_rank is not None and args.data_parallel_rank < 0:
        raise SystemExit("--data-parallel-rank must be non-negative")
    if args.model_service_id and not re.fullmatch(
        r"sha256:[0-9a-f]{64}",
        args.model_service_id,
    ):
        raise SystemExit(
            "--model-service-id must be the sha256 identity printed by "
            "tools/freeze_model_service.py"
        )
    upstream_api_key: str | None = None
    if args.upstream_api_key_file:
        try:
            upstream_api_key = (
                Path(args.upstream_api_key_file).read_text(encoding="utf-8").strip()
            )
        except OSError as exc:
            raise SystemExit(f"cannot read --upstream-api-key-file: {exc}") from exc
        if not upstream_api_key:
            raise SystemExit("--upstream-api-key-file is empty")
        if "\r" in upstream_api_key or "\n" in upstream_api_key:
            raise SystemExit("--upstream-api-key-file must contain one line")
    if args.shard_count < 1:
        raise SystemExit("--shard-count must be positive")
    if not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("--shard-index must be in [0, --shard-count)")

    run_root = Path(args.run_root).expanduser().resolve() if args.run_root else None

    try:
        preflight_tasks_dir = TASKS_DIR
        if run_root is not None and (run_root / "suite_snapshot").exists():
            preflight_tasks_dir, _, _ = freeze_suite_snapshot(run_root)
        preflight_catalog_dir = CATALOG_DIR
        if args.task_set is not None and run_root is not None:
            frozen_catalog = run_root / "selection" / "catalog"
            if (frozen_catalog / f"{args.task_set}.yaml").is_file():
                preflight_catalog_dir = frozen_catalog
        if args.task_set is not None and not (
            preflight_catalog_dir / f"{args.task_set}.yaml"
        ).is_file():
            raise RuntimeError(
                "task-set manifest does not exist: "
                f"{preflight_catalog_dir / f'{args.task_set}.yaml'}"
            )
        live_tasks = load_benchmark_tasks(preflight_tasks_dir)
        preflight_selection = resolve_benchmark_selection(
            live_tasks,
            requested_tasks=args.tasks,
            task_set_name=args.task_set,
            catalog_dir=preflight_catalog_dir,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"task selection preflight failed: {exc}") from exc

    controller_runtime, controller_runtime_id = controller_runtime_identity()
    require_docker()
    isolation = docker_security_identity()
    harness_spec: HarnessSpec | None = None
    if args.harness == "openclaw":
        requested_image = args.openclaw_image
        harness_version = "2026.5.26"
        harness_protocol = "chat_completions"
        trace_complete = True
        if requested_image == DEFAULT_OPENCLAW_IMAGE:
            build_openclaw_image()
            image = requested_image
        else:
            image = ensure_image(
                requested_image,
                offline_aliases=(
                    OPENCLAW_OFFLINE_IMAGE_ALIASES
                    if requested_image == OFFICIAL_OPENCLAW_IMAGE
                    else None
                ),
            )
    else:
        harness_spec = get_harness_spec(args.harness)
        requested_image = args.harness_image or harness_spec.image
        image = requested_image
        harness_version = harness_spec.version
        harness_protocol = harness_spec.protocol
        trace_complete = harness_spec.trace_complete
        if args.harness_image:
            image = ensure_image(image)
    ensure_image(STAGER_IMAGE)
    ensure_image(GRADER_BASE_IMAGE)
    runtime_images = build_images()
    if harness_spec is not None and not args.harness_image:
        runtime_images[image] = build_harness_image(harness_spec)
    else:
        resolved_image_id = docker(
            "image", "inspect", image, "--format", "{{.Id}}"
        ).stdout.strip()
        runtime_images[requested_image] = resolved_image_id
        if image != requested_image:
            runtime_images[image] = resolved_image_id
    if run_root is None:
        model_slug = "".join(
            char if char.isalnum() or char in "._-" else "-"
            for char in args.model.lower()
        ).strip("-")[:48]
        run_id = (
            time.strftime("%Y%m%d-%H%M%S")
            + "-"
            + (model_slug or "model")
            + "-"
            + args.harness
            + "-harnessbench"
        )
        run_root = EVALUATIONS_DIR / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    safe_chmod(EVALUATIONS_DIR, 0o755)
    safe_chmod(run_root, 0o755)
    tasks_root = run_root / "tasks"
    tasks_root.mkdir(parents=True, exist_ok=True)
    safe_chmod(tasks_root, 0o755)
    frozen_catalog_dir = CATALOG_DIR
    if preflight_selection.task_set is not None:
        try:
            frozen_catalog_dir = freeze_task_set_manifest(
                run_root,
                preflight_selection,
            )
        except (OSError, ValueError) as exc:
            raise SystemExit(f"cannot freeze task selection: {exc}") from exc
    copy_dashboard(run_root)
    (
        snapshot_tasks,
        snapshot_sha256,
        controller_sha256,
    ) = freeze_suite_snapshot(run_root)
    all_tasks = load_benchmark_tasks(snapshot_tasks)
    try:
        frozen_selection = resolve_benchmark_selection(
            all_tasks,
            requested_tasks=args.tasks,
            task_set_name=args.task_set,
            catalog_dir=frozen_catalog_dir,
        )
    except ValueError as exc:
        raise SystemExit(f"frozen task selection is invalid: {exc}") from exc
    if frozen_selection.identity != preflight_selection.identity:
        raise SystemExit("task selection changed between preflight and frozen snapshot")
    tasks = list(frozen_selection.tasks)
    grading = suite_grading_mode(tasks)
    task_environment_specs: dict[str, TaskEnvironmentSpec] = {}
    resolved_task_environments: dict[str, ResolvedTaskEnvironment] = {}
    task_environment_records: dict[str, dict[str, Any]] = {}
    task_environment_set_sha256: str | None = None
    if args.experimental_task_environments:
        environment_digest = hashlib.sha256()
        builder = TaskEnvironmentBuilder()
        try:
            for task in tasks:
                spec = load_task_environment(task.task_dir, task.metadata)
                resolved = builder.resolve(
                    spec=spec,
                    harness_image=image,
                    grader_image=GRADER_IMAGE,
                )
                task_environment_specs[task.task_id] = spec
                resolved_task_environments[task.task_id] = resolved
                task_environment_records[task.task_id] = resolved.as_dict()
                environment_digest.update(
                    f"{task.task_id}:{spec.sha256}\n".encode("ascii")
                )
                runtime_images[resolved.combined_image] = resolved.combined_image_id
                runtime_images[resolved.task_grader_image] = (
                    resolved.task_grader_image_id
                )
        except (OSError, ValueError, TaskEnvironmentError) as exc:
            raise SystemExit(f"task environment preflight failed: {exc}") from exc
        task_environment_set_sha256 = environment_digest.hexdigest()
    tasks = [
        task
        for index, task in enumerate(tasks)
        if index % args.shard_count == args.shard_index
    ]
    state = SuiteState(
        run_root=run_root,
        tasks=all_tasks,
        model=args.model,
        base_url=args.base_url,
        model_service_id=args.model_service_id or args.base_url,
        image=requested_image,
        harness_id=args.harness,
        harness_protocol=harness_protocol,
        harness_version=harness_version,
        trace_complete=trace_complete,
        workers=args.workers,
        max_tokens=args.max_tokens,
        context_window=args.context_window,
        temperature=args.temperature,
        data_parallel_rank=args.data_parallel_rank,
        thinking=args.thinking,
        timeout_override=args.timeout,
        max_agent_turns=args.max_agent_turns,
        runtime_images=runtime_images,
        isolation=isolation,
        selected_task_ids={task.task_id for task in tasks},
        shard_index=args.shard_index,
        shard_count=args.shard_count,
        suite_snapshot_sha256=snapshot_sha256,
        controller_sha256=controller_sha256,
        controller_runtime=controller_runtime,
        controller_runtime_id=controller_runtime_id,
        grading=grading,
        task_set_name=args.task_set,
        selection_identity=frozen_selection.identity,
        experimental_task_environments=args.experimental_task_environments,
        task_environment_set_sha256=task_environment_set_sha256,
        task_environments=task_environment_records,
    )
    pending = state.pending_tasks(
        tasks,
        retry_infrastructure=args.retry_infrastructure,
    )
    print(
        json.dumps(
            {
                "run_root": str(run_root),
                "dashboard": str(run_root / "dashboard" / "index.html"),
                "suite_size": BENCHMARK_TASK_COUNT,
                "task_set": args.task_set,
                "selection": frozen_selection.identity,
                "selected": len(tasks),
                "pending": len(pending),
                "workers": args.workers,
                "context_window": args.context_window,
                "max_agent_turns": args.max_agent_turns,
                "shard_index": args.shard_index,
                "shard_count": args.shard_count,
                "grading": grading,
                "isolation": isolation,
                "controller_runtime_id": controller_runtime_id,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    if not pending:
        return

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.workers,
        thread_name_prefix="hb",
    ) as executor:
        futures = {
            executor.submit(
                run_one,
                task,
                state=state,
                base_url=args.base_url,
                model=args.model,
                image=image,
                harness_spec=harness_spec,
                timeout_override=args.timeout,
                max_tokens=args.max_tokens,
                context_window=args.context_window,
                temperature=args.temperature,
                data_parallel_rank=args.data_parallel_rank,
                thinking=args.thinking,
                max_agent_turns=args.max_agent_turns,
                upstream_api_key=upstream_api_key,
                resolved_environment=resolved_task_environments.get(task.task_id),
                task_environment_spec=task_environment_specs.get(task.task_id),
            ): task
            for task in pending
        }
        for future in concurrent.futures.as_completed(futures):
            task = futures[future]
            try:
                record = future.result()
                print(
                    f"[{task.task_id}] {record['status']} "
                    f"oracle={record.get('outcome_score')} "
                    f"elapsed={record['elapsed_sec']}s",
                    flush=True,
                )
            except Exception as exc:
                error = f"worker crashed: {type(exc).__name__}: {exc}"
                state.update(
                    task.task_id,
                    status="infrastructure_error",
                    error=error,
                    review_flag="infra_failure",
                    finished_epoch=time.time(),
                )
                print(f"[{task.task_id}] {error}", file=sys.stderr, flush=True)
    state.write()
    print(json.dumps(json.loads(state.summary_path.read_text()), ensure_ascii=False))


if __name__ == "__main__":
    main()
