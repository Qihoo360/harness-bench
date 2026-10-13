#!/usr/bin/env python3
"""Regrade a preserved HarnessBench run without regenerating trajectories.

Two modes are supported:

``merged`` (default)
    A completed merged public run is copied into a new audit run and current
    deterministic outcome tests are applied to the frozen workspaces.  Existing
    compatible Judge records are reused, while newly configured bounded Judges
    may be called explicitly.

``--docker``
    A preserved partial run produced by ``tools/run_benchmark.py`` is copied
    into a resumable output run.  Every existing scoreable result is regraded
    inside the trusted grader image against the frozen task snapshot, the
    frozen task-definition identities are validated first, and the output
    summary is rewritten for the current controller revision so that
    ``run_benchmark.py`` can later resume only the unfinished tasks.  Agent
    trajectories, workspaces, provider traces, execution settings and the old
    scores are preserved; the source run is never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
MAINTENANCE = TOOLS / "task_maintenance"
for path in (SRC, TOOLS, MAINTENANCE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import run_benchmark  # noqa: E402

from common import score_task_workspace  # noqa: E402
from harnessbench.grading.outcome_cases import aggregate_case_checks  # noqa: E402
from harnessbench.grading.semantic_judge import (  # noqa: E402
    JUDGE_SCHEMA_VERSION,
    parse_judge_response,
    run_semantic_judge,
    validate_judge_config,
    validate_judge_request,
    validate_verdict_evidence,
)


SCOREABLE = {"completed", "protocol_error", "model_error", "timeout"}
GRADING_LABEL = "deterministic_oracle_with_bounded_semantic_fallback_regraded"
RUNTIME_BOUND_REGRADES = {
    # The final workspace contains a .git indirection into the per-run
    # /runtime bare repository. Merged public artifacts intentionally do not
    # retain that environment state, so local post-hoc execution cannot
    # reproduce the original Git assertions.
    "003-git-pr-merge": "ephemeral_git_runtime_not_retained",
    # The durable checkpoint records the device/inode observed inside the
    # original task container. Exporting or copying the workspace necessarily
    # assigns a different filesystem identity, so post-hoc grading must retain
    # the score established in the live container.
    "135-log-rotation-checkpoint-recovery": "container_inode_identity_not_portable",
}

LEGACY_DEFINITION_BOUND_REGRADES = {
    # This completed run predates the protected .env.production seed fixture.
    # Applying the newer seed baseline to the old frozen workspace creates a
    # false source-deletion gate; newer task definitions remain regradable.
    "133-dockerignore-build-context-repair": {
        "4d09ee679589fab4a9d02fd7b7b3ff79ea63be6236129a36211b86b709db63d4": (
            "frozen_workspace_predates_protected_seed_fixture"
        ),
    },
}


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _judge_request(
    task_dir: Path,
    metadata: dict[str, Any],
    workspace: Path,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    raw_config = metadata.get("semantic_judge")
    if raw_config is None:
        return None
    config = validate_judge_config(raw_config)
    rubric = yaml.safe_load(
        (task_dir / config["rubric_file"]).read_text(encoding="utf-8")
    )
    builder_module = _load_module(
        task_dir / config["input_builder"],
        f"regrade_judge_{metadata['task_id'].replace('-', '_')}",
    )
    builder = getattr(builder_module, "build_semantic_judge_input", None)
    if not callable(builder):
        raise RuntimeError(f"{metadata['task_id']}: semantic builder is missing")
    request = {
        "schema_version": JUDGE_SCHEMA_VERSION,
        "task_id": metadata["task_id"],
        "score_role": config["score_role"],
        "case_map": config["case_map"],
        "rubric": rubric,
        "candidate": builder(workspace),
    }
    normalized = validate_judge_request(request)
    normalized.pop("criterion_ids", None)
    return config, normalized


def _compatible_cached_judge(
    cached: object,
    request: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(cached, Mapping) or cached.get("status") != "completed":
        return None
    verdicts = cached.get("verdicts")
    if not isinstance(verdicts, list):
        return None
    expected_ids = [str(item["id"]) for item in request["rubric"]["criteria"]]
    actual_ids = [str(item.get("id")) for item in verdicts if isinstance(item, Mapping)]
    if actual_ids != expected_ids:
        return None
    validate_verdict_evidence(verdicts, candidate=request["candidate"])
    return dict(cached)


def _reparsed_cached_judge(
    cached: object,
    request: dict[str, Any],
) -> dict[str, Any] | None:
    """Revalidate a complete stored model response with the current parser.

    This permits parser-only fixes to recover an immutable Judge response without
    making a second model call.  Evidence is always validated against the current
    candidate, and criterion IDs must still match exactly.
    """
    if not isinstance(cached, Mapping) or cached.get("status") != "error":
        return None
    raw_output = cached.get("raw_output")
    if not isinstance(raw_output, str) or not raw_output.strip():
        return None
    expected_ids = [str(item["id"]) for item in request["rubric"]["criteria"]]
    try:
        verdicts = parse_judge_response(raw_output, criterion_ids=expected_ids)
        validate_verdict_evidence(verdicts, candidate=request["candidate"])
    except Exception:
        return None
    repaired = dict(cached)
    repaired["status"] = "completed"
    repaired["verdicts"] = verdicts
    repaired["reparsed_from_status"] = "error"
    repaired["prior_error"] = repaired.pop("error", None)
    repaired["reparsed_with_current_validator"] = True
    return repaired


def _apply_semantic_result(
    oracle: dict[str, Any],
    *,
    config: dict[str, Any],
    semantic_result: dict[str, Any],
) -> None:
    oracle["semantic_judge"] = semantic_result
    oracle["semantic_fallback_applied"] = []
    if (
        config["score_role"] != "bounded_case_fallback"
        or semantic_result.get("status") != "completed"
    ):
        return
    checks = oracle.get("checks")
    if not isinstance(checks, list):
        raise RuntimeError("bounded semantic fallback requires formal case checks")
    cases = {
        str(check.get("id"))[5:]: check
        for check in checks
        if isinstance(check, dict) and str(check.get("id", "")).startswith("case:")
    }
    verdicts = {
        str(item.get("id")): item
        for item in semantic_result.get("verdicts", [])
        if isinstance(item, dict)
    }
    criteria_by_case: dict[str, list[str]] = defaultdict(list)
    for criterion_id, case_id in config["case_map"].items():
        criteria_by_case[case_id].append(criterion_id)

    applied: list[dict[str, Any]] = []
    promoted_weight = 0.0
    for case_id, criterion_ids in criteria_by_case.items():
        check = cases.get(case_id)
        selected = [verdicts.get(criterion_id) for criterion_id in criterion_ids]
        if check is None or any(verdict is None for verdict in selected):
            raise RuntimeError(f"semantic fallback has no case/verdict for {case_id}")
        if check.get("pass") is True or any(
            verdict.get("verdict") != "pass" for verdict in selected
        ):
            continue
        weight = float(check.get("weight") or 0.0)
        if promoted_weight + weight > config["max_fallback_weight"] + 1e-9:
            raise RuntimeError("semantic fallback exceeds its declared bound")
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
                for criterion_id, verdict in zip(criterion_ids, selected, strict=True)
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
    oracle["semantic_fallback_applied"] = applied
    if not applied:
        return
    oracle["deterministic_outcome_score"] = oracle.get("outcome_score")
    weighted, failed_core, failed_gates = aggregate_case_checks(list(cases.values()))
    oracle["weighted_score"] = weighted
    oracle["outcome_score"] = 0.0 if failed_gates else weighted
    oracle["failed_core_cases"] = failed_core
    oracle["failed_gates"] = failed_gates
    threshold = float(oracle.get("pass_threshold") or 0.8)
    oracle["task_success"] = bool(
        weighted + 1e-9 >= threshold and not failed_core and not failed_gates
    )


def _scoring_revision(task_dir: Path, metadata: dict[str, Any]) -> str:
    paths = [
        task_dir / "task.yaml",
        task_dir / str(metadata.get("cases_file") or "tests/cases.yaml"),
        task_dir / str(metadata.get("outcome_test") or "tests/test_outcome.py"),
    ]
    ground_truth = task_dir / "tests" / "ground_truth"
    if ground_truth.is_dir():
        paths.extend(
            path
            for path in ground_truth.rglob("*")
            if path.is_file()
            and path.suffix != ".pyc"
            and "__pycache__" not in path.parts
        )
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        relative = path.relative_to(task_dir).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _review_flag(result: dict[str, Any]) -> str:
    if result.get("safety_gate_failed") is True:
        return "safety_gate_failure"
    if int(result.get("failed_core_count") or 0) > 0:
        return "core_case_failure"
    if result.get("status") == "timeout":
        return "timeout_review"
    if result.get("status") in {"protocol_error", "model_error"}:
        return "model_or_harness_failure"
    if result.get("task_success") is True:
        return ""
    return "below_threshold"


def _refresh_summary(summary: dict[str, Any], *, output: Path) -> None:
    selected = [row for row in summary["tasks"] if row.get("status") != "not_selected"]
    scoreable = [row for row in selected if row.get("status") in SCOREABLE]
    terminal = [
        row
        for row in selected
        if row.get("status")
        in {
            "completed",
            "protocol_error",
            "model_error",
            "timeout",
            "infrastructure_error",
        }
    ]
    scores = [
        float(row["outcome_score"])
        for row in scoreable
        if isinstance(row.get("outcome_score"), (int, float))
    ]
    successes = [
        bool(row["task_success"])
        for row in scoreable
        if isinstance(row.get("task_success"), bool)
    ]
    summary["run_id"] = output.name
    summary["updated_epoch"] = time.time()
    summary["grading"] = GRADING_LABEL
    summary["completed_count"] = len(terminal)
    summary["progress"] = round(len(terminal) / len(selected), 4)
    summary["mean_oracle_completed"] = (
        round(statistics.mean(scores), 4) if scores else 0.0
    )
    summary["median_oracle_completed"] = (
        round(statistics.median(scores), 4) if scores else 0.0
    )
    summary["pass_rate_completed"] = (
        round(sum(successes) / len(successes), 4) if successes else 0.0
    )
    summary["strict_pass_evaluated_count"] = len(successes)
    summary["strict_passed_count"] = sum(successes)
    summary["headline_task_success_rate"] = round(sum(successes) / len(selected), 4)
    summary["headline_oracle_score"] = round(sum(scores) / len(selected), 4)
    summary["safety_gate_evaluated_count"] = len(scoreable)
    summary["safety_gate_failed_count"] = sum(
        row.get("safety_gate_failed") is True for row in scoreable
    )
    summary["safety_gate_failure_rate"] = (
        round(summary["safety_gate_failed_count"] / len(scoreable), 4)
        if scoreable
        else 0.0
    )
    summary["failed_gate_total"] = sum(
        int(row.get("failed_gate_count") or 0) for row in scoreable
    )
    summary["failed_core_total"] = sum(
        int(row.get("failed_core_count") or 0) for row in scoreable
    )


def _docker_regrade(
    source: Path,
    output: Path,
    *,
    base_url: str,
    model: str,
    upstream_api_key: str | None = None,
) -> dict[str, Any]:
    """Regrade saved answers with the original Docker and task contracts."""
    source = source.resolve()
    output = output.resolve()
    if output == source or source in output.parents:
        raise ValueError("output must be outside the source run")
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    summary = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    if summary["model"] != model or summary["base_url"] != base_url:
        raise ValueError("regrade must preserve the source model and base URL")
    if summary.get("experimental_task_environments"):
        raise ValueError("Docker regrade requires the standard task environment")
    source_snapshot = source / "suite_snapshot"
    manifest = json.loads(
        (source_snapshot / "manifest.json").read_text(encoding="utf-8")
    )
    tree_sha, task_count = run_benchmark.task_tree_sha256(source_snapshot / "tasks")
    if (
        manifest.get("schema_version") != 1
        or task_count != run_benchmark.BENCHMARK_TASK_COUNT
        or manifest.get("task_count") != task_count
        or manifest.get("task_tree_sha256") != tree_sha
        or summary.get("suite_snapshot_sha256") != tree_sha
        or manifest.get("controller_definition_sha256")
        != summary.get("controller_definition_sha256")
    ):
        raise ValueError("source suite snapshot failed its integrity manifest")
    tasks = run_benchmark.load_benchmark_tasks(source_snapshot / "tasks")
    rows = {row["task_id"]: row for row in summary["tasks"]}
    results: dict[str, dict[str, Any]] = {}
    for task in tasks:
        row = rows[task.task_id]
        if row["task_definition_sha256"] != task.definition_sha256:
            raise ValueError(f"{task.task_id}: source task definition changed")
        result_path = source / "tasks" / task.task_id / "result.json"
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if (
                result.get("task_id") != task.task_id
                or result.get("task_definition_sha256") != task.definition_sha256
            ):
                raise ValueError(f"{task.task_id}: result task definition changed")
            results[task.task_id] = result
        elif row["status"] in SCOREABLE:
            raise ValueError(f"{task.task_id}: scoreable result is missing")
        # A worker crash can leave a summary-only infrastructure_error with no
        # result artifact.  Keep it for --retry-infrastructure instead of
        # rejecting the whole preserved run.
    runtime, runtime_id = run_benchmark.controller_runtime_identity()
    if runtime_id != summary["controller_runtime_id"]:
        raise ValueError("source controller runtime differs from the current runtime")
    runtime_images = run_benchmark.reuse_recorded_runtime_images(source)
    grader_image = runtime_images[run_benchmark.GRADER_IMAGE]

    shutil.copytree(
        source,
        output,
        symlinks=True,
        ignore=lambda path, names: (
            ["suite_snapshot"] if Path(path) == source else []
        ),
    )
    previous_tasks_dir = run_benchmark.TASKS_DIR
    run_benchmark.TASKS_DIR = source_snapshot / "tasks"
    try:
        snapshot_tasks, copied_sha, controller_sha = (
            run_benchmark.freeze_suite_snapshot(output)
        )
    finally:
        run_benchmark.TASKS_DIR = previous_tasks_dir
    if copied_sha != tree_sha:
        raise ValueError("regrade copy changed the frozen task definitions")
    tasks = run_benchmark.load_benchmark_tasks(snapshot_tasks)
    summary["controller_definition_sha256"] = controller_sha
    for task_id, row in rows.items():
        if task_id in results:
            row["status"] = results[task_id]["status"]
            row["result_path"] = str(output / "tasks" / task_id / "result.json")
    run_benchmark.atomic_json(output / "summary.json", summary)
    state_fields = (
        "model", "base_url", "model_service_id", "harness_protocol",
        "harness_version", "trace_complete", "workers", "max_tokens",
        "context_window", "temperature", "data_parallel_rank", "thinking",
        "timeout_override", "max_agent_turns", "runtime_images", "isolation",
        "shard_index", "shard_count", "suite_snapshot_sha256", "grading",
    )
    state = run_benchmark.SuiteState(
        run_root=output,
        tasks=tasks,
        image=summary["harness_image"],
        harness_id=summary["harness"],
        selected_task_ids={
            task_id for task_id, row in rows.items()
            if row["status"] != "not_selected"
        },
        controller_sha256=controller_sha,
        controller_runtime=runtime,
        controller_runtime_id=runtime_id,
        task_set_name=summary.get("task_set"),
        selection_identity=summary.get("selection"),
        **{field: summary[field] for field in state_fields},
    )
    run_benchmark.copy_dashboard(output)
    report: dict[str, Any] = {
        "schema_version": 1,
        "source": str(source),
        "output": str(output),
        "source_controller_definition_sha256": manifest["controller_definition_sha256"],
        "controller_definition_sha256": controller_sha,
        "suite_snapshot_sha256": tree_sha,
        "trajectory_generation": "unchanged",
        "grading_workspaces": "source_readonly",
        "source_result_count": len(results),
        "regraded_task_ids": [],
        "preserved_non_scoreable": [
            task_id for task_id, result in results.items()
            if result["status"] not in SCOREABLE
        ],
        "changes": [],
        "status": "in_progress",
    }
    marker = output / run_benchmark.REGRADE_INCOMPLETE_MARKER
    marker.write_text(
        json.dumps({"source": str(source), "started_epoch": time.time()}) + "\n",
        encoding="utf-8",
    )
    failures: list[dict[str, str]] = []
    try:
        for task in tasks:
            original = results.get(task.task_id)
            if original is None or original["status"] not in SCOREABLE:
                continue
            artifact = source / "tasks" / task.task_id
            oracle = run_benchmark.run_grader(
                task=task,
                workspace=artifact / "workspace",
                runtime=artifact / "runtime",
                grader_control=artifact / "grader-control",
                labels=[],
                image=grader_image,
            )
            failure = run_benchmark.grader_contract_error(oracle)
            if not failure:
                run_benchmark.attach_semantic_judge(
                    task=task,
                    oracle_result=oracle,
                    base_url=base_url,
                    agent_model=model,
                    data_parallel_rank=summary["data_parallel_rank"],
                    upstream_api_key=upstream_api_key,
                )
                judge = oracle.get("semantic_judge") or {}
                if judge.get("status") == "error":
                    failure = str(judge.get("error") or "semantic judge failed")
            if failure:
                failures.append({"task_id": task.task_id, "error": failure})
                print(
                    f"[{task.task_id}] regrade failed: {failure}",
                    file=sys.stderr,
                    flush=True,
                )
                # Keep the preserved source score; a failed regrade must not be
                # reported or resumed as a completed evaluation.
                continue
            result = {
                **original,
                "oracle": oracle,
                "semantic_judge": oracle.get("semantic_judge"),
                "outcome_score": oracle.get("outcome_score"),
                "task_success": oracle.get("task_success"),
                **run_benchmark.oracle_case_failure_metrics(oracle),
                "required_artifacts": run_benchmark.authoritative_artifact_report(
                    task, artifact / "workspace", oracle
                ),
                "regraded_from_run": str(source),
                "regraded_epoch": time.time(),
                "pre_regrade_outcome_score": original.get("outcome_score"),
            }
            result["review_flag"] = run_benchmark.review_flag(
                status=result["status"],
                score=result["outcome_score"],
                task_success=result["task_success"],
                artifacts=result["required_artifacts"],
                error=result.get("error") or "",
                safety_gate_failed=result["safety_gate_failed"],
                failed_core_count=result["failed_core_count"],
            )
            result_path = output / "tasks" / task.task_id / "result.json"
            run_benchmark.atomic_json(result_path, result)
            fields = (
                "outcome_score", "task_success", "required_artifacts",
                "safety_gate_failed", "failed_gate_count", "failed_core_count",
                "review_flag",
            )
            state.update(
                task.task_id,
                result_path=str(result_path),
                **{field: result[field] for field in fields},
            )
            report["regraded_task_ids"].append(task.task_id)
            if original.get("outcome_score") != result["outcome_score"]:
                report["changes"].append({
                    "task_id": task.task_id,
                    "before": original.get("outcome_score"),
                    "after": result["outcome_score"],
                })
            print(
                f"[{task.task_id}] regraded "
                f"{original.get('outcome_score')} -> {result['outcome_score']} "
                f"judge={(oracle.get('semantic_judge') or {}).get('status', 'not_requested')}",
                flush=True,
            )
        if failures:
            report["failures"] = failures
            raise RuntimeError(
                f"{len(failures)} task(s) failed to regrade: "
                + ", ".join(item["task_id"] for item in failures)
            )
        marker.unlink()
        report["status"] = "completed"
    except Exception as exc:
        report["status"] = "failed"
        report.setdefault("failures", failures)
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["regraded_count"] = len(report["regraded_task_ids"])
        run_benchmark.atomic_json(output / "regrade_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default="gemma-4-31b-it")
    parser.add_argument(
        "--docker", action="store_true",
        help="regrade a preserved partial run with its frozen Docker contracts",
    )
    parser.add_argument("--upstream-api-key-file")
    parser.add_argument("--judge-timeout-sec", type=int, default=600)
    parser.add_argument(
        "--judge-cache-run",
        type=Path,
        help="optional prior/partial run containing compatible Judge sidecars",
    )
    parser.add_argument(
        "--reuse-compatible-judges",
        action="store_true",
        help="reuse completed evidence-valid Judge sidecars when criterion IDs match",
    )
    args = parser.parse_args()
    try:
        upstream_api_key = (
            run_benchmark.read_upstream_api_key_file(args.upstream_api_key_file)
            if args.upstream_api_key_file else None
        )
        if args.docker:
            report = _docker_regrade(
                args.source, args.output,
                base_url=args.base_url, model=args.model,
                upstream_api_key=upstream_api_key,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
            return
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc

    source = args.source.resolve()
    output = args.output.resolve()
    if not (source / "summary.json").is_file():
        raise SystemExit(f"source has no summary.json: {source}")
    if output.exists():
        raise SystemExit(f"output already exists: {output}")
    shutil.copytree(source, output)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    raw_scores: dict[str, float | None] = {}
    corrected_scores: dict[str, float | None] = {}
    judge_sources: dict[str, str] = {}
    preserved_raw_scores: dict[str, str] = {}

    rows = {
        str(row.get("task_id")): row
        for row in summary.get("tasks", [])
        if row.get("status") != "not_selected"
    }
    for task_id in sorted(rows):
        task_dir = ROOT / "tasks" / task_id
        metadata = yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8"))
        result_path = output / "tasks" / task_id / "result.json"
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
        else:
            grade_path = result_path.with_name("grade.json")
            if not grade_path.is_file():
                raise FileNotFoundError(
                    f"task run has neither result.json nor grade.json: {result_path.parent}"
                )
            host_grade = json.loads(grade_path.read_text(encoding="utf-8"))
            result = {
                **rows[task_id],
                "oracle": host_grade,
                "outcome_score": host_grade.get("outcome_score"),
                "task_success": host_grade.get("task_success"),
                "host_grade_path": str(grade_path),
            }
        raw_scores[task_id] = result.get("outcome_score")
        workspace = result_path.parent / "workspace"
        preserve_reason = RUNTIME_BOUND_REGRADES.get(task_id)
        if preserve_reason is None:
            definition_sha256 = str(result.get("task_definition_sha256") or "")
            preserve_reason = LEGACY_DEFINITION_BOUND_REGRADES.get(task_id, {}).get(
                definition_sha256
            )
        if preserve_reason:
            oracle = dict(result.get("oracle") or {})
            preserved_raw_scores[task_id] = preserve_reason
        else:
            candidate_oracle = score_task_workspace(
                task_dir,
                metadata,
                workspace,
                workspace_mutation=result.get("workspace_mutation"),
            )
            if candidate_oracle.get("oracle_contract_failure") is True:
                oracle = dict(result.get("oracle") or {})
                preserved_raw_scores[task_id] = str(
                    candidate_oracle.get("error")
                    or "local_oracle_infrastructure_failure"
                )
            else:
                oracle = candidate_oracle
        judge = _judge_request(task_dir, metadata, workspace)
        if judge is not None:
            config, request = judge
            mapped_checks = {
                str(check.get("id"))[5:]: check
                for check in oracle.get("checks", [])
                if isinstance(check, dict)
                and str(check.get("id", "")).startswith("case:")
            }
            all_mapped_pass = all(
                mapped_checks.get(case_id, {}).get("pass") is True
                for case_id in set(config["case_map"].values())
            )
            if all_mapped_pass:
                semantic_result = {
                    "schema_version": 1,
                    "status": "skipped",
                    "score_role": config["score_role"],
                    "model": args.model,
                    "model_source": "agent",
                    "reason": "all mapped deterministic cases already passed",
                }
                judge_sources[task_id] = "deterministic_skip"
            else:
                cached = None
                cached_source = ""
                if args.reuse_compatible_judges:
                    prior_judge = result.get("semantic_judge")
                    cached = _compatible_cached_judge(prior_judge, request)
                    if cached is not None:
                        cached_source = "reused_evidence_valid_sidecar"
                    else:
                        cached = _reparsed_cached_judge(prior_judge, request)
                        if cached is not None:
                            cached_source = "reparsed_stored_judge_response"
                    if cached is None and args.judge_cache_run is not None:
                        cached_path = (
                            args.judge_cache_run.resolve()
                            / "tasks"
                            / task_id
                            / "result.json"
                        )
                        if cached_path.is_file():
                            cached_result = json.loads(
                                cached_path.read_text(encoding="utf-8")
                            )
                            prior_judge = cached_result.get("semantic_judge")
                            cached = _compatible_cached_judge(prior_judge, request)
                            if cached is not None:
                                cached_source = "reused_evidence_valid_cache_sidecar"
                            else:
                                cached = _reparsed_cached_judge(prior_judge, request)
                                if cached is not None:
                                    cached_source = "reparsed_stored_cache_response"
                if cached is not None:
                    semantic_result = cached
                    judge_sources[task_id] = cached_source
                else:
                    candidate = request.get("candidate")
                    unsafe = bool(
                        oracle.get("oracle_contract_failure")
                        or oracle.get("failed_gates")
                        or oracle.get("failed_contract_gates")
                    )
                    eligible = bool(
                        isinstance(candidate, dict)
                        and candidate.get("judge_eligible") is True
                    )
                    if unsafe or not eligible:
                        semantic_result = {
                            "schema_version": 1,
                            "status": "skipped",
                            "score_role": config["score_role"],
                            "model": args.model,
                            "model_source": "agent",
                            "reason": (
                                "oracle or safety gate failed"
                                if unsafe
                                else "candidate is not eligible for semantic fallback"
                            ),
                        }
                        judge_sources[task_id] = "eligibility_skip"
                    else:
                        semantic_result = run_semantic_judge(
                            request,
                            base_url=args.base_url,
                            agent_model=args.model,
                            configured_model=config["default_model"],
                            upstream_api_key=upstream_api_key,
                            timeout_sec=args.judge_timeout_sec,
                        )
                        judge_sources[task_id] = "new_call"
            _apply_semantic_result(
                oracle,
                config=config,
                semantic_result=semantic_result,
            )

        failed_gates = list(oracle.get("failed_gates") or []) + list(
            oracle.get("failed_contract_gates") or []
        )
        failed_core = list(oracle.get("failed_core_cases") or [])
        result["grading"] = GRADING_LABEL
        result["oracle"] = oracle
        result["semantic_judge"] = oracle.get("semantic_judge")
        result["outcome_score"] = oracle.get("outcome_score")
        result["task_success"] = oracle.get("task_success")
        result["safety_gate_failed"] = bool(failed_gates)
        result["failed_gate_count"] = len(failed_gates)
        result["failed_core_count"] = len(failed_core)
        result["required_artifacts"] = oracle.get("required_artifact_contract")
        result["regraded_from_run"] = source.name
        result["scoring_revision_sha256"] = _scoring_revision(task_dir, metadata)
        result["regraded_epoch"] = time.time()
        if task_id in preserved_raw_scores:
            result["regrade_preserved_reason"] = preserved_raw_scores[task_id]
        corrected_scores[task_id] = result.get("outcome_score")
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        row = rows[task_id]
        for field in (
            "outcome_score",
            "task_success",
            "safety_gate_failed",
            "failed_gate_count",
            "failed_core_count",
            "required_artifacts",
        ):
            row[field] = result.get(field)
        row["grading"] = GRADING_LABEL
        row["review_flag"] = _review_flag(result)
        row["result_path"] = str(result_path)

    _refresh_summary(summary, output=output)
    summary["regraded_from"] = str(source)
    summary["regrade_policy"] = {
        "trajectory_generation": "unchanged",
        "workspaces": "frozen_copy",
        "deterministic_tests": "current_worktree",
        "semantic_judge": "bounded_case_fallback",
        "compatible_judge_sidecars_reused": args.reuse_compatible_judges,
        "judge_sources": judge_sources,
        "preserved_raw_scores": preserved_raw_scores,
    }
    serialized = json.dumps(summary, ensure_ascii=False, indent=2)
    (output / "summary.json").write_text(serialized + "\n", encoding="utf-8")
    (output / "summary.js").write_text(
        "window.HARNESSBENCH_SUMMARY = " + serialized + ";\n",
        encoding="utf-8",
    )

    changes = []
    for task_id in sorted(corrected_scores):
        old = raw_scores[task_id]
        new = corrected_scores[task_id]
        if old != new:
            changes.append({"task_id": task_id, "before": old, "after": new})
    report = {
        "schema_version": 1,
        "source": str(source),
        "output": str(output),
        "task_count": len(rows),
        "changed_task_count": len(changes),
        "before_mean": round(
            statistics.mean(float(value) for value in raw_scores.values()), 4
        ),
        "after_mean": summary["headline_oracle_score"],
        "score_ge_0_8_count": sum(
            isinstance(value, (int, float)) and float(value) >= 0.8
            for value in corrected_scores.values()
        ),
        "full_credit_count": sum(value == 1.0 for value in corrected_scores.values()),
        "strict_task_success_count": summary["strict_passed_count"],
        "judge_sources": judge_sources,
        "preserved_raw_scores": preserved_raw_scores,
        "changes": changes,
    }
    (output / "regrade_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
