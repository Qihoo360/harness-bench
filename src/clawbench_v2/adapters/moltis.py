from __future__ import annotations

import json
import os
import subprocess
import sys
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


def _loads_toml(text: str) -> dict[str, Any]:
    if sys.version_info >= (3, 11):
        import tomllib

        return tomllib.loads(text)
    import tomli

    return tomli.loads(text)


def _load_user_config(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    raw = path.read_text(encoding="utf-8")
    if suffix == ".json":
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    if suffix in (".toml",):
        return _loads_toml(raw)
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError(f"{path} requires PyYAML; install pyyaml or use .toml/.json") from exc
        loaded = yaml.safe_load(raw)
        return loaded if isinstance(loaded, dict) else {}
    # Guess by content
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    return _loads_toml(raw)


def _merge_providers_for_proxy(
    data: dict[str, Any],
    proxy_base_url: str,
    routes_file: Path | None,
) -> None:
    if not proxy_base_url or routes_file is None:
        return
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return
    skip = {"offered", "show_legacy_models"}
    routes: dict[str, dict[str, str]] = {}
    for name, cfg in providers.items():
        if name in skip or not isinstance(cfg, dict):
            continue
        upstream = str(cfg.get("base_url") or cfg.get("url") or "").strip()
        if not upstream:
            continue
        prefix = f"/moltis/{name}"
        routes[prefix] = {
            "framework": "moltis",
            "provider": str(name),
            "upstream": upstream,
        }
        cfg["base_url"] = f"{proxy_base_url.rstrip('/')}{prefix}"
    if routes:
        register_routes(routes_file, routes)


def _write_merged_config(data: dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class MoltisAdapter(BaseAdapter):
    """Run Moltis via `moltis agent` with isolated config/data dirs (ClawBench v2)."""

    name = "moltis"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        command = str(ctx.model_config.get("command") or "moltis")
        user_config_raw = str(ctx.model_config.get("user_config") or os.path.expanduser("~/.config/moltis/moltis.toml"))
        user_config = _resolve_project_path(user_config_raw)
        if not user_config.is_file():
            return AdapterRunResult(ok=False, stderr=f"missing Moltis source config: {user_config}")

        isolated_home = ctx.sandbox
        moltis_config_dir = isolated_home / ".config" / "moltis"
        moltis_config_dir.mkdir(parents=True, exist_ok=True)
        merged_cfg = moltis_config_dir / "moltis.json"

        data = _load_user_config(user_config)
        _merge_providers_for_proxy(
            data,
            proxy_base_url=str(ctx.env.get("CLAWBENCH_LLM_PROXY_URL") or ""),
            routes_file=Path(ctx.env["CLAWBENCH_LLM_PROXY_ROUTES"]) if ctx.env.get("CLAWBENCH_LLM_PROXY_ROUTES") else None,
        )
        _write_merged_config(data, merged_cfg)

        extra = [str(x) for x in (ctx.model_config.get("args") or [])]
        cmd: list[str] = [command, *extra, "agent", "--message", ctx.prompt, "--session-id", ctx.session_id, "--timeout", str(ctx.timeout_sec)]
        model_override = ctx.model_config.get("model")
        if model_override:
            cmd.extend(["--model", str(model_override)])

        env = os.environ.copy()
        env.update(ctx.env)
        env["HOME"] = str(isolated_home)
        env["MOLTIS_CONFIG_DIR"] = str(moltis_config_dir)
        env["MOLTIS_DATA_DIR"] = str(ctx.workspace.resolve())

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
                "moltis_config_dir": str(moltis_config_dir),
                "merged_config_path": str(merged_cfg),
                "source_user_config_path": str(user_config),
                "isolated_home": str(isolated_home),
                "workspace": str(ctx.workspace),
            },
        )
