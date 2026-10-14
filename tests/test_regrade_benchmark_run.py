from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import regrade_benchmark_run as regrade  # noqa: E402
from regrade_benchmark_run import _reparsed_cached_judge  # noqa: E402

runner = regrade.run_benchmark


def request() -> dict:
    return {
        "rubric": {"criteria": [{"id": "handoff"}]},
        "candidate": {"artifact": "handoff.md", "text": "Recovery is pending review."},
    }


def test_reparse_complete_error_response_without_model_call() -> None:
    cached = {
        "status": "error",
        "raw_output": '{"criteria":{"handoff":{"verdict":"pass","evidence":["pending review"],"reason":"bounded"}}}',
        "error": "old parser rejected mapping",
    }
    result = _reparsed_cached_judge(cached, request())
    assert result is not None
    assert result["status"] == "completed"
    assert result["verdicts"][0]["verdict"] == "pass"
    assert result["prior_error"] == "old parser rejected mapping"


def test_reparse_rejects_ungrounded_pass_evidence() -> None:
    cached = {
        "status": "error",
        "raw_output": '{"criteria":[{"id":"handoff","verdict":"pass","evidence":["not in candidate"],"reason":"unsupported"}]}',
    }
    assert _reparsed_cached_judge(cached, request()) is None


def test_reparse_does_not_replace_completed_sidecar() -> None:
    cached = {"status": "completed", "raw_output": "{}"}
    assert _reparsed_cached_judge(cached, request()) is None


@pytest.fixture
def partial_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    source = tmp_path / "source"
    source.mkdir()
    task_root = tmp_path / "definitions"
    for index in range(1, 5):
        task_id = f"{index:03}-test"
        task_dir = task_root / task_id
        task_dir.mkdir(parents=True)
        metadata = {"task_id": task_id, "required_artifacts": []}
        if index == 1:
            metadata["semantic_judge"] = {
                "rubric_file": "tests/ground_truth/semantic_judge.yaml",
                "input_builder": "tests/test_outcome.py",
                "default_model": "agent",
                "score_role": "supplemental_non_leaderboard",
            }
        (task_dir / "task.yaml").write_text(json.dumps(metadata))
    monkeypatch.setattr(runner, "BENCHMARK_TASK_COUNT", 4)
    monkeypatch.setattr(runner, "TASKS_DIR", task_root)
    frozen, tree_sha, _ = runner.freeze_suite_snapshot(source)
    manifest_path = source / "suite_snapshot" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["controller_definition_sha256"] = "b" * 64
    manifest_path.chmod(0o644)
    manifest_path.write_text(json.dumps(manifest))
    tasks = runner.load_benchmark_tasks(frozen)
    runtime = {
        "schema_version": 1,
        "implementation": "TestPython",
        "python_version": "3.12.0",
        "python_executable_sha256": "c" * 64,
        "distributions": {
            "PyYAML": {
                "version": "6.0",
                "distribution_tree_sha256": "d" * 64,
                "file_count": 1,
            },
        },
        "uv_lock_sha256": "e" * 64,
    }
    runtime_id = runner._controller_runtime_id(runtime)
    image_id = "sha256:" + "f" * 64
    monkeypatch.setattr(
        runner, "controller_runtime_identity", lambda: (runtime, runtime_id)
    )
    image_inspect = mock.Mock(return_value=SimpleNamespace(stdout=image_id + "\n"))
    image_inspect.return_value.returncode = 0
    monkeypatch.setattr(runner, "docker", image_inspect)
    state_kwargs = {
        "model": "test-model",
        "base_url": "https://provider/api/v3",
        "model_service_id": "https://provider/api/v3",
        "image": "harnessbench/mixdog:1.1.2",
        "harness_id": "mixdog",
        "harness_protocol": "chat_completions",
        "harness_version": "1.1.2",
        "trace_complete": True,
        "workers": 12,
        "max_tokens": 32768,
        "context_window": 200000,
        "temperature": 1.0,
        "data_parallel_rank": None,
        "thinking": "high",
        "timeout_override": None,
        "max_agent_turns": 150,
        "runtime_images": {runner.GRADER_IMAGE: image_id},
        "isolation": {"engine": "docker"},
        "selected_task_ids": {task.task_id for task in tasks},
        "shard_index": 0,
        "shard_count": 1,
        "suite_snapshot_sha256": tree_sha,
        "controller_runtime": runtime,
        "controller_runtime_id": runtime_id,
        "grading": runner.BOUNDED_HYBRID_GRADING,
    }
    state = runner.SuiteState(
        run_root=source, tasks=tasks, controller_sha256="b" * 64, **state_kwargs
    )
    for task, score, status in zip(
        tasks[:3], [0.25, 0.5, None],
        ["completed", "completed", "infrastructure_error"],
    ):
        artifact = source / "tasks" / task.task_id
        for name in ("workspace", "runtime", "grader-control", "provider-trace"):
            (artifact / name).mkdir(parents=True)
        (artifact / "workspace" / "answer.txt").write_text("saved answer")
        (artifact / "provider-trace" / "transactions.jsonl").write_text("{}\n")
        result = {
            "task_id": task.task_id,
            "task_definition_sha256": task.definition_sha256,
            "status": status,
            "outcome_score": score,
            "task_success": False if score is not None else None,
            "provider_request_count": 10,
            "elapsed_sec": 20.0,
            "error": "transport failed" if score is None else "",
        }
        runner.atomic_json(artifact / "result.json", result)
        state.update(task.task_id, **{
            key: value for key, value in result.items()
            if key not in {"task_id", "task_definition_sha256"}
        })

    def grade(**kwargs: object) -> dict:
        task = kwargs["task"]
        result = {
            "outcome_score": 0.75 if task.task_id == "001-test" else 0.5,
            "task_success": False,
            "checks": [],
        }
        if task.task_id == "001-test":
            result["semantic_judge_request"] = {
                "candidate": {"valid_native_document": True}
            }
        return result

    grader = mock.Mock(side_effect=grade)
    monkeypatch.setattr(runner, "run_grader", grader)
    judge = mock.Mock(return_value={
        "status": "completed",
        "score_role": "supplemental_non_leaderboard",
        "verdicts": [],
    })
    monkeypatch.setattr(runner, "run_semantic_judge", judge)
    return {
        "source": source,
        "output": tmp_path / "output",
        "grader": grader,
        "judge": judge,
        "image_inspect": image_inspect,
        "state_kwargs": state_kwargs,
        "before": {
            path.relative_to(source): path.read_bytes()
            for path in source.rglob("*") if path.is_file()
        },
    }


def test_docker_regrade_preserves_source_and_resumes_only_unfinished(
    partial_run: dict,
) -> None:
    source, output = partial_run["source"], partial_run["output"]
    report = regrade._docker_regrade(
        source, output, base_url="https://provider/api/v3",
        model="test-model", upstream_api_key="judge-test-secret",
    )
    assert report["status"] == "completed"
    assert report["source_result_count"] == 3
    assert report["regraded_count"] == 2
    assert report["preserved_non_scoreable"] == ["003-test"]
    assert report["changes"] == [
        {"task_id": "001-test", "before": 0.25, "after": 0.75}
    ]
    assert partial_run["grader"].call_count == 2
    for call in partial_run["grader"].call_args_list:
        assert source in call.kwargs["workspace"].parents
        assert call.kwargs["image"] == partial_run["state_kwargs"]["runtime_images"][
            runner.GRADER_IMAGE
        ]
    assert partial_run["judge"].call_args.kwargs["upstream_api_key"] == (
        "judge-test-secret"
    )
    assert {
        path.relative_to(source): path.read_bytes()
        for path in source.rglob("*") if path.is_file()
    } == partial_run["before"]
    result = json.loads((output / "tasks" / "001-test" / "result.json").read_text())
    assert result["provider_request_count"] == 10
    assert result["elapsed_sec"] == 20.0
    assert (output / "tasks" / "001-test" / "workspace" / "answer.txt").read_text() == (
        "saved answer"
    )
    assert (output / "tasks" / "003-test" / "result.json").read_bytes() == (
        source / "tasks" / "003-test" / "result.json"
    ).read_bytes()
    tasks_dir, _, controller_sha = runner.freeze_suite_snapshot(output)
    tasks = runner.load_benchmark_tasks(tasks_dir)
    state = runner.SuiteState(
        run_root=output, tasks=tasks, controller_sha256=controller_sha,
        **partial_run["state_kwargs"],
    )
    assert {task.task_id for task in state.pending_tasks(tasks)} == {"004-test"}
    assert {
        task.task_id for task in state.pending_tasks(tasks, retry_infrastructure=True)
    } == {"003-test", "004-test"}
    summary = json.loads((output / "summary.json").read_text())
    assert summary["counts"]["completed"] == 2
    assert summary["mean_oracle_completed"] == 0.625
    assert "judge-test-secret" not in json.dumps(report)
    assert "judge-test-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "fault", ["task_tree", "result_hash", "image", "runtime", "missing_result"]
)
def test_docker_regrade_rejects_integrity_drift(
    partial_run: dict, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    source = partial_run["source"]
    if fault == "task_tree":
        path = source / "suite_snapshot" / "tasks" / "001-test" / "task.yaml"
        path.chmod(0o644)
        path.write_text(path.read_text() + "\n# altered")
    elif fault == "result_hash":
        path = source / "tasks" / "001-test" / "result.json"
        result = json.loads(path.read_text())
        result["task_definition_sha256"] = "0" * 64
        path.write_text(json.dumps(result))
    elif fault == "image":
        partial_run["image_inspect"].return_value.stdout = "sha256:changed\n"
    elif fault == "runtime":
        monkeypatch.setattr(
            runner, "controller_runtime_identity", lambda: ({}, "sha256:changed")
        )
    else:
        (source / "tasks" / "001-test" / "result.json").unlink()
    with pytest.raises(ValueError):
        regrade._docker_regrade(
            source, partial_run["output"],
            base_url="https://provider/api/v3", model="test-model",
        )
    assert not partial_run["output"].exists()
    partial_run["grader"].assert_not_called()


def test_docker_regrade_exposes_judge_failure_without_touching_source(
    partial_run: dict,
) -> None:
    partial_run["judge"].return_value = {
        "status": "error", "error": "HTTPError: HTTP 401: Unauthorized"
    }
    with pytest.raises(RuntimeError, match="failed to regrade"):
        regrade._docker_regrade(
            partial_run["source"], partial_run["output"],
            base_url="https://provider/api/v3", model="test-model",
        )
    report = json.loads(
        (partial_run["output"] / "regrade_report.json").read_text()
    )
    assert report["status"] == "failed"
    # The failing task keeps its preserved source score and is excluded from
    # the regraded set, while independent tasks still complete.
    assert report["regraded_count"] == 1
    assert report["regraded_task_ids"] == ["002-test"]
    assert report["failures"] == [
        {"task_id": "001-test", "error": "HTTPError: HTTP 401: Unauthorized"}
    ]
    evidence = json.loads(
        (partial_run["output"] / "tasks" / "001-test" / "result.json").read_text()
    )
    assert evidence == json.loads(
        (partial_run["source"] / "tasks" / "001-test" / "result.json").read_text()
    )
    incomplete = partial_run["output"] / runner.REGRADE_INCOMPLETE_MARKER
    assert incomplete.is_file()
    with pytest.raises(ValueError, match="output already exists"):
        regrade._docker_regrade(
            partial_run["source"], partial_run["output"],
            base_url="https://provider/api/v3", model="test-model",
        )
    assert (partial_run["source"] / "summary.json").read_bytes() == (
        partial_run["before"][Path("summary.json")]
    )


def test_incomplete_regrade_blocks_resume_and_preserves_source_scores(
    partial_run: dict,
) -> None:
    partial_run["judge"].return_value = {"status": "error", "error": "judge down"}
    with pytest.raises(RuntimeError):
        regrade._docker_regrade(
            partial_run["source"], partial_run["output"],
            base_url="https://provider/api/v3", model="test-model",
        )
    output = partial_run["output"]
    tasks_dir, tree_sha, controller_sha = runner.freeze_suite_snapshot(output)
    tasks = runner.load_benchmark_tasks(tasks_dir)
    state = runner.SuiteState(
        run_root=output, tasks=tasks, controller_sha256=controller_sha,
        **partial_run["state_kwargs"],
    )
    assert state.records["001-test"]["outcome_score"] == 0.25
    assert state.records["001-test"]["status"] == "completed"
    with pytest.raises(SystemExit, match="regrade did not finish"):
        runner.ensure_resumable_run(output)
    with pytest.raises(ValueError, match="requires an existing run"):
        runner.reuse_recorded_runtime_images(output / "missing-run")
    recorded = runner.reuse_recorded_runtime_images(output)
    assert recorded == partial_run["state_kwargs"]["runtime_images"]


def test_docker_cli_reads_keyfile_and_passes_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    keyfile = tmp_path / "key.txt"
    keyfile.write_text("judge-test-secret\n")
    monkeypatch.setattr(sys, "argv", [
        "regrade_benchmark_run.py", str(tmp_path / "source"),
        "--output", str(tmp_path / "output"), "--docker",
        "--base-url", "https://provider/api/v3", "--model", "test-model",
        "--upstream-api-key-file", str(keyfile),
    ])
    with mock.patch.object(regrade, "_docker_regrade", return_value={}) as run:
        regrade.main()
    assert run.call_args.kwargs["upstream_api_key"] == "judge-test-secret"


@pytest.mark.parametrize("content", ["", "first\nsecond"])
def test_keyfile_rejects_empty_and_multiline_credentials(
    tmp_path: Path, content: str,
) -> None:
    path = tmp_path / "key.txt"
    path.write_text(content)
    with pytest.raises(ValueError):
        runner.read_upstream_api_key_file(path)
