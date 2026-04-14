from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

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
        providers = data.get("providers") or {}
        if isinstance(providers, dict):
            for provider_name, provider_cfg in providers.items():
                if not isinstance(provider_cfg, dict):
                    continue
                upstream = str(provider_cfg.get("apiBase") or "").strip()
                if not upstream:
                    continue
                prefix = f"/nanobot/{provider_name}"
                routes[prefix] = {
                    "framework": "nanobot",
                    "provider": str(provider_name),
                    "upstream": upstream,
                }
                provider_cfg["apiBase"] = f"{proxy_base_url}{prefix}"
        if routes:
            register_routes(proxy_routes_file, routes)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class NanoBotAdapter(BaseAdapter):
    name = "nanobot"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        command = str(ctx.model_config.get("command") or "nanobot")
        user_config_raw = str(ctx.model_config.get("user_config") or "config/nanobot.json")
        user_config = _resolve_project_path(user_config_raw)
        if not user_config.is_file():
            return AdapterRunResult(ok=False, stderr=f"missing NanoBot source config: {user_config}")

        isolated_home = ctx.sandbox
        nanobot_home = isolated_home / ".nanobot"
        nanobot_home.mkdir(parents=True, exist_ok=True)
        nanobot_workspace = nanobot_home / "workspace"
        nanobot_workspace.mkdir(parents=True, exist_ok=True)

        sandbox_user_config = nanobot_home / "nanobot_src.json"
        shutil.copy2(user_config, sandbox_user_config)
        merged_cfg = nanobot_home / "nanobot.json"
        _merge_user_config(
            sandbox_user_config,
            nanobot_workspace,
            merged_cfg,
            proxy_base_url=str(ctx.env.get("CLAWBENCH_LLM_PROXY_URL") or ""),
            proxy_routes_file=Path(ctx.env["CLAWBENCH_LLM_PROXY_ROUTES"]) if ctx.env.get("CLAWBENCH_LLM_PROXY_ROUTES") else None,
        )

        cmd = [
            command,
            "agent",
            "--message",
            ctx.prompt,
            "--session",
            ctx.session_id,
            "--workspace",
            str(nanobot_workspace),
            "--config",
            str(merged_cfg),
        ]
        if bool(ctx.model_config.get("no_markdown")):
            cmd.append("--no-markdown")
        if bool(ctx.model_config.get("logs")):
            cmd.append("--logs")
        else:
            cmd.append("--no-logs")

        env = os.environ.copy()
        env.update(ctx.env)
        env["HOME"] = str(isolated_home)
        env["NANOBOT_HOME"] = str(nanobot_home)
        env["NANOBOT_WORKSPACE"] = str(nanobot_workspace)
        env["WORKSPACE"] = str(ctx.workspace)
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
                "nanobot_config_path": str(merged_cfg),
                "source_user_config_path": str(user_config),
                "sandbox_user_config_path": str(sandbox_user_config),
                "isolated_home": str(isolated_home),
                "nanobot_home": str(nanobot_home),
                "nanobot_workspace": str(nanobot_workspace),
                "workspace": str(ctx.workspace),
            },
        )
