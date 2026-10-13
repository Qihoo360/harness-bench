"""Declarative OCI contract for HarnessBench harness implementations.

The trusted controller owns task setup, isolation, timeouts and grading.  Each
non-OpenClaw harness image implements one small interface:

* read the prompt and generated configuration from ``/run/harness``;
* work only in ``/workspace`` and its fresh ``/state``;
* write native stdout/stderr/session material to ``/trace``;
* exit when the one-shot agent turn is complete.

OpenClaw keeps its existing, separately pinned runtime path until its official
image is migrated to this contract.
"""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from container_runtime import docker, docker_build_proxy_args


ROOT = Path(__file__).resolve().parents[1]
HARNESS_DOCKER_DIR = ROOT / "docker" / "harnesses"
HARNESS_SOURCE_LABEL = "org.harnessbench.harness-source-sha256"
HARNESS_SHARED_SOURCE_FILES = (
    ROOT / ".dockerignore",
    ROOT / "docker" / "openclaw" / "requirements.lock",
    ROOT / "docker" / "openclaw" / "verify_toolchain.py",
    ROOT / "docker" / "harnesses" / "multimodal-npm",
)


@dataclass(frozen=True)
class HarnessSpec:
    harness_id: str
    version: str
    image: str
    protocol: str
    config_name: str
    docker_context: Path | None
    user: str | None = "1000:1000"
    trace_complete: bool = False


SPECS = {
    "hermes": HarnessSpec(
        harness_id="hermes",
        version="2026.7.20",
        image="harnessbench/hermes:2026.7.20",
        protocol="chat_completions",
        config_name="config.yaml",
        docker_context=HARNESS_DOCKER_DIR / "hermes",
    ),
    "nanobot": HarnessSpec(
        harness_id="nanobot",
        version="0.3.0",
        image="harnessbench/nanobot:0.3.0",
        protocol="chat_completions",
        config_name="config.json",
        docker_context=HARNESS_DOCKER_DIR / "nanobot",
    ),
    "goose": HarnessSpec(
        harness_id="goose",
        version="1.44.0",
        image="harnessbench/goose:1.44.0",
        protocol="chat_completions",
        config_name="config.json",
        docker_context=HARNESS_DOCKER_DIR / "goose",
        trace_complete=True,
    ),
    "opencode": HarnessSpec(
        harness_id="opencode",
        version="1.18.8",
        image="harnessbench/opencode:1.18.8",
        protocol="chat_completions",
        config_name="opencode.json",
        docker_context=HARNESS_DOCKER_DIR / "opencode",
        trace_complete=True,
    ),
    "claude-code": HarnessSpec(
        harness_id="claude-code",
        version="2.1.220",
        image="harnessbench/claude-code:2.1.220",
        protocol="anthropic_messages",
        config_name="config.json",
        docker_context=HARNESS_DOCKER_DIR / "claude-code",
        trace_complete=True,
    ),
    "codex": HarnessSpec(
        harness_id="codex",
        version="0.145.0",
        image="harnessbench/codex:0.145.0",
        protocol="responses",
        config_name="config.toml",
        docker_context=HARNESS_DOCKER_DIR / "codex",
        trace_complete=True,
    ),
    "antigravity": HarnessSpec(
        harness_id="antigravity",
        version="1.3.3",
        image="harnessbench/antigravity:1.3.3",
        protocol="chat_completions",
        config_name="config.json",
        docker_context=HARNESS_DOCKER_DIR / "antigravity",
        trace_complete=True,
    ),
}


def get_harness_spec(harness_id: str) -> HarnessSpec:
    try:
        return SPECS[harness_id]
    except KeyError as exc:
        raise ValueError(f"unknown OCI harness: {harness_id}") from exc


def harness_source_sha256(spec: HarnessSpec) -> str:
    if spec.docker_context is None:
        raise ValueError(f"harness has no Docker source: {spec.harness_id}")
    digest = hashlib.sha256()
    sources = [
        *sorted(p for p in spec.docker_context.iterdir() if p.is_file()),
        *HARNESS_SHARED_SOURCE_FILES,
    ]
    for path in sources:
        name = path.relative_to(ROOT).as_posix()
        payload = path.read_bytes()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def build_harness_image(spec: HarnessSpec) -> str:
    if spec.docker_context is None:
        raise RuntimeError(f"required harness image is absent: {spec.image}")
    source_sha256 = harness_source_sha256(spec)
    present = docker(
        "image",
        "inspect",
        spec.image,
        "--format",
        f'{{{{ index .Config.Labels "{HARNESS_SOURCE_LABEL}" }}}}',
        check=False,
    )
    if present.returncode == 0 and present.stdout.strip() == source_sha256:
        return docker(
            "image", "inspect", spec.image, "--format", "{{.Id}}"
        ).stdout.strip()
    build_args: list[str] = []
    for source_name, build_name in (
        ("HARNESSBENCH_PYPI_INDEX_URL", "PIP_INDEX_URL"),
        ("HARNESSBENCH_PYPI_EXTRA_INDEX_URL", "PIP_EXTRA_INDEX_URL"),
        ("HARNESSBENCH_PYPI_TRUSTED_HOST", "PIP_TRUSTED_HOST"),
    ):
        value = os.environ.get(source_name, "").strip()
        if value:
            build_args.extend(["--build-arg", f"{build_name}={value}"])
    if spec.harness_id == "nanobot":
        pypi_index = os.environ.get("HARNESSBENCH_PYPI_INDEX_URL", "").strip()
        if pypi_index:
            build_args.extend(["--build-arg", f"PYPI_INDEX_URL={pypi_index}"])
    docker(
        "build",
        "--pull=false",
        "--provenance=false",
        *docker_build_proxy_args(),
        *build_args,
        "--label",
        f"{HARNESS_SOURCE_LABEL}={source_sha256}",
        "--tag",
        spec.image,
        "--file",
        str(spec.docker_context / "Dockerfile"),
        str(ROOT),
        capture=False,
    )
    return docker("image", "inspect", spec.image, "--format", "{{.Id}}").stdout.strip()


def render_harness_config(
    spec: HarnessSpec,
    *,
    base_url: str,
    model: str,
    max_tokens: int,
    temperature: float,
    thinking: str = "high",
    context_window: int = 200000,
    max_agent_turns: int = 150,
) -> str:
    base_url = base_url.rstrip("/")
    effort = "none" if thinking == "off" else thinking
    model_name = model.casefold()
    if spec.harness_id == "hermes":
        hermes_transport = (
            "codex_responses" if "gpt-" in model_name else "chat_completions"
        )
        config: dict[str, Any] = {
            "providers": {
                "harnessbench": {
                    "api": base_url,
                    "api_key": "local-vllm-no-auth",
                    "transport": hermes_transport,
                    "default_model": model,
                    "discover_models": False,
                }
            },
            "model": {
                "default": model,
                "provider": "custom:harnessbench",
                "context_length": context_window,
            },
            "terminal": {
                "backend": "local",
                "cwd": "/workspace",
                "home_mode": "profile",
                "timeout": 300,
            },
            "memory": {
                "memory_enabled": False,
                "user_profile_enabled": False,
                "write_approval": False,
            },
            "agent": {
                "max_turns": max_agent_turns,
                "reasoning_effort": effort,
                "disabled_toolsets": [
                    "memory",
                    "web",
                    "browser",
                    "vision",
                    "image_generation",
                    "delegation",
                    "cron",
                ]
            },
        }
        return yaml.safe_dump(config, sort_keys=False)

    if spec.harness_id == "nanobot":
        provider_name = "openai" if "gpt-" in model_name else "vllm"
        provider_config: dict[str, Any] = {
            "apiKey": "local-vllm-no-auth",
            "apiBase": base_url,
        }
        if provider_name == "openai":
            provider_config["apiType"] = "responses"
        config = {
            "providers": {
                provider_name: provider_config,
            },
            "modelPresets": {
                "harnessbench": {
                    "label": "HarnessBench vLLM",
                    "provider": provider_name,
                    "model": model,
                    "maxTokens": max_tokens,
                    "contextWindowTokens": context_window,
                    "temperature": temperature,
                    "reasoningEffort": effort,
                }
            },
            "agents": {
                "defaults": {
                    "modelPreset": "harnessbench",
                    "fallbackModels": [],
                    "maxToolIterations": max_agent_turns,
                }
            },
            "tools": {
                "restrictToWorkspace": True,
                "exec": {
                    "timeout": 300,
                    "allowedEnvKeys": [
                        "GIT_CONFIG_GLOBAL",
                        "PATH",
                        "NODE_PATH",
                        "PLAYWRIGHT_BROWSERS_PATH",
                        "npm_config_cache",
                    ],
                },
                "mcpServers": {},
                "web": {
                    "search": {"enabled": False},
                    "fetch": {"enabled": False},
                },
            },
            "channels": {},
        }
        return json.dumps(config, ensure_ascii=False, indent=2) + "\n"

    if spec.harness_id == "goose":
        config = {
            "provider": "openai",
            "model": model,
            "base_url": base_url,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "context_window": context_window,
            "max_turns": max_agent_turns,
        }
        return json.dumps(config, ensure_ascii=False, indent=2) + "\n"

    if spec.harness_id == "opencode":
        provider_package = (
            "@ai-sdk/openai" if "gpt-" in model_name else "@ai-sdk/openai-compatible"
        )
        config = {
            "$schema": "https://opencode.ai/config.json",
            "model": f"harnessbench/{model}",
            "provider": {
                "harnessbench": {
                    "npm": provider_package,
                    "name": "HarnessBench vLLM",
                    "options": {
                        "baseURL": base_url,
                        "apiKey": "local-vllm-no-auth",
                    },
                    "models": {
                        model: {
                            "name": model,
                            "limit": {
                                "context": context_window,
                                "output": max_tokens,
                            },
                            "options": {
                                "temperature": temperature,
                                "reasoningEffort": effort,
                            },
                        }
                    },
                }
            },
            "agent": {
                "build": {
                    "steps": max_agent_turns,
                    "temperature": temperature,
                }
            },
            "permission": {
                "*": "deny",
                "bash": "allow",
                "edit": "allow",
                "glob": "allow",
                "grep": "allow",
                "list": "allow",
                "patch": "allow",
                "read": "allow",
                "write": "allow",
            },
            "share": "disabled",
            "autoupdate": False,
        }
        return json.dumps(config, ensure_ascii=False, indent=2) + "\n"

    if spec.harness_id == "claude-code":
        config = {
            "base_url": (base_url[:-3] if base_url.endswith("/v1") else base_url),
            "model": model,
            "max_tokens": max_tokens,
            "context_window": context_window,
            "max_turns": max_agent_turns,
            "thinking": thinking,
        }
        return json.dumps(config, ensure_ascii=False, indent=2) + "\n"

    if spec.harness_id == "codex":
        escaped_url = base_url.replace("\\", "\\\\").replace('"', '\\"')
        escaped_model = model.replace("\\", "\\\\").replace('"', '\\"')
        return (
            f'model = "{escaped_model}"\n'
            'model_provider = "vllm"\n'
            f'model_reasoning_effort = "{effort}"\n'
            f"model_context_window = {context_window}\n"
            f"model_auto_compact_token_limit = {max(1, context_window - max_tokens)}\n"
            'approval_policy = "never"\n'
            'sandbox_mode = "danger-full-access"\n'
            'web_search = "disabled"\n'
            "\n"
            "[model_providers.vllm]\n"
            'name = "HarnessBench vLLM"\n'
            'env_key = "VLLM_API_KEY"\n'
            f'base_url = "{escaped_url}"\n'
            'wire_api = "responses"\n'
            "supports_websockets = false\n"
        )

    if spec.harness_id == "antigravity":
        config = {
            "base_url": base_url,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "context_window": context_window,
            "max_turns": max_agent_turns,
            "thinking": effort,
        }
        return json.dumps(config, ensure_ascii=False, indent=2) + "\n"

    raise ValueError(f"no config renderer for {spec.harness_id}")


def run_contract_round(
    *,
    spec: HarnessSpec,
    container_name: str,
    image: str,
    workspace: Path,
    runtime: Path,
    state_volume: str,
    controller_dir: Path,
    trace_dir: Path,
    timeout: int,
    labels: list[str],
    network: str = "bridge",
) -> subprocess.CompletedProcess[str]:
    command = [
        "run",
        "--name",
        container_name,
        *labels,
        "--network",
        network,
        "--dns",
        "127.0.0.1",
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
        "--mount",
        f"type=bind,src={workspace},dst=/workspace",
        "--mount",
        f"type=bind,src={runtime},dst=/runtime",
        "--mount",
        f"type=volume,src={state_volume},dst=/state",
        "--mount",
        f"type=bind,src={controller_dir},dst=/run/harness,readonly",
        "--mount",
        f"type=bind,src={trace_dir},dst=/trace",
        "--env",
        "HARNESSBENCH_PROMPT=/run/harness/prompt.txt",
        "--env",
        f"HARNESSBENCH_CONFIG=/run/harness/{spec.config_name}",
        "--env",
        "HARNESSBENCH_WORKSPACE=/workspace",
        "--env",
        "HARNESSBENCH_STATE=/state",
        "--env",
        "HARNESSBENCH_TRACE=/trace",
        "--env",
        "GIT_CONFIG_GLOBAL=/state/gitconfig",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "NO_PROXY=*",
        "--env",
        "no_proxy=*",
        "--workdir",
        "/workspace",
    ]
    if spec.user:
        command.extend(["--user", spec.user])
    command.extend(
        [
            "--entrypoint",
            "/opt/harness/bin/run",
            image,
        ]
    )
    try:
        return docker(*command, check=False, timeout=timeout + 60)
    except subprocess.TimeoutExpired as exc:
        docker("rm", "--force", container_name, check=False)
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
        docker("rm", "--force", container_name, check=False)


def contract_protocol_report(
    stdout: str,
    stderr: str,
    returncode: int,
) -> dict[str, Any]:
    combined = stdout + "\n" + stderr
    dangling_markers = (
        "<|tool_call>",
        "<tool_call|>",
        "<tool_call>",
        "</tool_call>",
    )
    textual_tool_marker_observed = any(marker in combined for marker in dangling_markers)

    # Contract harnesses often render the complete transcript to stdout, so a
    # marker anywhere in that transcript is not enough to prove a protocol
    # failure.  Goose emits JSONL message chunks; reconstruct its last assistant
    # message when possible.  For plain-text harnesses, only a marker at the
    # actual end is terminal.  This keeps genuinely unexecuted final calls fatal
    # without reclassifying an earlier, already-consumed call.
    assistant_chunks: dict[str, list[str]] = {}
    assistant_order: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict) or event.get("type") != "message":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        message_id = str(message.get("id") or f"line-{len(assistant_order)}")
        if message_id not in assistant_chunks:
            assistant_chunks[message_id] = []
            assistant_order.append(message_id)
        content = message.get("content")
        if not isinstance(content, list):
            continue
        assistant_chunks[message_id].extend(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )

    if assistant_order:
        terminal_text = "".join(assistant_chunks[assistant_order[-1]]).rstrip()
    else:
        terminal_text = combined.rstrip()
    dangling_tool_call = textual_tool_marker_observed and any(
        terminal_text.endswith(marker) for marker in dangling_markers
    )
    provider_stream_idle = (
        "Error calling LLM: stream stalled for more than" in combined
    )
    provider_request_timeout = any(
        marker in combined
        for marker in (
            "Error calling LLM: timed out after ",
            "API Error: The operation timed out.",
        )
    )
    retryable_provider_timeout = provider_stream_idle or provider_request_timeout
    provider_stream_error = any(
        marker in combined
        for marker in (
            "Network error: Stream decode error:",
            "Request failed: Stream decode error:",
        )
    )
    return {
        "replay_invalid": False,
        "liveness_state": None,
        "stop_reason": None,
        "dangling_tool_call": dangling_tool_call,
        "textual_tool_marker_observed": textual_tool_marker_observed,
        "aborted": returncode == 124,
        # Controller/round deadlines are reported through return code 124.
        # These messages instead mean one provider request or stream failed.
        "timed_out": False,
        "timeout_phase": (
            "model_stream_idle" if provider_stream_idle
            else "model_request" if provider_request_timeout
            else None
        ),
        "provider_request_timeout": retryable_provider_timeout,
        "provider_stream_error": provider_stream_error,
        "fatal": (
            dangling_tool_call
            or retryable_provider_timeout
            or provider_stream_error
        ),
    }


def collect_trace_files(trace_dir: Path) -> list[str]:
    return [
        path.relative_to(trace_dir).as_posix()
        for path in sorted(trace_dir.rglob("*"))
        if path.is_file()
    ]
