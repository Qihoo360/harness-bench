"""GenericAgent adapter for Harness Bench.

This adapter is intentionally a thin bridge into the real GA runtime:
- llmcore.resolve_client / ToolClient for the configured LLM backend
- agentmain.get_system_prompt / TOOLS_SCHEMA for GA's native prompt and tools
- ga.GenericAgentHandler for tool execution in the benchmark workspace
- agent_loop.agent_runner_loop for the normal multi-turn tool loop

It must not contain task-specific shortcuts. The only benchmark-facing work here is
wiring the Harness Bench context to GA's native agent interfaces.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import time
import traceback
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from clawbench_v2.adapters.base import BaseAdapter
from clawbench_v2.models import AdapterRunContext, AdapterRunResult
from clawbench_v2.usage_proxy import register_routes


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        return str(value)


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _content_blocks(content: Any) -> list[dict[str, str]]:
    if isinstance(content, str):
        text = content.strip()
    elif content is None:
        text = ""
    else:
        text = json.dumps(_json_safe(content), ensure_ascii=False)
    return [{"type": "text", "text": text}] if text else []


def _format_tool_calls(tool_calls: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    formatted: list[dict[str, Any]] = []
    names_by_id: dict[str, str] = {}
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        name = str(tc.get("tool_name") or tc.get("name") or "")
        if not name or name == "no_tool":
            continue
        tid = str(tc.get("id") or tc.get("tool_call_id") or "")
        args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
        formatted.append(
            {
                "id": tid,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(_json_safe(args), ensure_ascii=False),
                },
            }
        )
        if tid:
            names_by_id[tid] = name
    return formatted, names_by_id


def _find_ga_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "agent_loop.py").is_file() and (parent / "llmcore.py").is_file():
            return parent
    raise RuntimeError(f"backend_not_available: cannot locate GenericAgent root from {start}")


_GA_ROOT = _find_ga_root(Path(__file__))
if str(_GA_ROOT) not in sys.path:
    sys.path.insert(0, str(_GA_ROOT))


class GaAgentAdapter(BaseAdapter):
    """Run Harness Bench tasks with the in-process GenericAgent runtime."""

    name = "ga_agent"

    def __init__(self) -> None:
        super().__init__()
        self._client_cache: dict[str, Any] = {}  # session_id -> client (preserves backend.history across rounds)

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        started = time.time()
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        command = ["ga_agent", "inprocess", "agent_runner_loop"]
        old_cwd = os.getcwd()
        old_env: dict[str, str | None] = {}

        try:
            ctx.workspace.mkdir(parents=True, exist_ok=True)
            timeout_sec = int(ctx.model_config.get("timeout_sec") or ctx.timeout_sec or 840)
            max_turns = int(ctx.model_config.get("max_turns") or os.getenv("GA_BENCH_MAX_TURNS") or 60)
            cfg_name = self._resolve_ga_config_name(ctx)
            model_override = self._resolve_model_override(ctx)

            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                self._apply_env(ctx.env, old_env)
                os.chdir(ctx.workspace)

                from llmcore import resolve_client
                from agentmain import TOOLS_SCHEMA
                from agent_loop import agent_runner_loop
                from ga import GenericAgentHandler

                # Benchmark isolation: strip memory/SOP tools that could leak cross-task knowledge.
                # Only keep task-execution tools (file I/O, code_run, web, ask_user).
                _BLOCKED_TOOLS = {"update_working_checkpoint", "start_long_term_update"}
                bench_tools = [t for t in TOOLS_SCHEMA if t.get("function", {}).get("name") not in _BLOCKED_TOOLS]

                # Reuse client across rounds of the same session (preserves backend.history for multi-round tasks)
                session_id = ctx.session_id
                if session_id and session_id in self._client_cache:
                    client = self._client_cache[session_id]
                    resolved_cfg_name = cfg_name
                    backend_note = "reused_from_session_cache"
                else:
                    client, resolved_cfg_name, backend_note = self._build_client(resolve_client, cfg_name)
                    if session_id:
                        self._client_cache[session_id] = client

                backend_override_saved, backend_override_base, backend_override_key_set = self._apply_backend_override(client, ctx)
                proxy_route = self._apply_usage_proxy(client, ctx.env, resolved_cfg_name)
                backend_model = self._apply_model_override(client, model_override)

                parent = SimpleNamespace(
                    llmclient=client,
                    task_dir=None,
                    verbose=False,
                    _turn_end_hooks={},
                )
                handler = GenericAgentHandler(parent, cwd=str(ctx.workspace))
                parent.handler = handler

                original_prompt = self._build_prompt(ctx, timeout_sec=timeout_sec)
                transcript_path = ctx.workspace / "sessions" / f"{ctx.session_id}.jsonl"
                transcript_path.parent.mkdir(parents=True, exist_ok=True)
                if transcript_path.exists():
                    transcript_path.unlink()
                _append_jsonl(
                    transcript_path,
                    {
                        "message": {"role": "user", "content": original_prompt},
                        "session_id": ctx.session_id,
                        "turn": 0,
                        "source": "ga_agent_adapter",
                    },
                )

                def _record_turn(frame: dict[str, Any]) -> None:
                    response = frame.get("response")
                    turn = int(frame.get("turn") or 0)
                    content = getattr(response, "content", "")
                    tool_calls, tool_names = _format_tool_calls(frame.get("tool_calls") or [])
                    _append_jsonl(
                        transcript_path,
                        {
                            "message": {"role": "assistant", "content": _content_blocks(content), "tool_calls": tool_calls},
                            "session_id": ctx.session_id,
                            "turn": turn,
                            "source": "ga_agent_adapter",
                        },
                    )
                    for result in frame.get("tool_results") or []:
                        if not isinstance(result, dict):
                            continue
                        tool_call_id = str(result.get("tool_use_id") or result.get("tool_call_id") or "")
                        _append_jsonl(
                            transcript_path,
                            {
                                "message": {
                                    "role": "tool",
                                    "tool_call_id": tool_call_id,
                                    "name": tool_names.get(tool_call_id, "tool"),
                                    "content": result.get("content", ""),
                                },
                                "session_id": ctx.session_id,
                                "turn": turn,
                                "source": "ga_agent_adapter",
                            },
                        )

                parent._turn_end_hooks["clawbench_transcript"] = _record_turn

                # Time-budget guard: dynamically reduce max_turns when approaching timeout
                budget_threshold = timeout_sec * 0.60  # start winding down at 60% of timeout
                wrap_up_injected = False

                def _time_budget_hook(frame: dict[str, Any]) -> None:
                    nonlocal wrap_up_injected
                    if wrap_up_injected:
                        return
                    elapsed_now = time.time() - started
                    if elapsed_now > budget_threshold:
                        cur_turn = getattr(handler, 'current_turn', 0) or 0
                        new_max = cur_turn + 3
                        handler.max_turns = min(getattr(handler, 'max_turns', 999), new_max)
                        wrap_up_injected = True
                        print(f"[TIME_BUDGET] triggered at {elapsed_now:.0f}s, cur_turn={cur_turn}, max_turns→{handler.max_turns}", flush=True)

                parent._turn_end_hooks["clawbench_time_budget"] = _time_budget_hook

                required_deliverables = self._extract_required_deliverables(ctx.prompt)
                remaining_turns = max_turns
                max_retries = 2  # up to 2 re-entries if agent exits with missing deliverables
                min_reentry_seconds = max(120, min(timeout_sec // 4, 240))
                prompt = original_prompt
                missing_deliverables: list[str] = []
                reentry_attempts = 0
                reentry_skipped_reason: str | None = None

                for attempt in range(1 + max_retries):
                    chunks: list[str] = []
                    multimodal_content = self._build_multimodal_content(prompt, ctx.workspace)
                    for chunk in agent_runner_loop(
                        client,
                        self._get_bench_system_prompt(),
                        prompt,
                        handler,
                        bench_tools,
                        max_turns=remaining_turns,
                        verbose=False,
                        initial_user_content=multimodal_content,
                    ):
                        if chunk:
                            chunks.append(str(chunk))
                        # Wall-clock budget: break out of generator if approaching timeout
                        if time.time() - started > timeout_sec * 0.75:
                            break

                    produced_files = self._collect_workspace_outputs(ctx.workspace)
                    missing_deliverables = self._missing_required_deliverables(ctx.workspace, required_deliverables)
                    if attempt < max_retries and (not produced_files or missing_deliverables):
                        elapsed_now = time.time() - started
                        remaining_seconds = timeout_sec - elapsed_now
                        if remaining_seconds < min_reentry_seconds:
                            reentry_skipped_reason = (
                                f"insufficient time for QA re-entry: {remaining_seconds:.1f}s remaining, "
                                f"need at least {min_reentry_seconds}s"
                            )
                            break
                        remaining_turns = max(20, remaining_turns - getattr(handler, "current_turn", 0))
                        reason = (
                            "no output files were created"
                            if not produced_files
                            else "these required output files are missing or empty: " + ", ".join(missing_deliverables)
                        )
                        reentry_attempts += 1
                        prompt = self._build_reentry_prompt(
                            original_prompt,
                            reason=reason,
                            required_deliverables=required_deliverables,
                            produced_files=produced_files,
                        )
                        continue
                    break

            elapsed = time.time() - started
            stdout = stdout_buf.getvalue()
            loop_output = "".join(chunks) if "chunks" in locals() else ""
            if loop_output:
                stdout = stdout + ("\n" if stdout else "") + loop_output
            metadata = {
                "adapter": self.name,
                "ga_root": str(_GA_ROOT),
                "ga_config_requested": cfg_name,
                "ga_config_resolved": resolved_cfg_name,
                "backend_note": backend_note,
                "model_requested": model_override,
                "backend_model": backend_model,
                "usage_proxy_route": proxy_route,
                "max_turns": max_turns,
                "timeout_sec": timeout_sec,
                "session_id": ctx.session_id,
                "transcript_file": str(transcript_path),
                "openclaw_home": str(ctx.workspace),
                "reentry_attempts": reentry_attempts,
                "missing_deliverables": missing_deliverables,
                "reentry_skipped_reason": reentry_skipped_reason,
                "workspace": str(ctx.workspace),
                "backend_override_base": backend_override_base,
                "backend_override_key_set": backend_override_key_set,
            }
            return AdapterRunResult(ok=True, command=command, stdout=stdout, stderr=stderr_buf.getvalue(), metadata=metadata)
        except Exception as exc:
            stdout = stdout_buf.getvalue()
            stderr = stderr_buf.getvalue()
            if stderr and not stderr.endswith("\n"):
                stderr += "\n"
            stderr += f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            return AdapterRunResult(
                ok=False,
                command=command,
                stdout=stdout,
                stderr=stderr,
                metadata={
                    "adapter": self.name,
                    "ga_root": str(_GA_ROOT),
                    "workspace": str(ctx.workspace),
                    "elapsed_sec": time.time() - started,
                    "error_type": type(exc).__name__,
                },
            )
        finally:
            self._restore_backend(client if 'client' in locals() else None, backend_override_saved if 'backend_override_saved' in locals() else {})
            os.chdir(old_cwd)
            self._restore_env(old_env)

    @staticmethod
    def _apply_env(env: dict[str, str], old_env: dict[str, str | None]) -> None:
        for key, value in (env or {}).items():
            old_env[key] = os.environ.get(key)
            os.environ[key] = str(value)

    @staticmethod
    def _restore_env(old_env: dict[str, str | None]) -> None:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    @staticmethod
    def _build_client(resolve_client: Any, cfg_name: str) -> tuple[Any, str, str]:
        try:
            client = resolve_client(cfg_name)
            if client is not None:
                return client, cfg_name, "resolved_explicit_config"
            explicit_error: Exception | None = RuntimeError(f"resolve_client({cfg_name!r}) returned None")
        except Exception as exc:
            explicit_error = exc

        from agentmain import GenericAgent

        ga = GenericAgent()
        client = getattr(ga, "llmclient", None)
        if client is None:
            raise RuntimeError(f"backend_not_available: explicit config {cfg_name!r} failed and GenericAgent default client is unavailable: {explicit_error}")
        backend = getattr(client, "backend", None)
        resolved_name = getattr(backend, "name", None) or getattr(backend, "model", None) or "genericagent_default"
        return client, str(resolved_name), f"fallback_to_genericagent_default_after: {type(explicit_error).__name__}: {explicit_error}"

    @staticmethod
    def _resolve_ga_config_name(ctx: AdapterRunContext) -> str:
        cfg = ctx.model_config or {}
        for key in ("ga_config", "llm_config", "config"):
            value = cfg.get(key)
            if value:
                return str(value)
        for key in ("GA_LLM_CONFIG", "GA_CONFIG"):
            value = os.getenv(key)
            if value:
                return value
        return "native_oai_config"

    @staticmethod
    def _resolve_model_override(ctx: AdapterRunContext) -> str | None:
        cfg = ctx.model_config or {}
        for key in ("model", "model_override", "GA_BENCH_MODEL"):
            value = cfg.get(key)
            if value:
                return str(value)
        for key in ("GA_BENCH_MODEL", "GA_MODEL_OVERRIDE"):
            value = os.getenv(key)
            if value:
                return value
        return None

    @staticmethod
    def _normalize_backend_base(base_url: str) -> str:
        """Normalize an OpenAI-compatible base URL to a version-less host root.

        GA's auto_make_url appends '/v1/<path>' to any base that lacks a '/vN'
        segment, and the usage proxy concatenates upstream + that path. If the
        upstream base itself ends with '/v1', the request path becomes
        '/v1/v1/chat/completions' (double version -> 404). Stripping a trailing
        version segment keeps the working single-'/v1' shape for every endpoint,
        regardless of whether the proxy is in the path. This is a generic URL
        normalization, not an endpoint-specific shim.
        """
        cleaned = (base_url or "").strip().rstrip("/")
        cleaned = re.sub(r"/v\d+$", "", cleaned)
        return cleaned

    def _apply_backend_override(self, client: Any, ctx: AdapterRunContext) -> tuple[dict[str, Any], str | None, bool]:
        """Temporarily override backend.api_base / api_key from env or model_config.

        Lookup order for both fields:
          1. ctx.env (per-task injected by runner)
          2. os.environ (process-wide)
          3. ctx.model_config (models.yaml entry)

        Field names (env): GA_BENCH_BACKEND_BASE_URL, GA_BENCH_BACKEND_API_KEY, GA_BENCH_BACKEND_REASONING_EFFORT
        Field names (model_config): backend_base_url, backend_api_key, backend_reasoning_effort

        Returns (saved_dict, applied_base, key_was_set). saved_dict is consumed by _restore_backend.
        """
        cfg = ctx.model_config or {}
        env = ctx.env or {}

        def _pick(env_key: str, cfg_key: str) -> str | None:
            v = env.get(env_key) or os.environ.get(env_key) or cfg.get(cfg_key)
            return str(v).strip() if v else None

        base_url = _pick("GA_BENCH_BACKEND_BASE_URL", "backend_base_url")
        api_key = _pick("GA_BENCH_BACKEND_API_KEY", "backend_api_key")
        reasoning_effort = _pick("GA_BENCH_BACKEND_REASONING_EFFORT", "backend_reasoning_effort")
        max_retries_str = _pick("GA_BENCH_BACKEND_MAX_RETRIES", "backend_max_retries")
        max_tokens_str = _pick("GA_BENCH_BACKEND_MAX_TOKENS", "backend_max_tokens")

        backend = getattr(client, "backend", None)
        if backend is None or (not base_url and not api_key and not reasoning_effort and not max_retries_str and not max_tokens_str):
            return {}, None, False

        saved: dict[str, Any] = {}
        applied_base: str | None = None
        key_set = False

        if base_url:
            saved["api_base"] = getattr(backend, "api_base", None)
            normalized_base = self._normalize_backend_base(base_url)
            setattr(backend, "api_base", normalized_base)
            applied_base = normalized_base
        if api_key:
            saved["api_key"] = getattr(backend, "api_key", None)
            setattr(backend, "api_key", api_key)
            key_set = True
        if reasoning_effort:
            saved["reasoning_effort"] = getattr(backend, "reasoning_effort", None)
            setattr(backend, "reasoning_effort", reasoning_effort)
        if max_retries_str:
            saved["max_retries"] = getattr(backend, "max_retries", None)
            setattr(backend, "max_retries", int(max_retries_str))
        if max_tokens_str:
            saved["max_tokens"] = getattr(backend, "max_tokens", None)
            setattr(backend, "max_tokens", int(max_tokens_str))

        return saved, applied_base, key_set

    @staticmethod
    def _restore_backend(client: Any, saved: dict[str, Any]) -> None:
        if not saved or client is None:
            return
        backend = getattr(client, "backend", None)
        if backend is None:
            return
        for attr, value in saved.items():
            try:
                setattr(backend, attr, value)
            except Exception:
                pass

    @staticmethod
    def _apply_usage_proxy(client: Any, env: dict[str, str], route_name: str) -> str | None:
        proxy_base_url = str((env or {}).get("CLAWBENCH_LLM_PROXY_URL") or "").rstrip("/")
        routes_file_raw = str((env or {}).get("CLAWBENCH_LLM_PROXY_ROUTES") or "").strip()
        if not proxy_base_url and not routes_file_raw:
            return None
        if not proxy_base_url or not routes_file_raw:
            raise RuntimeError("backend_not_available: incomplete usage proxy configuration")

        backend = getattr(client, "backend", None)
        if backend is None:
            raise RuntimeError("backend_not_available: usage proxy requires a backend object")
        upstream = str(getattr(backend, "api_base", "") or "").strip().rstrip("/")
        if not upstream:
            raise RuntimeError("backend_not_available: usage proxy requires backend.api_base")

        provider = str(route_name or getattr(backend, "model", None) or getattr(backend, "name", None) or "ga_agent")
        safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in provider).strip("-") or "ga_agent"
        prefix = f"/ga_agent/{safe_name}"
        register_routes(
            Path(routes_file_raw),
            {
                prefix: {
                    "framework": "ga_agent",
                    "provider": provider,
                    "upstream": upstream,
                }
            },
        )
        setattr(backend, "api_base", f"{proxy_base_url}{prefix}")
        return prefix

    @staticmethod
    def _apply_model_override(client: Any, model_override: str | None) -> str | None:
        backend = getattr(client, "backend", None)
        if backend is None:
            return None
        if model_override:
            if not hasattr(backend, "model"):
                raise RuntimeError("backend_not_available: resolved backend does not support model override")
            setattr(backend, "model", model_override)
        model = getattr(backend, "model", None)
        return str(model) if model else None

    @staticmethod
    def _get_bench_system_prompt() -> str:
        """Lean system prompt for benchmark tasks — no persona, no CTF, no memory management."""
        return (
            "You are a highly capable task-execution agent running on Windows with PowerShell.\n"
            "You have access to tools for: reading/writing files, running shell commands (code_run), "
            "web browsing, and taking screenshots for visual analysis.\n\n"
            "## Core Principles\n"
            "- Execute tasks to completion. Do not stop at analysis or partial work.\n"
            "- Read all input files thoroughly before producing output.\n"
            "- Verify every deliverable before finishing: files exist, parse correctly, meet schema requirements.\n"
            "- When working with data, write scripts to automate extraction and validation rather than doing it manually.\n"
            "- For code tasks: reproduce the bug, fix root cause, run tests to confirm.\n"
            "- For multi-file analysis: read every relevant file, cross-reference systematically.\n"
            "- For image/OCR tasks: use vision capabilities to inspect images carefully, count precisely, validate results.\n"
            "- For progress tracking: record both pending and done status transitions in progress.md when progress.md is required.\n"
            "- For verification scripts: import the actual target module/package and print a clear success message only after the check passes.\n"
            "- For Git/RCA tasks: ALWAYS run `git log --oneline` first to find the real commit hash.\n"
            "  Copy the exact hash from git output into your report. NEVER write a commit hash from memory.\n"
            "  After writing the report, verify: `git cat-file -e <hash>^{commit}` must succeed.\n"
            "- For document generation: ensure all required sections exist with substantive content meeting length requirements.\n\n"
            "## Error Recovery\n"
            "- If a command fails, read the error message carefully and fix the root cause.\n"
            "- After 2 failures with the same approach, try a fundamentally different method.\n"
            "- Do not give up. Exhaust your tool budget working toward the solution.\n\n"
            "## Iterative Completion\n"
            "- For code-fix/debug/repair tasks: run the FULL test suite after each fix. Count remaining failures.\n"
            "  Repeat fix→test→verify cycles until ALL tests pass or all layers/subtasks are done.\n"
            "  Never stop after fixing only one issue if the suite reports more failures.\n"
            "- For document/report tasks: verify all required sections exist, meet length requirements,\n"
            "  and contain substantive (non-placeholder) content before finishing.\n\n"
            "## Monitoring & Heartbeat Tasks\n"
            "- For tasks requiring a monitoring loop, polling, or heartbeat detection:\n"
            "  Start monitoring on your VERY FIRST tool call — do NOT read documentation first.\n"
            "  If the framework exposes a heartbeat/scheduled-task config mechanism, use it (configure it\n"
            "  with the input source, output target, and any filter the prompt specifies, using relative paths).\n"
            "  Then run a single polling script that watches for new inputs and writes the required outputs\n"
            "  for the full duration the prompt asks for. Time-sensitive tasks penalize late detection —\n"
            "  every second spent reading before monitoring is lost from the detection window.\n\n"
            "## Document Synthesis & Report Tasks\n"
            "- When producing analytical reports, ensure each evaluation criterion mentioned in the prompt\n"
            "  has a corresponding EXPLICIT section/paragraph in the output.\n"
            "- Use the exact terminology from the prompt as section headers.\n"
            "- Before finishing, self-check: does the report contain a visible segment for EVERY\n"
            "  required element listed in the prompt? If not, add the missing sections.\n\n"
            "## Multi-Round Sessions\n"
            "- You may receive multiple prompts within the same session. Information from earlier rounds\n"
            "  is retained in your conversation memory. If a task references a value given in a previous\n"
            "  round, recall it from memory — do not attempt to find it in workspace files.\n\n"
            "## Final Check\n"
            "Before your last message, always verify:\n"
            "1. All required output files exist in the correct location\n"
            "2. Files are non-empty and well-formed\n"
            "3. Filenames match exactly what was requested (case-sensitive)\n"
            "4. Data values are derived from actual inputs, not fabricated\n"
            "Once all deliverables are verified, stop immediately. Do not perform additional cleanup, exploration, or bonus work.\n"
        )

    _DELIVERABLE_EXTENSIONS = {
        ".csv", ".json", ".jsonl", ".txt", ".md", ".html", ".xml", ".yaml", ".yml",
        ".py", ".js", ".ts", ".sql", ".sh", ".ps1", ".png", ".jpg", ".jpeg", ".svg",
        ".pdf", ".zip", ".tar", ".gz", ".parquet", ".xlsx", ".docx",
    }
    _IGNORED_OUTPUT_PREFIXES = (
        "fixtures/", "fixture/", "in/", "input/", "inputs/", "data/", "datasets/",
        "images/", "image/", "assets/", "sessions/", "__pycache__/", ".git/", ".pytest_cache/",
    )
    _OUTPUT_CONTEXT_MARKERS = (
        "$workspace/out/", "/out/", "\\out\\", " output", "outputs", "deliverable", "deliverables",
        "artifact", "artifacts", "required", "write", "create", "save", "produce", "generate",
        "append", "place", "store", "named", "called", "report", "result", "notification",
        "\u8f93\u51fa", "\u4ea7\u51fa", "\u4ea7\u7269", "\u4ea4\u4ed8", "\u5fc5\u987b", "\u52a1\u5fc5", "\u9700\u8981",
        "\u9700", "\u751f\u6210", "\u521b\u5efa", "\u5199\u5165", "\u8ffd\u52a0", "\u4fdd\u5b58", "\u653e\u5165",
        "\u653e\u5728", "\u653e\u5230", "\u62a5\u544a", "\u901a\u77e5", "\u6700\u7ec8", "\u5b8c\u6574", "\u7ed3\u679c",
    )
    _OUTPUT_SECTION_MARKERS = (
        "$workspace/out/", "/out/", "\\out\\", "outputs", "deliverable", "deliverables",
        "artifact", "artifacts", "required output", "required outputs", "required artifact",
        "required artifacts", "must produce", "must create", "must write", "write to", "save to",
        "produce the following", "create the following", "output file", "output files",
        "\u8f93\u51fa", "\u4ea7\u51fa", "\u4ea7\u7269", "\u4ea4\u4ed8", "\u5199\u5165", "\u4fdd\u5b58\u5230", "\u653e\u5165",
        "\u653e\u5728", "\u653e\u5230", "\u4ee5\u4e0b\u6587\u4ef6", "\u4ee5\u4e0b\u4ea7\u7269",
    )
    _INPUT_SECTION_MARKERS = (
        "$workspace/in/", "/in/", "\\in\\", " input", "inputs", "fixture", "fixtures/",
        "input file", "input files", "input directory", "input data", "provided file", "provided files",
        "\u8f93\u5165", "\u8f93\u5165\u6587\u4ef6", "\u8d44\u6599", "\u6570\u636e", "\u6536\u5230", "\u8bfb\u53d6",
        "\u90ae\u7bb1\u76ee\u5f55", "\u5b57\u6bb5\u5305\u62ec",
    )
    _INPUT_CONTEXT_MARKERS = (
        "$workspace/in/", "/in/", "\\in\\", " input", "inputs", "fixture", "fixtures/", "received",
        "read all input", "\u6536\u5230", "\u8bfb\u53d6", "\u90ae\u7bb1\u76ee\u5f55", "\u5b57\u6bb5\u5305\u62ec",
    )
    _SECTION_STOP_MARKERS = (
        "score", "scoring", "graded", "\u8bc4\u5206", "\u8bc4\u4f30\u6807\u51c6",
    )
    _FILE_TOKEN_RE = re.compile(
        r"(?:\$\{?WORKSPACE\}?|%WORKSPACE%|WORKSPACE|\.{1,2}|[A-Za-z0-9_.-]+)?"
        r"(?:[/\\][A-Za-z0-9_.-]+)*[/\\]?[A-Za-z0-9][A-Za-z0-9_.-]*\.[A-Za-z0-9]{1,8}",
        re.IGNORECASE,
    )

    _DIRECTORY_TOKEN_RE = re.compile(
        r"(?:\$\{?WORKSPACE\}?|%WORKSPACE%|WORKSPACE|\.{1,2})?"
        r"[/\\]?(?:[A-Za-z0-9_.-]+[/\\])+",
        re.IGNORECASE,
    )

    @classmethod
    def _extract_required_deliverables(cls, prompt_text: str) -> list[str]:
        candidates: list[str] = []
        output_section = False
        input_section = False
        for raw_line in (prompt_text or "").splitlines():
            line = raw_line.strip()
            if not line:
                input_section = False
                continue

            explicit_output_path = cls._has_explicit_output_path(line)
            if cls._is_section_stop_line(line):
                output_section = False
                input_section = False

            if cls._starts_output_section(line, explicit_output_path=explicit_output_path):
                output_section = True
                input_section = False
            elif cls._starts_input_section(line):
                input_section = True
                output_section = False

            line_output_context = cls._has_marker(line, cls._OUTPUT_CONTEXT_MARKERS)
            line_input_context = cls._has_marker(line, cls._INPUT_CONTEXT_MARKERS)
            blocked_by_input_context = input_section or (line_input_context and not output_section)

            for match in cls._FILE_TOKEN_RE.finditer(line):
                candidate = match.group(0).strip().strip("`'\".,;:) ]}")
                normalized = cls._normalize_deliverable_path(candidate)
                if not normalized:
                    continue
                lowered = normalized.lower()
                eligible = (
                    lowered.startswith(("out/", "outputs/"))
                    or explicit_output_path
                    or output_section
                    or (line_output_context and not blocked_by_input_context)
                )
                if not eligible:
                    continue
                if cls._looks_like_example_only(line, match.start()) and not explicit_output_path:
                    continue
                if normalized not in candidates:
                    candidates.append(normalized)

            for match in cls._DIRECTORY_TOKEN_RE.finditer(line):
                candidate = match.group(0).strip().strip("`'\".,;:) ]}")
                normalized = cls._normalize_deliverable_path(candidate, allow_directory=True)
                if not normalized:
                    continue
                lowered = normalized.lower()
                eligible = (
                    lowered.startswith(("out/", "outputs/"))
                    or explicit_output_path
                    or output_section
                    or (line_output_context and not blocked_by_input_context)
                )
                if not eligible:
                    continue
                if cls._looks_like_example_only(line, match.start()) and not explicit_output_path:
                    continue
                if normalized not in candidates:
                    candidates.append(normalized)
        return candidates

    @classmethod
    def _starts_output_section(cls, line: str, *, explicit_output_path: bool) -> bool:
        if explicit_output_path:
            return True
        if not cls._has_marker(line, cls._OUTPUT_SECTION_MARKERS):
            return False
        stripped = line.strip()
        lowered = stripped.casefold()
        return (
            stripped.startswith(("#", "-", "*"))
            or bool(re.match(r"^(?:\d+[\.)]|[A-Za-z][\.)])\s+", stripped))
            or stripped.endswith((":", "\uff1a"))
            or any(phrase in lowered for phrase in ("following", "below", "\u4ee5\u4e0b"))
        )

    @classmethod
    def _starts_input_section(cls, line: str) -> bool:
        if not cls._has_marker(line, cls._INPUT_SECTION_MARKERS):
            return False
        stripped = line.strip()
        lowered = stripped.casefold()
        return (
            stripped.startswith(("#", "-", "*"))
            or bool(re.match(r"^(?:\d+[\.)]|[A-Za-z][\.)])\s+", stripped))
            or stripped.endswith((":", "\uff1a"))
            or any(phrase in lowered for phrase in ("following", "below", "\u4ee5\u4e0b"))
        )

    @staticmethod
    def _is_section_stop_line(line: str) -> bool:
        stripped = line.strip().strip("#*- ")
        if not stripped:
            return False
        lowered = stripped.casefold().strip(":\uff1a")
        if lowered in {"score", "scoring", "graded", "grading", "scoring criteria"}:
            return True
        if re.match(r"^(?:score|scoring|graded|grading)\b", lowered):
            return True
        return any(marker in stripped for marker in ("\u8bc4\u5206", "\u8bc4\u4f30\u6807\u51c6"))

    @staticmethod
    def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
        lowered = text.casefold().replace("\\", "/")
        return any(marker.casefold().replace("\\", "/") in lowered for marker in markers)

    @staticmethod
    def _has_explicit_output_path(text: str) -> bool:
        lowered = text.casefold().replace("\\", "/")
        return "$workspace/out/" in lowered or "/out/" in lowered or lowered.startswith("out/")

    @staticmethod
    def _looks_like_example_only(line: str, start: int) -> bool:
        prefix = line[:start].casefold()[-40:]
        return any(marker in prefix for marker in ("e.g.", "example", "for example", "例如", "如"))

    @classmethod
    def _normalize_deliverable_path(cls, value: str, *, allow_directory: bool = False) -> str | None:
        cleaned = value.replace("\\", "/").strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"(?i)^(?:\$\{?WORKSPACE\}?|%WORKSPACE%|WORKSPACE)/+", "", cleaned)
        if not cleaned or "://" in cleaned or cleaned.startswith("/"):
            return None
        if re.match(r"^[A-Za-z]:/", cleaned):
            return None
        cleaned = cleaned.lstrip("./")
        if not cleaned or ".." in Path(cleaned).parts:
            return None
        if allow_directory:
            if not cleaned.endswith("/"):
                return None
            cleaned = cleaned.rstrip("/") + "/"
            if Path(cleaned.rstrip("/")).suffix:
                return None
        else:
            suffix = Path(cleaned).suffix.lower()
            if suffix not in cls._DELIVERABLE_EXTENSIONS:
                return None
        lowered = cleaned.lower()
        if allow_directory and lowered in {"out/", "outputs/"}:
            return None
        if lowered.startswith(cls._IGNORED_OUTPUT_PREFIXES):
            return None
        if lowered in {"prompt.txt", "readme.md", "requirements.txt", "package.json", "pyproject.toml"}:
            return None
        return cleaned

    @classmethod
    def _collect_workspace_outputs(cls, workspace: Path) -> list[str]:
        outputs: list[str] = []
        for file_path in sorted(workspace.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(workspace).as_posix()
            lowered = rel.lower()
            if lowered.startswith(cls._IGNORED_OUTPUT_PREFIXES) or lowered.startswith("."):
                continue
            if file_path.stat().st_size <= 0:
                continue
            outputs.append(rel)
        return outputs

    @staticmethod
    def _required_deliverable_candidates(workspace: Path, rel: str) -> list[Path]:
        normalized = rel.replace("\\", "/").lstrip("./")
        is_directory = normalized.endswith("/")
        normalized = normalized.rstrip("/") if is_directory else normalized
        lowered = normalized.lower()
        if "/" in normalized or lowered.startswith(("out/", "outputs/")):
            return [workspace / normalized]
        return [workspace / normalized, workspace / "out" / normalized]

    @classmethod
    def _missing_required_deliverables(cls, workspace: Path, required_deliverables: list[str]) -> list[str]:
        missing: list[str] = []
        for rel in required_deliverables:
            candidates = cls._required_deliverable_candidates(workspace, rel)
            is_directory = rel.endswith("/")
            if is_directory:
                if not any(target.is_dir() for target in candidates):
                    missing.append(rel)
            elif not any(target.is_file() and target.stat().st_size > 0 for target in candidates):
                missing.append(rel)
        return missing

    @staticmethod
    def _build_reentry_prompt(
        original_prompt: str,
        *,
        reason: str,
        required_deliverables: list[str],
        produced_files: list[str],
    ) -> str:
        required_text = "\n".join(f"- {path}" for path in required_deliverables) or "- Infer required output filenames from the task prompt."
        produced_text = "\n".join(f"- {path}" for path in produced_files) or "- None"
        return (
            f"{original_prompt}\n\n"
            "[AUTOMATED FINAL QA RE-ENTRY]\n"
            f"Reason: {reason}.\n\n"
            "Required deliverables detected from the prompt:\n"
            f"{required_text}\n\n"
            "Current non-empty workspace outputs:\n"
            f"{produced_text}\n\n"
            "Continue the task now. Create every missing required deliverable using exact, case-sensitive filenames. "
            "If the original prompt names a workspace-root deliverable such as `progress.md` or `final_report.md`, keep that root copy and also mirror it under `out/` for final QA. "
            "Only use a different location when the prompt explicitly names an output subdirectory or absolute workspace-relative path. "
            "Do not summarize instead of writing files. After writing, run a quick validation that each deliverable exists, is non-empty, and is in the expected directory."
        )
    _IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tiff', '.tif'}
    _MAX_IMAGE_BYTES = 20 * 1024 * 1024  # skip images > 20MB

    @classmethod
    def _build_multimodal_content(cls, prompt_text: str, workspace: Path) -> list | None:
        """Scan prompt for image file references and workspace fixtures for images.
        
        Returns a list of OpenAI content blocks (text + image_url) if images found,
        or None if no images (caller falls back to plain text).
        """
        image_paths: list[Path] = []

        # Strategy 1: Find absolute paths referenced in prompt text
        # Matches patterns like /path/to/image.png or C:\path\to\image.jpg
        path_pattern = re.compile(r'(?:[A-Za-z]:\\|/)[\w\\/\-. ]+\.(?:png|jpe?g|gif|bmp|webp|tiff?)', re.IGNORECASE)
        for match in path_pattern.finditer(prompt_text):
            p = Path(match.group())
            if p.exists() and p.stat().st_size <= cls._MAX_IMAGE_BYTES:
                if p not in image_paths:
                    image_paths.append(p)

        # Strategy 2: Scan workspace fixtures directories for image files
        for subdir_name in ('fixtures', 'image', 'images', 'input', 'inputs', 'in'):
            subdir = workspace / subdir_name
            if subdir.is_dir():
                for f in sorted(subdir.rglob('*')):
                    if f.is_file() and f.suffix.lower() in cls._IMAGE_EXTENSIONS:
                        if f.stat().st_size <= cls._MAX_IMAGE_BYTES and f not in image_paths:
                            image_paths.append(f)

        # Strategy 3: Check workspace root for image files (some tasks put images there directly)
        if workspace.is_dir():
            for f in sorted(workspace.iterdir()):
                if f.is_file() and f.suffix.lower() in cls._IMAGE_EXTENSIONS:
                    if f.stat().st_size <= cls._MAX_IMAGE_BYTES and f not in image_paths:
                        image_paths.append(f)

        if not image_paths:
            return None

        # Build multimodal content blocks
        # Add vision hint to tell the agent images are already attached
        vision_hint = (
            "\n\n[VISION NOTE] The image(s) referenced in this task have been attached directly to this message. "
            "You can see them above. Analyze the images directly from what you see. "
            "For simple recognition tasks, visual analysis is sufficient. "
            "For precise counting or data extraction from dense/complex images, you may ALSO write a Python script "
            "(using PIL/OpenCV) to load the image file from the workspace and perform programmatic analysis to cross-verify your visual count."
        )
        content_blocks: list[dict] = [{"type": "text", "text": prompt_text + vision_hint}]

        for img_path in image_paths:
            try:
                data = img_path.read_bytes()
                b64 = base64.b64encode(data).decode('ascii')
                suffix = img_path.suffix.lower().lstrip('.')
                if suffix in ('jpg', 'jpeg'):
                    mime = 'image/jpeg'
                elif suffix == 'png':
                    mime = 'image/png'
                elif suffix == 'gif':
                    mime = 'image/gif'
                elif suffix == 'webp':
                    mime = 'image/webp'
                else:
                    mime = f'image/{suffix}'
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{b64}",
                        "detail": "high"
                    }
                })
            except (OSError, MemoryError):
                continue  # skip unreadable images

        # Only return multimodal if we actually loaded at least one image
        if len(content_blocks) > 1:
            return content_blocks
        return None

    @staticmethod
    def _build_prompt(ctx: AdapterRunContext, *, timeout_sec: int) -> str:
        env_hints = ""
        if ctx.env:
            skip_keys = {"PYTHONPATH", "PATH", "HOME", "USER", "SHELL", "TERM"}
            useful = {k: v for k, v in ctx.env.items()
                      if not k.startswith("_") and v and k not in skip_keys}
            if useful:
                lines = [f"  {k}={v}" for k, v in sorted(useful.items())]
                env_hints = "Env:\n" + "\n".join(lines) + "\n\n"
        return (
            f"Workspace: {ctx.workspace} | Timeout: {timeout_sec}s | Task: {ctx.task.task_id}\n"
            f"{env_hints}"
            "EXECUTION PROTOCOL:\n"
            "1. `ls` workspace → discover in/, fixtures/, out/ structure\n"
            "2. Read ALL input files completely. For multi-file tasks: read every file, never assume from filename.\n"
            "3. Execute: produce each artifact to completion. Run scripts you create. "
            "For code: reproduce → fix → run full test suite → repeat until all pass.\n"
            "4. Verify: file exists, non-empty, parses correctly, schema matches, content is substantive.\n"
            "5. If verify fails, fix and re-verify. Never finish with known defects.\n\n"
            "CRITICAL RULES:\n"
            "- Output files: place in exact location specified by prompt. If prompt says 'workspace root' or '当前工作目录', write there directly, not in out/. If prompt says out/, use out/. If ambiguous, default to out/.\n"
            "- JSON keys case-sensitive. Validate with json.loads().\n"
            "- Git: `git config --global --add safe.directory .` first. Use only real hashes from `git log`.\n"
            "- progress.md: pending→done status per subtask with timestamps. It is graded.\n"
            "- Task decomposition: cover ALL themes/aspects mentioned in the prompt. Execute every subtask to its done state.\n"
            "- CODE DEBUG/REPAIR: Run FULL test suite after EACH fix. Count remaining failures. "
            "Repeat fix→test until ALL pass. Never stop after partial fix — partial fixes score poorly. "
            "Apply fixes to the file that the prompt's verification step actually runs, not to a copy.\n"
            "- Iterative/layered tasks: complete ALL layers/rounds. Do not stop early.\n"
            "- CSV: validate headers, column count, row count, UTF-8 no BOM. Never use utf-8-sig.\n"
            "- OCR/image-table: script-based extraction, verify row counts against source. "
            "For vision counting: methodical, multi-direction, cross-verify. Undercounting at edges is common.\n"
            "- Document synthesis/reports: Before writing, list EVERY required element from the prompt. "
            "After writing, re-read your output and verify each element has a dedicated, substantive section. "
            "A missing required element scores zero. Meet whatever length requirement the prompt specifies.\n"
            "- Analytical reports: include concrete numeric evidence, cover every requested topic.\n"
            "- MULTI-ARTIFACT: If prompt lists N required outputs, produce ALL N. Verify each exists before finishing.\n"
            "- Comparison/audit: extract both sides into structured data, diff programmatically.\n"
            "- Budget: " + str(timeout_sec) + "s. Spend 20% reading, 80% executing.\n"
            "- After 2 same-approach failures, try fundamentally different method.\n"
            "- STOP CONDITION: Once all required deliverables are verified, finish immediately. Do not explore, refactor, or add unrequested features.\n\n"
            "TASK PROMPT:\n"
            f"{ctx.prompt}\n"
        )
