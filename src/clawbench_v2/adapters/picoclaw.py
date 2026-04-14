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
    task_workspace: Path,
    out_path: Path,
    proxy_base_url: str = "",
    proxy_routes_file: Path | None = None,
) -> None:
    data = json.loads(user_config.read_text(encoding="utf-8"))
    agents = data.setdefault("agents", {})
    defaults = agents.setdefault("defaults", {})
    defaults["workspace"] = str(workspace)

    tools = data.setdefault("tools", {})
    task_workspace_str = str(task_workspace)

    def _append_unique_path(raw: object) -> list[str]:
        items = list(raw) if isinstance(raw, list) else []
        values = [str(item) for item in items]
        if task_workspace_str not in values:
            values.append(task_workspace_str)
        return values

    tools["allow_read_paths"] = _append_unique_path(tools.get("allow_read_paths"))
    tools["allow_write_paths"] = _append_unique_path(tools.get("allow_write_paths"))

    if proxy_base_url and proxy_routes_file is not None:
        routes: dict[str, dict[str, str]] = {}
        model_list = data.get("model_list") or []
        if isinstance(model_list, list):
            for index, model_cfg in enumerate(model_list):
                if not isinstance(model_cfg, dict):
                    continue
                upstream = str(model_cfg.get("api_base") or "").strip()
                if not upstream:
                    continue
                route_name = str(model_cfg.get("model_name") or model_cfg.get("model") or f"model-{index}")
                safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in route_name).strip("-") or f"model-{index}"
                prefix = f"/picoclaw/{safe_name}"
                routes[prefix] = {
                    "framework": "picoclaw",
                    "provider": route_name,
                    "upstream": upstream,
                }
                model_cfg["api_base"] = f"{proxy_base_url}{prefix}"
        if routes:
            register_routes(proxy_routes_file, routes)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class PicoClawAdapter(BaseAdapter):
    name = "picoclaw"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        command = str(ctx.model_config.get("command") or "picoclaw")
        user_config_raw = str(ctx.model_config.get("user_config") or "config/picoclaw.json")
        user_config = _resolve_project_path(user_config_raw)
        if not user_config.is_file():
            return AdapterRunResult(ok=False, stderr=f"missing PicoClaw source config: {user_config}")

        isolated_home = ctx.sandbox
        picoclaw_home = isolated_home / ".picoclaw"
        picoclaw_home.mkdir(parents=True, exist_ok=True)
        picoclaw_workspace = picoclaw_home / "workspace"
        picoclaw_workspace.mkdir(parents=True, exist_ok=True)

        sandbox_user_config = picoclaw_home / "config.src.json"
        shutil.copy2(user_config, sandbox_user_config)
        merged_cfg = picoclaw_home / "config.json"
        _merge_user_config(
            sandbox_user_config,
            picoclaw_workspace,
            ctx.workspace,
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
        ]
        model_override = str(ctx.model_config.get("model_override") or "").strip()
        if model_override:
            cmd.extend(["--model", model_override])

        env = os.environ.copy()
        env.update(ctx.env)
        env["HOME"] = str(isolated_home)
        env["PICOCLAW_HOME"] = str(picoclaw_home)
        env["PICOCLAW_CONFIG"] = str(merged_cfg)
        env["PICOCLAW_WORKSPACE"] = str(picoclaw_workspace)
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
                "picoclaw_config_path": str(merged_cfg),
                "source_user_config_path": str(user_config),
                "sandbox_user_config_path": str(sandbox_user_config),
                "isolated_home": str(isolated_home),
                "picoclaw_home": str(picoclaw_home),
                "picoclaw_workspace": str(picoclaw_workspace),
                "workspace": str(ctx.workspace),
            },
        )
