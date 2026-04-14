from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from clawbench_v2.adapters.base import BaseAdapter
from clawbench_v2.models import AdapterRunContext, AdapterRunResult
from clawbench_v2.usage_proxy import register_routes


def _merge_user_config(
    user_config: Path,
    workspace: Path,
    out_path: Path,
    proxy_base_url: str = "",
    proxy_routes_file: Path | None = None,
) -> None:
    data = json.loads(user_config.read_text(encoding="utf-8"))
    data.setdefault("agents", {}).setdefault("defaults", {})["workspace"] = str(workspace)
    if proxy_base_url and proxy_routes_file is not None:
        routes: dict[str, dict[str, str]] = {}
        providers = ((data.get("models") or {}).get("providers") or {})
        if isinstance(providers, dict):
            for provider_name, provider_cfg in providers.items():
                if not isinstance(provider_cfg, dict):
                    continue
                upstream = str(provider_cfg.get("baseUrl") or "").strip()
                if not upstream:
                    continue
                prefix = f"/openclaw/{provider_name}"
                routes[prefix] = {
                    "framework": "openclaw",
                    "provider": str(provider_name),
                    "upstream": upstream,
                }
                provider_cfg["baseUrl"] = f"{proxy_base_url}{prefix}"
        if routes:
            register_routes(proxy_routes_file, routes)
    # Benchmark runs only need model + agent workspace. Drop user-specific channel/plugin
    # wiring so isolated OPENCLAW_HOME does not fail validation on missing local installs.
    data["channels"] = {}
    plugins = data.get("plugins")
    if isinstance(plugins, dict):
        plugins["allow"] = []
        plugins["entries"] = {}
        plugins["installs"] = {}
    data.pop("wizard", None)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_project_path(raw: str | Path) -> Path:
    p = Path(os.path.expanduser(str(raw)))
    if not p.is_absolute():
        p = _project_root() / p
    return p.resolve()


def _copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def _source_state_dir(source_config: Path) -> Path:
    return source_config.resolve().parent


def _sync_minimal_auth_state(target_state_dir: Path, source_config: Path) -> None:
    source_openclaw_state = _source_state_dir(source_config)
    # Minimal auth/runtime state needed by embedded local agent.
    _copy_if_exists(source_openclaw_state / "agents" / "main" / "agent" / "auth-profiles.json", target_state_dir / "agents" / "main" / "agent" / "auth-profiles.json")
    _copy_if_exists(source_openclaw_state / "identity", target_state_dir / "identity")
    _copy_if_exists(source_openclaw_state / "agents" / "main" / "agent" / "models.json", target_state_dir / "agents" / "main" / "agent" / "models.json")


class OpenClawAdapter(BaseAdapter):
    name = "openclaw"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        command = str(ctx.model_config.get("command") or "openclaw")
        args = list(ctx.model_config.get("args") or ["agent", "--local"])
        user_config_raw = str(ctx.model_config.get("user_config") or os.path.expanduser("~/.openclaw/openclaw.json"))
        user_config = _resolve_project_path(user_config_raw)
        if not user_config.is_file():
            return AdapterRunResult(ok=False, stderr=f"missing OpenClaw source config: {user_config}")

        isolated_home = ctx.sandbox
        openclaw_home = isolated_home / ".openclaw"
        openclaw_home.mkdir(parents=True, exist_ok=True)
        # _sync_minimal_auth_state(openclaw_home, user_config)
        openclaw_workspace = openclaw_home / "workspace"
        openclaw_workspace.mkdir(parents=True, exist_ok=True)

        sandbox_user_config = openclaw_home / "openclaw_src.json"
        shutil.copy2(user_config, sandbox_user_config)
        merged_cfg = openclaw_home / "openclaw.json"
        _merge_user_config(
            sandbox_user_config,
            openclaw_workspace,
            merged_cfg,
            proxy_base_url=str(ctx.env.get("CLAWBENCH_LLM_PROXY_URL") or ""),
            proxy_routes_file=Path(ctx.env["CLAWBENCH_LLM_PROXY_ROUTES"]) if ctx.env.get("CLAWBENCH_LLM_PROXY_ROUTES") else None,
        )

        cmd = [
            command, 
            *args, 
            "--session-id", 
            ctx.session_id, 
            "--timeout", 
            str(ctx.timeout_sec), 
            "--message", 
            ctx.prompt
        ]
        env = os.environ.copy()
        env.update(ctx.env)
        env["HOME"] = str(isolated_home)
        env["OPENCLAW_HOME"] = str(openclaw_home)
        env["OPENCLAW_CONFIG_PATH"] = str(merged_cfg)
        env["OPENCLAW_STATE_DIR"] = str(openclaw_home)
        if bool(ctx.model_config.get("use_gateway")):
            env["OPENCLAW_AGENT_WORKSPACE_DIR"] = str(ctx.workspace.resolve())
        completed = subprocess.run(
            cmd,
            cwd=str(ctx.sandbox),
            text=True,
            capture_output=True,
            timeout=ctx.timeout_sec,
            env=env,
            check=False,
        )
        return AdapterRunResult(
            ok=completed.returncode == 0,
            command=cmd,
            stdout=completed.stdout,
            stderr=completed.stderr,
            metadata={
                "returncode": completed.returncode,
                "openclaw_config_path": str(merged_cfg),
                "source_user_config_path": str(user_config),
                "sandbox_user_config_path": str(sandbox_user_config),
                "isolated_home": str(isolated_home),
                "openclaw_home": str(openclaw_home),
                "workspace": str(ctx.workspace),
            },
        )
