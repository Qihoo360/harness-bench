from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from harness_runtime import (  # noqa: E402
    HARNESS_SHARED_SOURCE_FILES,
    SPECS,
    contract_protocol_report,
    harness_source_sha256,
    render_harness_config,
    run_contract_round,
)
from container_runtime import (  # noqa: E402
    GRADER_BASE_IMAGE,
    GRADER_IMAGE,
    GRADER_SOURCE_FILES,
    GRADER_SOURCE_LABEL,
    STAGER_IMAGE,
    _grader_build_args,
    build_images,
    docker_security_identity,
    grader_source_sha256,
)
from openclaw_image import (  # noqa: E402
    OPENCLAW_SOURCE_FILES,
    OPENCLAW_SOURCE_LABEL,
    openclaw_source_sha256,
)
from openclaw_runtime import (  # noqa: E402
    DEFAULT_OPENCLAW_IMAGE,
    OFFICIAL_OPENCLAW_IMAGE,
    openclaw_config,
)


class HarnessRuntimeTests(unittest.TestCase):
    def render(self, harness_id: str) -> str:
        return render_harness_config(
            SPECS[harness_id],
            base_url="http://model.internal:8000/v1/",
            model="gemma-4-31b-it",
            max_tokens=8192,
            temperature=0.7,
        )

    def test_every_declared_context_exists(self) -> None:
        for spec in SPECS.values():
            with self.subTest(harness=spec.harness_id):
                self.assertIsNotNone(spec.docker_context)
                self.assertTrue((spec.docker_context / "Dockerfile").is_file())
                self.assertTrue((spec.docker_context / "entrypoint.sh").is_file())
                self.assertRegex(harness_source_sha256(spec), r"^[0-9a-f]{64}$")

    @mock.patch("container_runtime.docker")
    def test_docker_security_identity_records_transitional_runtime(
        self,
        docker_mock: mock.Mock,
    ) -> None:
        docker_mock.return_value = mock.Mock(
            stdout=json.dumps(
                {
                    "security_options": [
                        "name=seccomp,profile=builtin",
                        "name=apparmor",
                        "name=cgroupns",
                    ],
                    "default_runtime": "runc",
                    "cgroup_driver": "systemd",
                    "server_version": "29.4.2",
                }
            )
        )

        identity = docker_security_identity()

        self.assertEqual(identity["level"], "transitional")
        self.assertFalse(identity["rootless"])
        self.assertEqual(identity["default_runtime"], "runc")
        self.assertEqual(identity["server_version"], "29.4.2")
        self.assertEqual(
            identity["security_options"],
            sorted(identity["security_options"]),
        )

    @mock.patch("container_runtime.docker")
    def test_docker_security_identity_requires_full_l2_boundary(
        self,
        docker_mock: mock.Mock,
    ) -> None:
        docker_mock.return_value = mock.Mock(
            stdout=json.dumps(
                {
                    "security_options": [
                        "name=rootless",
                        "name=seccomp,profile=builtin",
                        "name=apparmor",
                        "name=cgroupns",
                    ],
                    "default_runtime": "runsc",
                    "cgroup_driver": "systemd",
                    "server_version": "test",
                }
            )
        )

        identity = docker_security_identity()

        self.assertEqual(identity["level"], "L2")
        self.assertTrue(identity["rootless"])

    def test_images_are_versioned_non_root_and_do_not_nest_docker(self) -> None:
        for spec in SPECS.values():
            with self.subTest(harness=spec.harness_id):
                dockerfile = (spec.docker_context / "Dockerfile").read_text()
                entrypoint = (spec.docker_context / "entrypoint.sh").read_text()
                self.assertNotIn(":latest", dockerfile)
                self.assertIn("USER 1000:1000", dockerfile)
                self.assertNotIn("/var/run/docker.sock", entrypoint)
                self.assertNotIn("docker run", entrypoint)

    def test_every_harness_image_inherits_shared_python_task_toolchain(self) -> None:
        for spec in SPECS.values():
            with self.subTest(harness=spec.harness_id):
                dockerfile = (spec.docker_context / "Dockerfile").read_text()
                self.assertIn("docker/openclaw/requirements.lock", dockerfile)
                self.assertIn(
                    "COPY --from=task-toolchain /usr/local/ /usr/local/",
                    dockerfile,
                )
                self.assertIn("verify_toolchain.py --python-only", dockerfile)

    def test_every_harness_installs_sqlite_cli_for_sql_tasks(self) -> None:
        for harness_id in SPECS:
            with self.subTest(harness=harness_id):
                dockerfile = (SPECS[harness_id].docker_context / "Dockerfile").read_text()
                self.assertRegex(dockerfile, r"\bsqlite3(?:\s|\\)")

        verifier = (ROOT / "docker" / "openclaw" / "verify_toolchain.py").read_text()
        self.assertIn('["sqlite3", ":memory:", "SELECT 1;"]', verifier)

    def test_harness_source_identity_covers_shared_task_toolchain(self) -> None:
        self.assertEqual(
            {path.relative_to(ROOT) for path in HARNESS_SHARED_SOURCE_FILES},
            {
                Path(".dockerignore"),
                Path("docker/openclaw/requirements.lock"),
                Path("docker/openclaw/verify_toolchain.py"),
                Path("docker/harnesses/multimodal-npm"),
            },
        )
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("!docker/harnesses/**", dockerignore)

    def test_hermes_controlled_profile(self) -> None:
        config = yaml.safe_load(self.render("hermes"))
        self.assertEqual(config["model"]["provider"], "custom:harnessbench")
        self.assertEqual(config["providers"]["harnessbench"]["transport"], "chat_completions")
        self.assertEqual(config["agent"]["reasoning_effort"], "high")
        self.assertFalse(config["memory"]["memory_enabled"])
        self.assertEqual(config["terminal"]["backend"], "local")
        self.assertIn("delegation", config["agent"]["disabled_toolsets"])

    def test_hermes_gpt_uses_codex_responses(self) -> None:
        config = yaml.safe_load(
            render_harness_config(
                SPECS["hermes"],
                base_url="http://model.example/v1",
                model="gpt-5.6-luna",
                max_tokens=32768,
                temperature=1.0,
                thinking="high",
                context_window=200000,
                max_agent_turns=150,
            )
        )
        self.assertEqual(config["providers"]["harnessbench"]["transport"], "codex_responses")
        self.assertEqual(config["agent"]["reasoning_effort"], "high")

    def test_hermes_runtime_is_isolated_from_the_task_python(self) -> None:
        dockerfile = (SPECS["hermes"].docker_context / "Dockerfile").read_text()
        entrypoint = (SPECS["hermes"].docker_context / "entrypoint.sh").read_text()
        self.assertIn(
            "ln -sf /usr/bin/python3.13 /opt/hermes/.venv/bin/python",
            dockerfile,
        )
        self.assertIn("ln -sf /usr/local/bin/python3 /usr/bin/python3", dockerfile)
        self.assertIn("/opt/hermes/.venv/bin/hermes chat", entrypoint)

    def test_nanobot_has_no_fallback(self) -> None:
        config = json.loads(self.render("nanobot"))
        self.assertEqual(
            config["agents"]["defaults"]["fallbackModels"],
            [],
        )
        self.assertEqual(
            config["modelPresets"]["harnessbench"]["provider"],
            "vllm",
        )
        self.assertEqual(
            config["tools"]["exec"]["allowedEnvKeys"],
            [
                "GIT_CONFIG_GLOBAL",
                "PATH",
                "NODE_PATH",
                "PLAYWRIGHT_BROWSERS_PATH",
                "npm_config_cache",
            ],
        )

    def test_nanobot_does_not_apply_its_short_default_model_timeout(self) -> None:
        dockerfile = (SPECS["nanobot"].docker_context / "Dockerfile").read_text()
        self.assertIn("NANOBOT_STREAM_IDLE_TIMEOUT_S=7200", dockerfile)
        self.assertIn("NANOBOT_OPENAI_COMPAT_TIMEOUT_S=7200", dockerfile)

    def test_opencode_denies_unlisted_tools(self) -> None:
        config = json.loads(self.render("opencode"))
        self.assertEqual(config["permission"]["*"], "deny")
        self.assertEqual(config["permission"]["bash"], "allow")
        self.assertEqual(config["model"], "harnessbench/gemma-4-31b-it")

    def test_antigravity_configuration_and_entrypoint(self) -> None:
        config = json.loads(self.render("antigravity"))
        self.assertEqual(config["model"], "gemma-4-31b-it")
        self.assertEqual(config["base_url"], "http://model.internal:8000/v1")
        self.assertEqual(config["max_tokens"], 8192)
        self.assertEqual(config["thinking"], "high")

        entrypoint = (
            SPECS["antigravity"].docker_context / "entrypoint.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('"read_url(*)"', entrypoint)
        self.assertNotIn('"allow"', entrypoint)
        self.assertIn("agy --print", entrypoint)
        self.assertIn("--dangerously-skip-permissions", entrypoint)
        self.assertIn("litellm", entrypoint)
        self.assertIn("litellm_config.yaml", entrypoint)

    def test_protocol_specific_harnesses(self) -> None:
        claude = json.loads(self.render("claude-code"))
        codex = self.render("codex")
        self.assertEqual(
            claude["base_url"],
            "http://model.internal:8000",
        )
        self.assertEqual(claude["thinking"], "high")
        claude_entrypoint = (
            SPECS["claude-code"].docker_context / "entrypoint.sh"
        ).read_text()
        self.assertIn("CLAUDE_CODE_EFFORT_LEVEL", claude_entrypoint)
        self.assertIn('wire_api = "responses"', codex)
        self.assertIn(
            'base_url = "http://model.internal:8000/v1"',
            codex,
        )
        self.assertIn('model_reasoning_effort = "high"', codex)

    def test_reasoning_configuration_is_model_and_protocol_specific(self) -> None:
        nanobot_gpt = json.loads(
            render_harness_config(
                SPECS["nanobot"],
                base_url="http://model.internal:8000/v1",
                model="openai/gpt-5.6-luna",
                max_tokens=32768,
                temperature=1.0,
                thinking="high",
            )
        )
        self.assertEqual(
            nanobot_gpt["providers"]["openai"]["apiType"],
            "responses",
        )
        self.assertEqual(
            nanobot_gpt["modelPresets"]["harnessbench"]["reasoningEffort"],
            "high",
        )
        opencode_gpt = json.loads(
            render_harness_config(
                SPECS["opencode"],
                base_url="http://model.internal:8000/v1",
                model="openai/gpt-5.6-luna",
                max_tokens=32768,
                temperature=1.0,
                thinking="high",
            )
        )
        self.assertEqual(
            opencode_gpt["provider"]["harnessbench"]["npm"],
            "@ai-sdk/openai",
        )
        openclaw_gpt = openclaw_config(
            "http://model.internal:8000/v1",
            "openai/gpt-5.6-luna",
            32768,
            thinking="high",
        )
        self.assertEqual(
            openclaw_gpt["models"]["providers"]["vllm"]["api"],
            "openai-responses",
        )
        self.assertEqual(
            openclaw_gpt["agents"]["defaults"]["models"]
            ["vllm/openai/gpt-5.6-luna"]["params"]["reasoningEffort"],
            "high",
        )

    def test_fairness_limits_are_rendered_per_harness(self) -> None:
        rendered = {
            harness_id: render_harness_config(
                spec,
                base_url="http://model.internal:8000/v1",
                model="gemma-4-31b-it",
                max_tokens=32768,
                temperature=1.0,
                context_window=200000,
                max_agent_turns=150,
            )
            for harness_id, spec in SPECS.items()
        }
        self.assertEqual(yaml.safe_load(rendered["hermes"])["model"]["context_length"], 200000)
        self.assertEqual(
            json.loads(rendered["nanobot"])["modelPresets"]["harnessbench"]["contextWindowTokens"],
            200000,
        )
        self.assertEqual(
            json.loads(rendered["nanobot"])["agents"]["defaults"]["maxToolIterations"],
            150,
        )
        self.assertEqual(
            json.loads(rendered["opencode"])["provider"]["harnessbench"]["models"]["gemma-4-31b-it"]["limit"]["context"],
            200000,
        )
        self.assertEqual(json.loads(rendered["opencode"])["agent"]["build"]["steps"], 150)
        self.assertEqual(json.loads(rendered["claude-code"])["max_turns"], 150)
        self.assertEqual(json.loads(rendered["claude-code"])["thinking"], "high")
        self.assertIn("model_context_window = 200000", rendered["codex"])

    @mock.patch("harness_runtime.docker")
    def test_contract_harness_gets_one_writable_workspace_mount(
        self,
        docker_mock: mock.Mock,
    ) -> None:
        docker_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            runtime = root / "runtime"
            controller = root / "controller"
            trace = root / "trace"
            for path in (
                workspace,
                runtime,
                controller,
                trace,
            ):
                path.mkdir(parents=True)
            run_contract_round(
                spec=SPECS["nanobot"],
                container_name="hb-test",
                image="harnessbench/nanobot:test",
                workspace=workspace,
                runtime=runtime,
                state_volume="hb-test-state",
                controller_dir=controller,
                trace_dir=trace,
                timeout=60,
                labels=["--label", "test=true"],
            )

        run_arguments = docker_mock.call_args_list[0].args
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
                run_arguments[index + 1] == "GIT_CONFIG_GLOBAL=/state/gitconfig"
                for index in env_positions
            )
        )
        self.assertTrue(
            any(
                run_arguments[index + 1] == "PYTHONDONTWRITEBYTECODE=1"
                for index in env_positions
            )
        )

    @mock.patch("harness_runtime.docker")
    def test_antigravity_contract_round_execution(
        self,
        docker_mock: mock.Mock,
    ) -> None:
        docker_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            workspace = base / "workspace"
            runtime = base / "runtime"
            controller = base / "controller"
            trace = base / "trace"
            for path in (workspace, runtime, controller, trace):
                path.mkdir(parents=True)
            run_contract_round(
                spec=SPECS["antigravity"],
                container_name="hb-test-antigravity",
                image="harnessbench/antigravity:test",
                workspace=workspace,
                runtime=runtime,
                state_volume="hb-test-state",
                controller_dir=controller,
                trace_dir=trace,
                timeout=60,
                labels=["--label", "test=true"],
            )

        run_arguments = docker_mock.call_args_list[0].args
        self.assertIn("host.docker.internal:host-gateway", run_arguments)
        self.assertIn("--dns", run_arguments)
        self.assertIn("127.0.0.1", run_arguments)

    def test_codex_is_strictly_non_interactive(self) -> None:
        entrypoint = (SPECS["codex"].docker_context / "entrypoint.sh").read_text()
        self.assertIn("--ignore-rules", entrypoint)
        self.assertIn("--cd /workspace", entrypoint)
        self.assertIn("printf '%s' \"${prompt}\" | codex exec", entrypoint)
        self.assertIn("\n  - \\\n", entrypoint)

    def test_grader_does_not_depend_on_a_harness_image(self) -> None:
        dockerfile = (ROOT / "docker" / "grader" / "Dockerfile").read_text()
        self.assertIn("FROM python:3.10-bookworm@sha256:", dockerfile)
        self.assertIn("git version 2.39.5", dockerfile)
        self.assertIn(
            "FROM quay.io/prometheus/alertmanager:v0.32.1@sha256:", dockerfile
        )
        self.assertIn("COPY --from=alertmanager /bin/amtool", dockerfile)
        self.assertIn("/usr/local/bin/amtool", dockerfile)
        self.assertIn('amtool --version 2>&1 | grep -F "version 0.32.1"', dockerfile)
        self.assertIn("--require-hashes", dockerfile)
        self.assertIn('yaml.__version__ == "6.0.2"', dockerfile)
        self.assertIn(GRADER_SOURCE_LABEL, dockerfile)
        self.assertNotIn("openclaw", dockerfile.lower())

    def test_grader_source_identity_covers_every_build_input(self) -> None:
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", grader_source_sha256()))
        self.assertEqual(
            set(GRADER_SOURCE_FILES),
            {
                Path(".dockerignore"),
                Path("docker/grader/Dockerfile"),
                Path("docker/grader/grade_entrypoint.py"),
                Path("docker/grader/requirements.lock"),
                Path("src/harnessbench/__init__.py"),
                Path("src/harnessbench/grading/__init__.py"),
                Path("src/harnessbench/grading/deterministic_oracle.py"),
                Path("src/harnessbench/grading/legacy_case_adapter.py"),
                Path("src/harnessbench/grading/oracle_contract.py"),
                Path("src/harnessbench/grading/outcome_cases.py"),
                Path("src/harnessbench/grading/semantic_judge.py"),
                Path("src/harnessbench/grading/pure_function.py"),
                Path("src/harnessbench/grading/record_equivalence.py"),
                Path("src/harnessbench/grading/structured_io.py"),
                Path("src/harnessbench/grading/task_checks.py"),
            },
        )

    def test_grader_build_args_forward_configured_package_index(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "HARNESSBENCH_DOCKER_BUILD_NETWORK": "host",
                "HARNESSBENCH_PYPI_INDEX_URL": "http://mirror.example/simple ",
                "HARNESSBENCH_PYPI_TRUSTED_HOST": " mirror.example",
            },
            clear=True,
        ):
            self.assertEqual(
                _grader_build_args(),
                [
                    "--network",
                    "host",
                    "--build-arg",
                    "HARNESSBENCH_PYPI_INDEX_URL=http://mirror.example/simple",
                    "--build-arg",
                    "HARNESSBENCH_PYPI_TRUSTED_HOST=mirror.example",
                ],
            )

    def test_grader_build_args_omit_unconfigured_package_index(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_grader_build_args(), [])

    def test_grader_build_args_reject_unknown_build_network(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"HARNESSBENCH_DOCKER_BUILD_NETWORK": "agent-bridge"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "must be 'default' or 'host'"):
                _grader_build_args()

    def test_grader_dockerfile_accepts_package_index_build_args(self) -> None:
        dockerfile = (ROOT / "docker" / "grader" / "Dockerfile").read_text()
        self.assertIn(
            "ARG HARNESSBENCH_PYPI_INDEX_URL=https://pypi.org/simple",
            dockerfile,
        )
        self.assertIn("ARG HARNESSBENCH_PYPI_TRUSTED_HOST=", dockerfile)

    def test_docker_context_includes_every_grader_copy_source(self) -> None:
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        for relative in (
            "docker/grader/Dockerfile",
            "docker/grader/grade_entrypoint.py",
            "docker/grader/requirements.lock",
            "src/harnessbench/__init__.py",
            "src/harnessbench/grading/__init__.py",
            "src/harnessbench/grading/deterministic_oracle.py",
            "src/harnessbench/grading/oracle_contract.py",
            "src/harnessbench/grading/outcome_cases.py",
            "src/harnessbench/grading/structured_io.py",
            "src/harnessbench/grading/task_checks.py",
        ):
            self.assertIn(f"!{relative}", dockerignore)

            "src/harnessbench/grading/pure_function.py",
    @mock.patch("container_runtime._grader_image_source")
    @mock.patch("container_runtime.grader_source_sha256")
    @mock.patch("container_runtime.docker")
    def test_build_images_does_not_require_classic_base_image_reference(
        self,
        docker_mock: mock.Mock,
        source_sha256_mock: mock.Mock,
        image_source_mock: mock.Mock,
    ) -> None:
        source_sha256_mock.return_value = "a" * 64
        image_source_mock.return_value = "a" * 64

        def inspect(*args: str, **_: object) -> mock.Mock:
            self.assertEqual(args[:2], ("image", "inspect"))
            if args[2] == GRADER_IMAGE:
                return mock.Mock(stdout="sha256:grader\n")
            if args[2] == STAGER_IMAGE:
                return mock.Mock(stdout="sha256:stager\n")
            self.fail(f"unexpected image inspection: {args[2]}")

        docker_mock.side_effect = inspect

        images = build_images()

        self.assertEqual(images[GRADER_IMAGE], "sha256:grader")
        self.assertEqual(images[STAGER_IMAGE], "sha256:stager")
        self.assertEqual(
            images[GRADER_BASE_IMAGE],
            GRADER_BASE_IMAGE.rsplit("@", 1)[-1],
        )

    def test_openclaw_runtime_is_derived_from_the_pinned_release(self) -> None:
        dockerfile = (ROOT / "docker" / "openclaw" / "Dockerfile").read_text()
        self.assertEqual(
            DEFAULT_OPENCLAW_IMAGE,
            "harnessbench/openclaw:2026.5.26-tasktools",
        )
        self.assertIn(OFFICIAL_OPENCLAW_IMAGE, dockerfile)
        self.assertIn("python:3.12-slim-bookworm@sha256:", dockerfile)
        self.assertIn("ALERTMANAGER_VERSION=0.32.1", dockerfile)
        self.assertIn("sha256sum -c -", dockerfile)
        self.assertIn("/usr/local/bin/amtool", dockerfile)
        self.assertIn("ln -sf /usr/local/bin/python3 /usr/bin/python3", dockerfile)
        self.assertIn(OPENCLAW_SOURCE_LABEL, dockerfile)
        self.assertIn("USER node", dockerfile)

    def test_openclaw_source_identity_covers_every_build_input(self) -> None:
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", openclaw_source_sha256()))
        self.assertEqual(
            set(OPENCLAW_SOURCE_FILES),
            {
                Path(".dockerignore"),
                Path("docker/openclaw/Dockerfile"),
                Path("docker/openclaw/requirements.lock"),
                Path("docker/openclaw/verify_toolchain.py"),
                Path("docker/harnesses/multimodal-npm"),
            },
        )

    def test_grader_records_legacy_oracle_runtime(self) -> None:
        entrypoint = (ROOT / "docker" / "grader" / "grade_entrypoint.py").read_text()
        self.assertIn('["git", "--version"]', entrypoint)
        self.assertIn('"grader_runtime"', entrypoint)

    def test_git_task_setup_uses_isolated_helper(self) -> None:
        hooks = (
            ROOT / "tasks" / "003-git-pr-merge" / "environment" / "hooks.py"
        ).read_text()
        self.assertIn('runtime.get("run_trusted_helper")', hooks)
        self.assertIn('helper(["python3", "-c", script])', hooks)
        self.assertIn('"/runtime/remote.git"', hooks)

    def test_regression_task_prepare_uses_fixed_git_tooling_in_task_image(self) -> None:
        task_dir = (
            ROOT
            / "tasks"
            / "005-repository-regression-bisect-repair"
        )
        metadata = yaml.safe_load((task_dir / "task.yaml").read_text())
        self.assertNotIn("hooks_module", metadata)
        self.assertEqual(
            metadata["environment"]["prepare"],
            "environment/prepare_workspace.py",
        )
        prepare = (task_dir / "environment" / "prepare_workspace.py").read_text()
        self.assertNotIn("sys.executable", prepare)
        self.assertEqual(prepare.count("subprocess.run("), 1)
        self.assertIn('["git", *args]', prepare)
        self.assertNotIn("shell=True", prepare)
        self.assertNotIn("pytest", prepare)
        self.assertNotIn('["python', prepare)

    def test_terminal_raw_tool_marker_is_protocol_failure(self) -> None:
        report = contract_protocol_report(
            '<|tool_call>{"name":"bash"}</tool_call>',
            "",
            0,
        )
        self.assertTrue(report["fatal"])
        self.assertTrue(report["dangling_tool_call"])
        self.assertTrue(report["textual_tool_marker_observed"])

    def test_historical_tool_marker_followed_by_final_answer_is_nonfatal(self) -> None:
        report = contract_protocol_report(
            '<tool_call>{"name":"write"}</tool_call>\nFinished the requested work.',
            "",
            0,
        )
        self.assertFalse(report["fatal"])
        self.assertFalse(report["dangling_tool_call"])
        self.assertTrue(report["textual_tool_marker_observed"])

    def test_chunked_terminal_goose_tool_marker_is_protocol_failure(self) -> None:
        events = [
            {"type": "message", "message": {"id": "final", "role": "assistant", "content": [{"type": "text", "text": "<tool_call>"}]}},
            {"type": "message", "message": {"id": "final", "role": "assistant", "content": [{"type": "text", "text": '{"name":"write"}'}]}},
            {"type": "message", "message": {"id": "final", "role": "assistant", "content": [{"type": "text", "text": "</tool_call>"}]}},
            {"type": "complete", "total_tokens": 10},
        ]
        report = contract_protocol_report(
            "\n".join(json.dumps(event) for event in events),
            "",
            0,
        )
        self.assertTrue(report["fatal"])
        self.assertTrue(report["dangling_tool_call"])

    def test_native_stream_idle_is_retryable_provider_timeout(self) -> None:
        report = contract_protocol_report(
            "Error calling LLM: stream stalled for more than 90 seconds",
            "",
            0,
        )
        self.assertTrue(report["fatal"])
        self.assertFalse(report["timed_out"])
        self.assertTrue(report["provider_request_timeout"])
        self.assertEqual(report["timeout_phase"], "model_stream_idle")

    def test_native_request_timeout_is_retryable_provider_failure(self) -> None:
        for message in (
            "Error calling LLM: timed out after 600s",
            "API Error: The operation timed out.",
        ):
            with self.subTest(message=message):
                report = contract_protocol_report(message, "", 0)
                self.assertTrue(report["fatal"])
                self.assertFalse(report["timed_out"])
                self.assertTrue(report["provider_request_timeout"])
                self.assertEqual(report["timeout_phase"], "model_request")
                self.assertFalse(report["provider_stream_error"])

    def test_provider_stream_decode_error_is_retryable_infrastructure(self) -> None:
        report = contract_protocol_report(
            (
                "Network error: Stream decode error: error decoding response body\n"
                "Please resend your message to try again."
            ),
            "",
            0,
        )
        self.assertTrue(report["fatal"])
        self.assertTrue(report["provider_stream_error"])
        self.assertFalse(report["timed_out"])
        self.assertIsNone(report["timeout_phase"])

    def test_clean_contract_completion_has_no_provider_failure(self) -> None:
        report = contract_protocol_report("done", "", 0)
        self.assertFalse(report["fatal"])
        self.assertFalse(report["timed_out"])
        self.assertFalse(report["provider_request_timeout"])
        self.assertFalse(report["provider_stream_error"])


if __name__ == "__main__":
    unittest.main()
