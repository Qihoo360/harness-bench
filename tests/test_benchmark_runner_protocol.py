from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import run_benchmark as runner_module  # noqa: E402
from run_benchmark import (  # noqa: E402
    BenchmarkTask,
    SuiteState,
    agent_tree_normalization_script,
    agent_round_failure_status,
    attach_semantic_judge,
    authoritative_artifact_report,
    controller_runtime_identity,
    enforce_cleanup_workspace_immutability,
    ensure_workspace_root,
    environment_mutations,
    hook_context,
    grader_contract_error,
    load_module,
    make_agent_writable,
    make_readable,
    measure_agent_workspace_mutation,
    oracle_case_failure_metrics,
    prepare_hook,
    protocol_failure_status,
    protocol_report,
    provider_forward_failure,
    provider_turn_budget_exhausted,
    review_flag,
    validate_openclaw_trace,
)
from openclaw_runtime import openclaw_config  # noqa: E402


class OpenClawConfigTests(unittest.TestCase):
    def test_provider_timeout_matches_trial_round_budget(self) -> None:
        config = openclaw_config(
            "http://model/v1",
            "model",
            8192,
            provider_timeout_sec=3600,
        )
        provider = config["models"]["providers"]["vllm"]
        self.assertEqual(provider["timeoutSeconds"], 3600)

    def test_context_window_is_configurable(self) -> None:
        config = openclaw_config(
            "http://model/v1",
            "model",
            8192,
            context_window=200000,
        )
        model = config["models"]["providers"]["vllm"]["models"][0]
        self.assertEqual(model["contextWindow"], 200000)

    def test_provider_timeout_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            openclaw_config(
                "http://model/v1",
                "model",
                8192,
                provider_timeout_sec=0,
            )


class ProviderTurnBudgetTests(unittest.TestCase):
    def test_rejected_request_after_exact_budget_is_a_clean_terminal_stop(self) -> None:
        proxy = SimpleNamespace(
            model_requests=150,
            rejected_requests=1,
            forward_errors=0,
        )
        self.assertTrue(
            provider_turn_budget_exhausted(proxy, max_agent_turns=150)
        )

    def test_provider_failure_is_not_mistaken_for_turn_exhaustion(self) -> None:
        proxy = SimpleNamespace(
            model_requests=150,
            rejected_requests=1,
            forward_errors=1,
        )
        self.assertFalse(
            provider_turn_budget_exhausted(proxy, max_agent_turns=150)
        )

    def test_provider_forward_error_is_retryable_even_after_harness_exit(self) -> None:
        proxy = SimpleNamespace(
            forward_errors=2,
            last_error="ConnectionResetError: connection reset by peer",
        )
        self.assertEqual(
            provider_forward_failure(proxy),
            (
                "model provider forwarding failed 2 time(s): "
                "ConnectionResetError: connection reset by peer"
            ),
        )

    def test_no_provider_forward_error_needs_no_reclassification(self) -> None:
        proxy = SimpleNamespace(
            forward_errors=0,
            last_error="",
        )
        self.assertIsNone(provider_forward_failure(proxy))


class GraderFailureAttributionTests(unittest.TestCase):
    def test_suite_grading_label_discloses_bounded_semantic_fallback(self) -> None:
        deterministic = BenchmarkTask(
            task_id="001-test",
            title="test",
            domain="test",
            difficulty="hard",
            task_dir=ROOT / "tasks" / "001-meeting-summary",
            metadata={},
            definition_sha256="0" * 64,
        )
        hybrid = BenchmarkTask(
            task_id="004-test",
            title="test",
            domain="test",
            difficulty="hard",
            task_dir=ROOT / "tasks" / "004-office-docs",
            metadata={
                "semantic_judge": {
                    "rubric_file": "tests/ground_truth/semantic_judge.yaml",
                    "input_builder": "tests/test_outcome.py",
                    "default_model": "agent",
                    "score_role": "bounded_case_fallback",
                    "case_map": {"meaning": "business_meaning"},
                    "max_fallback_weight": 0.25,
                }
            },
            definition_sha256="1" * 64,
        )
        self.assertEqual(
            runner_module.suite_grading_mode([deterministic]), "oracle_only"
        )
        self.assertEqual(
            runner_module.suite_grading_mode([deterministic, hybrid]),
            "deterministic_oracle_with_bounded_semantic_fallback",
        )

    def test_oracle_failures_are_not_scoreable_candidate_results(self) -> None:
        self.assertEqual(grader_contract_error({"outcome_score": 0.0}), "")
        message = grader_contract_error(
            {
                "outcome_score": 0.0,
                "oracle_contract_failure": True,
                "oracle_infrastructure_failure": True,
                "error": "OracleInfrastructureError: amtool unavailable",
            }
        )
        self.assertIn("infrastructure unavailable", message)
        self.assertIn("amtool unavailable", message)

    def test_formal_case_failures_are_exposed_without_case_ids(self) -> None:
        self.assertEqual(
            oracle_case_failure_metrics(
                {
                    "failed_gates": ["input_integrity"],
                    "failed_contract_gates": ["workspace_mutation_safety"],
                    "failed_core_cases": ["business_outcome", "audit_close"],
                }
            ),
            {
                "safety_gate_failed": True,
                "failed_gate_count": 2,
                "failed_core_count": 2,
            },
        )
        self.assertEqual(
            oracle_case_failure_metrics({"outcome_score": 0.5}),
            {
                "safety_gate_failed": False,
                "failed_gate_count": 0,
                "failed_core_count": 0,
            },
        )

    def test_semantic_judge_defaults_to_agent_model_without_changing_oracle(
        self,
    ) -> None:
        task = BenchmarkTask(
            task_id="004-office-docs",
            title="test",
            domain="Office Productivity",
            difficulty="hard",
            task_dir=ROOT / "tasks" / "004-office-docs",
            metadata={
                "semantic_judge": {
                    "rubric_file": "tests/ground_truth/semantic_judge.yaml",
                    "input_builder": "tests/test_outcome.py",
                    "default_model": "agent",
                    "score_role": "supplemental_non_leaderboard",
                }
            },
            definition_sha256="0" * 64,
        )
        score = {
            "outcome_score": 0.8,
            "semantic_judge_request": {
                "candidate": {"valid_native_document": True},
            },
        }
        completed = {
            "schema_version": 1,
            "status": "completed",
            "score_role": "supplemental_non_leaderboard",
            "model": "gemma-4-31b",
            "model_source": "agent",
        }

        with mock.patch(
            "run_benchmark.run_semantic_judge",
            return_value=completed,
        ) as judge:
            attach_semantic_judge(
                task=task,
                oracle_result=score,
                base_url="http://model:8000/v1",
                agent_model="gemma-4-31b",
                data_parallel_rank=2,
                upstream_api_key="judge-test-secret",
            )

        self.assertEqual(score["outcome_score"], 0.8)
        self.assertEqual(score["semantic_judge"], completed)
        self.assertNotIn("semantic_judge_request", score)
        self.assertEqual(judge.call_args.kwargs["agent_model"], "gemma-4-31b")
        self.assertEqual(
            judge.call_args.kwargs["upstream_api_key"], "judge-test-secret"
        )

    def test_semantic_judge_skips_when_oracle_contract_failed(self) -> None:
        task = BenchmarkTask(
            task_id="004-office-docs",
            title="test",
            domain="Office Productivity",
            difficulty="hard",
            task_dir=ROOT / "tasks" / "004-office-docs",
            metadata={
                "semantic_judge": {
                    "rubric_file": "tests/ground_truth/semantic_judge.yaml",
                    "input_builder": "tests/test_outcome.py",
                    "default_model": "agent",
                    "score_role": "supplemental_non_leaderboard",
                }
            },
            definition_sha256="0" * 64,
        )
        score = {
            "outcome_score": 0.0,
            "oracle_contract_failure": True,
            "semantic_judge_request": {
                "candidate": {"valid_native_document": True},
            },
        }

        with mock.patch("run_benchmark.run_semantic_judge") as judge:
            attach_semantic_judge(
                task=task,
                oracle_result=score,
                base_url="http://model:8000/v1",
                agent_model="gemma-4-31b",
                data_parallel_rank=None,
            )

        judge.assert_not_called()
        self.assertEqual(score["semantic_judge"]["status"], "skipped")

    def test_explicit_judge_ineligibility_overrides_native_document_validity(
        self,
    ) -> None:
        task = BenchmarkTask(
            task_id="004-office-docs",
            title="test",
            domain="Office Productivity",
            difficulty="hard",
            task_dir=ROOT / "tasks" / "004-office-docs",
            metadata={
                "semantic_judge": {
                    "rubric_file": "tests/ground_truth/semantic_judge.yaml",
                    "input_builder": "tests/test_outcome.py",
                    "default_model": "agent",
                    "score_role": "bounded_case_fallback",
                    "case_map": {"meaning": "business_meaning"},
                    "max_fallback_weight": 0.25,
                }
            },
            definition_sha256="0" * 64,
        )
        score = {
            "outcome_score": 0.75,
            "failed_gates": [],
            "checks": [
                {
                    "id": "case:business_meaning",
                    "pass": False,
                    "weight": 0.25,
                    "core": True,
                    "gate": False,
                }
            ],
            "semantic_judge_request": {
                "candidate": {
                    "judge_eligible": False,
                    "valid_native_document": True,
                }
            },
        }
        with mock.patch("run_benchmark.run_semantic_judge") as judge:
            attach_semantic_judge(
                task=task,
                oracle_result=score,
                base_url="http://model:8000/v1",
                agent_model="gemma-4-31b",
                data_parallel_rank=None,
            )
        judge.assert_not_called()
        self.assertEqual(score["outcome_score"], 0.75)
        self.assertEqual(score["semantic_judge"]["status"], "skipped")

    def test_bounded_semantic_judge_can_promote_only_its_mapped_case(self) -> None:
        task = BenchmarkTask(
            task_id="004-office-docs",
            title="test",
            domain="Office Productivity",
            difficulty="hard",
            task_dir=ROOT / "tasks" / "004-office-docs",
            metadata={
                "semantic_judge": {
                    "rubric_file": "tests/ground_truth/semantic_judge.yaml",
                    "input_builder": "tests/test_outcome.py",
                    "default_model": "agent",
                    "score_role": "bounded_case_fallback",
                    "case_map": {"meaning": "business_meaning"},
                    "max_fallback_weight": 0.25,
                }
            },
            definition_sha256="0" * 64,
        )
        score = {
            "outcome_score": 0.75,
            "weighted_score": 0.75,
            "pass_threshold": 0.8,
            "task_success": False,
            "failed_core_cases": ["business_meaning"],
            "failed_gates": [],
            "failed_contract_gates": [],
            "checks": [
                {
                    "id": "case:business_meaning",
                    "pass": False,
                    "weight": 0.25,
                    "core": True,
                    "gate": False,
                    "detail": "literal phrase absent",
                },
                {
                    "id": "case:structured_delivery",
                    "pass": True,
                    "weight": 0.75,
                    "core": False,
                    "gate": False,
                    "detail": None,
                },
            ],
            "semantic_judge_request": {
                "candidate": {"judge_eligible": True},
            },
        }
        completed = {
            "schema_version": 1,
            "status": "completed",
            "score_role": "bounded_case_fallback",
            "model": "gemma-4-31b",
            "model_source": "agent",
            "verdicts": [
                {
                    "id": "meaning",
                    "verdict": "pass",
                    "evidence": ["same business meaning"],
                    "reason": "semantically equivalent",
                }
            ],
        }

        with mock.patch("run_benchmark.run_semantic_judge", return_value=completed):
            attach_semantic_judge(
                task=task,
                oracle_result=score,
                base_url="http://model:8000/v1",
                agent_model="gemma-4-31b",
                data_parallel_rank=None,
            )

        self.assertEqual(score["deterministic_outcome_score"], 0.75)
        self.assertEqual(score["outcome_score"], 1.0)
        self.assertTrue(score["task_success"])
        self.assertEqual(score["failed_core_cases"], [])
        self.assertEqual(
            score["semantic_fallback_applied"],
            [
                {
                    "criterion_ids": ["meaning"],
                    "case_id": "business_meaning",
                    "weight": 0.25,
                }
            ],
        )

    def test_bounded_case_requires_every_mapped_semantic_subcriterion(self) -> None:
        task = BenchmarkTask(
            task_id="004-office-docs",
            title="test",
            domain="Office Productivity",
            difficulty="hard",
            task_dir=ROOT / "tasks" / "004-office-docs",
            metadata={
                "semantic_judge": {
                    "rubric_file": "tests/ground_truth/semantic_judge.yaml",
                    "input_builder": "tests/test_outcome.py",
                    "default_model": "agent",
                    "score_role": "bounded_case_fallback",
                    "case_map": {
                        "required_part_a": "business_meaning",
                        "required_part_b": "business_meaning",
                    },
                    "max_fallback_weight": 0.25,
                }
            },
            definition_sha256="0" * 64,
        )
        score = {
            "outcome_score": 0.75,
            "weighted_score": 0.75,
            "pass_threshold": 0.8,
            "task_success": False,
            "failed_core_cases": ["business_meaning"],
            "failed_gates": [],
            "failed_contract_gates": [],
            "checks": [
                {
                    "id": "case:business_meaning",
                    "pass": False,
                    "weight": 0.25,
                    "core": True,
                    "gate": False,
                    "detail": "one required part is absent",
                },
                {
                    "id": "case:structured_delivery",
                    "pass": True,
                    "weight": 0.75,
                    "core": False,
                    "gate": False,
                    "detail": None,
                },
            ],
            "semantic_judge_request": {"candidate": {"judge_eligible": True}},
        }
        completed = {
            "schema_version": 1,
            "status": "completed",
            "score_role": "bounded_case_fallback",
            "model": "gemma-4-31b",
            "model_source": "agent",
            "verdicts": [
                {
                    "id": "required_part_a",
                    "verdict": "pass",
                    "evidence": ["part A"],
                    "reason": "present",
                },
                {
                    "id": "required_part_b",
                    "verdict": "fail",
                    "evidence": [],
                    "reason": "absent",
                },
            ],
        }
        with mock.patch("run_benchmark.run_semantic_judge", return_value=completed):
            attach_semantic_judge(
                task=task,
                oracle_result=score,
                base_url="http://model:8000/v1",
                agent_model="gemma-4-31b",
                data_parallel_rank=None,
            )
        self.assertEqual(score["outcome_score"], 0.75)
        self.assertEqual(score["semantic_fallback_applied"], [])
        self.assertNotIn("deterministic_outcome_score", score)

    def test_bounded_semantic_judge_fails_closed_on_judge_error(self) -> None:
        task = BenchmarkTask(
            task_id="004-office-docs",
            title="test",
            domain="Office Productivity",
            difficulty="hard",
            task_dir=ROOT / "tasks" / "004-office-docs",
            metadata={
                "semantic_judge": {
                    "rubric_file": "tests/ground_truth/semantic_judge.yaml",
                    "input_builder": "tests/test_outcome.py",
                    "default_model": "agent",
                    "score_role": "bounded_case_fallback",
                    "case_map": {"meaning": "business_meaning"},
                    "max_fallback_weight": 0.25,
                }
            },
            definition_sha256="0" * 64,
        )
        score = {
            "outcome_score": 0.75,
            "checks": [
                {
                    "id": "case:business_meaning",
                    "pass": False,
                    "weight": 0.25,
                    "core": True,
                    "gate": False,
                }
            ],
            "semantic_judge_request": {
                "candidate": {"judge_eligible": True},
            },
        }
        errored = {
            "schema_version": 1,
            "status": "error",
            "score_role": "bounded_case_fallback",
            "error": "malformed output",
        }
        with mock.patch("run_benchmark.run_semantic_judge", return_value=errored):
            attach_semantic_judge(
                task=task,
                oracle_result=score,
                base_url="http://model:8000/v1",
                agent_model="gemma-4-31b",
                data_parallel_rank=None,
            )
        self.assertEqual(score["outcome_score"], 0.75)
        self.assertNotIn("deterministic_outcome_score", score)

    def test_bounded_judge_is_not_called_when_mapped_case_already_passes(self) -> None:
        task = BenchmarkTask(
            task_id="004-office-docs",
            title="test",
            domain="Office Productivity",
            difficulty="hard",
            task_dir=ROOT / "tasks" / "004-office-docs",
            metadata={
                "semantic_judge": {
                    "rubric_file": "tests/ground_truth/semantic_judge.yaml",
                    "input_builder": "tests/test_outcome.py",
                    "default_model": "agent",
                    "score_role": "bounded_case_fallback",
                    "case_map": {"meaning": "business_meaning"},
                    "max_fallback_weight": 0.25,
                }
            },
            definition_sha256="0" * 64,
        )
        score = {
            "outcome_score": 1.0,
            "checks": [
                {
                    "id": "case:business_meaning",
                    "pass": True,
                    "weight": 0.25,
                    "core": True,
                    "gate": False,
                }
            ],
            "semantic_judge_request": {
                "candidate": {"judge_eligible": True},
            },
        }
        with mock.patch("run_benchmark.run_semantic_judge") as judge:
            attach_semantic_judge(
                task=task,
                oracle_result=score,
                base_url="http://model:8000/v1",
                agent_model="gemma-4-31b",
                data_parallel_rank=None,
            )
        judge.assert_not_called()
        self.assertEqual(score["outcome_score"], 1.0)
        self.assertEqual(score["semantic_judge"]["status"], "skipped")
        self.assertEqual(score["semantic_fallback_applied"], [])

    def test_review_flag_separates_safety_and_core_business_failures(self) -> None:
        common = {
            "status": "completed",
            "score": 0.0,
            "task_success": False,
            "artifacts": {"missing": []},
            "error": "",
        }
        self.assertEqual(
            review_flag(
                **common,
                safety_gate_failed=True,
                failed_core_count=0,
            ),
            "safety_gate_failure",
        )
        self.assertEqual(
            review_flag(
                **common,
                safety_gate_failed=False,
                failed_core_count=1,
            ),
            "core_case_failure",
        )
        self.assertEqual(
            review_flag(
                **{**common, "status": "timeout"},
                safety_gate_failed=True,
                failed_core_count=0,
            ),
            "safety_gate_failure",
        )


class OpenClawContainerTests(unittest.TestCase):
    @mock.patch("run_benchmark.docker")
    def test_local_agent_disables_gateway_healthcheck(
        self,
        docker_mock: mock.Mock,
    ) -> None:
        docker_mock.return_value = mock.Mock(
            returncode=0,
            stdout="{}",
            stderr="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            runtime = root / "runtime"
            workspace.mkdir()
            runtime.mkdir()
            task = BenchmarkTask(
                task_id="001-test",
                title="test",
                domain="test",
                difficulty="test",
                task_dir=root,
                metadata={},
                definition_sha256="0" * 64,
            )
            runner_module.run_agent_round(
                task=task,
                prompt="do the task",
                round_index=0,
                workspace=workspace,
                runtime=runtime,
                state_volume="state",
                image="openclaw:test",
                session_id="session",
                timeout=60,
                thinking="low",
                labels=["--label", "test=true"],
            )

        run_arguments = docker_mock.call_args_list[0].args
        self.assertIn("--no-healthcheck", run_arguments)
        self.assertIn(
            f"type=bind,src={workspace},dst=/workspace",
            run_arguments,
        )
        workspace_targets = [
            field.removeprefix("dst=")
            for value in run_arguments
            for field in str(value).split(",")
            if field.startswith("dst=/workspace")
        ]
        self.assertEqual(
            workspace_targets,
            ["/workspace"],
        )
        env_positions = [
            index for index, value in enumerate(run_arguments) if value == "--env"
        ]
        self.assertTrue(
            any(
                run_arguments[index + 1] == "PYTHONDONTWRITEBYTECODE=1"
                for index in env_positions
            )
        )
        self.assertTrue(
            any(
                run_arguments[index + 1]
                == "GIT_CONFIG_GLOBAL=/home/node/.openclaw/.gitconfig"
                for index in env_positions
            )
        )


class WorkspaceIsolationTests(unittest.TestCase):
    def test_refined_cleanup_cannot_rewrite_the_graded_workspace(self) -> None:
        before = {
            "result.json": {
                "kind": "file",
                "sha256": "a" * 64,
                "size": 4,
            }
        }
        enforce_cleanup_workspace_immutability(before, dict(before))
        with self.assertRaisesRegex(RuntimeError, "cleanup hooks must not change"):
            enforce_cleanup_workspace_immutability(
                before,
                {
                    "result.json": {
                        "kind": "file",
                        "sha256": "b" * 64,
                        "size": 4,
                    }
                },
            )

    def test_refined_post_round_change_requires_content_addressed_provenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            before = runner_module.workspace_manifest(workspace)
            snapshot = workspace / ".trusted-state.json"
            snapshot.write_text('{"ok": true}\n', encoding="utf-8")
            after = runner_module.workspace_manifest(workspace)
            expected = after[".trusted-state.json"]
            hooks = SimpleNamespace(
                post_round_environment_mutations=lambda *_: {
                    "created": {".trusted-state.json": expected},
                    "modified": {},
                    "deleted": {},
                }
            )

            declared = environment_mutations(
                hooks,
                {},
                {},
                0,
                callback_name="post_round_environment_mutations",
            )
            report = measure_agent_workspace_mutation(
                before,
                after,
                contract={
                    "allowed_created": [],
                    "allowed_modified": [],
                    "allowed_deleted": [],
                },
                trusted=declared,
            )
            self.assertTrue(report["complete"])
            self.assertEqual(
                report["environment_mutations"]["matched"]["created"],
                [".trusted-state.json"],
            )

            declared["created"][".trusted-state.json"] = {
                **expected,
                "size": int(expected["size"]) + 1,
            }
            rejected = measure_agent_workspace_mutation(
                before,
                after,
                contract={
                    "allowed_created": [],
                    "allowed_modified": [],
                    "allowed_deleted": [],
                },
                trusted=declared,
            )
            self.assertFalse(rejected["complete"])
            self.assertTrue(rejected["safe"])
            self.assertEqual(
                rejected["environment_mutations"]["mismatched_or_absent"]["created"],
                [".trusted-state.json"],
            )

    def test_initial_protection_does_not_promote_scratch_files_between_rounds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            initial = runner_module.workspace_manifest(workspace)
            scratch = workspace / "agent_notes.txt"
            scratch.write_text("round one\n", encoding="utf-8")
            after_first = runner_module.workspace_manifest(workspace)
            first = measure_agent_workspace_mutation(
                initial,
                after_first,
                contract={
                    "allowed_created": [],
                    "allowed_modified": [],
                    "allowed_deleted": [],
                },
                protected_paths=set(initial),
            )
            scratch.write_text("round two\n", encoding="utf-8")
            second = measure_agent_workspace_mutation(
                after_first,
                runner_module.workspace_manifest(workspace),
                contract={
                    "allowed_created": [],
                    "allowed_modified": [],
                    "allowed_deleted": [],
                },
                protected_paths=set(initial),
            )

        self.assertFalse(first["complete"])
        self.assertTrue(first["safe"])
        self.assertFalse(second["complete"])
        self.assertTrue(second["safe"])
        self.assertEqual(second["protected_modified"], [])

    def test_hook_workspace_rebuild_preserves_workspace_root(self) -> None:
        """Regression: task003 setup replaces the staged workspace."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            runtime = root / "runtime"
            runtime.mkdir()
            ensure_workspace_root(workspace)
            hooks = load_module(
                ROOT / "tasks" / "003-git-pr-merge" / "environment" / "hooks.py",
                "test_task003_hooks",
            )
            self.assertIsNotNone(hooks)
            context = {
                "sandbox": runtime,
                "workspace": workspace,
                "reference_mode": False,
                "run_trusted_helper": lambda command: mock.Mock(
                    args=command,
                    returncode=0,
                    stdout="",
                    stderr="",
                ),
            }

            prepare_hook(hooks, context)

            self.assertTrue(workspace.is_dir())
            ensure_workspace_root(workspace)
            self.assertTrue(workspace.is_dir())

    def test_workspace_contract_rejects_redirected_mount_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            target = root / "redirect"
            target.mkdir()
            workspace.symlink_to(target, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "must not be a symlink"):
                ensure_workspace_root(workspace)

    def test_workspace_contract_rejects_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.write_text("not a directory", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "must be a directory"):
                ensure_workspace_root(workspace)

    def test_host_permission_normalization_never_follows_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            out = workspace
            out.mkdir(parents=True)
            sentinel = root / "sentinel"
            sentinel.write_text("host-owned", encoding="utf-8")
            sentinel.chmod(0o600)
            (out / "redirect").symlink_to(sentinel)

            make_agent_writable(workspace)
            make_readable(workspace)

            self.assertEqual(stat.S_IMODE(sentinel.stat().st_mode), 0o600)

    def test_make_agent_writable_preserves_executable_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            runtime.mkdir()
            executable = runtime / "task-tool"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o555)
            data = runtime / "task-data"
            data.write_text("data\n", encoding="utf-8")
            data.chmod(0o444)

            make_agent_writable(runtime)

            self.assertEqual(stat.S_IMODE(executable.stat().st_mode), 0o777)
            self.assertEqual(stat.S_IMODE(data.stat().st_mode), 0o666)

    def test_agent_tree_helper_rejects_symlink_and_fifo_before_chmod(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            runtime = root / "runtime"
            out = workspace
            out.mkdir(parents=True)
            runtime.mkdir()
            sentinel = root / "sentinel"
            sentinel.write_text("host-owned", encoding="utf-8")
            sentinel.chmod(0o600)
            (out / "redirect").symlink_to(sentinel)
            completed = subprocess.run(
                [
                    "sh",
                    "-c",
                    agent_tree_normalization_script("a+rwX"),
                    "harnessbench-normalize-test",
                    str(workspace),
                    str(runtime),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(completed.returncode, 86)
            self.assertIn("unsafe agent-owned path", completed.stderr)
            self.assertEqual(stat.S_IMODE(sentinel.stat().st_mode), 0o600)

            (out / "redirect").unlink()
            os.mkfifo(out / "blocking-pipe")
            completed = subprocess.run(
                [
                    "sh",
                    "-c",
                    agent_tree_normalization_script("a+rX,go-w"),
                    "harnessbench-normalize-test",
                    str(workspace),
                    str(runtime),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(completed.returncode, 86)
            self.assertIn("unsafe agent-owned path", completed.stderr)

    def test_stage_workspace_seed_excludes_runtime_cache_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = root / "task"
            seed = task_dir / "workspace" / "repo"
            cache = seed / "__pycache__"
            cache.mkdir(parents=True)
            (seed / "simulate.py").write_text("VALUE = 1\n", encoding="utf-8")
            (cache / "simulate.cpython-312.pyc").write_bytes(b"cache")
            (seed / ".DS_Store").write_bytes(b"metadata")
            workspace = root / "run-workspace"
            task = BenchmarkTask(
                task_id="001-test",
                title="test",
                domain="test",
                difficulty="test",
                task_dir=task_dir,
                metadata={"workspace_dir": "workspace"},
                definition_sha256="0" * 64,
            )

            runner_module.stage_workspace_seed(task, workspace)

            self.assertTrue((workspace / "repo" / "simulate.py").is_file())
            self.assertFalse((workspace / "repo" / "__pycache__").exists())
            self.assertFalse((workspace / "repo" / ".DS_Store").exists())


class SuiteSnapshotTests(unittest.TestCase):
    def make_source(self, root: Path) -> Path:
        source = root / "source-tasks"
        for number in (1, 2):
            task = source / f"{number:03d}-test"
            (task / "reference").mkdir(parents=True)
            (task / "task.yaml").write_text(
                f"task_id: {number:03d}-test\n",
                encoding="utf-8",
            )
            (task / "oracle_grade.py").write_text(
                f"EXPECTED = {number}\n",
                encoding="utf-8",
            )
            (task / "reference" / "answer.txt").write_text(
                f"{number}\n",
                encoding="utf-8",
            )
        return source

    def make_writable(self, root: Path) -> None:
        if not root.exists():
            return
        for path in root.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        root.chmod(0o755)

    def test_snapshot_is_immutable_and_detached_from_live_tasks(self) -> None:
        temporary = Path(tempfile.mkdtemp())
        try:
            source = self.make_source(temporary)
            legacy_rubric = source / "001-test" / "llm_rubric.py"
            legacy_rubric.write_text("LEGACY = 1\n", encoding="utf-8")
            digest_without_rubric, _ = runner_module.task_tree_sha256(source)
            legacy_rubric.write_text("LEGACY = 2\n", encoding="utf-8")
            self.assertEqual(
                runner_module.task_tree_sha256(source)[0],
                digest_without_rubric,
            )
            run_root = temporary / "run"
            run_root.mkdir()
            with (
                mock.patch.object(runner_module, "TASKS_DIR", source),
                mock.patch.object(
                    runner_module,
                    "BENCHMARK_TASK_COUNT",
                    2,
                ),
            ):
                snapshot, digest, controller_sha = runner_module.freeze_suite_snapshot(
                    run_root
                )
                original = (snapshot / "001-test" / "oracle_grade.py").read_text(
                    encoding="utf-8"
                )
                (source / "001-test" / "oracle_grade.py").write_text(
                    "EXPECTED = 999\n",
                    encoding="utf-8",
                )
                resumed, resumed_digest, resumed_controller_sha = (
                    runner_module.freeze_suite_snapshot(run_root)
                )

            self.assertEqual(resumed, snapshot)
            self.assertEqual(resumed_digest, digest)
            self.assertEqual(resumed_controller_sha, controller_sha)
            self.assertEqual(
                (snapshot / "001-test" / "oracle_grade.py").read_text(encoding="utf-8"),
                original,
            )
            self.assertFalse(
                (snapshot / "001-test" / "oracle_grade.py").stat().st_mode & 0o222
            )
            self.assertFalse((snapshot / "001-test" / "llm_rubric.py").exists())
        finally:
            self.make_writable(temporary / "run" / "suite_snapshot")
            shutil.rmtree(temporary, ignore_errors=True)

    def test_snapshot_tampering_fails_resume(self) -> None:
        temporary = Path(tempfile.mkdtemp())
        try:
            source = self.make_source(temporary)
            run_root = temporary / "run"
            run_root.mkdir()
            with (
                mock.patch.object(runner_module, "TASKS_DIR", source),
                mock.patch.object(
                    runner_module,
                    "BENCHMARK_TASK_COUNT",
                    2,
                ),
            ):
                snapshot, _, _ = runner_module.freeze_suite_snapshot(run_root)
                oracle = snapshot / "002-test" / "oracle_grade.py"
                oracle.chmod(0o644)
                oracle.write_text("EXPECTED = -1\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    RuntimeError,
                    "integrity manifest",
                ):
                    runner_module.freeze_suite_snapshot(run_root)
        finally:
            self.make_writable(temporary / "run" / "suite_snapshot")
            shutil.rmtree(temporary, ignore_errors=True)


class ProtocolReportTests(unittest.TestCase):
    def test_plain_final_response_is_valid(self) -> None:
        report = protocol_report(
            {
                "payloads": [{"text": "Done."}],
                "meta": {
                    "finalAssistantVisibleText": "Done.",
                    "replayInvalid": True,
                    "aborted": False,
                    "livenessState": "working",
                    "stopReason": "stop",
                },
            }
        )
        self.assertFalse(report["fatal"])
        self.assertTrue(report["replay_invalid"])
        self.assertFalse(report["automatic_replay_safe"])
        self.assertIsNone(protocol_failure_status(report))

    def test_openclaw_trace_integrity_accepts_paired_tool_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "session.jsonl"
            events = [
                {"type": "session", "id": "s"},
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "toolCall", "id": "call-1", "name": "read"}
                        ],
                    },
                },
                {
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "toolCallId": "call-1",
                        "content": [{"type": "text", "text": "ok"}],
                    },
                },
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "done"}],
                    },
                },
            ]
            session.write_text(
                "".join(f"{json.dumps(event)}\n" for event in events),
                encoding="utf-8",
            )

            report = validate_openclaw_trace(root, ["session.jsonl"])

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["tool_calls"], 1)
        self.assertEqual(report["tool_results"], 1)
        self.assertEqual(report["tool_failures"], 0)
        self.assertEqual(report["tool_successes"], 1)

    def test_openclaw_trace_counts_raw_tool_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "session.jsonl"
            events = [{"type": "session", "id": "s"}]
            for call_id, result_fields in (
                ("ok", {"content": [{"type": "text", "text": "done"}]}),
                (
                    "flagged",
                    {
                        "isError": True,
                        "content": [{"type": "text", "text": "permission denied"}],
                    },
                ),
                (
                    "exit",
                    {
                        "details": {"status": "completed", "exitCode": 2},
                        "content": [{"type": "text", "text": "failed"}],
                    },
                ),
            ):
                events.extend(
                    [
                        {
                            "type": "message",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "toolCall",
                                        "id": call_id,
                                        "name": "exec",
                                    }
                                ],
                            },
                        },
                        {
                            "type": "message",
                            "message": {
                                "role": "toolResult",
                                "toolCallId": call_id,
                                **result_fields,
                            },
                        },
                    ]
                )
            events.append(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "done"}],
                    },
                }
            )
            session.write_text(
                "".join(f"{json.dumps(event)}\n" for event in events),
                encoding="utf-8",
            )

            report = validate_openclaw_trace(root, ["session.jsonl"])

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["tool_calls"], 3)
        self.assertEqual(report["tool_results"], 3)
        self.assertEqual(report["tool_failures"], 2)
        self.assertEqual(report["tool_successes"], 1)

    def test_openclaw_trace_integrity_accepts_reused_completed_call_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "session.jsonl"
            events = [{"type": "session", "id": "s"}]
            for command in ("first", "second"):
                events.extend(
                    [
                        {
                            "type": "message",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "toolCall",
                                        "id": "exec_0",
                                        "name": "exec",
                                        "arguments": {"command": command},
                                    }
                                ],
                            },
                        },
                        {
                            "type": "message",
                            "message": {
                                "role": "toolResult",
                                "toolCallId": "exec_0",
                                "content": [{"type": "text", "text": "ok"}],
                            },
                        },
                    ]
                )
            session.write_text(
                "".join(f"{json.dumps(event)}\n" for event in events),
                encoding="utf-8",
            )

            report = validate_openclaw_trace(root, ["session.jsonl"])

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["tool_calls"], 2)
        self.assertEqual(report["tool_results"], 2)

    def test_openclaw_trace_integrity_rejects_dangling_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "session.jsonl").write_text(
                '{"type":"message","message":{"role":"assistant",'
                '"content":[{"type":"toolCall","id":"call-1"}]}}\n',
                encoding="utf-8",
            )

            report = validate_openclaw_trace(root, ["session.jsonl"])

        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            any("dangling tool call" in item for item in report["problems"])
        )

    def test_abandoned_error_payload_is_non_deliverable(self) -> None:
        report = protocol_report(
            {
                "payloads": [
                    {
                        "text": (
                            "⚠️ Agent couldn't generate a response. "
                            "Please verify before retrying."
                        )
                    }
                ],
                "meta": {
                    "aborted": False,
                    "livenessState": "abandoned",
                },
            }
        )
        self.assertTrue(report["error_payload"])
        self.assertTrue(report["non_deliverable_terminal_turn"])
        self.assertTrue(report["fatal"])
        self.assertEqual(protocol_failure_status(report), "protocol_error")

    def test_native_non_deliverable_terminal_error_is_fatal(self) -> None:
        report = protocol_report(
            {"payloads": [], "meta": {"aborted": False}},
            stderr=(
                "[agent/embedded] incomplete turn detected: "
                "stopReason=length payloads=0 — surfacing error to user\n"
                "terminalError=non_deliverable_terminal_turn"
            ),
        )
        self.assertTrue(report["native_non_deliverable"])
        self.assertTrue(report["non_deliverable_terminal_turn"])
        self.assertEqual(protocol_failure_status(report), "protocol_error")

    def test_terminal_error_metadata_is_non_deliverable(self) -> None:
        report = protocol_report(
            {
                "payloads": [],
                "meta": {
                    "aborted": False,
                    "finalStatus": "error",
                    "terminalError": "non_deliverable_terminal_turn",
                },
            }
        )
        self.assertEqual(
            report["terminal_error"],
            "non_deliverable_terminal_turn",
        )
        self.assertEqual(report["final_status"], "error")
        self.assertTrue(report["non_deliverable_terminal_turn"])
        self.assertEqual(protocol_failure_status(report), "protocol_error")

    def test_empty_length_stop_is_non_deliverable(self) -> None:
        report = protocol_report(
            {
                "payloads": [],
                "meta": {
                    "aborted": False,
                    "livenessState": "working",
                    "stopReason": "length",
                },
            }
        )
        self.assertTrue(report["length_without_payload"])
        self.assertTrue(report["non_deliverable_terminal_turn"])
        self.assertEqual(protocol_failure_status(report), "protocol_error")

    def test_deliverable_plain_text_is_not_mistaken_for_error_payload(
        self,
    ) -> None:
        report = protocol_report(
            {
                "payloads": [
                    {
                        "text": (
                            "The incident log says an agent couldn't "
                            "generate a response; remediation is complete."
                        )
                    }
                ],
                "meta": {
                    "aborted": False,
                    "livenessState": "working",
                    "stopReason": "stop",
                },
            }
        )
        self.assertFalse(report["error_payload"])
        self.assertFalse(report["non_deliverable_terminal_turn"])
        self.assertIsNone(protocol_failure_status(report))

    def test_successful_reasoning_continuation_log_is_not_fatal(self) -> None:
        report = protocol_report(
            {
                "payloads": [{"text": "Done."}],
                "meta": {
                    "finalAssistantVisibleText": "Done.",
                    "aborted": False,
                    "livenessState": "working",
                    "stopReason": "stop",
                },
            },
            stderr=(
                "reasoning-only assistant turn detected — retrying 1/2 "
                "with visible-answer continuation"
            ),
        )
        self.assertFalse(report["native_non_deliverable"])
        self.assertFalse(report["fatal"])
        self.assertIsNone(protocol_failure_status(report))

    def test_unexecuted_tool_call_is_fatal(self) -> None:
        report = protocol_report(
            {
                "meta": {
                    "finalAssistantRawText": (
                        '<|tool_call>{"name":"exec","arguments":{}}</tool_call>'
                    )
                }
            }
        )
        self.assertTrue(report["dangling_tool_call"])
        self.assertTrue(report["fatal"])
        self.assertEqual(protocol_failure_status(report), "protocol_error")

    def test_aborted_session_is_fatal(self) -> None:
        report = protocol_report({"meta": {"aborted": True}})
        self.assertTrue(report["aborted"])
        self.assertTrue(report["fatal"])
        self.assertFalse(report["timed_out"])
        self.assertEqual(protocol_failure_status(report), "protocol_error")

    def test_provider_timeout_phase_is_retryable_infrastructure(self) -> None:
        report = protocol_report(
            {
                "meta": {
                    "aborted": True,
                    "replayInvalid": True,
                    "timeoutPhase": "provider",
                }
            }
        )
        self.assertTrue(report["aborted"])
        self.assertTrue(report["replay_invalid"])
        self.assertTrue(report["fatal"])
        self.assertFalse(report["timed_out"])
        self.assertTrue(report["provider_request_timeout"])
        self.assertEqual(report["timeout_phase"], "provider")
        self.assertEqual(protocol_failure_status(report), "infrastructure_error")
        self.assertEqual(agent_round_failure_status(1, report), "infrastructure_error")

    def test_explicit_provider_timeout_payload_is_retryable(self) -> None:
        report = protocol_report(
            {
                "payloads": [
                    {
                        "text": (
                            "Request timed out before a response was "
                            "generated. Please try again."
                        )
                    }
                ],
                "meta": {"aborted": True},
            }
        )
        self.assertTrue(report["explicit_timeout"])
        self.assertFalse(report["timed_out"])
        self.assertTrue(report["provider_request_timeout"])
        self.assertEqual(protocol_failure_status(report), "infrastructure_error")

    def test_timeout_signal_requires_aborted_session(self) -> None:
        report = protocol_report(
            {
                "payloads": [{"text": "Request timed out."}],
                "meta": {"aborted": False, "timeoutPhase": "provider"},
            }
        )
        self.assertTrue(report["explicit_timeout"])
        self.assertFalse(report["timed_out"])
        self.assertFalse(report["provider_request_timeout"])
        self.assertIsNone(protocol_failure_status(report))

    def test_embedded_timeout_stderr_overrides_exit_code_one(self) -> None:
        stderr = (
            "[agent/embedded] embedded run timeout: "
            "runId=8b25c284-e07e-480f-bdd0-95a32dadc904 "
            "sessionId=hb-183-c16cbf0530 timeoutMs=1200000\n"
            "[agent/embedded] embedded attempt cleanup detected session "
            "takeover after prompt failure; preserving prompt error: "
            "promptError=request timed out\n"
            "[diagnostic] lane task error: lane=main durationMs=1202506 "
            'error="EmbeddedAttemptSessionTakeoverError: request timed out"\n'
            "EmbeddedAttemptSessionTakeoverError: request timed out\n"
        )
        report = protocol_report({}, stderr=stderr)

        self.assertTrue(report["stderr_timeout"])
        self.assertTrue(report["explicit_timeout"])
        self.assertTrue(report["timed_out"])
        self.assertTrue(report["fatal"])
        self.assertEqual(protocol_failure_status(report), "timeout")
        self.assertEqual(agent_round_failure_status(1, report), "timeout")

    def test_provider_failover_timeout_stderr_is_not_a_model_error(self) -> None:
        stderr = (
            "[agent/embedded] embedded run completed: "
            "isError=true error=LLM request timed out. rawError=terminated\n"
            "FailoverError: LLM request timed out.\n"
        )
        report = protocol_report({}, stderr=stderr)
        self.assertTrue(report["stderr_timeout"])
        self.assertFalse(report["timed_out"])
        self.assertTrue(report["provider_request_timeout"])
        self.assertEqual(protocol_failure_status(report), "infrastructure_error")
        self.assertEqual(agent_round_failure_status(1, report), "infrastructure_error")

    def test_grader_artifact_report_is_authoritative(self) -> None:
        task = BenchmarkTask(
            task_id="test",
            title="test",
            domain="test",
            difficulty="test",
            task_dir=ROOT,
            metadata={"required_artifacts": ["result.json"]},
            definition_sha256="0" * 64,
        )
        report = authoritative_artifact_report(
            task,
            ROOT / "path-not-readable-by-controller",
            {
                "required_artifact_contract": {
                    "declared": ["result.json"],
                    "present": ["result.json"],
                    "missing": [],
                    "invalid": {},
                    "complete": True,
                }
            },
        )
        self.assertTrue(report["complete"])
        self.assertEqual(report["completion_ratio"], 1.0)


class SuiteResumeGuardTests(unittest.TestCase):
    def make_state(
        self,
        run_root: Path,
        *,
        definition_sha256: str,
        runtime_images: dict[str, str] | None = None,
        isolation: dict[str, object] | None = None,
        controller_runtime: dict[str, object] | None = None,
        task_set_name: str | None = None,
        selection_identity: dict[str, object] | None = None,
    ) -> SuiteState:
        task = BenchmarkTask(
            task_id="001-test",
            title="test",
            domain="test",
            difficulty="test",
            task_dir=run_root,
            metadata={},
            definition_sha256=definition_sha256,
        )
        runtime = controller_runtime or {
            "schema_version": 1,
            "implementation": "TestPython",
            "python_version": "3.10.0",
            "python_executable_sha256": "d" * 64,
            "distributions": {
                "PyYAML": {
                    "version": "6.0",
                    "distribution_tree_sha256": "e" * 64,
                    "file_count": 1,
                },
                "tomli": {
                    "version": "2.0",
                    "distribution_tree_sha256": "f" * 64,
                    "file_count": 1,
                },
            },
            "uv_lock_sha256": "0" * 64,
        }
        return SuiteState(
            run_root=run_root,
            tasks=[task],
            model="model",
            base_url="http://model/v1",
            model_service_id="test-model-service",
            image="harness@sha256:abc",
            harness_id="openclaw",
            harness_protocol="chat_completions",
            harness_version="1.0",
            trace_complete=True,
            workers=1,
            max_tokens=128,
            temperature=0.0,
            data_parallel_rank=None,
            thinking="low",
            timeout_override=60,
            max_agent_turns=150,
            runtime_images=runtime_images or {"grader": "sha256:grader"},
            isolation=isolation
            or {
                "schema_version": 1,
                "level": "transitional",
                "engine": "docker",
                "default_runtime": "runc",
            },
            selected_task_ids={"001-test"},
            shard_index=0,
            shard_count=1,
            suite_snapshot_sha256="b" * 64,
            controller_sha256="c" * 64,
            controller_runtime=runtime,
            controller_runtime_id=runner_module._controller_runtime_id(runtime),
            grading="oracle_only",
            task_set_name=task_set_name,
            selection_identity=selection_identity,
        )

    def test_checkpoint_writes_file_dashboard_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_state(root, definition_sha256="a" * 64)
            script = (root / "summary.js").read_text(encoding="utf-8")
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(script.startswith("window.HARNESSBENCH_SUMMARY = {"))
            self.assertTrue(script.endswith(";\n"))
            self.assertIsInstance(summary["controller_runtime"], dict)
            self.assertEqual(
                summary["controller_runtime_id"],
                runner_module._controller_runtime_id(summary["controller_runtime"]),
            )

    def test_checkpoint_aggregates_safety_only_over_scoreable_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = self.make_state(root, definition_sha256="a" * 64)
            state.update(
                "001-test",
                status="completed",
                outcome_score=0.0,
                task_success=False,
                safety_gate_failed=True,
                failed_gate_count=1,
                failed_core_count=0,
            )
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["safety_gate_evaluated_count"], 1)
        self.assertEqual(summary["safety_gate_failed_count"], 1)
        self.assertEqual(summary["safety_gate_failure_rate"], 1.0)
        self.assertEqual(summary["failed_gate_total"], 1)
        self.assertEqual(summary["failed_core_total"], 0)

    def test_checkpoint_aggregates_provider_tokens_and_trajectory_length(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = self.make_state(root, definition_sha256="a" * 64)
            state.update(
                "001-test",
                status="completed",
                provider_trace={
                    "requests_with_usage": 2,
                    "requests_eligible_for_usage": 2,
                    "token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 10,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 5,
                        "total_tokens": 120,
                    },
                    "trajectory_length": {
                        "provider_transactions": 2,
                        "completed_transactions": 2,
                        "request_context_events": 6,
                        "response_events": 3,
                        "user_events": 2,
                        "assistant_events": 2,
                        "tool_calls": 1,
                        "tool_results": 1,
                        "raw_request_bytes": 1000,
                        "raw_response_bytes": 500,
                    },
                },
            )
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))

        provider = summary["provider_trace"]
        self.assertEqual(provider["sessions_recorded"], 1)
        self.assertEqual(provider["sessions_expected"], 1)
        self.assertEqual(provider["token_coverage"], 1.0)
        self.assertEqual(provider["token_usage"]["total_tokens"], 120)
        self.assertEqual(provider["trajectory_length"]["tool_calls"], 1)
        self.assertEqual(provider["trajectory_length"]["raw_request_bytes"], 1000)

    def test_checkpoint_excludes_infrastructure_from_score_and_safety(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = self.make_state(root, definition_sha256="a" * 64)
            state.update(
                "001-test",
                status="infrastructure_error",
                outcome_score=1.0,
                task_success=True,
                safety_gate_failed=True,
                failed_gate_count=4,
                failed_core_count=3,
            )
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))

        self.assertIsNone(summary["mean_oracle_completed"])
        self.assertEqual(summary["safety_gate_evaluated_count"], 0)
        self.assertEqual(summary["safety_gate_failed_count"], 0)
        self.assertIsNone(summary["safety_gate_failure_rate"])
        self.assertEqual(summary["failed_gate_total"], 0)
        self.assertEqual(summary["failed_core_total"], 0)

    def test_resume_rejects_changed_task_definition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_state(root, definition_sha256="a" * 64)
            with self.assertRaisesRegex(ValueError, "suite_definition_sha256"):
                self.make_state(root, definition_sha256="b" * 64)

    def test_resume_rejects_changed_named_selection_identity(self) -> None:
        original = runner_module.make_task_selection_identity(
            mode="named",
            task_ids=("001-test",),
            task_set="curated",
            manifest_sha256="sha256:" + "1" * 64,
        )
        changed = {
            **original,
            "manifest_sha256": "sha256:" + "2" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_state(
                root,
                definition_sha256="a" * 64,
                task_set_name="curated",
                selection_identity=original,
            )
            with self.assertRaisesRegex(ValueError, "selection"):
                self.make_state(
                    root,
                    definition_sha256="a" * 64,
                    task_set_name="curated",
                    selection_identity=changed,
                )

    def test_resume_rejects_changed_runtime_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_state(
                root,
                definition_sha256="a" * 64,
                runtime_images={"grader": "sha256:old"},
            )
            with self.assertRaisesRegex(ValueError, "runtime_images"):
                self.make_state(
                    root,
                    definition_sha256="a" * 64,
                    runtime_images={"grader": "sha256:new"},
                )

    def test_resume_rejects_changed_isolation_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_state(
                root,
                definition_sha256="a" * 64,
                isolation={"schema_version": 1, "level": "transitional"},
            )
            with self.assertRaisesRegex(ValueError, "isolation"):
                self.make_state(
                    root,
                    definition_sha256="a" * 64,
                    isolation={"schema_version": 1, "level": "L2"},
                )

    def test_resume_rejects_changed_controller_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = {
                "schema_version": 1,
                "implementation": "CPython",
                "python_version": "3.10.1",
            }
            changed = {**original, "python_version": "3.10.2"}
            self.make_state(
                root,
                definition_sha256="a" * 64,
                controller_runtime=original,
            )
            with self.assertRaisesRegex(ValueError, "controller_runtime"):
                self.make_state(
                    root,
                    definition_sha256="a" * 64,
                    controller_runtime=changed,
                )


class ControllerRuntimeIdentityTests(unittest.TestCase):
    class FakeDistribution:
        def __init__(self, root: Path, *, version: str = "1.0") -> None:
            self.root = root
            self.version = version
            self.files = [
                PurePosixPath("package/__init__.py"),
                PurePosixPath("package/data.txt"),
                PurePosixPath("package/__pycache__/ignored.pyc"),
            ]

        def locate_file(self, entry: PurePosixPath) -> Path:
            return self.root / entry.as_posix()

    def make_distribution_tree(self, root: Path, *, content: str = "data") -> None:
        package = root / "package"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
        (package / "data.txt").write_text(content, encoding="utf-8")

    def test_distribution_identity_is_path_independent_and_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_root = root / "first-install"
            second_root = root / "other" / "install"
            self.make_distribution_tree(first_root)
            self.make_distribution_tree(second_root)
            first = self.FakeDistribution(first_root)
            second = self.FakeDistribution(second_root)
            with mock.patch(
                "run_benchmark.importlib.metadata.distribution",
                return_value=first,
            ):
                first_identity = runner_module._distribution_identity("example")
                repeated_identity = runner_module._distribution_identity("example")
            with mock.patch(
                "run_benchmark.importlib.metadata.distribution",
                return_value=second,
            ):
                second_identity = runner_module._distribution_identity("example")

        self.assertEqual(first_identity, repeated_identity)
        self.assertEqual(first_identity, second_identity)
        self.assertNotIn(str(root), json.dumps(first_identity))

    def test_package_content_and_version_change_runtime_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_distribution_tree(root)
            distribution = self.FakeDistribution(root)
            with mock.patch(
                "run_benchmark.importlib.metadata.distribution",
                return_value=distribution,
            ):
                original = runner_module._distribution_identity("example")
                (root / "package" / "data.txt").write_text(
                    "changed",
                    encoding="utf-8",
                )
                changed_tree = runner_module._distribution_identity("example")
                distribution.version = "2.0"
                changed_version = runner_module._distribution_identity("example")

        self.assertNotEqual(
            original["distribution_tree_sha256"],
            changed_tree["distribution_tree_sha256"],
        )
        base_payload = {"distribution": original}
        tree_payload = {"distribution": changed_tree}
        version_payload = {"distribution": changed_version}
        self.assertNotEqual(
            runner_module._controller_runtime_id(base_payload),
            runner_module._controller_runtime_id(tree_payload),
        )
        self.assertNotEqual(
            runner_module._controller_runtime_id(tree_payload),
            runner_module._controller_runtime_id(version_payload),
        )

    def test_missing_dependency_and_non_regular_file_are_rejected(self) -> None:
        with mock.patch(
            "run_benchmark.importlib.metadata.distribution",
            side_effect=runner_module.importlib.metadata.PackageNotFoundError(
                "missing"
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "distribution is missing"):
                runner_module._distribution_identity("missing")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_distribution_tree(root)
            (root / "package" / "data.txt").unlink()
            (root / "package" / "data.txt").mkdir()
            with mock.patch(
                "run_benchmark.importlib.metadata.distribution",
                return_value=self.FakeDistribution(root),
            ):
                with self.assertRaisesRegex(RuntimeError, "not a regular file"):
                    runner_module._distribution_identity("example")

    def test_runtime_identity_is_stable_and_contains_no_absolute_paths(self) -> None:
        first, first_id = controller_runtime_identity()
        second, second_id = controller_runtime_identity()
        self.assertEqual(first, second)
        self.assertEqual(first_id, second_id)
        self.assertEqual(first["schema_version"], 1)
        self.assertIn("PyYAML", first["distributions"])
        serialized = json.dumps(first, sort_keys=True)
        self.assertNotIn(str(ROOT), serialized)
        self.assertNotIn(str(Path(sys.executable).parent), serialized)

    def test_python_310_runtime_requires_tomli_identity(self) -> None:
        package_identity = {
            "version": "1",
            "distribution_tree_sha256": "a" * 64,
            "file_count": 1,
        }
        with (
            mock.patch.object(runner_module.sys, "version_info", (3, 10, 14)),
            mock.patch(
                "run_benchmark._distribution_identity",
                return_value=package_identity,
            ) as distribution_identity,
        ):
            runtime, _ = controller_runtime_identity()

        self.assertEqual(runtime["python_version"], "3.10.14")
        self.assertEqual(
            [call.args[0] for call in distribution_identity.call_args_list],
            ["PyYAML", "tomli"],
        )


class TrustedHelperTests(unittest.TestCase):
    @mock.patch("run_benchmark.docker")
    def test_helper_passes_fixed_paths_and_read_only_mounts(
        self,
        docker_mock: mock.Mock,
    ) -> None:
        docker_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            runtime = root / "runtime"
            workspace.mkdir()
            runtime.mkdir()
            task = BenchmarkTask(
                task_id="test",
                title="test",
                domain="test",
                difficulty="test",
                task_dir=root,
                metadata={},
                definition_sha256="0" * 64,
            )
            helper = hook_context(
                task,
                workspace,
                runtime,
                ["--label", "test=true"],
            )["run_trusted_helper"]
            helper(
                ["python3", "-c", "pass"],
                workspace_read_only=True,
                runtime_read_only=True,
                workdir="/workspace",
            )
        arguments = docker_mock.call_args.args
        self.assertIn(f"{os.getuid()}:{os.getgid()}", arguments)
        self.assertIn("HB_WORKSPACE=/workspace", arguments)
        self.assertIn("HB_RUNTIME=/runtime", arguments)
        self.assertIn("GIT_CONFIG_VALUE_0=*", arguments)
        self.assertIn(
            f"type=bind,src={workspace.resolve()},dst=/workspace,readonly",
            arguments,
        )
        self.assertIn(
            f"type=bind,src={runtime.resolve()},dst=/runtime,readonly",
            arguments,
        )


if __name__ == "__main__":
    unittest.main()
