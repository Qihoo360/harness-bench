from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from clawbench_v2.adapters.base import BaseAdapter
from clawbench_v2.models import AdapterRunContext, AdapterRunResult
from clawbench_v2.usage_proxy import register_routes


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_project_path(raw: str | Path) -> Path:
    p = Path(os.path.expanduser(str(raw)))
    if not p.is_absolute():
        p = _project_root() / p
    return p.resolve()


def _resolve_optional_path(raw: Any) -> Path | None:
    if raw in (None, ""):
        return None
    return _resolve_project_path(str(raw))


def _resolve_command(raw: str) -> str:
    expanded = os.path.expanduser(raw)
    if "/" in expanded or "\\" in expanded:
        return str(_resolve_project_path(expanded))
    return raw


def _provider_base_url(provider_cfg: dict[str, Any]) -> str:
    return str(provider_cfg.get("base_url") or provider_cfg.get("baseUrl") or "").strip()


def _set_provider_base_url(provider_cfg: dict[str, Any], value: str) -> None:
    if "base_url" in provider_cfg:
        provider_cfg["base_url"] = value
        return
    if "baseUrl" in provider_cfg:
        provider_cfg["baseUrl"] = value
        return
    provider_cfg["base_url"] = value


def _merge_user_config(
    user_config: Path,
    agent_workspace: Path,
    out_path: Path,
    proxy_base_url: str = "",
    proxy_routes_file: Path | None = None,
) -> None:
    data = json.loads(user_config.read_text(encoding="utf-8"))
    agents = data.setdefault("agents", {})
    defaults = agents.setdefault("defaults", {})
    defaults["workspace"] = str(agent_workspace)
    defaults["workspace_path"] = str(agent_workspace)
    data.setdefault("channels", {})
    data["channels"]["cli"] = True
    data.setdefault("memory", {"backend": "sqlite", "auto_save": True})
    data.setdefault(
        "gateway",
        {"host": "127.0.0.1", "port": 3000, "require_pairing": True},
    )
    data.setdefault(
        "autonomy",
        {"level": "supervised", "workspace_only": True, "max_actions_per_hour": 20},
    )
    data.setdefault(
        "security",
        {"sandbox": {"backend": "auto"}, "audit": {"enabled": True}},
    )

    if proxy_base_url and proxy_routes_file is not None:
        routes: dict[str, dict[str, str]] = {}
        providers = ((data.get("models") or {}).get("providers") or {})
        if isinstance(providers, dict):
            for provider_name, provider_cfg in providers.items():
                if not isinstance(provider_cfg, dict):
                    continue
                upstream = _provider_base_url(provider_cfg)
                if not upstream:
                    continue
                prefix = f"/nullclaw/{provider_name}"
                routes[prefix] = {
                    "framework": "nullclaw",
                    "provider": str(provider_name),
                    "upstream": upstream,
                }
                _set_provider_base_url(provider_cfg, f"{proxy_base_url}{prefix}")
        if routes:
            register_routes(proxy_routes_file, routes)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _prepare_internal_workspace(runtime_workspace: Path) -> None:
    if runtime_workspace.exists() or runtime_workspace.is_symlink():
        if runtime_workspace.is_symlink() or runtime_workspace.is_file():
            runtime_workspace.unlink()
        else:
            shutil.rmtree(runtime_workspace)
    runtime_workspace.mkdir(parents=True, exist_ok=True)

    memory_dir = runtime_workspace / "memory"
    sessions_dir = runtime_workspace / "sessions"
    state_dir = runtime_workspace / "state"
    memory_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)


def _move_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.move(str(src), str(dst))


def _normalize_outer_workspace_state(benchmark_workspace: Path, runtime_workspace: Path) -> None:
    memory_dir = runtime_workspace / "memory"
    state_dir = runtime_workspace / "state"

    for name in ("memory.db", "memory.db-shm", "memory.db-wal"):
        _move_if_exists(benchmark_workspace / name, memory_dir / name)

    outer_state_dir = benchmark_workspace / ".nullclaw"
    if outer_state_dir.is_dir():
        for child in list(outer_state_dir.iterdir()):
            _move_if_exists(child, state_dir / child.name)
        shutil.rmtree(outer_state_dir, ignore_errors=True)
    elif outer_state_dir.exists():
        outer_state_dir.unlink()


def _rewrite_prompt_for_runtime(prompt: str, benchmark_workspace: Path) -> str:
    workspace_str = str(benchmark_workspace)
    rewritten = prompt.replace(f"`{workspace_str}`", "`当前工作目录`")
    rewritten = rewritten.replace(workspace_str, "当前工作目录")
    prefix = (
        "补充运行说明：当前命令工作目录已经映射为题目的工作区，"
        "请直接使用相对路径（如 `in/`、`out/`）完成任务，"
        "不要再执行 `cd` 到绝对路径。"
        "如果 `shell` 工具因策略限制无法执行带重定向、管道或链式命令，"
        "请立即改用相对路径的文件工具（如 `file_write`、`file_append`）"
        "直接在 `out/` 下生成等价产物，不要反复重试被拦截的命令。\n\n"
    )
    return prefix + rewritten


class NullClawAdapter(BaseAdapter):
    name = "nullclaw"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        command = _resolve_command(str(ctx.model_config.get("command") or "nullclaw"))
        source_config = _resolve_optional_path(ctx.env.get("NULLCLAW_SOURCE_CONFIG") or ctx.model_config.get("user_config"))
        if source_config is None:
            source_config = _resolve_project_path("config/nullclaw.json")

        isolated_home = ctx.sandbox
        state_dir = isolated_home / ".nullclaw"
        state_dir.mkdir(parents=True, exist_ok=True)
        runtime_workspace = state_dir / "workspace"
        _prepare_internal_workspace(runtime_workspace)
        (ctx.workspace / "in").mkdir(parents=True, exist_ok=True)
        (ctx.workspace / "out").mkdir(parents=True, exist_ok=True)

        if not source_config.is_file():
            return AdapterRunResult(
                ok=False,
                stderr=(
                    "missing NullClaw source config: "
                    f"{source_config}. Copy config/nullclaw.example.json to config/nullclaw.json, "
                    "or set NULLCLAW_SOURCE_CONFIG in config/bench.env"
                ),
            )

        sandbox_user_config = state_dir / "nullclaw_src.json"
        try:
            shutil.copy2(source_config, sandbox_user_config)
            merged_cfg = state_dir / "config.json"
            _merge_user_config(
                sandbox_user_config,
                ctx.workspace,
                merged_cfg,
                proxy_base_url=str(ctx.env.get("CLAWBENCH_LLM_PROXY_URL") or ""),
                proxy_routes_file=Path(ctx.env["CLAWBENCH_LLM_PROXY_ROUTES"]) if ctx.env.get("CLAWBENCH_LLM_PROXY_ROUTES") else None,
            )
        except json.JSONDecodeError as exc:
            return AdapterRunResult(ok=False, stderr=f"invalid NullClaw config JSON: {source_config}: {exc}")
        except OSError as exc:
            return AdapterRunResult(ok=False, stderr=f"failed to prepare NullClaw config: {exc}")

        if "/" not in command and "\\" not in command and shutil.which(command) is None:
            return AdapterRunResult(ok=False, stderr=f"NullClaw command not found on PATH: {command}")

        cmd = [command, "agent", "-m", _rewrite_prompt_for_runtime(ctx.prompt, ctx.workspace)]
        env = os.environ.copy()
        env.update(ctx.env)
        env["HOME"] = str(isolated_home)
        env["NULLCLAW_HOME"] = str(state_dir)
        env["NULLCLAW_STATE_DIR"] = str(state_dir)
        env["NULLCLAW_CONFIG_PATH"] = str(merged_cfg)
        env["NULLCLAW_SESSION_ID"] = ctx.session_id
        env["NULLCLAW_WORKSPACE"] = str(ctx.workspace)

        try:
            completed = subprocess.run(
                cmd,
                cwd=str(ctx.workspace),
                text=True,
                capture_output=True,
                timeout=ctx.timeout_sec,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            _normalize_outer_workspace_state(ctx.workspace, runtime_workspace)
            return AdapterRunResult(ok=False, command=cmd, stderr=f"NullClaw command timed out after {ctx.timeout_sec}s")
        except FileNotFoundError:
            return AdapterRunResult(ok=False, command=cmd, stderr=f"NullClaw command not found: {command}")
        except OSError as exc:
            return AdapterRunResult(ok=False, command=cmd, stderr=f"NullClaw command failed to start: {exc}")

        _normalize_outer_workspace_state(ctx.workspace, runtime_workspace)

        return AdapterRunResult(
            ok=completed.returncode == 0,
            command=cmd,
            stdout=completed.stdout,
            stderr=completed.stderr,
            metadata={
                "returncode": completed.returncode,
                "state_dir": str(state_dir),
                "nullclaw_home": str(state_dir),
                "nullclaw_config_path": str(merged_cfg),
                "source_user_config_path": str(source_config),
                "sandbox_user_config_path": str(sandbox_user_config),
                "workspace": str(ctx.workspace),
            },
        )
